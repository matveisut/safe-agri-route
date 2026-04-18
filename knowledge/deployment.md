# Развёртывание — SafeAgriRoute

Инструкция по запуску стека: локально, Docker, SITL (WSL2 и Docker).

---

## 1. Быстрый старт (без SITL)

```bash
# 1. Клонировать репозиторий
git clone <repo_url> && cd safe-agri-route

# 2. Запустить стек
docker-compose up -d

# 3. Заполнить БД тестовыми данными
docker exec safe_agri_route_backend python seed.py

# 4. Открыть
#   Frontend:  http://localhost:3000
#   Swagger:   http://localhost:8000/docs
```

Режим симуляции: телеметрия воспроизводится по waypoints через `/ws/telemetry` (без MAVLink).

---

## 2. Переменные окружения

| Переменная | Сервис | Описание | Дефолт |
|---|---|---|---|
| `DATABASE_URL` | backend | asyncpg DSN | `postgresql+asyncpg://safeagri:safeagripassword@db:5432/safeagriroute` |
| `SECRET_KEY` | backend | JWT signing key | `supersecretkey` (сменить в проде) |
| `SITL_HOSTS` | backend | Список TCP-адресов SITL | `""` (simulation mode) |
| `VITE_API_URL` | frontend | Base URL бэкенда | `http://localhost:8000` |
| `POSTGRES_USER` | db | Юзер PostgreSQL | `safeagri` |
| `POSTGRES_PASSWORD` | db | Пароль | `safeagripassword` |
| `POSTGRES_DB` | db | Имя БД | `safeagriroute` |

Формат `SITL_HOSTS`:
```
tcp:HOST:PORT,tcp:HOST:PORT,...
```

---

## 3. Режим SITL — WSL2

Запускает 4 экземпляра ArduPilot локально (без Docker), бэкенд достучится через `host.docker.internal`.

```bash
# Требуется ArduPilot в ~/ardupilot
bash start_sitl_wsl.sh
```

Скрипт:
- Запускает 4 ArduCopter на портах 14550 / 14560 / 14570 / 14580
- Логи: `/tmp/sitl_drone_0/sitl.log` … `/tmp/sitl_drone_3/sitl.log`
- PIDs: `/tmp/sitl_pids.txt`

После запуска скрипта настроить `SITL_HOSTS` в docker-compose.yml перед `docker-compose up`:

```yaml
# docker-compose.yml → backend → environment
- SITL_HOSTS=tcp:host.docker.internal:14550,tcp:host.docker.internal:14560,tcp:host.docker.internal:14570,tcp:host.docker.internal:14580
```

Либо через env-переменную:
```bash
SITL_HOSTS="tcp:host.docker.internal:14550,..." docker-compose up -d
```

**WSL2-нюанс:** `host.docker.internal` резолвится в IP хостовой машины WSL2 (Windows-хост). Docker Desktop автоматически прописывает этот хост. Если не резолвится — проверить `extra_hosts` в docker-compose.yml или `/etc/hosts` внутри контейнера.

---

## 4. Режим SITL — Docker Compose overlay

Запускает SITL внутри Docker-контейнеров (не нужна локальная установка ArduPilot).

```bash
docker-compose -f docker-compose.yml -f docker-compose.sitl.yml up -d
```

`docker-compose.sitl.yml` добавляет:
- Сервисы `sitl-1` … `sitl-4` (образ `Dockerfile.sitl`)
- Overrides backend: `SITL_HOSTS` по именам контейнеров (`tcp:sitl-1:14550,...`)
- `depends_on: service_healthy` — бэкенд стартует только после успешного healthcheck всех SITL

Healthcheck SITL-контейнера:
```yaml
test: ["CMD-SHELL", "nc -z localhost ${PORT}"]
interval: 30s
timeout: 10s
retries: 10
start_period: 120s
```

ArduPilot компилируется при первом `docker build` (~5–15 мин). Последующие запуски используют кэш.

---

## 5. Dockerfile.sitl

```
Ubuntu 22.04
  → git clone ArduPilot (stable)
  → install_prereqs.sh + Tools/environment_install
  → pip install MAVProxy (sim_vehicle.py зависимость)
  → netcat-openbsd (healthcheck)
  → USER ardupilot (non-root — SITL нестабилен под root)
  → CMD sim_vehicle.py -v ArduCopter --instance ${INSTANCE}
              --out=tcp:0.0.0.0:${PORT} --no-mavproxy --speedup=1
```

ENV переменные контейнера:

| Переменная | Значение (sitl-1) |
|---|---|
| `INSTANCE` | 0 |
| `PORT` | 14550 |
| `LOCATION` | 45.0448,41.9734,0,0 (Ставрополь) |

---

## 6. Сети Docker

Все сервисы подключены к сети `safagri_net` (bridge).

```
safagri_net
  ├── db                    (postgres, порт 5432 — только внутри сети)
  ├── backend               (0.0.0.0:8000 → 8000)
  ├── frontend              (0.0.0.0:3000 → 3000)
  ├── sitl-1..4             (14550/14560/14570/14580 — только внутри сети)
```

**Важно:** порт `5432` БД **не публикуется** на хост. Если нужен прямой доступ с хоста (DBeaver, psql), раскомментировать в `docker-compose.yml`:
```yaml
# ports:
#   - "5433:5432"
```

Причина: порт 5432 может быть занят другим PostgreSQL на хосте.

---

## 7. Диагностика

### Бэкенд недоступен (порт 8000)

```bash
docker-compose logs backend
# Обычная причина: БД ещё не готова → ждать healthcheck
docker inspect safe_agri_route_db | grep -A5 '"Health"'
```

### БД потеряла сеть

Симптом: `sqlalchemy.exc.OperationalError: could not translate host name "db"`.

```bash
docker network inspect safe-agri-route_safagri_net
# Если db отсутствует в списке:
docker network connect safe-agri-route_safagri_net safe_agri_route_db
```

### SITL не подключается

```bash
# Проверить, что порты открыты внутри сети
docker exec safe_agri_route_backend nc -zv sitl-1 14550
# Проверить лог SITL
docker logs safe_agri_route_sitl_1
```

### Пересоздание БД

```bash
docker-compose down -v          # удалить volumes (данные!)
docker-compose up -d
docker exec safe_agri_route_backend python seed.py
```

---

## 8. Локальная разработка (без Docker)

```bash
# Backend
cd backend
python -m venv venv && source venv/activate
pip install -r requirements.txt
DATABASE_URL=postgresql+asyncpg://... uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev
```

Потребуется локальный PostgreSQL с расширением PostGIS.

---

## 9. Производительность и ограничения MVP

| Параметр | Значение |
|---|---|
| Макс. дронов | 10 (OR-Tools TSP масштаб) |
| Макс. точек поля | ~500 (grid_step=0.0002°) |
| Перепланирование 4 дрона × 50 wp | < 500 мс |
| SITL warm-up (EKF2 init) | ~10 сек после heartbeat |
| Docker SITL first build | 5–15 мин |
