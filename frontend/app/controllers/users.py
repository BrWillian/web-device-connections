"""User management. Admin only, and every mutation is a form POST."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import Redirect, page_admin, redirect_with, render
from app.models import User, get_session, user as repo
from app.services import RuleError, users as rules

router = APIRouter(prefix="/manage/users")

SECTION = "Usuários"


async def _load(session: AsyncSession, user_id: int) -> User:
    record = await repo.get(session, user_id)
    if record is None:
        raise Redirect("/manage/users")
    return record


@router.get("", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    admin = page_admin(request)
    return render(
        request, "users.html", admin, section=SECTION,
        users=await repo.list_all(session),
    )


# Declared before the "/{user_id}" routes below: FastAPI matches in declaration
# order, and "novo" would otherwise be tried as a user id.
@router.get("/novo", response_class=HTMLResponse)
async def new_form(request: Request):
    admin = page_admin(request)
    return render(
        request, "user_form.html", admin, section=SECTION,
        user=None, form={"role": "operator"},
    )


@router.post("")
async def create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form("operator"),
    session: AsyncSession = Depends(get_session),
):
    admin = page_admin(request)
    try:
        created = await rules.create(session, username, password, role)
    except RuleError as exc:
        # Re-render rather than redirect, so the typed values are still there.
        return render(
            request, "user_form.html", admin, section=SECTION, status_code=400,
            user=None, form={"username": username, "role": role},
            flash={"kind": "error", "message": str(exc)},
        )
    return redirect_with("/manage/users", f'Usuário "{created.username}" criado.')


@router.get("/{user_id}", response_class=HTMLResponse)
async def edit_form(
    request: Request, user_id: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, user_id)
    return render(
        request, "user_form.html", admin, section=SECTION,
        user=record,
        form={
            "username": record.username,
            "role": record.role,
            "is_active": record.is_active,
        },
    )


@router.post("/{user_id}")
async def save(
    request: Request,
    user_id: int,
    password: str = Form(""),
    role: str = Form("operator"),
    is_active: str = Form("false"),
    session: AsyncSession = Depends(get_session),
):
    admin = page_admin(request)
    record = await _load(session, user_id)
    # The form pairs a hidden "false" with the checkbox's "true"; the last value
    # wins, which is how an unchecked box can mean anything at all.
    active = is_active == "true"

    try:
        await rules.update(
            session, record,
            password=password or None,
            role=role,
            is_active=active,
            actor_username=admin["username"],
        )
    except RuleError as exc:
        return render(
            request, "user_form.html", admin, section=SECTION, status_code=409,
            user=record,
            form={"username": record.username, "role": role, "is_active": active},
            flash={"kind": "error", "message": str(exc)},
        )
    return redirect_with("/manage/users", f'Usuário "{record.username}" atualizado.')


@router.get("/{user_id}/excluir", response_class=HTMLResponse)
async def delete_confirm(
    request: Request, user_id: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, user_id)
    return render(
        request, "confirm.html", admin, section=SECTION,
        title=f'Excluir o usuário "{record.username}"?',
        body=[
            "Isso é permanente e não pode ser desfeito.",
            "Para apenas bloquear o acesso mantendo o histórico, desative o "
            "usuário em vez de excluí-lo.",
        ],
        action=f"/manage/users/{record.id}/excluir",
        cancel_url="/manage/users",
        confirm_label="Excluir",
        tone="danger",
        icon="fa-trash",
    )


@router.post("/{user_id}/{action}")
async def act(
    request: Request,
    user_id: int,
    action: str,
    session: AsyncSession = Depends(get_session),
):
    """Activate, deactivate or delete.

    One handler, because all three are a POST with no body whose only difference
    is which rule guards them.
    """
    admin = page_admin(request)
    if action not in ("ativar", "desativar", "excluir"):
        raise Redirect("/manage/users")

    record = await _load(session, user_id)
    try:
        if action == "excluir":
            await rules.delete(session, record, admin["username"])
            message = f'Usuário "{record.username}" excluído.'
        else:
            activating = action == "ativar"
            await rules.set_active(session, record, activating, admin["username"])
            message = (
                f'Usuário "{record.username}" '
                f'{"reativado" if activating else "desativado"}.'
            )
    except RuleError as exc:
        return redirect_with("/manage/users", str(exc), "error")
    return redirect_with("/manage/users", message)
