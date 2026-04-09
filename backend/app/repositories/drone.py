from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.models.drone import Drone
from .base import BaseRepository

class DroneRepository(BaseRepository[Drone]):
    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[Drone]:
        query = select(Drone).where(Drone.name == name)
        result = await db.execute(query)
        return result.scalars().first()

drone_repo = DroneRepository(Drone)
