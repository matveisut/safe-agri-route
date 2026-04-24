import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from pydantic import ValidationError
from app.schemas.mission import DroneRoute, PlanMissionResponse
from app.services.mavlink_service import mavlink_service
from app.services.mission_fusion_runtime import (
    get_dynamic_zones_snapshot,
    get_fusion_snapshot,
)

router = APIRouter(tags=["Telemetry"])


class TelemetryStartPayload(BaseModel):
    routes: list[DroneRoute]
    irm: float | None = None   # initial IRM forwarded from /plan; echoed back in first frame


class MissionTelemetryStartPayload(BaseModel):
    protocol: str = "v1"
    mode: str = "simulation"  # simulation | live
    routes: list[DroneRoute] = Field(default_factory=list)
    irm: float | None = None


def _build_sim_frame(
    routes: list[DroneRoute],
    step_idx: int,
    initial_irm: float | None = None,
) -> dict:
    telemetry: list[dict] = []
    for dr in routes:
        if not dr.route:
            telemetry.append(
                {
                    "drone_id": dr.drone_id,
                    "lat": 0.0,
                    "lng": 0.0,
                    "status": "idle",
                }
            )
            continue
        if step_idx < len(dr.route):
            pt = dr.route[step_idx]
            telemetry.append(
                {
                    "drone_id": dr.drone_id,
                    "lat": pt.lat,
                    "lng": pt.lng,
                    "status": "in_flight",
                }
            )
        else:
            pt = dr.route[-1]
            telemetry.append(
                {
                    "drone_id": dr.drone_id,
                    "lat": pt.lat,
                    "lng": pt.lng,
                    "status": "idle",
                }
            )

    frame: dict = {
        "protocol": "v1",
        "source": "simulation",
        "telemetry": telemetry,
        "fusion_by_drone": {},
        "dynamic_zones": [],
        "message": None,
    }
    if step_idx == 0 and initial_irm is not None:
        frame["irm_update"] = initial_irm
    return frame


def _build_live_frame(
    telemetry: list[dict],
    fusion_by_drone: dict,
    dynamic_zones: list[dict] | None = None,
    initial_irm: float | None = None,
    include_irm: bool = False,
) -> dict:
    frame: dict = {
        "protocol": "v1",
        "source": "live",
        "telemetry": telemetry,
        "fusion_by_drone": fusion_by_drone,
        "dynamic_zones": dynamic_zones or [],
        "message": None,
    }
    if include_irm and initial_irm is not None:
        frame["irm_update"] = initial_irm
    return frame


def _extract_drone_ids_for_live(payload: MissionTelemetryStartPayload) -> list[int]:
    route_ids = [dr.drone_id for dr in payload.routes]
    cache_ids = list(mavlink_service.telemetry.keys())
    host_ids = list(getattr(mavlink_service, "_hosts", {}).keys())
    ids = sorted(set(route_ids + cache_ids + host_ids))
    return ids or [1]


@router.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    """
    Deprecated thin-wrapper for simulation telemetry.

    Simulates drone telemetry based on pre-planned routes.
    Expects client to send the PlanMissionResponse (List of DroneRoute).
    It then streams coordinates for each drone back to client.
    """
    await websocket.accept()
    
    try:
        # Wait for client to send the route plan
        data = await websocket.receive_json()
        
        initial_irm: float | None = None
        try:
            # Preferred payload from plan endpoint response
            plan = PlanMissionResponse(**data)
            routes = plan.routes
            initial_irm = plan.reliability_index
        except ValidationError:
            try:
                # Backward-compatible payload: {"routes": [...], "irm": 0.9}
                payload = TelemetryStartPayload(**data)
                routes = payload.routes
                initial_irm = payload.irm
            except ValidationError:
                await websocket.send_json({"error": "Invalid route plan format"})
                return
            
        if not routes:
            await websocket.send_json({"message": "Empty routes provided"})
            return

        # Find the max route length to know when to stop simulating
        max_points = max([len(dr.route) for dr in routes])
        
        for step_idx in range(max_points):
            payload = []

            for dr in routes:
                if step_idx < len(dr.route):
                    pt = dr.route[step_idx]
                    payload.append({
                        "drone_id": dr.drone_id,
                        "lat": pt.lat,
                        "lng": pt.lng,
                        "status": "in_flight"
                    })
                else:
                    # Drone finished its route
                    pt = dr.route[-1]
                    payload.append({
                        "drone_id": dr.drone_id,
                        "lat": pt.lat,
                        "lng": pt.lng,
                        "status": "idle"
                    })

            # irm_update is non-null only in the first frame so the frontend can
            # initialise its IRM display without a separate REST call.
            frame: dict = {"telemetry": payload}
            if step_idx == 0 and initial_irm is not None:
                frame["irm_update"] = initial_irm

            await websocket.send_json(frame)
            await asyncio.sleep(0.1)  # 100 ms per frame → smooth animation
            
        await websocket.send_json({"message": "Mission Completed"})

    except WebSocketDisconnect:
        print("Telemetry Client disconnected")
    except Exception as e:
        print(f"Error in telemetry websocket: {e}")


@router.websocket("/ws/telemetry/mission")
async def mission_telemetry_stream(websocket: WebSocket):
    """
    Unified mission telemetry stream for both simulation and live modes.

    Expected handshake payload:
    {
        "protocol": "v1",
        "mode": "simulation" | "live",
        "routes": [...],
        "irm": <float optional>
    }
    """
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        payload = MissionTelemetryStartPayload(**data)
        if payload.protocol != "v1":
            await websocket.send_json({"error": "Unsupported protocol"})
            return
        if payload.mode not in {"simulation", "live"}:
            await websocket.send_json({"error": "Invalid mode"})
            return

        if payload.mode == "simulation":
            if not payload.routes:
                await websocket.send_json({"error": "Simulation mode requires routes"})
                return
            max_points = max([len(dr.route) for dr in payload.routes], default=0)
            for step_idx in range(max_points):
                frame = _build_sim_frame(payload.routes, step_idx, payload.irm)
                await websocket.send_json(frame)
                await asyncio.sleep(0.1)
            await websocket.send_json(
                {
                    "protocol": "v1",
                    "source": "simulation",
                    "telemetry": [],
                    "fusion_by_drone": {},
                    "dynamic_zones": [],
                    "message": "Mission Completed",
                }
            )
            return

        drone_ids = _extract_drone_ids_for_live(payload)
        generators = {did: mavlink_service.read_telemetry_loop(did) for did in drone_ids}
        sent_first_frame = False

        while True:
            telemetry: list[dict] = []
            for did, gen in generators.items():
                try:
                    frame = await anext(gen)
                except StopAsyncIteration:
                    frame = {
                        "drone_id": did,
                        "lat": 0.0,
                        "lng": 0.0,
                        "alt": 0.0,
                        "battery": 0,
                        "heading": 0,
                        "status": "LOST",
                        "groundspeed": 0.0,
                    }
                telemetry.append(frame)

            fusion_by_drone: dict[str, dict] = {}
            for item in telemetry:
                did = int(item["drone_id"])
                fusion = get_fusion_snapshot(did)
                if fusion is not None:
                    fusion_by_drone[str(did)] = fusion

            out = _build_live_frame(
                telemetry=telemetry,
                fusion_by_drone=fusion_by_drone,
                dynamic_zones=get_dynamic_zones_snapshot(),
                initial_irm=payload.irm,
                include_irm=not sent_first_frame,
            )
            await websocket.send_json(out)
            sent_first_frame = True
            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        pass
    except ValidationError:
        await websocket.send_json({"error": "Invalid mission telemetry handshake"})
    except Exception as exc:
        print(f"Mission telemetry WS error: {exc}")


@router.websocket("/ws/telemetry/{drone_id}")
async def mavlink_telemetry_websocket(websocket: WebSocket, drone_id: int):
    """
    Deprecated thin-wrapper for single-drone live telemetry.

    Streams live MAVLink telemetry for a single drone every ~200 ms.

    When SITL is connected the frames contain real GPS/battery data from
    ArduPilot.  When SITL is unreachable the endpoint streams the last-known
    cached snapshot with status=LOST so the frontend can react.

    Frame schema
    ------------
    {
        "drone_id": int,
        "lat":        float,
        "lng":        float,
        "alt":        float,   # metres AGL
        "battery":    int,     # 0-100 %
        "heading":    int,     # 0-360 degrees
        "status":     str,     # ACTIVE | LOST | LANDED | RTL
        "groundspeed": float
    }
    """
    await websocket.accept()
    try:
        async for frame in mavlink_service.read_telemetry_loop(drone_id):
            out = dict(frame)
            fusion = get_fusion_snapshot(drone_id)
            if fusion is not None:
                out["fusion"] = fusion
            await websocket.send_json(out)
            if frame.get("status") == "LOST":
                # Notify frontend and close — caller should trigger replanner
                await websocket.send_json({"event": "drone_lost", "drone_id": drone_id})
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"MAVLink telemetry WS error (drone {drone_id}): {exc}")
