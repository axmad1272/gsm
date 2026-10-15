import os
import uuid
from datetime import date

os.environ.setdefault("GSM_DATABASE_URL", "postgresql+psycopg://gsm:gsm@localhost:5432/gsm_test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.db import Base, get_db  # noqa: E402
from app.core.storage import LocalFileStorage, get_storage  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.didox.gateway import (  # noqa: E402
    DidoxCompany,
    DidoxDocument,
    Direction,
    DocType,
    FakeDidoxGateway,
    SignStatus,
    get_didox_gateway,
)
from app.modules.warehouses.models import (  # noqa: E402
    QuantityMode,
    User,
    UserWarehouse,
    Warehouse,
)

engine = create_engine(os.environ["GSM_DATABASE_URL"])
Session = sessionmaker(bind=engine, expire_on_commit=False)

BUYER_TIN = "301234567"
OTHER_TIN = "309999999"


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def clean():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    s = Session()
    yield s
    s.close()


@pytest.fixture
def didox():
    gw = FakeDidoxGateway()
    gw.companies.append(DidoxCompany(BUYER_TIN, "ООО «Покупатель»"))
    gw.add(
        DidoxDocument(
            id="C1",
            doc_type=DocType.CONTRACT,
            direction=Direction.OUTGOING,
            status=SignStatus.ACCEPTED,
            partner_tin=BUYER_TIN,
            number="15/24",
            doc_date=date(2024, 1, 10),
        )
    )
    gw.add(
        DidoxDocument(
            id="P1",
            doc_type=DocType.POWER_OF_ATTORNEY,
            direction=Direction.INCOMING,
            status=SignStatus.AWAITING_US,
            partner_tin=BUYER_TIN,
            number="Д-7",
            doc_date=date(2026, 10, 1),
            valid_from=date(2026, 10, 1),
            valid_to=date(2027, 12, 31),
            contract_number="15/24",
            contract_date=date(2024, 1, 10),
        )
    )
    return gw


@pytest.fixture
def client(didox, tmp_path):
    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_didox_gateway] = lambda: didox
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(str(tmp_path))
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def world(db):
    """Три склада и пользователи по ролям."""
    rohat = Warehouse(
        code="ROHAT",
        name="Рохат",
        quantity_mode=QuantityMode.VOLUME_DENSITY,
        allow_manual_basis=True,
    )
    almalyk = Warehouse(code="ALMALYK", name="Алмалык", quantity_mode=QuantityMode.VOLUME_MASS)
    navoi = Warehouse(code="NAVOI", name="Навои", quantity_mode=QuantityMode.VOLUME_DENSITY)
    db.add_all([rohat, almalyk, navoi])
    db.flush()
    everything = [rohat.id, almalyk.id, navoi.id]

    def user(login, roles, whs):
        u = User(id=uuid.uuid4(), login=login, full_name=login, roles=roles)
        db.add(u)
        db.flush()
        for w in whs:
            db.add(UserWarehouse(user_id=u.id, warehouse_id=w))
        return {"X-User-Id": str(u.id)}

    w = {
        "rohat": rohat.id,
        "almalyk": almalyk.id,
        "navoi": navoi.id,
        "operator": user("operator", ["operator"], [rohat.id, almalyk.id]),
        "navoi_operator": user("navoi_op", ["operator"], [navoi.id]),
        "accountant": user("accountant", ["accountant"], everything),
        "financier": user("financier", ["financier"], everything),
        "director": user("director", ["director"], everything),
        "admin": user("admin", ["admin"], everything),
    }
    db.commit()
    return w


def sale(world, **over):
    body = {
        "warehouse_id": world["rohat"],
        "operation": "sale",
        "shipment_date": "2026-10-15",
        "customer": {"name": "ООО «Покупатель»", "tin": BUYER_TIN},
        "consignee": {"name": "АЗС №3 Покупателя", "tin": BUYER_TIN},
        "basis": {"mode": "didox", "contract_didox_id": "C1", "poa_didox_id": "P1"},
        "vehicle_plate": "01 A 123 BC",
        "driver_pinfl": "31234567890123",
        "fuel_name": "Дизельное топливо",
        "volume_l": "20000",
        "density": "0.840",
    }
    body.update(over)
    return body
