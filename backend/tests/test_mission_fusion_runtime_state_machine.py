from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.services import mission_fusion_runtime as runtime
from app.services.telemetry_features import reset_telemetry_buffers
from app.services.threat_fusion import reset_fusion_state


class _Svc:
    def __init__(self) -> None:
        self.telemetry = {
            1: {"drone_id": 1, "lat": 45.04, "lng": 41.97, "groundspeed": 5.0},
        }
        self.update_calls = 0

    async def update_mission(self, *_args, **_kwargs):
        self.update_calls += 1
        return True


def _run(coro):
    return asyncio.run(coro)


def _fusion(level: float):
    return level, {"fused_threat_level": level}


def setup_function():
    runtime.clear_fusion_context()
    reset_telemetry_buffers()
    reset_fusion_state()


def teardown_function():
    runtime.clear_fusion_context()
    reset_telemetry_buffers()
    reset_fusion_state()


def test_noise_around_threshold_does_not_confirm():
    svc = _Svc()
    seq = [0.69, 0.71, 0.68, 0.70, 0.69, 0.71]

    async def _scenario():
        with (
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_ALPHA", 1.0),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_T_HIGH", 0.72),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_T_LOW", 0.45),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_CONFIRM_STREAK", 3),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_RECOVERY_STREAK", 2),
        ):
            for val in seq:
                with patch(
                    "app.services.mission_fusion_runtime.fuse_threat_scores",
                    return_value=_fusion(val),
                ):
                    await runtime.process_telemetry_fusion(1, svc)

    _run(_scenario())
    snap = runtime.get_fusion_snapshot(1)
    assert snap is not None
    assert snap["state"] != "CONFIRMED_JAMMING"


def test_state_transitions_normal_suspect_confirmed_recovering_normal():
    svc = _Svc()
    seq = [0.50, 0.80, 0.81, 0.82, 0.40, 0.39, 0.38]

    async def _scenario():
        with (
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_ALPHA", 1.0),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_T_HIGH", 0.72),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_T_LOW", 0.45),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_CONFIRM_STREAK", 3),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_RECOVERY_STREAK", 2),
        ):
            for val in seq:
                with patch(
                    "app.services.mission_fusion_runtime.fuse_threat_scores",
                    return_value=_fusion(val),
                ):
                    await runtime.process_telemetry_fusion(1, svc)

    _run(_scenario())
    snap = runtime.get_fusion_snapshot(1)
    assert snap is not None
    assert snap["state"] == "NORMAL"
    assert snap["recovery_streak"] >= 2


def test_dynamic_zone_lifecycle_create_merge_expire():
    svc = _Svc()
    fake_now = {"t": 1_000.0}

    def _now():
        return fake_now["t"]

    async def _tick(level: float):
        with patch("app.services.mission_fusion_runtime.fuse_threat_scores", return_value=_fusion(level)):
            await runtime.process_telemetry_fusion(1, svc)

    async def _scenario():
        with (
            patch("app.services.mission_fusion_runtime.time.time", side_effect=_now),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_ALPHA", 1.0),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_CONFIRM_STREAK", 1),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_RECOVERY_STREAK", 1),
            patch("app.services.mission_fusion_runtime.FUSION_DYNAMIC_ZONE_TTL_SEC", 5.0),
            patch("app.services.mission_fusion_runtime.FUSION_DYNAMIC_ZONE_MERGE_DISTANCE_M", 200.0),
        ):
            # Create
            await _tick(0.95)
            z1 = runtime.get_dynamic_zones_snapshot()
            assert len(z1) == 1

            # Update/merge nearby
            svc.telemetry[1]["lat"] += 0.0003
            fake_now["t"] += 1.0
            await _tick(0.90)
            z2 = runtime.get_dynamic_zones_snapshot()
            assert len(z2) == 1
            assert z2[0]["state"] == "active"

            # Recovery -> fading
            fake_now["t"] += 1.0
            await _tick(0.10)
            z3 = runtime.get_dynamic_zones_snapshot()
            assert len(z3) == 1
            assert z3[0]["state"] == "fading"

            # TTL expire
            fake_now["t"] += 10.0
            z4 = runtime.get_dynamic_zones_snapshot()
            assert z4 == []

    _run(_scenario())


def test_suspected_zone_becomes_confirmed_when_inside_and_risk_confirmed():
    svc = _Svc()

    async def _scenario():
        with (
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_ALPHA", 1.0),
            patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_CONFIRM_STREAK", 1),
        ):
            created = runtime.add_manual_suspected_zone(
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[41.969, 45.039], [41.971, 45.039], [41.971, 45.041], [41.969, 45.041], [41.969, 45.039]]],
                },
                source="operator",
            )
            assert created["state"] == "DRAWN"
            with patch(
                "app.services.mission_fusion_runtime.fuse_threat_scores",
                return_value=_fusion(0.95),
            ):
                await runtime.process_telemetry_fusion(1, svc)

    _run(_scenario())
    snap = runtime.get_dynamic_zones_snapshot()
    assert any(z["state"] == "CONFIRMED" for z in snap)
