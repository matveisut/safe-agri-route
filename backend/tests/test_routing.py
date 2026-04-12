"""
Unit tests for RoutingService (Risk-Weighted Voronoi mission planner).

Run with:
    pytest backend/tests/test_routing.py -v
"""
import sys
import os

# Make sure `app` package is importable when running from the repo root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shapely.wkb
import pytest
from unittest.mock import MagicMock
from shapely.geometry import Polygon, Point

from app.services.routing_service import RoutingService, MissionPlanResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_field(polygon: Polygon):
    field = MagicMock()
    field.geometry = MagicMock()
    field.geometry.data = shapely.wkb.dumps(polygon)
    return field


def _make_risk_zone(polygon: Polygon, severity: float):
    rz = MagicMock()
    rz.geometry = MagicMock()
    rz.geometry.data = shapely.wkb.dumps(polygon)
    rz.severity_weight = severity
    return rz


def _make_drone(drone_id: int, battery: int = 100, speed: float = 10.0):
    drone = MagicMock()
    drone.id = drone_id
    drone.battery_capacity = battery
    drone.max_speed = speed
    return drone


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlanMissionAvoidRiskZones:
    """
    Field: rectangle (0,0)–(0.01,0.01)
    Risk zone: small square (0.004,0.004)–(0.006,0.006) with severity=0.9
    Drones: 2
    Grid step: 0.001°  →  ~100 field points, ~4 excluded inside RZ
    """

    def setup_method(self):
        self.field_polygon = Polygon([
            (0.0, 0.0), (0.01, 0.0), (0.01, 0.01), (0.0, 0.01),
        ])
        self.risk_polygon = Polygon([
            (0.004, 0.004), (0.006, 0.004), (0.006, 0.006), (0.004, 0.006),
        ])
        self.field = _make_field(self.field_polygon)
        self.risk_zone = _make_risk_zone(self.risk_polygon, severity=0.9)
        self.drone1 = _make_drone(1, battery=100, speed=10.0)
        self.drone2 = _make_drone(2, battery=80, speed=8.0)

    def _run(self):
        return RoutingService.plan_mission(
            self.field,
            [self.drone1, self.drone2],
            [self.risk_zone],
            step_deg=0.001,
        )

    def test_returns_mission_plan_result(self):
        result = self._run()
        assert isinstance(result, MissionPlanResult)

    def test_no_waypoint_inside_risk_zone(self):
        """No drone waypoint should lie inside the REB risk zone."""
        result = self._run()
        for drone_route in result.routes:
            for wp in drone_route.route:
                p = Point(wp.lng, wp.lat)
                assert not self.risk_polygon.contains(p), (
                    f"Waypoint ({wp.lat}, {wp.lng}) is inside the risk zone"
                )

    def test_reliability_index_present_and_valid(self):
        result = self._run()
        assert hasattr(result, "reliability_index"), "reliability_index missing"
        assert 0.0 <= result.reliability_index <= 1.0, (
            f"reliability_index={result.reliability_index} not in [0, 1]"
        )

    def test_estimated_coverage_pct_present_and_positive(self):
        result = self._run()
        assert hasattr(result, "estimated_coverage_pct"), "estimated_coverage_pct missing"
        assert result.estimated_coverage_pct > 0, (
            f"estimated_coverage_pct={result.estimated_coverage_pct} should be > 0"
        )


class TestRiskWeightedVoronoi:
    """Unit tests for the weighted Lloyd's algorithm in isolation."""

    def test_splits_into_k_zones(self):
        pts = [(Point(float(i), float(j)), 1.0) for i in range(10) for j in range(10)]
        zones = RoutingService.risk_weighted_voronoi(pts, k=3)
        assert len(zones) == 3

    def test_all_points_assigned(self):
        pts = [(Point(float(i), 0.0), 1.0) for i in range(20)]
        zones = RoutingService.risk_weighted_voronoi(pts, k=4)
        total = sum(len(z) for z in zones)
        assert total == len(pts)

    def test_k_larger_than_points_clamps(self):
        pts = [(Point(float(i), 0.0), 1.0) for i in range(3)]
        zones = RoutingService.risk_weighted_voronoi(pts, k=10)
        total = sum(len(z) for z in zones)
        assert total == len(pts)

    def test_empty_input_returns_empty(self):
        zones = RoutingService.risk_weighted_voronoi([], k=3)
        assert zones == []

    def test_single_zone(self):
        pts = [(Point(float(i), 0.0), 1.0) for i in range(5)]
        zones = RoutingService.risk_weighted_voronoi(pts, k=1)
        assert len(zones) == 1
        assert len(zones[0]) == 5


class TestGenerateWeightedGrid:
    """Unit tests for the grid generator."""

    def test_safe_points_excluded_inside_risk_zone(self):
        field_polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        risk_polygon = Polygon([(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)])
        rz = _make_risk_zone(risk_polygon, severity=0.5)

        weighted, total = RoutingService.generate_weighted_grid(
            field_polygon, [rz], step=0.1
        )

        safe_points = [p for p, _ in weighted]
        for p in safe_points:
            assert not risk_polygon.contains(p)

    def test_total_count_includes_risk_zone_points(self):
        field_polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        risk_polygon = Polygon([(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)])
        rz = _make_risk_zone(risk_polygon, severity=0.5)

        weighted, total = RoutingService.generate_weighted_grid(
            field_polygon, [rz], step=0.1
        )
        assert total >= len(weighted)

    def test_weights_are_positive(self):
        field_polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        weighted, _ = RoutingService.generate_weighted_grid(field_polygon, [], step=0.1)
        for _, w in weighted:
            assert w > 0


class TestProximityRisk:
    """Unit tests for the internal risk computation helper."""

    def test_point_far_from_zone_has_zero_risk(self):
        rz_poly = Polygon([(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)])
        rz_data = [(rz_poly, 0.9)]
        p = Point(10.0, 10.0)
        risk = RoutingService._proximity_risk(p, rz_data, influence_radius=0.5)
        assert risk == 0.0

    def test_point_adjacent_to_zone_has_nonzero_risk(self):
        rz_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        rz_data = [(rz_poly, 1.0)]
        p = Point(1.05, 0.5)  # just outside boundary
        risk = RoutingService._proximity_risk(p, rz_data, influence_radius=0.2)
        assert risk > 0.0

    def test_risk_capped_at_one(self):
        rz_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        rz_data = [(rz_poly, 2.0)]  # severity > 1 edge case
        p = Point(1.001, 0.5)
        risk = RoutingService._proximity_risk(p, rz_data, influence_radius=1.0)
        assert risk <= 1.0
