# Дипломная валидация MVP (Prompt 20) — обновлено после ввода PLR

Дата пересчета: 2026-04-24  
Проект: **SafeAgriRoute**

## 1) Базовые сценарии (перезапуск `backend/simulation/runner.py`)

### Сценарий 1 (штатный режим)
- Baseline: `coverage=100.0%`, `mean_IRM=1.000`, `time=1510.40s`, `waypoints=940`
- SafeAgriRoute: `coverage=100.0%`, `mean_IRM=1.000`, `time=1582.01s`, `waypoints=786`

### Сценарий 2 (статичные jammer-зоны)
- Baseline: `coverage=21.87%`, `routes_through_jammer=2`
- SafeAgriRoute: `coverage=91.82%`, `routes_through_jammer=0`, `mean_IRM=0.780`, `estimated_coverage=95.01%`

### Сценарий 3 (динамика + потеря дрона)
- Baseline timeline: `{0: 0.0, 25: 84.65, 50: 91.30, 75: 91.30, 100: 91.30}`
- SafeAgriRoute timeline: `{0: 0.0, 25: 68.54, 50: 83.50, 75: 95.14, 100: 100.0}`

## 2) Пересчет PLR-сценариев (runtime/fusion)

Синтетический прогон с packet-loss counters + jitter:

| scenario | drop_rate | mean_plr | max_jam_prob | detector_state | replan_triggered |
|---|---:|---:|---:|---|---|
| baseline | 0.00 | 0.000 | 0.382 | NORMAL | No |
| moderate_loss | 0.15 | 0.149 | 0.544 | SUSPECT | No |
| high_loss | 0.40 | 0.402 | 0.580 | SUSPECT | No |

Вывод по PLR: после добавления признака `PLR` модель устойчиво повышает `jam_prob`, но в изолированных loss-сценариях остается в `SUSPECT`. Для перехода в `CONFIRMED_JAMMING` нужны мультисигнальные деградации или отдельная калибровка порогов/весов.

## 3) KPI-статус после пересчета

| KPI | Статус |
|---|---|
| Mission Success Rate | Улучшение подтверждено в угрозовых сценариях (S2/S3) |
| FPR (пересечения jammer-маршрутов) | Улучшение: `50% -> 0%` |
| TTD | Нужен отдельный замер live-стенда под итоговые секунды |
| TTR | Нужен отдельный замер live-стенда под итоговые секунды |

## 4) Обновленные изображения

Перегенерированы в `backend/simulation/results/`:

- `heatmap_scenario2.png`
- `routes_comparison_scenario2.png`
- `coverage_timeline_scenario3.png`
- `summary_table.png`
- `metrics_overview_prompt20.png`
- `radar_baseline_vs_target.png`
- `incident_timeline_prompt20.png`
- `before_after_routes_concept.png`
- `state_machine_fusion.png`
- `executive_dashboard.png`
