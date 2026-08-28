"""Propagating device revocations to the relay.

Postgres is the source of truth for whether a device is revoked, but the relay
must not read it. The relay is supposed to keep working when this app is down, and
giving it a dependency on the frontend's database — or worse, an HTTP call to the
frontend — would put both in the device authentication path.

So revocations are mirrored into Redis, which the relay already uses for presence
and spent grants. One key per revoked device, no TTL:

    wdc:revoked:<device_id> = "1"

**Failure direction.** If Redis is unreachable the relay cannot tell a revoked
device from a good one, and it allows the connection: failing closed there would
take the whole fleet offline on a Redis blip. The same reasoning means a Redis
data loss silently un-revokes, so ``sync_all`` re-publishes every revoked id at
startup. A revocation is therefore eventually consistent, not instant-and-final —
if you need a device provably locked out, rotate DEVICE_MASTER_SECRET and re-mint
the tokens for the fleet.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from app.config import settings

logger = logging.getLogger(__name__)

REVOKED_PREFIX = "wdc:revoked:"

_redis = None


async def connect() -> None:
    """Open the Redis connection, if one is configured."""
    global _redis
    if not settings.redis_url:
        logger.warning(
            "REDIS_URL is unset: device revocations cannot reach the relay. A device "
            "marked revoked here will keep connecting until this is configured."
        )
        return

    from redis.asyncio import Redis  # imported lazily: optional in local dev

    _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await _redis.ping()
    logger.info("revocation mirror connected to Redis")


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def available() -> bool:
    return _redis is not None


async def set_revoked(device_id: str, revoked: bool) -> bool:
    """Publish (or clear) a revocation. False means the relay was not told."""
    if _redis is None:
        return False
    try:
        key = REVOKED_PREFIX + device_id
        if revoked:
            await _redis.set(key, "1")
        else:
            await _redis.delete(key)
        return True
    except Exception as exc:
        # Never fail the request over the mirror: the database write already
        # happened and sync_all will reconcile on the next start.
        logger.error("could not publish revocation for %s: %s", device_id, exc)
        return False


async def sync_all(revoked_ids: Iterable[str]) -> Optional[int]:
    """Reconcile Redis with the database. Returns how many keys were written.

    Runs at startup so a flushed or restored Redis does not leave revoked devices
    quietly accepted. Also clears stale keys for devices that were un-revoked
    while this app was down.
    """
    if _redis is None:
        return None

    wanted = set(revoked_ids)
    try:
        existing = {
            key[len(REVOKED_PREFIX):]
            async for key in _redis.scan_iter(match=REVOKED_PREFIX + "*", count=200)
        }
        for device_id in wanted - existing:
            await _redis.set(REVOKED_PREFIX + device_id, "1")
        for device_id in existing - wanted:
            await _redis.delete(REVOKED_PREFIX + device_id)
    except Exception as exc:
        logger.error("revocation sync failed: %s", exc)
        return None

    logger.info("revocation mirror synced: %d revoked device(s)", len(wanted))
    return len(wanted)
