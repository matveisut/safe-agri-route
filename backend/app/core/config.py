"""
Параметры fusion угрозы (ТЗ §10). Значения по умолчанию можно переопределить через .env.

Переменные окружения (все опциональны):
- FUSION_WEIGHT_GNSS, FUSION_WEIGHT_LINK, FUSION_WEIGHT_IMU, FUSION_WEIGHT_SWARM, FUSION_WEIGHT_PLR
  — ненормированные веса; после загрузки нормируются к сумме 1.
- FUSION_FEATURE_LOW_THRESHOLD — порог «сырого» признака-стабильности, ниже которого
  считается деградация и усиливается вклад GNSS.
- FUSION_GNSS_BOOST — множитель к весу GNSS перед нормировкой при срабатывании правила.
- FUSION_SMOOTH_ALPHA — коэффициент экспоненциального сглаживания итоговой угрозы.
- FUSION_THRESHOLD — уровень сглаженной угрозы для логики перепланирования (Промпт 11).
- FUSION_AUTO_REPLAN_STREAK — подряд кадров с угрозой ≥ порога до триггера replan.
- FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC — минимум секунд между авто-replan на миссию.
- FUSION_AUTO_ZONE_RADIUS_M — радиус круговой зоны jammer вокруг дрона (метры).
- FUSION_AUTO_SEVERITY_SCALE — множитель severity зоны: min(1, fused * scale).
"""

from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


# Сырые веса (сумма может быть ≠ 1 — нормируем при импорте)
_WG = _env_float("FUSION_WEIGHT_GNSS", 0.35)
_WL = _env_float("FUSION_WEIGHT_LINK", 0.25)
_WI = _env_float("FUSION_WEIGHT_IMU", 0.25)
_WS = _env_float("FUSION_WEIGHT_SWARM", 0.15)
_WP = _env_float("FUSION_WEIGHT_PLR", 0.20)
_W_SUM = _WG + _WL + _WI + _WS + _WP
if _W_SUM <= 0:
    raise ValueError("Fusion weights must sum to a positive value")

FUSION_WEIGHT_GNSS: float = _WG / _W_SUM
FUSION_WEIGHT_LINK: float = _WL / _W_SUM
FUSION_WEIGHT_IMU: float = _WI / _W_SUM
FUSION_WEIGHT_SWARM: float = _WS / _W_SUM
FUSION_WEIGHT_PLR: float = _WP / _W_SUM

FUSION_FEATURE_LOW_THRESHOLD: float = _env_float("FUSION_FEATURE_LOW_THRESHOLD", 0.3)
FUSION_GNSS_BOOST: float = _env_float("FUSION_GNSS_BOOST", 0.5)

# Новое сглаженное значение: alpha * raw + (1-alpha) * previous
FUSION_SMOOTH_ALPHA: float = _env_float("FUSION_SMOOTH_ALPHA", 0.25)

# Порог для downstream (replanner); сам fusion только вычисляет уровень
FUSION_THRESHOLD: float = _env_float("FUSION_THRESHOLD", 0.65)

# Автоперепланирование по fusion (Промпт 11)
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


FUSION_AUTO_REPLAN_STREAK: int = max(1, _env_int("FUSION_AUTO_REPLAN_STREAK", 5))
FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC: float = _env_float(
    "FUSION_AUTO_REPLAN_MIN_INTERVAL_SEC", 30.0
)
FUSION_AUTO_ZONE_RADIUS_M: float = _env_float("FUSION_AUTO_ZONE_RADIUS_M", 350.0)
FUSION_AUTO_SEVERITY_SCALE: float = _env_float("FUSION_AUTO_SEVERITY_SCALE", 1.0)

# Detector state-machine + dynamic zones (Промпт 14)
FUSION_DETECTOR_ALPHA: float = _env_float("FUSION_DETECTOR_ALPHA", 0.35)
FUSION_DETECTOR_T_HIGH: float = _env_float("FUSION_DETECTOR_T_HIGH", 0.72)
FUSION_DETECTOR_T_LOW: float = _env_float("FUSION_DETECTOR_T_LOW", 0.45)
FUSION_DETECTOR_CONFIRM_STREAK: int = max(
    1, _env_int("FUSION_DETECTOR_CONFIRM_STREAK", 3)
)
FUSION_DETECTOR_RECOVERY_STREAK: int = max(
    1, _env_int("FUSION_DETECTOR_RECOVERY_STREAK", 5)
)
FUSION_DYNAMIC_ZONE_TTL_SEC: float = _env_float("FUSION_DYNAMIC_ZONE_TTL_SEC", 45.0)
FUSION_DYNAMIC_ZONE_MERGE_DISTANCE_M: float = _env_float(
    "FUSION_DYNAMIC_ZONE_MERGE_DISTANCE_M", 70.0
)
FUSION_DYNAMIC_ZONE_RADIUS_BASE_M: float = _env_float(
    "FUSION_DYNAMIC_ZONE_RADIUS_BASE_M", 40.0
)
FUSION_DYNAMIC_ZONE_RADIUS_GAIN_M: float = _env_float(
    "FUSION_DYNAMIC_ZONE_RADIUS_GAIN_M", 120.0
)

# Safety action before replan (Промпт 16)
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_SAFETY_ACTION_BEFORE_REPLAN: bool = _env_bool(
    "ENABLE_SAFETY_ACTION_BEFORE_REPLAN", False
)
SAFETY_ACTION: str = os.getenv("SAFETY_ACTION", "LOITER").strip().upper()
