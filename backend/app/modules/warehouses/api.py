from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Actor, current_actor
from app.core.db import get_db
from app.modules.warehouses.models import Warehouse

router = APIRouter(prefix="/api", tags=["warehouses"])


@router.get("/me")
def me(actor: Actor = Depends(current_actor)):
    return {
        "id": str(actor.id),
        "login": actor.login,
        "full_name": actor.full_name,
        "permissions": sorted(actor.permissions),
        "warehouse_ids": sorted(actor.warehouse_ids),
    }


@router.get("/warehouses")
def warehouses(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    rows = db.scalars(select(Warehouse).order_by(Warehouse.id))
    return [
        {
            "id": w.id,
            "code": w.code,
            "name": w.name,
            "quantity_mode": w.quantity_mode,
            "allow_manual_basis": w.allow_manual_basis,
            "accessible": w.id in actor.warehouse_ids,
        }
        for w in rows
    ]
