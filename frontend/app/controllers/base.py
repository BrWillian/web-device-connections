"""What every controller needs: who is asking, and how to answer.

Controllers turn a request into a response and nothing else. They read the
session, call a service, and render a view or redirect — the rules they enforce
live in ``app/services``, and the markup they produce lives in ``app/views``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app import views
from app.config import settings
from app.models import ROLE_ADMIN
from app.services import users as user_rules
from app.services.security import SESSION_COOKIE, read_session


class Redirect(Exception):
    """Raised by the page guards, so a guard can send the browser elsewhere.

    A page cannot answer an unauthenticated visitor with a 401 the way an
    endpoint does — there is nothing to display. ``app/main.py`` registers the
    handler that turns this into the redirect that actually helps.
    """

    def __init__(self, target: str):
        self.target = target


def current_user(request: Request) -> Optional[dict]:
    return read_session(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> dict:
    """Dependency for the endpoints a script calls: fail with a status code."""
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão expirada"
        )
    return user


def page_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise Redirect("/login")
    return user


def page_admin(request: Request) -> dict:
    """Same, but the page is admin-only.

    Sent back to the dashboard rather than to the login page: the session is
    valid, so logging in again would only bounce them straight back here.
    """
    user = page_user(request)
    if user["role"] != ROLE_ADMIN:
        raise Redirect("/")
    return user


def render(
    request: Request,
    template: str,
    actor: Optional[dict] = None,
    *,
    status_code: int = 200,
    **extra,
) -> Response:
    """One place where every page picks up the shell's context.

    The signed-in user is ``actor`` rather than ``user`` on purpose: the form
    pages pass the record being edited as ``user``, and sharing the name made
    that a duplicate-argument TypeError instead of a page.

    The flash is read and cleared in the same breath: it exists to survive
    exactly one redirect, and leaving the cookie behind would replay the message
    on the next page the operator opened.
    """
    flash = views.take_flash(request)
    response = views.templates.TemplateResponse(
        template,
        {
            "request": request,
            "app_name": settings.app_name,
            "theme": views.read_theme(request),
            "current_path": request.url.path,
            "flash": flash,
            "username": actor["username"] if actor else None,
            "role": actor["role"] if actor else None,
            "is_admin": bool(actor) and actor["role"] == ROLE_ADMIN,
            "role_labels": user_rules.ROLE_LABELS,
            "min_password_length": settings.min_password_length,
            **extra,
        },
        status_code=status_code,
    )
    if flash:
        views.clear_flash(response)
    return response


def redirect_with(target: str, message: str, kind: str = "success") -> Response:
    """POST → 303 → GET, carrying the outcome in a one-shot cookie.

    The redirect is what stops a refresh from re-submitting the form.
    """
    response = RedirectResponse(target, status_code=303)
    views.flash(response, message, kind)
    return response
