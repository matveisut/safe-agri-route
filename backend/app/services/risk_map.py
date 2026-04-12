"""
Risk map builder for the SafeAgriRoute mission planner.

build_risk_map() creates a discrete 2-D risk grid over a field polygon,
combining electromagnetic jammer influence and restricted-zone penalties.

get_risk_for_point() performs nearest-neighbour lookup on a pre-built grid
for any arbitrary (lat, lng) coordinate.
"""

import numpy as np
from typing import List, Tuple, Dict, Any

from shapely.geometry import Polygon, Point

# Jammer influence decay radius in degrees (~500 m at mid-latitudes)
_JAMMER_INFLUENCE_RADIUS = 0.005


def build_risk_map(
    field: Polygon,
    risk_zones: List[Dict[str, Any]],
    grid_step: float = 0.0002,
) -> Tuple[np.ndarray, List[Tuple[float, float]], List[Tuple[int, int]]]:
    """
    Build a discrete risk map for the given field polygon.

    Parameters
    ----------
    field : Shapely Polygon
        Boundary of the agricultural field.
    risk_zones : list of dicts
        Each dict must have keys:
            "geometry"  – Shapely Polygon of the zone
            "severity"  – float ∈ [0, 1]
            "zone_type" – str, one of "jammer" | "restricted"
    grid_step : float
        Grid resolution in degrees (default 0.0002° ≈ 22 m).

    Returns
    -------
    risk_grid : np.ndarray, shape (N, M)
        Risk values ∈ [0, 1] normalised across the grid.
        Rows correspond to latitude steps, columns to longitude steps.
        Cells outside the field boundary remain 0.
    grid_points : list of (lat, lng) tuples
        Coordinates of every cell that lies inside the field.
    grid_indices : list of (i, j) tuples
        Corresponding (row, col) indices into risk_grid for each grid_point.
    """
    minx, miny, maxx, maxy = field.bounds

    xs = np.arange(minx, maxx + grid_step * 0.5, grid_step)
    ys = np.arange(miny, maxy + grid_step * 0.5, grid_step)
    N, M = len(ys), len(xs)

    risk_grid = np.zeros((N, M), dtype=float)

    jammer_zones = [z for z in risk_zones if z.get("zone_type") == "jammer"]
    restricted_zones = [z for z in risk_zones if z.get("zone_type") == "restricted"]

    grid_points: List[Tuple[float, float]] = []
    grid_indices: List[Tuple[int, int]] = []

    for i, y in enumerate(ys):
        for j, x in enumerate(xs):
            p = Point(x, y)
            if not field.contains(p):
                continue

            # --- r_jammer: electromagnetic jamming influence ---
            # Inside the zone  → use zone severity directly.
            # Outside but within influence radius → linear decay scaled by severity.
            r_jammer = 0.0
            for zone in jammer_zones:
                geom: Polygon = zone["geometry"]
                sev: float = zone["severity"]
                if geom.contains(p):
                    r_jammer = max(r_jammer, sev)
                else:
                    dist = p.distance(geom)
                    if dist < _JAMMER_INFLUENCE_RADIUS:
                        r_jammer = max(
                            r_jammer,
                            sev * (1.0 - dist / _JAMMER_INFLUENCE_RADIUS),
                        )

            # --- r_zone: hard restricted areas ---
            r_zone = 0.0
            for zone in restricted_zones:
                if zone["geometry"].contains(p):
                    r_zone = max(r_zone, zone["severity"])

            # Combined risk – additive, capped at 1.0.
            # r_coverage = 0.0 for MVP (ground station always reachable).
            risk = min(1.0, r_jammer + r_zone)
            risk_grid[i, j] = risk

            grid_points.append((float(y), float(x)))   # (lat, lng)
            grid_indices.append((i, j))

    # Normalise to [0, 1] so that the maximum observed risk maps to 1.0.
    max_val = float(risk_grid.max())
    if max_val > 0.0:
        risk_grid = risk_grid / max_val

    return risk_grid, grid_points, grid_indices


def get_risk_for_point(
    risk_grid: np.ndarray,
    grid_meta: Dict[str, Any],
    lat: float,
    lng: float,
) -> float:
    """
    Look up the risk value for an arbitrary (lat, lng) coordinate using
    nearest-neighbour interpolation on a pre-built risk grid.

    Parameters
    ----------
    risk_grid : np.ndarray, shape (N, M)
        Output of build_risk_map().
    grid_meta : dict with keys "minx", "miny", "step"
        Spatial reference produced alongside risk_grid:
            minx – longitude of the first grid column
            miny – latitude of the first grid row
            step – grid step in degrees
    lat : float
        Query latitude.
    lng : float
        Query longitude.

    Returns
    -------
    float
        Risk value ∈ [0, 1], or 0.0 if the point is outside the grid extent.
    """
    miny: float = grid_meta["miny"]
    minx: float = grid_meta["minx"]
    step: float = grid_meta["step"]
    N, M = risk_grid.shape

    i = int(round((lat - miny) / step))
    j = int(round((lng - minx) / step))

    if i < 0 or i >= N or j < 0 or j >= M:
        return 0.0

    return float(risk_grid[i, j])
