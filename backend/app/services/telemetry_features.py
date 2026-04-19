"""
Слой признаков из потока MAVLink для целевой модели динамической угрозы (ТЗ §10).

Без ML: скользящие окна по каждому дрону и эвристические оценки стабильности GNSS,
радиоканала, «IMU-прокси» по траектории и согласованности с роем.

Состояние хранится в памяти процесса (`defaultdict` + `deque`); для unit-тестов
доступен `reset_telemetry_buffers`.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Deque, Dict, Mapping, MutableMapping, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Константы (можно вынести в Settings при появлении конфига)
# ---------------------------------------------------------------------------

WINDOW_SIZE: int = 20

# Пороги эвристик (подобраны для нормирования в [0, 1])
_MAX_POS_INCONSISTENCY: float = 0.85  # доля несоответствия v*dt и Δpos
_SAT_DROP_PENALTY: float = 0.15       # за резкое падение числа спутников
_EPH_BAD_M: float = 10.0             # HDOP-подобная ошибка (м), выше — хуже
_LINK_JITTER_MAX: float = 0.5       # относительный разброс интервалов
_IMU_JERK_THRESH: float = 8.0        # м/с² — выше считаем «рваным» движением
_SWARM_SPEED_SPREAD_M_S: float = 5.0
_SWARM_HEADING_SPREAD_DEG: float = 45.0


@dataclass
class TelemetrySnapshot:
    """Одна точка скользящего окна (после нормализации полей MAVLink)."""

    t_sec: float
    lat: float
    lng: float
    alt_m: float
    groundspeed_m_s: float
    heading_deg: float
    battery_pct: float
    fix_type: Optional[int] = None
    satellites_visible: Optional[int] = None
    eph_m: Optional[float] = None
    epv_m: Optional[float] = None
    rssi_dbm: Optional[float] = None


Buffers = Dict[int, Deque[TelemetrySnapshot]]

_buffers: Buffers = defaultdict(lambda: deque(maxlen=WINDOW_SIZE))


def reset_telemetry_buffers(drone_id: Optional[int] = None) -> None:
    """Очистить окна (все дроны или один); для тестов и сброса сессии."""
    global _buffers
    if drone_id is None:
        _buffers = defaultdict(lambda: deque(maxlen=WINDOW_SIZE))
    else:
        _buffers.pop(drone_id, None)


def _get_buffers() -> Buffers:
    return _buffers


def _coerce_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _coerce_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except (TypeError, ValueError):
        return default


def _normalize_mavlink_dict(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Привести произвольный dict (как у `MAVLinkService` telemetry frame) к канонике.
    Поддерживаются плоские ключи и вложенность по имени сообщения.
    """
    if not raw:
        return {}
    flat: Dict[str, Any] = dict(raw)
    for key in ("GLOBAL_POSITION_INT", "VFR_HUD", "GPS_RAW_INT", "SYS_STATUS"):
        nested = raw.get(key)
        if isinstance(nested, Mapping):
            flat.update(nested)
    return flat


def _snapshot_from_flat(flat: Mapping[str, Any]) -> TelemetrySnapshot:
    t_sec = _coerce_float(flat.get("t_sec"), time.time())
    lat = _coerce_float(flat.get("lat"))
    if flat.get("lng") is not None:
        lng = _coerce_float(flat.get("lng"))
    elif flat.get("lon") is not None:
        lng = _coerce_float(flat.get("lon"))
    else:
        lng = 0.0
    alt_m = _coerce_float(flat.get("alt"), _coerce_float(flat.get("alt_m")))
    gs = _coerce_float(flat.get("groundspeed"), _coerce_float(flat.get("groundspeed_m_s")))
    hdg = _coerce_float(flat.get("heading"), _coerce_float(flat.get("heading_deg")))
    bat = _coerce_float(flat.get("battery"), 100.0)
    fix = _coerce_int(flat.get("fix_type"))
    sats = _coerce_int(flat.get("satellites_visible"), _coerce_int(flat.get("satellites")))
    eph = flat.get("eph")
    epv = flat.get("epv")
    eph_m = _coerce_float(eph) if eph is not None else None
    epv_m = _coerce_float(epv) if epv is not None else None
    if eph_m is not None and eph_m > 1e3:
        # иногда приходит в мм
        eph_m = eph_m / 1000.0
    if epv_m is not None and epv_m > 1e3:
        epv_m = epv_m / 1000.0
    rssi = flat.get("rssi")
    rssi_dbm = _coerce_float(rssi) if rssi is not None else None

    return TelemetrySnapshot(
        t_sec=t_sec,
        lat=lat,
        lng=lng,
        alt_m=alt_m,
        groundspeed_m_s=max(0.0, gs),
        heading_deg=hdg % 360.0,
        battery_pct=max(0.0, min(100.0, bat)),
        fix_type=fix,
        satellites_visible=sats,
        eph_m=eph_m,
        epv_m=epv_m,
        rssi_dbm=rssi_dbm,
    )


def update_from_mavlink_snapshot(
    drone_id: int,
    messages: Union[MutableMapping[str, Any], TelemetrySnapshot, Any],
) -> None:
    """
    Добавить снимок телеметрии в скользящее окно дрона.

    Parameters
    ----------
    drone_id
        Идентификатор дрона (как в остальном backend).
    messages
        Словарь полей кадра (как из `read_telemetry_loop`) **или** dataclass
        с теми же атрибутами. См. ТЗ §10 (вход мультисенсорного контура).

    Примечание: при отсутствии ``t_sec`` подставляется ``time.time()`` для оценки
    интервалов радиоканала.
    """
    if isinstance(messages, TelemetrySnapshot):
        raw = asdict(messages)
    elif is_dataclass(messages) and not isinstance(messages, type):
        raw = asdict(messages)
    elif isinstance(messages, Mapping):
        raw = dict(messages)
    else:
        raise TypeError("messages must be a mapping or a dataclass instance")
    flat = _normalize_mavlink_dict(raw)
    snap = _snapshot_from_flat(flat)
    _buffers[drone_id].append(snap)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def compute_gnss_stability_score(drone_id: int) -> float:
    """
    Оценка стабильности GNSS/навигации ∈ [0, 1], 1 — доверять положению.

    Эвристики (ТЗ §10): несоответствие перемещения ожидаемому ``v·dt``,
    падение числа спутников, высокая ``eph``.
    """
    buf = _buffers.get(drone_id)
    if not buf or len(buf) < 2:
        return 1.0

    penalties: list[float] = []
    prev = buf[0]
    for cur in list(buf)[1:]:
        dt = max(1e-3, cur.t_sec - prev.t_sec)
        dist = _haversine_m(prev.lat, prev.lng, cur.lat, cur.lng)
        v_avg = 0.5 * (abs(prev.groundspeed_m_s) + abs(cur.groundspeed_m_s))
        expected = v_avg * dt
        denom = max(expected, dist, 1.0)
        inconsistency = abs(dist - expected) / denom
        penalties.append(min(1.0, inconsistency / max(1e-6, _MAX_POS_INCONSISTENCY)))

        if (
            prev.satellites_visible is not None
            and cur.satellites_visible is not None
            and cur.satellites_visible - prev.satellites_visible <= -3
        ):
            penalties.append(_SAT_DROP_PENALTY)

        if cur.eph_m is not None and cur.eph_m > _EPH_BAD_M:
            penalties.append(min(1.0, (cur.eph_m - _EPH_BAD_M) / (_EPH_BAD_M * 2)))

        prev = cur

    if not penalties:
        return 1.0
    return float(max(0.0, min(1.0, 1.0 - sum(penalties) / len(penalties))))


def compute_link_stability_score(drone_id: int) -> float:
    """
    Стабильность канала связи ∈ [0, 1] по интервалам между кадрами и опционально RSSI.

    При отсутствии вариации интервалов (один кадр) возвращает 1.0.
    """
    buf = _buffers.get(drone_id)
    if not buf or len(buf) < 2:
        return 1.0

    dts = []
    rssi_pen = 0.0
    samples = list(buf)
    for a, b in zip(samples[:-1], samples[1:]):
        dts.append(max(1e-4, b.t_sec - a.t_sec))
    if not dts:
        return 1.0
    mean_dt = sum(dts) / len(dts)
    var = sum((x - mean_dt) ** 2 for x in dts) / len(dts)
    rel_jitter = math.sqrt(var) / max(mean_dt, 1e-4)
    jitter_penalty = min(1.0, rel_jitter / _LINK_JITTER_MAX)

    last = samples[-1]
    if last.rssi_dbm is not None:
        # типичный диапазон Wi-Fi/радио: -30 … -90 dBm
        if last.rssi_dbm < -85:
            rssi_pen = 0.35
        elif last.rssi_dbm < -75:
            rssi_pen = 0.15

    score = 1.0 - 0.7 * jitter_penalty - rssi_pen
    return float(max(0.0, min(1.0, score)))


def compute_imu_proxy_score(drone_id: int) -> float:
    """
    Прокси IMU без датчика: величина «рывка» из вторых разностей позиции (ТЗ §10).
    """
    buf = _buffers.get(drone_id)
    if not buf or len(buf) < 3:
        return 1.0

    samples = list(buf)
    jerk_mag_max = 0.0
    for i in range(2, len(samples)):
        a, b, c = samples[i - 2], samples[i - 1], samples[i]
        dt1 = max(1e-3, b.t_sec - a.t_sec)
        dt2 = max(1e-3, c.t_sec - b.t_sec)
        # ускорение из разностей скоростей (приближённо), м/с²
        v1x = (b.lat - a.lat) * 111_320.0 / dt1
        v1y = (b.lng - a.lng) * 111_320.0 * math.cos(math.radians(b.lat)) / dt1
        v2x = (c.lat - b.lat) * 111_320.0 / dt2
        v2y = (c.lng - b.lng) * 111_320.0 * math.cos(math.radians(c.lat)) / dt2
        ax = (v2x - v1x) / (0.5 * (dt1 + dt2))
        ay = (v2y - v1y) / (0.5 * (dt1 + dt2))
        jerk = math.sqrt(ax * ax + ay * ay)
        jerk_mag_max = max(jerk_mag_max, jerk)

    penalty = min(1.0, jerk_mag_max / _IMU_JERK_THRESH)
    return float(max(0.0, min(1.0, 1.0 - penalty)))


def get_swarm_outlier_score(
    drone_id: int,
    all_drone_positions: Mapping[int, Tuple[float, float]],
) -> float:
    """
    Насколько дрон согласован с роем по скорости/курсу (1 — типичный, 0 — выброс).

    Использует последние значения ``groundspeed`` / ``heading`` из окон; позиции
    в ``all_drone_positions`` резервируем для будущих расширений (см. ТЗ §10).
    """
    _ = all_drone_positions  # в MVP основной сигнал — последние скорости из буферов
    peers = [did for did in all_drone_positions if did != drone_id]
    my_buf = _buffers.get(drone_id)
    if not my_buf or not peers:
        return 1.0
    mine = my_buf[-1]

    other_speeds: list[float] = []
    other_headings: list[float] = []
    for pid in peers:
        b = _buffers.get(pid)
        if not b:
            continue
        s = b[-1]
        other_speeds.append(s.groundspeed_m_s)
        other_headings.append(s.heading_deg)

    if not other_speeds:
        return 1.0

    med_sp = sorted(other_speeds)[len(other_speeds) // 2]
    n_h = len(other_headings)
    cx = sum(math.cos(math.radians(h)) for h in other_headings) / n_h
    sn = sum(math.sin(math.radians(h)) for h in other_headings) / n_h
    ref_h = math.degrees(math.atan2(sn, cx)) % 360

    d_speed = abs(mine.groundspeed_m_s - med_sp)
    dh = abs(mine.heading_deg - ref_h) % 360
    dh = min(dh, 360 - dh)

    sp_pen = min(1.0, d_speed / _SWARM_SPEED_SPREAD_M_S)
    hd_pen = min(1.0, dh / _SWARM_HEADING_SPREAD_DEG)
    penalty = 0.5 * sp_pen + 0.5 * hd_pen
    return float(max(0.0, min(1.0, 1.0 - penalty)))
