"""The frontend's client for the relay.

Every server-to-server call to the relay goes through here, so the shared secret
and the base URL are named in one place instead of being rebuilt at each call
site. The browser never talks to these endpoints — it cannot, it has no secret —
which is exactly what keeps it same-origin and the relay free of CORS.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5.0


class RelayUnavailable(Exception):
    """The relay could not be reached, or could not answer.

    Carries a message meant for the operator: every caller ends up putting it on
    a page, and "Relay indisponível" is more use than a stack trace.
    """


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        return await client.get(
            f"{settings.relay_base}{path}",
            headers={"X-Relay-Secret": settings.relay_secret},
        )


async def list_online_devices() -> Sequence[dict]:
    """Every device attached to any replica, not just one of them.

    The relay answers from its shared presence store, which is what lets any
    replica report on the whole fleet.
    """
    try:
        response = await _get("/devices")
    except httpx.RequestError as exc:
        logger.warning("relay unreachable: %s", exc)
        raise RelayUnavailable("Sem conexão com o relay") from exc

    if response.status_code == 401:
        logger.error("relay rejected our secret: RELAY_SECRET differs between apps")
        raise RelayUnavailable("Frontend não autorizado no relay")
    if response.status_code != 200:
        raise RelayUnavailable("O relay respondeu com erro")

    return response.json()


async def device_token(device_id: str) -> str:
    """The token to provision on a device.

    Asked of the relay rather than derived here, so DEVICE_MASTER_SECRET stays in
    one process. Handing this app that secret would put the key every device
    token derives from into two places at once.
    """
    try:
        response = await _get(f"/devices/{device_id}/token")
    except httpx.RequestError as exc:
        logger.warning("relay unreachable while minting a token: %s", exc)
        raise RelayUnavailable(
            "Relay indisponível — não foi possível gerar o token."
        ) from exc

    if response.status_code == 503:
        raise RelayUnavailable("DEVICE_MASTER_SECRET não está configurado no relay.")
    if response.status_code != 200:
        raise RelayUnavailable("O relay não pôde gerar o token.")

    return response.json()["token"]
