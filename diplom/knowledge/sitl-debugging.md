# SITL — Отладка и выводы

> Актуализация 22.04.2026: для mission UI используется unified stream `/ws/telemetry/mission`; single-drone `/ws/telemetry/{drone_id}` сохранён как legacy/debug канал; в runtime добавлена packet-loss simulation для проверки PLR.

Документирует проблемы, найденные при настройке ArduPilot SITL для SafeAgriRoute, и их решения.

---

## 1. Итоговая рабочая конфигурация

**Скрипт запуска:** `start_sitl_wsl.sh`

Ключевые настройки:
```bash
export SITL_RITW_TERMINAL="bash"   # вместо xterm — работает без X11
# sim_vehicle.py в screen-сессии: управляет arducopter + mavproxy вместе
# --out tcpin: MAVProxy слушает на отдельном порту для бэкенда
screen -S "sitl_N" -d -m bash -c "
    source ~/venv-ardupilot/bin/activate
    export SITL_RITW_TERMINAL=bash
    cd ~/ardupilot
    python Tools/autotest/sim_vehicle.py \
        -v ArduCopter --instance N \
        --custom-location='45.0448,41.9734,0,0' \
        --out 'tcpin:0.0.0.0:PORT' \
        --no-rebuild --speedup=1
"
```

**Порты MAVProxy tcpin (для бэкенда):**
| Инстанс | Порт | Протокол |
|---|---|---|
| Дрон 0 | 14550 | tcp:0.0.0.0:14550 |
| Дрон 1 | 14560 | tcp:0.0.0.0:14560 |
| Дрон 2 | 14570 | tcp:0.0.0.0:14570 |
| Дрон 3 | 14580 | tcp:0.0.0.0:14580 |

**`.env` для Docker-бэкенда:**
```
SITL_HOSTS=tcp:host.docker.internal:14550,tcp:host.docker.internal:14560,tcp:host.docker.internal:14570,tcp:host.docker.internal:14580
```

**Виртуальное окружение:** `~/venv-ardupilot` (как минимум: `pymavlink`, `MAVProxy`, **`pexpect`** — последний нужен `Tools/autotest/sim_vehicle.py` → `pysim`; без него `ModuleNotFoundError: pexpect`). Полный набор тянет `Tools/environment_install/install-prereqs-ubuntu.sh` из клона ArduPilot.

**Просмотр инстанса:** `screen -r sitl_0` (выход: Ctrl+A D)

---

## 2. Хронология ошибок и выводы

### 2.1 `--out=tcp:... --no-mavproxy` не открывает порты

**Проблема:** исходный скрипт использовал:
```bash
sim_vehicle.py --out=tcp:0.0.0.0:14550 --no-mavproxy
```
Порты 14550-14580 **не открывались**.

**Причина:** `--out` — флаг для MAVProxy. Без MAVProxy (`--no-mavproxy`) он игнорируется. ArduCopter сам не слушает на кастомных портах при этом сочетании флагов.

**Вывод:** Если нужен кастомный порт — либо использовать MAVProxy без `--no-mavproxy`, либо передавать порт через `--serial0 tcp:PORT` (напрямую бинарнику ArduCopter, но без физического симулятора это не работает).

---

### 2.2 `host.docker.internal` не достигает SITL на WSL2

**Проблема:** `.env` содержал `tcp:host.docker.internal:14550`, но SITL слушал на `127.0.0.1`.

**Причина:** `host.docker.internal` в Docker Desktop на Windows резолвится в IP Windows-хоста, а не в WSL2 loopback. SITL, запущенный в WSL2 на `127.0.0.1`, недоступен через этот путь.

**Решение:** использовать порты, доступные через Docker-сеть:
- Если SITL на WSL2 слушает на `0.0.0.0` → `host.docker.internal:PORT` работает
- Если слушает на `127.0.0.1` → нужен IP eth0 WSL2 (ненадёжно) или bind на `0.0.0.0`

**ArduCopter с `--no-mavproxy` по умолчанию слушает на `0.0.0.0`** → `host.docker.internal` работает.

---

### 2.3 xterm не стартует SITL из bash без DISPLAY/tty

**Проблема:** `sim_vehicle.py` запускает arducopter через `run_in_terminal_window.sh`, который использует xterm. Из скриптов без TTY (Claude bash tool, фоновые процессы) xterm не стартовал.

**Симптом:** `MAVProxy exited` — MAVProxy не мог подключиться к arducopter на 5760 (arducopter не запустился).

**Решение:** `export SITL_RITW_TERMINAL="bash"` — `run_in_terminal_window.sh` использует эту переменную и запускает arducopter напрямую через `bash /tmp/scriptfile &` вместо xterm.

**Вывод:** В production-терминале с `DISPLAY=:0` xterm работает. Для автоматических/headless запусков — `SITL_RITW_TERMINAL="bash"`.

---

### 2.4 "Waiting for internal clock bits" — зависание SITL

**Проблема:** При использовании `sim_vehicle.py` с MAVProxy, arducopter стартовал, MAVProxy подключался, но arducopter зависал с сообщением:
```
Waiting for internal clock bits to be set (current=0x00)
```
MAVProxy через некоторое время выходил, sim_vehicle.py перезапускался и всё повторялось.

**Причина:** ArduCopter SITL ждёт SITL-протокольные данные (RC input) от MAVProxy через UDP 5501. Если MAVProxy не отправляет данные вовремя (timing issue, несовместимость версий), физический симулятор не тикает.

**Решение:** Отказаться от MAVProxy — использовать `--no-mavproxy`. ArduCopter напрямую слушает на своём default MAVLink-порту (5760 для инстанса 0, 5770 для инстанса 1, и т.д.).

**Подтверждение:** Прямое подключение pymavlink к порту 5760 **работает** и возвращает HEARTBEAT.

---

### 2.5 Параллельная сборка ArduCopter ломает артефакты

**Проблема:** Запуск 4 инстансов `sim_vehicle.py` одновременно приводил к ошибке сборки:
```
Build failed -> missing file: '.../AP_ICEngine.cpp.0.o'
```

**Причина:** Все 4 процесса запускали `waf build` параллельно, конкурируя за артефакты компиляции.

**Решение:** Флаг `--no-rebuild` — пропускает сборку. Бинарник собирается один раз заранее (первый запуск или вручную).

---

### 2.6 ArduCopter binary без физического симулятора падает через ~15 сек

**Проблема:** Запуск бинарника напрямую (`arducopter --model + ... --serial0 tcp:14550`) без `sim_vehicle.py` — процесс зависал и падал.

**Причина:** `--model +` (SIM_Multicopter) — встроенная физика, но она требует RC input по UDP 5501. Без sim_vehicle.py этот поток не поступает, физический движок не тикает, процесс зависает.

**Вывод:** `sim_vehicle.py` обязателен — он организует связку ArduCopter + физический симулятор.

---

## 3. Ограничение: одно соединение на порт

ArduCopter SITL принимает **только одно активное TCP-соединение** на порту 5760 одновременно (или обрабатывает их последовательно с буфером).

**Следствие для тестов:** Integration tests (`test_mavlink_service.py::TestSITLIntegration`) не могут запускаться пока Docker-бэкенд подключён к SITL. Функция `_sitl_available()` проверяет реальный heartbeat через pymavlink — тест пропускается если соединение занято.

**Workflow для integration tests:**
1. Остановить бэкенд: `docker-compose stop backend`
2. Запустить тесты: `pytest -m integration`
3. Перезапустить бэкенд: `docker-compose start backend`

---

## 4. Структура Dockerfile.sitl (Docker-SITL)

Альтернатива WSL2 SITL. Запускает ArduCopter внутри Docker:
```bash
docker-compose -f docker-compose.yml -f docker-compose.sitl.yml up -d
```

Отличия от WSL2-режима:
- Не нужен `DISPLAY` / xterm
- `SITL_HOSTS` использует имена контейнеров: `tcp:sitl-1:14550,...`
- Первый `docker build` занимает 5-15 мин (компиляция ArduPilot)
- Порты 14550-14580 (через MAVProxy внутри контейнера)

---

## 5. Быстрый чеклист запуска SITL

```bash
# 1. Активировать venv
source ~/venv-ardupilot/bin/activate

# 2. Запустить SITL
bash start_sitl_wsl.sh

# 3. Проверить порты MAVProxy tcpin (~15-30 сек после запуска)
ss -tlnp | grep "1455"
# Должны быть: 14550, 14560, 14570, 14580

# 4. Проверить heartbeat через все 4 порта
python3 -c "
import pymavlink.mavutil as m
for port in [14550, 14560, 14570, 14580]:
    conn = m.mavlink_connection(f'tcp:127.0.0.1:{port}', retries=1)
    hb = conn.recv_match(type='HEARTBEAT', blocking=True, timeout=5)
    print(f'Port {port}:', 'OK sysid=' + str(hb.get_srcSystem()) if hb else 'FAIL')
    conn.close()
"

# 5. Перезапустить бэкенд
docker-compose up -d --no-deps backend

# 6. Проверить что бэкенд подключился к SITL (не simulation mode)
docker-compose logs backend | grep -i "sitl\|mavlink\|connect\|simulation"
```

---

## 6. Хронология сессии: итоговый путь к рабочей конфигурации

Краткая выжимка всего что было отлажено:

| Подход | Результат | Почему не работает |
|---|---|---|
| `arducopter --serial0 tcp:PORT --no-mavproxy` | Падает ~15 сек | Нет RC input → physics freeze → "Waiting for internal clock bits" |
| `sim_vehicle.py --out=tcp:PORT --no-mavproxy` | Порты не открываются | `--out` — флаг MAVProxy; без MAVProxy игнорируется |
| `sim_vehicle.py` без screen, `mavproxy.py` в background bash | MAVProxy crash loop | Нет PTY → multiprocessing pipe error при перезапуске arducopter |
| `sim_vehicle.py` в `screen -d -m` с `--out tcpin:PORT` | **Работает** | screen даёт PTY; MAVProxy слушает входящие TCP как сервер |

**Ключевой инсайт:** MAVProxy — не просто прокси, он поставляет RC input для физического симулятора ArduCopter (UDP 5501). Без MAVProxy физика не тикает. Без PTY MAVProxy падает при перезапусках arducopter.

---

## 7. Симптомы и диагностика

| Симптом в логах | Причина | Решение |
|---|---|---|
| `ModuleNotFoundError: No module named 'pexpect'` | В venv нет зависимости autotest (`pysim` / `sim_vehicle.py`) | `source ~/venv-ardupilot/bin/activate && pip install pexpect` |
| `Waiting for internal clock bits (current=0x00)` | MAVProxy не отправляет RC input на UDP 5501 | Убедиться что запускается с MAVProxy (без `--no-mavproxy`) |
| `MAVProxy exited` сразу после старта | нет PTY или arducopter не стартовал | Запускать в `screen -d -m` |
| `_recv_bytes` / pipe error в MAVProxy | нет PTY при перезапуске arducopter | Запускать в `screen -d -m` |
| `EOF on TCP socket` (arducopter) | запуск arducopter напрямую без sim_vehicle.py | Всегда через `sim_vehicle.py` |
| Порты 14550-14580 не видны (`ss -tlnp`) | MAVProxy ещё не запустил tcpin listener | Подождать 20-30 сек; проверить лог `/tmp/sitl_drone_N.log` |
| Бэкенд пишет `simulation mode` | `SITL_HOSTS` пуст или порты не открыты | Проверить `.env`, запустить SITL перед бэкендом |
| Integration-тесты не получают heartbeat | Бэкенд занял соединение | `docker-compose stop backend` перед `pytest -m integration` |

---

## 8. Связь с fusion (§10)

Дополнение по состоянию 22.04.2026:
- в `jam_prob` добавлен признак `PLR` (Packet Loss Rate);
- packet-loss simulation позволяет воспроизводимо поднимать `packet_loss_rate` для live/sim потоков;
- ожидаемая цепочка в логах: рост `packet_loss_rate` -> рост `jam_prob` -> `SUSPECT/CONFIRMED` -> controlled replan (по порогам и rate-limit).

При работающем SITL основной канал фронтенда — **`WS /ws/telemetry/mission`** (в кадрах `fusion_by_drone`, `dynamic_zones`, `irm_update`).  
Single-drone **`WS /ws/telemetry/{drone_id}`** используется как legacy/debug. Для авто-replan миссии предварительно вызывается **`POST /api/v1/mission/{id}/fusion-context`**. Подробности — [`architecture.md`](architecture.md), [`api-reference.md`](api-reference.md).
