"""The fleet panel, the terminal page, and the two endpoints a script calls."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import views
from app.config import settings
from app.controllers.base import page_user, render, require_user
from app.models import device as device_repo, get_session
from app.services import devices as device_rules, revocation
from app.services.relay import RelayUnavailable
from app.services.security import VALID_SCOPES, sign_grant

logger = logging.getLogger(__name__)
router = APIRouter()

# Shown in place of the grid when the fleet cannot be read at all.
RELAY_HINTS = {
    "Sem conexão com o relay": "Verifique se o relay está acessível",
    "Frontend não autorizado no relay": (
        "RELAY_SECRET está diferente entre as duas aplicações"
    ),
}


async def _grid_context(session: AsyncSession, query: str) -> dict:
    try:
        return {"devices": await device_rules.fleet(session, query),
                "error": None, "error_detail": None}
    except RelayUnavailable as exc:
        message = str(exc)
        return {
            "devices": [],
            "error": message,
            "error_detail": RELAY_HINTS.get(message, "O relay não pôde ser lido"),
        }


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_session),
):
    user = page_user(request)
    return render(request, "index.html", user, query=q, **await _grid_context(session, q))


@router.get("/partials/devices", response_class=HTMLResponse)
async def devices_partial(
    request: Request,
    q: str = "",
    _: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """The card grid on its own, for the dashboard's periodic refresh.

    HTML rather than JSON on purpose: the markup is built by the same Jinja
    include the full page uses, so there is no second, JavaScript copy of how a
    card looks that can drift from this one.
    """
    return views.templates.TemplateResponse(
        "_device_cards.html",
        {"request": request, "query": q, **await _grid_context(session, q)},
    )


@router.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request, device: Optional[str] = None):
    page_user(request)
    return views.templates.TemplateResponse(
        "terminal.html",
        {"request": request, "app_name": settings.app_name, "device_id": device or ""},
    )


@router.get("/config.js")
async def config_js() -> Response:
    """Runtime configuration for the page scripts.

    Only the browser-facing relay URL is exposed here — never a secret.
    """
    body = (
        "window.APP_CONFIG = "
        f'{{ relayWsBase: "{settings.relay_ws_base}", '
        f'appName: "{settings.app_name}" }};\n'
    )
    return Response(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/ws-grant")
async def ws_grant(
    device_id: str = Form(...),
    scope: str = Form(...),
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Sign a one-shot authorisation for a single WebSocket upgrade.

    The one endpoint the browser still calls with fetch, because what follows it
    is a WebSocket the page has to open itself.
    """
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope deve ser um de: {', '.join(sorted(VALID_SCOPES))}",
        )
    if not settings.relay_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RELAY_SECRET não configurado",
        )

    # Refuse a grant for a revoked device. The relay would drop it anyway, but
    # failing here gives the operator a reason instead of a silent disconnect.
    record = await device_repo.get_by_device_id(session, device_id)
    if record is not None and record.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Dispositivo revogado"
        )

    return {
        "grant": sign_grant(user["username"], device_id, scope),
        "expires_in": settings.grant_ttl,
    }


@router.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "relay": settings.relay_base,
        "revocation_mirror": "redis" if revocation.available() else "none",
    }
