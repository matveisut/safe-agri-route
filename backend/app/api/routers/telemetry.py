import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from pydantic import ValidationError
from app.schemas.mission import DroneRoute, PlanMissionResponse

router = APIRouter(tags=["Telemetry"])


class TelemetryStartPayload(BaseModel):
    routes: list[DroneRoute]

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
        
        try:
            # Preferred payload from plan endpoint response
            plan = PlanMissionResponse(**data)
            routes = plan.routes
        except ValidationError:
            try:
                # Backward-compatible payload used by current frontend: {"routes": [...]}
                payload = TelemetryStartPayload(**data)
                routes = payload.routes
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
            
            await websocket.send_json({"telemetry": payload})
            await asyncio.sleep(0.1) # Send updates every 100ms for fast animation
            
        await websocket.send_json({"message": "Mission Completed"})

    except WebSocketDisconnect:
        print("Telemetry Client disconnected")
    except Exception as e:
        print(f"Error in telemetry websocket: {e}")
