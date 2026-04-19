"""
Synthetic scene: 3 km × 1.7 km field (~500 ha), reference latitude for m↔deg.

Grid step is chosen so that ~800 cells fall inside the rectangle (not the production
0.0002° / ~22 m step — that would be ~10⁴ cells and too slow for OR-Tools in one run).
"""

from __future__ import annotations

import math
from typing import List

import shapely.wkb
from shapely.geometry import Polygon, box

from unittest.mock import MagicMock

# Reference point (WGS84): mid-latitude for metric ↔ degree conversion
REF_LAT_DEG = 50.0
REF_LON_DEG = 36.0

FIELD_WIDTH_M = 3000.0
FIELD_HEIGHT_M = 1700.0

# Scenario 2: two static jammers (jammer type for risk_map)
JAMMER_W_M = 400.0
JAMMER_H_M = 300.0
JAMMER_SEVERITY = 0.8

# Target number of grid cells inside the field (performance vs diploma detail)
TARGET_GRID_CELLS = 800

# Mission parameters (prompt)
NUM_DRONES = 4
DRONE_SPEED_M_S = 10.0
DRONE_BATTERY_PCT = 100


def meters_to_lat_deg(m: float) -> float:
    return m / 111_320.0


def meters_to_lng_deg(m: float, lat_deg: float = REF_LAT_DEG) -> float:
    return m / (111_320.0 * math.cos(math.radians(lat_deg)))


def field_polygon() -> Polygon:
    """Axis-aligned rectangle: width along longitude, height along latitude."""
    d_lat = meters_to_lat_deg(FIELD_HEIGHT_M)
    d_lng = meters_to_lng_deg(FIELD_WIDTH_M)
    minx = REF_LON_DEG
    miny = REF_LAT_DEG
    maxx = REF_LON_DEG + d_lng
    maxy = REF_LAT_DEG + d_lat
    return box(minx, miny, maxx, maxy)


def grid_step_deg() -> float:
    """Single step for build_risk_map / plan_mission (~800 cells in bbox)."""
    poly = field_polygon()
    minx, miny, maxx, maxy = poly.bounds
    d_lng = maxx - minx
    d_lat = maxy - miny
    return math.sqrt(d_lng * d_lat / TARGET_GRID_CELLS)


def _rect_centered_at(
    cx_m: float,
    cy_m: float,
    width_m: float,
    height_m: float,
) -> Polygon:
    """
    cx_m, cy_m: offset in meters from field south-west corner along (east, north).
    """
    minx = REF_LON_DEG
    miny = REF_LAT_DEG
    cx = minx + meters_to_lng_deg(cx_m)
    cy = miny + meters_to_lat_deg(cy_m)
    half_w = meters_to_lng_deg(width_m / 2.0)
    half_h = meters_to_lat_deg(height_m / 2.0)
    return box(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def jammer_zones_scenario2() -> List[Polygon]:
    """
    Two jammers at 30% and 70% of field length (horizontal / east-west), vertically centered.
    """
    centers_x = [0.3 * FIELD_WIDTH_M, 0.7 * FIELD_WIDTH_M]
    cy = 0.5 * FIELD_HEIGHT_M
    return [_rect_centered_at(cx, cy, JAMMER_W_M, JAMMER_H_M) for cx in centers_x]


def jammer_zone_scenario3_dynamic() -> Polygon:
    """
    Вертикальная полоса по всей высоте поля (узкая по долготе) — baseline со змейкой
    по полосам неизбежно пересекает её после появления; компактный прямоугольник 400×300 м
    мог оказаться «между» проходами и не давать ранний обрыв миссии.
    """
    minx, miny, maxx, maxy = field_polygon().bounds
    cx = (minx + maxx) / 2.0
    half_w = meters_to_lng_deg(75.0)  # ~150 м по ширине
    return box(cx - half_w, miny, cx + half_w, maxy)


def make_field_mock(poly: Polygon | None = None) -> MagicMock:
    p = poly or field_polygon()
    m = MagicMock()
    m.geometry = MagicMock()
    m.geometry.data = shapely.wkb.dumps(p)
    return m


def make_risk_zone_mock(poly: Polygon, severity: float, zone_type: str = "jammer") -> MagicMock:
    rz = MagicMock()
    rz.geometry = MagicMock()
    rz.geometry.data = shapely.wkb.dumps(poly)
    rz.severity_weight = severity
    rz.type = zone_type
    return rz


def make_drone_mock(drone_id: int, battery: int = DRONE_BATTERY_PCT, speed: float = DRONE_SPEED_M_S) -> MagicMock:
    d = MagicMock()
    d.id = drone_id
    d.name = f"drone-{drone_id}"
    d.battery_capacity = battery
    d.max_speed = speed
    d.status = "active"
    return d


def default_drones() -> List[MagicMock]:
    return [make_drone_mock(i) for i in range(1, NUM_DRONES + 1)]
