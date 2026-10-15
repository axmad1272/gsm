"""Начальные данные для разработки: три склада и пользователи по ролям.
Запуск: python -m app.seed"""

import uuid

from sqlalchemy import select

import app.models  # noqa: F401
from app.core.db import get_db
from app.modules.warehouses.models import QuantityMode, User, UserWarehouse, Warehouse

WAREHOUSES = [
    ("ROHAT", "Рохат", QuantityMode.VOLUME_DENSITY, True),
    ("ALMALYK", "Алмалык", QuantityMode.VOLUME_MASS, False),
    ("NAVOI", "Навои", QuantityMode.VOLUME_DENSITY, False),
]
USERS = ["operator", "accountant", "financier", "director", "admin", "technologist"]


def main() -> None:
    db = next(get_db())
    for code, name, mode, manual in WAREHOUSES:
        if db.scalars(select(Warehouse).where(Warehouse.code == code)).first() is None:
            db.add(Warehouse(code=code, name=name, quantity_mode=mode, allow_manual_basis=manual))
    db.flush()
    wh_ids = list(db.scalars(select(Warehouse.id)))
    for role in USERS:
        if db.scalars(select(User).where(User.login == role)).first() is None:
            u = User(id=uuid.uuid4(), login=role, full_name=role, roles=[role])
            db.add(u)
            db.flush()
            for wid in wh_ids:
                db.add(UserWarehouse(user_id=u.id, warehouse_id=wid))
    db.commit()
    for u in db.scalars(select(User).order_by(User.login)):
        print(f"{u.login:14} X-User-Id: {u.id}")


if __name__ == "__main__":
    main()
