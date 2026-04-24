"""
Generate additional Prompt-20 visuals using updated PLR-aware metrics.

Usage:
    python simulation/prompt20_visuals.py
"""

from __future__ import annotations

import os

import matplotlib.pyplot as plt
import numpy as np


FIG = {"figsize": (12, 8), "dpi": 300}


def _out_dir() -> str:
    path = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(path, exist_ok=True)
    return path


def _save(fig, name: str) -> None:
    fig.tight_layout()
    fig.savefig(os.path.join(_out_dir(), name))
    plt.close(fig)


def _plr_rows():
    # Recomputed from PLR-aware runtime experiment (2026-04-24)
    return [
        ("baseline", 0.00, 0.000, 0.382, "NORMAL", "No"),
        ("moderate_loss", 0.15, 0.149, 0.544, "SUSPECT", "No"),
        ("high_loss", 0.40, 0.402, 0.580, "SUSPECT", "No"),
    ]


def render_metrics_overview() -> None:
    fig, ax = plt.subplots(**FIG)
    ax.axis("off")
    rows = [[r[0], f"{r[1]:.2f}", f"{r[2]:.3f}", f"{r[3]:.3f}", r[4], r[5]] for r in _plr_rows()]
    cols = ["Scenario", "drop_rate", "mean_plr", "max_jam_prob", "detector_state", "replan"]
    t = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="center")
    t.scale(1.2, 2.1)
    ax.set_title("Prompt 20: Updated PLR Metrics (24.04.2026)")
    _save(fig, "metrics_overview_prompt20.png")


def render_radar() -> None:
    labels = ["Coverage S2", "IRM S2", "No jammer crossings", "Final coverage S3"]
    baseline = [21.9, 75.6, 50.0, 91.3]
    target = [91.8, 78.0, 100.0, 100.0]
    # Normalize to 0..1 for radar
    b = np.array(baseline) / 100.0
    t = np.array(target) / 100.0
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False)
    angles = np.append(angles, angles[0])
    b = np.append(b, b[0])
    t = np.append(t, t[0])

    fig = plt.figure(**FIG)
    ax = fig.add_subplot(111, polar=True)
    ax.plot(angles, b, "r--", linewidth=2, label="Baseline")
    ax.fill(angles, b, "r", alpha=0.15)
    ax.plot(angles, t, "g-", linewidth=2, label="SafeAgriRoute")
    ax.fill(angles, t, "g", alpha=0.15)
    ax.set_thetagrids(angles[:-1] * 180 / np.pi, labels)
    ax.set_ylim(0, 1.0)
    ax.set_title("Baseline vs Target (Updated)")
    ax.legend(loc="upper right")
    _save(fig, "radar_baseline_vs_target.png")


def render_incident_timeline() -> None:
    fig, ax = plt.subplots(**FIG)
    xs = [0, 25, 50, 75, 100]
    baseline = [0.0, 84.65, 91.30, 91.30, 91.30]
    target = [0.0, 68.54, 83.50, 95.14, 100.0]
    ax.plot(xs, baseline, "r--o", label="Baseline")
    ax.plot(xs, target, "g-s", label="SafeAgriRoute")
    ax.axvline(40, color="gray", linestyle=":")
    ax.axvline(60, color="purple", linestyle=":")
    ax.text(40, 98, "Jammer appears", rotation=90, va="top", ha="right", fontsize=9)
    ax.text(60, 98, "Drone #2 loss", rotation=90, va="top", ha="right", fontsize=9)
    ax.set_xlabel("Mission progress, %")
    ax.set_ylabel("Coverage, %")
    ax.set_title("Incident Timeline (Updated Scenario 3)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    _save(fig, "incident_timeline_prompt20.png")


def render_before_after() -> None:
    fig, ax = plt.subplots(**FIG)
    ax.axis("off")
    txt = (
        "Before / After (Updated):\n\n"
        "Scenario 2:\n"
        "- Baseline coverage: 21.9%\n"
        "- SAR coverage: 91.8%\n"
        "- Baseline jammer-route crossings: 2/4 (50%)\n"
        "- SAR jammer-route crossings: 0/4 (0%)\n\n"
        "Scenario 3 final coverage:\n"
        "- Baseline: 91.3%\n"
        "- SAR: 100.0%"
    )
    ax.text(0.05, 0.95, txt, va="top", ha="left", fontsize=14)
    ax.set_title("Routes Before/After Concept (Updated)")
    _save(fig, "before_after_routes_concept.png")


def render_state_machine() -> None:
    fig, ax = plt.subplots(**FIG)
    ax.axis("off")
    text = (
        "Fusion detector state machine\n\n"
        "NORMAL -> SUSPECT -> CONFIRMED_JAMMING -> RECOVERING -> NORMAL\n\n"
        "Updated observation (PLR-only runs):\n"
        "- baseline: NORMAL\n"
        "- moderate loss (0.15): SUSPECT\n"
        "- high loss (0.40): SUSPECT\n\n"
        "Interpretation: PLR is a strong feature, but current thresholds\n"
        "require multi-signal degradation for CONFIRMED transition."
    )
    ax.text(0.05, 0.95, text, va="top", ha="left", fontsize=13)
    ax.set_title("State Machine & PLR Observation (Updated)")
    _save(fig, "state_machine_fusion.png")


def render_dashboard() -> None:
    fig, ax = plt.subplots(**FIG)
    ax.axis("off")
    lines = [
        "Executive Dashboard (Updated)",
        "",
        "Scenario 1: coverage baseline/SAR = 100.0% / 100.0%",
        "Scenario 2: coverage baseline/SAR = 21.9% / 91.8%",
        "Scenario 3 final: baseline/SAR = 91.3% / 100.0%",
        "PLR moderate (0.15): mean_plr=0.149, state=SUSPECT",
        "PLR high (0.40): mean_plr=0.402, state=SUSPECT",
        "",
        "KPI status:",
        "- Mission Success Rate: improved in threat scenarios",
        "- FPR (jammer-route crossings): 50% -> 0%",
        "- TTD/TTR: require dedicated live-stand measurements",
    ]
    ax.text(0.05, 0.95, "\n".join(lines), va="top", ha="left", fontsize=13)
    _save(fig, "executive_dashboard.png")


def main() -> None:
    render_metrics_overview()
    render_radar()
    render_incident_timeline()
    render_before_after()
    render_state_machine()
    render_dashboard()
    print(f"Updated Prompt-20 visuals saved to {_out_dir()}")


if __name__ == "__main__":
    main()
