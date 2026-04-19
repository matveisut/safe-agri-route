"""
Greedy baseline: horizontal strips + boustrophedon, ignores REB until loss event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from shapely.geometry import Point, Polygon
from shapely.geometry import box as shapely_box

from app.schemas.mission import DroneRoute, RoutePoint

from simulation.metrics import coverage_pct_from_paths, flown_paths_until_time_parallel, first_jammer_hit_time_sec
from simulation.scene import DRONE_SPEED_M_S, field_polygon, grid_step_deg


@dataclass
class BaselineResult:
    routes: List[DroneRoute]
    coverage_pct: float
    lost_drones_count: int
    mission_completed: bool


def _grid_points_in_polygon(poly: Polygon, step_deg: float) -> List[Point]:
    minx, miny, maxx, maxy = poly.bounds
    pts: List[Point] = []
    y = miny
    row = 0
    while y <= maxy + 1e-12:
        xs = []
        x = minx
        while x <= maxx + 1e-12:
            p = Point(x, y)
            if poly.contains(p) or poly.touches(p):
                xs.append(x)
            x += step_deg
        if row % 2 == 0:
            for x in xs:
                pts.append(Point(x, y))
        else:
            for x in reversed(xs):
                pts.append(Point(x, y))
        y += step_deg
        row += 1
    return pts


def build_boustrophedon_routes(
    field_poly: Polygon,
    num_drones: int,
    step_deg: float | None = None,
) -> List[DroneRoute]:
    """
    Split the field into `num_drones` horizontal bands (equal latitude span); each band
    is covered in boustrophedon order at the given grid step.
    """
    step = step_deg or grid_step_deg()
    minx, miny, maxx, maxy = field_poly.bounds
    height = maxy - miny
    band_h = height / num_drones
    routes: List[DroneRoute] = []

    for k in range(num_drones):
        y0 = miny + k * band_h
        y1 = miny + (k + 1) * band_h
        strip = field_poly.intersection(shapely_box(minx, y0, maxx, y1))
        if strip.is_empty:
            routes.append(DroneRoute(drone_id=k + 1, route=[]))
            continue
        geoms = [strip] if strip.geom_type == "Polygon" else [g for g in strip.geoms]
        band_pts: List[Point] = []
        for g in geoms:
            if g.geom_type == "Polygon":
                band_pts.extend(_grid_points_in_polygon(g, step))
        # de-dup while keeping order
        seen = set()
        ordered: List[Point] = []
        for p in band_pts:
            key = (round(p.x, 9), round(p.y, 9))
            if key not in seen:
                seen.add(key)
                ordered.append(p)
        rps = [RoutePoint(lat=p.y, lng=p.x) for p in ordered]
        routes.append(DroneRoute(drone_id=k + 1, route=rps))

    return routes


def evaluate_baseline(
    routes: List[DroneRoute],
    jammer_polys: Sequence[Polygon],
    *,
    abort_on_jammer: bool = True,
    speed_m_s: float = DRONE_SPEED_M_S,
) -> BaselineResult:
    """
    Compute coverage and losses. If abort_on_jammer and a route crosses a jammer,
    mission stops at first hit time (parallel drones); coverage only up to that time.
    """
    t_limit: float | None = None
    lost = 0
    if jammer_polys and abort_on_jammer:
        t_hit = first_jammer_hit_time_sec(list(routes), list(jammer_polys), speed_m_s)
        if t_hit is not None:
            t_limit = t_hit
            lost = 1

    if t_limit is not None:
        paths = flown_paths_until_time_parallel(routes, t_limit, speed_m_s)
        cov = coverage_pct_from_paths(paths)
        completed = False
    else:
        paths = [
            [(wp.lng, wp.lat) for wp in dr.route]
            for dr in routes
            if dr.route
        ]
        cov = coverage_pct_from_paths(paths)
        completed = all(len(dr.route) > 0 for dr in routes)

    return BaselineResult(
        routes=routes,
        coverage_pct=cov,
        lost_drones_count=lost,
        mission_completed=completed,
    )


def baseline_for_field(
    jammer_polys: Sequence[Polygon] | None = None,
    num_drones: int = 4,
) -> BaselineResult:
    poly = field_polygon()
    routes = build_boustrophedon_routes(poly, num_drones)
    return evaluate_baseline(routes, jammer_polys or [], abort_on_jammer=bool(jammer_polys))
