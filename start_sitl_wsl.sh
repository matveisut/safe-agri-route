#!/bin/bash
# start_sitl_wsl.sh
# Запуск 4 SITL инстансов ArduCopter для SafeAgriRoute в WSL2.
#
# Каждый инстанс = sim_vehicle.py в отдельной screen-сессии.
# sim_vehicle.py управляет arducopter + mavproxy как единым процессом.
# screen даёт MAVProxy настоящий PTY — без него mavproxy падает при кражах arducopter.
#
# Порты MAVProxy tcpin (для бэкенда):
#   Дрон 0: tcp:0.0.0.0:14550
#   Дрон 1: tcp:0.0.0.0:14560
#   Дрон 2: tcp:0.0.0.0:14570
#   Дрон 3: tcp:0.0.0.0:14580
#
# Для Docker-бэкенда (.env):
#   SITL_HOSTS=tcp:host.docker.internal:14550,tcp:host.docker.internal:14560,tcp:host.docker.internal:14570,tcp:host.docker.internal:14580
#
# Просмотр инстанса: screen -r sitl_0  (выход: Ctrl+A D)
# Остановка: bash start_sitl_wsl.sh stop
#   или: screen -ls | grep sitl | awk '{print $1}' | xargs -I{} screen -S {} -X quit

set -euo pipefail

LOCATION="45.0448,41.9734,0,0"
ARDUPILOT_DIR="${HOME}/ardupilot"
VENV="${HOME}/venv-ardupilot"
PORTS=(14550 14560 14570 14580)

free_tcp_port() {
    local port=$1
    if command -v fuser >/dev/null 2>&1; then
        if fuser "${port}/tcp" >/dev/null 2>&1; then
            fuser -k "${port}/tcp" >/dev/null 2>&1 || true
            echo "  → освобождён занятый порт ${port} (fuser -k)"
        fi
    elif command -v lsof >/dev/null 2>&1; then
        local pids
        pids=$(lsof -t -i "tcp:${port}" 2>/dev/null || true)
        if [[ -n "${pids}" ]]; then
            kill -9 ${pids} 2>/dev/null || true
            echo "  → освобождён занятый порт ${port} (lsof/kill)"
        fi
    fi
}

stop_sitl_sessions() {
    for i in 0 1 2 3; do
        screen -S "sitl_${i}" -X quit 2>/dev/null || true
    done
    sleep 1
    for p in "${PORTS[@]}"; do
        free_tcp_port "${p}"
    done
    sleep 1
}

port_is_listening() {
    local port=$1
    ss -tlnp 2>/dev/null | grep -qE ":${port}([^0-9]|$)"
}

if [[ "${1:-}" == "stop" ]]; then
    echo "Остановка screen sitl_0…3 и освобождение портов ${PORTS[*]} …"
    stop_sitl_sessions
    echo "Готово."
    exit 0
fi

if [ ! -d "${ARDUPILOT_DIR}" ]; then
    echo "ERROR: ArduPilot не найден в ${ARDUPILOT_DIR}"
    exit 1
fi

if [ ! -f "${VENV}/bin/activate" ]; then
    echo "ERROR: venv не найден: ${VENV}/bin/activate"
    exit 1
fi

# sim_vehicle.py тянет pysim → pexpect; без него screen сессия падает сразу (см. README / sitl-debugging.md).
if ! "${VENV}/bin/python" -c "import pexpect" 2>/dev/null; then
    echo "ERROR: в venv ${VENV} нет пакета pexpect (нужен для Tools/autotest/sim_vehicle.py)."
    echo "  source ${VENV}/bin/activate && pip install pexpect"
    echo "  (если ставили только pymavlink/MAVProxy по README — добавьте pexpect; install-prereqs-ubuntu.sh из ArduPilot тянет зависимости шире)"
    exit 1
fi

echo "=========================================="
echo "  SafeAgriRoute — Запуск 4 SITL инстансов"
echo "  Локация: ${LOCATION}"
echo "=========================================="

stop_sitl_sessions

for i in 0 1 2 3; do
    GCS_PORT=$((14550 + i * 10))
    LOG="/tmp/sitl_drone_${i}.log"

    echo "→ Инстанс ${i} → MAVProxy tcpin tcp:0.0.0.0:${GCS_PORT}..."

    screen -S "sitl_${i}" -d -m bash -c "
        source '${VENV}/bin/activate'
        export SITL_RITW_TERMINAL=bash
        cd '${ARDUPILOT_DIR}'
        python Tools/autotest/sim_vehicle.py \
            -v ArduCopter \
            --instance ${i} \
            --custom-location='${LOCATION}' \
            --out 'tcpin:0.0.0.0:${GCS_PORT}' \
            --no-rebuild \
            --speedup=1 \
            2>&1 | tee '${LOG}'
    "

    echo "  screen: sitl_${i} | Лог: ${LOG} | просмотр: screen -r sitl_${i}"

    sleep 1
    if ! screen -ls 2>/dev/null | grep -qE "[0-9]+\.sitl_${i}[[:space:]]"; then
        echo ""
        echo "ERROR: сессия screen sitl_${i} сразу исчезла — sim_vehicle/MAVProxy не держит PTY."
        echo "Частые причины: падение sim_vehicle.py, несовместимость ArduPilot/venv, нехватка RAM."
        echo "Последние строки ${LOG}:"
        tail -40 "${LOG}" 2>/dev/null || echo "(лог пуст или недоступен)"
        echo ""
        echo "Полная остановка:  bash $0 stop"
        exit 1
    fi

    echo -n "  Ожидание порта ${GCS_PORT}"
    READY=0
    for attempt in $(seq 1 45); do
        if port_is_listening "${GCS_PORT}"; then
            echo " — готов."
            READY=1
            break
        fi
        echo -n "."
        sleep 2
    done
    if [[ "${READY}" -ne 1 ]]; then
        echo ""
        echo "ERROR: порт ${GCS_PORT} не открылся за ~90 с. Возможен зависший SITL или конфликт порта."
        echo "Проверка: ss -tlnp | grep ${GCS_PORT}"
        echo "Последние строки ${LOG}:"
        tail -50 "${LOG}" 2>/dev/null || true
        echo ""
        echo "Полная остановка и повтор:  bash $0 stop   &&   bash $0"
        exit 1
    fi
done

echo ""
echo "=========================================="
echo "  Все 4 SITL инстанса запущены!"
echo ""
echo "  Ожидайте ~30 сек пока ArduCopter инициализируется."
echo ""
echo "  Порты MAVProxy TCP:"
echo "    Дрон 0: tcp:0.0.0.0:14550"
echo "    Дрон 1: tcp:0.0.0.0:14560"
echo "    Дрон 2: tcp:0.0.0.0:14570"
echo "    Дрон 3: tcp:0.0.0.0:14580"
echo ""
echo "  .env (уже настроен):"
echo "  SITL_HOSTS=tcp:host.docker.internal:14550,...:14560,...:14570,...:14580"
echo ""
echo "  Просмотр: screen -r sitl_0  (цифра 0…3; не вводите букву N)"
echo "  Остановка: bash $0 stop"
echo "=========================================="
