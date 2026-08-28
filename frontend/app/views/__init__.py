"""View layer: how a response is put together.

Templates live in ``templates/``, browser assets in ``static/``, and this module
holds what turns model data into a page — the Jinja environment, the formatting
filters that used to be duplicated in JavaScript, the theme preference, and the
one-shot flash message that carries the result of a POST across the redirect
that follows it.

Nothing here knows a business rule, and nothing here touches the database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

from app.config import settings

VIEWS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = VIEWS_DIR / "templates"
STATIC_DIR = VIEWS_DIR / "static"

THEME_COOKIE = "wdc_theme"
FLASH_COOKIE = "wdc_flash"
VALID_THEMES = ("light", "dark")

# Timestamps are stored in UTC and were previously formatted by the browser, in
# whatever timezone the operator's machine happened to be set to. Rendering on the
# server means picking one explicitly, so the same row reads the same for the whole
# team regardless of where they open it from.
try:
    DISPLAY_TZ = ZoneInfo(settings.display_tz)
except ZoneInfoNotFoundError:  # slim images without tzdata
    DISPLAY_TZ = timezone.utc


# --------------------------------------------------------------------------
# Filters (the former formatUptime / formatDate)
# --------------------------------------------------------------------------

def format_uptime(total_seconds) -> str:
    try:
        seconds = max(0, int(float(total_seconds or 0)))
    except (TypeError, ValueError):
        return "—"

    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)

    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_datetime(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    # Rows written before the column was timezone-aware would otherwise raise on
    # astimezone; assume UTC, which is what utcnow() has always produced.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")


def blank(value) -> str:
    """Em dash for the empty cells, so a table never has holes in it."""
    return value if value not in (None, "") else "—"


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------

_ASSET_VERSIONS: dict = {}


def asset(path: str) -> str:
    """URL for a file under /static, stamped with a version that changes with it.

    StaticFiles answers with an ETag but no Cache-Control, which leaves the
    browser free to reuse a file without ever revalidating. That is harmless
    until a release renames a CSS class: the new HTML arrives, the old
    stylesheet is still in cache, and every selector misses — the page renders
    with no styling at all and nothing in the logs to show for it. A stamp in
    the query string makes each release a different URL, so there is nothing
    stale left to reuse.

    Computed once per process. The container is replaced on deploy, which is
    what refreshes it.
    """
    version = _ASSET_VERSIONS.get(path)
    if version is None:
        try:
            stat = (STATIC_DIR / path).stat()
            version = f"{int(stat.st_mtime):x}-{stat.st_size:x}"
        except OSError:
            # A missing asset is the router's 404 to report, not ours to hide.
            version = "0"
        _ASSET_VERSIONS[path] = version
    return f"/static/{path}?v={version}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["uptime"] = format_uptime
templates.env.filters["datetime_br"] = format_datetime
templates.env.filters["blank"] = blank
templates.env.globals["asset"] = asset


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------

def read_theme(request: Request) -> str:
    theme = request.cookies.get(THEME_COOKIE, "")
    return theme if theme in VALID_THEMES else "light"


def set_theme(response: Response, theme: str) -> None:
    response.set_cookie(
        THEME_COOKIE,
        theme if theme in VALID_THEMES else "light",
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
        path="/",
    )


def safe_next(target: Optional[str], fallback: str = "/") -> str:
    """Only ever redirect back to a path on this app.

    ``//evil.com`` and ``https://evil.com`` are both absolute URLs to a browser,
    so a bare "starts with /" check is not enough on its own.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return fallback
    return target


# --------------------------------------------------------------------------
# Flash messages
# --------------------------------------------------------------------------
#
# A POST that mutates something answers with a redirect, so the result has to
# survive one hop. A cookie is the simplest carrier that works with more than one
# frontend replica behind a load balancer — unlike server-side session storage,
# there is nothing to share between them.
#
# The value is displayed, never trusted: Jinja escapes it, and the worst a user
# can do by hand-editing their own cookie is show themselves a message.

def flash(response: Response, message: str, kind: str = "success") -> None:
    if kind not in ("success", "error", "warning", "info"):
        kind = "info"
    # Percent-encoded because cookie values are Latin-1 and these messages are in
    # Portuguese, accents and all.
    response.set_cookie(
        FLASH_COOKIE,
        f"{kind}|{quote(message)}",
        max_age=30,
        httponly=True,
        samesite="lax",
        path="/",
    )


def take_flash(request: Request) -> Optional[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw or "|" not in raw:
        return None
    kind, _, message = raw.partition("|")
    return {"kind": kind, "message": unquote(message)}


def clear_flash(response: Response) -> None:
    response.delete_cookie(FLASH_COOKIE, path="/")
