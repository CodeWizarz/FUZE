import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# In a real app, use a proper config loader.
# Docker compose sets POSTGRES_USER=postgres, POSTGRES_PASSWORD=postgres, POSTGRES_DB=temporal
# But we usually want a separate DB for the app or share schemas.
# The docker-compose configured 'temporal' DB. We'll use that.
DATABASE_URL = os.getenv(
    "POSTGRES_DSN", 
    "postgresql+asyncpg://postgres:postgres@localhost:5432/temporal"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False, # Set to True for SQL query logging
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autoflush=False # We want explicit flushes usually
)

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a transactional scope around a series of operations.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
