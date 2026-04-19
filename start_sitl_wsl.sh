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
# Остановка: screen -ls | grep sitl | awk '{print $1}' | xargs -I{} screen -S {} -X quit

LOCATION="45.0448,41.9734,0,0"
ARDUPILOT_DIR="${HOME}/ardupilot"
VENV="${HOME}/venv-ardupilot"

if [ ! -d "${ARDUPILOT_DIR}" ]; then
    echo "ERROR: ArduPilot не найден в ${ARDUPILOT_DIR}"
    exit 1
fi

echo "=========================================="
echo "  SafeAgriRoute — Запуск 4 SITL инстансов"
echo "  Локация: ${LOCATION}"
echo "=========================================="

# Убить старые сессии если есть
for i in 0 1 2 3; do
    screen -S "sitl_${i}" -X quit 2>/dev/null || true
done

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

    # Ждём пока MAVProxy откроет tcpin порт
    echo -n "  Ожидание порта ${GCS_PORT}"
    for attempt in $(seq 1 40); do
        if ss -tlnp 2>/dev/null | grep -q ":${GCS_PORT}"; then
            echo " — готов."
            break
        fi
        echo -n "."
        sleep 2
    done
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
echo "  Просмотр: screen -r sitl_N"
echo "  Остановка: screen -ls | grep sitl | awk '{print \$1}' | xargs -I{} screen -S {} -X quit"
echo "=========================================="
