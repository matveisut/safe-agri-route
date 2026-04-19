"""
Unit tests for telemetry_features — скользящие окна и эвристики ТЗ §10.

Run: pytest backend/tests/test_telemetry_features.py -v
"""

from __future__ import annotations

import pytest

from app.services.telemetry_features import (
    TelemetrySnapshot,
    compute_gnss_stability_score,
    compute_imu_proxy_score,
    compute_link_stability_score,
    get_swarm_outlier_score,
    reset_telemetry_buffers,
    update_from_mavlink_snapshot,
)


def _frame(
    t_sec: float,
    lat: float,
    lng: float,
    *,
    gs: float = 0.0,
    hdg: float = 0.0,
    battery: float = 100.0,
    sats: int | None = None,
    eph: float | None = None,
    rssi: float | None = None,
) -> dict:
    return {
        "t_sec": t_sec,
        "lat": lat,
        "lng": lng,
        "alt": 10.0,
        "groundspeed": gs,
        "heading": hdg,
        "battery": battery,
        "satellites_visible": sats,
        "eph": eph,
        "rssi": rssi,
    }


@pytest.fixture(autouse=True)
def _clean_buffers():
    reset_telemetry_buffers()
    yield
    reset_telemetry_buffers()


def test_update_accepts_telemetry_snapshot_dataclass():
    reset_telemetry_buffers()
    snap = TelemetrySnapshot(
        t_sec=1.0,
        lat=50.0,
        lng=30.0,
        alt_m=10.0,
        groundspeed_m_s=5.0,
        heading_deg=90.0,
        battery_pct=80.0,
    )
    update_from_mavlink_snapshot(7, snap)
    assert compute_link_stability_score(7) == 1.0


def test_gnss_score_drops_on_position_jump():
    """Резкий скачок позиции при почти нулевой скорости → штраф к GNSS score."""
    reset_telemetry_buffers()
    base_t = 10_000.0
    for i in range(22):
        update_from_mavlink_snapshot(
            1,
            _frame(base_t + i * 0.1, 50.0, 30.0, gs=0.0),
        )
    stable = compute_gnss_stability_score(1)

    reset_telemetry_buffers()
    for i in range(21):
        update_from_mavlink_snapshot(
            2,
            _frame(base_t + i * 0.1, 50.0, 30.0, gs=0.0),
        )
    # большой скачок ~0.1° широты без соответствующей скорости
    update_from_mavlink_snapshot(
        2,
        _frame(base_t + 21 * 0.1, 50.1, 30.0, gs=0.0),
    )
    jumpy = compute_gnss_stability_score(2)

    assert stable > jumpy
    assert 0.0 <= jumpy <= 1.0


def test_link_score_drops_on_irregular_timestamps():
    reset_telemetry_buffers()
    t0 = 500.0
    for i in range(22):
        dt = 0.05 if i % 2 == 0 else 0.35
        update_from_mavlink_snapshot(1, _frame(t0, 51.0, 31.0))
        t0 += dt
    jitter = compute_link_stability_score(1)

    reset_telemetry_buffers()
    t0 = 500.0
    for i in range(22):
        update_from_mavlink_snapshot(2, _frame(t0, 51.0, 31.0))
        t0 += 0.1
    stable = compute_link_stability_score(2)

    assert stable > jitter


def test_link_penalizes_very_weak_rssi():
    reset_telemetry_buffers()
    for i in range(5):
        update_from_mavlink_snapshot(1, _frame(100.0 + i * 0.1, 50.0, 30.0, rssi=-90.0))
    good_rssi = compute_link_stability_score(1)

    reset_telemetry_buffers()
    for i in range(5):
        update_from_mavlink_snapshot(2, _frame(100.0 + i * 0.1, 50.0, 30.0, rssi=-30.0))
    strong = compute_link_stability_score(2)

    assert strong > good_rssi


def test_imu_proxy_penalizes_high_jerk():
    reset_telemetry_buffers()
    t = 0.0
    for i in range(25):
        lat = 50.0 + i * 1e-6
        update_from_mavlink_snapshot(1, _frame(t, lat, 30.0))
        t += 0.1
    smooth = compute_imu_proxy_score(1)

    reset_telemetry_buffers()
    t = 0.0
    lats = [50.0, 50.0, 50.002, 50.0, 50.0, 50.002] * 5
    for lat in lats[:25]:
        update_from_mavlink_snapshot(2, _frame(t, lat, 30.0))
        t += 0.1
    erratic = compute_imu_proxy_score(2)

    assert smooth >= erratic


def test_swarm_outlier_when_speed_and_heading_differ():
    reset_telemetry_buffers()
    pos = {1: (50.0, 30.0), 2: (50.001, 30.0), 3: (50.002, 30.0)}
    for _ in range(15):
        update_from_mavlink_snapshot(1, _frame(200.0, 50.0, 30.0, gs=12.0, hdg=10.0))
        update_from_mavlink_snapshot(2, _frame(200.0, 50.001, 30.0, gs=12.0, hdg=10.0))
        update_from_mavlink_snapshot(3, _frame(200.0, 50.002, 30.0, gs=12.0, hdg=10.0))
    aligned = get_swarm_outlier_score(1, pos)

    reset_telemetry_buffers()
    for _ in range(15):
        update_from_mavlink_snapshot(1, _frame(200.0, 50.0, 30.0, gs=40.0, hdg=200.0))
        update_from_mavlink_snapshot(2, _frame(200.0, 50.001, 30.0, gs=12.0, hdg=10.0))
        update_from_mavlink_snapshot(3, _frame(200.0, 50.002, 30.0, gs=12.0, hdg=10.0))
    outlier = get_swarm_outlier_score(1, pos)

    assert aligned > outlier


def test_reset_single_drone():
    update_from_mavlink_snapshot(5, _frame(1.0, 50.0, 30.0))
    reset_telemetry_buffers(drone_id=5)
    assert compute_gnss_stability_score(5) == 1.0


def test_invalid_messages_type_raises():
    with pytest.raises(TypeError):
        update_from_mavlink_snapshot(1, "not-a-mapping")
