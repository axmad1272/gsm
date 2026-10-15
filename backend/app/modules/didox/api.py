import re
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.auth import Actor, current_actor
from app.core.db import get_db
from app.core.permissions import P
from app.modules.didox.gateway import DidoxDocument, DidoxGateway, DocType, get_didox_gateway
from app.modules.didox.models import Counterparty, CounterpartyAlias
from app.modules.didox.rules import (
    Candidate,
    contract_candidates,
    poa_candidates,
    poa_confirmed_contracts,
)

router = APIRouter(prefix="/api", tags=["didox"])


def normalize_name(name: str) -> str:
    s = name.lower().replace("ё", "е")
    s = re.sub(r"\b(ооо|мчж|ao|ао|ooo|mchj|xk|чп|ип)\b", " ", s)
    s = re.sub(r"[\"'«»“”.,()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _doc(d: DidoxDocument) -> dict:
    return {
        "id": d.id,
        "type": d.doc_type.value,
        "direction": d.direction.value,
        # Статус подписи показывается как есть, без превращения в «Подписано».
        "sign_status": d.status.value,
        "number": d.number,
        "date": d.doc_date.isoformat(),
        "valid_from": d.valid_from.isoformat() if d.valid_from else None,
        "valid_to": d.valid_to.isoformat() if d.valid_to else None,
        "contract_number": d.contract_number,
        "contract_date": d.contract_date.isoformat() if d.contract_date else None,
    }


def _cand(c: Candidate) -> dict:
    return {**_doc(c.document), "selectable": c.selectable, "reason": c.reason}


@router.get("/counterparties/search")
def search_counterparties(
    q: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
    gw: DidoxGateway = Depends(get_didox_gateway),
):
    actor.require(P.SHIPMENT_CREATE)
    norm = normalize_name(q)
    saved = db.execute(
        select(Counterparty)
        .join(CounterpartyAlias, CounterpartyAlias.counterparty_id == Counterparty.id)
        .where(CounterpartyAlias.normalized.contains(norm))
        .limit(20)
    ).scalars()
    by_tin = db.scalars(select(Counterparty).where(Counterparty.tin == q.strip())).all()
    local = {
        c.tin: {"tin": c.tin, "name": c.name, "counterparty_id": c.id, "source": "saved"}
        for c in [*saved, *by_tin]
    }
    remote = [
        {"tin": c.tin, "name": c.name, "counterparty_id": None, "source": "didox"}
        for c in gw.search_companies(q)
        if c.tin not in local
    ]
    return {"items": [*local.values(), *remote]}


class CounterpartyIn(BaseModel):
    tin: str = Field(pattern=r"^(\d{9}|\d{14})$")
    name: str = Field(min_length=1, max_length=300)
    alias: str | None = Field(default=None, max_length=300)


@router.post("/counterparties")
def save_counterparty(
    body: CounterpartyIn, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)
):
    """Сохраняет соответствие «название → идентификатор»."""
    actor.require(P.SHIPMENT_CREATE)
    db.execute(
        pg_insert(Counterparty)
        .values(tin=body.tin, name=body.name)
        .on_conflict_do_nothing(index_elements=["tin"])
    )
    cp = db.scalars(select(Counterparty).where(Counterparty.tin == body.tin)).one()
    for alias in {body.name, body.alias} - {None}:
        db.execute(
            pg_insert(CounterpartyAlias)
            .values(
                counterparty_id=cp.id,
                alias=alias,
                normalized=normalize_name(alias),
                created_by=actor.id,
            )
            .on_conflict_do_nothing(index_elements=["normalized"])
        )
    db.commit()
    return {"counterparty_id": cp.id, "tin": cp.tin, "name": cp.name}


@router.get("/didox/contracts")
def contracts(
    partner_tin: str,
    on: date,
    actor: Actor = Depends(current_actor),
    gw: DidoxGateway = Depends(get_didox_gateway),
):
    actor.require(P.SHIPMENT_CREATE)
    docs = gw.list_documents(partner_tin, {DocType.CONTRACT, DocType.POWER_OF_ATTORNEY})
    return {
        "contracts": [_cand(c) for c in contract_candidates(docs, partner_tin, on)],
        # Старый договор с автопролонгацией: основание «Подтверждён доверенностью».
        "confirmed_by_poa": [_doc(d) for d in poa_confirmed_contracts(docs, partner_tin, on)],
    }


@router.get("/didox/powers-of-attorney")
def powers_of_attorney(
    partner_tin: str,
    on: date,
    contract_number: str,
    contract_date: date,
    contract_id: str | None = None,
    actor: Actor = Depends(current_actor),
    gw: DidoxGateway = Depends(get_didox_gateway),
):
    actor.require(P.SHIPMENT_CREATE)
    docs = gw.list_documents(partner_tin, {DocType.POWER_OF_ATTORNEY})
    return {
        "items": [
            _cand(c)
            for c in poa_candidates(
                docs,
                partner_tin,
                on,
                contract_id=contract_id,
                contract_number=contract_number,
                contract_date=contract_date,
            )
        ]
    }
