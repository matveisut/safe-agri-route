# SITL — Отладка и выводы

Документирует проблемы, найденные при настройке ArduPilot SITL для SafeAgriRoute, и их решения.

---

## 1. Итоговая рабочая конфигурация

**Скрипт запуска:** `start_sitl_wsl.sh`

Ключевые настройки:
```bash
export SITL_RITW_TERMINAL="bash"   # вместо xterm — работает без X11
# --no-mavproxy: ArduCopter слушает напрямую на MAVLink-портах
python Tools/autotest/sim_vehicle.py \
    -v ArduCopter --instance N \
    --no-mavproxy --no-rebuild --speedup=1 \
    --custom-location="45.0448,41.9734,0,0"
```

**Порты:**
| Инстанс | Порт | Протокол |
|---|---|---|
| Дрон 0 | 5760 | tcp:127.0.0.1:5760 |
| Дрон 1 | 5770 | tcp:127.0.0.1:5770 |
| Дрон 2 | 5780 | tcp:127.0.0.1:5780 |
| Дрон 3 | 5790 | tcp:127.0.0.1:5790 |

**`.env` для Docker-бэкенда:**
```
SITL_HOSTS=tcp:host.docker.internal:5760,tcp:host.docker.internal:5770,tcp:host.docker.internal:5780,tcp:host.docker.internal:5790
```

**Виртуальное окружение:** `~/venv-ardupilot` (содержит mavproxy, pymavlink, sim_vehicle.py зависимости).

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

# 3. Проверить порты (через ~15 сек)
ss -tlnp | grep "576"

# 4. Проверить heartbeat
python3 -c "
import pymavlink.mavutil as m
conn = m.mavlink_connection('tcp:127.0.0.1:5760', retries=1)
hb = conn.recv_match(type='HEARTBEAT', blocking=True, timeout=5)
print('OK:', hb) if hb else print('FAIL')
"

# 5. Перезапустить бэкенд с новым .env
docker-compose up -d --no-deps backend
```
