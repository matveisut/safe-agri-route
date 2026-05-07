"""
Regression tests covering fixes made during code review:

1. /simulate-loss endpoint — Body(...) fix
   Verifies that FastAPI correctly parses `drone_id` from query param
   and the request body simultaneously (regression of walrus/Body(...) fix).

2. /plan endpoint — silent drone-id drop behaviour
   Verifies that missing drone_ids are skipped and 400 is returned when
   ALL requested ids are invalid (vs. the old walrus path that was confusing).

3. read_telemetry_loop — packet-loss live-mode sleep
   Verifies that the live path calls asyncio.sleep between every dropped
   packet so the event loop is not starved (regression of hot-spin fix).
"""

from __future__ import annotations

import asyncio
import sys
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.deps import require_operator
from app.database import get_db
from app.main import app
from app.services.mavlink_service import MAVLinkService
from app.services.mission_fusion_runtime import clear_fusion_context


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

async def _fake_operator():
    return SimpleNamespace(id=1, role="operator", email="op@test.com", is_active=True)


def _fake_db():
    return AsyncMock()


def _make_wkb_field(lat: float = 45.04, lng: float = 41.97):
    """Return a mock DB field row with a tiny WKB geometry blob."""
    import shapely.wkb
    from shapely.geometry import Polygon

    poly = Polygon([
        (lng, lat), (lng + 0.01, lat), (lng + 0.01, lat + 0.01),
        (lng, lat + 0.01), (lng, lat),
    ])
    f = MagicMock()
    f.id = 1
    f.name = "Test field"
    f.geometry = MagicMock()
    f.geometry.data = shapely.wkb.dumps(poly)
    return f


def _make_drone(drone_id: int):
    d = MagicMock()
    d.id = drone_id
    d.battery_capacity = 5000
    d.max_speed = 10.0
    d.status = "idle"
    return d


def _setup():
    clear_fusion_context()
    app.dependency_overrides[require_operator] = _fake_operator
    app.dependency_overrides[get_db] = _fake_db


def _teardown():
    clear_fusion_context()
    app.dependency_overrides.clear()


# pytest setup_function only applies to module-level functions.
# Class tests use setup_method / teardown_method (added to each class below).
def setup_function():
    _setup()


def teardown_function():
    _teardown()


def teardown_function():
    clear_fusion_context()
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. /simulate-loss — Body(...) + Query(...) both parsed correctly
# ---------------------------------------------------------------------------

class TestSimulateLossEndpointSignature:
    """
    Regression: before the fix, FastAPI would treat the Pydantic body as a
    query-style param when mixed with Query(...), causing 422 on every call.
    """

    def setup_method(self):
        _setup()

    def teardown_method(self):
        _teardown()

    def _minimal_body(self, field_id: int = 1, drone_ids: list | None = None,
                      routes: list | None = None):
        return {
            "field_id": field_id,
            "drone_ids": drone_ids or [2, 3],
            "current_routes": routes or [
                {"drone_id": 2, "route": [{"lat": 45.04, "lng": 41.97}]},
            ],
            "visited_counts": {2: 0, 3: 0},
        }

    def test_accepts_body_and_query_together_not_422(self):
        """Sending body + ?drone_id= must NOT return 422 (binding error)."""
        with patch("app.api.routers.mission.field_repo") as mock_field_repo, \
             patch("app.api.routers.mission.drone_repo") as mock_drone_repo, \
             patch("app.api.routers.mission.risk_zone_repo") as mock_rz_repo, \
             patch("app.api.routers.mission.replan_on_drone_loss") as mock_replan, \
             patch("app.api.routers.mission.mavlink_service") as mock_mav:

            mock_field_repo.get = AsyncMock(return_value=_make_wkb_field())
            mock_drone_repo.get = AsyncMock(side_effect=lambda db, d_id: _make_drone(d_id))
            mock_rz_repo.get_multi = AsyncMock(return_value=[])
            mock_mav.update_mission = AsyncMock(return_value=True)
            mock_replan.return_value = {
                "status": "ok",
                "updated_routes": [{"drone_id": 2, "route": [{"lat": 45.04, "lng": 41.97}]}],
                "new_irm": 0.9,
            }

            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/mission/42/simulate-loss?drone_id=1",
                    json=self._minimal_body(),
                )

        # Must NOT be 422 (validation error from wrong binding)
        assert resp.status_code != 422, (
            f"Got 422 — Body/Query binding is broken. Detail: {resp.json()}"
        )

    def test_missing_body_returns_422(self):
        """Omitting the body entirely must still 422 (body is required)."""
        with TestClient(app) as client:
            resp = client.post("/api/v1/mission/1/simulate-loss?drone_id=1")
        assert resp.status_code == 422

    def test_missing_drone_id_query_returns_422(self):
        """Omitting ?drone_id= must 422 (Query is required)."""
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/mission/1/simulate-loss",
                json=self._minimal_body(),
            )
        assert resp.status_code == 422

    def test_field_not_found_returns_404(self):
        """Field lookup returns None → 404."""
        with patch("app.api.routers.mission.field_repo") as mock_field_repo:
            mock_field_repo.get = AsyncMock(return_value=None)

            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/mission/1/simulate-loss?drone_id=1",
                    json=self._minimal_body(),
                )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. /plan — invalid drone_ids silently skipped → 400 when all invalid
# ---------------------------------------------------------------------------

class TestPlanEndpointDroneValidation:
    """
    Regression: walrus `if clone := drone:` was confusing but still skipped
    None results. After the fix we use a plain `if drone:` — behaviour is
    identical; all-invalid IDs must still 400.
    """

    def setup_method(self):
        _setup()

    def teardown_method(self):
        _teardown()

    _FIELD_GEOJSON = {
        "field_id": 1,
        "drone_ids": [99, 100],  # neither exists in DB
    }

    def test_all_invalid_drone_ids_returns_400(self):
        with patch("app.api.routers.mission.field_repo") as mock_field_repo, \
             patch("app.api.routers.mission.drone_repo") as mock_drone_repo, \
             patch("app.api.routers.mission.risk_zone_repo") as mock_rz_repo:

            mock_field_repo.get = AsyncMock(return_value=_make_wkb_field())
            mock_drone_repo.get = AsyncMock(return_value=None)  # always not found
            mock_rz_repo.get_multi = AsyncMock(return_value=[])

            with TestClient(app) as client:
                resp = client.post("/api/v1/mission/plan", json=self._FIELD_GEOJSON)

        assert resp.status_code == 400
        assert "drones" in resp.json()["detail"].lower()

    def test_partial_invalid_drone_ids_skipped_and_plan_proceeds(self):
        """One valid drone out of two: planning should not 400."""
        with patch("app.api.routers.mission.field_repo") as mock_field_repo, \
             patch("app.api.routers.mission.drone_repo") as mock_drone_repo, \
             patch("app.api.routers.mission.risk_zone_repo") as mock_rz_repo, \
             patch("app.api.routers.mission.RoutingService") as mock_rs:

            mock_field_repo.get = AsyncMock(return_value=_make_wkb_field())
            # drone 1 → valid, drone 99 → None
            mock_drone_repo.get = AsyncMock(
                side_effect=lambda db, d_id: _make_drone(d_id) if d_id == 1 else None
            )
            mock_rz_repo.get_multi = AsyncMock(return_value=[])

            # Stub routing result so we never hit real OR-Tools
            mock_result = MagicMock()
            mock_result.routes = [{"drone_id": 1, "route": [{"lat": 45.04, "lng": 41.97}]}]
            mock_result.reliability_index = 1.0
            mock_result.estimated_coverage_pct = 90.0
            mock_result.risk_grid_preview = []
            mock_rs.plan_mission.return_value = mock_result

            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/mission/plan",
                    json={"field_id": 1, "drone_ids": [1, 99]},
                )

        assert resp.status_code == 200

    def test_field_not_found_returns_404(self):
        with patch("app.api.routers.mission.field_repo") as mock_field_repo:
            mock_field_repo.get = AsyncMock(return_value=None)

            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/mission/plan",
                    json={"field_id": 999, "drone_ids": [1]},
                )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. read_telemetry_loop — live-mode packet-drop calls asyncio.sleep
# ---------------------------------------------------------------------------

class TestPacketLossLiveModeNoHotSpin:
    """
    Regression: before the fix, the live read_telemetry_loop had a bare
    `continue` after a packet drop, hot-spinning the event loop indefinitely.
    After the fix, every dropped packet awaits asyncio.sleep(TELEMETRY_WINDOW).

    Strategy: mock `_should_drop_packet` to return True for the first N calls
    then False; patch asyncio.sleep so it's instant but recorded; verify
    sleep was called exactly N times before the first real frame.
    """

    def setup_method(self):
        _setup()

    def teardown_method(self):
        _teardown()

    def _make_svc_with_fake_connection(self):
        from app.services.mavlink_service import MAVLinkService, STATUS_ACTIVE

        svc = MAVLinkService()

        # Minimal mocked connection entry so we don't take the simulation path
        svc.connections[1] = MagicMock(name="conn")
        svc.telemetry[1] = {
            "drone_id": 1, "lat": 45.0, "lng": 42.0, "alt": 30.0,
            "battery": 90, "heading": 180, "status": STATUS_ACTIVE, "groundspeed": 5.0,
        }
        return svc

    def test_sleep_called_per_dropped_packet(self):
        """asyncio.sleep must be awaited once per dropped packet."""
        import app.services.mavlink_service as mod
        from app.services.mavlink_service import TELEMETRY_WINDOW

        svc = self._make_svc_with_fake_connection()

        # Drop the first 3 packets, then let one through
        drop_counter = {"n": 0}

        def drop_first_three(drone_id: int) -> bool:
            drop_counter["n"] += 1
            return drop_counter["n"] <= 3

        svc._should_drop_packet = drop_first_three
        svc._update_packet_counters = MagicMock()
        svc._attach_packet_metrics = MagicMock()

        # Fake _blocking_read_telemetry so the "real" read returns instantly
        frame_template = dict(svc.telemetry[1])
        frame_template["_heartbeat"] = True

        def fake_blocking_read(conn, drone_id):
            return dict(frame_template)

        svc._blocking_read_telemetry = fake_blocking_read

        sleep_calls: list[float] = []

        async def fake_sleep(t: float) -> None:
            sleep_calls.append(t)

        async def _collect_one():
            with patch.object(mod.asyncio, "sleep", side_effect=fake_sleep):
                async for _frame in svc.read_telemetry_loop(1):
                    break  # take only the first yielded frame

        asyncio.run(_collect_one())

        # 3 drops → 3 sleep calls with TELEMETRY_WINDOW duration
        assert len(sleep_calls) == 3, (
            f"Expected 3 sleep calls (one per drop), got {len(sleep_calls)}"
        )
        assert all(t == pytest.approx(TELEMETRY_WINDOW) for t in sleep_calls), (
            f"Sleep duration should be TELEMETRY_WINDOW={TELEMETRY_WINDOW}, got {sleep_calls}"
        )

    def test_no_drops_no_extra_sleep(self):
        """When no packets are dropped the live path must NOT call sleep."""
        import app.services.mavlink_service as mod

        svc = self._make_svc_with_fake_connection()
        svc._should_drop_packet = lambda drone_id: False
        svc._update_packet_counters = MagicMock()
        svc._attach_packet_metrics = MagicMock()

        frame_template = dict(svc.telemetry[1])
        frame_template["_heartbeat"] = True
        svc._blocking_read_telemetry = lambda conn, d: dict(frame_template)

        sleep_calls: list[float] = []

        async def fake_sleep(t: float) -> None:
            sleep_calls.append(t)

        async def _collect_one():
            with patch.object(mod.asyncio, "sleep", side_effect=fake_sleep):
                async for _frame in svc.read_telemetry_loop(1):
                    break

        asyncio.run(_collect_one())

        assert sleep_calls == [], (
            f"Expected no sleep calls when no drops, got {sleep_calls}"
        )

    def test_drop_rate_1_blocks_until_sleep_releases(self):
        """With 100% drop rate, sleep must be the only thing keeping the loop from spinning."""
        import app.services.mavlink_service as mod
        from app.services.mavlink_service import TELEMETRY_WINDOW

        svc = self._make_svc_with_fake_connection()

        # Allow at most 5 iterations before forcing stop
        iteration = {"n": 0}

        def drop_always(drone_id: int) -> bool:
            iteration["n"] += 1
            return True  # always drop

        svc._should_drop_packet = drop_always
        svc._update_packet_counters = MagicMock()

        sleep_calls: list[float] = []
        STOP_AFTER = 5

        async def fake_sleep(t: float) -> None:
            sleep_calls.append(t)
            if len(sleep_calls) >= STOP_AFTER:
                raise asyncio.CancelledError()

        async def _run():
            try:
                with patch.object(mod.asyncio, "sleep", side_effect=fake_sleep):
                    async for _ in svc.read_telemetry_loop(1):
                        pass
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        # Must have slept exactly STOP_AFTER times, not N*1000
        assert len(sleep_calls) == STOP_AFTER
        assert all(t == pytest.approx(TELEMETRY_WINDOW) for t in sleep_calls)
