"""
test_infrastructure.py — тесты инфраструктуры SafeAgriRoute.

Три группы тестов:

1. Unit (без Docker, без SITL)
   Проверяют структуру конфигурационных файлов через разбор YAML/текста.
   Всегда выполняются в CI.

2. ``docker`` — требуют установленный Docker CLI (без запущенного стека)
   pytest -m docker tests/test_infrastructure.py -v

3. ``stack`` — требуют запущенный docker-compose стек
   docker-compose up -d --build
   pytest -m stack tests/test_infrastructure.py -v

Запуск только unit-тестов:
   pytest tests/test_infrastructure.py -v -m "not docker and not stack"
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

import pytest
import yaml

# ---------------------------------------------------------------------------
# Пути к файлам (всегда от корня репозитория)
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

COMPOSE_MAIN    = os.path.join(REPO_ROOT, "docker-compose.yml")
COMPOSE_SITL    = os.path.join(REPO_ROOT, "docker-compose.sitl.yml")
SITL_SCRIPT     = os.path.join(REPO_ROOT, "start_sitl_wsl.sh")
DOCKERFILE_SITL = os.path.join(REPO_ROOT, "Dockerfile.sitl")
DOCKERFILE_FE   = os.path.join(REPO_ROOT, "frontend", "Dockerfile")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _read_text(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, OSError):
        return False


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _stack_running(host: str = "127.0.0.1", port: int = 8000) -> bool:
    return _port_open(host, port)


docker_required    = pytest.mark.skipif(not _docker_available(), reason="Docker CLI not available")
stack_required     = pytest.mark.skipif(not _stack_running(), reason="docker-compose stack not running on :8000")
frontend_required  = pytest.mark.skipif(not _port_open("127.0.0.1", 3000), reason="Frontend not running on :3000")


# ===========================================================================
# 1. docker-compose.yml — основной стек
# ===========================================================================

class TestMainCompose:
    """Проверка структуры docker-compose.yml без запуска Docker."""

    def setup_method(self):
        self.cfg = _load_yaml(COMPOSE_MAIN)
        self.svc = self.cfg["services"]

    # ── обязательные сервисы ──────────────────────────────────────────────

    def test_required_services_present(self):
        assert "db" in self.svc,       "Отсутствует сервис db"
        assert "backend" in self.svc,  "Отсутствует сервис backend"
        assert "frontend" in self.svc, "Отсутствует сервис frontend"

    # ── backend ───────────────────────────────────────────────────────────

    def test_backend_port_8000(self):
        ports = self.svc["backend"].get("ports", [])
        assert any("8000" in str(p) for p in ports), \
            f"backend не публикует порт 8000: {ports}"

    def test_backend_has_database_url(self):
        env = self.svc["backend"].get("environment", [])
        env_str = str(env)
        assert "DATABASE_URL" in env_str, "DATABASE_URL не задан в backend"

    def test_backend_has_sitl_hosts_env(self):
        env = self.svc["backend"].get("environment", [])
        env_str = str(env)
        assert "SITL_HOSTS" in env_str, \
            "SITL_HOSTS отсутствует в env бэкенда; нужен для MAVLink-интеграции"

    def test_backend_depends_on_db(self):
        deps = self.svc["backend"].get("depends_on", {})
        # depends_on может быть списком или dict
        deps_keys = deps if isinstance(deps, list) else list(deps.keys())
        assert "db" in deps_keys, "backend не объявляет depends_on: db"

    def test_backend_db_healthcheck_condition(self):
        """depends_on db должен использовать condition: service_healthy."""
        deps = self.svc["backend"].get("depends_on", {})
        if isinstance(deps, dict) and "db" in deps:
            condition = deps["db"].get("condition", "")
            assert condition == "service_healthy", \
                f"backend→db condition должен быть service_healthy, получили: {condition!r}"

    # ── database ──────────────────────────────────────────────────────────

    def test_db_image_postgis(self):
        image = self.svc["db"].get("image", "")
        assert "postgis" in image.lower(), \
            f"db должен использовать PostGIS-образ, получили: {image!r}"

    def test_db_has_healthcheck(self):
        hc = self.svc["db"].get("healthcheck", {})
        assert hc, "db не имеет healthcheck — backend не узнает о готовности БД"
        test_cmd = str(hc.get("test", ""))
        assert "pg_isready" in test_cmd or "psql" in test_cmd, \
            f"healthcheck db должен проверять PostgreSQL; получили: {test_cmd!r}"

    def test_db_postgres_credentials(self):
        env = self.svc["db"].get("environment", {})
        env_str = str(env)
        for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            assert key in env_str, f"Отсутствует {key} в env сервиса db"

    # ── frontend ──────────────────────────────────────────────────────────

    def test_frontend_port_3000(self):
        ports = self.svc["frontend"].get("ports", [])
        assert any("3000" in str(p) for p in ports), \
            f"frontend не публикует порт 3000: {ports}"

    # ── networks ──────────────────────────────────────────────────────────

    def test_shared_network_defined(self):
        nets = self.cfg.get("networks", {})
        assert "safagri_net" in nets, \
            "Общая сеть safagri_net не определена в docker-compose.yml"

    def test_all_services_on_shared_network(self):
        for name, svc in self.svc.items():
            svc_nets = svc.get("networks", [])
            # networks может быть dict или list
            if isinstance(svc_nets, dict):
                net_names = list(svc_nets.keys())
            else:
                net_names = svc_nets
            assert "safagri_net" in net_names, \
                f"Сервис {name!r} не подключён к сети safagri_net"

    # ── volumes ───────────────────────────────────────────────────────────

    def test_postgres_volume_defined(self):
        vols = self.cfg.get("volumes", {})
        assert "safe_agri_route_pgdata" in vols, \
            "Postgres volume safe_agri_route_pgdata не определён"

    def test_backend_mounts_code(self):
        vols = self.svc["backend"].get("volumes", [])
        assert any("backend" in str(v) or "/app" in str(v) for v in vols), \
            "backend не монтирует директорию кода как volume"


# ===========================================================================
# 2. docker-compose.sitl.yml — SITL-оверлей
# ===========================================================================

SITL_SERVICES  = ["sitl-1", "sitl-2", "sitl-3", "sitl-4"]
SITL_PORTS     = [14550, 14560, 14570, 14580]
SITL_INSTANCES = [0, 1, 2, 3]


class TestSITLCompose:
    """Проверка docker-compose.sitl.yml без запуска Docker."""

    def setup_method(self):
        self.cfg = _load_yaml(COMPOSE_SITL)
        self.svc = self.cfg["services"]

    # ── наличие всех 4 SITL-сервисов ─────────────────────────────────────

    def test_all_four_sitl_services_present(self):
        for name in SITL_SERVICES:
            assert name in self.svc, \
                f"Сервис {name!r} отсутствует в docker-compose.sitl.yml"

    # ── порты и инстансы ──────────────────────────────────────────────────

    @pytest.mark.parametrize("svc_name,expected_port,expected_instance", [
        ("sitl-1", 14550, 0),
        ("sitl-2", 14560, 1),
        ("sitl-3", 14570, 2),
        ("sitl-4", 14580, 3),
    ])
    def test_sitl_port_and_instance(self, svc_name, expected_port, expected_instance):
        svc = self.svc[svc_name]

        # Порт
        ports = svc.get("ports", [])
        assert any(str(expected_port) in str(p) for p in ports), \
            f"{svc_name}: ожидался порт {expected_port}, получили {ports}"

        # INSTANCE через environment
        env = svc.get("environment", [])
        env_str = str(env)
        assert f"INSTANCE={expected_instance}" in env_str or \
               f"INSTANCE: {expected_instance}" in env_str, \
            f"{svc_name}: INSTANCE должен быть {expected_instance}, env={env}"

    # ── стартовые координаты (Ставрополь) ────────────────────────────────

    @pytest.mark.parametrize("svc_name", SITL_SERVICES)
    def test_sitl_stavropol_coordinates(self, svc_name):
        svc = self.svc[svc_name]
        env_str = str(svc.get("environment", []))
        assert "45.0448" in env_str and "41.9734" in env_str, \
            f"{svc_name}: отсутствуют координаты Ставрополя в LOCATION"

    # ── healthcheck ───────────────────────────────────────────────────────

    @pytest.mark.parametrize("svc_name,port", list(zip(SITL_SERVICES, SITL_PORTS)))
    def test_sitl_healthcheck_present(self, svc_name, port):
        hc = self.svc[svc_name].get("healthcheck", {})
        assert hc, f"{svc_name} не имеет healthcheck"
        cmd_str = str(hc.get("test", ""))
        assert "nc" in cmd_str or "netcat" in cmd_str or str(port) in cmd_str, \
            f"{svc_name}: healthcheck должен проверять порт {port}; получили: {cmd_str!r}"

    # ── backend override в SITL-оверлее ──────────────────────────────────

    def test_backend_service_in_sitl_overlay(self):
        assert "backend" in self.svc, \
            "SITL-оверлей должен переопределять сервис backend (SITL_HOSTS + depends_on)"

    def test_backend_sitl_hosts_uses_container_dns(self):
        """SITL_HOSTS в оверлее должен ссылаться на имена контейнеров (sitl-1 … sitl-4)."""
        env = self.svc["backend"].get("environment", [])
        env_str = str(env)
        assert "SITL_HOSTS" in env_str, "SITL_HOSTS не задан в backend-оверлее"
        for svc_name in SITL_SERVICES:
            assert svc_name in env_str, \
                f"SITL_HOSTS в backend-оверлее не содержит имя контейнера {svc_name!r}"

    def test_backend_depends_on_all_sitl(self):
        deps = self.svc["backend"].get("depends_on", {})
        deps_keys = deps if isinstance(deps, list) else list(deps.keys())
        for svc_name in SITL_SERVICES:
            assert svc_name in deps_keys, \
                f"backend не объявляет depends_on {svc_name!r} в SITL-оверлее"

    def test_backend_sitl_depends_on_service_healthy(self):
        """Каждый SITL-сервис должен ждать condition: service_healthy."""
        deps = self.svc["backend"].get("depends_on", {})
        if not isinstance(deps, dict):
            pytest.skip("depends_on — список, проверка condition невозможна")
        for svc_name in SITL_SERVICES:
            cond = deps.get(svc_name, {}).get("condition", "")
            assert cond == "service_healthy", \
                f"backend→{svc_name} condition должен быть service_healthy, получили: {cond!r}"

    # ── все SITL-сервисы используют один Dockerfile ───────────────────────

    @pytest.mark.parametrize("svc_name", SITL_SERVICES)
    def test_sitl_dockerfile_reference(self, svc_name):
        build = self.svc[svc_name].get("build", {})
        dockerfile = build.get("dockerfile", "")
        assert "Dockerfile.sitl" in dockerfile, \
            f"{svc_name}: должен использовать Dockerfile.sitl, получили: {dockerfile!r}"

    # ── нет дублирования портов ───────────────────────────────────────────

    def test_no_duplicate_ports(self):
        all_ports = []
        for svc_name in SITL_SERVICES:
            ports = self.svc[svc_name].get("ports", [])
            for p in ports:
                host_port = str(p).split(":")[0]
                all_ports.append(host_port)
        assert len(all_ports) == len(set(all_ports)), \
            f"Дублирующиеся порты в SITL-сервисах: {all_ports}"


# ===========================================================================
# 3. start_sitl_wsl.sh
# ===========================================================================

class TestSITLScript:
    """Проверка содержимого start_sitl_wsl.sh без запуска."""

    def setup_method(self):
        self.content = _read_text(SITL_SCRIPT)

    def test_file_exists(self):
        assert os.path.isfile(SITL_SCRIPT), f"Файл не найден: {SITL_SCRIPT}"

    def test_shebang_bash(self):
        first_line = self.content.splitlines()[0]
        assert first_line.startswith("#!/bin/bash"), \
            f"Первая строка должна быть shebang '#!/bin/bash', получили: {first_line!r}"

    def test_is_executable(self):
        assert os.access(SITL_SCRIPT, os.X_OK), \
            f"{SITL_SCRIPT} не исполняемый; выполните: chmod +x start_sitl_wsl.sh"

    @pytest.mark.parametrize("port", [14550, 14560, 14570, 14580])
    def test_all_four_ports_present(self, port):
        assert str(port) in self.content, \
            f"Порт {port} не найден в start_sitl_wsl.sh"

    def test_ardupilot_location_stavropol(self):
        assert "45.0448" in self.content and "41.9734" in self.content, \
            "Координаты Ставрополя (45.0448, 41.9734) не найдены в скрипте"

    def test_arducopter_vehicle_type(self):
        assert "ArduCopter" in self.content, \
            "Тип БПЛА 'ArduCopter' не указан в скрипте"

    def test_four_instances_loop(self):
        """Скрипт должен запускать 4 инстанса (0, 1, 2, 3)."""
        # Либо явное перечисление, либо цикл for i in 0 1 2 3
        has_explicit_instances = all(
            f"--instance {i}" in self.content or f"--instance $i" in self.content.replace('"', '')
            for i in range(4)
        )
        has_loop = "for i in 0 1 2 3" in self.content or \
                   re.search(r"for\s+i\s+in\s+[0-9\s]+", self.content) is not None
        assert has_explicit_instances or has_loop, \
            "Скрипт не запускает 4 инстанса — не найдено явного перечисления или цикла"

    def test_mavproxy_with_screen(self):
        assert "screen" in self.content, \
            "sim_vehicle.py должен запускаться в screen-сессии — иначе MAVProxy падает без PTY"
        assert "tcpin" in self.content, \
            "MAVProxy должен слушать через tcpin (server mode) для подключения бэкенда"

    def test_screen_stop_command(self):
        assert "screen" in self.content and "quit" in self.content, \
            "Скрипт должен управлять screen-сессиями (screen -X quit) вместо PID-файлов"

    def test_ardupilot_dir_variable(self):
        assert "ardupilot" in self.content.lower(), \
            "Путь к ArduPilot не упомянут в скрипте"

    def test_sleep_between_instances(self):
        assert "sleep" in self.content, \
            "Пауза между запусками инстансов (sleep) обязательна — иначе конфликты портов"

    def test_bash_syntax_check(self):
        """bash -n проверяет синтаксис без выполнения скрипта."""
        if shutil.which("bash") is None:
            pytest.skip("bash недоступен в PATH")
        result = subprocess.run(
            ["bash", "-n", SITL_SCRIPT],
            capture_output=True, text=True
        )
        assert result.returncode == 0, \
            f"bash -n сообщил о синтаксической ошибке:\n{result.stderr}"


# ===========================================================================
# 4. Dockerfile.sitl
# ===========================================================================

class TestDockerfileSITL:
    """Проверка структуры Dockerfile.sitl."""

    def setup_method(self):
        self.content = _read_text(DOCKERFILE_SITL)

    def test_file_exists(self):
        assert os.path.isfile(DOCKERFILE_SITL), \
            f"Файл не найден: {DOCKERFILE_SITL}"

    def test_base_image_ubuntu_22(self):
        assert "ubuntu:22.04" in self.content.lower() or \
               "ubuntu:jammy" in self.content.lower(), \
            "Dockerfile.sitl должен использовать Ubuntu 22.04 как базовый образ"

    def test_ardupilot_clone_or_install(self):
        assert "ardupilot" in self.content.lower(), \
            "Dockerfile.sitl должен устанавливать/клонировать ArduPilot"

    def test_instance_env_variable(self):
        assert "INSTANCE" in self.content, \
            "Dockerfile.sitl должен объявлять ENV INSTANCE (переопределяется docker-compose)"

    def test_port_env_variable(self):
        assert "PORT" in self.content, \
            "Dockerfile.sitl должен объявлять ENV PORT (переопределяется docker-compose)"

    def test_sim_vehicle_cmd(self):
        assert "sim_vehicle.py" in self.content, \
            "Dockerfile.sitl CMD должен содержать вызов sim_vehicle.py"

    def test_no_mavproxy_in_cmd(self):
        assert "--no-mavproxy" in self.content, \
            "CMD в Dockerfile.sitl должен содержать --no-mavproxy"

    def test_non_root_user(self):
        """SITL нестабилен под root — должен быть другой пользователь."""
        assert re.search(r"^USER\s+\w+", self.content, re.MULTILINE), \
            "Dockerfile.sitl должен переключаться на не-root пользователя"

    def test_netcat_installed(self):
        """nc нужен для healthcheck."""
        assert "netcat" in self.content.lower() or "nc" in self.content, \
            "Dockerfile.sitl должен устанавливать netcat (нужен для healthcheck)"


# ===========================================================================
# 5. frontend/Dockerfile
# ===========================================================================

class TestDockerfileFrontend:
    """Проверка структуры frontend/Dockerfile."""

    def setup_method(self):
        self.content = _read_text(DOCKERFILE_FE)

    def test_file_exists(self):
        assert os.path.isfile(DOCKERFILE_FE), \
            f"Файл не найден: {DOCKERFILE_FE}"

    def test_node_20_base_image(self):
        assert "node:20" in self.content, \
            "frontend/Dockerfile должен использовать Node.js 20 (LTS)"

    def test_exposes_port_3000(self):
        assert "3000" in self.content, \
            "frontend/Dockerfile должен публиковать порт 3000"

    def test_npm_install_or_ci(self):
        assert "npm install" in self.content or "npm ci" in self.content, \
            "frontend/Dockerfile должен устанавливать зависимости через npm"

    def test_workdir_set(self):
        assert "WORKDIR" in self.content, \
            "frontend/Dockerfile должен устанавливать WORKDIR"

    def test_entrypoint_or_cmd_dev(self):
        assert "dev" in self.content or "CMD" in self.content, \
            "frontend/Dockerfile должен запускать dev-сервер (npm run dev)"


# ===========================================================================
# 6. Docker CLI — валидация конфигурации (docker_required)
# ===========================================================================

@pytest.mark.docker
class TestDockerConfigValidation:
    """
    Требует установленный Docker CLI.
    НЕ запускает контейнеры — только валидирует конфиги через docker-compose config.
    """

    @docker_required
    def test_main_compose_config_valid(self):
        """docker-compose config должен завершиться без ошибок."""
        result = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_MAIN, "config", "--quiet"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.returncode == 0, \
            f"docker-compose.yml невалиден:\n{result.stderr}"

    @docker_required
    def test_sitl_overlay_config_valid(self):
        """Комбинация main + sitl оверлея должна быть валидна."""
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", COMPOSE_MAIN,
                "-f", COMPOSE_SITL,
                "config", "--quiet"
            ],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.returncode == 0, \
            f"docker-compose.sitl.yml невалиден:\n{result.stderr}"

    @docker_required
    def test_main_compose_lists_expected_services(self):
        """docker-compose config должен содержать все три сервиса."""
        result = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_MAIN, "config", "--services"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.returncode == 0
        services = result.stdout.strip().splitlines()
        for expected in ("db", "backend", "frontend"):
            assert expected in services, \
                f"Сервис {expected!r} не найден в выводе docker-compose config"

    @docker_required
    def test_sitl_overlay_lists_sitl_services(self):
        """После применения оверлея должны появиться sitl-1 … sitl-4."""
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", COMPOSE_MAIN,
                "-f", COMPOSE_SITL,
                "config", "--services"
            ],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.returncode == 0
        services = result.stdout.strip().splitlines()
        for expected in SITL_SERVICES:
            assert expected in services, \
                f"SITL-сервис {expected!r} не найден после применения оверлея"

    @docker_required
    def test_backend_dockerfile_builds(self):
        """backend/Dockerfile должен проходить docker build --check без ошибок."""
        dockerfile = os.path.join(REPO_ROOT, "backend", "Dockerfile")
        if not os.path.isfile(dockerfile):
            pytest.skip("backend/Dockerfile не найден")
        result = subprocess.run(
            ["docker", "build", "--check", "--quiet", "."],
            capture_output=True, text=True,
            cwd=os.path.join(REPO_ROOT, "backend")
        )
        # --check доступен только в BuildKit; если неизвестная команда — skip
        if result.returncode == 1 and "unknown flag" in result.stderr:
            pytest.skip("docker build --check недоступен (нужен BuildKit ≥ 0.12)")
        assert result.returncode == 0, \
            f"backend/Dockerfile не прошёл docker build --check:\n{result.stderr}"


# ===========================================================================
# 7. Интеграционные smoke-тесты запущенного стека (stack_required)
# ===========================================================================

@pytest.mark.stack
class TestRunningStack:
    """
    Требуют запущенный docker-compose стек:
        docker-compose up -d --build
    и инициализированную БД:
        docker-compose exec backend python seed.py
    """

    # Даём стеку время подняться перед первым запросом
    BACKEND_URL  = "http://127.0.0.1:8000"
    FRONTEND_URL = "http://127.0.0.1:3000"

    @stack_required
    def test_backend_port_open(self):
        assert _port_open("127.0.0.1", 8000), \
            "Backend не отвечает на порту 8000"

    @stack_required
    @frontend_required
    def test_frontend_port_open(self):
        assert _port_open("127.0.0.1", 3000), \
            "Frontend не отвечает на порту 3000"

    @stack_required
    def test_swagger_ui_accessible(self):
        assert _http_ok(f"{self.BACKEND_URL}/docs"), \
            "Swagger UI (GET /docs) вернул не 2xx/3xx"

    @stack_required
    def test_openapi_json_accessible(self):
        assert _http_ok(f"{self.BACKEND_URL}/openapi.json"), \
            "OpenAPI JSON (GET /openapi.json) недоступен"

    @stack_required
    def test_health_or_root_endpoint(self):
        ok = _http_ok(f"{self.BACKEND_URL}/health") or \
             _http_ok(f"{self.BACKEND_URL}/")
        assert ok, "Ни /health ни / не отвечают 2xx"

    @stack_required
    def test_auth_login_endpoint_reachable(self):
        """POST /auth/login без тела должен вернуть 4xx (не 500, не connection error)."""
        try:
            req = urllib.request.Request(
                f"{self.BACKEND_URL}/auth/login",
                data=b"",
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                # Любой HTTP-ответ означает, что endpoint достижим
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        except urllib.error.URLError as e:
            pytest.fail(f"Эндпоинт /auth/login недоступен: {e}")

        assert 400 <= status <= 499 or status == 200, \
            f"Ожидался 4xx (нет credentials) или 200, получили {status}"

    @stack_required
    def test_fields_endpoint_requires_auth(self):
        """GET /api/v1/mission/fields без токена должен вернуть 401/403, не 500."""
        try:
            with urllib.request.urlopen(
                f"{self.BACKEND_URL}/api/v1/mission/fields", timeout=5
            ) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        except urllib.error.URLError as e:
            pytest.fail(f"/api/v1/mission/fields недоступен: {e}")

        assert status in (401, 403, 422), \
            f"Ожидался 401/403 (нет токена), получили {status}"

    @stack_required
    def test_risk_zones_endpoint_requires_auth(self):
        """GET /api/v1/mission/risk-zones без токена должен вернуть 401/403."""
        try:
            with urllib.request.urlopen(
                f"{self.BACKEND_URL}/api/v1/mission/risk-zones", timeout=5
            ) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        except urllib.error.URLError as e:
            pytest.fail(f"/api/v1/mission/risk-zones недоступен: {e}")

        assert status in (401, 403, 422), \
            f"Ожидался 401/403, получили {status}"

    @stack_required
    def test_db_connection_via_backend_metrics(self):
        """
        Если стек поднят с seed.py — /api/v1/mission/fields с правильным токеном
        вернёт 200.  Здесь только проверяем, что бэкенд не падает с 500.
        """
        # Пробуем логин с тестовым пользователем из seed.py
        import json
        login_data = "username=operator%40safegriroute.com&password=operator123"
        try:
            req = urllib.request.Request(
                f"{self.BACKEND_URL}/auth/login",
                data=login_data.encode(),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                token = body.get("access_token")
        except Exception:
            pytest.skip("Пользователь seed не найден или auth недоступен — seed.py не запускался?")

        if not token:
            pytest.skip("Токен не получен")

        req = urllib.request.Request(
            f"{self.BACKEND_URL}/api/v1/mission/fields",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        assert status == 200, \
            f"GET /api/v1/mission/fields с валидным токеном вернул {status}"

    @stack_required
    @frontend_required
    def test_frontend_returns_html(self):
        """Frontend должен отдавать HTML-страницу (React SPA)."""
        try:
            with urllib.request.urlopen(
                f"{self.FRONTEND_URL}/", timeout=5
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read(512).decode("utf-8", errors="ignore")
        except urllib.error.URLError as e:
            pytest.fail(f"Frontend не отвечает: {e}")

        assert "html" in content_type.lower() or "<!doctype" in body.lower() or \
               "<html" in body.lower(), \
            f"Frontend не вернул HTML. Content-Type={content_type!r}, тело={body!r}"


# ===========================================================================
# 8. Совместимость конфигурации с mavlink_service
# ===========================================================================

class TestSITLHostsConfig:
    """
    Проверяет, что SITL_HOSTS в конфиге корректно разбирается
    функцией _parse_sitl_hosts из mavlink_service.
    """

    def test_overlay_sitl_hosts_parses_four_drones(self, monkeypatch):
        """
        Значение SITL_HOSTS из SITL-оверлея должно давать 4 drone_id
        при разборе через _parse_sitl_hosts.
        """
        cfg = _load_yaml(COMPOSE_SITL)
        env = cfg["services"]["backend"].get("environment", [])

        sitl_hosts_value = None
        if isinstance(env, list):
            for item in env:
                if item.startswith("SITL_HOSTS="):
                    sitl_hosts_value = item.split("=", 1)[1]
                    break
        elif isinstance(env, dict):
            sitl_hosts_value = env.get("SITL_HOSTS", "")

        if not sitl_hosts_value:
            pytest.skip("SITL_HOSTS не задан в backend-оверлее")

        monkeypatch.setenv("SITL_HOSTS", sitl_hosts_value)
        from app.services.mavlink_service import _parse_sitl_hosts

        hosts = _parse_sitl_hosts()
        assert len(hosts) == 4, \
            f"SITL_HOSTS из оверлея должен давать 4 drone_id, получили {len(hosts)}: {hosts}"

    def test_overlay_sitl_hosts_all_tcp_scheme(self, monkeypatch):
        """Все адреса должны начинаться с tcp: для корректной работы pymavlink."""
        cfg = _load_yaml(COMPOSE_SITL)
        env = cfg["services"]["backend"].get("environment", [])

        sitl_hosts_value = None
        if isinstance(env, list):
            for item in env:
                if item.startswith("SITL_HOSTS="):
                    sitl_hosts_value = item.split("=", 1)[1]
        elif isinstance(env, dict):
            sitl_hosts_value = env.get("SITL_HOSTS", "")

        if not sitl_hosts_value:
            pytest.skip("SITL_HOSTS не задан")

        monkeypatch.setenv("SITL_HOSTS", sitl_hosts_value)
        from app.services.mavlink_service import _parse_sitl_hosts

        hosts = _parse_sitl_hosts()
        for drone_id, addr in hosts.items():
            assert addr.startswith("tcp:"), \
                f"Drone {drone_id}: адрес {addr!r} не использует tcp: схему"

    @pytest.mark.parametrize("raw,expected_count", [
        ("tcp:sitl-1:14550,tcp:sitl-2:14560,tcp:sitl-3:14570,tcp:sitl-4:14580", 4),
        ("tcp:127.0.0.1:14550", 1),
        ("tcp:h.d.i:14550,tcp:h.d.i:14560", 2),
        ("", 1),   # пустая строка → дефолт 1 дрон
    ])
    def test_parse_hosts_count(self, monkeypatch, raw, expected_count):
        if raw:
            monkeypatch.setenv("SITL_HOSTS", raw)
        else:
            monkeypatch.delenv("SITL_HOSTS", raising=False)

        from app.services.mavlink_service import _parse_sitl_hosts
        # Пересоздать — модуль может быть закэширован
        import importlib, app.services.mavlink_service as m
        importlib.reload(m)
        hosts = m._parse_sitl_hosts()
        assert len(hosts) == expected_count, \
            f"raw={raw!r} → ожидалось {expected_count} drone_id, получили {len(hosts)}"

    def test_drone_ids_are_sequential_from_one(self, monkeypatch):
        """drone_id всегда начинается с 1 и идёт последовательно."""
        monkeypatch.setenv("SITL_HOSTS", "tcp:a:1,tcp:b:2,tcp:c:3")
        import importlib, app.services.mavlink_service as m
        importlib.reload(m)
        hosts = m._parse_sitl_hosts()
        assert list(hosts.keys()) == [1, 2, 3]

    def test_whitespace_in_hosts_trimmed(self, monkeypatch):
        monkeypatch.setenv("SITL_HOSTS", " tcp:a:1 , tcp:b:2 ")
        import importlib, app.services.mavlink_service as m
        importlib.reload(m)
        hosts = m._parse_sitl_hosts()
        assert len(hosts) == 2
        assert hosts[1] == "tcp:a:1"
        assert hosts[2] == "tcp:b:2"
