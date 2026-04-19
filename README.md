# SafeAgriRoute

**Отказоустойчивая система маршрутизации сельскохозяйственных БПЛА**  
*MVP SaaS-платформа для планирования безопасных облётов полей в условиях радиоэлектронного противодействия (GPS-spoofing, Jamming) с динамическим перепланированием миссий в реальном времени.*

![Status](https://img.shields.io/badge/Status-Beta-success) ![Stack](https://img.shields.io/badge/Stack-React%20%7C%20FastAPI%20%7C%20PostGIS-blue) ![MAVLink](https://img.shields.io/badge/Protocol-MAVLink%202.0-orange) ![Auth](https://img.shields.io/badge/Auth-JWT-green)

---

## О проекте

SafeAgriRoute решает проблему безопасного использования агродронов в зонах с возможными кибер-рисками. Обычные алгоритмы строят прямые пути, что может привести к потере дрона (в зоне глушения) или перехвату управления (в зоне спуфинга).

Система реализует полный цикл управления роем:

- **Планирование**: Risk-Weighted Voronoi делит поле на зоны с учётом карты угроз. OR-Tools решает CVRP внутри каждой зоны, обходя РЭБ-препятствия через систему штрафов на рёбрах графа.
- **Исполнение**: MAVLink-слой загружает маршруты на реальные дроны (ArduPilot SITL / железо), армирует и запускает миссию.
- **Мониторинг**: WebSocket стримит телеметрию 5 раз в секунду (GPS, батарея, курс, скорость).
- **Реагирование**: Два сценария динамического перепланирования — потеря дрона и появление новой зоны РЭБ — без остановки миссии.

---

## Технологический стек

| Слой | Технологии |
|---|---|
| **Frontend** | React 18, Vite, TypeScript, TailwindCSS, Zustand, React-Leaflet |
| **Backend** | Python 3.12, FastAPI, SQLAlchemy (Async), GeoAlchemy2 |
| **Алгоритмы** | Shapely, NetworkX, Google OR-Tools, NumPy |
| **MAVLink** | pymavlink 2.4.41 (ArduPilot SITL / реальные дроны) |
| **Auth** | JWT (python-jose), bcrypt |
| **БД** | PostgreSQL 15 + PostGIS 3.3 |
| **Инфраструктура** | Docker, Docker Compose |

---

## Требования

- **Windows 10/11** с WSL2 (Ubuntu 22.04) — для запуска SITL
- **Docker Desktop** с WSL2-бэкендом (Settings → General → "Use the WSL 2 based engine" = ON)
- **Node.js 20+** (если запускаете фронтенд вне Docker)

> **ВАЖНО для Windows**: без WSL2-бэкенда в Docker Desktop контейнеры с ArduPilot работают нестабильно.

---

## Быстрый старт (без SITL — только демо UI)

```bash
# Поднять полный стек: PostgreSQL + PostGIS + Backend + Frontend
docker-compose up --build

# В отдельном терминале — инициализировать БД демо-данными
docker-compose exec backend python seed.py
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

Логин: `operator@safegriroute.com` / `operator123`

В этом режиме телеметрия работает как симуляция по маршрутным точкам (без реального MAVLink).

---

## Запуск с реальным MAVLink SITL (рекомендуется через WSL2)

### Шаг 1: Установить ArduPilot в WSL2 (однократно)

```bash
# В терминале WSL2 Ubuntu:
git clone https://github.com/ArduPilot/ardupilot.git ~/ardupilot
cd ~/ardupilot
git submodule update --init --recursive
Tools/environment_install/install-prereqs-ubuntu.sh -y
source ~/.bashrc
```

### Шаг 2: Подготовить venv и запустить 4 SITL инстанса в WSL2

```bash
# Однократно: создать venv с pymavlink/MAVProxy
python3 -m venv ~/venv-ardupilot
source ~/venv-ardupilot/bin/activate
pip install pymavlink MAVProxy

# Запустить SITL (4 инстанса в screen-сессиях):
bash start_sitl_wsl.sh

# Подождать ~30 сек пока ArduCopter инициализируется, затем проверить порты:
ss -tlnp | grep "1455"
# Должны быть видны: 14550, 14560, 14570, 14580
```

> **Примечание:** каждый инстанс запускается через `sim_vehicle.py` в отдельной `screen`-сессии.
> Просмотр: `screen -r sitl_0` (выход: Ctrl+A D).
> Остановка: `screen -ls | grep sitl | awk '{print $1}' | xargs -I{} screen -S {} -X quit`

### Шаг 3: Запустить основной стек

Файл `.env` в корне проекта уже настроен:

```env
SITL_HOSTS=tcp:host.docker.internal:14550,tcp:host.docker.internal:14560,tcp:host.docker.internal:14570,tcp:host.docker.internal:14580
```

```bash
# PowerShell / bash:
docker-compose up --build
```

Проверить подключение к SITL:

```bash
docker-compose logs backend | grep -i "sitl\|mavlink\|connect"
```

---

## Запуск SITL через Docker (альтернатива)

> Требует успешной сборки `Dockerfile.sitl` (~15 мин при первом запуске).

```bash
docker-compose -f docker-compose.yml -f docker-compose.sitl.yml up --build
```

Бэкенд автоматически дождётся healthcheck всех 4 SITL-контейнеров перед стартом.

---

## Сценарии демо

### Сценарий A: Базовое планирование маршрута

1. Откройте http://localhost:3000
2. Авторизуйтесь: `operator@safegriroute.com` / `operator123`
3. В блоке **Target Field** выберите "Stavropol Wheat Field"
4. В блоке **Assign Drones** отметьте дроны 1–4
5. Нажмите **Generate Neural Route**
   - На карте появятся цветные маршруты, огибающие красные зоны РЭБ
   - В панели отобразится `reliability_index` и `estimated_coverage_pct`
6. Нажмите **Start Telemetry Sim**
   - Маркеры дронов начнут двигаться по карте в реальном времени
   - Если SITL запущен — координаты реальные из ArduPilot

### Сценарий B: Динамическое перепланирование (потеря дрона)

1. Выполните шаги 1–6 из Сценария A
2. После начала движения дронов нажмите **Simulate Drone Loss** для дрона №1
3. Наблюдайте: бэкенд перераспределит незакрытые waypoints между оставшимися дронами
4. На карте появятся обновлённые маршруты без остановки миссии

### Сценарий C: Динамическое перепланирование (новая зона РЭБ)

1. Выполните шаги 1–6 из Сценария A
2. В процессе полёта нажмите **Add Risk Zone** и нарисуйте полигон на карте
3. Бэкенд пересчитает маршруты для дронов, чьи пути пересекают новую зону
4. Обновлённые маршруты отобразятся на карте без прерывания телеметрии

---

## Аутентификация

Все API-эндпоинты защищены JWT Bearer-токенами.

### Тестовые пользователи (после `seed.py`)

| Email | Пароль | Роль | Права |
|---|---|---|---|
| `operator@safegriroute.com` | `operator123` | operator | Полный доступ |
| `viewer@safegriroute.com` | `viewer123` | viewer | Только чтение |

### Получить токен

```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=operator@safegriroute.com&password=operator123"
# {"access_token": "eyJ...", "token_type": "bearer"}
```

---

## API Reference

| Метод | Путь | Роль | Описание |
|---|---|---|---|
| POST | `/auth/login` | — | Получить JWT токен |
| POST | `/auth/register` | — | Создать пользователя |
| GET | `/api/v1/mission/fields` | viewer+ | Список полей |
| GET | `/api/v1/mission/risk-zones` | viewer+ | Зоны РЭБ |
| POST | `/api/v1/mission/plan` | operator | Спланировать маршруты |
| POST | `/api/v1/missions/{id}/start` | operator | Загрузить и запустить на дронах |
| POST | `/api/v1/missions/{id}/simulate-loss` | operator | Симулировать потерю дрона |
| POST | `/api/v1/missions/{id}/risk-zones` | operator | Добавить зону РЭБ в полёте |
| WS | `/ws/telemetry` | — | Симуляция телеметрии по маршруту |
| WS | `/ws/telemetry/{drone_id}` | — | Реальная MAVLink телеметрия |

---

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://safeagri:safeagripassword@db:5432/safeagriroute` | Строка подключения к PostgreSQL |
| `SITL_HOSTS` | _(пусто)_ | Адреса SITL через запятую. Пусто = режим симуляции |
| `JWT_SECRET` | `safegriroute-super-secret-256-bit-key-change-in-prod` | Секрет для подписи JWT |
| `JWT_ALGORITHM` | `HS256` | Алгоритм JWT |
| `JWT_EXPIRE_HOURS` | `8` | Срок жизни токена (часов) |
| `VITE_API_URL` | `http://localhost:8000` | URL бэкенда для фронтенда |

---

## Частые проблемы на Windows

| Проблема | Решение |
|---|---|
| Порты SITL не видны из Docker | Используйте `host.docker.internal` в `SITL_HOSTS`, порты 14550-14580 |
| WSL2 и Docker конфликтуют | Перезапустите Docker Desktop после запуска WSL2 терминала |
| `pymavlink` не устанавливается | Запускайте бэкенд в WSL2 напрямую, а не в Docker |
| SITL не запускается в Docker | Используйте `start_sitl_wsl.sh` вместо `docker-compose.sitl.yml` |
| Контейнер frontend не видит backend | Проверьте что оба сервиса в сети `safagri_net` (см. docker-compose.yml) |

---

## Архитектура

Подробное описание всех алгоритмов, слоёв и модулей — в [ARCHITECTURE.md](./ARCHITECTURE.md).

```
Frontend (React) ──► POST /mission/plan ──► RoutingService (Voronoi + OR-Tools)
                 ◄── WS /ws/telemetry   ◄── MAVLinkService (pymavlink)
                                                    │
                                         ArduPilot SITL (4 инстанса)
                                         tcp:*:14550 / 14560 / 14570 / 14580
```
