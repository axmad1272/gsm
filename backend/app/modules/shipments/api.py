import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core import idempotency
from app.core.auth import Actor, current_actor
from app.core.db import get_db
from app.core.errors import NotFound
from app.core.permissions import P
from app.core.storage import FileStorage, get_storage
from app.modules.didox.gateway import DidoxGateway, get_didox_gateway
from app.modules.shipments import queries
from app.modules.shipments.models import Operation, Shipment, ShipmentPrint
from app.modules.shipments.schemas import (
    AccountingDateIn,
    ConfirmDraftIn,
    EditIn,
    ReasonIn,
    RemarkIn,
    ReviewIn,
    ShipmentContentIn,
)
from app.modules.shipments.service import ShipmentService

router = APIRouter(prefix="/api", tags=["shipments"])


def service(
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
    didox: DidoxGateway = Depends(get_didox_gateway),
    storage: FileStorage = Depends(get_storage),
) -> ShipmentService:
    return ShipmentService(db, actor, didox, storage)


def _card(svc: ShipmentService, sh: Shipment) -> dict:
    svc.db.flush()
    return queries.card(svc.db, svc.actor, sh.id)


def _do(svc: ShipmentService, key: str | None, request: dict, fn) -> dict:
    return idempotency.run(svc.db, svc.actor.id, key, request, fn)


# ------------------------------------------------------------------- reading


@router.get("/shipments")
def list_shipments(
    tab: queries.Tab = "registry",
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    operation: Operation | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
):
    return queries.list_shipments(
        db,
        actor,
        tab=tab,
        warehouse_id=warehouse_id,
        date_from=date_from,
        date_to=date_to,
        operation=operation,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/shipments/{shipment_id}")
def get_shipment(
    shipment_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
):
    return queries.card(db, actor, shipment_id)


@router.get("/shipments/{shipment_id}/preview.pdf")
def preview(shipment_id: uuid.UUID, svc: ShipmentService = Depends(service)):
    return Response(svc.preview(shipment_id), media_type="application/pdf")


@router.get("/prints/{print_id}/pdf")
def print_pdf(
    print_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
    storage: FileStorage = Depends(get_storage),
):
    p = db.get(ShipmentPrint, print_id)
    if p is None:
        raise NotFound("not_found", "Печать не найдена")
    queries.card(db, actor, p.shipment_id)  # проверка доступа
    return Response(storage.get(p.storage_key), media_type="application/pdf")


@router.get("/verify/prints/{print_id}")
def verify_print(print_id: uuid.UUID, db: Session = Depends(get_db)):
    return queries.verify_print(db, print_id)


@router.get("/warehouses/{warehouse_id}/balance")
def balance(warehouse_id: int, on: date | None = None, svc: ShipmentService = Depends(service)):
    svc.actor.require(P.SHIPMENT_VIEW)
    return svc.balance(warehouse_id, on)


# ------------------------------------------------------------------- writing


@router.post("/shipments/drafts", status_code=201)
def create_draft(
    body: ShipmentContentIn,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    req = {"op": "draft", "body": body.model_dump(mode="json")}
    return _do(svc, idempotency_key, req, lambda: _card(svc, svc.create_draft(body)))


@router.put("/shipments/drafts/{shipment_id}")
def update_draft(
    shipment_id: uuid.UUID, body: ShipmentContentIn, svc: ShipmentService = Depends(service)
):
    return _do(svc, None, {}, lambda: _card(svc, svc.update_draft(shipment_id, body)))


@router.post("/shipments/drafts/{shipment_id}/confirm")
def confirm_draft(
    shipment_id: uuid.UUID,
    body: ConfirmDraftIn,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    req = {"op": "confirm", "id": str(shipment_id), "body": body.model_dump(mode="json")}
    return _do(
        svc,
        idempotency_key,
        req,
        lambda: _card(svc, svc.confirm_draft(shipment_id, body.content)),
    )


@router.post("/shipments", status_code=201)
def create(
    body: ShipmentContentIn,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    req = {"op": "create", "body": body.model_dump(mode="json")}
    return _do(svc, idempotency_key, req, lambda: _card(svc, svc.create(body)))


@router.put("/shipments/{shipment_id}")
def edit(
    shipment_id: uuid.UUID,
    body: EditIn,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    req = {"op": "edit", "id": str(shipment_id), "body": body.model_dump(mode="json")}
    return _do(
        svc,
        idempotency_key,
        req,
        lambda: _card(
            svc,
            svc.edit(shipment_id, body.content, reason=body.reason, admin_mode=body.admin_mode),
        ),
    )


@router.delete("/shipments/{shipment_id}")
def delete(shipment_id: uuid.UUID, svc: ShipmentService = Depends(service)):
    return _do(svc, None, {}, lambda: _card(svc, svc.delete(shipment_id)))


@router.post("/shipments/{shipment_id}/print")
def print_(
    shipment_id: uuid.UUID,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    def fn():
        pr = svc.print(shipment_id)
        return {"print_id": str(pr.id), "is_first": pr.is_first, "shipment": _card(svc, pr_sh(pr))}

    def pr_sh(pr: ShipmentPrint) -> Shipment:
        sh = svc.db.get(Shipment, pr.shipment_id)
        assert sh is not None
        return sh

    return _do(svc, idempotency_key, {"op": "print", "id": str(shipment_id)}, fn)


@router.post("/shipments/{shipment_id}/annul")
def annul(
    shipment_id: uuid.UUID,
    body: ReasonIn,
    svc: ShipmentService = Depends(service),
    idempotency_key: str | None = Header(default=None),
):
    req = {"op": "annul", "id": str(shipment_id), "body": body.model_dump()}
    return _do(svc, idempotency_key, req, lambda: _card(svc, svc.annul(shipment_id, body.reason)))


@router.post("/shipments/{shipment_id}/reviews")
def review(shipment_id: uuid.UUID, body: ReviewIn, svc: ShipmentService = Depends(service)):
    def fn():
        svc.review(shipment_id, body.kind, body.approved, body.comment)
        return queries.card(svc.db, svc.actor, shipment_id)

    return _do(svc, None, {}, fn)


@router.post("/shipments/{shipment_id}/remarks")
def remark(shipment_id: uuid.UUID, body: RemarkIn, svc: ShipmentService = Depends(service)):
    def fn():
        svc.remark(shipment_id, body.text, body.grant_correction)
        return queries.card(svc.db, svc.actor, shipment_id)

    return _do(svc, None, {}, fn)


@router.post("/shipments/{shipment_id}/close")
def close(shipment_id: uuid.UUID, svc: ShipmentService = Depends(service)):
    return _do(svc, None, {}, lambda: _card(svc, svc.close(shipment_id)))


@router.put("/shipments/{shipment_id}/accounting-date")
def accounting_date(
    shipment_id: uuid.UUID, body: AccountingDateIn, svc: ShipmentService = Depends(service)
):
    return _do(
        svc,
        None,
        {},
        lambda: _card(svc, svc.set_accounting_date(shipment_id, body.accounting_date)),
    )
