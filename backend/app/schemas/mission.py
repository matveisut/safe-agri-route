from pydantic import BaseModel, ConfigDict
from typing import List, Dict, Any, Tuple

class PlanMissionRequest(BaseModel):
    field_id: int
    drone_ids: List[int]

class RoutePoint(BaseModel):
    lat: float
    lng: float

class DroneRoute(BaseModel):
    drone_id: int
    route: List[RoutePoint]

class PlanMissionResponse(BaseModel):
    routes: List[DroneRoute]
    
class GridParameters(BaseModel):
    # Base grid step sizes in degrees
    step_deg: float = 0.0002
