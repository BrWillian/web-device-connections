"""Rules about devices: the inventory, and the one flag that gates access.

Registering a device is bookkeeping — the relay accepts any device whose derived
token checks out, registered or not. Revoking is the operation with teeth, and
it is the only one here with a side effect outside the database.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, device as repo
from app.services import revocation
from app.services.errors import RuleError
from app.services.relay import list_online_devices

logger = logging.getLogger(__name__)


def check_device_id(device_id: str) -> str:
    device_id = (device_id or "").strip()
    if not device_id:
        raise RuleError("O device_id é obrigatório.")
    # It travels in a URL path and is the ingress's hash key, so keep it to
    # characters that survive that trip without escaping.
    if not all(c.isalnum() or c in "-_." for c in device_id):
        raise RuleError("O device_id aceita apenas letras, números, '-', '_' e '.'.")
    return device_id


async def create(
    session: AsyncSession,
    device_id: str,
    name: str,
    owner: str,
    description: str,
) -> Device:
    device_id = check_device_id(device_id)
    name = (name or "").strip()
    if not name:
        raise RuleError("O nome é obrigatório.")

    device = await repo.create(
        session, device_id, name, (description or "").strip(), (owner or "").strip()
    )
    if device is None:
        raise RuleError(f'O device_id "{device_id}" já está cadastrado.')
    logger.info("device %r registered", device_id)
    return device


async def update(
    session: AsyncSession, device: Device, *, name: str, owner: str, description: str
) -> Device:
    """device_id is deliberately not updatable.

    The token is derived from it, so changing it would orphan the credential
    already sitting on the device. Delete and re-register instead.
    """
    name = (name or "").strip()
    if not name:
        raise RuleError("O nome é obrigatório.")

    return await repo.update(
        session,
        device,
        name=name,
        owner=(owner or "").strip(),
        description=(description or "").strip(),
    )


async def set_revoked(
    session: AsyncSession, device: Device, revoked: bool, actor_username: str
) -> Optional[str]:
    """Flip the revocation flag. Returns a warning when the relay was not told.

    Postgres is the source of truth and Redis is the copy the relay actually
    reads. When the mirror write fails the database change still stands, so the
    caller has to say so rather than report a clean success — ``sync_all``
    reconciles at the next startup.
    """
    await repo.update(session, device, is_revoked=revoked)
    logger.info(
        "device %r %s by %r",
        device.device_id,
        "revoked" if revoked else "un-revoked",
        actor_username,
    )

    if not await revocation.set_revoked(device.device_id, revoked):
        return (
            "Salvo, mas o relay não foi notificado (Redis indisponível). "
            "A mudança será aplicada na próxima inicialização."
        )
    return None


async def delete(session: AsyncSession, device: Device, actor_username: str) -> bool:
    """Remove a registry entry. Returns whether it was revoked at the time.

    Deleting does *not* by itself lock the device out: the relay authenticates on
    the derived token, so an unregistered device with a valid token still
    connects. A revoked device keeps its revocation, which is why the Redis key
    is deliberately left in place.
    """
    was_revoked = device.is_revoked
    device_id = device.device_id
    await repo.delete(session, device)

    if was_revoked:
        logger.info("device %r deleted while revoked; revocation kept in place", device_id)
    logger.info("device %r deleted by %r", device_id, actor_username)
    return was_revoked


async def fleet(session: AsyncSession, query: str = "") -> list[dict]:
    """Who is online, joined with the registry, optionally filtered.

    A device can be online without being registered, so unregistered arrivals are
    flagged rather than hidden. That is precisely the list an operator needs in
    order to notice something they did not put there.

    Raises ``RelayUnavailable`` when the fleet cannot be read at all.
    """
    online = await list_online_devices()
    registry = {d.device_id: d for d in await repo.list_all(session)}
    needle = query.strip().lower()

    devices = []
    for entry in online:
        device_id = entry.get("id") or ""
        record = registry.get(device_id)
        # Matching the registered name too, because that is what the card shows.
        haystack = f"{device_id} {record.name if record else ''}".lower()
        if needle and needle not in haystack:
            continue
        devices.append(
            {
                **entry,
                "name": record.name if record else None,
                # Shown on the card, so an operator reads what the device is for
                # without opening the registry entry.
                "description": record.description if record else None,
                "owner": record.owner if record else None,
                "registered": record is not None,
            }
        )
    return devices
