from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
import shapely.wkb

from app.database import get_db
from app.repositories import field as field_repo, drone as drone_repo, risk_zone as risk_zone_repo
from app.schemas.mission import (
    PlanMissionRequest, PlanMissionResponse,
    SimulateLossRequest, AddRiskZoneRequest, ReplanResponse,
    StartMissionRequest, StartMissionResponse,
)
from app.services.routing_service import RoutingService
from app.services.replanner import replan_on_drone_loss, replan_on_new_risk_zone
from app.services.mavlink_service import mavlink_service

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
    result = RoutingService.plan_mission(field, drones, all_zones, step_deg=0.002)

    return PlanMissionResponse(
        routes=result.routes,
        reliability_index=result.reliability_index,
        estimated_coverage_pct=result.estimated_coverage_pct,
    )

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


@router.post("/{mission_id}/start", response_model=StartMissionResponse)
async def start_mission(
    mission_id: int,
    request: StartMissionRequest,
):
    """
    Upload planned routes to drones via MAVLink and launch them.

    For each drone in the route list:
      1. Converts RoutePoint list → waypoint dicts with cruise altitude.
      2. Calls mavlink_service.upload_mission() to send MISSION_ITEM_INT frames.
      3. Calls mavlink_service.start_mission() to arm, takeoff, and switch AUTO.

    When SITL is unreachable the service logs a warning and returns partial/failed
    status — the backend itself never crashes.
    """
    del mission_id  # reserved for future Mission DB lookup

    uploaded_ids: list[int] = []
    started_ids: list[int] = []

    for drone_route in request.routes:
        drone_id = drone_route.drone_id
        waypoints = [
            {"lat": rp.lat, "lng": rp.lng, "alt": request.altitude_m}
            for rp in drone_route.route
        ]

        if await mavlink_service.upload_mission(drone_id, waypoints):
            uploaded_ids.append(drone_id)
            if await mavlink_service.start_mission(drone_id):
                started_ids.append(drone_id)

    if not request.routes:
        status = "failed"
    elif len(started_ids) == len(request.routes):
        status = "started"
    elif started_ids:
        status = "partial"
    else:
        status = "failed"

    return StartMissionResponse(
        status=status,
        uploaded=uploaded_ids,
        started=started_ids,
    )


@router.post("/{mission_id}/simulate-loss", response_model=ReplanResponse)
async def simulate_drone_loss(
    mission_id: int,
    drone_id: int = Query(..., description="ID of the drone that was lost"),
    request: SimulateLossRequest = ...,
    db: AsyncSession = Depends(get_db),
):
    """
    Scenario A — drone loss.
    Redistributes uncovered waypoints of the lost drone among active drones
    and re-solves TSP for each affected drone.
    """
    del mission_id  # path param reserved for future Mission DB lookup
    field = await field_repo.get(db, request.field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")

    field_polygon = shapely.wkb.loads(bytes(field.geometry.data))

    drones = []
    for d_id in request.drone_ids:
        drone = await drone_repo.get(db, d_id)
        if drone:
            drones.append(drone)
    if not drones:
        raise HTTPException(status_code=400, detail="No valid drones found")

    all_zones = await risk_zone_repo.get_multi(db, limit=1000)

    result = await replan_on_drone_loss(
        lost_drone_id=drone_id,
        current_routes=request.current_routes,
        visited_counts=request.visited_counts,
        drones=drones,
        field_polygon=field_polygon,
        risk_zones=all_zones,
    )

    if result["status"] == "mission_failed":
        return ReplanResponse(status="mission_failed", updated_routes=[], new_irm=0.0)

    from app.schemas.mission import DroneRoute
    updated = [DroneRoute(**r) for r in result["updated_routes"]]

    # Push updated routes to drones mid-flight via MAVLink
    for dr in updated:
        waypoints = [{"lat": rp.lat, "lng": rp.lng, "alt": 30.0} for rp in dr.route]
        await mavlink_service.update_mission(dr.drone_id, waypoints)

    return ReplanResponse(
        status=result["status"],
        updated_routes=updated,
        new_irm=result["new_irm"],
    )


@router.post("/{mission_id}/risk-zones", response_model=ReplanResponse)
async def add_risk_zone_during_mission(
    mission_id: int,
    request: AddRiskZoneRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Scenario B — new REB zone detected mid-mission.
    Re-routes any drone whose remaining path intersects the new zone.
    """
    del mission_id  # path param reserved for future Mission DB lookup
    field = await field_repo.get(db, request.field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")

    field_polygon = shapely.wkb.loads(bytes(field.geometry.data))

    drones = []
    for d_id in request.drone_ids:
        drone = await drone_repo.get(db, d_id)
        if drone:
            drones.append(drone)
    if not drones:
        raise HTTPException(status_code=400, detail="No valid drones found")

    existing_zones = await risk_zone_repo.get_multi(db, limit=1000)

    result = await replan_on_new_risk_zone(
        new_zone=request.new_zone,
        current_routes=request.current_routes,
        visited_counts=request.visited_counts,
        drones=drones,
        field_polygon=field_polygon,
        existing_risk_zones=existing_zones,
    )

    from app.schemas.mission import DroneRoute
    updated = [DroneRoute(**r) for r in result["updated_routes"]]

    # Push updated routes to drones mid-flight via MAVLink
    for dr in updated:
        waypoints = [{"lat": rp.lat, "lng": rp.lng, "alt": 30.0} for rp in dr.route]
        await mavlink_service.update_mission(dr.drone_id, waypoints)

    return ReplanResponse(
        status=result["status"],
        updated_routes=updated,
        new_irm=result["new_irm"],
    )
