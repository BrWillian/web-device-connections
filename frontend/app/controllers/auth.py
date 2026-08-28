"""Signing in, signing out, and the one preference stored server-side."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import views
from app.config import settings
from app.controllers.base import current_user, render
from app.models import get_session
from app.services.security import (
    SESSION_COOKIE,
    create_session_token,
    verify_login,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    if current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", error=error)


@router.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if not username or not password:
        return RedirectResponse("/login?error=empty", status_code=303)

    user = await verify_login(session, username, password)
    if user is None:
        logger.info("failed login for %r", username)
        return RedirectResponse("/login?error=invalid", status_code=303)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.username, user.role),
        httponly=True,      # unreachable from JavaScript, unlike localStorage
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl,
        path="/",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/theme")
async def theme(to: str = "light", next: str = "/"):
    """Store the theme preference server-side, then go back where we came from.

    A cookie rather than localStorage, so the first byte of HTML already carries
    the right ``data-theme`` and the page never paints in the wrong palette.
    """
    response = RedirectResponse(views.safe_next(next), status_code=303)
    views.set_theme(response, to)
    return response
