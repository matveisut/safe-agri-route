#!/bin/bash
# start_sitl_wsl.sh
# Запуск 4 SITL инстансов ArduCopter для SafeAgriRoute в WSL2.
#
# Использование:
#   bash start_sitl_wsl.sh
#
# Требования:
#   - WSL2 Ubuntu 22.04
#   - ArduPilot установлен в ~/ardupilot
#   - sim_vehicle.py доступен в PATH
#     (источник: Tools/autotest/sim_vehicle.py)
#
# После запуска порты:
#   Дрон 0: tcp:127.0.0.1:14550
#   Дрон 1: tcp:127.0.0.1:14560
#   Дрон 2: tcp:127.0.0.1:14570
#   Дрон 3: tcp:127.0.0.1:14580
#
# Бэкенд (в Docker или локально) должен иметь переменную:
#   SITL_HOSTS=tcp:127.0.0.1:14550,tcp:127.0.0.1:14560,tcp:127.0.0.1:14570,tcp:127.0.0.1:14580
#
# Для остановки: нажмите Ctrl+C или выполните: kill $(cat /tmp/sitl_pids.txt)

set -e

LOCATION="45.0448,41.9734,0,0"
ARDUPILOT_DIR="${HOME}/ardupilot"
PID_FILE="/tmp/sitl_pids.txt"

# Проверяем наличие ArduPilot
if [ ! -d "${ARDUPILOT_DIR}" ]; then
    echo "ERROR: ArduPilot не найден в ${ARDUPILOT_DIR}"
    echo "Установите ArduPilot:"
    echo "  git clone https://github.com/ArduPilot/ardupilot.git ~/ardupilot"
    echo "  cd ~/ardupilot && git submodule update --init --recursive"
    echo "  Tools/environment_install/install-prereqs-ubuntu.sh -y"
    exit 1
fi

# Проверяем sim_vehicle.py
if ! command -v sim_vehicle.py &> /dev/null; then
    # Пробуем путь по умолчанию
    SIM_VEHICLE="${ARDUPILOT_DIR}/Tools/autotest/sim_vehicle.py"
    if [ ! -f "${SIM_VEHICLE}" ]; then
        echo "ERROR: sim_vehicle.py не найден"
        echo "Добавьте в PATH: export PATH=\$PATH:${ARDUPILOT_DIR}/Tools/autotest"
        exit 1
    fi
    export PATH="${ARDUPILOT_DIR}/Tools/autotest:${PATH}"
fi

echo "=========================================="
echo "  SafeAgriRoute — Запуск 4 SITL инстансов"
echo "  Локация: ${LOCATION}"
echo "=========================================="

# Очищаем старый PID-файл
> "${PID_FILE}"

cd "${ARDUPILOT_DIR}"

for i in 0 1 2 3; do
    PORT=$((14550 + i * 10))
    DRONE_DIR="/tmp/sitl_drone_${i}"
    mkdir -p "${DRONE_DIR}"

    echo "→ Запуск SITL инстанса ${i} на порту tcp:127.0.0.1:${PORT}..."

    sim_vehicle.py \
        -v ArduCopter \
        --instance "${i}" \
        --custom-location="${LOCATION}" \
        --out="tcp:0.0.0.0:${PORT}" \
        --no-mavproxy \
        --speedup=1 \
        --sim-address=127.0.0.1 \
        -D \
        > "${DRONE_DIR}/sitl.log" 2>&1 &

    echo $! >> "${PID_FILE}"
    echo "  PID: $! | Лог: ${DRONE_DIR}/sitl.log"

    # Пауза между стартами — избегаем конфликтов при инициализации
    sleep 3
done

echo ""
echo "=========================================="
echo "  Все 4 SITL инстанса запущены!"
echo ""
echo "  Порты:"
echo "    Дрон 0: tcp:127.0.0.1:14550"
echo "    Дрон 1: tcp:127.0.0.1:14560"
echo "    Дрон 2: tcp:127.0.0.1:14570"
echo "    Дрон 3: tcp:127.0.0.1:14580"
echo ""
echo "  Установите переменную в .env бэкенда или docker-compose.yml:"
echo "  SITL_HOSTS=tcp:127.0.0.1:14550,tcp:127.0.0.1:14560,tcp:127.0.0.1:14570,tcp:127.0.0.1:14580"
echo ""
echo "  Логи: /tmp/sitl_drone_N/sitl.log"
echo "  Остановить: kill \$(cat ${PID_FILE})"
echo "=========================================="

# Ожидаем завершения всех фоновых процессов
wait
