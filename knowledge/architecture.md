# Архитектура проекта SafeAgriRoute

Детальное описание слоистой архитектуры для разработчиков и инженеров поддержки.

---

## Обзор системы

```
┌─────────────────────────────────────────────────────────┐
│                     Frontend (React)                     │
│  MissionPanel  ──►  POST /mission/plan                  │
│  MapArea       ◄──  GET  /fields, /risk-zones           │
│  useTelemetry  ◄──  WS   /ws/telemetry/{drone_id}       │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP / WebSocket
                            ▼
┌─────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                      │
│                                                          │
│  /auth/*          JWT auth (login / register)           │
│  /api/v1/mission  Mission planning & replanning          │
│  /ws/telemetry    MAVLink telemetry stream               │
│                                                          │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────┐  │
│  │RoutingService│  │  RiskMapService │  │  Replanner  │  │
│  └──────┬───────┘  └────────────────┘  └──────┬──────┘  │
│         │                                      │         │
│  ┌──────▼──────────────────────────────────────▼──────┐  │
│  │              MAVLinkService (pymavlink)             │  │
│  └──────────────────────────┬──────────────────────── ┘  │
└─────────────────────────────┼───────────────────────────┘
                              │ TCP (MAVLink 2.0)
                              ▼
               ┌──────────────────────────┐
               │   ArduPilot SITL / Drone  │
               │   tcp:127.0.0.1:5760      │
               └──────────────────────────┘
```

---

## 1. Бэкенд

Фреймворк: **FastAPI**. База данных: **PostgreSQL 15 + PostGIS 3.3**.

### 1.1 Слой данных (Models & Repositories)

Файлы: `backend/app/models/`, `backend/app/repositories/`

**Модели SQLAlchemy:**

| Модель | Ключевые поля | Особенности |
|---|---|---|
| `Field` | `name`, `geometry` | `Geometry('POLYGON', srid=4326)` через GeoAlchemy2 |
| `RiskZone` | `type`, `geometry`, `severity_weight` | `Geometry('POLYGON', srid=4326)` |
| `Drone` | `name`, `battery_capacity`, `max_speed`, `status` | |
| `User` | `email`, `hashed_password`, `role`, `is_active` | `role`: `"operator"` или `"viewer"` |

`BaseRepository` — дженерик-класс с полным CRUD поверх `AsyncSession`. Все репозитории наследуют его и добавляют доменные запросы (например, `UserRepository.get_by_email`).

Геометрия возвращается через `ST_AsGeoJSON()` — бэкенд отдаёт GeoJSON-строки, фронтенд их парсит и свапает координаты `[lng, lat] → [lat, lng]` для Leaflet.

---

### 1.2 Аутентификация (JWT)

Файлы: `backend/app/core/security.py`, `backend/app/api/deps.py`, `backend/app/api/routers/auth.py`

**Схема:** OAuth2 Password Flow + JWT Bearer.

- Пароли хешируются `bcrypt` (прямой вызов `bcrypt.hashpw/checkpw`, без passlib — несовместима с bcrypt 5.x).
- Токены подписываются через `python-jose` (HS256, срок жизни 8 часов, настраивается через `.env`).
- Конфигурация: `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_HOURS` в файле `backend/.env`.

**Dependency-иерархия:**

```
oauth2_scheme (Bearer token)
    └── get_current_user()    → проверяет токен, достаёт User из БД → 401 если невалиден
            ├── require_viewer  → alias, любая активная роль
            └── require_operator → 403 если role != "operator"
```

**Матрица доступа:**

| Эндпоинт | Минимальная роль |
|---|---|
| `GET /fields`, `GET /risk-zones` | viewer |
| `POST /plan`, `POST /start` | operator |
| `POST /simulate-loss`, `POST /risk-zones` | operator |

---

### 1.3 Математическое ядро: RoutingService

Файл: `backend/app/services/routing_service.py`

Пайплайн `plan_mission()`:

#### Шаг 1: Построение карты рисков

Вызывает `build_risk_map()` из `risk_map.py`:
- Создаёт дискретную numpy-сетку с шагом `0.0002°` (~22 м) над bbox поля.
- Для каждой ячейки вычисляет риск:
  - **jammer-зоны**: линейное затухание от границы зоны в радиусе `0.005°` (~500 м), умноженное на `severity`.
  - **restricted-зоны**: `severity` внутри периметра, 0 снаружи.
  - `R = min(1.0, r_jammer + r_zone)`, нормализация на [0, 1].
- Точки внутри зон РЭБ исключаются из пула waypoints.

#### Шаг 2: Risk-Weighted Voronoi (замена K-Means)

Взвешенный алгоритм Ллойда (до 50 итераций):
- Каждая точка получает вес: `w = 1 / (1 - risk + ε)`
- Инициализация N центроидов равномерно внутри поля.
- На каждой итерации: назначение точек ближайшему центроиду → сдвиг центроида во взвешенный центр своей зоны.
- **Итог:** опасные территории получают более высокий вес — центроиды уходят от зон РЭБ, деля поле с учётом угроз.

Назначение дронов на зоны — жадный алгоритм:
- `zone_load = Σ weight` всех точек зоны.
- Дроны сортируются по `battery_capacity × max_speed` (суммарный ресурс).
- Самый мощный дрон → самая тяжёлая зона.

Добавляются метрики:
- **`reliability_index` (IRM)**: `1 − mean(risk всех waypoints маршрута)` ∈ [0, 1].
- **`estimated_coverage_pct`**: `(достижимые точки / все точки поля) × 100`.

#### Шаг 3: Построение взвешенного графа

Для каждого кластера строится полный граф (`NetworkX`):
- Базовый вес ребра — евклидово расстояние.
- **Penalty-система**: если ребро A→B пересекает RiskZone, его вес увеличивается на `severity_weight × 500`. Полёт «насквозь» становится математически невыгодным.

#### Шаг 4: Решение CVRP

Матрица расстояний подаётся в **Google OR-Tools** (эвристика `PATH_CHEAPEST_ARC`).  
Результат: массив `RoutePoint` — точный маршрут каждого дрона.

---

### 1.4 Динамическое перепланирование

Файл: `backend/app/services/replanner.py`

**Сценарий A — потеря дрона** (`replan_on_drone_loss`):
1. Определяет непосещённые waypoints потерянного дрона.
2. Вычисляет `residual_capacity` активных дронов: `battery_capacity × max_speed`.
3. Распределяет waypoints пропорционально весам, приоритизируя географически близкие точки.
4. Пересчитывает TSP для затронутых дронов через жадный NN-алгоритм (O(n²), <2 мс для n=67).
5. Вычисляет новый IRM.

**Сценарий B — новая зона РЭБ** (`replan_on_new_risk_zone`):
1. Инкрементально добавляет новую зону в карту рисков.
2. Проверяет пересечение оставшихся сегментов каждого дрона с зоной (`LineString.intersects`).
3. Для затронутых дронов пересчитывает TSP с обновлёнными весами рёбер.

CPU-интенсивные операции выполняются в `asyncio.run_in_executor` — event loop не блокируется. Целевое время выполнения ≤ 500 мс для 4 дронов и 200 waypoints.

---

### 1.5 MAVLink-интеграция

Файл: `backend/app/services/mavlink_service.py`

Синглтон `mavlink_service` инициализируется при старте FastAPI (`lifespan` context manager) и подключается к дронам из переменной `SITL_HOSTS`.

**Конфигурация:**
```
SITL_HOSTS=tcp:127.0.0.1:5760,tcp:127.0.0.1:5770,...
```
По умолчанию: `tcp:127.0.0.1:5760` (один дрон, SITL без MAVProxy).

**Ключевые методы:**

| Метод | Описание |
|---|---|
| `connect_all()` | Подключается ко всем хостам при старте; запускает фоновый reconnect loop (каждые 10 сек) |
| `upload_mission(drone_id, waypoints)` | Загружает маршрут через `MISSION_ITEM_INT` протокол |
| `start_mission(drone_id)` | GUIDED → ARM → TAKEOFF 30м → AUTO |
| `update_mission(drone_id, waypoints)` | `MISSION_CLEAR_ALL` → upload → `MISSION_START` (для replanner) |
| `read_telemetry_loop(drone_id)` | Async-генератор, читает `GLOBAL_POSITION_INT`, `BATTERY_STATUS`, `HEARTBEAT`, `VFR_HUD` каждые 200 мс |
| `simulate_drone_loss(drone_id)` | Помечает дрон `LOST` без остановки SITL (demo) |

Все blocking-вызовы pymavlink выполняются в `run_in_executor`. Перед созданием подключения делается быстрый TCP socket check (1 сек таймаут) — pymavlink не вызывается если порт закрыт, что исключает шумные сообщения `Connection refused sleeping`.

При потере heartbeat (>5 сек) дрон помечается `LOST`, телеметрия-генератор останавливается — вызывающий код может автоматически запустить replanner.

---

### 1.6 WebSocket телеметрия

Файл: `backend/app/api/routers/telemetry.py`

| Эндпоинт | Описание |
|---|---|
| `WS /ws/telemetry` | Симуляция: принимает planned routes, шагает по точкам каждые 100 мс |
| `WS /ws/telemetry/{drone_id}` | Реальная MAVLink телеметрия через `read_telemetry_loop()`, 200 мс |

При `status=LOST` WebSocket отправляет `{"event": "drone_lost", "drone_id": N}` и закрывает соединение — сигнал для фронтенда запустить replanner.

---

## 2. Фронтенд (React/Vite)

### 2.1 Глобальный стейт (Zustand)

Файл: `frontend/src/store/useMissionStore.ts`

| Поле | Тип | Назначение |
|---|---|---|
| `selectedFieldId` | `number \| null` | Выбранное поле |
| `selectedDroneIds` | `number[]` | Выбранные дроны |
| `plannedRoutes` | `DroneRoute[]` | Результат `/plan` — векторные линии маршрутов |
| `telemetry` | `Record<number, Coordinates>` | Live-позиция каждого дрона, обновляется ~5 раз/сек |

Zustand обновляет только подписанные срезы стора — `CircleMarker` перерисовывается только при изменении своих координат.

### 2.2 Компонент карты (`MapArea.tsx`)

- При инициализации загружает поля и зоны риска (2 GET-запроса).
- Рендерит GeoJSON-полигоны полей и зон через React-Leaflet.
- Маршруты рисуются `Polyline`, цвет по `drone_id % colorPalette`.
- Маркер дрона — `CircleMarker`, реагирует на мутации `telemetry[drone_id]`.

### 2.3 Панель оператора (`MissionPanel.tsx`)

- Выбор поля и дронов → `POST /mission/plan` → `setPlannedRoutes`.
- `startSimulation()` из `useTelemetry.ts` → открывает WebSocket → обновляет `telemetry` в стейте.

---

## 3. Структура проекта

```
safe-agri-route/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── deps.py              # get_current_user, require_operator, require_viewer
│   │   │   └── routers/
│   │   │       ├── auth.py          # POST /auth/login, /auth/register
│   │   │       ├── mission.py       # /mission/* (plan, start, simulate-loss, risk-zones)
│   │   │       └── telemetry.py     # WS /ws/telemetry, /ws/telemetry/{id}
│   │   ├── core/
│   │   │   └── security.py          # hash_password, verify_password, JWT encode/decode
│   │   ├── models/                  # SQLAlchemy: Field, RiskZone, Drone, User
│   │   ├── repositories/            # BaseRepository + доменные: field, risk_zone, drone, user
│   │   ├── schemas/
│   │   │   └── mission.py           # Pydantic: PlanMissionRequest/Response, ReplanResponse, ...
│   │   ├── services/
│   │   │   ├── routing_service.py   # Voronoi + graph + OR-Tools CVRP
│   │   │   ├── risk_map.py          # Дискретная карта рисков (numpy)
│   │   │   ├── replanner.py         # Сценарии A (потеря дрона) и B (новая зона)
│   │   │   └── mavlink_service.py   # MAVLink: upload/start/update mission, telemetry loop
│   │   ├── database.py              # AsyncEngine, AsyncSessionLocal, get_db()
│   │   └── main.py                  # FastAPI app, lifespan (MAVLink init), CORS
│   ├── tests/
│   │   ├── test_routing.py
│   │   ├── test_risk_map.py
│   │   └── test_replanner.py
│   ├── seed.py                      # Демо-данные + тестовые пользователи
│   ├── .env                         # JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── features/
│       │   ├── MapDashboard/MapArea.tsx
│       │   └── MissionControl/MissionPanel.tsx
│       ├── hooks/useTelemetry.ts
│       ├── services/api.ts          # Axios instance
│       └── store/useMissionStore.ts
├── docker-compose.yml
├── ARCHITECTURE.md
└── README.md
```

---

## 4. Векторы развития

1. **Production Docker**: настроить Nginx для раздачи собранного фронтенда вместо Vite dev-сервера.
2. **Haversine distance**: заменить евклидово расстояние в `calculate_distance()` на формулу Хаверсина — актуально для полей > 10 км.
3. **Ветровая коррекция**: добавить в БД скорость ветра и включить её как множитель в penalty-систему графа.
4. **Mission DB**: создать таблицу `Mission` для персистентного хранения запущенных миссий и привязки heartbeat-истории.
5. **Multi-tenant**: изолировать данные по организациям (fields, drones принадлежат tenant).
