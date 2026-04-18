#!/bin/bash
# start_sitl_wsl.sh
# Запуск 4 SITL инстансов ArduCopter для SafeAgriRoute в WSL2.
#
# Использование:
#   bash start_sitl_wsl.sh
#
# Порты (прямой MAVLink, без mavproxy):
#   Дрон 0: tcp:127.0.0.1:5760
#   Дрон 1: tcp:127.0.0.1:5770
#   Дрон 2: tcp:127.0.0.1:5780
#   Дрон 3: tcp:127.0.0.1:5790
#
# Для Docker-бэкенда (.env):
#   SITL_HOSTS=tcp:host.docker.internal:5760,tcp:host.docker.internal:5770,tcp:host.docker.internal:5780,tcp:host.docker.internal:5790
#
# Для остановки: kill $(cat /tmp/sitl_pids.txt)

set -e

LOCATION="45.0448,41.9734,0,0"
ARDUPILOT_DIR="${HOME}/ardupilot"
PID_FILE="/tmp/sitl_pids.txt"
VENV="${HOME}/venv-ardupilot"

if [ ! -d "${ARDUPILOT_DIR}" ]; then
    echo "ERROR: ArduPilot не найден в ${ARDUPILOT_DIR}"
    exit 1
fi

# Активируем venv с pymavlink/sim_vehicle
if [ -f "${VENV}/bin/activate" ]; then
    source "${VENV}/bin/activate"
fi

export PATH="${ARDUPILOT_DIR}/Tools/autotest:${PATH}"

# Запускать arducopter через bash (без xterm) — работает в любом окружении
export SITL_RITW_TERMINAL="bash"

echo "=========================================="
echo "  SafeAgriRoute — Запуск 4 SITL инстансов"
echo "  Локация: ${LOCATION}"
echo "=========================================="

> "${PID_FILE}"

cd "${ARDUPILOT_DIR}"

ARDUCOPTER_BIN="${ARDUPILOT_DIR}/build/sitl/bin/arducopter"

if [ ! -f "${ARDUCOPTER_BIN}" ]; then
    echo "→ Первый запуск: собираем ArduCopter SITL (~5-15 мин)..."
    SITL_RITW_TERMINAL="bash" python Tools/autotest/sim_vehicle.py \
        -v ArduCopter --no-mavproxy --instance 0 > /tmp/sitl_build.log 2>&1 || true
    echo "  Сборка завершена."
fi

for i in 0 1 2 3; do
    # ArduCopter по умолчанию занимает порт 5760 + 10*instance
    PORT=$((5760 + i * 10))
    DRONE_DIR="/tmp/sitl_drone_${i}"
    mkdir -p "${DRONE_DIR}"

    echo "→ Запуск SITL инстанса ${i} на порту tcp:127.0.0.1:${PORT}..."

    # --no-mavproxy: ArduCopter слушает напрямую на PORT (MAVLink TCP)
    python Tools/autotest/sim_vehicle.py \
        -v ArduCopter \
        --instance "${i}" \
        --custom-location="${LOCATION}" \
        --no-mavproxy \
        --no-rebuild \
        --speedup=1 \
        > "${DRONE_DIR}/sitl.log" 2>&1 &

    echo $! >> "${PID_FILE}"
    echo "  PID: $! | Лог: ${DRONE_DIR}/sitl.log"

    sleep 5
done

echo ""
echo "=========================================="
echo "  Все 4 SITL инстанса запущены!"
echo ""
echo "  Ожидайте ~15 сек пока ArduCopter инициализируется."
echo ""
echo "  Порты (прямой MAVLink):"
echo "    Дрон 0: tcp:127.0.0.1:5760"
echo "    Дрон 1: tcp:127.0.0.1:5770"
echo "    Дрон 2: tcp:127.0.0.1:5780"
echo "    Дрон 3: tcp:127.0.0.1:5790"
echo ""
echo "  Установите в .env бэкенда:"
echo "  SITL_HOSTS=tcp:host.docker.internal:5760,tcp:host.docker.internal:5770,tcp:host.docker.internal:5780,tcp:host.docker.internal:5790"
echo ""
echo "  Логи: /tmp/sitl_drone_N/sitl.log"
echo "  Остановить: kill \$(cat ${PID_FILE})"
echo "=========================================="

wait
