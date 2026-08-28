"""Trust checks for the two kinds of client that reach the relay.

The relay authenticates no people. It answers two narrower questions:

* **Is this really that device?** Device tokens are
  ``HMAC-SHA256(device_master_secret, device_id)``, verified without storing
  anything. A token leaked from one device does not unlock another.

* **Did the frontend authorise this browser session?** The frontend owns login
  and, once a user is signed in, signs a short-lived *grant* naming one device
  and one purpose. The relay verifies the signature, then burns the grant's id so
  it cannot be replayed — a URL with a grant in it may well end up in an access
  log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from app.core import presence as presence_module
from app.core.config import settings

logger = logging.getLogger(__name__)

GRANT_ALGORITHM = "HS256"


class DeviceAuthUnavailable(RuntimeError):
    """Raised when device authentication is requested but no master secret is set."""


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------

def mint_device_token(device_id: str, master_secret: Optional[str] = None) -> str:
    """Derive the token to provision on ``device_id``."""
    secret = master_secret if master_secret is not None else settings.device_master_secret
    if not secret:
        raise DeviceAuthUnavailable(
            "DEVICE_MASTER_SECRET is not set; cannot mint or verify device tokens"
        )
    digest = hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_device_token(device_id: str, token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        expected = mint_device_token(device_id)
    except DeviceAuthUnavailable:
        # Fail closed: with no secret configured nobody is authenticated, rather
        # than everybody.
        return False
    return hmac.compare_digest(expected, token)


# --------------------------------------------------------------------------
# Browser grants, issued by the frontend
# --------------------------------------------------------------------------

async def redeem_grant(
    grant: Optional[str], device_id: str, scope: str
) -> Optional[Dict[str, Any]]:
    """Validate a frontend-signed grant for this device and purpose, once.

    Returns the payload on success, or None for any failure — expired, forged,
    meant for another device or another scope, or already spent.
    """
    if not grant:
        return None
    if not settings.relay_secret:
        logger.error("RELAY_SECRET is not set: every grant will be rejected")
        return None

    try:
        payload = jwt.decode(
            grant,
            settings.relay_secret,
            algorithms=[GRANT_ALGORITHM],
            options={"leeway": settings.grant_leeway},
        )
    except JWTError as exc:
        logger.info("rejected grant for %s/%s: %s", device_id, scope, exc)
        return None

    if payload.get("dev") != device_id or payload.get("scp") != scope:
        logger.warning(
            "grant scope mismatch: signed for %s/%s, presented at %s/%s",
            payload.get("dev"), payload.get("scp"), device_id, scope,
        )
        return None

    jti = payload.get("jti")
    if not jti:
        return None

    # Single use. Burn against shared state so the guarantee holds across replicas.
    if not await presence_module.presence.burn_grant(jti, settings.presence_ttl):
        logger.warning("grant %s replayed for %s/%s", jti[:8], device_id, scope)
        return None

    return payload


def verify_relay_secret(presented: Optional[str]) -> bool:
    """Authorise a server-to-server call from the frontend."""
    if not settings.relay_secret or not presented:
        return False
    return hmac.compare_digest(settings.relay_secret, presented)


def origin_allowed(origin: Optional[str]) -> bool:
    """Check a WebSocket upgrade's Origin against the allow-list.

    WebSockets are not covered by CORS, so without this any page could attempt an
    upgrade. Grants make that useless to an attacker, but rejecting early is
    cheaper than relying on that alone.
    """
    allowed = settings.allowed_origin_list
    if not allowed:
        return True  # unset: no check (local development)
    if origin is None:
        return False  # a browser always sends Origin; a missing one is suspect
    return origin in allowed
