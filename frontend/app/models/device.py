"""The device inventory.

Being registered here is not what lets a device connect — the relay accepts any
device whose derived token checks out. This table is the fleet's *inventory*, plus
the one flag that does gate access: ``is_revoked``, which is mirrored into Redis
for the relay to read (see ``app/services/revocation.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The id that appears in the WebSocket path and that the device token is
    # derived from. Immutable once created: changing it would invalidate the
    # token already provisioned on the device.
    device_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Mirrored into Redis, which is what the relay actually consults. Postgres is
    # the source of truth; see services/revocation.py for why there are two copies.
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Device {self.device_id}>"


async def list_all(session: AsyncSession) -> Sequence[Device]:
    result = await session.execute(select(Device).order_by(Device.name))
    return result.scalars().all()


async def get(session: AsyncSession, device_pk: int) -> Optional[Device]:
    return await session.get(Device, device_pk)


async def get_by_device_id(session: AsyncSession, device_id: str) -> Optional[Device]:
    result = await session.execute(select(Device).where(Device.device_id == device_id))
    return result.scalar_one_or_none()


async def list_revoked_ids(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(Device.device_id).where(Device.is_revoked.is_(True))
    )
    return list(result.scalars().all())


async def create(
    session: AsyncSession,
    device_id: str,
    name: str,
    description: Optional[str],
    owner: Optional[str],
) -> Optional[Device]:
    """Returns None when device_id is already registered."""
    device = Device(
        device_id=device_id, name=name, description=description, owner=owner
    )
    session.add(device)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    await session.refresh(device)
    return device


async def update(session: AsyncSession, device: Device, **fields) -> Device:
    for key, value in fields.items():
        if value is not None:
            setattr(device, key, value)
    await session.commit()
    await session.refresh(device)
    return device


async def delete(session: AsyncSession, device: Device) -> None:
    await session.delete(device)
    await session.commit()
