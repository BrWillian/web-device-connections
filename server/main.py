"""Web Device Connections — device relay.

This process is not an API for people. It has no login, no user database and no
password hashing: the frontend owns all of that. What runs here relays bytes
between browsers and devices, and answers two trust questions before it does —
is this really that device, and did the frontend authorise this session.

Because browsers never make plain HTTP calls here (the frontend fetches the device
list server-to-server), there is no CORS middleware. The only browser-facing
surface is the WebSocket upgrade, which carries a signed grant.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import presence as presence_module
from app.core.config import settings
from app.routers import devices, files, terminal

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend = presence_module.build_presence()
    try:
        await backend.connect()
    except Exception as exc:
        # Refuse to start rather than come up mis-routing devices. This only
        # fires when Redis was explicitly configured.
        logger.error("could not reach the presence store at startup: %s", exc)
        raise

    presence_module.presence = backend
    logger.info("presence backend: %s (pod=%s)", backend.kind, settings.pod_name)

    if not settings.multi_replica_ready:
        logger.warning(
            "REDIS_URL is unset: device presence is per-process. This replica is "
            "only correct as a single instance — do not scale this Deployment."
        )
    if not settings.relay_secret:
        logger.error(
            "RELAY_SECRET is unset: every browser grant will be rejected and the "
            "frontend cannot read the device list. Set the same value on both."
        )
    if not settings.device_master_secret:
        logger.warning(
            "DEVICE_MASTER_SECRET is unset: no device can authenticate. Set it and "
            "provision tokens with `python -m tools.mint_device_token <device_id>`."
        )

    try:
        yield
    finally:
        await backend.close()


app = FastAPI(title="Web Device Connections Relay", version="3.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    """Liveness, plus whether this replica can safely be scaled out."""
    return {
        "status": "ok",
        "pod": settings.pod_name,
        "presence_backend": presence_module.presence.kind,
        "multi_replica_ready": settings.multi_replica_ready,
    }


app.include_router(devices.router)
app.include_router(terminal.router)
app.include_router(files.router)
