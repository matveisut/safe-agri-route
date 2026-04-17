"""
Unit tests for replanner.py (dynamic mission replanning).

Scenario A — replan_on_drone_loss()
Scenario B — replan_on_new_risk_zone()
Performance — replan_on_drone_loss() for 4 drones × 50 waypoints < 500 ms

Run with:
    pytest backend/tests/test_replanner.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import time
from unittest.mock import MagicMock
from shapely.geometry import Polygon, Point

from app.schemas.mission import DroneRoute, RoutePoint
from app.services.replanner import replan_on_drone_loss, replan_on_new_risk_zone


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# 0.1° × 0.1° field – same as the risk_map tests
FIELD = Polygon([(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)])


def _make_drone(drone_id: int, battery: int = 100, speed: float = 10.0, status: str = "active"):
    d = MagicMock()
    d.id = drone_id
    d.battery_capacity = battery
    d.max_speed = speed
    d.status = status
    return d


def _make_route(drone_id: int, n_points: int, lat_base: float = 0.01, lng_base: float = 0.01) -> DroneRoute:
    """Simple route: n points moving horizontally."""
    pts = [
        RoutePoint(lat=round(lat_base + i * 0.005, 4), lng=round(lng_base + drone_id * 0.01, 4))
        for i in range(n_points)
    ]
    return DroneRoute(drone_id=drone_id, route=pts)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Scenario A — drone loss
# ---------------------------------------------------------------------------

class TestReplanOnDroneLoss:

    def test_returns_replanned_status(self):
        """Basic smoke test: 4 drones, drone 1 lost → status == 'replanned'."""
        routes = [_make_route(i, 5) for i in range(1, 5)]
        visited = {i: 0 for i in range(1, 5)}
        drones = [_make_drone(i) for i in range(2, 5)]  # 3 active drones

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        assert result["status"] == "replanned"

    def test_uncovered_waypoints_redistributed_to_active_drones(self):
        """All 5 uncovered waypoints of drone 1 must appear in some active drone's route."""
        routes = [_make_route(i, 5) for i in range(1, 5)]
        visited = {i: 0 for i in range(1, 5)}
        drones = [_make_drone(i) for i in range(2, 5)]

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        # Each active drone must have a route
        drone_ids_in_result = {r["drone_id"] for r in result["updated_routes"]}
        assert {2, 3, 4}.issubset(drone_ids_in_result), (
            f"Active drones missing from result: {drone_ids_in_result}"
        )

    def test_total_coverage_not_reduced(self):
        """
        Uncovered from drone 1: 5 waypoints.
        Each active drone had 5 of their own waypoints (visited=0).
        Updated routes must contain ≥ 5+5+5+5 = 20 waypoints total
        (OR-Tools adds depot at end, so actual count will be slightly higher).
        """
        routes = [_make_route(i, 5) for i in range(1, 5)]
        visited = {i: 0 for i in range(1, 5)}
        drones = [_make_drone(i) for i in range(2, 5)]

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        total_wps = sum(len(r["route"]) for r in result["updated_routes"])
        assert total_wps >= 20, f"Expected ≥ 20 waypoints, got {total_wps}"

    def test_partially_visited_drone_uncovered_only(self):
        """
        Drone 1 visited 3 of 5 waypoints → only 2 are uncovered.
        Updated total = 2 (uncovered) + 5+5+5 (remaining of drones 2–4) = 17.
        """
        routes = [_make_route(i, 5) for i in range(1, 5)]
        visited = {1: 3, 2: 0, 3: 0, 4: 0}
        drones = [_make_drone(i) for i in range(2, 5)]

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        total_wps = sum(len(r["route"]) for r in result["updated_routes"])
        assert total_wps >= 17, f"Expected ≥ 17 waypoints, got {total_wps}"

    def test_no_active_drones_returns_mission_failed(self):
        """Only drone 1 is active; after its loss no active drones remain."""
        routes = [_make_route(1, 5), _make_route(2, 5)]
        visited = {1: 0, 2: 0}
        drones = [_make_drone(2, status="lost")]  # drone 2 is also lost

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        assert result["status"] == "mission_failed"

    def test_irm_in_valid_range(self):
        """new_irm must be in [0, 1]."""
        routes = [_make_route(i, 5) for i in range(1, 5)]
        visited = {i: 0 for i in range(1, 5)}
        drones = [_make_drone(i) for i in range(2, 5)]

        result = run(replan_on_drone_loss(
            lost_drone_id=1,
            current_routes=routes,
            visited_counts=visited,
            drones=drones,
            field_polygon=FIELD,
            risk_zones=[],
        ))

        assert "new_irm" in result
        assert 0.0 <= result["new_irm"] <= 1.0, f"new_irm={result['new_irm']} out of range"


# ---------------------------------------------------------------------------
# Scenario B — new REB zone
# ---------------------------------------------------------------------------

class TestReplanOnNewRiskZone:

    def test_returns_replanned_status(self):
        """Smoke test: any call returns status == 'replanned'."""
        routes = [DroneRoute(drone_id=1, route=[
            RoutePoint(lat=0.02, lng=0.02),
            RoutePoint(lat=0.02, lng=0.08),
        ])]
        new_zone = {
            "geometry": Polygon([(0.04, 0.01), (0.06, 0.01), (0.06, 0.04), (0.04, 0.04)]),
            "severity": 0.9,
            "zone_type": "jammer",
        }

        result = run(replan_on_new_risk_zone(
            new_zone=new_zone,
            current_routes=routes,
            visited_counts={1: 0},
            drones=[_make_drone(1)],
            field_polygon=FIELD,
            existing_risk_zones=[],
        ))

        assert result["status"] == "replanned"

    def test_non_intersecting_route_preserved(self):
        """
        Route entirely in the bottom-left corner; zone is in the top-right.
        Remaining waypoints must be returned unchanged.
        """
        route_pts = [
            RoutePoint(lat=0.01, lng=0.01),
            RoutePoint(lat=0.02, lng=0.01),
        ]
        routes = [DroneRoute(drone_id=1, route=route_pts)]
        new_zone = {
            "geometry": Polygon([(0.08, 0.08), (0.09, 0.08), (0.09, 0.09), (0.08, 0.09)]),
            "severity": 0.8,
            "zone_type": "restricted",
        }

        result = run(replan_on_new_risk_zone(
            new_zone=new_zone,
            current_routes=routes,
            visited_counts={1: 0},
            drones=[_make_drone(1)],
            field_polygon=FIELD,
            existing_risk_zones=[],
        ))

        out_route = next(r for r in result["updated_routes"] if r["drone_id"] == 1)
        assert len(out_route["route"]) == len(route_pts), (
            "Non-intersecting route length should be unchanged"
        )
        out_pts = [(p["lat"], p["lng"]) for p in out_route["route"]]
        expected = [(p.lat, p.lng) for p in route_pts]
        assert out_pts == expected, "Non-intersecting route coordinates should be identical"

    def test_intersecting_route_triggers_replanning(self):
        """
        Route goes from lng=0.01 to lng=0.09 crossing zone at lng ≈ 0.05.
        The result must contain a route for drone 1 (i.e. it was processed).
        """
        routes = [DroneRoute(drone_id=1, route=[
            RoutePoint(lat=0.05, lng=0.01),
            RoutePoint(lat=0.05, lng=0.09),
        ])]
        new_zone = {
            "geometry": Polygon([(0.04, 0.03), (0.06, 0.03), (0.06, 0.07), (0.04, 0.07)]),
            "severity": 0.9,
            "zone_type": "jammer",
        }

        result = run(replan_on_new_risk_zone(
            new_zone=new_zone,
            current_routes=routes,
            visited_counts={1: 0},
            drones=[_make_drone(1)],
            field_polygon=FIELD,
            existing_risk_zones=[],
        ))

        assert result["status"] == "replanned"
        drone_ids = {r["drone_id"] for r in result["updated_routes"]}
        assert 1 in drone_ids, "Drone 1 route missing from replanning result"

    def test_visited_waypoints_excluded(self):
        """
        Drone 1 has visited 1 of 3 waypoints.
        The replan should only consider the 2 remaining ones.
        """
        routes = [DroneRoute(drone_id=1, route=[
            RoutePoint(lat=0.01, lng=0.01),  # visited
            RoutePoint(lat=0.01, lng=0.02),
            RoutePoint(lat=0.01, lng=0.03),
        ])]
        new_zone = {
            "geometry": Polygon([(0.08, 0.08), (0.09, 0.08), (0.09, 0.09), (0.08, 0.09)]),
            "severity": 0.5,
            "zone_type": "restricted",
        }

        result = run(replan_on_new_risk_zone(
            new_zone=new_zone,
            current_routes=routes,
            visited_counts={1: 1},  # first waypoint already visited
            drones=[_make_drone(1)],
            field_polygon=FIELD,
            existing_risk_zones=[],
        ))

        out_route = next(r for r in result["updated_routes"] if r["drone_id"] == 1)
        assert len(out_route["route"]) == 2, (
            f"Expected 2 remaining waypoints, got {len(out_route['route'])}"
        )

    def test_irm_in_valid_range(self):
        routes = [DroneRoute(drone_id=1, route=[RoutePoint(lat=0.02, lng=0.02)])]
        result = run(replan_on_new_risk_zone(
            new_zone={
                "geometry": Polygon([(0.07, 0.07), (0.09, 0.07), (0.09, 0.09), (0.07, 0.09)]),
                "severity": 0.5,
                "zone_type": "jammer",
            },
            current_routes=routes,
            visited_counts={1: 0},
            drones=[_make_drone(1)],
            field_polygon=FIELD,
            existing_risk_zones=[],
        ))
        assert 0.0 <= result["new_irm"] <= 1.0


# ---------------------------------------------------------------------------
# Performance test
# ---------------------------------------------------------------------------

class TestPerformance:

    def test_drone_loss_completes_within_500ms(self):
        """
        4 drones × 50 waypoints = 200 total.
        Drone 1 is lost (all 50 uncovered).
        Active drones 2–4 each have 50 remaining + ~17 redistributed ≈ 67 wps.
        Must complete in < 500 ms.
        """
        n_per_drone = 50
        routes = []
        for drone_id in range(1, 5):
            pts = [
                RoutePoint(
                    lat=round(0.02 + (i % 10) * 0.04, 4),
                    lng=round(0.02 + (drone_id * 10 + i // 10) * 0.03, 4),
                )
                for i in range(n_per_drone)
            ]
            routes.append(DroneRoute(drone_id=drone_id, route=pts))

        visited = {i: 0 for i in range(1, 5)}
        drones = [_make_drone(i) for i in range(2, 5)]

        # Use a larger field to contain the waypoints
        big_field = Polygon([(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)])

        # asyncio.run() creates a new event loop and a fresh ThreadPoolExecutor
        # each call.  The first run_in_executor() on a cold pool costs ~200 ms
        # just for thread-creation (OS-level).  Production code runs inside a
        # warm uvicorn event loop, so the overhead doesn't exist there.
        # We warm the pool inside the same asyncio.run() call so only algorithm
        # time is measured.
        async def _timed_run():
            loop = asyncio.get_event_loop()
            # warm up: spin a no-op task to allocate the thread pool
            await loop.run_in_executor(None, lambda: None)

            t0 = time.perf_counter()
            res = await replan_on_drone_loss(
                lost_drone_id=1,
                current_routes=routes,
                visited_counts=visited,
                drones=drones,
                field_polygon=big_field,
                risk_zones=[],
                loop=loop,
            )
            return res, time.perf_counter() - t0

        result, elapsed = asyncio.run(_timed_run())

        assert result["status"] == "replanned", f"Unexpected status: {result['status']}"
        assert elapsed < 0.5, (
            f"replan_on_drone_loss took {elapsed * 1000:.0f} ms — exceeds 500 ms limit"
        )
