"""
Matplotlib exports for diploma (Russian labels, dpi=300).
"""

from __future__ import annotations

import os
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import Polygon

from app.services.risk_map import build_risk_map

from simulation.scene import JAMMER_SEVERITY, field_polygon, grid_step_deg


FIG_KW = {"figsize": (12, 8), "dpi": 300}


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def plot_heatmap_scenario2(s2: dict, out_path: str) -> None:
    poly = field_polygon()
    jpolys = s2["jammer_polys"]
    zone_dicts = [
        {"geometry": p, "severity": JAMMER_SEVERITY, "zone_type": "jammer"}
        for p in jpolys
    ]
    step = grid_step_deg()
    risk_grid, _, _ = build_risk_map(poly, zone_dicts, grid_step=step)
    minx, miny, maxx, maxy = poly.bounds

    fig, ax = plt.subplots(**FIG_KW)
    im = ax.imshow(
        risk_grid,
        origin="lower",
        extent=[minx, maxx, miny, maxy],
        aspect="auto",
        cmap="RdYlGn_r",
        interpolation="nearest",
    )
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("Уровень риска")
    ax.set_xlabel("Долгота")
    ax.set_ylabel("Широта")
    ax.set_title("Тепловая карта риска (сценарий 2)")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_routes_comparison_scenario2(s2: dict, out_path: str) -> None:
    bl = s2["routes_baseline"]
    sar = s2["routes_sar"]
    jpolys: list[Polygon] = s2["jammer_polys"]

    fig, (ax1, ax2) = plt.subplots(1, 2, **FIG_KW)
    poly = field_polygon()
    minx, miny, maxx, maxy = poly.bounds

    def draw(ax, routes, color: str, title: str) -> None:
        gx, gy = poly.exterior.xy
        ax.plot(gx, gy, "k-", linewidth=0.8)
        for jp in jpolys:
            jx, jy = jp.exterior.xy
            ax.fill(jx, jy, color="orange", alpha=0.35, linewidth=0)
        for dr in routes:
            if len(dr.route) < 2:
                continue
            xs = [p.lng for p in dr.route]
            ys = [p.lat for p in dr.route]
            ax.plot(xs, ys, color=color, linewidth=1.2, alpha=0.9)
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Долгота")
        ax.set_ylabel("Широта")
        ax.set_title(title)

    draw(ax1, bl, "red", "Baseline (игнор РЭБ)")
    draw(ax2, sar, "green", "SafeAgriRoute")
    fig.suptitle("Сравнение маршрутов: сценарий 2", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_coverage_timeline_scenario3(s3: dict, out_path: str) -> None:
    bl = s3["timeline_baseline"]
    sar = s3["timeline_sar"]
    xs = sorted(bl.keys())

    fig, ax = plt.subplots(**FIG_KW)
    yb = [bl[k] for k in xs]
    ys = [sar[k] for k in xs]
    ax.plot(xs, yb, "r--", linewidth=2, label="Baseline", marker="o", markersize=4)
    ax.plot(xs, ys, "g-", linewidth=2, label="SafeAgriRoute", marker="s", markersize=4)
    ymax = max(max(yb), max(ys), 1.0)
    ax.set_ylim(0, ymax * 1.1)
    ax.axvline(40, color="gray", linestyle=":", linewidth=1.5)
    ax.axvline(60, color="purple", linestyle=":", linewidth=1.5)
    ax.text(40, ymax, "Зона РЭБ появилась", rotation=90, va="top", ha="right", fontsize=9)
    ax.text(60, ymax, "Дрон #2 потерян", rotation=90, va="top", ha="right", fontsize=9)
    ax.set_xlabel("Прогресс миссии, %")
    ax.set_ylabel("Покрытие поля, %")
    ax.set_title("Покрытие во времени (сценарий 3)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_summary_table(s1: dict, s2: dict, s3: dict, out_path: str) -> None:
    fig, ax = plt.subplots(**FIG_KW)
    ax.axis("off")

    rows = [
        [
            "Сценарий 1",
            f"{s1['baseline']['coverage_pct']:.1f}",
            f"{s1['sar']['coverage_pct']:.1f}",
            f"{s1['baseline']['mean_IRM']:.3f}",
            f"{s1['sar']['mean_IRM']:.3f}",
        ],
        [
            "Сценарий 2",
            f"{s2['baseline']['coverage_pct']:.1f}",
            f"{s2['sar']['coverage_pct']:.1f}",
            f"{s2['baseline']['mean_IRM']:.3f}",
            f"{s2['sar']['mean_IRM']:.3f}",
        ],
        [
            "Сценарий 3 (финал)",
            f"{s3['timeline_baseline'][100]:.1f}",
            f"{s3['timeline_sar'][100]:.1f}",
            "—",
            "—",
        ],
    ]
    cols = ["Сценарий", "Покрытие baseline %", "Покрытие SAR %", "IRM baseline", "IRM SAR"]

    table = ax.table(
        cellText=rows,
        colLabels=cols,
        loc="center",
        cellLoc="center",
    )
    table.scale(1.2, 2.4)
    ax.set_title("Сводка экспериментов SafeAgriRoute", pad=20)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def render_all(s1: dict, s2: dict, s3: dict, out_dir: str) -> None:
    _ensure_dir(out_dir)
    plot_heatmap_scenario2(s2, os.path.join(out_dir, "heatmap_scenario2.png"))
    plot_routes_comparison_scenario2(s2, os.path.join(out_dir, "routes_comparison_scenario2.png"))
    plot_coverage_timeline_scenario3(s3, os.path.join(out_dir, "coverage_timeline_scenario3.png"))
    plot_summary_table(s1, s2, s3, os.path.join(out_dir, "summary_table.png"))
