"""Web Device Connections — frontend.

This app owns everything to do with people: it renders the pages, checks
credentials, holds the session cookie, signs the grants that let a browser open a
WebSocket against the relay, and keeps the database of users and registered
devices.

Two different addresses for the relay, and the distinction matters:

* ``RELAY_URL``     — reached by *this process*, server-to-server. Inside compose
  or Kubernetes that is an internal name.
* ``RELAY_WS_URL``  — reached by the *browser*. It must be resolvable from the
  user's machine, and in Kubernetes it must point at the ingress, because only
  the ingress applies the device-id hash that lands the upgrade on the right pod.

The pages are rendered here, in Jinja. The browser runs JavaScript only for the
two things a browser has to do itself — drive the terminal, and stream file bytes
over a WebSocket.

Layout, from the outside in::

    app/controllers/   turn a request into a response
    app/services/      the rules, independent of HTTP
    app/models/        what is stored, and how it is read and written
    app/views/         templates, static assets, and how a page is put together

The device list is fetched by this process rather than by the browser, so the
browser makes no cross-origin HTTP request at all and the relay needs no CORS
configuration.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import views
from app.config import settings
from app.controllers import auth, dashboard, devices, users
from app.controllers.base import Redirect
from app.models import base as db
from app.models import device as device_repo
from app.services import revocation
from app.services.security import ensure_bootstrap_admin, startup_warnings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_warnings()
    await db.init_db()

    async with db.SessionLocal() as session:
        await ensure_bootstrap_admin(session)

    await revocation.connect()
    # Re-publish revocations, so a flushed or restored Redis does not leave a
    # revoked device quietly accepted by the relay.
    async with db.SessionLocal() as session:
        await revocation.sync_all(await device_repo.list_revoked_ids(session))

    logger.info(
        "relay: %s (browser reaches it at %s)",
        settings.relay_base, settings.relay_ws_base,
    )
    if settings.relay_ws_base.startswith("ws://") and "localhost" not in settings.relay_ws_base:
        logger.warning(
            "RELAY_WS_URL is plain ws:// on a non-local host: an https page will "
            "refuse the upgrade as mixed content."
        )

    try:
        yield
    finally:
        await revocation.close()
        await db.close_db()


app = FastAPI(title="Web Device Connections", version="5.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(views.STATIC_DIR)), name="static")


@app.exception_handler(Redirect)
async def _handle_redirect(request: Request, exc: Redirect) -> Response:
    """A page guard's way of saying "not here — go there instead"."""
    return RedirectResponse(exc.target, status_code=303)


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(devices.router)
