"""Rules about users: what is valid, and who may do what to whom.

Kept apart from ``app/models/user.py`` so the guards can be read on their own.
The model knows how to write a row; this knows that writing *that* row would
leave the system with nobody able to administer it.

Every refusal is a ``RuleError`` carrying a message written for the operator,
because that message is what ends up on the screen.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ROLE_ADMIN, VALID_ROLES, User, user as repo
from app.services.errors import RuleError
from app.services.security import hash_password

logger = logging.getLogger(__name__)

ROLE_LABELS = {"admin": "Administrador", "operator": "Operador"}


def _check_password(password: str) -> None:
    if len(password) < settings.min_password_length:
        raise RuleError(
            f"A senha deve ter ao menos {settings.min_password_length} caracteres."
        )


def _check_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise RuleError("Papel inválido.")


async def create(
    session: AsyncSession, username: str, password: str, role: str
) -> User:
    username = (username or "").strip()
    if not username:
        raise RuleError("O nome de usuário é obrigatório.")
    _check_role(role)
    _check_password(password)

    user = await repo.create(session, username, hash_password(password), role)
    if user is None:
        raise RuleError(f'O usuário "{username}" já existe.')
    logger.info("user %r created with role %s", username, role)
    return user


async def _guard_admin_removal(
    session: AsyncSession, user: User, actor_username: str
) -> None:
    """Refuse the change that would leave nobody able to administer.

    Demotion and deactivation are checked alike, because either one removes an
    administrator.
    """
    if user.username == actor_username:
        raise RuleError("Você não pode remover seu próprio acesso de administrador.")
    if await repo.count_active_admins(session) <= 1:
        raise RuleError("Este é o último administrador ativo.")


async def update(
    session: AsyncSession,
    user: User,
    *,
    password: Optional[str],
    role: str,
    is_active: bool,
    actor_username: str,
) -> User:
    _check_role(role)
    if password:
        _check_password(password)

    # ``user.is_active`` is part of the condition, not an afterthought: demoting
    # an administrator who is already deactivated removes nobody, so the guard
    # must not fire on it. Without that term, an admin who had been deactivated
    # could never be turned back into an ordinary user.
    if user.role == ROLE_ADMIN and user.is_active and (role != ROLE_ADMIN or not is_active):
        await _guard_admin_removal(session, user, actor_username)

    updated = await repo.update(
        session,
        user,
        password_hash=hash_password(password) if password else None,
        role=role,
        # Passed explicitly rather than through the model's skip-None rule:
        # False is a value here, not an absence.
        is_active=is_active,
    )
    logger.info("user %r updated by %r", updated.username, actor_username)
    return updated


async def set_active(
    session: AsyncSession, user: User, is_active: bool, actor_username: str
) -> None:
    if user.role == ROLE_ADMIN and user.is_active and not is_active:
        await _guard_admin_removal(session, user, actor_username)
    await repo.update(session, user, is_active=is_active)
    logger.info(
        "user %r %s by %r",
        user.username,
        "activated" if is_active else "deactivated",
        actor_username,
    )


async def delete(session: AsyncSession, user: User, actor_username: str) -> None:
    if user.username == actor_username:
        raise RuleError("Você não pode excluir a si mesmo.")
    # Same reasoning as above: an inactive admin is not one of the active admins
    # the count is protecting, so deleting them cannot lock anyone out.
    if (
        user.role == ROLE_ADMIN
        and user.is_active
        and await repo.count_active_admins(session) <= 1
    ):
        raise RuleError("Este é o último administrador ativo.")

    await repo.delete(session, user)
    logger.info("user %r deleted by %r", user.username, actor_username)
