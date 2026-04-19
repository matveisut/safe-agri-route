"""
Слияние признаков стабильности телеметрии в скаляр угрозы (ТЗ §10, Промпт 10).

Входные ``scores`` — это оценки **стабильности** из ``telemetry_features``
(1.0 = хорошо). Итог ``fused_threat_level`` ∈ [0, 1]: 1 — высокая уверенность
в деградации/угрозе. Канальная «опасность» = ``1 - stability``, затем взвешенная
сумма, опциональное усиление веса GNSS и экспоненциальное сглаживание по кадрам.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from app.core.config import (
    FUSION_FEATURE_LOW_THRESHOLD,
    FUSION_GNSS_BOOST,
    FUSION_SMOOTH_ALPHA,
    FUSION_WEIGHT_GNSS,
    FUSION_WEIGHT_IMU,
    FUSION_WEIGHT_LINK,
    FUSION_WEIGHT_SWARM,
)

_SCORE_KEYS = ("gnss", "link", "imu_proxy", "swarm")

# Сглаженное значение по ``drone_id`` (процесс в памяти)
_smoothed_threat: Dict[int, float] = {}


def reset_fusion_state(drone_id: Optional[int] = None) -> None:
    """Сбросить EMA для тестов или новой сессии."""
    global _smoothed_threat
    if drone_id is None:
        _smoothed_threat.clear()
    else:
        _smoothed_threat.pop(drone_id, None)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _normalize_weights(wg: float, wl: float, wi: float, ws: float) -> Tuple[float, float, float, float]:
    s = wg + wl + wi + ws
    if s <= 0:
        return 0.25, 0.25, 0.25, 0.25
    return wg / s, wl / s, wi / s, ws / s


def fuse_threat_scores(
    scores: Mapping[str, float],
    *,
    drone_id: int = 0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Объединить стабильности каналов в уровень угрозы.

    Parameters
    ----------
    scores
        Ключи: ``gnss``, ``link``, ``imu_proxy``, ``swarm`` — значения ∈ [0, 1]
        (стабильность). Отсутствующие ключи считаются 1.0 (нет сигнала опасности).
    drone_id
        Идентификатор для независимого EMA по дронам.

    Returns
    -------
    fused_threat_level
        Сглаченный уровень угрозы ∈ [0, 1].
    breakdown
        ``peril`` (вклад опасности по каналам до суммирования), ``weights`` (после
        усиления GNSS и нормировки), ``fused_raw``, ``fused_threat_level``,
        ``raw_scores``, ``any_feature_low``.
    """
    raw: Dict[str, float] = {}
    peril: Dict[str, float] = {}
    for key in _SCORE_KEYS:
        v = scores.get(key, 1.0)
        v = _clamp01(float(v))
        raw[key] = v
        peril[key] = _clamp01(1.0 - v)

    any_low = any(raw[k] < FUSION_FEATURE_LOW_THRESHOLD for k in _SCORE_KEYS)

    wg, wl, wi, ws = (
        FUSION_WEIGHT_GNSS,
        FUSION_WEIGHT_LINK,
        FUSION_WEIGHT_IMU,
        FUSION_WEIGHT_SWARM,
    )
    if any_low:
        wg *= 1.0 + FUSION_GNSS_BOOST
    wg, wl, wi, ws = _normalize_weights(wg, wl, wi, ws)

    fused_raw = (
        wg * peril["gnss"]
        + wl * peril["link"]
        + wi * peril["imu_proxy"]
        + ws * peril["swarm"]
    )
    fused_raw = _clamp01(fused_raw)

    alpha = FUSION_SMOOTH_ALPHA
    prev = _smoothed_threat.get(drone_id, fused_raw)
    smoothed = alpha * fused_raw + (1.0 - alpha) * prev
    smoothed = _clamp01(smoothed)
    _smoothed_threat[drone_id] = smoothed

    breakdown: Dict[str, Any] = {
        "raw_scores": raw,
        "peril": peril,
        "weights": {"gnss": wg, "link": wl, "imu_proxy": wi, "swarm": ws},
        "fused_raw": fused_raw,
        "fused_threat_level": smoothed,
        "any_feature_low": any_low,
    }
    return smoothed, breakdown


def get_last_fused_threat(drone_id: int = 0) -> Optional[float]:
    """Последнее сглаженное значение без нового расчёта (если ещё не было кадров)."""
    return _smoothed_threat.get(drone_id)
