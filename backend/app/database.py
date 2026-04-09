import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# For local default without docker set os environ or use this fallback
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://safeagri:safeagripassword@localhost:5432/safeagriroute"
)

# engine handling connection pool
engine = create_async_engine(
    DATABASE_URL,
    echo=True, # Set to False in production
    pool_size=5,
    max_overflow=10,
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def get_db():
    """Dependency for providing a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
