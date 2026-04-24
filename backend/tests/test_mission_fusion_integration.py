"""
Интеграция fusion → replanner (Промпт 11). Моки MAVLink / replan без SITL.

Run: pytest backend/tests/test_mission_fusion_integration.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shapely.geometry import Polygon

from app.schemas.mission import DroneRoute, RoutePoint
from app.services.mission_fusion_runtime import (
    FusionMissionContext,
    clear_fusion_context,
    process_telemetry_fusion,
    set_fusion_context,
)
from app.services.telemetry_features import reset_telemetry_buffers
from app.services.threat_fusion import reset_fusion_state

FIELD = Polygon([(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)])


def _drone(drone_id: int) -> MagicMock:
    d = MagicMock()
    d.id = drone_id
    d.battery_capacity = 100
    d.max_speed = 10.0
    d.status = "active"
    return d


def _route(drone_id: int, n: int = 5) -> DroneRoute:
    pts = [
        RoutePoint(lat=0.05, lng=round(0.01 + i * 0.01, 4))
        for i in range(n)
    ]
    return DroneRoute(drone_id=drone_id, route=pts)


@pytest.fixture
def clean_runtime():
    clear_fusion_context()
    reset_telemetry_buffers()
    reset_fusion_state()
    yield
    clear_fusion_context()
    reset_telemetry_buffers()
    reset_fusion_state()


def test_high_threat_triggers_replan_once_then_rate_limited(clean_runtime):
    """Синтетически высокая угроза: debounce → один вызов replan; далее rate limit."""

    async def _run():
        routes = [_route(1), _route(2)]
        ctx = FusionMissionContext(
            mission_id=42,
            field_id=7,
            field_polygon=FIELD,
            drones=[_drone(1), _drone(2)],
            risk_zones=[],
            current_routes=routes,
            visited_counts={1: 0, 2: 0},
        )
        set_fusion_context(ctx)

        class Svc:
            telemetry = {
                1: {"lat": 0.05, "lng": 0.05, "drone_id": 1, "groundspeed": 0.0},
                2: {"lat": 0.05, "lng": 0.06, "drone_id": 2, "groundspeed": 0.0},
            }

            async def update_mission(self, *_a, **_k):
                return True

        svc = Svc()

        def _fake_fuse(_scores, drone_id=0):
            return (0.95, {"fused_threat_level": 0.95})

        with patch(
            "app.services.mission_fusion_runtime.replan_on_new_risk_zone",
            new_callable=AsyncMock,
        ) as rp:
            rp.return_value = {
                "status": "replanned",
                "updated_routes": [r.model_dump() for r in routes],
                "new_irm": 0.85,
            }
            with patch(
                "app.services.mission_fusion_runtime.fuse_threat_scores",
                side_effect=_fake_fuse,
            ):
                with patch(
                    "app.services.mission_fusion_runtime.FUSION_AUTO_REPLAN_STREAK",
                    3,
                ):
                    with patch(
                        "app.services.mission_fusion_runtime.FUSION_THRESHOLD",
                        0.5,
                    ):
                        with patch(
                            "app.services.mission_fusion_runtime.FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC",
                            3600.0,
                        ):
                            for _ in range(3):
                                await process_telemetry_fusion(1, svc)
                            assert rp.call_count == 1

                            for _ in range(3):
                                await process_telemetry_fusion(1, svc)
                            assert rp.call_count == 1

    asyncio.run(_run())


def test_without_context_no_replan(clean_runtime):
    async def _run():
        with patch(
            "app.services.mission_fusion_runtime.replan_on_new_risk_zone",
            new_callable=AsyncMock,
        ) as rp:
            class Svc:
                telemetry = {1: {"lat": 1.0, "lng": 2.0}}

            await process_telemetry_fusion(1, Svc())
            rp.assert_not_called()

    asyncio.run(_run())


def test_confirmed_flow_calls_safety_then_replan_then_update(clean_runtime):
    async def _run():
        routes = [_route(1), _route(2)]
        ctx = FusionMissionContext(
            mission_id=77,
            field_id=7,
            field_polygon=FIELD,
            drones=[_drone(1), _drone(2)],
            risk_zones=[],
            current_routes=routes,
            visited_counts={1: 0, 2: 0},
        )
        set_fusion_context(ctx)

        events: list[str] = []

        class Svc:
            telemetry = {
                1: {"lat": 0.05, "lng": 0.05, "drone_id": 1, "groundspeed": 0.0},
                2: {"lat": 0.05, "lng": 0.06, "drone_id": 2, "groundspeed": 0.0},
            }

            async def apply_safety_action(self, *_a, **_k):
                events.append("safety")
                return True

            async def update_mission(self, *_a, **_k):
                events.append("update")
                return True

            async def set_auto_mode(self, *_a, **_k):
                events.append("auto")
                return True

        svc = Svc()

        def _fake_fuse(_scores, drone_id=0):
            return (0.95, {"fused_threat_level": 0.95})

        with patch(
            "app.services.mission_fusion_runtime.replan_on_new_risk_zone",
            new_callable=AsyncMock,
        ) as rp:
            async def _rp(*_a, **_k):
                events.append("replan")
                return {
                    "status": "replanned",
                    "updated_routes": [r.model_dump() for r in routes],
                    "new_irm": 0.85,
                }

            rp.side_effect = _rp
            with patch(
                "app.services.mission_fusion_runtime.fuse_threat_scores",
                side_effect=_fake_fuse,
            ):
                with patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_ALPHA", 1.0):
                    with patch("app.services.mission_fusion_runtime.FUSION_DETECTOR_CONFIRM_STREAK", 1):
                        with patch("app.services.mission_fusion_runtime.FUSION_AUTO_REPLAN_STREAK", 1):
                            with patch("app.services.mission_fusion_runtime.FUSION_THRESHOLD", 0.5):
                                with patch(
                                    "app.services.mission_fusion_runtime.ENABLE_SAFETY_ACTION_BEFORE_REPLAN",
                                    True,
                                ):
                                    await process_telemetry_fusion(1, svc)

        assert "safety" in events
        assert "replan" in events
        assert events.index("safety") < events.index("replan")
        assert "update" in events
        assert "auto" in events

    asyncio.run(_run())
