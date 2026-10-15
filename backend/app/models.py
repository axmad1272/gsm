"""Импорт всех моделей — для Alembic и create_all в тестах."""

from app.core.idempotency import IdempotencyKey  # noqa: F401
from app.modules.audit.models import AuditEvent  # noqa: F401
from app.modules.didox.models import (  # noqa: F401
    Counterparty,
    CounterpartyAlias,
    DidoxReconciliationTask,
)
from app.modules.periods.models import ClosedPeriod  # noqa: F401
from app.modules.shipments.models import (  # noqa: F401
    FuelMovement,
    NumberSequence,
    ReleasedNumber,
    Shipment,
    ShipmentPrint,
    ShipmentRemark,
    ShipmentReview,
    ShipmentRevision,
)
from app.modules.warehouses.models import User, UserWarehouse, Warehouse  # noqa: F401
