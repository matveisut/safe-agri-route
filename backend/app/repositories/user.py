from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.models.user import User
from .base import BaseRepository

class UserRepository(BaseRepository[User]):
    async def get_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        query = select(User).where(User.username == username)
        result = await db.execute(query)
        return result.scalars().first()

user_repo = UserRepository(User)
