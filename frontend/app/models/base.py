"""Database wiring shared by every model.

The relay deliberately has no database — it verifies derived tokens and signed
grants and relays bytes. Everything that has to be *remembered* about users and
devices lives on this side, because this is the app that knows what a user is.

Schema is created on startup with ``create_all``. That is enough while the model
only ever grows columns; the moment a column has to change type or be dropped,
this wants Alembic rather than a hand-edited table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
VALID_ROLES = (ROLE_ADMIN, ROLE_OPERATOR)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    # Imported for their side effect: a model that is never imported is not in
    # Base.metadata, so create_all would silently skip its table.
    from app.models import device, user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with SessionLocal() as session:
        yield session
