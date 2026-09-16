"""
tests/test_stock_adjust_and_worker_crud.py — the batch manual stock
adjustment endpoint, and the newly-completed worker attendance/payment
Update/Delete operations.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _auth(client, make_token, membership_store, role="farmer"):
    membership_store.add(FARM_A, USER_A, role=role)
    token = make_token(USER_A)
    return {"Authorization": f"Bearer {token}"}


# ── Batch stock adjustment ────────────────────────────────────────────────

def test_owner_can_adjust_batch_stock_upward(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="farmer")
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 90, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value={**batch, "quantity_current": 95}) as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
            headers=headers,
            json={"delta": 5, "reason": "Physical recount found 5 more birds"},
        )
    assert r.status_code == 200
    assert r.json()["quantity_current"] == 95
    # delta=+5 (add) -> decrement_batch_quantity(batch, -5)
    mocked.assert_called_once_with(batch, -5)


def test_owner_can_adjust_batch_stock_downward(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="farmer")
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 90, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value={**batch, "quantity_current": 87}) as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
            headers=headers,
            json={"delta": -3, "reason": "3 escaped and were not recorded as mortality"},
        )
    assert r.status_code == 200
    mocked.assert_called_once_with(batch, 3)


def test_adjustment_requires_a_reason(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="farmer")
    r = client.post(
        f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
        headers=headers,
        json={"delta": 5, "reason": ""},
    )
    assert r.status_code == 422


def test_adjustment_rejects_zero_delta(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="farmer")
    r = client.post(
        f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
        headers=headers,
        json={"delta": 0, "reason": "no-op"},
    )
    assert r.status_code == 422


def test_adjustment_below_zero_rejected_cleanly(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="farmer")
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 2, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity", side_effect=ValueError("insufficient_stock")):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
            headers=headers,
            json={"delta": -10, "reason": "correcting a miscount"},
        )
    assert r.status_code == 409


def test_worker_cannot_adjust_batch_stock(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="worker")
    with patch("routes.animal_routes.crud.decrement_batch_quantity") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/adjust",
            headers=headers,
            json={"delta": 5, "reason": "trying anyway"},
        )
    assert r.status_code == 403
    mocked.assert_not_called()


# ── Worker attendance update/delete ──────────────────────────────────────

def test_update_attendance_record(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    worker = {"id": "w1", "farm_id": FARM_A}
    record = {"id": "a1", "worker_id": "w1", "status": "present"}
    with patch("routes.worker_routes.crud.get_worker", return_value=worker), \
         patch("routes.worker_routes.crud.get_attendance_record", return_value=record), \
         patch("routes.worker_routes.crud.update_attendance_record", return_value={**record, "status": "absent"}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/workers/w1/attendance/a1", headers=headers, json={"status": "absent"})
    assert r.status_code == 200
    assert r.json()["status"] == "absent"


def test_update_attendance_duplicate_date_rejected_cleanly(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    worker = {"id": "w1", "farm_id": FARM_A}
    record = {"id": "a1", "worker_id": "w1"}
    with patch("routes.worker_routes.crud.get_worker", return_value=worker), \
         patch("routes.worker_routes.crud.get_attendance_record", return_value=record), \
         patch("routes.worker_routes.crud.update_attendance_record", side_effect=ValueError("duplicate_attendance")):
        r = client.patch(f"/api/v1/farms/{FARM_A}/workers/w1/attendance/a1", headers=headers, json={"date": "2026-01-01"})
    assert r.status_code == 409


def test_delete_attendance_record(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    worker = {"id": "w1", "farm_id": FARM_A}
    with patch("routes.worker_routes.crud.get_worker", return_value=worker), \
         patch("routes.worker_routes.crud.get_attendance_record", return_value={"id": "a1"}), \
         patch("routes.worker_routes.crud.delete_attendance_record") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/workers/w1/attendance/a1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with("w1", "a1")


# ── Worker payment update/delete ─────────────────────────────────────────

def test_update_payment_record(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    worker = {"id": "w1", "farm_id": FARM_A}
    record = {"id": "p1", "worker_id": "w1", "amount": 100}
    with patch("routes.worker_routes.crud.get_worker", return_value=worker), \
         patch("routes.worker_routes.crud.get_payment", return_value=record), \
         patch("routes.worker_routes.crud.update_payment", return_value={**record, "amount": 150}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/workers/w1/payments/p1", headers=headers, json={"amount": 150})
    assert r.status_code == 200
    assert r.json()["amount"] == 150


def test_worker_role_cannot_update_payment(client, make_token, membership_store):
    """Payments stay manager+ only, matching create — a worker recording
    their own attendance shouldn't be able to edit payroll records."""
    headers = _auth(client, make_token, membership_store, role="worker")
    with patch("routes.worker_routes.crud.update_payment") as mocked:
        r = client.patch(f"/api/v1/farms/{FARM_A}/workers/w1/payments/p1", headers=headers, json={"amount": 999})
    assert r.status_code == 403
    mocked.assert_not_called()


def test_delete_payment_record(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    worker = {"id": "w1", "farm_id": FARM_A}
    with patch("routes.worker_routes.crud.get_worker", return_value=worker), \
         patch("routes.worker_routes.crud.get_payment", return_value={"id": "p1"}), \
         patch("routes.worker_routes.crud.delete_payment") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/workers/w1/payments/p1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with("w1", "p1")
