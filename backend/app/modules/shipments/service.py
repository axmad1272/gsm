"""Жизненный цикл ТТН модуля «Расход».

Методы не коммитят: транзакцией управляет вызывающий код (API или воркер).
Все проверки прав — здесь, а не в интерфейсе.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.auth import Actor
from app.core.config import get_settings
from app.core.errors import Conflict, DomainError, Forbidden, NotFound
from app.core.permissions import P
from app.core.storage import FileStorage
from app.modules.audit.service import record
from app.modules.didox.gateway import DidoxGateway
from app.modules.didox.models import DidoxReconciliationTask
from app.modules.didox.rules import BasisSelection, validate_basis
from app.modules.periods.service import ensure_open
from app.modules.shipments import pdf
from app.modules.shipments.models import (
    CONTENT_FIELDS,
    MOVEMENT_FIELDS,
    BasisSource,
    ContractKind,
    DidoxCheck,
    DocState,
    FuelMovement,
    MovementKind,
    NumberSequence,
    Operation,
    ReleasedNumber,
    ReviewKind,
    Shipment,
    ShipmentPrint,
    ShipmentRemark,
    ShipmentReview,
    ShipmentRevision,
)
from app.modules.shipments.quantities import compute
from app.modules.shipments.schemas import ShipmentContentIn
from app.modules.warehouses.models import Warehouse

TRANSFER_BASIS = "Перемещение"


def _now() -> datetime:
    return datetime.now(UTC)


def _json(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def snapshot(sh: Shipment) -> dict[str, Any]:
    return {f: _json(getattr(sh, f)) for f in CONTENT_FIELDS}


@dataclass
class BuiltContent:
    values: dict[str, Any]
    needs_reconciliation: bool = False


class ShipmentService:
    def __init__(
        self,
        db: Session,
        actor: Actor,
        didox: DidoxGateway,
        storage: FileStorage,
    ) -> None:
        self.db = db
        self.actor = actor
        self.didox = didox
        self.storage = storage
        self.settings = get_settings()

    # ------------------------------------------------------------------ helpers

    def _get(self, shipment_id: uuid.UUID, *, lock: bool = False) -> Shipment:
        q = select(Shipment).where(Shipment.id == shipment_id)
        if lock:
            q = q.with_for_update()
        sh = self.db.scalars(q).one_or_none()
        if sh is None or not self._visible(sh):
            raise NotFound("not_found", "ТТН не найдена")
        return sh

    def _visible(self, sh: Shipment) -> bool:
        if not self.actor.has(P.SHIPMENT_VIEW):
            return False
        wh = self.actor.warehouse_ids
        return sh.warehouse_id in wh or (sh.destination_warehouse_id in wh)

    def _warehouse(self, wid: int | None, field: str) -> Warehouse:
        wh = self.db.get(Warehouse, wid) if wid is not None else None
        if wh is None:
            raise DomainError("warehouse_not_found", "Склад не найден", field=field)
        return wh

    def _audit(self, sh: Shipment, action: str, **payload: Any) -> None:
        record(
            self.db,
            entity_type="shipment",
            entity_id=sh.id,
            action=action,
            actor_id=self.actor.id,
            revision_no=sh.current_revision_no,
            **payload,
        )

    # ------------------------------------------------------------ content build

    def _build(
        self,
        data: ShipmentContentIn,
        *,
        complete: bool,
        previous: Shipment | None = None,
    ) -> BuiltContent:
        """Собирает и проверяет содержание ТТН. complete=False — черновик (допускает пробелы)."""
        wh = self._warehouse(data.warehouse_id, "warehouse_id")
        self.actor.require_warehouse(wh.id)
        v: dict[str, Any] = {f: None for f in CONTENT_FIELDS}
        v.update(
            warehouse_id=wh.id,
            operation=data.operation.value,
            shipment_date=data.shipment_date,
            vehicle_plate=data.vehicle_plate,
            vehicle_owner_id=data.vehicle_owner_id,
            driver_pinfl=data.driver_pinfl,
            fuel_name=data.fuel_name,
            note=data.note,
        )
        missing: list[str] = []

        def need(field: str, value: Any) -> None:
            if value in (None, ""):
                missing.append(field)

        need("shipment_date", data.shipment_date)
        need("vehicle_plate", data.vehicle_plate)
        need("fuel_name", data.fuel_name)
        need("volume_l", data.volume_l)

        if data.volume_l is not None:
            has_second = data.density is not None or data.mass_t is not None
            if has_second or complete:
                q = compute(
                    wh.quantity_mode,
                    volume_l=data.volume_l,
                    density=data.density,
                    mass_t=data.mass_t,
                )
                v.update(
                    volume_ml=q.volume_ml,
                    mass_g=q.mass_g,
                    density=q.density,
                    quantity_mode=q.mode,
                )

        reconcile = False
        if data.operation == Operation.TRANSFER:
            dest = self._warehouse(data.destination_warehouse_id, "destination_warehouse_id")
            if dest.id == wh.id:
                raise DomainError(
                    "same_warehouse",
                    "Склад отправления и назначения должны различаться",
                    field="destination_warehouse_id",
                )
            v.update(
                destination_warehouse_id=dest.id,
                customer_name=dest.name,
                consignee_name=dest.name,
                basis_source=BasisSource.TRANSFER.value,
                didox_check=DidoxCheck.NOT_APPLICABLE.value,
                contract_number=TRANSFER_BASIS,
            )
        else:
            c, g = data.customer, data.consignee
            v.update(
                customer_name=c.name if c else None,
                customer_tin=c.tin if c else None,
                customer_counterparty_id=c.counterparty_id if c else None,
                consignee_name=g.name if g else None,
                consignee_tin=g.tin if g else None,
                consignee_counterparty_id=g.counterparty_id if g else None,
            )
            need("customer.name", v["customer_name"])
            need("customer.tin", v["customer_tin"])
            need("consignee.name", v["consignee_name"])
            need("consignee.tin", v["consignee_tin"])
            if data.basis is None:
                missing.append("basis")
            elif complete and not missing:
                reconcile = self._apply_basis(data, wh, v, previous)
            else:
                # Черновик: сохраняем выбор как есть, проверка — при подтверждении.
                b = data.basis
                v.update(
                    basis_source=b.mode,
                    contract_didox_id=b.contract_didox_id,
                    poa_didox_id=b.poa_didox_id,
                    contract_number=b.contract_number,
                    contract_date=b.contract_date,
                    poa_number=b.poa_number,
                    poa_date=b.poa_date,
                )
        if complete and missing:
            raise DomainError("incomplete", "Заполните обязательные поля", fields=missing)
        return BuiltContent(v, reconcile)

    def _apply_basis(
        self, data: ShipmentContentIn, wh: Warehouse, v: dict, previous: Shipment | None
    ) -> bool:
        b = data.basis
        assert b is not None and data.shipment_date is not None
        if b.mode == "manual":
            if not wh.allow_manual_basis:
                raise DomainError(
                    "manual_basis_disabled", "Ручной ввод основания для склада не разрешён"
                )
            self.actor.require(P.DIDOX_MANUAL_BASIS)
            for f in ("contract_number", "contract_date", "poa_number", "poa_date"):
                if getattr(b, f) in (None, ""):
                    raise DomainError("incomplete", "Заполните реквизиты основания", fields=[f])
            unchanged = (
                previous is not None
                and previous.basis_source == BasisSource.MANUAL
                and (previous.contract_number, previous.contract_date)
                == (b.contract_number, b.contract_date)
                and (previous.poa_number, previous.poa_date) == (b.poa_number, b.poa_date)
                and previous.customer_tin == v["customer_tin"]
                and previous.shipment_date == data.shipment_date
            )
            v.update(
                basis_source=BasisSource.MANUAL.value,
                didox_check=previous.didox_check if unchanged else DidoxCheck.MANUAL_PENDING.value,
                contract_kind=ContractKind.MANUAL.value,
                contract_number=b.contract_number,
                contract_date=b.contract_date,
                poa_number=b.poa_number,
                poa_date=b.poa_date,
            )
            return not unchanged

        if not b.poa_didox_id:
            raise DomainError("incomplete", "Выберите доверенность", fields=["basis.poa_didox_id"])
        sel = BasisSelection(
            poa_didox_id=b.poa_didox_id,
            contract_didox_id=b.contract_didox_id,
            confirmed_by_poa=b.confirmed_by_poa,
        )
        # Если основание, контрагент и дата не менялись — повторно в Didox не ходим.
        if (
            previous is not None
            and previous.basis_source == BasisSource.DIDOX
            and previous.poa_didox_id == sel.poa_didox_id
            and previous.contract_didox_id == sel.contract_didox_id
            and (previous.contract_kind == ContractKind.CONFIRMED_BY_POA) == sel.confirmed_by_poa
            and previous.customer_tin == v["customer_tin"]
            and previous.shipment_date == data.shipment_date
        ):
            for f in (
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
            ):
                v[f] = getattr(previous, f)
            return False

        vb = validate_basis(self.didox, sel, v["customer_tin"], data.shipment_date)
        v.update(
            basis_source=BasisSource.DIDOX.value,
            didox_check=DidoxCheck.VERIFIED.value,
            contract_kind=vb.contract_kind.value,
            contract_number=vb.contract_number,
            contract_date=vb.contract_date,
            contract_didox_id=vb.contract_didox_id,
            contract_didox_status=vb.contract_didox_status,
            poa_number=vb.poa_number,
            poa_date=vb.poa_date,
            poa_didox_id=vb.poa_didox_id,
            poa_didox_status=vb.poa_didox_status,
        )
        return False

    def _assign(self, sh: Shipment, values: dict[str, Any]) -> list[str]:
        changed = []
        for f, val in values.items():
            if _json(getattr(sh, f)) != _json(val):
                changed.append(f)
                setattr(sh, f, val)
        return changed

    # --------------------------------------------------------------- numbering

    def _lock_sequence(self, warehouse_id: int, year: int) -> NumberSequence:
        self.db.execute(
            pg_insert(NumberSequence)
            .values(warehouse_id=warehouse_id, year=year, next_number=1)
            .on_conflict_do_nothing()
        )
        return self.db.scalars(
            select(NumberSequence)
            .where(NumberSequence.warehouse_id == warehouse_id, NumberSequence.year == year)
            .with_for_update()
        ).one()

    def _reserve_number(self, sh: Shipment) -> None:
        assert sh.shipment_date is not None
        wid, year = sh.warehouse_id, sh.shipment_date.year
        seq = self._lock_sequence(wid, year)
        released = self.db.scalars(
            select(ReleasedNumber)
            .where(ReleasedNumber.warehouse_id == wid, ReleasedNumber.year == year)
            .order_by(ReleasedNumber.number)
            .limit(1)
        ).one_or_none()
        if released is not None:
            number = released.number
            self.db.delete(released)
        else:
            number = seq.next_number
            seq.next_number += 1
        sh.number, sh.number_warehouse_id, sh.number_year = number, wid, year

    def _release_number(self, sh: Shipment) -> None:
        """Освобождается только ненапечатанный номер."""
        if sh.number is None or sh.number_fixed:
            return
        assert sh.number_warehouse_id is not None and sh.number_year is not None
        self._lock_sequence(sh.number_warehouse_id, sh.number_year)
        self.db.add(
            ReleasedNumber(
                warehouse_id=sh.number_warehouse_id, year=sh.number_year, number=sh.number
            )
        )
        self.db.flush()

    def _number_key_changed(self, sh: Shipment) -> bool:
        return sh.number is not None and (
            sh.number_warehouse_id != sh.warehouse_id
            or sh.shipment_date is None
            or sh.number_year != sh.shipment_date.year
        )

    # --------------------------------------------------------------- movements

    def _legs(self, sh: Shipment) -> list[tuple[int, str, int]]:
        legs = [(sh.warehouse_id, "out", -1)]
        if sh.operation == Operation.TRANSFER:
            assert sh.destination_warehouse_id is not None
            legs.append((sh.destination_warehouse_id, "in", 1))
        return legs

    def _post_movements(self, sh: Shipment) -> None:
        assert sh.shipment_date and sh.volume_ml and sh.mass_g
        for wid, direction, sign in self._legs(sh):
            self.db.add(
                FuelMovement(
                    shipment_id=sh.id,
                    revision_no=sh.current_revision_no,
                    warehouse_id=wid,
                    direction=direction,
                    kind=MovementKind.NORMAL,
                    movement_date=sh.shipment_date,
                    volume_ml=sign * sh.volume_ml,
                    mass_g=sign * sh.mass_g,
                )
            )
        self.db.flush()

    def _active_movements(self, sh: Shipment) -> list[FuelMovement]:
        stornoed = select(FuelMovement.reverses_id).where(
            FuelMovement.shipment_id == sh.id, FuelMovement.reverses_id.is_not(None)
        )
        return list(
            self.db.scalars(
                select(FuelMovement).where(
                    FuelMovement.shipment_id == sh.id,
                    FuelMovement.kind == MovementKind.NORMAL,
                    FuelMovement.id.not_in(stornoed),
                )
            )
        )

    def _storno(self, sh: Shipment) -> None:
        for m in self._active_movements(sh):
            self.db.add(
                FuelMovement(
                    shipment_id=sh.id,
                    revision_no=sh.current_revision_no,
                    warehouse_id=m.warehouse_id,
                    direction=m.direction,
                    kind=MovementKind.STORNO,
                    reverses_id=m.id,
                    movement_date=m.movement_date,
                    volume_ml=-m.volume_ml,
                    mass_g=-m.mass_g,
                )
            )
        self.db.flush()

    # ------------------------------------------------------------------ drafts

    def create_draft(self, data: ShipmentContentIn) -> Shipment:
        self.actor.require(P.SHIPMENT_CREATE)
        built = self._build(data, complete=False)
        sh = Shipment(state=DocState.DRAFT, created_by=self.actor.id)
        self._assign(sh, built.values)
        sh.draft_payload = data.model_dump(mode="json")
        self.db.add(sh)
        self.db.flush()
        self._audit(sh, "draft_created")
        return sh

    def update_draft(self, shipment_id: uuid.UUID, data: ShipmentContentIn) -> Shipment:
        self.actor.require(P.SHIPMENT_CREATE)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.DRAFT:
            raise Conflict("not_draft", "Документ уже не черновик")
        self.actor.require_warehouse(sh.warehouse_id)
        built = self._build(data, complete=False)
        self._assign(sh, built.values)
        sh.draft_payload = data.model_dump(mode="json")
        self._audit(sh, "draft_updated")
        return sh

    def confirm_draft(
        self, shipment_id: uuid.UUID, data: ShipmentContentIn | None = None
    ) -> Shipment:
        self.actor.require(P.SHIPMENT_CREATE)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.DRAFT:
            raise Conflict("not_draft", "Документ уже не черновик")
        self.actor.require_warehouse(sh.warehouse_id)
        payload = data or ShipmentContentIn.model_validate(sh.draft_payload or {})
        self._make_official(sh, payload)
        self._audit(sh, "draft_confirmed")
        return sh

    # ---------------------------------------------------------------- creation

    def create(self, data: ShipmentContentIn) -> Shipment:
        self.actor.require(P.SHIPMENT_CREATE)
        sh = Shipment(state=DocState.DRAFT, created_by=self.actor.id)
        self._make_official(sh, data)
        self._audit(sh, "created")
        return sh

    def _make_official(self, sh: Shipment, data: ShipmentContentIn) -> None:
        built = self._build(data, complete=True)
        ensure_open(self.db, data.shipment_date)
        self._assign(sh, built.values)
        sh.state = DocState.CREATED
        sh.draft_payload = None
        sh.current_revision_no = 1
        self.db.add(sh)
        self.db.flush()
        self._reserve_number(sh)
        self.db.add(
            ShipmentRevision(
                shipment_id=sh.id,
                revision_no=1,
                kind="initial",
                content=snapshot(sh),
                changed_fields=[],
                author_id=self.actor.id,
            )
        )
        if built.needs_reconciliation:
            self._enqueue_reconciliation(sh)
        self.db.flush()

    def _enqueue_reconciliation(self, sh: Shipment) -> None:
        self.db.add(DidoxReconciliationTask(shipment_id=sh.id, revision_no=sh.current_revision_no))

    # ----------------------------------------------------------------- editing

    def corrections_left(self, sh: Shipment) -> int:
        return max(
            0,
            self.settings.operator_correction_limit + sh.corrections_granted - sh.corrections_used,
        )

    def edit(
        self,
        shipment_id: uuid.UUID,
        data: ShipmentContentIn,
        *,
        reason: str | None,
        admin_mode: bool = False,
    ) -> Shipment:
        sh = self._get(shipment_id, lock=True)
        if sh.state == DocState.CREATED:
            return self._edit_unprinted(sh, data)
        if sh.state == DocState.ISSUED:
            return self._correct(sh, data, reason=reason, admin_mode=admin_mode)
        raise Conflict("not_editable", "Документ в этом состоянии не редактируется", state=sh.state)

    def _edit_unprinted(self, sh: Shipment, data: ShipmentContentIn) -> Shipment:
        self.actor.require(P.SHIPMENT_EDIT)
        self.actor.require_warehouse(sh.warehouse_id)
        old_date = sh.shipment_date
        built = self._build(data, complete=True, previous=sh)
        ensure_open(self.db, old_date, data.shipment_date)
        changed = self._assign(sh, built.values)
        if not changed:
            raise DomainError("no_changes", "Изменений нет")
        if self._number_key_changed(sh):
            self._release_number(sh)
            self._reserve_number(sh)
        self._new_revision(sh, "edit_before_print", changed, reason=None, consumed=False)
        if built.needs_reconciliation:
            self._enqueue_reconciliation(sh)
        self._audit(sh, "edited_before_print", changed=changed)
        return sh

    def _correct(
        self, sh: Shipment, data: ShipmentContentIn, *, reason: str | None, admin_mode: bool
    ) -> Shipment:
        if not reason or not reason.strip():
            raise DomainError("reason_required", "Укажите причину исправления", field="reason")
        if admin_mode:
            self.actor.require(P.SHIPMENT_ADMIN_CORRECT)
        else:
            self.actor.require(P.SHIPMENT_EDIT)
            self.actor.require_warehouse(sh.warehouse_id)
            if sh.closed_at is not None:
                raise Conflict("closed", "ТТН закрыта: исправление только административное")
            if self.corrections_left(sh) <= 0:
                raise Conflict(
                    "correction_limit",
                    "Лимит исправлений исчерпан: требуется административное исправление",
                )
            if data.warehouse_id != sh.warehouse_id or data.shipment_date != sh.shipment_date:
                raise Forbidden(
                    "admin_correction_required",
                    "Склад и дату отгрузки меняет только административное исправление",
                )
        old_date = sh.shipment_date
        built = self._build(data, complete=True, previous=sh)
        ensure_open(self.db, old_date, data.shipment_date)
        # Сторно выполняется по движениям прежней редакции до изменения полей.
        will_change = [f for f, val in built.values.items() if _json(getattr(sh, f)) != _json(val)]
        if not will_change:
            raise DomainError("no_changes", "Изменений нет")
        sh.current_revision_no += 1
        if MOVEMENT_FIELDS & set(will_change):
            self._storno(sh)
        changed = self._assign(sh, built.values)
        if MOVEMENT_FIELDS & set(changed):
            self._post_movements(sh)
        if self._number_key_changed(sh):
            # Напечатанный номер не освобождается; новый — из другой последовательности.
            self._reserve_number(sh)
        sh.reprint_required = True
        if admin_mode:
            sh.closed_at = None
            sh.closed_by = None
        else:
            sh.corrections_used += 1
        self._new_revision(
            sh,
            "admin_correction" if admin_mode else "correction",
            changed,
            reason=reason.strip(),
            consumed=not admin_mode,
            bump=False,
        )
        if built.needs_reconciliation:
            self._enqueue_reconciliation(sh)
        self._audit(
            sh,
            "admin_corrected" if admin_mode else "corrected",
            changed=changed,
            reason=reason.strip(),
        )
        return sh

    def _new_revision(
        self,
        sh: Shipment,
        kind: str,
        changed: list[str],
        *,
        reason: str | None,
        consumed: bool,
        bump: bool = True,
    ) -> None:
        if bump:
            sh.current_revision_no += 1
        self.db.add(
            ShipmentRevision(
                shipment_id=sh.id,
                revision_no=sh.current_revision_no,
                kind=kind,
                content=snapshot(sh),
                changed_fields=changed,
                reason=reason,
                author_id=self.actor.id,
                consumed_attempt=consumed,
            )
        )
        self.db.flush()

    # ---------------------------------------------------------- delete / annul

    def delete(self, shipment_id: uuid.UUID) -> Shipment:
        self.actor.require(P.SHIPMENT_DELETE)
        sh = self._get(shipment_id, lock=True)
        self.actor.require_warehouse(sh.warehouse_id)
        if sh.state not in (DocState.DRAFT, DocState.CREATED):
            raise Conflict(
                "delete_after_print", "Напечатанную ТТН нельзя удалить — только аннулировать"
            )
        self._release_number(sh)
        sh.state = DocState.DELETED
        self._audit(sh, "deleted", number=sh.number)
        return sh

    def annul(self, shipment_id: uuid.UUID, reason: str) -> Shipment:
        self.actor.require(P.SHIPMENT_ANNUL)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.ISSUED:
            raise Conflict("not_issued", "Аннулировать можно только проведённую ТТН")
        active = self._active_movements(sh)
        ensure_open(self.db, sh.shipment_date, *(m.movement_date for m in active))
        self._storno(sh)
        sh.state = DocState.ANNULLED
        sh.annulled_at = _now()
        sh.annulled_by = self.actor.id
        sh.annul_reason = reason.strip()
        self._audit(sh, "annulled", reason=sh.annul_reason)
        return sh

    # ------------------------------------------------------------------ print

    def _print_context(self, sh: Shipment, print_id: uuid.UUID | None) -> pdf.PrintContext:
        wh = self.db.get(Warehouse, sh.warehouse_id)
        dest = (
            self.db.get(Warehouse, sh.destination_warehouse_id)
            if sh.destination_warehouse_id
            else None
        )
        return pdf.PrintContext(
            warehouse_name=wh.name if wh else "",
            destination_name=dest.name if dest else None,
            verify_url=(
                f"{self.settings.public_base_url}/api/verify/prints/{print_id}"
                if print_id
                else None
            ),
        )

    def preview(self, shipment_id: uuid.UUID) -> bytes:
        """Предпросмотр: ничего не проводит и не регистрирует."""
        sh = self._get(shipment_id)
        return pdf.render(sh, self._print_context(sh, None))

    def print(self, shipment_id: uuid.UUID) -> ShipmentPrint:
        self.actor.require(P.SHIPMENT_PRINT)
        sh = self._get(shipment_id, lock=True)
        self.actor.require_warehouse(sh.warehouse_id)
        if sh.state not in (DocState.CREATED, DocState.ISSUED):
            raise Conflict("not_printable", "Документ в этом состоянии не печатается")
        first = sh.state == DocState.CREATED
        if first:
            ensure_open(self.db, sh.shipment_date)

        print_id = uuid.uuid4()
        data = pdf.render(sh, self._print_context(sh, print_id))
        key = f"prints/{sh.id}/{print_id}.pdf"
        digest = self.storage.put(key, data)
        pr = ShipmentPrint(
            id=print_id,
            shipment_id=sh.id,
            revision_no=sh.current_revision_no,
            is_first=first,
            printed_by=self.actor.id,
            storage_key=key,
            sha256=digest,
        )
        self.db.add(pr)
        self.db.flush()
        for prev in self.db.scalars(
            select(ShipmentPrint).where(
                ShipmentPrint.shipment_id == sh.id,
                ShipmentPrint.superseded_by.is_(None),
                ShipmentPrint.id != pr.id,
            )
        ):
            prev.superseded_by = pr.id

        if first:
            # Первая печать: номер закрепляется, расход проводится.
            sh.state = DocState.ISSUED
            sh.number_fixed = True
            sh.first_printed_at = _now()
            self._post_movements(sh)
        # Перепечатка выдаёт актуальную бумагу и не проводит расход повторно.
        sh.reprint_required = False
        self._audit(sh, "printed", print_id=str(pr.id), first=first)
        return pr

    # ------------------------------------------------------- review and close

    def _latest_reviews(self, sh: Shipment) -> dict[str, ShipmentReview]:
        rows = self.db.scalars(
            select(ShipmentReview)
            .where(
                ShipmentReview.shipment_id == sh.id,
                ShipmentReview.revision_no == sh.current_revision_no,
            )
            .order_by(ShipmentReview.id)
        )
        return {r.kind: r for r in rows}

    def review(
        self, shipment_id: uuid.UUID, kind: ReviewKind, approved: bool, comment: str | None
    ) -> ShipmentReview:
        perm = P.REVIEW_DOCUMENTS if kind == ReviewKind.DOCUMENTS else P.REVIEW_ACTUAL
        self.actor.require(perm)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.ISSUED:
            raise Conflict("not_issued", "Проверяется только проведённая ТТН")
        if not approved and not (comment and comment.strip()):
            raise DomainError("comment_required", "Укажите причину отказа", field="comment")
        r = ShipmentReview(
            shipment_id=sh.id,
            revision_no=sh.current_revision_no,
            kind=kind.value,
            approved=approved,
            comment=comment,
            reviewer_id=self.actor.id,
        )
        self.db.add(r)
        self.db.flush()
        self._audit(sh, "reviewed", kind=kind.value, approved=approved)
        return r

    def close(self, shipment_id: uuid.UUID) -> Shipment:
        self.actor.require(P.SHIPMENT_CLOSE)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.ISSUED or sh.closed_at is not None:
            raise Conflict("not_closable", "ТТН нельзя закрыть в текущем состоянии")
        if sh.reprint_required:
            raise Conflict("reprint_required", "Сначала перепечатайте ТТН")
        reviews = self._latest_reviews(sh)
        for kind in (ReviewKind.DOCUMENTS, ReviewKind.ACTUAL):
            r = reviews.get(kind.value)
            if r is None or not r.approved:
                raise Conflict(
                    "review_missing",
                    "Нужны положительные проверки бухгалтера и финансиста по текущей редакции",
                    kind=kind.value,
                )
        sh.closed_at = _now()
        sh.closed_by = self.actor.id
        self._audit(sh, "closed")
        return sh

    def remark(self, shipment_id: uuid.UUID, text: str, grant_correction: bool) -> ShipmentRemark:
        self.actor.require(P.SHIPMENT_REMARK)
        sh = self._get(shipment_id, lock=True)
        if grant_correction:
            self.actor.require(P.SHIPMENT_GRANT_CORRECTION)
            if sh.state != DocState.ISSUED:
                raise Conflict("not_issued", "Исправление возвращается только для проведённой ТТН")
            if self.corrections_left(sh) > 0:
                raise Conflict("limit_not_exhausted", "У оператора ещё есть попытки исправления")
            sh.corrections_granted += 1
        rm = ShipmentRemark(
            shipment_id=sh.id,
            revision_no=sh.current_revision_no,
            author_id=self.actor.id,
            text=text.strip(),
            granted_correction=grant_correction,
        )
        self.db.add(rm)
        self.db.flush()
        self._audit(sh, "remark", remark_id=rm.id, granted_correction=grant_correction)
        return rm

    def set_accounting_date(self, shipment_id: uuid.UUID, value: date | None) -> Shipment:
        self.actor.require(P.ACCOUNTING_DATE)
        sh = self._get(shipment_id, lock=True)
        if sh.state != DocState.ISSUED:
            raise Conflict("not_issued", "Бухгалтерская дата ставится проведённой ТТН")
        ensure_open(self.db, sh.accounting_date, value)
        old = sh.accounting_date
        sh.accounting_date = value
        self._audit(sh, "accounting_date", old=_json(old), new=_json(value))
        return sh

    # ----------------------------------------------------------------- reads

    def balance(self, warehouse_id: int, on: date | None = None) -> dict[str, int]:
        self.actor.require_warehouse(warehouse_id)
        q = select(
            func.coalesce(func.sum(FuelMovement.volume_ml), 0),
            func.coalesce(func.sum(FuelMovement.mass_g), 0),
        ).where(FuelMovement.warehouse_id == warehouse_id)
        if on:
            q = q.where(FuelMovement.movement_date <= on)
        vol, mass = self.db.execute(q).one()
        return {"volume_ml": int(vol), "mass_g": int(mass)}
