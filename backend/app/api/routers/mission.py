from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.repositories import field as field_repo, drone as drone_repo, risk_zone as risk_zone_repo
from app.schemas.mission import PlanMissionRequest, PlanMissionResponse
from app.services.routing_service import RoutingService

router = APIRouter(prefix="/mission", tags=["Mission"])

@router.post("/plan", response_model=PlanMissionResponse)
async def plan_mission(request: PlanMissionRequest, db: AsyncSession = Depends(get_db)):
    """
    Plans drone mission. Takes a field, drones, and computes CVRP logic.
    """
    # 1. Fetch field
    field = await field_repo.get(db, request.field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")

    # 2. Fetch drones
    drones = []
    for d_id in request.drone_ids:
        drone = await drone_repo.get(db, d_id)
        if clone := drone:
            drones.append(clone)
            
    if not drones:
        raise HTTPException(status_code=400, detail="No valid drones found")

    # 3. Fetch risk zones (active across all territory)
    # We load all risk zones simply
    all_zones = await risk_zone_repo.get_multi(db, limit=1000)
    
    # 4. Process routing logic
    # In real world, step size should be dynamic depending on field size
    routes = RoutingService.plan_mission(field, drones, all_zones, step_deg=0.002)
    
    return PlanMissionResponse(routes=routes)

from geoalchemy2.functions import ST_AsGeoJSON
from sqlalchemy import select

@router.get("/fields")
async def get_fields(db: AsyncSession = Depends(get_db)):
    # Raw query to grab geometry as GeoJSON string rather than WKB Element
    query = select(field_repo.model.id, field_repo.model.name, ST_AsGeoJSON(field_repo.model.geometry).label("geojson"))
    result = await db.execute(query)
    rows = result.all()
    
    # Pack to dict
    out = [{"id": r.id, "name": r.name, "geojson": r.geojson} for r in rows]
    return {"fields": out}
    
@router.get("/risk-zones")
async def get_risk_zones(db: AsyncSession = Depends(get_db)):
    query = select(risk_zone_repo.model.id, risk_zone_repo.model.type, risk_zone_repo.model.severity_weight, ST_AsGeoJSON(risk_zone_repo.model.geometry).label("geojson"))
    result = await db.execute(query)
    rows = result.all()
    
    out = [{"id": r.id, "type": r.type, "severity_weight": r.severity_weight, "geojson": r.geojson} for r in rows]
    return {"risk_zones": out}
