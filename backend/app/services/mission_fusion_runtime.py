"""
Контекст активной миссии для автоматического перепланирования по fusion (ТЗ §10, Промпт 11).

Клиент после старта миссии регистрирует маршруты и счётчики посещённых точек
через POST /api/v1/mission/{id}/fusion-context. Пока контекст задан, поток
телеметрии MAVLink вызывает цепочку features → fusion; при устойчиво высокой
угрозе добавляется круговая зона jammer вокруг дрона и вызывается
``replan_on_new_risk_zone`` с ограничением частоты.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from shapely.geometry import Point, mapping

from app.core.config import (
    FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC,
    FUSION_AUTO_REPLAN_STREAK,
    FUSION_AUTO_SEVERITY_SCALE,
    FUSION_AUTO_ZONE_RADIUS_M,
    FUSION_THRESHOLD,
)
from app.schemas.mission import DroneRoute
from app.services.replanner import replan_on_new_risk_zone
from app.services.telemetry_features import (
    compute_gnss_stability_score,
    compute_imu_proxy_score,
    compute_link_stability_score,
    get_swarm_outlier_score,
    update_from_mavlink_snapshot,
)
from app.services.threat_fusion import fuse_threat_scores

if TYPE_CHECKING:
    from app.services.mavlink_service import MAVLinkService

logger = logging.getLogger(__name__)

# ~ метры → градусы широты (MVP)
_METERS_PER_DEG_LAT: float = 111_320.0


@dataclass
class FusionMissionContext:
    """Снимок состояния, нужный для replan_on_new_risk_zone."""

    mission_id: int
    field_id: int
    field_polygon: Any  # shapely Polygon
    drones: List[Any]
    risk_zones: List[Any]
    current_routes: List[DroneRoute]
    visited_counts: Dict[int, int]
    drone_ids: Set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.drone_ids = {d.id for d in self.drones}


_context: Optional[FusionMissionContext] = None
_high_threat_streak: Dict[int, int] = {}
_last_auto_replan_mono: float = 0.0

# Последний снимок fusion по дрону (для WS / UI, Промпт 12)
_last_fusion_snapshots: Dict[int, Dict[str, Any]] = {}
_auto_replan_event_id: int = 0


def get_fusion_context() -> Optional[FusionMissionContext]:
    return _context


def set_fusion_context(ctx: FusionMissionContext) -> None:
    """Установить контекст (обычно из HTTP handler)."""
    global _context
    _context = ctx


def clear_fusion_context() -> None:
    """Сбросить контекст и счётчики (тесты / конец миссии)."""
    global _context, _high_threat_streak, _last_auto_replan_mono, _auto_replan_event_id
    _context = None
    _high_threat_streak.clear()
    _last_auto_replan_mono = 0.0
    _last_fusion_snapshots.clear()
    _auto_replan_event_id = 0


def get_fusion_snapshot(drone_id: int) -> Optional[Dict[str, Any]]:
    """Последний рассчитанный fusion для дрона (JSON-сериализуемый)."""
    snap = _last_fusion_snapshots.get(drone_id)
    return dict(snap) if snap is not None else None


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, (int, str, bool)) or obj is None:
        return obj
    return str(obj)


def _store_fusion_snapshot(drone_id: int, fused: float, breakdown: Any) -> None:
    _last_fusion_snapshots[drone_id] = {
        "fused_threat_level": float(max(0.0, min(1.0, fused))),
        "breakdown": _json_safe(breakdown),
        "auto_replan_event_id": _auto_replan_event_id,
    }


def reset_fusion_streaks() -> None:
    """Только счётчики подряд идущих кадров с высокой угрозой."""
    _high_threat_streak.clear()


def _sync_telemetry_buffers_from_cache(mavlink_svc: "MAVLinkService") -> None:
    for did, snap in mavlink_svc.telemetry.items():
        update_from_mavlink_snapshot(did, snap)


def _positions_for_swarm(mavlink_svc: "MAVLinkService") -> Dict[int, tuple[float, float]]:
    out: Dict[int, tuple[float, float]] = {}
    for did, snap in mavlink_svc.telemetry.items():
        lat = float(snap.get("lat") or 0.0)
        lng = float(snap.get("lng") or 0.0)
        out[did] = (lat, lng)
    return out


def _circle_zone_around(lat: float, lng: float, fused: float) -> Dict[str, Any]:
    r_deg = FUSION_AUTO_ZONE_RADIUS_M / _METERS_PER_DEG_LAT
    poly = Point(lng, lat).buffer(r_deg)
    severity = min(1.0, fused * FUSION_AUTO_SEVERITY_SCALE)
    return {
        "geometry": mapping(poly),
        "severity": severity,
        "zone_type": "jammer",
    }


async def process_telemetry_fusion(drone_id: int, mavlink_svc: "MAVLinkService") -> None:
    """
    Вызвать после обновления кэша телеметрии: признаки → fusion → снимок для UI;
    при активном контексте миссии — логика авто-replan.
    """
    global _last_auto_replan_mono, _auto_replan_event_id

    _sync_telemetry_buffers_from_cache(mavlink_svc)
    positions = _positions_for_swarm(mavlink_svc)

    scores = {
        "gnss": compute_gnss_stability_score(drone_id),
        "link": compute_link_stability_score(drone_id),
        "imu_proxy": compute_imu_proxy_score(drone_id),
        "swarm": get_swarm_outlier_score(drone_id, positions),
    }
    fused, breakdown = fuse_threat_scores(scores, drone_id=drone_id)
    _store_fusion_snapshot(drone_id, fused, breakdown)

    ctx = _context
    if ctx is None or drone_id not in ctx.drone_ids:
        return

    if fused < FUSION_THRESHOLD:
        _high_threat_streak[drone_id] = 0
        return

    _high_threat_streak[drone_id] = _high_threat_streak.get(drone_id, 0) + 1
    if _high_threat_streak[drone_id] < FUSION_AUTO_REPLAN_STREAK:
        return

    now = time.monotonic()
    if (
        _last_auto_replan_mono > 0.0
        and now - _last_auto_replan_mono < FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC
    ):
        logger.info(
            "event=fusion_auto_replan_skipped reason=rate_limit mission_id=%s drone_id=%s "
            "delta_sec=%.2f min_interval_sec=%.2f",
            ctx.mission_id,
            drone_id,
            now - _last_auto_replan_mono,
            FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC,
        )
        return

    tel = mavlink_svc.telemetry.get(drone_id) or {}
    lat = float(tel.get("lat") or 0.0)
    lng = float(tel.get("lng") or 0.0)
    new_zone = _circle_zone_around(lat, lng, fused)

    logger.info(
        "event=fusion_auto_replan_triggered mission_id=%s drone_id=%s fused_threat=%.4f "
        "streak=%s breakdown=%s",
        ctx.mission_id,
        drone_id,
        fused,
        _high_threat_streak.get(drone_id, 0),
        breakdown,
    )

    try:
        result = await replan_on_new_risk_zone(
            new_zone=new_zone,
            current_routes=ctx.current_routes,
            visited_counts=ctx.visited_counts,
            drones=ctx.drones,
            field_polygon=ctx.field_polygon,
            existing_risk_zones=ctx.risk_zones,
        )
    except Exception as exc:
        logger.exception(
            "event=fusion_auto_replan_failed mission_id=%s drone_id=%s err=%s",
            ctx.mission_id,
            drone_id,
            exc,
        )
        return

    if result.get("status") != "replanned":
        return

    ctx.current_routes = [DroneRoute(**r) for r in result["updated_routes"]]
    _last_auto_replan_mono = time.monotonic()
    _high_threat_streak.clear()
    _auto_replan_event_id += 1
    _store_fusion_snapshot(drone_id, fused, breakdown)

    for dr in ctx.current_routes:
        wps = [{"lat": rp.lat, "lng": rp.lng, "alt": 30.0} for rp in dr.route]
        await mavlink_svc.update_mission(dr.drone_id, wps)
