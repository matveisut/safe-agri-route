import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon

from app.database import engine, AsyncSessionLocal
from app.models.base import Base
from app.models.field import Field
from app.models.risk_zone import RiskZone
from app.models.drone import Drone
from app.models.user import User
from app.core.security import hash_password

async def recreate_tables():
    """Drop all tables and create them freshly. We enable PostGIS extension first."""
    async with engine.begin() as conn:
        # Create Postgis extension if not exists
        await conn.execute(
            text("CREATE EXTENSION IF NOT EXISTS postgis;")
        )
        
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
from sqlalchemy import text

async def seed_data():
    async with AsyncSessionLocal() as session:
        # Create Drones
        drones_data = [
            {"name": "AgriFly-1", "battery_capacity": 5000, "max_speed": 15.0, "status": "idle"},
            {"name": "AgriFly-2", "battery_capacity": 7500, "max_speed": 12.5, "status": "idle"},
            {"name": "AgriFly-3", "battery_capacity": 10000, "max_speed": 10.0, "status": "idle"},
        ]
        for d in drones_data:
            session.add(Drone(**d))

        # Stavropol Krai Field shifted 15km East. 1 deg Lon ~ 78km roughly, so 15km is ~0.19 lon.
        lon, lat = 42.1634, 45.0428
        delta = 0.01 
        
        # Non-square field (irregular pentagon/hexagon)
        field_shape = Polygon([
            (lon - delta * 1.5, lat - delta * 0.8), # Bottom-Left
            (lon + delta * 2.0, lat - delta * 1.2), # Bottom-Right (slanted down)
            (lon + delta * 2.5, lat + delta * 0.2), # Mid-Right
            (lon + delta * 1.0, lat + delta * 1.5), # Top-Right
            (lon - delta * 1.0, lat + delta * 0.5), # Top-Left
            (lon - delta * 1.5, lat - delta * 0.8)  # Bottom-Left (close)
        ])
        
        field = Field(
            name="Stavropol Wheat Field (Irregular)",
            geometry=from_shape(field_shape, srid=4326)
        )
        session.add(field)

        # Risk Zone 1: Jamming (Partially overlapping bottom-left)
        jamming_shape = Polygon([
            (lon - delta * 2.0, lat - delta * 1.5),
            (lon + delta * 0.5, lat - delta * 1.8),
            (lon, lat + delta * 0.2),
            (lon - delta * 1.8, lat - delta * 0.2),
            (lon - delta * 2.0, lat - delta * 1.5)
        ])
        # Labels are matched case-insensitively in risk_map (jammer / jamming / spoofing → RF REB)
        jamming_zone = RiskZone(
            type="Jamming",
            geometry=from_shape(jamming_shape, srid=4326),
            severity_weight=2.0
        )
        session.add(jamming_zone)
        
        # Second REB zone (legacy name "Spoofing" — same RF model as jammer in build_risk_map)
        spoofing_shape = Polygon([
            (lon + delta * 0.8, lat + delta * 0.5),
            (lon + delta * 3.0, lat + delta * 0.8),
            (lon + delta * 2.8, lat + delta * 2.5),
            (lon + delta * 0.5, lat + delta * 2.0),
            (lon + delta * 0.8, lat + delta * 0.5)
        ])
        spoofing_zone = RiskZone(
            type="Spoofing",
            geometry=from_shape(spoofing_shape, srid=4326),
            severity_weight=3.5
        )
        session.add(spoofing_zone)

        # Seed users
        users_data = [
            {"email": "operator@safegriroute.com", "password": "operator123", "role": "operator"},
            {"email": "viewer@safegriroute.com",   "password": "viewer123",   "role": "viewer"},
        ]
        for u in users_data:
            session.add(User(
                email=u["email"],
                hashed_password=hash_password(u["password"]),
                role=u["role"],
                is_active=True,
            ))

        await session.commit()
        print("Database seeded: 1 Field, 2 Risk Zones, 3 Drones, 2 Users!")

async def main():
    print("Recreating database tables...")
    await recreate_tables()
    print("Seeding data...")
    await seed_data()

if __name__ == "__main__":
    asyncio.run(main())
