from __future__ import annotations


def _make(client, **overrides):
    body = {"user_id": 37, "item_name": "Perfume", "quantity": 2}
    body.update(overrides)
    return client.post("/sales", json=body)


def test_create_sale(client):
    r = _make(client)
    assert r.status_code == 201
    data = r.json()
    assert data["id"] >= 1
    assert data["user_id"] == 37
    assert data["item_name"] == "Perfume"
    assert data["quantity"] == 2
    assert data["created_at"]


def test_get_sale(client):
    sale_id = _make(client).json()["id"]
    r = client.get(f"/sales/{sale_id}")
    assert r.status_code == 200
    assert r.json()["id"] == sale_id


def test_list_sales(client):
    _make(client, item_name="A")
    _make(client, item_name="B")
    r = client.get("/sales")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert [i["item_name"] for i in items] == ["A", "B"]


def test_update_sale(client):
    sale_id = _make(client).json()["id"]
    r = client.put(
        f"/sales/{sale_id}",
        json={"user_id": 40, "item_name": "Soap", "quantity": 5},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == 40
    assert data["item_name"] == "Soap"
    assert data["quantity"] == 5


def test_delete_sale(client):
    sale_id = _make(client).json()["id"]
    assert client.delete(f"/sales/{sale_id}").status_code == 204
    assert client.get(f"/sales/{sale_id}").status_code == 404


def test_missing_sale_returns_404(client):
    assert client.get("/sales/999999").status_code == 404
    assert (
        client.put(
            "/sales/999999", json={"user_id": 1, "item_name": "x", "quantity": 1}
        ).status_code
        == 404
    )
    assert client.delete("/sales/999999").status_code == 404


def test_quantity_must_be_positive(client):
    assert _make(client, quantity=0).status_code == 422
    assert _make(client, quantity=-3).status_code == 422


def test_user_id_is_required(client):
    r = client.post("/sales", json={"item_name": "Perfume", "quantity": 2})
    assert r.status_code == 422


def test_item_name_must_not_be_blank(client):
    assert _make(client, item_name="   ").status_code == 422
