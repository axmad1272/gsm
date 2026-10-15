from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.auth import Actor, current_actor
from app.core.db import get_db
from app.core.permissions import P
from app.modules.audit.service import record
from app.modules.periods.models import ClosedPeriod

router = APIRouter(prefix="/api/periods", tags=["periods"])


@router.get("")
def list_closed(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    rows = db.scalars(select(ClosedPeriod).order_by(ClosedPeriod.year, ClosedPeriod.month))
    return [{"year": p.year, "month": p.month, "closed_at": p.closed_at.isoformat()} for p in rows]


@router.post("/{year}/{month}/close")
def close_period(
    year: int = Path(ge=2000, le=2100),
    month: int = Path(ge=1, le=12),
    db: Session = Depends(get_db),
    actor: Actor = Depends(current_actor),
):
    actor.require(P.PERIOD_CLOSE)
    db.execute(
        pg_insert(ClosedPeriod)
        .values(year=year, month=month, closed_by=actor.id)
        .on_conflict_do_nothing()
    )
    record(
        db,
        entity_type="period",
        entity_id=f"{year}-{month:02d}",
        action="closed",
        actor_id=actor.id,
    )
    db.commit()
    return {"year": year, "month": month, "status": "closed"}
