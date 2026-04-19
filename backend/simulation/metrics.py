"""
Shared metrics: coverage along routes, IRM, jammer crossings, flight time.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np
from shapely.geometry import LineString, Polygon

from app.schemas.mission import DroneRoute, RoutePoint
from app.services.risk_map import build_risk_map, get_risk_for_point

from simulation.scene import FIELD_HEIGHT_M, NUM_DRONES, REF_LAT_DEG, field_polygon, grid_step_deg


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def route_length_m(route: Sequence[RoutePoint]) -> float:
    if len(route) < 2:
        return 0.0
    s = 0.0
    for a, b in zip(route[:-1], route[1:]):
        s += haversine_m(a.lat, a.lng, b.lat, b.lng)
    return s


def total_time_sec_parallel(routes: List[DroneRoute], speed_m_s: float) -> float:
    """Wall-clock mission time if all drones fly in parallel."""
    if not routes or speed_m_s <= 0:
        return 0.0
    return max(route_length_m(r.route) for r in routes) / speed_m_s


def _zone_dicts_from_polygons(jammers: List[Polygon], severity: float) -> List[dict]:
    return [
        {"geometry": p, "severity": severity, "zone_type": "jammer"}
        for p in jammers
    ]


def build_coverage_grid() -> Tuple[np.ndarray, dict, List[Tuple[float, float]]]:
    """Risk grid meta reused for coverage: same step as mission planner."""
    poly = field_polygon()
    step = grid_step_deg()
    zone_dicts: List[dict] = []
    risk_grid, grid_points, grid_indices = build_risk_map(poly, zone_dicts, grid_step=step)
    minx, miny, _, _ = poly.bounds
    grid_meta = {"minx": minx, "miny": miny, "step": step}
    return risk_grid, grid_meta, grid_points


def default_cover_radius_m() -> float:
    """
    Радиус «кисти» вокруг трека. Для baseline с горизонтальными полосами центры ячеек
    у границ полосы могут быть до ~половины ширины полосы от змейки — задаём запас в метрах.
    """
    step_deg = grid_step_deg()
    step_m = step_deg * 111_320.0 * math.cos(math.radians(REF_LAT_DEG))
    half_strip_m = (FIELD_HEIGHT_M / max(1, NUM_DRONES)) / 2.0
    return max(120.0, 0.55 * step_m, half_strip_m * 0.95)


def _min_dist_m_point_to_path(lat: float, lng: float, path: List[Tuple[float, float]]) -> float:
    """Минимальное расстояние (м) от точки до полилинии: вершины + середины сегментов (без Shapely distance на WGS84)."""
    if not path:
        return float("inf")
    best = float("inf")
    for lngp, latp in path:
        best = min(best, haversine_m(lat, lng, latp, lngp))
    if len(path) < 2:
        return best
    for i in range(len(path) - 1):
        lng1, lat1 = path[i]
        lng2, lat2 = path[i + 1]
        mid_lat = (lat1 + lat2) / 2.0
        mid_lng = (lng1 + lng2) / 2.0
        best = min(
            best,
            haversine_m(lat, lng, lat1, lng1),
            haversine_m(lat, lng, lat2, lng2),
            haversine_m(lat, lng, mid_lat, mid_lng),
        )
    return best


def coverage_pct_from_paths(
    paths: List[List[Tuple[float, float]]],
    cover_radius_m: float | None = None,
) -> float:
    """
    Fraction of field grid cells whose centre is within cover_radius_m of any polyline vertex/segment.
    paths: list of lists of (lng, lat) waypoints.
    """
    if cover_radius_m is None:
        cover_radius_m = default_cover_radius_m()
    poly = field_polygon()
    step = grid_step_deg()
    _, grid_points, _ = build_risk_map(poly, [], grid_step=step)
    if not grid_points:
        return 0.0

    covered = 0
    r = cover_radius_m * 1.05
    for lat, lng in grid_points:
        ok = False
        for path in paths:
            if len(path) < 1:
                continue
            if _min_dist_m_point_to_path(lat, lng, path) <= r:
                ok = True
                break
        if ok:
            covered += 1

    return 100.0 * covered / len(grid_points)


def coverage_pct_from_drone_routes(routes: List[DroneRoute]) -> float:
    """Та же дискретизация поля, что и для baseline — для сопоставимости с экспериментом."""
    paths = [[(wp.lng, wp.lat) for wp in dr.route] for dr in routes if dr.route]
    return coverage_pct_from_paths(paths)


def mean_irm_baseline_routes(
    routes: List[DroneRoute],
    jammer_polys: List[Polygon],
    severity: float,
) -> float:
    """IRM = 1 - mean(risk(wp)) using same risk grid as backend."""
    poly = field_polygon()
    step = grid_step_deg()
    zd = _zone_dicts_from_polygons(jammer_polys, severity)
    risk_grid, _, _ = build_risk_map(poly, zd, grid_step=step)
    minx, miny, _, _ = poly.bounds
    grid_meta = {"minx": minx, "miny": miny, "step": step}
    risks: List[float] = []
    for dr in routes:
        for wp in dr.route:
            risks.append(get_risk_for_point(risk_grid, grid_meta, wp.lat, wp.lng))
    if not risks:
        return 1.0
    return max(0.0, min(1.0, 1.0 - sum(risks) / len(risks)))


def routes_through_jammer_count(routes: List[DroneRoute], jammer_polys: List[Polygon]) -> int:
    """Number of drone routes with at least one segment intersecting any jammer polygon."""
    n = 0
    for dr in routes:
        hit = False
        r = dr.route
        for i in range(len(r) - 1):
            seg = LineString([(r[i].lng, r[i].lat), (r[i + 1].lng, r[i + 1].lat)])
            for jp in jammer_polys:
                if seg.intersects(jp):
                    hit = True
                    break
            if hit:
                break
        if hit:
            n += 1
    return n


def segments_until_distance(
    route: Sequence[RoutePoint],
    max_dist_m: float,
) -> List[Tuple[float, float]]:
    """Return polyline as (lng, lat) points along route until cumulative length reaches max_dist_m."""
    if not route or max_dist_m <= 0:
        return []
    out: List[Tuple[float, float]] = [(route[0].lng, route[0].lat)]
    acc = 0.0
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        d = haversine_m(a.lat, a.lng, b.lat, b.lng)
        if acc + d <= max_dist_m:
            acc += d
            out.append((b.lng, b.lat))
            continue
        # partial segment
        need = max_dist_m - acc
        t = need / d if d > 1e-9 else 1.0
        lat = a.lat + t * (b.lat - a.lat)
        lng = a.lng + t * (b.lng - a.lng)
        out.append((lng, lat))
        break
    return out


def flown_paths_until_time_parallel(
    routes: List[DroneRoute],
    t_sec: float,
    speed_m_s: float,
) -> List[List[Tuple[float, float]]]:
    """Each drone flies its route; distance budget = speed * t (parallel wall clock)."""
    if t_sec <= 0 or speed_m_s <= 0:
        return []
    budget_m = t_sec * speed_m_s
    out: List[List[Tuple[float, float]]] = []
    for dr in routes:
        out.append(segments_until_distance(dr.route, budget_m))
    return out


def first_jammer_hit_time_sec(
    routes: List[DroneRoute],
    jammer_polys: List[Polygon],
    speed_m_s: float,
) -> float | None:
    """
    Earliest wall-clock time when any drone enters a jammer segment (segment intersects polygon).
    If none, return None.
    """
    if not jammer_polys:
        return None
    best: float | None = None
    for dr in routes:
        acc = 0.0
        r = dr.route
        for i in range(len(r) - 1):
            a, b = r[i], r[i + 1]
            seg = LineString([(a.lng, a.lat), (b.lng, b.lat)])
            d = haversine_m(a.lat, a.lng, b.lat, b.lng)
            for jp in jammer_polys:
                if seg.intersects(jp):
                    # time to start of this segment
                    t_hit = acc / speed_m_s if speed_m_s > 0 else float("inf")
                    if best is None or t_hit < best:
                        best = t_hit
            acc += d
    return best


def first_jammer_hit_after_time(
    routes: List[DroneRoute],
    jammer_polys: List[Polygon],
    speed_m_s: float,
    t_active_after: float,
) -> float | None:
    """
    Earliest wall-clock time when a jammer-active segment is flown (jammer exists from t_active_after).
    """
    if not jammer_polys or speed_m_s <= 0:
        return None
    v = speed_m_s
    best: float | None = None
    for dr in routes:
        acc = 0.0
        r = dr.route
        for i in range(len(r) - 1):
            a, b = r[i], r[i + 1]
            seg = LineString([(a.lng, a.lat), (b.lng, b.lat)])
            d = haversine_m(a.lat, a.lng, b.lat, b.lng)
            t_seg0 = acc / v
            t_seg1 = (acc + d) / v
            if t_seg1 < t_active_after:
                acc += d
                continue
            for jp in jammer_polys:
                if seg.intersects(jp):
                    t_hit = max(t_seg0, t_active_after)
                    if best is None or t_hit < best:
                        best = t_hit
            acc += d
    return best


def paths_for_timeline_parallel(
    routes: List[DroneRoute],
    t_sec: float,
    speed_m_s: float,
    t_stop: float | None,
) -> List[List[Tuple[float, float]]]:
    """Truncate flight at t_stop (mission abort) if t_stop < t_sec."""
    t_eff = t_sec if t_stop is None else min(t_sec, t_stop)
    return flown_paths_until_time_parallel(routes, t_eff, speed_m_s)


def sar_mean_waypoints(routes: List[DroneRoute]) -> float:
    if not routes:
        return 0.0
    return sum(len(r.route) for r in routes) / len(routes)
