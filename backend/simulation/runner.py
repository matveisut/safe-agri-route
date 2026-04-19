"""
Run three diploma scenarios (baseline vs SafeAgriRoute) and export plots.

Usage (from `backend/`):
    python simulation/runner.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure `backend/` is on sys.path when executed as `python simulation/runner.py`
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from shapely.geometry import Polygon

from app.schemas.mission import DroneRoute
from app.services.replanner import replan_on_drone_loss, replan_on_new_risk_zone
from app.services.routing_service import RoutingService

from simulation.baseline import build_boustrophedon_routes, evaluate_baseline
from simulation.metrics import (
    coverage_pct_from_paths,
    coverage_pct_from_drone_routes,
    first_jammer_hit_after_time,
    haversine_m,
    mean_irm_baseline_routes,
    paths_for_timeline_parallel,
    routes_through_jammer_count,
    total_time_sec_parallel,
)
from simulation.scene import (
    DRONE_SPEED_M_S,
    JAMMER_SEVERITY,
    default_drones,
    field_polygon,
    grid_step_deg,
    jammer_zone_scenario3_dynamic,
    jammer_zones_scenario2,
    make_field_mock,
    make_risk_zone_mock,
)
from simulation import visualizer


def risk_mocks_from_polygons(polys: list[Polygon], severity: float):
    return [make_risk_zone_mock(p, severity) for p in polys]


def _waypoint_index_after_dist(route: list, dist_budget_m: float) -> int:
    if not route or dist_budget_m <= 0:
        return 0
    acc = 0.0
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        d = haversine_m(a.lat, a.lng, b.lat, b.lng)
        if acc + d >= dist_budget_m:
            return i + 1
        acc += d
    return len(route)


def run_scenario1() -> dict:
    step = grid_step_deg()
    field = make_field_mock()
    drones = default_drones()
    sar = RoutingService.plan_mission(field, drones, [], step_deg=step)
    bl_routes = build_boustrophedon_routes(field_polygon(), len(drones), step)
    bl = evaluate_baseline(bl_routes, [], abort_on_jammer=False)

    return {
        "name": "Сценарий 1 (штатный)",
        "baseline": {
            "coverage_pct": bl.coverage_pct,
            "mean_IRM": mean_irm_baseline_routes(bl_routes, [], 0.0),
            "total_time_sec": total_time_sec_parallel(bl_routes, DRONE_SPEED_M_S),
            "waypoints_count": sum(len(r.route) for r in bl_routes),
        },
        "sar": {
            "coverage_pct": coverage_pct_from_drone_routes(sar.routes),
            "mean_IRM": sar.reliability_index,
            "total_time_sec": total_time_sec_parallel(sar.routes, DRONE_SPEED_M_S),
            "waypoints_count": sum(len(r.route) for r in sar.routes),
            "estimated_coverage_pct": sar.estimated_coverage_pct,
        },
        "routes_baseline": bl_routes,
        "routes_sar": sar.routes,
        "risk_grid_preview": sar.risk_grid_preview or [],
    }


def run_scenario2() -> dict:
    step = grid_step_deg()
    field = make_field_mock()
    drones = default_drones()
    jpolys = jammer_zones_scenario2()
    risk_mocks = [make_risk_zone_mock(p, JAMMER_SEVERITY) for p in jpolys]

    sar = RoutingService.plan_mission(field, drones, risk_mocks, step_deg=step)
    bl_routes = build_boustrophedon_routes(field_polygon(), len(drones), step)
    bl = evaluate_baseline(bl_routes, jpolys, abort_on_jammer=True)

    return {
        "name": "Сценарий 2 (статичные РЭБ)",
        "jammer_polys": jpolys,
        "baseline": {
            "coverage_pct": bl.coverage_pct,
            "lost_drones": routes_through_jammer_count(bl_routes, jpolys),
            "mean_IRM": mean_irm_baseline_routes(bl_routes, jpolys, JAMMER_SEVERITY),
            "routes_through_jammer_count": routes_through_jammer_count(bl_routes, jpolys),
        },
        "sar": {
            "coverage_pct": coverage_pct_from_drone_routes(sar.routes),
            "mean_IRM": sar.reliability_index,
            "routes_through_jammer_count": routes_through_jammer_count(sar.routes, jpolys),
            "estimated_coverage_pct": sar.estimated_coverage_pct,
        },
        "routes_baseline": bl_routes,
        "routes_sar": sar.routes,
        "risk_grid_preview": sar.risk_grid_preview or [],
    }


async def _sar_scenario3_async() -> dict:
    step = grid_step_deg()
    field_m = make_field_mock()
    drones = default_drones()
    poly = field_polygon()
    jam_dyn = jammer_zone_scenario3_dynamic()

    initial = RoutingService.plan_mission(field_m, drones, [], step_deg=step)
    r0 = initial.routes
    T0 = total_time_sec_parallel(r0, DRONE_SPEED_M_S)
    if T0 <= 0:
        T0 = 1.0

    t40 = 0.4 * T0
    t60 = 0.6 * T0

    visited_40 = {dr.drone_id: min(len(dr.route), _waypoint_index_after_dist(dr.route, DRONE_SPEED_M_S * t40)) for dr in r0}

    r1_dict = await replan_on_new_risk_zone(
        {"geometry": jam_dyn, "severity": JAMMER_SEVERITY, "zone_type": "jammer"},
        r0,
        visited_40,
        drones,
        poly,
        [],
    )
    r1 = [DroneRoute.model_validate(x) for x in r1_dict["updated_routes"]]

    dist_40_60 = (t60 - t40) * DRONE_SPEED_M_S
    visited_60 = {}
    for dr in r1:
        visited_60[dr.drone_id] = _waypoint_index_after_dist(dr.route, dist_40_60)

    drones2 = default_drones()
    for d in drones2:
        if d.id == 2:
            d.status = "lost"

    r2_dict = await replan_on_drone_loss(
        2,
        r1,
        visited_60,
        drones2,
        poly,
        list(risk_mocks_from_polygons([jam_dyn], JAMMER_SEVERITY)),
    )
    r2 = [DroneRoute.model_validate(x) for x in r2_dict["updated_routes"]]

    def cov_at_frac(f: float) -> float:
        from simulation.metrics import flown_paths_until_time_parallel

        t = f * T0
        if t <= t40:
            return coverage_pct_from_paths(flown_paths_until_time_parallel(r0, t, DRONE_SPEED_M_S))
        if t <= t60:
            p1 = flown_paths_until_time_parallel(r0, t40, DRONE_SPEED_M_S)
            p2 = flown_paths_until_time_parallel(r1, t - t40, DRONE_SPEED_M_S)
            return coverage_pct_from_paths(p1 + p2)
        p1 = flown_paths_until_time_parallel(r0, t40, DRONE_SPEED_M_S)
        p2 = flown_paths_until_time_parallel(r1, t60 - t40, DRONE_SPEED_M_S)
        p3 = flown_paths_until_time_parallel(r2, t - t60, DRONE_SPEED_M_S)
        return coverage_pct_from_paths(p1 + p2 + p3)

    timeline_sar = {int(f * 100): cov_at_frac(f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)}

    # Baseline scenario 3: no replan, stop at jammer (after 40%) or 60% drone 2
    bl0 = build_boustrophedon_routes(poly, len(drones), step)

    hit = first_jammer_hit_after_time(bl0, [jam_dyn], DRONE_SPEED_M_S, t40)
    t_stop = t60
    if hit is not None:
        t_stop = min(t_stop, hit)

    def bl_cov_at(f: float) -> float:
        t = f * T0
        paths = paths_for_timeline_parallel(bl0, t, DRONE_SPEED_M_S, t_stop)
        return coverage_pct_from_paths(paths)

    timeline_bl = {int(f * 100): bl_cov_at(f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)}

    return {
        "name": "Сценарий 3 (динамика)",
        "T0_sec": T0,
        "timeline_baseline": timeline_bl,
        "timeline_sar": timeline_sar,
        "routes_baseline": bl0,
        "routes_sar_initial": r0,
        "routes_sar_final": r2,
        "jammer_poly": jam_dyn,
    }


def main() -> None:
    os.chdir(_BACKEND_ROOT)
    out_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)

    s1 = run_scenario1()
    s2 = run_scenario2()
    s3 = asyncio.run(_sar_scenario3_async())

    print("=== Сценарий 1 ===")
    print("baseline:", s1["baseline"])
    print("SAR:     ", s1["sar"])
    print("=== Сценарий 2 ===")
    print("baseline:", s2["baseline"])
    print("SAR:     ", s2["sar"])
    print("=== Сценарий 3 (таймлайн % покрытия) ===")
    # Примечание: при дискретной сетке ~800 ячеек «покрытие у трека» к 25–40% времени
    # уже может быть >80% (змейка быстро заполняет поле). Числовые пороги из Промпты.md
    # §проверка 5 — ориентир; для текста ВКР сравнивайте кривые и финал SAR vs обрыв baseline.
    print("baseline:", s3["timeline_baseline"])
    print("SAR:     ", s3["timeline_sar"])

    visualizer.render_all(s1, s2, s3, out_dir)
    print(f"PNG сохранены в {out_dir}")


if __name__ == "__main__":
    main()
