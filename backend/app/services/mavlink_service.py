"""
mavlink_service.py — MAVLink integration layer for SafeAgriRoute.

Manages real-time communication with ArduPilot SITL instances (or real drones).
All blocking pymavlink calls are dispatched to a thread-pool executor so the
FastAPI event loop is never blocked.

Configuration
-------------
SITL_HOSTS env var (comma-separated connection strings):
    SITL_HOSTS=tcp:127.0.0.1:14550,tcp:127.0.0.1:14560,tcp:127.0.0.1:14570

drone_id=1 → first address, drone_id=2 → second, etc.
"""

import asyncio
import logging
import os
import time
from typing import AsyncGenerator, Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# ArduCopter custom mode numbers
COPTER_MODE_GUIDED = 4
COPTER_MODE_AUTO = 3

# Timing
HEARTBEAT_TIMEOUT = 5.0   # seconds until drone is declared LOST
TELEMETRY_WINDOW = 0.2    # seconds per telemetry read window (200 ms)
RECONNECT_INTERVAL = 10.0 # seconds between reconnect attempts for offline drones

# Drone status strings
STATUS_ACTIVE = "ACTIVE"
STATUS_LOST = "LOST"
STATUS_LANDED = "LANDED"
STATUS_RTL = "RTL"


def _parse_sitl_hosts() -> Dict[int, str]:
    """Parse SITL_HOSTS into {drone_id: connection_string}.

    Default port 5760 = ArduPilot SITL SERIAL0 (used when running sim_vehicle.py
    with --no-mavproxy).  Set SITL_HOSTS=tcp:127.0.0.1:14550 if using MAVProxy.
    """
    raw = os.environ.get("SITL_HOSTS", "tcp:127.0.0.1:5760")
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return {idx + 1: host for idx, host in enumerate(hosts)}


class MAVLinkService:
    """
    Singleton service that owns all MAVLink connections.

    Usage
    -----
    On app startup::

        await mavlink_service.connect_all()

    Then in route handlers::

        ok = await mavlink_service.upload_mission(drone_id, waypoints)
        ok = await mavlink_service.start_mission(drone_id)
        async for frame in mavlink_service.read_telemetry_loop(drone_id):
            ...
    """

    def __init__(self) -> None:
        self._hosts: Dict[int, str] = _parse_sitl_hosts()
        # drone_id → mavutil.mavlink_connection (or None when SITL unreachable)
        self.connections: Dict[int, Any] = {}
        # drone_id → latest telemetry snapshot (always populated for all known drones)
        self.telemetry: Dict[int, Dict] = {}
        self._mavutil: Optional[Any] = None       # lazy import of pymavlink.mavutil
        self._simulation_mode: bool = False        # True when pymavlink absent / no SITL

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect_all(self) -> None:
        """
        Connect to every SITL host from SITL_HOSTS.

        Hosts that fail to return a heartbeat within HEARTBEAT_TIMEOUT seconds
        are skipped; the service falls back to simulation mode for those drones.
        A background reconnect task keeps retrying every RECONNECT_INTERVAL seconds
        so SITL can be started after the backend without restarting uvicorn.
        """
        try:
            from pymavlink import mavutil as mu  # noqa: F401
            self._mavutil = mu
        except ImportError:
            logger.warning(
                "pymavlink not installed — SITL unavailable, running in simulation mode"
            )
            self._simulation_mode = True
            return

        # Initialise telemetry cache for all known drones
        for drone_id in self._hosts:
            self.telemetry.setdefault(drone_id, _empty_snapshot(drone_id, STATUS_LOST))

        await self._try_connect_all()

        if not self.connections:
            logger.warning(
                "MAVLink: no SITL instances reachable — simulation mode active"
            )
            self._simulation_mode = True

        # Start background reconnect loop regardless of initial result
        asyncio.ensure_future(self._reconnect_loop())

    async def _try_connect_all(self) -> None:
        """Single pass: attempt connection to every host that isn't connected yet."""
        loop = asyncio.get_event_loop()
        for drone_id, host in self._hosts.items():
            if drone_id in self.connections:
                continue  # already connected
            try:
                conn = await loop.run_in_executor(
                    None, self._blocking_connect, drone_id, host
                )
                if conn is not None:
                    self.connections[drone_id] = conn
                    self.telemetry[drone_id]["status"] = STATUS_ACTIVE
                    self._simulation_mode = False
                    logger.warning("MAVLink: connected to drone %d at %s", drone_id, host)
                else:
                    logger.debug(
                        "MAVLink: drone %d at %s — no heartbeat in %.0fs",
                        drone_id, host, HEARTBEAT_TIMEOUT,
                    )
            except Exception as exc:
                logger.debug("MAVLink: connect to drone %d failed: %s", drone_id, exc)

    async def _reconnect_loop(self) -> None:
        """
        Background task: retry disconnected drones every RECONNECT_INTERVAL seconds.
        Exits cleanly when all hosts are connected.
        """
        while True:
            await asyncio.sleep(RECONNECT_INTERVAL)
            missing = [d for d in self._hosts if d not in self.connections]
            if not missing:
                return  # all connected — stop the loop
            logger.info(
                "MAVLink: retrying connection for drone(s) %s ...", missing
            )
            await self._try_connect_all()
            if self.connections:
                self._simulation_mode = False

    def _blocking_connect(self, drone_id: int, host: str) -> Optional[Any]:
        """
        Blocking: open TCP connection, await first heartbeat, then request
        telemetry data streams so GLOBAL_POSITION_INT etc. start flowing.

        pymavlink prints '[Errno 111] Connection refused sleeping' to stdout
        when the TCP host is down.  We redirect stdout to suppress that noise —
        our caller already logs a clean warning if the connection fails.
        """
        import contextlib, io
        mu = self._mavutil
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                conn = mu.mavlink_connection(host, retries=1)
        except Exception as exc:
            logger.debug("MAVLink: mavlink_connection(%s) raised: %s", host, exc)
            return None
        msg = conn.wait_heartbeat(timeout=HEARTBEAT_TIMEOUT)
        if msg is None:
            return None
        logger.debug(
            "MAVLink: heartbeat from drone %d (sysid=%d)",
            drone_id, conn.target_system,
        )
        # Request all telemetry streams at 10 Hz so GLOBAL_POSITION_INT,
        # BATTERY_STATUS, VFR_HUD etc. start arriving immediately.
        conn.mav.request_data_stream_send(
            conn.target_system,
            conn.target_component,
            mu.mavlink.MAV_DATA_STREAM_ALL,
            10,   # 10 Hz
            1,    # start streaming
        )
        return conn

    # ------------------------------------------------------------------
    # Mission upload
    # ------------------------------------------------------------------

    async def upload_mission(
        self, drone_id: int, waypoints: List[Dict]
    ) -> bool:
        """
        Upload a route to the drone via MISSION_ITEM_INT.

        Parameters
        ----------
        drone_id : int
        waypoints : list of {"lat": float, "lng": float, "alt": float}
            alt defaults to 30 m (relative to home) when omitted.

        Returns
        -------
        bool – True on MISSION_ACK / MAV_MISSION_ACCEPTED.
        """
        conn = self.connections.get(drone_id)
        if conn is None:
            logger.warning("MAVLink: upload_mission — drone %d not connected", drone_id)
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._blocking_upload_mission, conn, drone_id, waypoints
        )

    def _blocking_upload_mission(
        self, conn, drone_id: int, waypoints: List[Dict]
    ) -> bool:
        mu = self._mavutil
        mav = mu.mavlink

        # Total items = home (index 0) + mission waypoints
        total = len(waypoints) + 1

        conn.mav.mission_count_send(
            conn.target_system,
            conn.target_component,
            total,
            mav.MAV_MISSION_TYPE_MISSION,
        )

        home = waypoints[0] if waypoints else {"lat": 0.0, "lng": 0.0, "alt": 30.0}

        def _send_wp(seq: int, wp: Dict) -> bool:
            """Send one MISSION_ITEM_INT and wait for next REQUEST or final ACK.

            ArduPilot ≥ 4.0 uses MISSION_REQUEST_INT; older builds fall back to
            the deprecated MISSION_REQUEST.  We accept both so the upload works
            against any SITL version.
            """
            conn.mav.mission_item_int_send(
                conn.target_system,
                conn.target_component,
                seq,
                mav.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                mav.MAV_CMD_NAV_WAYPOINT,
                0,    # current
                1,    # autocontinue
                0.0, 0.0, 0.0, float("nan"),
                int(wp["lat"] * 1e7),
                int(wp["lng"] * 1e7),
                float(wp.get("alt", 30.0)),
            )
            resp = conn.recv_match(
                type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
                blocking=True,
                timeout=5.0,
            )
            if resp is None:
                logger.error(
                    "MAVLink: upload timeout after seq %d (drone %d)", seq, drone_id
                )
                return False
            if resp.get_type() == "MISSION_ACK":
                return resp.type == mav.MAV_MISSION_ACCEPTED
            # MISSION_REQUEST_INT or MISSION_REQUEST — next item expected
            return True

        # Index 0: home
        if not _send_wp(0, home):
            return False

        # Indices 1..N: actual mission waypoints
        for seq, wp in enumerate(waypoints, start=1):
            if not _send_wp(seq, wp):
                return False

        # If the loop consumed the final MISSION_ACK inside _send_wp we are done,
        # otherwise wait for it now (some firmware sends it after the last item)
        ack = conn.recv_match(type="MISSION_ACK", blocking=True, timeout=5.0)
        if ack and ack.type != mav.MAV_MISSION_ACCEPTED:
            logger.error(
                "MAVLink: MISSION_ACK error %d (drone %d)", ack.type, drone_id
            )
            return False

        logger.info(
            "MAVLink: mission uploaded to drone %d (%d waypoints)",
            drone_id, len(waypoints),
        )
        return True

    # ------------------------------------------------------------------
    # Mission start
    # ------------------------------------------------------------------

    async def start_mission(self, drone_id: int) -> bool:
        """
        Arm the drone and launch the uploaded mission.

        Command sequence
        ----------------
        1. MAV_CMD_DO_SET_MODE → GUIDED  (custom_mode=4)
        2. MAV_CMD_COMPONENT_ARM_DISARM  (arm=1)
        3. MAV_CMD_NAV_TAKEOFF           (alt=30 m)
        4. MAV_CMD_DO_SET_MODE → AUTO    (custom_mode=3)
        """
        conn = self.connections.get(drone_id)
        if conn is None:
            logger.warning("MAVLink: start_mission — drone %d not connected", drone_id)
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._blocking_start_mission, conn, drone_id
        )

    def _blocking_start_mission(self, conn, drone_id: int) -> bool:
        mu = self._mavutil
        mav = mu.mavlink

        def _cmd(command: int, *params) -> bool:
            """Send COMMAND_LONG and wait for COMMAND_ACK."""
            p = list(params) + [0.0] * (7 - len(params))
            conn.mav.command_long_send(
                conn.target_system,
                conn.target_component,
                command, 0,
                float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                float(p[4]), float(p[5]), float(p[6]),
            )
            ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=5.0)
            if ack is None:
                logger.warning(
                    "MAVLink: no COMMAND_ACK for cmd=%d (drone %d)", command, drone_id
                )
                return False
            if ack.result != mav.MAV_RESULT_ACCEPTED:
                logger.warning(
                    "MAVLink: cmd=%d rejected result=%d (drone %d)",
                    command, ack.result, drone_id,
                )
                return False
            return True

        # 1. GUIDED mode
        if not _cmd(
            mav.MAV_CMD_DO_SET_MODE,
            mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            COPTER_MODE_GUIDED,
        ):
            return False

        # 2. Arm
        if not _cmd(mav.MAV_CMD_COMPONENT_ARM_DISARM, 1):
            return False

        # 3. Takeoff to 30 m
        if not _cmd(mav.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 30.0):
            return False

        # Give SITL a moment to reach altitude before switching to AUTO
        time.sleep(2.0)

        # 4. AUTO mode
        if not _cmd(
            mav.MAV_CMD_DO_SET_MODE,
            mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            COPTER_MODE_AUTO,
        ):
            return False

        logger.info("MAVLink: drone %d armed and started", drone_id)
        return True

    # ------------------------------------------------------------------
    # In-flight mission update (called by replanner)
    # ------------------------------------------------------------------

    async def update_mission(
        self, drone_id: int, new_waypoints: List[Dict]
    ) -> bool:
        """
        Replace the mission while the drone is airborne.

        Sequence
        --------
        1. MISSION_CLEAR_ALL
        2. Upload new_waypoints via upload_mission()
        3. MAV_CMD_MISSION_START from index 0
        """
        conn = self.connections.get(drone_id)
        if conn is None:
            logger.warning("MAVLink: update_mission — drone %d not connected", drone_id)
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._blocking_update_mission, conn, drone_id, new_waypoints
        )

    def _blocking_update_mission(
        self, conn, drone_id: int, new_waypoints: List[Dict]
    ) -> bool:
        mu = self._mavutil
        mav = mu.mavlink

        # 1. Clear current mission
        conn.mav.mission_clear_all_send(
            conn.target_system,
            conn.target_component,
            mav.MAV_MISSION_TYPE_MISSION,
        )
        conn.recv_match(type="MISSION_ACK", blocking=True, timeout=3.0)

        # 2. Upload new waypoints
        if not self._blocking_upload_mission(conn, drone_id, new_waypoints):
            return False

        # 3. Resume from first waypoint
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mav.MAV_CMD_MISSION_START, 0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )
        ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=3.0)
        ok = ack is not None and ack.result == mav.MAV_RESULT_ACCEPTED
        if ok:
            logger.info(
                "MAVLink: drone %d mission updated (%d wps)", drone_id, len(new_waypoints)
            )
        else:
            logger.warning("MAVLink: drone %d MISSION_START failed", drone_id)
        return ok

    # ------------------------------------------------------------------
    # Telemetry streaming
    # ------------------------------------------------------------------

    async def read_telemetry_loop(
        self, drone_id: int
    ) -> AsyncGenerator[Dict, None]:
        """
        Async generator that yields a telemetry snapshot every ~200 ms.

        Yielded dict keys
        -----------------
        drone_id, lat, lng, alt, battery, heading, status, groundspeed

        When SITL is unreachable the generator yields the last-known cached
        snapshot indefinitely (callers can detect STATUS_LOST and act).
        """
        if self._simulation_mode or drone_id not in self.connections:
            async for frame in self._simulated_telemetry(drone_id):
                yield frame
            return

        conn = self.connections[drone_id]
        loop = asyncio.get_event_loop()
        last_heartbeat_ts = time.monotonic()

        while True:
            frame = await loop.run_in_executor(
                None, self._blocking_read_telemetry, conn, drone_id
            )

            if frame.pop("_heartbeat", False):
                last_heartbeat_ts = time.monotonic()

            if time.monotonic() - last_heartbeat_ts > HEARTBEAT_TIMEOUT:
                logger.warning(
                    "MAVLink: drone %d heartbeat lost — marking LOST", drone_id
                )
                frame["status"] = STATUS_LOST
                self.telemetry[drone_id].update(frame)
                yield frame
                break  # stop generator; caller can trigger replanner

            self.telemetry[drone_id].update(frame)
            yield frame
            # _blocking_read_telemetry already spent ~200 ms collecting messages;
            # no additional sleep needed here.

    def _blocking_read_telemetry(self, conn, drone_id: int) -> Dict:
        """
        Collect MAVLink messages for TELEMETRY_WINDOW seconds and merge into
        a single snapshot dict.  Runs in a thread-pool executor.
        """
        frame: Dict = dict(self.telemetry.get(drone_id, _empty_snapshot(drone_id)))
        frame["_heartbeat"] = False

        mu = self._mavutil
        mav = mu.mavlink
        deadline = time.monotonic() + TELEMETRY_WINDOW

        while time.monotonic() < deadline:
            remaining = max(0.005, deadline - time.monotonic())
            msg = conn.recv_match(
                type=["GLOBAL_POSITION_INT", "BATTERY_STATUS", "HEARTBEAT", "VFR_HUD"],
                blocking=True,
                timeout=remaining,
            )
            if msg is None:
                break

            mtype = msg.get_type()
            if mtype == "GLOBAL_POSITION_INT":
                frame["lat"] = msg.lat / 1e7
                frame["lng"] = msg.lon / 1e7
                frame["alt"] = msg.relative_alt / 1e3  # mm → m
                frame["heading"] = msg.hdg // 100       # cdeg → deg
            elif mtype == "BATTERY_STATUS":
                if msg.battery_remaining >= 0:
                    frame["battery"] = int(msg.battery_remaining)
            elif mtype == "VFR_HUD":
                frame["groundspeed"] = float(msg.groundspeed)
            elif mtype == "HEARTBEAT":
                frame["_heartbeat"] = True
                armed = bool(
                    msg.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED
                )
                custom = msg.custom_mode
                if not armed:
                    frame["status"] = STATUS_LANDED
                elif custom == COPTER_MODE_AUTO:
                    frame["status"] = STATUS_ACTIVE
                else:
                    frame["status"] = STATUS_ACTIVE

        return frame

    # ------------------------------------------------------------------
    # Simulation fallback
    # ------------------------------------------------------------------

    async def _simulated_telemetry(
        self, drone_id: int
    ) -> AsyncGenerator[Dict, None]:
        """
        Yield the cached snapshot at TELEMETRY_WINDOW intervals when no SITL
        connection exists.  Status is always STATUS_LOST in this path.
        """
        while True:
            cached = dict(self.telemetry.get(drone_id, _empty_snapshot(drone_id)))
            cached.setdefault("status", STATUS_LOST)
            yield cached
            await asyncio.sleep(TELEMETRY_WINDOW)

    # ------------------------------------------------------------------
    # Demo helpers
    # ------------------------------------------------------------------

    async def simulate_drone_loss(self, drone_id: int) -> None:
        """
        Demo: mark a drone LOST without stopping SITL.
        Removes the active connection so read_telemetry_loop switches to
        the simulation fallback on next call.
        """
        self.connections.pop(drone_id, None)
        if drone_id in self.telemetry:
            self.telemetry[drone_id]["status"] = STATUS_LOST
        logger.info("MAVLink: drone %d manually marked as LOST (demo)", drone_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tcp_port_open(host: str, timeout: float = 1.0) -> bool:
    """
    Quick non-blocking check: return True only if the TCP port is accepting
    connections right now.  Parses connection strings like "tcp:127.0.0.1:14550".
    This prevents pymavlink from printing its own "[Errno 111] sleeping" retries.
    """
    import socket

    # Parse "tcp:host:port" or "host:port"
    parts = host.replace("tcp:", "").rsplit(":", 1)
    if len(parts) != 2:
        return False
    ip, port_str = parts
    try:
        port = int(port_str)
    except ValueError:
        return False

    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _empty_snapshot(drone_id: int, status: str = STATUS_LOST) -> Dict:
    return {
        "drone_id": drone_id,
        "lat": 0.0,
        "lng": 0.0,
        "alt": 0.0,
        "battery": 100,
        "heading": 0,
        "status": status,
        "groundspeed": 0.0,
    }


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

mavlink_service = MAVLinkService()