"""Фоновая сверка ручных оснований с Didox.

Очередь — таблица в Postgres (SELECT ... FOR UPDATE SKIP LOCKED): воркеров можно
запускать сколько угодно, задача не обработается дважды.
Запуск: python -m app.worker
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.audit.service import record
from app.modules.didox.gateway import DidoxGateway, DidoxUnavailable, DocType, get_didox_gateway
from app.modules.didox.models import DidoxReconciliationTask
from app.modules.didox.rules import contract_candidates, poa_candidates, poa_confirmed_contracts
from app.modules.shipments.models import DidoxCheck, Shipment

log = logging.getLogger("gsm.worker")
MAX_BACKOFF = timedelta(hours=6)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace(" ", "")


def reconcile_one(db: Session, gw: DidoxGateway, task: DidoxReconciliationTask) -> None:
    sh = db.get(Shipment, task.shipment_id)
    assert sh is not None
    if sh.current_revision_no != task.revision_no or sh.didox_check != DidoxCheck.MANUAL_PENDING:
        task.status = "stale"  # основание уже изменено — сверяет новая задача
        return
    assert sh.customer_tin and sh.shipment_date and sh.contract_date and sh.poa_date
    try:
        docs = gw.list_documents(sh.customer_tin, {DocType.CONTRACT, DocType.POWER_OF_ATTORNEY})
    except DidoxUnavailable as e:
        task.attempts += 1
        task.last_error = e.message
        task.next_attempt_at = datetime.now(UTC) + min(
            timedelta(minutes=2**task.attempts), MAX_BACKOFF
        )
        return

    on = sh.shipment_date
    contract = next(
        (
            c.document
            for c in contract_candidates(docs, sh.customer_tin, on)
            if c.selectable
            and _norm(c.document.number) == _norm(sh.contract_number)
            and c.document.doc_date == sh.contract_date
        ),
        None,
    )
    confirmed = contract is None and any(
        _norm(d.contract_number) == _norm(sh.contract_number)
        and d.contract_date == sh.contract_date
        for d in poa_confirmed_contracts(docs, sh.customer_tin, on)
    )
    poa = next(
        (
            c.document
            for c in poa_candidates(
                docs,
                sh.customer_tin,
                on,
                contract_id=contract.id if contract else None,
                contract_number=sh.contract_number or "",
                contract_date=sh.contract_date,
            )
            if c.selectable
            and _norm(c.document.number) == _norm(sh.poa_number)
            and c.document.doc_date == sh.poa_date
        ),
        None,
    )
    ok = poa is not None and (contract is not None or confirmed)
    task.status = "done" if ok else "mismatch"
    task.result = {
        "contract_didox_id": contract.id if contract else None,
        "confirmed_by_poa": confirmed,
        "poa_didox_id": poa.id if poa else None,
    }
    # Ручное основание остаётся ручным: отметка источника не переписывается,
    # меняется только результат последующей сверки.
    sh.didox_check = DidoxCheck.VERIFIED if ok else DidoxCheck.MANUAL_MISMATCH
    record(
        db,
        entity_type="shipment",
        entity_id=sh.id,
        action="didox_reconciled",
        actor_id=None,
        ok=ok,
        **task.result,
    )


def run_batch(db: Session, gw: DidoxGateway, limit: int = 20) -> int:
    now = datetime.now(UTC)
    tasks = list(
        db.scalars(
            select(DidoxReconciliationTask)
            .where(
                DidoxReconciliationTask.status == "pending",
                or_(
                    DidoxReconciliationTask.next_attempt_at.is_(None),
                    DidoxReconciliationTask.next_attempt_at <= now,
                ),
            )
            .order_by(DidoxReconciliationTask.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for t in tasks:
        reconcile_one(db, gw, t)
    db.commit()
    return len(tasks)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    gw = get_didox_gateway()
    while True:
        db = next(get_db())
        try:
            n = run_batch(db, gw)
        except Exception:
            log.exception("reconciliation batch failed")
            n = 0
        finally:
            db.close()
        time.sleep(1 if n else 15)


if __name__ == "__main__":
    main()
