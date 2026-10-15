import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin


class Counterparty(Base, CreatedAtMixin):
    """Контрагент устанавливается по идентификатору (ИНН 9 цифр / ПИНФЛ 14 цифр),
    название используется только для поиска."""

    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(primary_key=True)
    tin: Mapped[str] = mapped_column(String(14), unique=True)
    name: Mapped[str] = mapped_column(String(300))


class CounterpartyAlias(Base, CreatedAtMixin):
    __tablename__ = "counterparty_aliases"
    __table_args__ = (UniqueConstraint("normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"))
    alias: Mapped[str] = mapped_column(String(300))
    normalized: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None]


class DidoxReconciliationTask(Base, CreatedAtMixin):
    """Очередь последующей сверки ручного основания с Didox.
    Обрабатывается воркером через SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "didox_reconciliation_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|mismatch
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
