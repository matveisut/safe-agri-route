# Модели данных — SafeAgriRoute

Описание PostgreSQL-схемы, SQLAlchemy-моделей и Pydantic-схем.

---

## 1. Схема базы данных (PostgreSQL 15 + PostGIS 3.3)

### Таблица `users`

```sql
CREATE TABLE users (
    id       SERIAL PRIMARY KEY,
    email    VARCHAR NOT NULL UNIQUE,
    hashed_password VARCHAR NOT NULL,
    role     VARCHAR,         -- 'operator' | 'viewer'
    is_active BOOLEAN DEFAULT TRUE
);
```

Пароли хешируются через `bcrypt.hashpw` (прямой вызов bcrypt, без passlib — несовместима с bcrypt 5.x).

### Таблица `drones`

```sql
CREATE TABLE drones (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR NOT NULL UNIQUE,
    battery_capacity INTEGER NOT NULL,  -- мА·ч (условные единицы для ранжирования)
    max_speed        FLOAT NOT NULL,    -- м/с
    status           VARCHAR            -- 'idle' | 'flying' | 'lost'
);
```

`battery_capacity × max_speed` используется как **proxy суммарного ресурса** при распределении кластеров. Абсолютные значения в физических единицах не критичны — важно соотношение между дронами.

### Таблица `fields`

```sql
CREATE TABLE fields (
    id       SERIAL PRIMARY KEY,
    name     VARCHAR NOT NULL,
    geometry geometry(POLYGON, 4326) NOT NULL  -- PostGIS, WGS 84
);

CREATE INDEX idx_fields_geometry ON fields USING GIST (geometry);
```

Геометрия хранится как PostGIS POLYGON в EPSG:4326. При чтении возвращается через `ST_AsGeoJSON()` как строка GeoJSON.

### Таблица `risk_zones`

```sql
CREATE TABLE risk_zones (
    id              SERIAL PRIMARY KEY,
    type            VARCHAR NOT NULL,   -- 'Jamming' | 'Spoofing' | 'jammer' | 'restricted'
    geometry        geometry(POLYGON, 4326) NOT NULL,
    severity_weight FLOAT NOT NULL      -- 0.1..5.0, влияет на penalty и IRM
);

CREATE INDEX idx_risk_zones_geometry ON risk_zones USING GIST (geometry);
```

`severity_weight` интерпретируется по-разному в зависимости от контекста:
- **risk_map.py**: умножается на линейное затухание для jammer-зон
- **routing_service.py**: умножается на 500 → штраф ребра в графе
- **IRM**: влияет на `risk[wp]` через карту рисков

---

## 2. SQLAlchemy-модели (`backend/app/models/`)

```python
# Все модели наследуют Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    hashed_password: Mapped[str]
    role: Mapped[str] = mapped_column(default="viewer")
    is_active: Mapped[bool] = mapped_column(default=True)

class Drone(Base):
    __tablename__ = "drones"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    battery_capacity: Mapped[int]
    max_speed: Mapped[float]
    status: Mapped[str] = mapped_column(default="idle")

class Field(Base):
    __tablename__ = "fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    geometry = Column(Geometry("POLYGON", srid=4326))  # GeoAlchemy2

class RiskZone(Base):
    __tablename__ = "risk_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    geometry = Column(Geometry("POLYGON", srid=4326))
    severity_weight: Mapped[float]
```

---

## 3. Repository-слой (`backend/app/repositories/`)

`BaseRepository[ModelType, CreateSchemaType, UpdateSchemaType]` — дженерик поверх `AsyncSession`:

```python
class BaseRepository:
    async def get(self, db, id) -> Model | None
    async def get_multi(self, db, skip=0, limit=100) -> List[Model]
    async def create(self, db, obj_in: dict) -> Model
    async def update(self, db, id, obj_in: dict) -> Model | None
    async def delete(self, db, id) -> bool
```

Специализированные репозитории добавляют доменные методы:
- `UserRepository.get_by_email(db, email)` — используется в `/auth/login`

---

## 4. Pydantic-схемы (`backend/app/schemas/mission.py`)

### Базовые типы геоданных

```python
class RoutePoint(BaseModel):
    lat: float   # широта, WGS 84
    lng: float   # долгота, WGS 84

class DroneRoute(BaseModel):
    drone_id: int
    route: List[RoutePoint]

class RiskGridPoint(BaseModel):
    lat: float
    lng: float
    risk: float  # [0.0, 1.0]
```

### Запросы

```python
class PlanMissionRequest(BaseModel):
    field_id: int
    drone_ids: List[int]

class StartMissionRequest(BaseModel):
    routes: List[DroneRoute]
    altitude_m: float = 30.0

class SimulateLossRequest(BaseModel):
    field_id: int
    drone_ids: List[int]
    current_routes: List[DroneRoute]
    visited_counts: Dict[int, int]  # drone_id → кол-во посещённых waypoints

class AddRiskZoneRequest(BaseModel):
    field_id: int
    drone_ids: List[int]
    new_zone: Dict[str, Any]        # {geometry, severity, zone_type}
    current_routes: List[DroneRoute]
    visited_counts: Dict[int, int]

class CreateFieldRequest(BaseModel):
    name: str
    geojson: str  # строка GeoJSON Polygon

class CreateRiskZoneRequest(BaseModel):
    zone_type: str       # "jammer" | "restricted"
    severity_weight: float
    geojson: str
```

### Ответы

```python
class PlanMissionResponse(BaseModel):
    routes: List[DroneRoute]
    reliability_index: float        # IRM ∈ [0, 1]
    estimated_coverage_pct: float   # % поля, охваченного маршрутами
    risk_grid_preview: List[RiskGridPoint] = []

class StartMissionResponse(BaseModel):
    status: str             # "started" | "partial" | "failed"
    uploaded: List[int]     # drone_ids с успешной загрузкой
    started: List[int]      # drone_ids, которые взлетели

class ReplanResponse(BaseModel):
    status: str             # "replanned" | "no_change" | "mission_failed"
    updated_routes: List[DroneRoute]
    new_irm: float
```

---

## 5. Seed-данные (`backend/seed.py`)

После `python seed.py` в БД создаются:

**Пользователи:**
| Email | Пароль | Роль |
|---|---|---|
| operator@safegriroute.com | operator123 | operator |
| viewer@safegriroute.com | viewer123 | viewer |

**Дроны:**
| Имя | Battery | Speed |
|---|---|---|
| AgriFly-1 | 5000 | 15.0 м/с |
| AgriFly-2 | 7500 | 12.5 м/с |
| AgriFly-3 | 10000 | 10.0 м/с |

**Поле:** Stavropol Wheat Field — нерегулярный полигон в окрестностях Ставрополя (~45.03–45.06°N, 42.14–42.19°E).

**Зоны РЭБ:**
| Тип | Severity | Расположение |
|---|---|---|
| Jamming | 2.0 | Западная часть поля |
| Spoofing | 3.5 | Восточная часть поля |

---

## 6. Передача геометрии по API

```
PostGIS (WKB) → ST_AsGeoJSON() → JSON строка → Frontend parse → Leaflet [lat, lng]
```

Swap координат на фронтенде (GeoJSON `[lng, lat]` → Leaflet `[lat, lng]`):

```typescript
const coords = JSON.parse(geojson).coordinates[0];
const leafletCoords = coords.map(([lng, lat]) => [lat, lng]);
```

---

## 7. Состояние fusion (§10) — не в БД

Скользящие буферы телеметрии, последние значения fusion и контекст авто-replan хранятся **в памяти процесса** (`telemetry_features`, `mission_fusion_runtime`). В PostgreSQL **не** дублируются; для демо клиент передаёт маршруты через **`POST /mission/{id}/fusion-context`** при необходимости.
