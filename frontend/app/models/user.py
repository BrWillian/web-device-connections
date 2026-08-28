"""The people who sign in, and how to read and write them.

Model and data access sit together on purpose: in this layout the Model owns
persistence, and the rules *about* users — who may demote whom, what a valid
password is — live in ``app/services/users.py``, which is where they can be read
without wading through SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ROLE_ADMIN, ROLE_OPERATOR, Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=ROLE_OPERATOR)
    # Disabling is preferred over deleting: it keeps the audit trail of who did
    # what intact while removing the ability to sign in.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username} ({self.role})>"


async def get_by_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)


async def list_all(session: AsyncSession) -> Sequence[User]:
    result = await session.execute(select(User).order_by(User.username))
    return result.scalars().all()


async def count_active_admins(session: AsyncSession) -> int:
    """Used to refuse the change that would leave nobody able to administer."""
    result = await session.execute(
        select(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True))
    )
    return len(result.scalars().all())


async def create(
    session: AsyncSession, username: str, password_hash: str, role: str
) -> Optional[User]:
    """Returns None when the username is taken.

    Relies on the unique constraint rather than a prior SELECT, so two concurrent
    requests for the same name cannot both succeed.
    """
    user = User(username=username, password_hash=password_hash, role=role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    await session.refresh(user)
    return user


async def update(session: AsyncSession, user: User, **fields) -> User:
    """Assign the named fields and commit. ``None`` means "leave alone"."""
    for key, value in fields.items():
        if value is not None:
            setattr(user, key, value)
    await session.commit()
    await session.refresh(user)
    return user


async def delete(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()
