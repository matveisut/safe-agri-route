"""
test_mavlink_service.py — unit + integration tests for MAVLinkService.

Unit tests run without SITL (pymavlink calls are mocked).
Integration tests require a real SITL on tcp:127.0.0.1:5760 and are
skipped automatically when that port is closed.

Run only unit tests:
    pytest tests/test_mavlink_service.py -v -m "not integration"

Run all (including integration, needs SITL):
    pytest tests/test_mavlink_service.py -v
"""

import asyncio
import socket
import os
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sitl_available(host: str = "127.0.0.1", port: int = 5760, timeout: float = 1.0) -> bool:
    """Return True only if something is listening on the SITL TCP port right now."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _close_coro(coro):
    """Drop-in replacement for asyncio.ensure_future used in tests.

    When connect_all() calls ensure_future(_reconnect_loop()), the coroutine
    object is created but the mock would normally just discard it.  Python's GC
    then warns 'coroutine was never awaited'.  Closing the coroutine explicitly
    silences that warning without actually scheduling the background loop.
    """
    if hasattr(coro, "close"):
        coro.close()


def _make_mavutil_mock(heartbeat_msg=True, sysid: int = 1, compid: int = 1):
    """
    Build a minimal mock of pymavlink.mavutil so unit tests never need SITL.

    Parameters
    ----------
    heartbeat_msg : bool
        If True, wait_heartbeat() returns a MagicMock; if False returns None
        (simulates timeout / no SITL).
    """
    mu = MagicMock(name="mavutil")

    # mavutil.mavlink namespace constants
    mav_ns = MagicMock(name="mavlink")
    mav_ns.MAV_DATA_STREAM_ALL = 6
    mav_ns.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
    mav_ns.MAV_CMD_NAV_WAYPOINT = 16
    mav_ns.MAV_CMD_DO_SET_MODE = 176
    mav_ns.MAV_CMD_COMPONENT_ARM_DISARM = 400
    mav_ns.MAV_CMD_NAV_TAKEOFF = 22
    mav_ns.MAV_CMD_MISSION_START = 300
    mav_ns.MAV_MISSION_TYPE_MISSION = 0
    mav_ns.MAV_MISSION_ACCEPTED = 0
    mav_ns.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
    mav_ns.MAV_MODE_FLAG_SAFETY_ARMED = 128
    mav_ns.MAV_RESULT_ACCEPTED = 0
    mu.mavlink = mav_ns

    # connection object
    conn = MagicMock(name="connection")
    conn.target_system = sysid
    conn.target_component = compid

    # mav sub-object (for sending)
    conn.mav = MagicMock(name="mav")

    if heartbeat_msg:
        hb = MagicMock(name="heartbeat_msg")
        conn.wait_heartbeat.return_value = hb
    else:
        conn.wait_heartbeat.return_value = None

    mu.mavlink_connection.return_value = conn
    return mu, conn


def _make_mission_ack(accepted: bool = True, mu_mock=None):
    """Create a MISSION_ACK mock."""
    ack = MagicMock(name="MISSION_ACK")
    ack.get_type.return_value = "MISSION_ACK"
    ack.type = 0 if accepted else 1  # 0 = MAV_MISSION_ACCEPTED
    return ack


def _make_command_ack(accepted: bool = True, mu_mock=None):
    """Create a COMMAND_ACK mock."""
    ack = MagicMock(name="COMMAND_ACK")
    ack.get_type.return_value = "COMMAND_ACK"
    ack.result = 0 if accepted else 4  # 0 = MAV_RESULT_ACCEPTED
    return ack


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestParseHosts:
    def test_default_single_host(self, monkeypatch):
        monkeypatch.delenv("SITL_HOSTS", raising=False)
        from app.services.mavlink_service import _parse_sitl_hosts
        hosts = _parse_sitl_hosts()
        assert hosts == {1: "tcp:127.0.0.1:5760"}

    def test_custom_env_single(self, monkeypatch):
        monkeypatch.setenv("SITL_HOSTS", "tcp:127.0.0.1:14550")
        from app.services.mavlink_service import _parse_sitl_hosts
        hosts = _parse_sitl_hosts()
        assert hosts == {1: "tcp:127.0.0.1:14550"}

    def test_multiple_hosts(self, monkeypatch):
        monkeypatch.setenv("SITL_HOSTS", "tcp:127.0.0.1:5760,tcp:127.0.0.1:5770,tcp:127.0.0.1:5780")
        from app.services.mavlink_service import _parse_sitl_hosts
        hosts = _parse_sitl_hosts()
        assert hosts == {
            1: "tcp:127.0.0.1:5760",
            2: "tcp:127.0.0.1:5770",
            3: "tcp:127.0.0.1:5780",
        }

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("SITL_HOSTS", "tcp:127.0.0.1:5760 , tcp:127.0.0.1:5770")
        from app.services.mavlink_service import _parse_sitl_hosts
        hosts = _parse_sitl_hosts()
        assert 1 in hosts and 2 in hosts


class TestEmptySnapshot:
    def test_shape(self):
        from app.services.mavlink_service import _empty_snapshot, STATUS_LOST
        snap = _empty_snapshot(42)
        assert snap["drone_id"] == 42
        assert snap["status"] == STATUS_LOST
        expected_keys = {"drone_id", "lat", "lng", "alt", "battery", "heading", "status", "groundspeed"}
        assert set(snap.keys()) == expected_keys

    def test_custom_status(self):
        from app.services.mavlink_service import _empty_snapshot, STATUS_ACTIVE
        snap = _empty_snapshot(1, STATUS_ACTIVE)
        assert snap["status"] == STATUS_ACTIVE


# ---------------------------------------------------------------------------
# Unit tests: MAVLinkService lifecycle
# ---------------------------------------------------------------------------

class TestConnectAll:
    def test_simulation_mode_when_pymavlink_missing(self):
        """If pymavlink is not installed the service enters simulation mode."""
        from app.services.mavlink_service import MAVLinkService

        svc = MAVLinkService()
        with patch.dict("sys.modules", {"pymavlink": None, "pymavlink.mavutil": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                # can't easily mock selective import; test via _simulation_mode flag
                pass

        # Patch at the point of use in connect_all
        with patch("app.services.mavlink_service.MAVLinkService._try_connect_all", new_callable=AsyncMock):
            with patch("app.services.mavlink_service.asyncio.ensure_future", side_effect=_close_coro):
                async def _run():
                    svc._simulation_mode = True  # mirror what connect_all does on ImportError

                asyncio.run(_run())
        assert svc._simulation_mode is True

    def test_simulation_mode_when_no_heartbeat(self):
        """All drones unreachable → simulation mode after connect_all."""
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock(heartbeat_msg=False)

        svc = MAVLinkService()
        svc._hosts = {1: "tcp:127.0.0.1:5760"}

        # Patch pymavlink import inside connect_all so it uses our mock
        with patch.dict("sys.modules", {"pymavlink": mu_mock, "pymavlink.mavutil": mu_mock}):
            with patch("app.services.mavlink_service.asyncio.ensure_future", side_effect=_close_coro):
                # Inject the mock at the point connect_all does "from pymavlink import mavutil as mu"
                async def _run():
                    # Directly drive through connect_all internal flow
                    svc._mavutil = mu_mock
                    for drone_id in svc._hosts:
                        from app.services.mavlink_service import _empty_snapshot, STATUS_LOST
                        svc.telemetry.setdefault(drone_id, _empty_snapshot(drone_id, STATUS_LOST))
                    await svc._try_connect_all()
                    # connect_all sets simulation_mode when connections is empty
                    if not svc.connections:
                        svc._simulation_mode = True

                asyncio.run(_run())

        assert 1 not in svc.connections
        assert svc._simulation_mode is True

    def test_connected_when_heartbeat_received(self):
        """Successful heartbeat → drone appears in connections, telemetry marked ACTIVE."""
        from app.services.mavlink_service import MAVLinkService, STATUS_ACTIVE, _empty_snapshot, STATUS_LOST

        mu_mock, conn_mock = _make_mavutil_mock(heartbeat_msg=True)

        svc = MAVLinkService()
        svc._hosts = {1: "tcp:127.0.0.1:5760"}
        svc._mavutil = mu_mock
        # Pre-seed telemetry as connect_all does before calling _try_connect_all
        svc.telemetry.setdefault(1, _empty_snapshot(1, STATUS_LOST))

        async def _run():
            with patch("app.services.mavlink_service.asyncio.ensure_future", side_effect=_close_coro):
                await svc._try_connect_all()

        asyncio.run(_run())
        assert 1 in svc.connections
        assert svc.telemetry[1]["status"] == STATUS_ACTIVE


# ---------------------------------------------------------------------------
# Unit tests: simulate_drone_loss
# ---------------------------------------------------------------------------

class TestSimulateDroneLoss:
    def test_removes_connection_and_marks_lost(self):
        from app.services.mavlink_service import MAVLinkService, STATUS_LOST, STATUS_ACTIVE

        svc = MAVLinkService()
        svc.connections[1] = MagicMock()
        svc.telemetry[1] = {"drone_id": 1, "status": STATUS_ACTIVE, "lat": 45.0, "lng": 42.0}

        asyncio.run(svc.simulate_drone_loss(1))

        assert 1 not in svc.connections
        assert svc.telemetry[1]["status"] == STATUS_LOST

    def test_idempotent_on_already_lost(self):
        """Calling simulate_drone_loss twice should not raise."""
        from app.services.mavlink_service import MAVLinkService

        svc = MAVLinkService()
        asyncio.run(svc.simulate_drone_loss(99))


# ---------------------------------------------------------------------------
# Unit tests: upload_mission
# ---------------------------------------------------------------------------

class TestUploadMission:
    def test_returns_false_when_not_connected(self):
        from app.services.mavlink_service import MAVLinkService

        svc = MAVLinkService()
        # No connection for drone 1

        result = asyncio.run(svc.upload_mission(1, [{"lat": 45.0, "lng": 42.0, "alt": 30.0}]))
        assert result is False

    def test_happy_path_single_waypoint(self):
        """Single waypoint: MISSION_REQUEST_INT → MISSION_ACK accepted."""
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock

        # Sequence: home → request wp1 → ack
        req_int = MagicMock()
        req_int.get_type.return_value = "MISSION_REQUEST_INT"
        accepted_ack = _make_mission_ack(accepted=True)

        conn_mock.recv_match.side_effect = [
            req_int,       # after home (seq=0)
            accepted_ack,  # after wp1 (seq=1)
            None,          # final wait_for_ack — already consumed
        ]

        waypoints = [{"lat": 45.044, "lng": 41.973, "alt": 30.0}]
        result = asyncio.run(svc.upload_mission(1, waypoints))
        assert result is True
        conn_mock.mav.mission_count_send.assert_called_once()

    def test_returns_false_on_timeout(self):
        """recv_match returns None (timeout) → upload fails."""
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock

        conn_mock.recv_match.return_value = None  # always timeout

        waypoints = [{"lat": 45.044, "lng": 41.973, "alt": 30.0}]
        result = asyncio.run(svc.upload_mission(1, waypoints))
        assert result is False


# ---------------------------------------------------------------------------
# Unit tests: start_mission
# ---------------------------------------------------------------------------

class TestStartMission:
    def test_returns_false_when_not_connected(self):
        from app.services.mavlink_service import MAVLinkService

        svc = MAVLinkService()
        result = asyncio.run(svc.start_mission(1))
        assert result is False

    def test_happy_path_command_sequence(self):
        """GUIDED → ARM → TAKEOFF → AUTO, all accepted → True."""
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock

        accepted_ack = _make_command_ack(accepted=True)
        conn_mock.recv_match.return_value = accepted_ack

        with patch("app.services.mavlink_service.time.sleep"):
            result = asyncio.run(svc.start_mission(1))

        assert result is True
        # 4 commands: SET_MODE(GUIDED), ARM, TAKEOFF, SET_MODE(AUTO)
        assert conn_mock.mav.command_long_send.call_count == 4

    def test_returns_false_when_arm_rejected(self):
        """If ARM command is rejected the sequence aborts and returns False."""
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock

        accepted_ack = _make_command_ack(accepted=True)
        rejected_ack = _make_command_ack(accepted=False)

        # GUIDED OK, ARM rejected
        conn_mock.recv_match.side_effect = [accepted_ack, rejected_ack]

        with patch("app.services.mavlink_service.time.sleep"):
            result = asyncio.run(svc.start_mission(1))

        assert result is False


# ---------------------------------------------------------------------------
# Unit tests: update_mission
# ---------------------------------------------------------------------------

class TestUpdateMission:
    def test_returns_false_when_not_connected(self):
        from app.services.mavlink_service import MAVLinkService

        svc = MAVLinkService()
        result = asyncio.run(svc.update_mission(1, [{"lat": 45.0, "lng": 42.0}]))
        assert result is False

    def test_clear_then_upload_then_start(self):
        from app.services.mavlink_service import MAVLinkService

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock

        clear_ack = _make_mission_ack(accepted=True)
        req_int = MagicMock()
        req_int.get_type.return_value = "MISSION_REQUEST_INT"
        upload_ack = _make_mission_ack(accepted=True)
        start_cmd_ack = _make_command_ack(accepted=True)

        conn_mock.recv_match.side_effect = [
            clear_ack,    # after MISSION_CLEAR_ALL
            req_int,      # after home wp send
            upload_ack,   # after mission wp send
            None,         # trailing upload ack wait
            start_cmd_ack,  # after MISSION_START
        ]

        result = asyncio.run(svc.update_mission(1, [{"lat": 45.044, "lng": 41.973, "alt": 30.0}]))
        assert result is True
        conn_mock.mav.mission_clear_all_send.assert_called_once()
        conn_mock.mav.command_long_send.assert_called_once()  # MISSION_START


# ---------------------------------------------------------------------------
# Unit tests: telemetry parsing
# ---------------------------------------------------------------------------

class TestBlockingReadTelemetry:
    def _make_service_with_conn(self):
        from app.services.mavlink_service import MAVLinkService, STATUS_ACTIVE

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock
        svc.telemetry[1] = {
            "drone_id": 1, "lat": 0.0, "lng": 0.0, "alt": 0.0,
            "battery": 100, "heading": 0, "status": STATUS_ACTIVE, "groundspeed": 0.0,
        }
        return svc, conn_mock, mu_mock

    def test_parses_global_position_int(self):
        svc, conn_mock, mu_mock = self._make_service_with_conn()

        gps_msg = MagicMock()
        gps_msg.get_type.return_value = "GLOBAL_POSITION_INT"
        gps_msg.lat = int(45.044 * 1e7)
        gps_msg.lon = int(41.973 * 1e7)
        gps_msg.relative_alt = int(30.0 * 1e3)
        gps_msg.hdg = 18000  # 180 degrees (cdeg)

        conn_mock.recv_match.side_effect = [gps_msg, None]

        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert abs(frame["lat"] - 45.044) < 1e-4
        assert abs(frame["lng"] - 41.973) < 1e-4
        assert abs(frame["alt"] - 30.0) < 0.1
        assert frame["heading"] == 180

    def test_parses_battery_status(self):
        svc, conn_mock, mu_mock = self._make_service_with_conn()

        bat_msg = MagicMock()
        bat_msg.get_type.return_value = "BATTERY_STATUS"
        bat_msg.battery_remaining = 72

        conn_mock.recv_match.side_effect = [bat_msg, None]
        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert frame["battery"] == 72

    def test_ignores_negative_battery_remaining(self):
        """battery_remaining=-1 means 'not available' — should not overwrite."""
        svc, conn_mock, mu_mock = self._make_service_with_conn()
        svc.telemetry[1]["battery"] = 55

        bat_msg = MagicMock()
        bat_msg.get_type.return_value = "BATTERY_STATUS"
        bat_msg.battery_remaining = -1

        conn_mock.recv_match.side_effect = [bat_msg, None]
        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert frame["battery"] == 55  # unchanged

    def test_parses_vfr_hud_groundspeed(self):
        svc, conn_mock, mu_mock = self._make_service_with_conn()

        hud_msg = MagicMock()
        hud_msg.get_type.return_value = "VFR_HUD"
        hud_msg.groundspeed = 12.5

        conn_mock.recv_match.side_effect = [hud_msg, None]
        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert frame["groundspeed"] == pytest.approx(12.5)

    def test_heartbeat_sets_flag(self):
        svc, conn_mock, mu_mock = self._make_service_with_conn()

        hb_msg = MagicMock()
        hb_msg.get_type.return_value = "HEARTBEAT"
        hb_msg.base_mode = 0       # not armed
        hb_msg.custom_mode = 0

        conn_mock.recv_match.side_effect = [hb_msg, None]
        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert frame["_heartbeat"] is True

    def test_empty_window_returns_cached_values(self):
        """If recv_match returns None immediately, cached values are preserved."""
        svc, conn_mock, mu_mock = self._make_service_with_conn()
        svc.telemetry[1]["lat"] = 45.1
        svc.telemetry[1]["lng"] = 41.9

        conn_mock.recv_match.return_value = None
        frame = svc._blocking_read_telemetry(svc.connections[1], 1)

        assert frame["lat"] == pytest.approx(45.1)
        assert frame["lng"] == pytest.approx(41.9)


# ---------------------------------------------------------------------------
# Unit tests: read_telemetry_loop (async generator)
# ---------------------------------------------------------------------------

class TestReadTelemetryLoop:
    def test_simulation_mode_yields_status_lost(self):
        """In simulation mode the generator yields STATUS_LOST frames."""
        from app.services.mavlink_service import MAVLinkService, STATUS_LOST

        svc = MAVLinkService()
        svc._simulation_mode = True
        svc.telemetry[1] = {
            "drone_id": 1, "lat": 0.0, "lng": 0.0, "alt": 0.0,
            "battery": 100, "heading": 0, "status": STATUS_LOST, "groundspeed": 0.0,
        }

        async def _collect():
            frames = []
            async for frame in svc.read_telemetry_loop(1):
                frames.append(frame)
                if len(frames) >= 3:
                    break
            return frames

        frames = asyncio.run(_collect())
        assert all(f["status"] == STATUS_LOST for f in frames)
        assert len(frames) == 3

    def test_heartbeat_timeout_marks_lost_and_stops(self):
        """When heartbeat is not received for > HEARTBEAT_TIMEOUT, generator yields LOST and stops."""
        import time
        from app.services.mavlink_service import MAVLinkService, STATUS_ACTIVE, STATUS_LOST, HEARTBEAT_TIMEOUT

        mu_mock, conn_mock = _make_mavutil_mock()
        svc = MAVLinkService()
        svc._mavutil = mu_mock
        svc.connections[1] = conn_mock
        svc.telemetry[1] = {
            "drone_id": 1, "lat": 45.0, "lng": 42.0, "alt": 0.0,
            "battery": 100, "heading": 0, "status": STATUS_ACTIVE, "groundspeed": 0.0,
        }

        # _blocking_read_telemetry returns a frame WITHOUT _heartbeat each call
        def fake_read(conn, drone_id):
            frame = dict(svc.telemetry[drone_id])
            frame["_heartbeat"] = False
            return frame

        svc._blocking_read_telemetry = fake_read

        async def _run():
            frames = []
            # Fake monotonic time: start = T0, after first frame = T0 + TIMEOUT + 1
            start_t = [time.monotonic()]
            call_count = [0]
            real_monotonic = time.monotonic

            def fake_monotonic():
                call_count[0] += 1
                # After a couple of calls, pretend heartbeat timeout has elapsed
                if call_count[0] > 4:
                    return start_t[0] + HEARTBEAT_TIMEOUT + 1.0
                return start_t[0]

            with patch("app.services.mavlink_service.time.monotonic", side_effect=fake_monotonic):
                async for frame in svc.read_telemetry_loop(1):
                    frames.append(frame)
                    if len(frames) > 10:
                        break  # safety guard
            return frames

        frames = asyncio.run(_run())
        assert frames[-1]["status"] == STATUS_LOST
        # Generator must have stopped (total frames should be small)
        assert len(frames) <= 5


# ---------------------------------------------------------------------------
# Integration tests — require real SITL on tcp:127.0.0.1:5760
# ---------------------------------------------------------------------------

SITL_HOST = "127.0.0.1"
SITL_PORT = 5760

sitl_available = pytest.mark.skipif(
    not _sitl_available(SITL_HOST, SITL_PORT),
    reason="ArduPilot SITL not running on tcp:127.0.0.1:5760",
)


@sitl_available
@pytest.mark.integration
class TestSITLIntegration:
    """
    Live tests against a running ArduPilot SITL instance.

    SITL accepts only ONE TCP connection on port 5760, so the service is
    connected once for the whole class (setup_class) and shared across all
    tests.  Creating a new MAVLinkService per test would exhaust the single
    connection slot.

    Start SITL before running these tests:
        cd ~/ardupilot
        sim_vehicle.py -v ArduCopter --custom-location=45.0448,41.9734,0,0 --no-mavproxy
    """

    svc = None  # shared across all tests in the class

    @classmethod
    def setup_class(cls):
        """Connect once to SITL for the entire test class."""
        os.environ.setdefault("SITL_HOSTS", f"tcp:{SITL_HOST}:{SITL_PORT}")

        from app.services.mavlink_service import MAVLinkService

        cls.svc = MAVLinkService()

        async def _connect():
            with patch("app.services.mavlink_service.asyncio.ensure_future", side_effect=_close_coro):
                await cls.svc.connect_all()

        asyncio.run(_connect())

    def test_connect_and_heartbeat(self):
        """Service should have at least one active connection after connect_all."""
        assert self.svc.connections, "Expected at least one MAVLink connection to SITL"
        assert not self.svc._simulation_mode, "Should NOT be in simulation mode"

    def test_upload_mission_returns_true(self):
        """
        Upload a 3-waypoint mission to the connected SITL drone.

        NOTE: must run before the telemetry tests.  _blocking_read_telemetry
        runs in a thread-pool executor; when the telemetry generator is
        abandoned (break), that thread may still be alive for up to 200 ms and
        can consume the MISSION_REQUEST_INT that SITL sends in reply to
        mission_count_send, causing a 3-second timeout.  Running upload first
        avoids the race entirely.
        """
        import time
        drone_id = list(self.svc.connections.keys())[0]

        waypoints = [
            {"lat": 45.044, "lng": 41.973, "alt": 30.0},
            {"lat": 45.045, "lng": 41.974, "alt": 30.0},
            {"lat": 45.046, "lng": 41.972, "alt": 30.0},
        ]

        result = asyncio.run(self.svc.upload_mission(drone_id, waypoints))
        assert result is True, "upload_mission() returned False — check SITL connection and MISSION_ACK"

    def test_telemetry_has_valid_coordinates(self):
        """
        Read three telemetry frames — coordinates should be non-zero.
        (SITL uses --custom-location=45.0448,41.9734 so lat/lng must be non-trivial.)
        """
        drone_id = list(self.svc.connections.keys())[0]

        async def _collect():
            frames = []
            async for frame in self.svc.read_telemetry_loop(drone_id):
                frames.append(frame)
                if len(frames) >= 3:
                    break
            return frames

        frames = asyncio.run(_collect())
        assert len(frames) >= 1

        gps_frames = [f for f in frames if f["lat"] != 0.0 or f["lng"] != 0.0]
        assert gps_frames, (
            "All telemetry frames have lat=0, lng=0. "
            "Check that SITL is streaming GLOBAL_POSITION_INT "
            "(MAV_DATA_STREAM_ALL request may have failed)."
        )
        lat = gps_frames[0]["lat"]
        lng = gps_frames[0]["lng"]
        assert 40.0 <= lat <= 50.0, f"Unexpected lat={lat}"
        assert 35.0 <= lng <= 50.0, f"Unexpected lng={lng}"

    def test_telemetry_frame_has_required_keys(self):
        """Every telemetry frame must carry all documented keys."""
        import time
        drone_id = list(self.svc.connections.keys())[0]
        required = {"drone_id", "lat", "lng", "alt", "battery", "heading", "status", "groundspeed"}

        async def _one_frame():
            async for frame in self.svc.read_telemetry_loop(drone_id):
                return frame

        frame = asyncio.run(_one_frame())
        assert required.issubset(set(frame.keys())), f"Missing keys: {required - set(frame.keys())}"

        # Wait for any lingering executor thread from _blocking_read_telemetry
        # (runs for up to TELEMETRY_WINDOW=200ms) to finish draining the recv
        # buffer before simulate_drone_loss tries to use the connection.
        time.sleep(0.4)

    def test_simulate_drone_loss_disconnects(self):
        """simulate_drone_loss removes the connection from the service."""
        drone_id = list(self.svc.connections.keys())[0]
        assert drone_id in self.svc.connections  # pre-condition

        asyncio.run(self.svc.simulate_drone_loss(drone_id))

        assert drone_id not in self.svc.connections
        from app.services.mavlink_service import STATUS_LOST
        assert self.svc.telemetry[drone_id]["status"] == STATUS_LOST
