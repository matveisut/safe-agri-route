"""
Unit tests for build_risk_map() and get_risk_for_point().

Run with:
    pytest backend/tests/test_risk_map.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from shapely.geometry import Polygon, Point

from app.services.risk_map import build_risk_map, get_risk_for_point


# ---------------------------------------------------------------------------
# Shared field geometry
# ---------------------------------------------------------------------------

# 11×11 grid at step 0.01° → grid indices 0..10 in both axes
FIELD = Polygon([(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)])
STEP = 0.01


def _zone(polygon: Polygon, severity: float, zone_type: str) -> dict:
    return {"geometry": polygon, "severity": severity, "zone_type": zone_type}


# ---------------------------------------------------------------------------
# Test 1 — field without any risk zones → all values == 0.0
# ---------------------------------------------------------------------------

class TestNoRiskZones:
    def test_all_risk_values_zero(self):
        risk_grid, grid_points, grid_indices = build_risk_map(FIELD, [], grid_step=STEP)

        assert risk_grid is not None
        # Every cell in the grid should be exactly zero
        assert float(risk_grid.max()) == 0.0, (
            f"Expected max risk 0.0, got {risk_grid.max()}"
        )

    def test_grid_points_non_empty(self):
        _, grid_points, grid_indices = build_risk_map(FIELD, [], grid_step=STEP)
        assert len(grid_points) > 0
        assert len(grid_indices) == len(grid_points)


# ---------------------------------------------------------------------------
# Test 2 — "jammer" zone severity=1.0 in field centre → centre cells risk > 0.8
# ---------------------------------------------------------------------------

class TestJammerZone:
    # Jammer covers the centre of the field; grid point (0.05, 0.05) is inside
    JAMMER = Polygon([
        (0.03, 0.03), (0.07, 0.03), (0.07, 0.07), (0.03, 0.07),
    ])

    def _build(self):
        zones = [_zone(self.JAMMER, severity=1.0, zone_type="jammer")]
        return build_risk_map(FIELD, zones, grid_step=STEP)

    def test_central_cells_high_risk(self):
        risk_grid, grid_points, grid_indices = self._build()

        # Collect risk values for points that lie inside the jammer polygon
        inside_risks = [
            float(risk_grid[i, j])
            for (lat, lng), (i, j) in zip(grid_points, grid_indices)
            if self.JAMMER.contains(Point(lng, lat))
        ]

        assert len(inside_risks) > 0, "No grid points found inside jammer zone"
        assert all(r > 0.8 for r in inside_risks), (
            f"Expected all inside-zone risks > 0.8, got: {inside_risks}"
        )

    def test_grid_has_nonzero_values(self):
        risk_grid, _, _ = self._build()
        assert float(risk_grid.max()) > 0.0

    def test_proximity_decay_outside_zone(self):
        """Points just outside the jammer boundary should have nonzero risk.

        Uses a finer grid (step=0.002°) so that a grid point at x≈0.028 falls
        within the 0.005° influence radius of the jammer boundary at x=0.03.
        """
        zones = [_zone(self.JAMMER, severity=1.0, zone_type="jammer")]
        # Fine grid: points at 0.000, 0.002, 0.004, ..., 0.028, 0.030, ...
        # Point (lng=0.028, lat=0.05): distance to boundary at x=0.03 → 0.002° < 0.005°
        risk_grid, grid_points, grid_indices = build_risk_map(FIELD, zones, grid_step=0.002)

        nearby_risks = [
            float(risk_grid[i, j])
            for (lat, lng), (i, j) in zip(grid_points, grid_indices)
            if abs(lng - 0.028) < 0.001 and abs(lat - 0.05) < 0.001
        ]
        assert len(nearby_risks) > 0, "No grid point found near x=0.028, y=0.05"
        assert any(r > 0.0 for r in nearby_risks), (
            f"Expected nonzero proximity risk near jammer boundary, got: {nearby_risks}"
        )


# ---------------------------------------------------------------------------
# Test 3 — "restricted" zone severity=0.9 → cells inside zone have risk > 0.8
# ---------------------------------------------------------------------------

class TestRestrictedZone:
    RESTRICTED = Polygon([
        (0.02, 0.02), (0.05, 0.02), (0.05, 0.05), (0.02, 0.05),
    ])

    def _build(self):
        zones = [_zone(self.RESTRICTED, severity=0.9, zone_type="restricted")]
        return build_risk_map(FIELD, zones, grid_step=STEP)

    def test_inside_cells_high_risk(self):
        risk_grid, grid_points, grid_indices = self._build()

        inside_risks = [
            float(risk_grid[i, j])
            for (lat, lng), (i, j) in zip(grid_points, grid_indices)
            if self.RESTRICTED.contains(Point(lng, lat))
        ]

        assert len(inside_risks) > 0, "No grid points found inside restricted zone"
        assert all(r > 0.8 for r in inside_risks), (
            f"Expected all inside-zone risks > 0.8, got: {inside_risks}"
        )

    def test_outside_cells_zero_risk(self):
        """Restricted zones have no proximity influence – far points stay at 0."""
        risk_grid, grid_points, grid_indices = self._build()

        # Point (0.09, 0.09) is far from the restricted zone
        far_risks = [
            float(risk_grid[i, j])
            for (lat, lng), (i, j) in zip(grid_points, grid_indices)
            if abs(lng - 0.09) < 0.005 and abs(lat - 0.09) < 0.005
        ]
        if far_risks:
            assert all(r == 0.0 for r in far_risks)


# ---------------------------------------------------------------------------
# Test 4 — cells outside the field are NOT present in grid_points
# ---------------------------------------------------------------------------

class TestGridBoundary:
    def test_all_grid_points_inside_field(self):
        zones = [_zone(
            Polygon([(0.03, 0.03), (0.07, 0.03), (0.07, 0.07), (0.03, 0.07)]),
            severity=0.5,
            zone_type="jammer",
        )]
        _, grid_points, _ = build_risk_map(FIELD, zones, grid_step=STEP)

        for lat, lng in grid_points:
            p = Point(lng, lat)
            assert FIELD.contains(p) or FIELD.boundary.distance(p) < 1e-9, (
                f"Point ({lat}, {lng}) is outside the field boundary"
            )

    def test_grid_indices_in_bounds(self):
        _, grid_points, grid_indices = build_risk_map(FIELD, [], grid_step=STEP)
        risk_grid, _, _ = build_risk_map(FIELD, [], grid_step=STEP)
        N, M = risk_grid.shape
        for i, j in grid_indices:
            assert 0 <= i < N, f"Row index {i} out of bounds (N={N})"
            assert 0 <= j < M, f"Col index {j} out of bounds (M={M})"


# ---------------------------------------------------------------------------
# Tests for get_risk_for_point()
# ---------------------------------------------------------------------------

class TestGetRiskForPoint:
    def test_returns_float(self):
        risk_grid, _, _ = build_risk_map(FIELD, [], grid_step=STEP)
        grid_meta = {"minx": 0.0, "miny": 0.0, "step": STEP}
        result = get_risk_for_point(risk_grid, grid_meta, lat=0.05, lng=0.05)
        assert isinstance(result, float)

    def test_out_of_bounds_returns_zero(self):
        risk_grid, _, _ = build_risk_map(FIELD, [], grid_step=STEP)
        grid_meta = {"minx": 0.0, "miny": 0.0, "step": STEP}
        result = get_risk_for_point(risk_grid, grid_meta, lat=99.0, lng=99.0)
        assert result == 0.0

    def test_lookup_matches_grid_value(self):
        jammer = Polygon([(0.03, 0.03), (0.07, 0.03), (0.07, 0.07), (0.03, 0.07)])
        zones = [_zone(jammer, severity=1.0, zone_type="jammer")]
        risk_grid, grid_points, grid_indices = build_risk_map(FIELD, zones, grid_step=STEP)
        grid_meta = {"minx": 0.0, "miny": 0.0, "step": STEP}

        # The centre point should have high risk via lookup too
        risk_at_centre = get_risk_for_point(risk_grid, grid_meta, lat=0.05, lng=0.05)
        assert risk_at_centre > 0.8, (
            f"Expected risk > 0.8 at centre (inside jammer), got {risk_at_centre}"
        )
