from __future__ import annotations

LEGACY = {
    "id": 500,
    "user_id": 37,
    "item_name": "Perfume",
    "quantity": 2,
    "created_at": "2023-01-15T10:30:00Z",
}


def test_import_preserves_id(client):
    r = client.post("/internal/sales/import", json=LEGACY)
    assert r.status_code == 201
    body = r.json()
    assert body["outcome"] == "created"
    assert body["sale"]["id"] == 500

    got = client.get("/sales/500")
    assert got.status_code == 200
    assert got.json()["id"] == 500


def test_import_is_idempotent(client):
    first = client.post("/internal/sales/import", json=LEGACY)
    assert first.status_code == 201

    second = client.post("/internal/sales/import", json=LEGACY)
    assert second.status_code == 200
    assert second.json()["outcome"] == "unchanged"

    listed = client.get("/sales").json()
    assert len(listed) == 1
    assert listed[0]["id"] == 500


def test_import_same_id_different_data_conflicts(client):
    assert client.post("/internal/sales/import", json=LEGACY).status_code == 201

    changed = {**LEGACY, "quantity": 99}
    r = client.post("/internal/sales/import", json=changed)
    assert r.status_code == 409
    body = r.json()
    assert body["sale_id"] == 500
    assert body["current"]["quantity"] == 2
    assert body["incoming"]["quantity"] == 99

    # original row untouched
    assert client.get("/sales/500").json()["quantity"] == 2


def test_native_create_after_high_id_import_does_not_collide(client):
    client.post(
        "/internal/sales/import",
        json={**LEGACY, "id": 1000},
    )

    r = client.post(
        "/sales", json={"user_id": 1, "item_name": "Native", "quantity": 1}
    )
    assert r.status_code == 201
    new_id = r.json()["id"]
    assert new_id > 1000  # sequence was realigned past the imported id

    # and a second native create keeps climbing
    r2 = client.post(
        "/sales", json={"user_id": 1, "item_name": "Native2", "quantity": 1}
    )
    assert r2.json()["id"] == new_id + 1


def test_import_missing_created_at_is_rejected(client):
    body = {k: v for k, v in LEGACY.items() if k != "created_at"}
    assert client.post("/internal/sales/import", json=body).status_code == 422
