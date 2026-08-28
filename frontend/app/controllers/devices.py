"""The device registry. Admin only, and every mutation is a form POST."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import Redirect, page_admin, redirect_with, render
from app.models import Device, device as repo, get_session
from app.services import RuleError, devices as rules
from app.services.relay import RelayUnavailable, device_token

router = APIRouter(prefix="/manage/devices")

SECTION = "Dispositivos"


async def _load(session: AsyncSession, device_pk: int) -> Device:
    record = await repo.get(session, device_pk)
    if record is None:
        raise Redirect("/manage/devices")
    return record


@router.get("", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    admin = page_admin(request)
    return render(
        request, "devices.html", admin, section=SECTION,
        devices=await repo.list_all(session),
    )


# Declared before "/{device_pk}": FastAPI matches in declaration order.
@router.get("/novo", response_class=HTMLResponse)
async def new_form(request: Request):
    admin = page_admin(request)
    return render(
        request, "device_form.html", admin, section=SECTION, device=None, form={},
    )


@router.post("")
async def create(
    request: Request,
    device_id: str = Form(""),
    name: str = Form(""),
    owner: str = Form(""),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    admin = page_admin(request)
    try:
        created = await rules.create(session, device_id, name, owner, description)
    except RuleError as exc:
        return render(
            request, "device_form.html", admin, section=SECTION, status_code=400,
            device=None,
            form={
                "device_id": device_id, "name": name,
                "owner": owner, "description": description,
            },
            flash={"kind": "error", "message": str(exc)},
        )
    # Straight to the token: registering a device is only half the job, and the
    # other half is putting the credential on the device.
    return redirect_with(
        f"/manage/devices/{created.id}/token",
        f'Dispositivo "{created.device_id}" cadastrado.',
    )


@router.get("/{device_pk}", response_class=HTMLResponse)
async def edit_form(
    request: Request, device_pk: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, device_pk)
    return render(
        request, "device_form.html", admin, section=SECTION,
        device=record,
        form={
            "device_id": record.device_id,
            "name": record.name,
            "owner": record.owner or "",
            "description": record.description or "",
        },
    )


@router.post("/{device_pk}")
async def save(
    request: Request,
    device_pk: int,
    name: str = Form(""),
    owner: str = Form(""),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    admin = page_admin(request)
    record = await _load(session, device_pk)
    try:
        await rules.update(
            session, record, name=name, owner=owner, description=description
        )
    except RuleError as exc:
        return render(
            request, "device_form.html", admin, section=SECTION, status_code=400,
            device=record,
            form={
                "device_id": record.device_id, "name": name,
                "owner": owner, "description": description,
            },
            flash={"kind": "error", "message": str(exc)},
        )
    return redirect_with(
        "/manage/devices", f'Dispositivo "{record.device_id}" atualizado.'
    )


@router.get("/{device_pk}/token", response_class=HTMLResponse)
async def token_page(
    request: Request, device_pk: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, device_pk)

    try:
        token = await device_token(record.device_id)
    except RelayUnavailable as exc:
        return redirect_with("/manage/devices", str(exc), "error")

    return render(
        request, "device_token.html", admin, section=SECTION,
        device=record,
        env_lines=f"DEVICE_ID={record.device_id}\nDEVICE_TOKEN={token}",
    )


@router.get("/{device_pk}/revogar", response_class=HTMLResponse)
async def revoke_confirm(
    request: Request, device_pk: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, device_pk)
    return render(
        request, "confirm.html", admin, section=SECTION,
        title=f'Revogar "{record.device_id}"?',
        body=[
            "O dispositivo será desconectado em até 15 segundos e não conseguirá "
            "autenticar de novo.",
            "É reversível: basta liberá-lo depois.",
        ],
        action=f"/manage/devices/{record.id}/revogar",
        cancel_url="/manage/devices",
        confirm_label="Revogar",
        tone="danger",
        icon="fa-ban",
    )


@router.get("/{device_pk}/excluir", response_class=HTMLResponse)
async def delete_confirm(
    request: Request, device_pk: int, session: AsyncSession = Depends(get_session)
):
    admin = page_admin(request)
    record = await _load(session, device_pk)
    return render(
        request, "confirm.html", admin, section=SECTION,
        title=f'Excluir "{record.device_id}" do cadastro?',
        body=(
            ["A revogação é mantida, então ele continuará bloqueado."]
            if record.is_revoked
            else [
                "Atenção: excluir NÃO bloqueia o dispositivo. O relay autentica "
                "pelo token derivado, então ele seguirá conectando.",
                "Revogue primeiro se a intenção é tirá-lo do ar.",
            ]
        ),
        action=f"/manage/devices/{record.id}/excluir",
        cancel_url="/manage/devices",
        confirm_label="Excluir",
        tone="danger",
        icon="fa-trash",
    )


@router.post("/{device_pk}/{action}")
async def act(
    request: Request,
    device_pk: int,
    action: str,
    session: AsyncSession = Depends(get_session),
):
    admin = page_admin(request)
    if action not in ("revogar", "liberar", "excluir"):
        raise Redirect("/manage/devices")

    record = await _load(session, device_pk)
    device_id = record.device_id

    if action == "excluir":
        still_revoked = await rules.delete(session, record, admin["username"])
        return redirect_with(
            "/manage/devices",
            f'Dispositivo "{device_id}" excluído do cadastro.'
            + (" A revogação foi mantida." if still_revoked else ""),
        )

    revoking = action == "revogar"
    warning = await rules.set_revoked(session, record, revoking, admin["username"])
    if warning:
        return redirect_with("/manage/devices", warning, "warning")
    return redirect_with(
        "/manage/devices",
        f'Dispositivo "{device_id}" {"revogado" if revoking else "liberado"}.',
    )
