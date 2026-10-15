import uuid
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin


class QuantityMode(StrEnum):
    # Оператор вводит литры и плотность, система считает массу (Рохат, Навои).
    VOLUME_DENSITY = "volume_density"
    # Оператор вводит литры и массу, система считает плотность (Алмалык).
    VOLUME_MASS = "volume_mass"


class Warehouse(Base, CreatedAtMixin):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity_mode: Mapped[str] = mapped_column(String(32), default=QuantityMode.VOLUME_DENSITY)
    # Разрешён ли контролируемый ручной ввод основания при недоступности Didox.
    allow_manual_basis: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base, CreatedAtMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    granted_permissions: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    revoked_permissions: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)


class UserWarehouse(Base):
    __tablename__ = "user_warehouses"
    __table_args__ = (UniqueConstraint("user_id", "warehouse_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"))
