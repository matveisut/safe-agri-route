# Тестирование — SafeAgriRoute

Описание тестовой структуры: что покрывают тесты, как запускать, маркеры.

---

## Запуск

```bash
# Полный прогон из корня проекта
source venv/bin/activate
pytest

# Только unit (без Docker и запущенного стека)
pytest -m "not docker and not stack and not integration"

# Конкретный файл
pytest backend/tests/test_routing.py -v

# С выводом stdout (полезно для отладки)
pytest -s -v backend/tests/test_mavlink_service.py
```

---

## Структура тестов

```
backend/
├── test_api.py                     # smoke-тест API роутинга
└── tests/
    ├── test_routing.py             # RoutingService (Voronoi, граф, CVRP)
    ├── test_risk_map.py            # build_risk_map (jammer, restricted, boundary)
    ├── test_replanner.py           # replan_on_drone_loss, replan_on_new_risk_zone
    ├── test_mavlink_service.py     # MAVLinkService (unit + integration с SITL)
    ├── test_ui_endpoints.py        # Pydantic-схемы, risk_grid_preview, telemetry WS
    └── test_infrastructure.py     # docker-compose, скрипты, stack smoke-тесты
```

---

## Маркеры pytest

Определены в `pytest.ini` (корень репозитория):

| Маркер | Условие запуска | Что тестирует |
|---|---|---|
| *(без маркера)* | Всегда | Unit-тесты без внешних зависимостей |
| `integration` | SITL на `tcp:127.0.0.1:5760` | Живой ArduPilot SITL |
| `docker` | Docker CLI установлен | `docker compose config` валидация |
| `stack` | Бэкенд на порту 8000 | Поднятый docker-compose стек |

```bash
# Только с SITL
pytest -m integration

# Только Docker-валидация
pytest -m docker

# Stack smoke-тесты (нужен docker-compose up + seed.py)
pytest -m stack
```

---

## test_routing.py

**`TestPlanMissionAvoidRiskZones`**
- Поле 0.01°×0.01°, зона риска в центре
- Проверяет: ни один waypoint не попадает внутрь RiskZone
- Проверяет: `reliability_index` ∈ [0, 1], `estimated_coverage_pct` > 0

**`TestRiskWeightedVoronoi`**
- `risk_weighted_voronoi(pts, k=3)` → 3 кластера
- Все точки назначены ровно 1 раз
- k > len(pts) → не падает

**`TestGenerateWeightedGrid`**
- Безопасные точки не пересекаются с RiskZone
- `total_count >= len(safe_points)`
- Все веса > 0

**`TestProximityRisk`**
- Точка в 10° от зоны → risk == 0.0
- Точка у границы → risk > 0.0
- risk всегда ≤ 1.0

---

## test_risk_map.py

**`TestNoRiskZones`** — сетка без зон: все значения == 0, сетка не пустая.

**`TestJammerZone`** — центральные ячейки внутри jammer имеют высокий риск; за пределами influence_radius → 0.

**`TestRestrictedZone`** — строгая граница: внутри > 0, снаружи == 0.

**`TestGridBoundary`** — все точки сетки лежат внутри поля.

**`TestGetRiskForPoint`** — lookup вне bbox → 0.0; внутри совпадает со значением сетки.

---

## test_replanner.py

**`TestReplanOnDroneLoss`**
- Потерянные waypoints перераспределяются среди активных дронов
- Суммарное покрытие не уменьшается
- `partial_visited_drone` → только uncovered waypoints берутся у потерявшегося
- Нет активных дронов → `mission_failed`
- `new_irm` ∈ [0, 1]

**`TestReplanOnNewRiskZone`**
- Дроны без пересечения → маршрут не меняется
- Дроны с пересечением → перепланируются
- `visited_counts` корректно отсекает уже пройденное

**`TestPerformance`**
- 4 дрона × 50 waypoints → должно завершиться за **<500 мс**
- Тест использует прогретый ThreadPoolExecutor внутри одного `asyncio.run()`

---

## test_mavlink_service.py

### Unit-тесты (без SITL)

**`TestParseHosts`** — разбор `SITL_HOSTS`: одиночный, список, пробелы.

**`TestEmptySnapshot`** — структура `_empty_snapshot()`: все ключи, статус.

**`TestConnectAll`**
- Нет pymavlink → simulation_mode
- Нет heartbeat → simulation_mode
- Heartbeat получен → drone в `connections`, status `ACTIVE`

**`TestSimulateDroneLoss`** — удаляет из connections, статус `LOST`, идемпотентен.

**`TestUploadMission`** — False без подключения; happy path: mission_count_send вызван; timeout → False.

**`TestStartMission`** — 4 команды (GUIDED, ARM, TAKEOFF, AUTO); ARM rejected → False.

**`TestUpdateMission`** — CLEAR → upload → START; `mission_clear_all_send` вызван.

**`TestBlockingReadTelemetry`** — разбор `GLOBAL_POSITION_INT`, `BATTERY_STATUS`, `VFR_HUD`, `HEARTBEAT`; battery=-1 не перезаписывает кэш; пустое окно возвращает кэш.

**`TestReadTelemetryLoop`** — simulation_mode → STATUS_LOST; heartbeat timeout → LOST и генератор останавливается.

### Integration-тесты (маркер `integration`)

Требуют ArduPilot SITL на `tcp:127.0.0.1:5760`. `setup_class` подключается один раз на весь класс + `time.sleep(10)` для EKF инициализации.

- `test_connect_and_heartbeat` — соединение есть, не simulation_mode
- `test_upload_mission_returns_true` — загрузка 3 waypoints → True
- `test_telemetry_has_valid_coordinates` — хоть один фрейм из 20 с ненулевым GPS
- `test_telemetry_frame_has_required_keys` — все 8 ключей присутствуют
- `test_simulate_drone_loss_disconnects` — дрон убран из connections

---

## test_ui_endpoints.py

**`TestSchemas`** — Pydantic валидация: RiskGridPoint, PlanMissionResponse, CreateFieldRequest, CreateRiskZoneRequest.

**`TestPlanMissionRiskPreview`** — `risk_grid_preview` заполняется, риски ∈ [0, 1], каждая 3-я точка (sparse sampling).

**`TestCreateFieldEndpoint`** — GeoJSON Polygon парсится в Shapely → WKTElement без ошибок.

**`TestTelemetryIRMUpdate`** — `TelemetryStartPayload` принимает `irm`, по умолчанию None.

---

## test_infrastructure.py

Тесты инфраструктурных файлов без запуска Docker.

**`TestMainCompose`** — docker-compose.yml: сервисы db/backend/frontend, порты 8000/3000, сеть safagri_net, healthcheck БД, depends_on с condition.

**`TestSITLCompose`** — docker-compose.sitl.yml: 4 SITL-сервиса, порты 14550-14580, инстансы 0-3, координаты Ставрополя, healthcheck nc, SITL_HOSTS в backend-оверлее, depends_on с service_healthy.

**`TestSITLScript`** — start_sitl_wsl.sh: shebang, executable, 4 порта, ArduCopter, --no-mavproxy, PID-файл, bash синтаксис.

**`TestDockerfileSITL`** — Ubuntu 22.04, ArduPilot, ENV INSTANCE/PORT, sim_vehicle.py, non-root USER.

**`TestDockerfileFrontend`** — Node 20, порт 3000, npm.

**`TestDockerConfigValidation`** *(маркер docker)* — `docker compose config` не падает для обоих compose-файлов.

**`TestRunningStack`** *(маркер stack)* — порты 8000/3000 открыты, Swagger 200, /auth/login 4xx, /fields 401.

**`TestSITLHostsConfig`** — `_parse_sitl_hosts()` корректно разбирает строки из SITL-оверлея: 4 дрона, tcp-схема, sequential IDs от 1.

---

## Часто встречающиеся проблемы

| Проблема | Решение |
|---|---|
| `test_drone_loss_completes_within_500ms` падает | Тест запускается пока компилируется SITL (100% CPU). Дождаться окончания компиляции |
| SITL-тесты скипаются | SITL не запущен. `bash start_sitl_wsl.sh` или дождаться компиляции |
| stack-тесты скипаются | `docker-compose up -d` + `docker exec backend python seed.py` |
| frontend-тесты скипаются | `cd frontend && bash node_modules/.bin/vite --host 0.0.0.0 --port 3000 &` |
