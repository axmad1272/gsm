from conftest import BUYER_TIN, sale


def _create(client, world, who="operator", **over):
    r = client.post("/api/shipments", json=sale(world, **over), headers=world[who])
    assert r.status_code == 201, r.text
    return r.json()


def _balance(client, world, wh="rohat"):
    return client.get(f"/api/warehouses/{world[wh]}/balance", headers=world["operator"]).json()


def test_create_reserves_number_without_movement(client, world):
    sh = _create(client, world)
    assert sh["number"] == 1
    assert sh["status"]["document"] == "not_printed"
    assert sh["status"]["didox"] == "verified"
    assert sh["basis"]["contract_kind"] == "didox_contract"
    # Статус подписи сохраняется как есть, а не «Подписано».
    assert sh["basis"]["poa_didox_status"] == "awaiting_us"
    assert sh["fuel"]["mass_g"] == 16_800_000
    assert sh["movements"] == []
    assert _balance(client, world) == {"volume_ml": 0, "mass_g": 0}


def test_preview_does_not_post(client, world):
    sh = _create(client, world)
    r = client.get(f"/api/shipments/{sh['id']}/preview.pdf", headers=world["operator"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    card = client.get(f"/api/shipments/{sh['id']}", headers=world["operator"]).json()
    assert card["movements"] == [] and card["prints"] == []


def test_first_print_posts_once_and_reprint_does_not(client, world):
    sh = _create(client, world)
    h = {**world["operator"], "Idempotency-Key": "print-1"}
    r1 = client.post(f"/api/shipments/{sh['id']}/print", headers=h)
    r2 = client.post(f"/api/shipments/{sh['id']}/print", headers=h)  # двойной клик
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["print_id"] == r2.json()["print_id"]
    assert r1.json()["is_first"] is True
    card = r1.json()["shipment"]
    assert card["status"]["document"] == "active"
    assert card["number_fixed"] is True
    assert _balance(client, world) == {"volume_ml": -20_000_000, "mass_g": -16_800_000}

    # Повторная печать без ключа — новый выпуск, но без нового расхода.
    r3 = client.post(f"/api/shipments/{sh['id']}/print", headers=world["operator"])
    assert r3.json()["is_first"] is False
    assert _balance(client, world)["mass_g"] == -16_800_000
    statuses = [p["status"] for p in r3.json()["shipment"]["prints"]]
    assert statuses == ["superseded", "current"]


def test_delete_releases_unprinted_number(client, world):
    a = _create(client, world)
    b = _create(client, world)
    assert (a["number"], b["number"]) == (1, 2)
    r = client.delete(f"/api/shipments/{a['id']}", headers=world["operator"])
    assert r.status_code == 200 and r.json()["status"]["document"] == "deleted"
    c = _create(client, world)
    assert c["number"] == 1  # освобождённый номер выдан снова
    d = _create(client, world)
    assert d["number"] == 3


def test_printed_number_never_released(client, world):
    a = _create(client, world)
    client.post(f"/api/shipments/{a['id']}/print", headers=world["operator"])
    r = client.delete(f"/api/shipments/{a['id']}", headers=world["operator"])
    assert r.status_code == 409 and r.json()["code"] == "delete_after_print"
    r = client.post(
        f"/api/shipments/{a['id']}/annul", json={"reason": "ошибка"}, headers=world["accountant"]
    )
    assert r.status_code == 200
    assert _create(client, world)["number"] == 2


def test_numbering_per_warehouse_and_year(client, world):
    assert _create(client, world)["number"] == 1
    assert _create(client, world, shipment_date="2027-01-02")["number"] == 1
    alm = _create(client, world, warehouse_id=world["almalyk"], density=None, mass_t="16.8")
    assert alm["number"] == 1
    assert alm["fuel"]["density"] == "0.8400"


def test_correction_after_print(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    body = {"content": sale(world, volume_l="10000"), "reason": None}

    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.json()["code"] == "reason_required"

    body["reason"] = "Неверный объём"
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["revision_no"] == 2
    assert card["status"]["document"] == "reprint_required"
    assert card["corrections"] == {"used": 1, "granted": 0, "left": 2}
    assert card["revisions"][-1]["changed_fields"] == ["volume_ml", "mass_g"]
    kinds = [(m["kind"], m["revision_no"], m["mass_g"]) for m in card["movements"]]
    assert kinds == [
        ("normal", 1, -16_800_000),
        ("storno", 2, 16_800_000),
        ("normal", 2, -8_400_000),
    ]
    assert _balance(client, world)["mass_g"] == -8_400_000
    # Старая бумага относится к прежней редакции.
    assert card["prints"][0]["status"] == "outdated_revision"

    r = client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    assert r.json()["shipment"]["status"]["document"] == "active"
    assert _balance(client, world)["mass_g"] == -8_400_000


def test_non_movement_correction_keeps_movements(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    body = {"content": sale(world, vehicle_plate="01B777AA"), "reason": "Другая машина"}
    card = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"]).json()
    assert len(card["movements"]) == 1
    assert card["revisions"][-1]["changed_fields"] == ["vehicle_plate"]


def test_correction_limit_and_grant(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    for i, vol in enumerate(["19000", "18000", "17000"]):
        body = {"content": sale(world, volume_l=vol), "reason": f"правка {i}"}
        assert (
            client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"]).status_code
            == 200
        )

    # Неуспешная попытка (нет изменений) не расходует лимит — проверяем до исчерпания.
    body = {"content": sale(world, volume_l="16000"), "reason": "ещё"}
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 409 and r.json()["code"] == "correction_limit"

    r = client.post(
        f"/api/shipments/{sid}/remarks",
        json={"text": "Исправьте объём по замеру", "grant_correction": True},
        headers=world["accountant"],
    )
    assert r.status_code == 200 and r.json()["corrections"]["left"] == 1
    assert r.json()["remarks"][0]["granted_correction"] is True

    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 200
    assert r.json()["corrections"] == {"used": 4, "granted": 1, "left": 0}


def test_failed_correction_does_not_consume_attempt(client, world, didox):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    body = {"content": sale(world, volume_l="1"), "reason": "x", "admin_mode": False}
    body["content"]["density"] = "5"  # ошибка валидации
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 422
    card = client.get(f"/api/shipments/{sid}", headers=world["operator"]).json()
    assert card["corrections"]["used"] == 0 and card["revision_no"] == 1


def test_operator_cannot_change_date_after_print_admin_can(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    body = {"content": sale(world, shipment_date="2026-10-14"), "reason": "дата"}
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 403 and r.json()["code"] == "admin_correction_required"

    body["admin_mode"] = True
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.status_code == 403  # у оператора нет права административного исправления
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["admin"])
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["corrections"]["used"] == 0
    assert card["revisions"][-1]["kind"] == "admin_correction"
    assert [m["date"] for m in card["movements"]] == ["2026-10-15", "2026-10-15", "2026-10-14"]


def test_admin_year_change_takes_new_number(client, world):
    _create(client, world, shipment_date="2027-01-05")  # № 1 за 2027
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    body = {"content": sale(world, shipment_date="2027-01-06"), "reason": "год", "admin_mode": True}
    card = client.put(f"/api/shipments/{sid}", json=body, headers=world["admin"]).json()
    assert (card["number"], card["number_year"]) == (2, 2027)
    # Напечатанный № 1/2026 не освобождается.
    assert _create(client, world)["number"] == 2


def test_annul(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    pr = client.post(f"/api/shipments/{sid}/print", headers=world["operator"]).json()
    for who in ("operator", "director"):
        r = client.post(f"/api/shipments/{sid}/annul", json={"reason": "отказ"}, headers=world[who])
        assert r.status_code == 403
    r = client.post(
        f"/api/shipments/{sid}/annul",
        json={"reason": "Отказ покупателя"},
        headers=world["accountant"],
    )
    assert r.status_code == 200
    card = r.json()
    assert card["status"]["document"] == "annulled"
    assert card["number"] == 1
    assert _balance(client, world)["mass_g"] == 0
    v = client.get(f"/api/verify/prints/{pr['print_id']}").json()
    assert v["status"] == "annulled"


def test_transfer(client, world):
    body = {
        "warehouse_id": world["rohat"],
        "operation": "transfer",
        "destination_warehouse_id": world["rohat"],
        "shipment_date": "2026-10-15",
        "vehicle_plate": "01A123BC",
        "fuel_name": "АИ-92",
        "volume_l": "1000",
        "density": "0.7500",
    }
    r = client.post("/api/shipments", json=body, headers=world["operator"])
    assert r.json()["code"] == "same_warehouse"
    body["destination_warehouse_id"] = world["almalyk"]
    sh = client.post("/api/shipments", json=body, headers=world["operator"]).json()
    assert sh["customer"]["name"] == sh["consignee"]["name"] == "Алмалык"
    assert sh["basis"]["source"] == "transfer"
    assert sh["basis"]["contract_number"] == "Перемещение"
    assert sh["status"]["didox"] == "not_applicable"
    card = client.post(f"/api/shipments/{sh['id']}/print", headers=world["operator"]).json()
    moves = {
        (m["warehouse_id"], m["direction"], m["mass_g"]) for m in card["shipment"]["movements"]
    }
    assert moves == {(world["rohat"], "out", -750_000), (world["almalyk"], "in", 750_000)}


def test_drafts(client, world):
    partial = {"warehouse_id": world["rohat"], "customer": {"name": "Покупатель", "tin": BUYER_TIN}}
    r = client.post("/api/shipments/drafts", json=partial, headers=world["operator"])
    assert r.status_code == 201
    d = r.json()
    assert d["status"]["document"] == "draft" and d["number"] is None
    r = client.post(f"/api/shipments/drafts/{d['id']}/confirm", json={}, headers=world["operator"])
    assert r.status_code == 422 and r.json()["code"] == "incomplete"
    client.put(f"/api/shipments/drafts/{d['id']}", json=sale(world), headers=world["operator"])
    r = client.post(f"/api/shipments/drafts/{d['id']}/confirm", json={}, headers=world["operator"])
    assert r.status_code == 200, r.text
    assert r.json()["number"] == 1 and r.json()["id"] == d["id"]
    tabs = client.get("/api/shipments", params={"tab": "drafts"}, headers=world["operator"]).json()
    assert tabs["items"] == []
    assert tabs["applied_filters"]["period_meaning"] == "по дате отгрузки"


def test_review_and_close(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    r = client.post(f"/api/shipments/{sid}/close", headers=world["financier"])
    assert r.json()["code"] == "review_missing"

    # Администратор не получает бухгалтерских действий автоматически.
    r = client.post(
        f"/api/shipments/{sid}/reviews",
        json={"kind": "documents", "approved": True},
        headers=world["admin"],
    )
    assert r.status_code == 403

    client.post(
        f"/api/shipments/{sid}/reviews",
        json={"kind": "documents", "approved": True},
        headers=world["accountant"],
    )
    card = client.post(
        f"/api/shipments/{sid}/reviews",
        json={"kind": "actual", "approved": True},
        headers=world["financier"],
    ).json()
    assert card["status"]["review"] == {"documents": "approved", "actual": "approved"}

    # Исправление после проверки: проверки прежней редакции устаревают.
    body = {"content": sale(world, volume_l="15000"), "reason": "замер"}
    card = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"]).json()
    assert card["status"]["review"] == {"documents": "stale", "actual": "stale"}
    queue = client.get("/api/shipments", params={"tab": "review"}, headers=world["accountant"])
    assert [i["id"] for i in queue.json()["items"]] == [sid]

    client.post(f"/api/shipments/{sid}/print", headers=world["operator"])
    for who, kind in (("accountant", "documents"), ("financier", "actual")):
        client.post(
            f"/api/shipments/{sid}/reviews",
            json={"kind": kind, "approved": True},
            headers=world[who],
        )
    r = client.post(f"/api/shipments/{sid}/close", headers=world["financier"])
    assert r.status_code == 200 and r.json()["status"]["closed"] is True
    hist = client.get("/api/shipments", params={"tab": "history"}, headers=world["accountant"])
    assert [i["id"] for i in hist.json()["items"]] == [sid]

    body = {"content": sale(world, volume_l="14000"), "reason": "ещё"}
    r = client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert r.json()["code"] == "closed"


def test_closed_period_blocks_changes(client, world):
    sh = _create(client, world)
    r = client.post("/api/periods/2026/10/close", headers=world["operator"])
    assert r.status_code == 403
    assert client.post("/api/periods/2026/10/close", headers=world["financier"]).status_code == 200
    r = client.post(f"/api/shipments/{sh['id']}/print", headers=world["operator"])
    assert r.status_code == 409 and r.json()["code"] == "period_closed"
    r = client.post("/api/shipments", json=sale(world), headers=world["operator"])
    assert r.json()["code"] == "period_closed"


def test_warehouse_access_and_personal_data(client, world):
    sh = _create(client, world)
    r = client.get(f"/api/shipments/{sh['id']}", headers=world["navoi_operator"])
    assert r.status_code == 404
    r = client.post("/api/shipments", json=sale(world), headers=world["navoi_operator"])
    assert r.status_code == 403 and r.json()["code"] == "warehouse_forbidden"
    card = client.get(f"/api/shipments/{sh['id']}", headers=world["director"]).json()
    assert card["driver_pinfl"] is None
    assert card["revisions"][0]["content"]["driver_pinfl"] is None
    assert "accounting_date" in card
    op = client.get(f"/api/shipments/{sh['id']}", headers=world["operator"]).json()
    assert op["driver_pinfl"] == "31234567890123"
    assert "accounting_date" not in op  # 1С и бухгалтерия — отдельная область доступа


def test_qr_verification_statuses(client, world):
    sh = _create(client, world)
    sid = sh["id"]
    p1 = client.post(f"/api/shipments/{sid}/print", headers=world["operator"]).json()["print_id"]
    assert client.get(f"/api/verify/prints/{p1}").json()["status"] == "current"
    p2 = client.post(f"/api/shipments/{sid}/print", headers=world["operator"]).json()["print_id"]
    assert client.get(f"/api/verify/prints/{p1}").json()["status"] == "superseded"
    body = {"content": sale(world, volume_l="100"), "reason": "x"}
    client.put(f"/api/shipments/{sid}", json=body, headers=world["operator"])
    assert client.get(f"/api/verify/prints/{p2}").json()["status"] == "outdated_revision"
    pdf = client.get(f"/api/prints/{p2}/pdf", headers=world["operator"])
    assert pdf.content.startswith(b"%PDF")
