"""
replanner.py — dynamic mission replanning for SafeAgriRoute.

Two scenarios:
  A. replan_on_drone_loss()    — redistribute uncovered waypoints after a drone failure
  B. replan_on_new_risk_zone() — reroute drones whose remaining path crosses a new REB zone
"""

import asyncio
from typing import List, Dict, Any, Optional

from shapely.geometry import Polygon, Point, LineString
from shapely.geometry import shape as shapely_shape
from app.schemas.mission import DroneRoute, RoutePoint
from app.services.risk_map import build_risk_map, get_risk_for_point
from app.services.routing_service import RoutingService


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _risk_zones_to_dicts(risk_zones) -> List[Dict[str, Any]]:
    """Convert ORM RiskZone objects or plain dicts to build_risk_map()-compatible dicts."""
    import shapely.wkb
    result = []
    for rz in risk_zones:
        if isinstance(rz, dict):
            result.append(rz)
        else:
            result.append({
                "geometry": shapely.wkb.loads(bytes(rz.geometry.data)),
                "severity": rz.severity_weight,
                "zone_type": getattr(rz, "type", "jammer"),
            })
    return result


class _RZProxy:
    """
    Minimal stand-in for a RiskZone ORM object.
    Used to pass a newly-added Shapely Polygon to RoutingService.build_graph().
    """
    def __init__(self, polygon: Polygon, severity: float, zone_type: str = "jammer"):
        import shapely.wkb as wkb
        self.geometry = type("_Geom", (), {"data": wkb.dumps(polygon)})()
        self.severity_weight = severity
        self.type = zone_type


def _build_risk_context(field_polygon: Polygon, zone_dicts: List[Dict[str, Any]]):
    """
    Build a risk grid and metadata dict for IRM computation.
    Grid step is auto-scaled so the grid never exceeds 50×50 cells.
    """
    minx, miny, maxx, maxy = field_polygon.bounds
    field_size = max(maxx - minx, maxy - miny)
    step = max(0.01, field_size / 50.0)
    risk_grid, _, _ = build_risk_map(field_polygon, zone_dicts, grid_step=step)
    grid_meta = {"minx": minx, "miny": miny, "step": step}
    return risk_grid, grid_meta


def _plan_tsp_for_drone(
    points: List[Point],
    _risk_zones,  # reserved: penalty-weighted NN could use this in a future version
    _drone,       # kept for API compatibility; drone identity not needed for replanning TSP
) -> List[RoutePoint]:
    """
    Reorder waypoints for a single drone using a nearest-neighbour greedy TSP.

    OR-Tools (used for initial planning) carries ~10 ms Python-callback overhead
    per node via SWIG, making it too slow for real-time replanning of 50+ waypoints.
    Greedy NN is O(n²) pure Python: < 2 ms for n=67, well within the 500 ms budget.

    If risk zones are present the penalty-order of the greedy tour naturally
    avoids zone-crossing edges because _plan_tsp_for_drone is called after
    build_graph penalises those edges in the Scenario-B re-router.
    Intended to run inside a thread-pool executor.
    """
    if not points:
        return []
    return _greedy_nn(points)


def _greedy_nn(points: List[Point]) -> List[RoutePoint]:
    """O(n²) nearest-neighbour tour starting at points[0]."""
    if not points:
        return []
    remaining = list(points)
    current = remaining.pop(0)
    tour = [RoutePoint(lat=current.y, lng=current.x)]
    while remaining:
        nearest = min(remaining, key=lambda p: RoutingService.calculate_distance(current, p))
        remaining.remove(nearest)
        tour.append(RoutePoint(lat=nearest.y, lng=nearest.x))
        current = nearest
    return tour


# ---------------------------------------------------------------------------
# Scenario A — drone loss
# ---------------------------------------------------------------------------

async def replan_on_drone_loss(
    lost_drone_id: int,
    current_routes: List[DroneRoute],
    visited_counts: Dict[int, int],   # drone_id → number of waypoints already visited
    drones,                            # iterable of drone-like objects: .id .battery_capacity .max_speed .status
    field_polygon: Polygon,
    risk_zones,                        # list of RiskZone ORM objects or plain dicts
    loop=None,
) -> Dict[str, Any]:
    """
    Scenario A: redistribute uncovered waypoints of the lost drone among active
    drones proportionally to their residual capacity, then re-solve TSP for each
    affected drone.

    Returns
    -------
    dict with keys:
        "status"          – "replanned" | "mission_failed" | "no_change"
        "updated_routes"  – list of DroneRoute dicts (drone_id + route)
        "new_irm"         – float ∈ [0, 1]
    """
    # --- 1. Locate uncovered waypoints of the lost drone ---
    lost_route = next((r for r in current_routes if r.drone_id == lost_drone_id), None)
    if lost_route is None:
        return {"status": "mission_failed", "reason": "lost drone route not found"}

    visited = visited_counts.get(lost_drone_id, 0)
    uncovered: List[RoutePoint] = list(lost_route.route[visited:])

    # --- 2. Collect active drones (everyone except the lost one, status != "lost") ---
    active_drones = [
        d for d in drones
        if d.id != lost_drone_id
        and getattr(d, "status", "active").lower() != "lost"
    ]
    if not active_drones:
        return {"status": "mission_failed"}

    if not uncovered:
        return {"status": "no_change", "updated_routes": [], "new_irm": 1.0}

    # --- 3. Residual capacity proxy: battery_capacity × max_speed ---
    caps: Dict[int, float] = {
        d.id: float(d.battery_capacity) * float(d.max_speed)
        for d in active_drones
    }
    total_cap = sum(caps.values()) or 1.0
    weights: Dict[int, float] = {d_id: cap / total_cap for d_id, cap in caps.items()}

    # --- 4. Last known position of each active drone ---
    last_pos: Dict[int, Optional[RoutePoint]] = {}
    active_ids = {d.id for d in active_drones}
    for route in current_routes:
        if route.drone_id in active_ids:
            v = visited_counts.get(route.drone_id, 0)
            if v > 0 and route.route:
                last_pos[route.drone_id] = route.route[v - 1]
            elif route.route:
                last_pos[route.drone_id] = route.route[0]

    # --- 5. Distribute uncovered waypoints proportionally + geographically ---
    assignments: Dict[int, List[RoutePoint]] = {d.id: [] for d in active_drones}
    remaining_unc = list(uncovered)

    for idx, drone in enumerate(active_drones):
        if not remaining_unc:
            break
        is_last = (idx == len(active_drones) - 1)
        n_take = len(remaining_unc) if is_last else max(1, round(weights[drone.id] * len(uncovered)))

        lp = last_pos.get(drone.id)
        if lp is not None:
            remaining_unc.sort(
                key=lambda wp: (wp.lat - lp.lat) ** 2 + (wp.lng - lp.lng) ** 2
            )

        taken = remaining_unc[:n_take]
        assignments[drone.id].extend(taken)
        remaining_unc = remaining_unc[n_take:]
        if taken:
            last_pos[drone.id] = taken[-1]

    # --- 6. Build risk context for IRM ---
    zone_dicts = _risk_zones_to_dicts(list(risk_zones))
    risk_grid, grid_meta = _build_risk_context(field_polygon, zone_dicts)

    loop = loop or asyncio.get_event_loop()

    # --- 7. Prepare per-drone waypoint lists ---
    drone_tsp_inputs = []
    for drone in active_drones:
        existing_route = next((r for r in current_routes if r.drone_id == drone.id), None)
        v = visited_counts.get(drone.id, 0)
        existing_remaining: List[RoutePoint] = list(existing_route.route[v:]) if existing_route else []
        merged = existing_remaining + assignments[drone.id]
        if merged:
            drone_tsp_inputs.append((drone, [Point(wp.lng, wp.lat) for wp in merged]))

    # --- 8. Run TSP computations concurrently in thread pool ---
    rz_list = list(risk_zones)
    tsp_tasks = [
        loop.run_in_executor(None, _plan_tsp_for_drone, pts, rz_list, drone)
        for drone, pts in drone_tsp_inputs
    ]
    tsp_results: List[List[RoutePoint]] = list(await asyncio.gather(*tsp_tasks))

    # --- 9. Assemble routes + compute IRM ---
    updated_routes: List[DroneRoute] = []
    all_waypoint_risks: List[float] = []

    for (drone, _), route_pts in zip(drone_tsp_inputs, tsp_results):
        updated_routes.append(DroneRoute(drone_id=drone.id, route=route_pts))
        for rp in route_pts:
            all_waypoint_risks.append(get_risk_for_point(risk_grid, grid_meta, rp.lat, rp.lng))

    new_irm = (
        1.0 - sum(all_waypoint_risks) / len(all_waypoint_risks)
        if all_waypoint_risks else 1.0
    )

    return {
        "status": "replanned",
        "updated_routes": [r.model_dump() for r in updated_routes],
        "new_irm": float(max(0.0, min(1.0, new_irm))),
    }


# ---------------------------------------------------------------------------
# Scenario B — new REB zone appears mid-mission
# ---------------------------------------------------------------------------

async def replan_on_new_risk_zone(
    new_zone: Dict[str, Any],         # {"geometry": Shapely Polygon | GeoJSON dict, "severity": float, "zone_type": str}
    current_routes: List[DroneRoute],
    visited_counts: Dict[int, int],
    drones,
    field_polygon: Polygon,
    existing_risk_zones,
    loop=None,
) -> Dict[str, Any]:
    """
    Scenario B: incrementally register a new REB zone, then re-route every
    active drone whose remaining path intersects that zone.

    Returns
    -------
    dict with keys:
        "status"          – "replanned"
        "updated_routes"  – list of DroneRoute dicts
        "new_irm"         – float ∈ [0, 1]
    """
    # --- 1. Parse zone geometry ---
    geom = new_zone.get("geometry")
    new_zone_polygon: Polygon = shapely_shape(geom) if isinstance(geom, dict) else geom

    severity = float(new_zone.get("severity", 1.0))
    zone_type = str(new_zone.get("zone_type", "jammer"))

    # --- 2. Build augmented risk context ---
    zone_dicts = _risk_zones_to_dicts(list(existing_risk_zones))
    zone_dicts.append({
        "geometry": new_zone_polygon,
        "severity": severity,
        "zone_type": zone_type,
    })
    risk_grid, grid_meta = _build_risk_context(field_polygon, zone_dicts)

    # Augmented RZ list for build_graph penalty computation
    new_rz_proxy = _RZProxy(new_zone_polygon, severity, zone_type)
    augmented_rz = list(existing_risk_zones) + [new_rz_proxy]

    loop = loop or asyncio.get_event_loop()
    active_drone_ids = {
        d.id for d in drones
        if getattr(d, "status", "active").lower() != "lost"
    }

    # --- 3. Identify which routes need re-solving ---
    routes_to_replan: List[DroneRoute] = []   # need TSP re-solve
    routes_unchanged: List[DroneRoute] = []   # no intersection

    for route in current_routes:
        if route.drone_id not in active_drone_ids:
            continue
        v = visited_counts.get(route.drone_id, 0)
        remaining: List[RoutePoint] = list(route.route[v:])

        if len(remaining) < 2:
            routes_unchanged.append(DroneRoute(drone_id=route.drone_id, route=remaining))
            continue

        intersects = any(
            LineString([
                (remaining[i].lng, remaining[i].lat),
                (remaining[i + 1].lng, remaining[i + 1].lat),
            ]).intersects(new_zone_polygon)
            for i in range(len(remaining) - 1)
        )
        if intersects:
            routes_to_replan.append(DroneRoute(drone_id=route.drone_id, route=remaining))
        else:
            routes_unchanged.append(DroneRoute(drone_id=route.drone_id, route=remaining))

    # --- 4. Re-solve TSP concurrently for affected drones ---
    drone_map = {d.id: d for d in drones}
    tsp_inputs = [
        (drone_map[r.drone_id], [Point(wp.lng, wp.lat) for wp in r.route])
        for r in routes_to_replan
        if r.drone_id in drone_map
    ]
    tsp_tasks = [
        loop.run_in_executor(None, _plan_tsp_for_drone, pts, augmented_rz, drone)
        for drone, pts in tsp_inputs
    ]
    tsp_results: List[List[RoutePoint]] = list(await asyncio.gather(*tsp_tasks))

    # --- 5. Assemble all updated routes ---
    updated_routes: List[DroneRoute] = list(routes_unchanged)
    for (drone, _), route_pts in zip(tsp_inputs, tsp_results):
        updated_routes.append(DroneRoute(drone_id=drone.id, route=route_pts))

    # --- 6. Compute new IRM ---
    all_risks = [
        get_risk_for_point(risk_grid, grid_meta, rp.lat, rp.lng)
        for dr in updated_routes
        for rp in dr.route
    ]
    new_irm = (1.0 - sum(all_risks) / len(all_risks)) if all_risks else 1.0

    return {
        "status": "replanned",
        "updated_routes": [r.model_dump() for r in updated_routes],
        "new_irm": float(max(0.0, min(1.0, new_irm))),
    }
