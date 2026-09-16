"""
tests/test_crud_completeness.py — Update/Delete for the entities that
previously only had Create + List: sales, expenses, income, feed
purchases/consumption, animal batches, mortality records, medication
records. The stock-reconciling paths (sales, mortality) get the most
scrutiny since a wrong reconciliation silently corrupts a batch's count.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _auth(client, make_token, membership_store, role="farmer"):
    membership_store.add(FARM_A, USER_A, role=role)
    token = make_token(USER_A)
    return {"Authorization": f"Bearer {token}"}


# ── Sales: update/delete reconcile batch stock ───────────────────────────

def test_update_sale_quantity_gives_back_the_difference(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": "b1", "quantity": 5, "unit_price": 10.0, "discount": 0}
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 10, "updated_at": "t0"}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.get_batch", return_value=batch), \
         patch("routes.finance_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.finance_routes.crud.update_sale", return_value={**sale, "quantity": 3}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers, json={"quantity": 3})
    assert r.status_code == 200
    # old=5, new=3 -> delta=2 -> give back 2 -> decrement_batch_quantity(batch, -2)
    mocked_adjust.assert_called_once_with(batch, -2)


def test_update_sale_quantity_increase_takes_more_stock(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": "b1", "quantity": 5, "unit_price": 10.0, "discount": 0}
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 10, "updated_at": "t0"}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.get_batch", return_value=batch), \
         patch("routes.finance_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.finance_routes.crud.update_sale", return_value={**sale, "quantity": 8}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers, json={"quantity": 8})
    assert r.status_code == 200
    # old=5, new=8 -> delta=-3 -> take 3 more -> decrement_batch_quantity(batch, 3)
    mocked_adjust.assert_called_once_with(batch, 3)


def test_update_sale_quantity_insufficient_stock_rejected(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": "b1", "quantity": 5, "unit_price": 10.0, "discount": 0}
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 2, "updated_at": "t0"}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.get_batch", return_value=batch), \
         patch("routes.finance_routes.crud.decrement_batch_quantity", side_effect=ValueError("insufficient_stock")), \
         patch("routes.finance_routes.crud.update_sale") as mocked_update:
        r = client.patch(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers, json={"quantity": 20})
    assert r.status_code == 409
    mocked_update.assert_not_called()


def test_update_sale_cannot_change_batch_id(client, make_token, membership_store):
    """batch_id isn't even a field on SaleUpdate — a client trying to send
    it just has it silently ignored by Pydantic, not applied."""
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": "b1", "quantity": 5, "unit_price": 10.0, "discount": 0}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.update_sale", return_value=sale) as mocked_update:
        r = client.patch(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers,
                          json={"notes": "fixed typo", "batch_id": "some-other-batch"})
    assert r.status_code == 200
    called_fields = mocked_update.call_args[0][2]
    assert "batch_id" not in called_fields


def test_delete_sale_restores_full_stock(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": "b1", "quantity": 4, "unit_price": 10.0, "discount": 0}
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 6, "updated_at": "t0"}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.get_batch", return_value=batch), \
         patch("routes.finance_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.finance_routes.crud.delete_sale") as mocked_delete:
        r = client.delete(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers)
    assert r.status_code == 204
    mocked_adjust.assert_called_once_with(batch, -4)  # give back all 4
    mocked_delete.assert_called_once_with(FARM_A, "s1")


def test_delete_sale_without_batch_skips_stock_adjustment(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    sale = {"id": "s1", "farm_id": FARM_A, "batch_id": None, "quantity": 4, "unit_price": 10.0, "discount": 0}
    with patch("routes.finance_routes.crud.get_sale", return_value=sale), \
         patch("routes.finance_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.finance_routes.crud.delete_sale") as mocked_delete:
        r = client.delete(f"/api/v1/farms/{FARM_A}/sales/s1", headers=headers)
    assert r.status_code == 204
    mocked_adjust.assert_not_called()
    mocked_delete.assert_called_once()


def test_update_nonexistent_sale_404(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_sale", return_value=None):
        r = client.patch(f"/api/v1/farms/{FARM_A}/sales/nope", headers=headers, json={"notes": "x"})
    assert r.status_code == 404


# ── Expenses / Income: simple, no stock side effects ────────────────────

def test_update_expense(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_expense", return_value={"id": "e1", "farm_id": FARM_A}), \
         patch("routes.finance_routes.crud.update_expense", return_value={"id": "e1", "amount": 50.0}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/expenses/e1", headers=headers, json={"amount": 50.0})
    assert r.status_code == 200
    assert r.json()["amount"] == 50.0


def test_delete_expense(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_expense", return_value={"id": "e1", "farm_id": FARM_A}), \
         patch("routes.finance_routes.crud.delete_expense") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/expenses/e1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "e1")


def test_update_income(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_income", return_value={"id": "i1", "farm_id": FARM_A}), \
         patch("routes.finance_routes.crud.update_income", return_value={"id": "i1", "amount": 75.0}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/income/i1", headers=headers, json={"amount": 75.0})
    assert r.status_code == 200


def test_delete_income(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_income", return_value={"id": "i1", "farm_id": FARM_A}), \
         patch("routes.finance_routes.crud.delete_income") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/income/i1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "i1")


def test_update_with_empty_body_rejected(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.finance_routes.crud.get_expense", return_value={"id": "e1", "farm_id": FARM_A}), \
         patch("routes.finance_routes.crud.update_expense") as mocked:
        r = client.patch(f"/api/v1/farms/{FARM_A}/expenses/e1", headers=headers, json={})
    assert r.status_code == 400
    mocked.assert_not_called()


# ── Feed purchases / consumption ─────────────────────────────────────────

def test_update_feed_purchase_recomputes_total_cost(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.feed_routes.crud.get_feed_purchase", return_value={"id": "p1", "farm_id": FARM_A}), \
         patch("routes.feed_routes.crud.update_feed_purchase", return_value={"id": "p1", "total_cost": 100.0}) as mocked:
        r = client.patch(f"/api/v1/farms/{FARM_A}/feed/purchases/p1", headers=headers, json={"quantity_kg": 20, "unit_cost": 5})
    assert r.status_code == 200
    mocked.assert_called_once()


def test_delete_feed_purchase(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.feed_routes.crud.get_feed_purchase", return_value={"id": "p1", "farm_id": FARM_A}), \
         patch("routes.feed_routes.crud.delete_feed_purchase") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/feed/purchases/p1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "p1")


def test_worker_can_update_feed_consumption_but_not_purchases(client, make_token, membership_store):
    """Purchases are a manager-level decision (spending money); consumption
    is a day-to-day worker task — matches the create-permission split
    that already existed."""
    headers = _auth(client, make_token, membership_store, role="worker")
    with patch("routes.feed_routes.crud.get_feed_purchase", return_value={"id": "p1", "farm_id": FARM_A}), \
         patch("routes.feed_routes.crud.update_feed_purchase") as mocked:
        r = client.patch(f"/api/v1/farms/{FARM_A}/feed/purchases/p1", headers=headers, json={"quantity_kg": 5})
    assert r.status_code == 403
    mocked.assert_not_called()

    with patch("routes.feed_routes.crud.get_feed_consumption_record", return_value={"id": "c1", "farm_id": FARM_A}), \
         patch("routes.feed_routes.crud.update_feed_consumption", return_value={"id": "c1"}) as mocked2:
        r2 = client.patch(f"/api/v1/farms/{FARM_A}/feed/consumption/c1", headers=headers, json={"quantity_kg": 5})
    assert r2.status_code == 200
    mocked2.assert_called_once()


# ── Animal batches ────────────────────────────────────────────────────────

def test_update_batch_info(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "batch_name": "Old name"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.update_batch", return_value={**batch, "batch_name": "New name"}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers, json={"batch_name": "New name"})
    assert r.status_code == 200
    assert r.json()["batch_name"] == "New name"


def test_can_update_batch_species_via_patch(client, make_token, membership_store):
    """AUDIT.md — species is now editable via the single batch-edit form,
    matching the mobile app's UI exactly (a farmer correcting a
    mis-entered species at creation time is a real, valid need — the
    previous exclusion was a web-only design choice, not a data-
    integrity requirement; species doesn't cascade into any other
    table's constraints)."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 10}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.update_batch", return_value=batch) as mocked:
        client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers,
                     json={"breed": "Leghorn", "species": "cattle"})
    called_fields = mocked.call_args[0][2]
    assert called_fields["species"] == "cattle"
    assert called_fields["breed"] == "Leghorn"


def test_update_batch_quantity_current_uses_atomic_adjustment(client, make_token, membership_store):
    """AUDIT.md — matching mobile's single edit form, quantity_current can
    now be changed here too, but must still go through the same atomic,
    race-safe path as every other stock change — never a raw overwrite."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 19, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value={**batch, "quantity_current": 23}) as mocked_adjust:
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers, json={"quantity_current": 23})
    assert r.status_code == 200
    # 19 -> 23 is a delta of +4, i.e. decrement_batch_quantity(batch, -4)
    mocked_adjust.assert_called_once_with(batch, -4)


def test_update_batch_quantity_unchanged_skips_adjustment(client, make_token, membership_store):
    """Submitting the form with the count left as-is shouldn't touch the
    stock-adjustment path at all — matches mobile showing the current
    value pre-filled and only 'changing' it if the user actually edits it."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 19, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.animal_routes.crud.update_batch", return_value=batch):
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers,
                          json={"quantity_current": 19, "breed": "Leghorn"})
    assert r.status_code == 200
    mocked_adjust.assert_not_called()


def test_update_batch_explicit_status_overrides_auto_close(client, make_token, membership_store):
    """If a quantity edit would auto-close the batch (hits zero) but the
    same save also explicitly sets status, the explicit choice wins —
    see update_batch's docstring."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 5, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value={**batch, "quantity_current": 0, "status": "closed"}), \
         patch("routes.animal_routes.crud.update_batch", return_value={**batch, "quantity_current": 0, "status": "active"}) as mocked_update:
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers,
                          json={"quantity_current": 0, "status": "active"})
    assert r.status_code == 200
    called_fields = mocked_update.call_args[0][2]
    assert called_fields["status"] == "active"


def test_delete_batch_soft_deletes(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": FARM_A}), \
         patch("routes.animal_routes.crud.soft_delete_batch") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "b1")


def test_worker_cannot_delete_batch(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store, role="worker")
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": FARM_A}), \
         patch("routes.animal_routes.crud.soft_delete_batch") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/animals/batches/b1", headers=headers)
    assert r.status_code == 403
    mocked.assert_not_called()


# ── Mortality: update/delete reconcile batch stock ───────────────────────

def test_update_mortality_notes_only_no_stock_change(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "m1", "batch_id": "b1", "quantity": 3}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_mortality_record", return_value=record), \
         patch("routes.animal_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.animal_routes.crud.update_mortality_record", return_value={**record, "cause": "disease"}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality/m1", headers=headers, json={"cause": "disease"})
    assert r.status_code == 200
    mocked_adjust.assert_not_called()  # quantity isn't editable, so no stock touch


def test_delete_mortality_restores_stock(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 7, "updated_at": "t0"}
    record = {"id": "m1", "batch_id": "b1", "quantity": 3}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_mortality_record", return_value=record), \
         patch("routes.animal_routes.crud.decrement_batch_quantity") as mocked_adjust, \
         patch("routes.animal_routes.crud.delete_mortality_record") as mocked_delete:
        r = client.delete(f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality/m1", headers=headers)
    assert r.status_code == 204
    mocked_adjust.assert_called_once_with(batch, -3)  # 3 animals restored
    mocked_delete.assert_called_once_with("b1", "m1")


def test_update_mortality_cannot_change_quantity(client, make_token, membership_store):
    """quantity isn't a field on MortalityUpdate — sending it is ignored,
    not applied and not reconciled against stock."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "m1", "batch_id": "b1", "quantity": 3}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_mortality_record", return_value=record), \
         patch("routes.animal_routes.crud.update_mortality_record", return_value=record) as mocked:
        client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality/m1", headers=headers,
                     json={"quantity": 99, "cause": "cold"})
    called_fields = mocked.call_args[0][2]
    assert "quantity" not in called_fields


# ── Medication ────────────────────────────────────────────────────────────

def test_update_medication_cost(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "med1", "batch_id": "b1", "cost": None}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_medication_record", return_value=record), \
         patch("routes.animal_routes.crud.update_medication_record", return_value={**record, "cost": 12.5}):
        r = client.patch(f"/api/v1/farms/{FARM_A}/animals/batches/b1/medication/med1", headers=headers, json={"cost": 12.5})
    assert r.status_code == 200
    assert r.json()["cost"] == 12.5


def test_delete_medication(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "med1", "batch_id": "b1"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_medication_record", return_value=record), \
         patch("routes.animal_routes.crud.delete_medication_record") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/animals/batches/b1/medication/med1", headers=headers)
    assert r.status_code == 204
    mocked.assert_called_once_with("b1", "med1")
