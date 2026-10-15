from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.modules.periods.models import ClosedPeriod


def ensure_open(db: Session, *dates: date | None) -> None:
    """Все затронутые операцией даты (старые и новые) должны быть в открытых месяцах."""
    months = {(d.year, d.month) for d in dates if d is not None}
    if not months:
        return
    closed = db.execute(
        select(ClosedPeriod.year, ClosedPeriod.month).where(
            ClosedPeriod.year.in_({y for y, _ in months}),
        )
    ).all()
    hit = sorted(months & {(y, m) for y, m in closed})
    if hit:
        y, m = hit[0]
        raise Conflict("period_closed", f"Период {m:02d}.{y} закрыт", year=y, month=m)
