from pydantic import BaseModel
from typing import List, Dict, Any

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
    reliability_index: float
    estimated_coverage_pct: float

class GridParameters(BaseModel):
    # Base grid step sizes in degrees
    step_deg: float = 0.0002


# ---------------------------------------------------------------------------
# Dynamic replanning schemas
# ---------------------------------------------------------------------------

class SimulateLossRequest(BaseModel):
    """Body for POST /mission/{id}/simulate-loss"""
    field_id: int
    drone_ids: List[int]                    # all drone IDs in the mission
    current_routes: List[DroneRoute]        # current route state for all drones
    visited_counts: Dict[int, int]          # drone_id → number of waypoints visited

class ReplanResponse(BaseModel):
    """Shared response shape for both replanning endpoints."""
    status: str
    updated_routes: List[DroneRoute]
    new_irm: float

class AddRiskZoneRequest(BaseModel):
    """Body for POST /mission/{id}/risk-zones"""
    field_id: int
    drone_ids: List[int]
    new_zone: Dict[str, Any]                # {"geometry": GeoJSON, "severity": float, "zone_type": str}
    current_routes: List[DroneRoute]
    visited_counts: Dict[int, int]


# ---------------------------------------------------------------------------
# MAVLink mission start schemas
# ---------------------------------------------------------------------------

class StartMissionRequest(BaseModel):
    """Body for POST /mission/{id}/start"""
    routes: List[DroneRoute]                # output of /plan — routes to upload
    altitude_m: float = 30.0               # cruise altitude in metres AGL

class StartMissionResponse(BaseModel):
    status: str                             # "started" | "partial" | "failed"
    uploaded: List[int]                     # drone_ids that got mission uploaded
    started: List[int]                      # drone_ids that armed and launched
