import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from pydantic import ValidationError
from app.schemas.mission import DroneRoute, PlanMissionResponse
from app.services.mavlink_service import mavlink_service
from app.services.mission_fusion_runtime import get_fusion_snapshot

router = APIRouter(tags=["Telemetry"])


class TelemetryStartPayload(BaseModel):
    routes: list[DroneRoute]
    irm: float | None = None   # initial IRM forwarded from /plan; echoed back in first frame

@router.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    """
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


@router.websocket("/ws/telemetry/{drone_id}")
async def mavlink_telemetry_websocket(websocket: WebSocket, drone_id: int):
    """
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
