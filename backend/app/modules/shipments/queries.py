"""Чтение: реестр, вкладки, карточка. Статус ТТН показывается по осям, а не одним ярлыком."""

import uuid
from datetime import date
from typing import Any, Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.auth import Actor
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.permissions import P
from app.modules.audit.models import AuditEvent
from app.modules.periods.models import ClosedPeriod
from app.modules.shipments.models import (
    PERSONAL_FIELDS,
    DocState,
    FuelMovement,
    Operation,
    ReviewKind,
    Shipment,
    ShipmentPrint,
    ShipmentRemark,
    ShipmentReview,
    ShipmentRevision,
)

Tab = Literal["drafts", "registry", "review", "history", "search"]

PERIOD_MEANING = "по дате отгрузки"


def _iso(v):
    return v.isoformat() if v is not None else None


def document_status(sh: Shipment) -> str:
    if sh.state == DocState.DRAFT:
        return "draft"
    if sh.state == DocState.CREATED:
        return "not_printed"
    if sh.state == DocState.ANNULLED:
        return "annulled"
    if sh.state == DocState.DELETED:
        return "deleted"
    return "reprint_required" if sh.reprint_required else "active"


def review_status(reviews: list[ShipmentReview], revision_no: int) -> dict[str, Any]:
    latest: dict[str, ShipmentReview] = {}
    for r in sorted(reviews, key=lambda r: r.id):
        if r.revision_no == revision_no:
            latest[r.kind] = r

    def one(kind: ReviewKind):
        r = latest.get(kind.value)
        if r is None:
            # Проверка прежней редакции не переносится на новое содержание.
            older = any(x.kind == kind.value and x.revision_no < revision_no for x in reviews)
            return "stale" if older else "pending"
        return "approved" if r.approved else "rejected"

    return {"documents": one(ReviewKind.DOCUMENTS), "actual": one(ReviewKind.ACTUAL)}


def serialize(
    sh: Shipment,
    actor: Actor,
    *,
    reviews: list[ShipmentReview] | None = None,
    closed_months: set[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    limit = get_settings().operator_correction_limit
    d: dict[str, Any] = {
        "id": str(sh.id),
        "number": sh.number,
        "number_year": sh.number_year,
        "number_fixed": sh.number_fixed,
        "revision_no": sh.current_revision_no,
        "warehouse_id": sh.warehouse_id,
        "operation": sh.operation,
        "destination_warehouse_id": sh.destination_warehouse_id,
        "shipment_date": _iso(sh.shipment_date),
        "customer": {
            "name": sh.customer_name,
            "tin": sh.customer_tin,
            "counterparty_id": sh.customer_counterparty_id,
        },
        "consignee": {
            "name": sh.consignee_name,
            "tin": sh.consignee_tin,
            "counterparty_id": sh.consignee_counterparty_id,
        },
        "basis": {
            "source": sh.basis_source,
            "contract_kind": sh.contract_kind,
            "contract_number": sh.contract_number,
            "contract_date": _iso(sh.contract_date),
            "contract_didox_id": sh.contract_didox_id,
            "contract_didox_status": sh.contract_didox_status,
            "poa_number": sh.poa_number,
            "poa_date": _iso(sh.poa_date),
            "poa_didox_id": sh.poa_didox_id,
            "poa_didox_status": sh.poa_didox_status,
        },
        "vehicle_plate": sh.vehicle_plate,
        "vehicle_owner_id": sh.vehicle_owner_id,
        "driver_pinfl": sh.driver_pinfl,
        "fuel": {
            "name": sh.fuel_name,
            "volume_ml": sh.volume_ml,
            "density": str(sh.density) if sh.density is not None else None,
            "mass_g": sh.mass_g,
            "quantity_mode": sh.quantity_mode,
        },
        "note": sh.note,
        "corrections": {
            "used": sh.corrections_used,
            "granted": sh.corrections_granted,
            "left": max(0, limit + sh.corrections_granted - sh.corrections_used),
        },
        "status": {
            "document": document_status(sh),
            "closed": sh.closed_at is not None,
            "didox": sh.didox_check,
            "onec": "none",  # появится с модулем сверки 1С
        },
        "created_at": _iso(sh.created_at),
        "first_printed_at": _iso(sh.first_printed_at),
    }
    if reviews is not None:
        d["status"]["review"] = review_status(reviews, sh.current_revision_no)
    if closed_months is not None and sh.shipment_date:
        closed = (sh.shipment_date.year, sh.shipment_date.month) in closed_months
        d["status"]["period"] = "closed" if closed else "open"
    if actor.has(P.ACCOUNTING_DATE) or actor.has(P.ONEC_VIEW):
        d["accounting_date"] = _iso(sh.accounting_date)
    if sh.state == DocState.ANNULLED:
        d["annulled"] = {"at": _iso(sh.annulled_at), "reason": sh.annul_reason}
    if not actor.has(P.SHIPMENT_VIEW_PERSONAL):
        for f in PERSONAL_FIELDS:
            d[f] = None
    return d


def _closed_months(db: Session) -> set[tuple[int, int]]:
    return {(y, m) for y, m in db.execute(select(ClosedPeriod.year, ClosedPeriod.month))}


def list_shipments(
    db: Session,
    actor: Actor,
    *,
    tab: Tab,
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    operation: Operation | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    actor.require(P.SHIPMENT_VIEW)
    wh = actor.warehouse_ids
    if warehouse_id is not None:
        actor.require_warehouse(warehouse_id)
        wh = frozenset({warehouse_id})
    cond = [or_(Shipment.warehouse_id.in_(wh), Shipment.destination_warehouse_id.in_(wh))]

    if tab == "drafts":
        cond.append(Shipment.state == DocState.DRAFT)
    elif tab == "registry":
        cond.append(Shipment.state.in_([DocState.CREATED, DocState.ISSUED]))
        cond.append(Shipment.closed_at.is_(None))
    elif tab == "history":
        cond.append(
            or_(
                Shipment.state == DocState.ANNULLED,
                and_(Shipment.state == DocState.ISSUED, Shipment.closed_at.is_not(None)),
            )
        )
    elif tab == "review":
        approved = {
            k: exists().where(
                ShipmentReview.shipment_id == Shipment.id,
                ShipmentReview.revision_no == Shipment.current_revision_no,
                ShipmentReview.kind == k.value,
                ShipmentReview.approved.is_(True),
            )
            for k in ReviewKind
        }
        cond += [
            Shipment.state == DocState.ISSUED,
            Shipment.closed_at.is_(None),
            or_(~approved[ReviewKind.DOCUMENTS], ~approved[ReviewKind.ACTUAL]),
        ]
    elif tab == "search":
        cond.append(Shipment.state != DocState.DELETED)

    if date_from:
        cond.append(Shipment.shipment_date >= date_from)
    if date_to:
        cond.append(Shipment.shipment_date <= date_to)
    if operation:
        cond.append(Shipment.operation == operation.value)
    if q:
        like = f"%{q.strip()}%"
        text_cond = [
            Shipment.customer_name.ilike(like),
            Shipment.consignee_name.ilike(like),
            Shipment.customer_tin == q.strip(),
            Shipment.vehicle_plate.ilike(like.replace(" ", "")),
        ]
        if q.strip().isdigit():
            text_cond.append(Shipment.number == int(q.strip()))
        cond.append(or_(*text_cond))

    rows = list(
        db.scalars(
            select(Shipment)
            .where(*cond)
            .order_by(Shipment.shipment_date.desc().nulls_last(), Shipment.number.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    ids = [r.id for r in rows]
    reviews: dict[uuid.UUID, list[ShipmentReview]] = {i: [] for i in ids}
    if ids:
        for r in db.scalars(select(ShipmentReview).where(ShipmentReview.shipment_id.in_(ids))):
            reviews[r.shipment_id].append(r)
    closed = _closed_months(db)
    return {
        # Возвращаем применённые фильтры, чтобы интерфейс показывал их явно.
        "applied_filters": {
            "tab": tab,
            "warehouse_ids": sorted(wh),
            "date_from": _iso(date_from),
            "date_to": _iso(date_to),
            "period_meaning": PERIOD_MEANING,
            "operation": operation.value if operation else None,
            "q": q,
        },
        "items": [serialize(r, actor, reviews=reviews[r.id], closed_months=closed) for r in rows],
        "limit": limit,
        "offset": offset,
    }


def card(db: Session, actor: Actor, shipment_id: uuid.UUID) -> dict[str, Any]:
    actor.require(P.SHIPMENT_VIEW)
    sh = db.get(Shipment, shipment_id)
    if sh is None or not (
        sh.warehouse_id in actor.warehouse_ids or sh.destination_warehouse_id in actor.warehouse_ids
    ):
        raise NotFound("not_found", "ТТН не найдена")
    reviews = list(db.scalars(select(ShipmentReview).where(ShipmentReview.shipment_id == sh.id)))
    data = serialize(sh, actor, reviews=reviews, closed_months=_closed_months(db))
    personal = actor.has(P.SHIPMENT_VIEW_PERSONAL)

    def content(c: dict) -> dict:
        return c if personal else {k: (None if k in PERSONAL_FIELDS else v) for k, v in c.items()}

    data["revisions"] = [
        {
            "revision_no": r.revision_no,
            "kind": r.kind,
            "changed_fields": r.changed_fields,
            "reason": r.reason,
            "author_id": str(r.author_id),
            "consumed_attempt": r.consumed_attempt,
            "created_at": _iso(r.created_at),
            "content": content(r.content),
        }
        for r in db.scalars(
            select(ShipmentRevision)
            .where(ShipmentRevision.shipment_id == sh.id)
            .order_by(ShipmentRevision.revision_no)
        )
    ]
    prints = list(
        db.scalars(
            select(ShipmentPrint)
            .where(ShipmentPrint.shipment_id == sh.id)
            .order_by(ShipmentPrint.printed_at)
        )
    )
    data["prints"] = [
        {
            "id": str(p.id),
            "revision_no": p.revision_no,
            "is_first": p.is_first,
            "printed_at": _iso(p.printed_at),
            "printed_by": str(p.printed_by),
            "status": print_status(sh, p),
        }
        for p in prints
    ]
    data["movements"] = [
        {
            "id": m.id,
            "revision_no": m.revision_no,
            "warehouse_id": m.warehouse_id,
            "direction": m.direction,
            "kind": m.kind,
            "reverses_id": m.reverses_id,
            "date": _iso(m.movement_date),
            "volume_ml": m.volume_ml,
            "mass_g": m.mass_g,
        }
        for m in db.scalars(
            select(FuelMovement).where(FuelMovement.shipment_id == sh.id).order_by(FuelMovement.id)
        )
    ]
    data["reviews"] = [
        {
            "kind": r.kind,
            "revision_no": r.revision_no,
            "approved": r.approved,
            "comment": r.comment,
            "reviewer_id": str(r.reviewer_id),
            "at": _iso(r.created_at),
        }
        for r in sorted(reviews, key=lambda r: r.id)
    ]
    data["remarks"] = [
        {
            "id": r.id,
            "revision_no": r.revision_no,
            "text": r.text,
            "granted_correction": r.granted_correction,
            "author_id": str(r.author_id),
            "at": _iso(r.created_at),
        }
        for r in db.scalars(
            select(ShipmentRemark)
            .where(ShipmentRemark.shipment_id == sh.id)
            .order_by(ShipmentRemark.id)
        )
    ]
    data["audit"] = [
        {"action": a.action, "actor_id": str(a.actor_id), "at": _iso(a.at), "payload": a.payload}
        for a in db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "shipment", AuditEvent.entity_id == str(sh.id))
            .order_by(AuditEvent.id)
        )
    ]
    return data


def print_status(sh: Shipment, p: ShipmentPrint) -> str:
    if sh.state == DocState.ANNULLED:
        return "annulled"
    if p.revision_no != sh.current_revision_no:
        return "outdated_revision"
    if p.superseded_by is not None:
        return "superseded"
    return "current"


def verify_print(db: Session, print_id: uuid.UUID) -> dict[str, Any]:
    """Публичная проверка конкретного бумажного экземпляра по QR. Без персональных данных."""
    p = db.get(ShipmentPrint, print_id)
    if p is None:
        raise NotFound("not_found", "Печатный экземпляр не найден")
    sh = db.get(Shipment, p.shipment_id)
    assert sh is not None
    return {
        "print_id": str(p.id),
        "status": print_status(sh, p),
        "number": sh.number,
        "shipment_date": _iso(sh.shipment_date),
        "warehouse_id": sh.warehouse_id,
        "printed_revision": p.revision_no,
        "current_revision": sh.current_revision_no,
        "printed_at": _iso(p.printed_at),
    }
