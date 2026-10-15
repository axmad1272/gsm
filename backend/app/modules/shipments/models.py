import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin


class Operation(StrEnum):
    SALE = "sale"  # отгрузка контрагенту
    TRANSFER = "transfer"  # перемещение между своими складами


class DocState(StrEnum):
    DRAFT = "draft"  # не официальная ТТН, без номера и расхода
    CREATED = "created"  # номер зарезервирован, не напечатана, расхода нет
    ISSUED = "issued"  # напечатана: номер закреплён, расход проведён
    ANNULLED = "annulled"  # аннулирована: движения сторнированы, номер не освобождается
    DELETED = "deleted"  # удалена до первой печати, номер освобождён


class BasisSource(StrEnum):
    DIDOX = "didox"
    MANUAL = "manual"  # ручной ввод при недоступности Didox
    TRANSFER = "transfer"  # «Перемещение», Didox не применяется


class DidoxCheck(StrEnum):
    VERIFIED = "verified"
    MANUAL_PENDING = "manual_pending"  # ждёт последующей сверки
    MANUAL_MISMATCH = "manual_mismatch"  # последующая сверка не подтвердила
    NOT_APPLICABLE = "not_applicable"


class ContractKind(StrEnum):
    DIDOX_CONTRACT = "didox_contract"
    CONFIRMED_BY_POA = "confirmed_by_poa"  # старый договор, подтверждён доверенностью
    MANUAL = "manual"


class Shipment(Base, CreatedAtMixin):
    """ТТН. id — постоянная идентичность, number — человекочитаемый номер склада/года.
    Поля содержания здесь — текущая редакция (для реестра и поиска);
    полные снимки всех редакций — в shipment_revisions."""

    __tablename__ = "shipments"
    __table_args__ = (
        # Номер уникален в последовательности склада и года среди неудалённых ТТН.
        Index(
            "uq_shipments_number_active",
            "number_warehouse_id",
            "number_year",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL AND state <> 'deleted'"),
        ),
        Index("ix_shipments_registry", "warehouse_id", "state", "shipment_date"),
        CheckConstraint(
            "operation <> 'transfer' OR (destination_warehouse_id IS NOT NULL "
            "AND destination_warehouse_id <> warehouse_id)",
            name="transfer_destination",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String(16), default=DocState.DRAFT)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

    # Нумерация
    number: Mapped[int | None] = mapped_column(Integer)
    number_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    number_year: Mapped[int | None] = mapped_column(Integer)
    number_fixed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Редакции и печать
    current_revision_no: Mapped[int] = mapped_column(Integer, default=1)
    reprint_required: Mapped[bool] = mapped_column(Boolean, default=False)
    first_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrections_used: Mapped[int] = mapped_column(Integer, default=0)
    corrections_granted: Mapped[int] = mapped_column(Integer, default=0)

    # Содержание текущей редакции
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    operation: Mapped[str] = mapped_column(String(16))
    destination_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    shipment_date: Mapped[date | None] = mapped_column(Date)

    customer_name: Mapped[str | None] = mapped_column(String(300))
    customer_tin: Mapped[str | None] = mapped_column(String(14))
    customer_counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    consignee_name: Mapped[str | None] = mapped_column(String(300))
    consignee_tin: Mapped[str | None] = mapped_column(String(14))
    consignee_counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))

    basis_source: Mapped[str | None] = mapped_column(String(16))
    didox_check: Mapped[str | None] = mapped_column(String(20))
    contract_kind: Mapped[str | None] = mapped_column(String(20))
    contract_number: Mapped[str | None] = mapped_column(String(100))
    contract_date: Mapped[date | None] = mapped_column(Date)
    contract_didox_id: Mapped[str | None] = mapped_column(String(64))
    contract_didox_status: Mapped[str | None] = mapped_column(String(40))
    poa_number: Mapped[str | None] = mapped_column(String(100))
    poa_date: Mapped[date | None] = mapped_column(Date)
    poa_didox_id: Mapped[str | None] = mapped_column(String(64))
    poa_didox_status: Mapped[str | None] = mapped_column(String(40))

    vehicle_plate: Mapped[str | None] = mapped_column(String(20))
    vehicle_owner_id: Mapped[str | None] = mapped_column(String(14))
    driver_pinfl: Mapped[str | None] = mapped_column(String(14))

    fuel_name: Mapped[str | None] = mapped_column(String(200))
    volume_ml: Mapped[int | None] = mapped_column(BigInteger)
    mass_g: Mapped[int | None] = mapped_column(BigInteger)
    density: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    quantity_mode: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)

    # Исходный ввод черновика (может быть неполным), из него собирается ТТН.
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Контроль
    accounting_date: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None]
    annulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    annulled_by: Mapped[uuid.UUID | None]
    annul_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Поля, составляющие содержание редакции (снимок для истории и сравнения).
CONTENT_FIELDS: tuple[str, ...] = (
    "warehouse_id",
    "operation",
    "destination_warehouse_id",
    "shipment_date",
    "customer_name",
    "customer_tin",
    "customer_counterparty_id",
    "consignee_name",
    "consignee_tin",
    "consignee_counterparty_id",
    "basis_source",
    "didox_check",
    "contract_kind",
    "contract_number",
    "contract_date",
    "contract_didox_id",
    "contract_didox_status",
    "poa_number",
    "poa_date",
    "poa_didox_id",
    "poa_didox_status",
    "vehicle_plate",
    "vehicle_owner_id",
    "driver_pinfl",
    "fuel_name",
    "volume_ml",
    "mass_g",
    "density",
    "quantity_mode",
    "note",
)

# Изменение этих полей меняет фактическое движение топлива.
MOVEMENT_FIELDS = frozenset(
    {
        "warehouse_id",
        "destination_warehouse_id",
        "operation",
        "shipment_date",
        "volume_ml",
        "mass_g",
    }
)
PERSONAL_FIELDS = frozenset({"vehicle_owner_id", "driver_pinfl"})


class ShipmentRevision(Base, CreatedAtMixin):
    __tablename__ = "shipment_revisions"
    __table_args__ = (UniqueConstraint("shipment_id", "revision_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"))
    revision_no: Mapped[int] = mapped_column(Integer)
    # initial | edit_before_print | correction | admin_correction
    kind: Mapped[str] = mapped_column(String(24))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    consumed_attempt: Mapped[bool] = mapped_column(Boolean, default=False)


class ShipmentPrint(Base):
    """Конкретный выпуск PDF. Его id зашит в QR."""

    __tablename__ = "shipment_prints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    is_first: Mapped[bool] = mapped_column(Boolean, default=False)
    printed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    printed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipment_prints.id"))


class MovementKind(StrEnum):
    NORMAL = "normal"
    STORNO = "storno"


class FuelMovement(Base, CreatedAtMixin):
    """Фактическое движение топлива. Знак: расход отрицательный, приход положительный.
    Сторно — запись с противоположным знаком, ссылающаяся на исходную."""

    __tablename__ = "fuel_movements"
    __table_args__ = (
        # Один проводимый набор движений на редакцию: повтор не создаёт двойной расход.
        Index(
            "uq_fuel_movements_normal",
            "shipment_id",
            "revision_no",
            "warehouse_id",
            "direction",
            unique=True,
            postgresql_where=text("kind = 'normal'"),
        ),
        Index("ix_fuel_movements_wh_date", "warehouse_id", "movement_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    direction: Mapped[str] = mapped_column(String(3))  # out | in
    kind: Mapped[str] = mapped_column(String(8), default=MovementKind.NORMAL)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_movements.id"), unique=True)
    movement_date: Mapped[date] = mapped_column(Date)
    volume_ml: Mapped[int] = mapped_column(BigInteger)
    mass_g: Mapped[int] = mapped_column(BigInteger)


class NumberSequence(Base):
    __tablename__ = "number_sequences"
    __table_args__ = (PrimaryKeyConstraint("warehouse_id", "year"),)

    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    year: Mapped[int] = mapped_column(Integer)
    next_number: Mapped[int] = mapped_column(Integer, default=1)


class ReleasedNumber(Base):
    """Номер, освобождённый удалением ненапечатанной ТТН. Выдаётся повторно первым."""

    __tablename__ = "released_numbers"
    __table_args__ = (PrimaryKeyConstraint("warehouse_id", "year", "number"),)

    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)


class ReviewKind(StrEnum):
    DOCUMENTS = "documents"  # бухгалтер
    ACTUAL = "actual"  # финансист


class ShipmentReview(Base, CreatedAtMixin):
    """Решение относится к конкретной редакции."""

    __tablename__ = "shipment_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    approved: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class ShipmentRemark(Base, CreatedAtMixin):
    __tablename__ = "shipment_remarks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    granted_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
