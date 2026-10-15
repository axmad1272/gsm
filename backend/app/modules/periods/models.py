import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, PrimaryKeyConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ClosedPeriod(Base):
    """Закрытый месяц. Закрывается целиком по компании."""

    __tablename__ = "closed_periods"
    __table_args__ = (PrimaryKeyConstraint("year", "month"),)

    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    closed_by: Mapped[uuid.UUID]
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
