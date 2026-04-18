# API Reference — SafeAgriRoute

Base URL: `http://localhost:8000`  
Интерактивная документация: `http://localhost:8000/docs` (Swagger UI)

Все эндпоинты кроме `/auth/*` требуют заголовок:
```
Authorization: Bearer <access_token>
```

---

## Аутентификация (`/auth`)

### POST /auth/login

Получить JWT-токен.

**Body** (`application/x-www-form-urlencoded`):
```
username=operator@safegriroute.com&password=operator123
```

**Response 200:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer"
}
```

**Response 401:** неверные credentials.

---

### POST /auth/register

Создать нового пользователя.

**Body** (`application/json`):
```json
{
  "email": "user@example.com",
  "password": "secret",
  "role": "operator"
}
```

`role`: `"operator"` | `"viewer"`

**Response 200:**
```json
{ "id": 3, "email": "user@example.com", "role": "operator" }
```

---

## Поля (`/mission/fields`)

### GET /mission/fields

Список всех сельскохозяйственных полей с геометрией.

**Роль:** viewer+

**Response 200:**
```json
{
  "fields": [
    {
      "id": 1,
      "name": "Stavropol Wheat Field (Irregular)",
      "geojson": "{\"type\":\"Polygon\",\"coordinates\":[[[42.1484,45.0348],...]]}"
    }
  ]
}
```

`geojson` — строка GeoJSON Polygon в системе WGS 84, координаты `[longitude, latitude]`.

---

### POST /mission/fields

Создать новое поле из полигона, нарисованного на карте.

**Роль:** operator

**Body:**
```json
{
  "name": "North Field",
  "geojson": "{\"type\":\"Polygon\",\"coordinates\":[[[42.14,45.03],[42.18,45.03],[42.18,45.06],[42.14,45.06],[42.14,45.03]]]}"
}
```

**Response 200:**
```json
{ "id": 2, "name": "North Field" }
```

---

## Зоны риска (`/mission/risk-zones`)

### GET /mission/risk-zones

Список зон РЭБ.

**Роль:** viewer+

**Response 200:**
```json
{
  "risk_zones": [
    {
      "id": 1,
      "type": "Jamming",
      "severity_weight": 2.0,
      "geojson": "{\"type\":\"Polygon\",...}"
    }
  ]
}
```

`type`: `"Jamming"` (глушение) | `"Spoofing"` (подмена сигнала)  
`severity_weight`: множитель тяжести зоны (float, используется в penalty-системе и IRM)

---

### POST /mission/risk-zones

Создать новую зону РЭБ.

**Роль:** operator

**Body:**
```json
{
  "zone_type": "jammer",
  "severity_weight": 0.8,
  "geojson": "{\"type\":\"Polygon\",\"coordinates\":[[[42.14,45.02],[42.17,45.02],[42.16,45.04],[42.14,45.02]]]}"
}
```

**Response 200:**
```json
{ "id": 3, "type": "jammer", "severity_weight": 0.8 }
```

---

## Планирование миссии

### POST /mission/plan

Спланировать маршруты роя.

**Роль:** operator

**Body:**
```json
{
  "field_id": 1,
  "drone_ids": [1, 2, 3, 4]
}
```

**Response 200:**
```json
{
  "routes": [
    {
      "drone_id": 1,
      "route": [
        { "lat": 45.044, "lng": 42.148 },
        { "lat": 45.046, "lng": 42.151 }
      ]
    }
  ],
  "reliability_index": 0.87,
  "estimated_coverage_pct": 94.2,
  "risk_grid_preview": [
    { "lat": 45.04, "lng": 42.15, "risk": 0.12 }
  ]
}
```

`reliability_index` ∈ [0, 1] — средняя безопасность маршрута (IRM).  
`estimated_coverage_pct` — процент поля, который будет охвачен.  
`risk_grid_preview` — разреженная выборка сетки рисков (каждая 3-я точка) для визуализации на карте.

---

### POST /mission/{mission_id}/start

Загрузить маршруты на дроны через MAVLink и запустить миссию.

**Роль:** operator

**Body:**
```json
{
  "routes": [
    { "drone_id": 1, "route": [{"lat": 45.044, "lng": 42.148}] }
  ],
  "altitude_m": 30.0
}
```

`altitude_m` — высота полёта в метрах над землёй (AGL). По умолчанию 30 м.

**Response 200:**
```json
{
  "status": "started",
  "uploaded": [1, 2, 3, 4],
  "started": [1, 2, 3, 4]
}
```

`status`: `"started"` | `"partial"` | `"failed"`  
`uploaded` — дроны, которым удалось загрузить маршрут.  
`started` — дроны, которые вооружились и взлетели.

Если SITL недоступен — возвращает `"failed"`, бэкенд не падает.

---

## Динамическое перепланирование

### POST /mission/{mission_id}/simulate-loss

**Сценарий A**: перераспределить waypoints потерянного дрона.

**Роль:** operator  
**Query param:** `drone_id=1` (ID потерянного дрона)

**Body:**
```json
{
  "field_id": 1,
  "drone_ids": [2, 3, 4],
  "current_routes": [
    {
      "drone_id": 1,
      "route": [{"lat": 45.044, "lng": 42.148}, {"lat": 45.046, "lng": 42.151}]
    }
  ],
  "visited_counts": { "1": 3, "2": 5, "3": 2, "4": 7 }
}
```

`visited_counts` — сколько waypoints каждый дрон уже посетил на момент перепланирования.

**Response 200:**
```json
{
  "status": "replanned",
  "updated_routes": [
    { "drone_id": 2, "route": [...] },
    { "drone_id": 3, "route": [...] }
  ],
  "new_irm": 0.83
}
```

`status`: `"replanned"` | `"no_change"` | `"mission_failed"`

---

### POST /mission/{mission_id}/risk-zones

**Сценарий B**: перестроить маршруты при появлении новой зоны РЭБ в полёте.

**Роль:** operator

**Body:**
```json
{
  "field_id": 1,
  "drone_ids": [1, 2, 3, 4],
  "new_zone": {
    "geometry": {"type": "Polygon", "coordinates": [[[42.16, 45.04], ...]]},
    "severity": 0.9,
    "zone_type": "jammer"
  },
  "current_routes": [...],
  "visited_counts": {"1": 5, "2": 3, "3": 7, "4": 2}
}
```

**Response 200:**
```json
{
  "status": "replanned",
  "updated_routes": [...],
  "new_irm": 0.79
}
```

---

## WebSocket телеметрия

### WS /ws/telemetry

Симуляция телеметрии по маршрутным точкам (без реального MAVLink).

**Клиент отправляет сразу при открытии:**
```json
{
  "routes": [
    { "drone_id": 1, "route": [{"lat": 45.044, "lng": 42.148}, ...] }
  ],
  "irm": 0.87
}
```

**Сервер отправляет каждые 100 мс:**
```json
{
  "drone_id": 1,
  "lat": 45.0441,
  "lng": 42.1482,
  "step": 3,
  "total_steps": 47,
  "irm_update": 0.87
}
```

`irm_update` — только в первом фрейме, потом отсутствует.

---

### WS /ws/telemetry/{drone_id}

Реальная MAVLink телеметрия от SITL или физического дрона.

**Сервер отправляет каждые 200 мс:**
```json
{
  "drone_id": 1,
  "lat": 45.0448,
  "lng": 41.9734,
  "alt": 30.1,
  "battery": 87,
  "heading": 135,
  "status": "ACTIVE",
  "groundspeed": 12.3
}
```

`status`: `"ACTIVE"` | `"LANDED"` | `"LOST"`

При `status = "LOST"` соединение закрывается — фронтенд должен инициировать `/simulate-loss`.

---

## Матрица доступа

| Эндпоинт | Метод | Минимальная роль |
|---|---|---|
| `/auth/login` | POST | — |
| `/auth/register` | POST | — |
| `/mission/fields` | GET | viewer |
| `/mission/risk-zones` | GET | viewer |
| `/mission/fields` | POST | operator |
| `/mission/risk-zones` | POST | operator |
| `/mission/plan` | POST | operator |
| `/mission/{id}/start` | POST | operator |
| `/mission/{id}/simulate-loss` | POST | operator |
| `/mission/{id}/risk-zones` | POST | operator |
| `/ws/telemetry` | WS | — |
| `/ws/telemetry/{id}` | WS | — |

---

## Коды ошибок

| Код | Причина |
|---|---|
| 400 | Невалидный GeoJSON, нет дронов, нет маршрутов |
| 401 | Токен отсутствует или истёк |
| 403 | Роль viewer пытается выполнить operator-действие |
| 404 | Field/Drone не найден |
| 500 | БД недоступна, внутренняя ошибка маршрутизатора |
