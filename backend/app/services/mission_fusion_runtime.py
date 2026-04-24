"""
Контекст активной миссии для автоматического перепланирования по fusion (ТЗ §10).

Промпт 14:
- detector state machine (NORMAL/SUSPECT/CONFIRMED_JAMMING/RECOVERING),
- hysteresis + EMA,
- dynamic zone lifecycle (create/update/merge/expire),
- JSON snapshot для mission WS.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set
from uuid import uuid4

from shapely.geometry import Point, mapping, shape

from app.core.config import (
    ENABLE_SAFETY_ACTION_BEFORE_REPLAN,
    FUSION_AUTO_REPLAN_STREAK,
    FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC,
    FUSION_AUTO_SEVERITY_SCALE,
    FUSION_DETECTOR_ALPHA,
    FUSION_DETECTOR_CONFIRM_STREAK,
    FUSION_DETECTOR_RECOVERY_STREAK,
    FUSION_DETECTOR_T_HIGH,
    FUSION_DETECTOR_T_LOW,
    FUSION_THRESHOLD,
    FUSION_DYNAMIC_ZONE_MERGE_DISTANCE_M,
    FUSION_DYNAMIC_ZONE_RADIUS_BASE_M,
    FUSION_DYNAMIC_ZONE_RADIUS_GAIN_M,
    FUSION_DYNAMIC_ZONE_TTL_SEC,
    SAFETY_ACTION,
)
from app.schemas.mission import DroneRoute
from app.services.replanner import replan_on_new_risk_zone
from app.services.telemetry_features import (
    compute_packet_loss_score,
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


class DetectorState(str, Enum):
    NORMAL = "NORMAL"
    SUSPECT = "SUSPECT"
    CONFIRMED_JAMMING = "CONFIRMED_JAMMING"
    RECOVERING = "RECOVERING"


@dataclass
class DroneDetectorRuntime:
    state: DetectorState = DetectorState.NORMAL
    jam_prob: float = 0.0
    confirm_streak: int = 0
    recovery_streak: int = 0
    last_updated_ts: float = field(default_factory=lambda: time.time())


@dataclass
class DynamicZone:
    zone_id: str
    zone_type: str
    origin: str
    state: str
    confidence: float
    ttl_sec: float
    center_lat: float
    center_lng: float
    radius_m: float
    severity: float
    created_at: float
    updated_at: float
    expires_at: float
    source_drone_id: int
    geometry_geojson: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


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
_last_auto_replan_mono: float = 0.0
_auto_replan_event_id: int = 0

# Снимки fusion для UI/WS
_last_fusion_snapshots: Dict[int, Dict[str, Any]] = {}

# Runtime detector state per drone
_detector_runtime: Dict[int, DroneDetectorRuntime] = {}

# Backward-compat field used by existing tests/helpers
_high_threat_streak: Dict[int, int] = {}

# Dynamic zones (created from confirmed jamming)
_dynamic_zones: Dict[str, DynamicZone] = {}

# Prevent replan storm per one confirmation episode
_replan_fired_for_confirmation: Dict[int, bool] = {}


def get_fusion_context() -> Optional[FusionMissionContext]:
    return _context


def set_fusion_context(ctx: FusionMissionContext) -> None:
    global _context
    _context = ctx


def clear_fusion_context() -> None:
    global _context, _last_auto_replan_mono, _auto_replan_event_id
    _context = None
    _last_auto_replan_mono = 0.0
    _auto_replan_event_id = 0
    _last_fusion_snapshots.clear()
    _detector_runtime.clear()
    _high_threat_streak.clear()
    _dynamic_zones.clear()
    _replan_fired_for_confirmation.clear()


def reset_fusion_streaks() -> None:
    _high_threat_streak.clear()
    for rt in _detector_runtime.values():
        rt.confirm_streak = 0
        rt.recovery_streak = 0


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


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    mean_lat_rad = ((lat1 + lat2) / 2.0) * 3.141592653589793 / 180.0
    meters_per_deg_lng = _METERS_PER_DEG_LAT * max(0.1, abs(__import__("math").cos(mean_lat_rad)))
    dy = (lat2 - lat1) * _METERS_PER_DEG_LAT
    dx = (lng2 - lng1) * meters_per_deg_lng
    return (dx * dx + dy * dy) ** 0.5


def _merge_zone_into(target: DynamicZone, incoming: DynamicZone, now_ts: float) -> None:
    # Weighted center update by confidence
    total_w = max(1e-6, target.confidence + incoming.confidence)
    target.center_lat = (target.center_lat * target.confidence + incoming.center_lat * incoming.confidence) / total_w
    target.center_lng = (target.center_lng * target.confidence + incoming.center_lng * incoming.confidence) / total_w
    target.confidence = max(target.confidence, incoming.confidence)
    target.radius_m = max(target.radius_m, incoming.radius_m)
    target.severity = max(target.severity, incoming.severity)
    target.state = "active"
    target.updated_at = now_ts
    target.expires_at = now_ts + target.ttl_sec


def _cleanup_expired_zones(now_ts: float) -> None:
    for zid in [zid for zid, z in _dynamic_zones.items() if z.expires_at <= now_ts]:
        del _dynamic_zones[zid]


def _upsert_dynamic_zone(drone_id: int, lat: float, lng: float, jam_prob: float, now_ts: float) -> DynamicZone:
    radius_m = FUSION_DYNAMIC_ZONE_RADIUS_BASE_M + FUSION_DYNAMIC_ZONE_RADIUS_GAIN_M * jam_prob
    severity = min(1.0, jam_prob * FUSION_AUTO_SEVERITY_SCALE)
    incoming = DynamicZone(
        zone_id=f"jam-{uuid4().hex[:8]}",
        zone_type="jammer",
        origin="fusion",
        state="active",
        confidence=max(0.0, min(1.0, jam_prob)),
        ttl_sec=FUSION_DYNAMIC_ZONE_TTL_SEC,
        center_lat=lat,
        center_lng=lng,
        radius_m=radius_m,
        severity=severity,
        created_at=now_ts,
        updated_at=now_ts,
        expires_at=now_ts + FUSION_DYNAMIC_ZONE_TTL_SEC,
        source_drone_id=drone_id,
    )

    nearest: Optional[DynamicZone] = None
    nearest_dist = float("inf")
    for zone in _dynamic_zones.values():
        if zone.origin != "fusion" or zone.zone_type != "jammer":
            continue
        dist = _distance_m(lat, lng, zone.center_lat, zone.center_lng)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = zone

    if nearest is not None and nearest_dist <= FUSION_DYNAMIC_ZONE_MERGE_DISTANCE_M:
        _merge_zone_into(nearest, incoming, now_ts)
        return nearest

    _dynamic_zones[incoming.zone_id] = incoming
    return incoming


def _update_zone_lifecycle_for_drone(drone_id: int, confirmed: bool, now_ts: float) -> None:
    for zone in _dynamic_zones.values():
        if zone.source_drone_id != drone_id:
            continue
        if confirmed:
            zone.state = "active"
            zone.updated_at = now_ts
            zone.expires_at = now_ts + zone.ttl_sec
        else:
            zone.state = "fading"


def _point_in_zone(zone: DynamicZone, lat: float, lng: float) -> bool:
    if zone.geometry_geojson:
        try:
            poly = shape(zone.geometry_geojson)
            return poly.contains(Point(lng, lat)) or poly.intersects(Point(lng, lat).buffer(1e-8))
        except Exception:
            return False
    dist = _distance_m(lat, lng, zone.center_lat, zone.center_lng)
    return dist <= zone.radius_m


def add_manual_suspected_zone(
    geometry: Dict[str, Any],
    source: str = "operator",
    ttl_sec: Optional[float] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    now_ts = time.time()
    zone_ttl = float(ttl_sec if ttl_sec is not None else FUSION_DYNAMIC_ZONE_TTL_SEC)
    geom = shape(geometry)
    centroid = geom.centroid
    zone = DynamicZone(
        zone_id=f"sus-{uuid4().hex[:8]}",
        zone_type="suspected_jammer",
        origin=source,
        state="DRAWN",
        confidence=0.4,
        ttl_sec=zone_ttl,
        center_lat=float(centroid.y),
        center_lng=float(centroid.x),
        radius_m=max(FUSION_DYNAMIC_ZONE_RADIUS_BASE_M, 25.0),
        severity=0.4,
        created_at=now_ts,
        updated_at=now_ts,
        expires_at=now_ts + zone_ttl,
        source_drone_id=0,
        geometry_geojson=geometry,
        note=note,
    )
    _dynamic_zones[zone.zone_id] = zone
    return {"zone_id": zone.zone_id, "state": zone.state}


def update_zone_state(zone_id: str, state: str) -> Optional[Dict[str, Any]]:
    zone = _dynamic_zones.get(zone_id)
    if zone is None:
        return None
    now_ts = time.time()
    zone.state = state
    zone.updated_at = now_ts
    if state == "EXPIRED":
        zone.expires_at = now_ts
        _cleanup_expired_zones(now_ts)
        return {"zone_id": zone_id, "state": "EXPIRED"}
    return {"zone_id": zone.zone_id, "state": zone.state}


def get_dynamic_zones_snapshot() -> List[Dict[str, Any]]:
    now_ts = time.time()
    _cleanup_expired_zones(now_ts)
    out: List[Dict[str, Any]] = []
    for zone in _dynamic_zones.values():
        item = {
            "zone_id": zone.zone_id,
            "zone_type": zone.zone_type,
            "origin": zone.origin,
            "state": zone.state,
            "confidence": float(max(0.0, min(1.0, zone.confidence))),
            "ttl_sec": float(zone.ttl_sec),
            "created_at": float(zone.created_at),
            "updated_at": float(zone.updated_at),
            "severity": float(zone.severity),
            "expires_in_sec": max(0.0, float(zone.expires_at - now_ts)),
            "source_drone_id": int(zone.source_drone_id),
        }
        if zone.geometry_geojson:
            item["geometry"] = zone.geometry_geojson
        else:
            item["center"] = {"lat": float(zone.center_lat), "lng": float(zone.center_lng)}
            item["radius_m"] = float(zone.radius_m)
        if zone.note:
            item["note"] = zone.note
        out.append(item)
    return out


def _store_fusion_snapshot(
    drone_id: int,
    raw_fused: float,
    breakdown: Any,
    detector: DroneDetectorRuntime,
) -> None:
    raw_scores = breakdown.get("raw_scores", {}) if isinstance(breakdown, dict) else {}
    plr = 1.0 - float(raw_scores.get("plr", 1.0))
    _last_fusion_snapshots[drone_id] = {
        "fused_threat_level": float(max(0.0, min(1.0, raw_fused))),
        "jam_prob": float(max(0.0, min(1.0, detector.jam_prob))),
        "packet_loss_rate": float(max(0.0, min(1.0, plr))),
        "state": detector.state.value,
        "confirm_streak": detector.confirm_streak,
        "recovery_streak": detector.recovery_streak,
        "breakdown": _json_safe(breakdown),
        "auto_replan_event_id": _auto_replan_event_id,
    }


def get_fusion_snapshot(drone_id: int) -> Optional[Dict[str, Any]]:
    snap = _last_fusion_snapshots.get(drone_id)
    return dict(snap) if snap is not None else None


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


def _circle_zone_around(lat: float, lng: float, severity: float) -> Dict[str, Any]:
    radius_m = FUSION_DYNAMIC_ZONE_RADIUS_BASE_M + FUSION_DYNAMIC_ZONE_RADIUS_GAIN_M * severity
    r_deg = radius_m / _METERS_PER_DEG_LAT
    poly = Point(lng, lat).buffer(r_deg)
    return {"geometry": mapping(poly), "severity": min(1.0, severity), "zone_type": "jammer"}


def _update_detector_state(detector: DroneDetectorRuntime, raw_jam_prob: float) -> None:
    detector.last_updated_ts = time.time()
    detector.jam_prob = (
        FUSION_DETECTOR_ALPHA * raw_jam_prob
        + (1.0 - FUSION_DETECTOR_ALPHA) * detector.jam_prob
    )

    # Streak logic uses raw signal so detector reacts fast enough,
    # while `jam_prob` remains EMA-smoothed for stability in UI/zones.
    if raw_jam_prob >= FUSION_DETECTOR_T_HIGH:
        detector.confirm_streak += 1
        detector.recovery_streak = 0
    elif raw_jam_prob <= FUSION_DETECTOR_T_LOW:
        detector.recovery_streak += 1
        detector.confirm_streak = 0
    else:
        detector.confirm_streak = max(0, detector.confirm_streak - 1)
        detector.recovery_streak = max(0, detector.recovery_streak - 1)

    if detector.state in {DetectorState.NORMAL, DetectorState.SUSPECT}:
        if detector.confirm_streak >= FUSION_DETECTOR_CONFIRM_STREAK:
            detector.state = DetectorState.CONFIRMED_JAMMING
        elif raw_jam_prob >= FUSION_DETECTOR_T_LOW:
            detector.state = DetectorState.SUSPECT
        else:
            detector.state = DetectorState.NORMAL
    elif detector.state == DetectorState.CONFIRMED_JAMMING:
        if raw_jam_prob <= FUSION_DETECTOR_T_LOW:
            detector.state = DetectorState.RECOVERING
    elif detector.state == DetectorState.RECOVERING:
        if detector.confirm_streak >= FUSION_DETECTOR_CONFIRM_STREAK:
            detector.state = DetectorState.CONFIRMED_JAMMING
        elif detector.recovery_streak >= FUSION_DETECTOR_RECOVERY_STREAK:
            detector.state = DetectorState.NORMAL


async def _maybe_trigger_auto_replan(
    drone_id: int,
    detector: DroneDetectorRuntime,
    mavlink_svc: "MAVLinkService",
    breakdown: Any,
) -> None:
    global _last_auto_replan_mono, _auto_replan_event_id

    ctx = _context
    if ctx is None or drone_id not in ctx.drone_ids:
        return
    if detector.state != DetectorState.CONFIRMED_JAMMING:
        _replan_fired_for_confirmation[drone_id] = False
        return
    if detector.confirm_streak < FUSION_AUTO_REPLAN_STREAK:
        return
    if detector.jam_prob < FUSION_THRESHOLD:
        return
    if _replan_fired_for_confirmation.get(drone_id):
        return

    now_mono = time.monotonic()
    if _last_auto_replan_mono > 0.0 and now_mono - _last_auto_replan_mono < FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC:
        logger.info(
            "event=fusion_auto_replan_skipped reason=rate_limit mission_id=%s drone_id=%s "
            "delta_sec=%.2f min_interval_sec=%.2f",
            ctx.mission_id,
            drone_id,
            now_mono - _last_auto_replan_mono,
            FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC,
        )
        return

    tel = mavlink_svc.telemetry.get(drone_id) or {}
    lat = float(tel.get("lat") or 0.0)
    lng = float(tel.get("lng") or 0.0)
    new_zone = _circle_zone_around(lat, lng, detector.jam_prob)

    logger.info(
        "event=fusion_auto_replan_triggered mission_id=%s drone_id=%s jam_prob=%.4f state=%s breakdown=%s",
        ctx.mission_id,
        drone_id,
        detector.jam_prob,
        detector.state.value,
        breakdown,
    )

    if ENABLE_SAFETY_ACTION_BEFORE_REPLAN:
        try:
            ok = await mavlink_svc.apply_safety_action(drone_id, SAFETY_ACTION)
            logger.info(
                "event=fusion_safety_action mission_id=%s drone_id=%s action=%s result=%s",
                ctx.mission_id,
                drone_id,
                SAFETY_ACTION,
                "success" if ok else "failed",
            )
        except Exception as exc:
            logger.exception(
                "event=fusion_safety_action_failed mission_id=%s drone_id=%s action=%s err=%s",
                ctx.mission_id,
                drone_id,
                SAFETY_ACTION,
                exc,
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
    _last_auto_replan_mono = now_mono
    _auto_replan_event_id += 1
    _replan_fired_for_confirmation[drone_id] = True

    for dr in ctx.current_routes:
        wps = [{"lat": rp.lat, "lng": rp.lng, "alt": 30.0} for rp in dr.route]
        await mavlink_svc.update_mission(dr.drone_id, wps)
        if ENABLE_SAFETY_ACTION_BEFORE_REPLAN:
            try:
                await mavlink_svc.set_auto_mode(dr.drone_id)
            except Exception as exc:
                logger.exception(
                    "event=fusion_set_auto_failed mission_id=%s drone_id=%s err=%s",
                    ctx.mission_id,
                    dr.drone_id,
                    exc,
                )


async def process_telemetry_fusion(drone_id: int, mavlink_svc: "MAVLinkService") -> None:
    """
    Вызывать после обновления кэша телеметрии:
    features -> threat_fusion -> detector state machine -> dynamic zones -> auto replan.
    """
    _sync_telemetry_buffers_from_cache(mavlink_svc)
    positions = _positions_for_swarm(mavlink_svc)

    scores = {
        "gnss": compute_gnss_stability_score(drone_id),
        "link": compute_link_stability_score(drone_id),
        "imu_proxy": compute_imu_proxy_score(drone_id),
        "swarm": get_swarm_outlier_score(drone_id, positions),
        # 1.0 = максимальные потери; convert to stability for fusion input.
        "plr": 1.0 - compute_packet_loss_score(drone_id),
    }
    raw_fused, breakdown = fuse_threat_scores(scores, drone_id=drone_id)

    detector = _detector_runtime.setdefault(drone_id, DroneDetectorRuntime())
    _update_detector_state(detector, raw_fused)

    now_ts = time.time()
    _high_threat_streak[drone_id] = detector.confirm_streak

    tel = mavlink_svc.telemetry.get(drone_id) or {}
    lat = float(tel.get("lat") or 0.0)
    lng = float(tel.get("lng") or 0.0)
    confirmed = detector.state == DetectorState.CONFIRMED_JAMMING

    if confirmed:
        _upsert_dynamic_zone(
            drone_id=drone_id,
            lat=lat,
            lng=lng,
            jam_prob=detector.jam_prob,
            now_ts=now_ts,
        )
    _update_zone_lifecycle_for_drone(drone_id, confirmed=confirmed, now_ts=now_ts)

    # suspected_jammer lifecycle: DRAWN -> OBSERVING -> CONFIRMED
    for zone in _dynamic_zones.values():
        if zone.zone_type != "suspected_jammer":
            continue
        inside = _point_in_zone(zone, lat, lng)
        if inside and zone.state == "DRAWN":
            zone.state = "OBSERVING"
            zone.updated_at = now_ts
        if inside and confirmed and zone.state in {"DRAWN", "OBSERVING"}:
            zone.state = "CONFIRMED"
            zone.zone_type = "jammer"
            zone.confidence = max(zone.confidence, detector.jam_prob)
            zone.severity = max(zone.severity, detector.jam_prob)
            zone.updated_at = now_ts
            zone.expires_at = now_ts + zone.ttl_sec

    _cleanup_expired_zones(now_ts)

    _store_fusion_snapshot(drone_id, raw_fused, breakdown, detector)
    await _maybe_trigger_auto_replan(drone_id, detector, mavlink_svc, breakdown)
