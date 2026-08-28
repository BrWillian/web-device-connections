"""Shared, cross-replica state: device presence and spent grant ids.

The live ``WebSocket`` objects themselves stay in ``app.state`` — a socket is a
TCP connection owned by one process and cannot be shared. What lives here is the
*metadata* every replica needs to see: which devices exist, which pod holds each
one, and which grants have already been spent, so a grant redeemed on one replica
cannot be replayed against another.

Two backends implement the same interface:

* ``RedisPresence``  — for multi-replica deployments. Required whenever the
  Deployment runs more than one pod.
* ``MemoryPresence`` — a single-process fallback so local development does not
  require a Redis. Silently wrong with more than one replica, which is why
  ``main.py`` logs a warning when it is selected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

DEVICE_PREFIX = "wdc:device:"
GRANT_PREFIX = "wdc:grant:"
# Written by the frontend, read here. The frontend's database is the source of
# truth; this is the copy the relay is allowed to depend on.
REVOKED_PREFIX = "wdc:revoked:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uptime_seconds(connected_at: Optional[str]) -> int:
    if not connected_at:
        return 0
    try:
        started = datetime.fromisoformat(connected_at)
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


class MemoryPresence:
    """Single-process fallback. Correct only while there is exactly one replica."""

    kind = "memory"

    def __init__(self) -> None:
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._spent_grants: Dict[str, float] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self._devices.clear()
        self._spent_grants.clear()

    async def register(self, device_id: str, pod: str) -> Dict[str, Any]:
        record = {"id": device_id, "pod": pod, "connected_at": _now_iso()}
        self._devices[device_id] = record
        return record

    async def refresh(self, device_id: str) -> None:
        return None  # nothing expires in-process

    async def unregister(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    async def list_devices(self) -> List[Dict[str, Any]]:
        return [
            {**rec, "uptime_seconds": _uptime_seconds(rec.get("connected_at"))}
            for rec in self._devices.values()
        ]

    async def burn_grant(self, jti: str, ttl: int) -> bool:
        """Record a grant id as spent. True the first time, False on replay."""
        now = time.monotonic()
        for spent_jti, expires_at in list(self._spent_grants.items()):
            if expires_at <= now:
                del self._spent_grants[spent_jti]
        if jti in self._spent_grants:
            return False
        self._spent_grants[jti] = now + ttl
        return True

    async def is_revoked(self, device_id: str) -> bool:
        """Always False: nothing writes revocations to a per-process store.

        Revocation is published by the frontend into shared state, so it only has
        meaning when Redis is configured. Answering False here keeps single-process
        development working rather than locking out every device.
        """
        return False


class RedisPresence:
    """Cross-replica backend. Device records carry a TTL kept alive by a heartbeat,
    so a pod that dies without cleaning up drops off the list on its own."""

    kind = "redis"

    def __init__(self, url: str, ttl: int) -> None:
        self._url = url
        self._ttl = ttl
        self._redis: Any = None

    async def connect(self) -> None:
        from redis.asyncio import Redis  # imported lazily so memory mode needs no redis

        self._redis = Redis.from_url(self._url, decode_responses=True)
        await self._redis.ping()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def register(self, device_id: str, pod: str) -> Dict[str, Any]:
        record = {"id": device_id, "pod": pod, "connected_at": _now_iso()}
        await self._redis.set(DEVICE_PREFIX + device_id, json.dumps(record), ex=self._ttl)
        return record

    async def refresh(self, device_id: str) -> None:
        # EXPIRE alone would silently do nothing if the key had already lapsed,
        # leaving a live device invisible until it reconnects.
        key = DEVICE_PREFIX + device_id
        if not await self._redis.expire(key, self._ttl):
            await self.register(device_id, settings.pod_name)

    async def unregister(self, device_id: str) -> None:
        await self._redis.delete(DEVICE_PREFIX + device_id)

    async def list_devices(self) -> List[Dict[str, Any]]:
        keys = [k async for k in self._redis.scan_iter(match=DEVICE_PREFIX + "*", count=200)]
        if not keys:
            return []
        devices = []
        for raw in await self._redis.mget(keys):
            if not raw:
                continue  # expired between SCAN and MGET
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            devices.append({**rec, "uptime_seconds": _uptime_seconds(rec.get("connected_at"))})
        return devices

    async def burn_grant(self, jti: str, ttl: int) -> bool:
        """Record a grant id as spent. True the first time, False on replay.

        SET NX is atomic, so two upgrades racing on the same grant produce exactly
        one winner even when they land on different replicas.
        """
        return bool(await self._redis.set(GRANT_PREFIX + jti, "1", nx=True, ex=ttl))

    async def is_revoked(self, device_id: str) -> bool:
        """Whether the frontend has revoked this device.

        Fails *open*. A Redis outage would otherwise lock out the entire fleet,
        which is a far worse failure than briefly honouring a revoked token — and
        the frontend re-publishes every revocation at startup, so the window
        closes on its own.
        """
        try:
            return bool(await self._redis.exists(REVOKED_PREFIX + device_id))
        except Exception as exc:
            logger.error("revocation check failed for %s: %s", device_id, exc)
            return False


# Module-level singleton, swapped in at startup by main.py's lifespan.
presence: Any = MemoryPresence()


def build_presence() -> Any:
    if settings.redis_url:
        return RedisPresence(settings.redis_url, settings.presence_ttl)
    return MemoryPresence()


async def heartbeat_loop(device_id: str) -> None:
    """Keep a device's presence record alive while its socket is connected."""
    interval = max(1, settings.presence_ttl // 3)
    try:
        while True:
            await asyncio.sleep(interval)
            await presence.refresh(device_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a presence blip must not kill the device session
        logger.warning("heartbeat failed for %s: %s", device_id, exc)
