"""Повторный клик или повтор после сетевого сбоя с тем же Idempotency-Key
возвращает сохранённый результат и не выполняет операцию второй раз."""

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import String, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base, CreatedAtMixin
from app.core.errors import Conflict, DomainError


class IdempotencyKey(Base, CreatedAtMixin):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


def run(
    db: Session,
    user_id: uuid.UUID,
    key: str | None,
    request: Any,
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if not key:
        result = fn()
        db.commit()
        return result
    if len(key) > 100:
        raise DomainError("bad_idempotency_key", "Idempotency-Key длиннее 100 символов")
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
    # Конкурентный запрос с тем же ключом ждёт на уникальном ключе до коммита первого.
    inserted = db.execute(
        pg_insert(IdempotencyKey)
        .values(user_id=user_id, key=key, request_hash=digest)
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    ).first()
    if inserted is None:
        row = db.scalars(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == key
            )
        ).one()
        if row.request_hash != digest:
            raise Conflict("idempotency_mismatch", "Ключ уже использован для другого запроса")
        response = row.response or {}
        db.rollback()
        return response
    result = fn()
    db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
        .values(response=result)
    )
    db.commit()
    return result
