"""
Тесты threat_fusion (ТЗ §10). Run: pytest backend/tests/test_threat_fusion.py -v
"""

from __future__ import annotations

import pytest

from app.services.threat_fusion import fuse_threat_scores, reset_fusion_state


@pytest.fixture(autouse=True)
def _reset_fusion():
    reset_fusion_state()
    yield
    reset_fusion_state()


def _stable_scores(gnss: float, **kwargs) -> dict:
    base = {
        "gnss": gnss,
        "link": 0.95,
        "imu_proxy": 0.95,
        "swarm": 0.95,
        "plr": 0.95,
    }
    base.update(kwargs)
    return base


def test_worse_gnss_higher_threat_than_good_gnss():
    """Ниже стабильность GNSS → выше угроза (при прочих равных)."""
    good, _ = fuse_threat_scores(_stable_scores(0.95), drone_id=1)
    reset_fusion_state()
    bad, _ = fuse_threat_scores(_stable_scores(0.2), drone_id=1)
    assert bad > good


def test_breakdown_contains_weights_and_peril():
    fused, br = fuse_threat_scores(
        {"gnss": 0.5, "link": 0.5, "imu_proxy": 0.5, "swarm": 0.5, "plr": 0.5},
        drone_id=0,
    )
    assert fused == br["fused_threat_level"]
    assert set(br["peril"].keys()) == {"gnss", "link", "imu_proxy", "swarm", "plr"}
    assert br["peril"]["gnss"] == pytest.approx(0.5)
    w = br["weights"]
    assert abs(w["gnss"] + w["link"] + w["imu_proxy"] + w["swarm"] + w["plr"] - 1.0) < 1e-9


def test_smoothing_blends_with_previous_frame():
    """Сглаженная угроза между предыдущим кадром и текущим raw (alpha < 1)."""
    reset_fusion_state()
    perfect = {"gnss": 1.0, "link": 1.0, "imu_proxy": 1.0, "swarm": 1.0, "plr": 1.0}
    fused_good, _ = fuse_threat_scores(perfect, drone_id=7)
    assert fused_good == pytest.approx(0.0, abs=1e-9)
    fused_bad, br = fuse_threat_scores(_stable_scores(0.05), drone_id=7)
    raw = br["fused_raw"]
    assert raw > fused_good
    assert fused_bad < raw


def test_any_low_feature_sets_flag_and_boosts_gnss_weight_in_breakdown():
    fused_low, br_low = fuse_threat_scores(
        {"gnss": 0.9, "link": 0.2, "imu_proxy": 0.9, "swarm": 0.9, "plr": 0.9},
        drone_id=2,
    )
    reset_fusion_state(2)
    fused_ok, br_ok = fuse_threat_scores(
        {"gnss": 0.9, "link": 0.9, "imu_proxy": 0.9, "swarm": 0.9, "plr": 0.9},
        drone_id=2,
    )
    assert br_low["any_feature_low"] is True
    assert br_ok["any_feature_low"] is False
    assert br_low["weights"]["gnss"] >= br_ok["weights"]["gnss"] - 1e-12


def test_missing_keys_treated_as_stable():
    fused, br = fuse_threat_scores({}, drone_id=3)
    assert br["raw_scores"]["gnss"] == 1.0
    assert fused == pytest.approx(0.0)


def test_high_plr_increases_threat_with_same_other_features():
    low_plr, _ = fuse_threat_scores(
        {"gnss": 0.9, "link": 0.9, "imu_proxy": 0.9, "swarm": 0.9, "plr": 0.95},
        drone_id=8,
    )
    reset_fusion_state(8)
    high_plr, _ = fuse_threat_scores(
        {"gnss": 0.9, "link": 0.9, "imu_proxy": 0.9, "swarm": 0.9, "plr": 0.25},
        drone_id=8,
    )
    assert high_plr > low_plr
