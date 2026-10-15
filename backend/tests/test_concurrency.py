import uuid
from concurrent.futures import ThreadPoolExecutor

from conftest import Session, sale

from app.core.auth import load_actor
from app.core.errors import DomainError
from app.core.storage import LocalFileStorage
from app.modules.shipments.schemas import ShipmentContentIn
from app.modules.shipments.service import ShipmentService


def _svc(world, didox, tmp_path):
    db = Session()
    actor = load_actor(db, uuid.UUID(world["operator"]["X-User-Id"]))
    return db, ShipmentService(db, actor, didox, LocalFileStorage(str(tmp_path)))


def test_parallel_creates_get_unique_numbers(world, didox, tmp_path):
    def create(_):
        db, svc = _svc(world, didox, tmp_path)
        try:
            sh = svc.create(ShipmentContentIn.model_validate(sale(world)))
            db.commit()
            return sh.number
        finally:
            db.close()

    with ThreadPoolExecutor(8) as ex:
        numbers = list(ex.map(create, range(16)))
    assert sorted(numbers) == list(range(1, 17))


def test_parallel_first_print_posts_once(client, world, didox, tmp_path):
    sid = client.post("/api/shipments", json=sale(world), headers=world["operator"]).json()["id"]

    def print_(_):
        db, svc = _svc(world, didox, tmp_path)
        try:
            pr = svc.print(uuid.UUID(sid))
            db.commit()
            return pr.is_first
        except DomainError:
            return None
        finally:
            db.close()

    with ThreadPoolExecutor(6) as ex:
        firsts = list(ex.map(print_, range(6)))
    assert firsts.count(True) == 1
    card = client.get(f"/api/shipments/{sid}", headers=world["operator"]).json()
    assert len(card["movements"]) == 1
    assert [p["status"] for p in card["prints"]].count("current") == 1
