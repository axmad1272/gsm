from dataclasses import replace
from datetime import date

from conftest import BUYER_TIN, OTHER_TIN, Session, sale
from sqlalchemy import text

from app.modules.didox.gateway import DidoxDocument, Direction, DocType, SignStatus
from app.worker import run_batch


def test_contract_and_poa_lookup(client, world, didox):
    didox.add(replace(didox.documents["C1"], id="C2", number="99", status=SignStatus.REJECTED))
    r = client.get(
        "/api/didox/contracts",
        params={"partner_tin": BUYER_TIN, "on": "2026-10-15"},
        headers=world["operator"],
    ).json()
    assert [(c["id"], c["selectable"]) for c in r["contracts"]] == [("C1", True), ("C2", False)]
    assert r["contracts"][1]["sign_status"] == "rejected"  # показывается для справки
    assert r["confirmed_by_poa"] == []

    r = client.get(
        "/api/didox/powers-of-attorney",
        params={
            "partner_tin": BUYER_TIN,
            "on": "2026-10-15",
            "contract_id": "C1",
            "contract_number": "15/24",
            "contract_date": "2024-01-10",
        },
        headers=world["operator"],
    ).json()
    assert [(p["id"], p["sign_status"]) for p in r["items"]] == [("P1", "awaiting_us")]
    # До начала действия доверенности она не подходит.
    r = client.get(
        "/api/didox/powers-of-attorney",
        params={
            "partner_tin": BUYER_TIN,
            "on": "2026-09-30",
            "contract_number": "15/24",
            "contract_date": "2024-01-10",
        },
        headers=world["operator"],
    ).json()
    assert r["items"][0]["selectable"] is False


def test_server_revalidates_basis(client, world, didox):
    # Форма выбрала документ, а к моменту сохранения его отклонили.
    didox.documents["C1"] = replace(didox.documents["C1"], status=SignStatus.REJECTED)
    r = client.post("/api/shipments", json=sale(world), headers=world["operator"])
    assert r.status_code == 422 and r.json()["code"] == "basis_invalid"


def test_poa_from_other_counterparty_rejected(client, world, didox):
    didox.documents["P1"] = replace(didox.documents["P1"], partner_tin=OTHER_TIN)
    r = client.post("/api/shipments", json=sale(world), headers=world["operator"])
    assert r.json()["code"] == "basis_invalid"


def test_confirmed_by_poa(client, world, didox):
    del didox.documents["C1"]
    r = client.get(
        "/api/didox/contracts",
        params={"partner_tin": BUYER_TIN, "on": "2026-10-15"},
        headers=world["operator"],
    ).json()
    assert [d["id"] for d in r["confirmed_by_poa"]] == ["P1"]
    body = sale(world, basis={"mode": "didox", "poa_didox_id": "P1", "confirmed_by_poa": True})
    sh = client.post("/api/shipments", json=body, headers=world["operator"]).json()
    assert sh["basis"]["contract_kind"] == "confirmed_by_poa"
    assert sh["basis"]["contract_number"] == "15/24"
    assert sh["basis"]["contract_didox_id"] is None


def test_confirmed_by_poa_refused_when_contract_exists(client, world):
    body = sale(world, basis={"mode": "didox", "poa_didox_id": "P1", "confirmed_by_poa": True})
    r = client.post("/api/shipments", json=body, headers=world["operator"])
    assert r.json()["code"] == "basis_invalid"


def test_didox_unavailable_then_manual_mode(client, world, didox):
    didox.available = False
    r = client.post("/api/shipments", json=sale(world), headers=world["operator"])
    assert r.status_code == 503 and r.json()["code"] == "didox_unavailable"

    manual = {
        "mode": "manual",
        "contract_number": "15/24",
        "contract_date": "2024-01-10",
        "poa_number": "Д-7",
        "poa_date": "2026-10-01",
    }
    # На Алмалыке ручной режим не разрешён.
    alm = sale(world, warehouse_id=world["almalyk"], density=None, mass_t="16.8", basis=manual)
    r = client.post("/api/shipments", json=alm, headers=world["operator"])
    assert r.json()["code"] == "manual_basis_disabled"

    sh = client.post("/api/shipments", json=sale(world, basis=manual), headers=world["operator"])
    assert sh.status_code == 201, sh.text
    sh = sh.json()
    assert sh["basis"]["source"] == "manual"
    assert sh["status"]["didox"] == "manual_pending"

    db = Session()
    assert run_batch(db, didox) == 1  # Didox всё ещё недоступен: задача отложена
    db.close()

    didox.available = True
    db = Session()
    assert run_batch(db, didox) == 0  # ждёт next_attempt_at
    db.execute(text("UPDATE didox_reconciliation_tasks SET next_attempt_at = NULL"))
    db.commit()
    assert run_batch(db, didox) == 1
    db.close()
    card = client.get(f"/api/shipments/{sh['id']}", headers=world["operator"]).json()
    assert card["status"]["didox"] == "verified"
    assert card["basis"]["source"] == "manual"  # источник не переписывается
    assert card["audit"][-1]["action"] == "didox_reconciled"


def test_manual_mismatch(client, world, didox):
    manual = {
        "mode": "manual",
        "contract_number": "НЕТ-ТАКОГО",
        "contract_date": "2020-01-01",
        "poa_number": "Д-7",
        "poa_date": "2026-10-01",
    }
    sh = client.post("/api/shipments", json=sale(world, basis=manual), headers=world["operator"])
    db = Session()
    run_batch(db, didox)
    db.close()
    card = client.get(f"/api/shipments/{sh.json()['id']}", headers=world["operator"]).json()
    assert card["status"]["didox"] == "manual_mismatch"


def test_correction_without_basis_change_skips_didox(client, world, didox):
    sh = client.post("/api/shipments", json=sale(world), headers=world["operator"]).json()
    client.post(f"/api/shipments/{sh['id']}/print", headers=world["operator"])
    didox.available = False
    body = {"content": sale(world, note="уточнение"), "reason": "примечание"}
    r = client.put(f"/api/shipments/{sh['id']}", json=body, headers=world["operator"])
    assert r.status_code == 200, r.text
    assert r.json()["status"]["didox"] == "verified"


def test_counterparty_alias_search(client, world, didox):
    r = client.post(
        "/api/counterparties",
        json={"tin": BUYER_TIN, "name": "ООО «Покупатель»", "alias": "Покупатель Трейд"},
        headers=world["operator"],
    )
    assert r.status_code == 200
    r = client.get(
        "/api/counterparties/search", params={"q": "покупатель трейд"}, headers=world["operator"]
    ).json()
    assert r["items"][0] == {
        "tin": BUYER_TIN,
        "name": "ООО «Покупатель»",
        "counterparty_id": r["items"][0]["counterparty_id"],
        "source": "saved",
    }


def test_poa_linked_by_document_id(world, didox):
    from app.modules.didox.rules import poa_candidates

    didox.add(
        DidoxDocument(
            id="P2",
            doc_type=DocType.POWER_OF_ATTORNEY,
            direction=Direction.INCOMING,
            status=SignStatus.ACCEPTED,
            partner_tin=BUYER_TIN,
            number="Д-8",
            doc_date=date(2026, 10, 2),
            linked_document_ids=("C1",),
        )
    )
    c = poa_candidates(
        list(didox.documents.values()),
        BUYER_TIN,
        date(2026, 10, 15),
        contract_id="C1",
        contract_number="15/24",
        contract_date=date(2024, 1, 10),
    )
    assert [x.document.id for x in c] == ["P2", "P1"]
