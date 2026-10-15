import uuid
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import DomainError, Forbidden
from app.core.permissions import effective_permissions
from app.modules.warehouses.models import User, UserWarehouse


class Unauthorized(DomainError):
    status_code = 401


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID
    login: str
    full_name: str
    permissions: frozenset[str]
    warehouse_ids: frozenset[int]

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if perm not in self.permissions:
            raise Forbidden("forbidden", f"Нет права {perm}", permission=perm)

    def require_warehouse(self, warehouse_id: int) -> None:
        if warehouse_id not in self.warehouse_ids:
            raise Forbidden(
                "warehouse_forbidden", "Нет доступа к складу", warehouse_id=warehouse_id
            )


def load_actor(db: Session, user_id: uuid.UUID) -> Actor:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("unauthorized", "Пользователь не найден или заблокирован")
    wh = db.scalars(select(UserWarehouse.warehouse_id).where(UserWarehouse.user_id == user.id))
    return Actor(
        id=user.id,
        login=user.login,
        full_name=user.full_name,
        permissions=effective_permissions(
            user.roles, user.granted_permissions, user.revoked_permissions
        ),
        warehouse_ids=frozenset(wh),
    )


def current_actor(
    db: Session = Depends(get_db), x_user_id: str | None = Header(default=None)
) -> Actor:
    if get_settings().auth_mode != "dev":
        # Точка подключения SSO/JWT. Пока не настроено — доступ закрыт.
        raise Unauthorized("auth_not_configured", "Аутентификация не настроена")
    if not x_user_id:
        raise Unauthorized("unauthorized", "Требуется X-User-Id")
    try:
        uid = uuid.UUID(x_user_id)
    except ValueError as e:
        raise Unauthorized("unauthorized", "Некорректный X-User-Id") from e
    return load_actor(db, uid)
