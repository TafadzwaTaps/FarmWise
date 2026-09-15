"""
tests/test_batch_profit.py — Phase 4: tests for crud.finance.batch_profit_summary,
the per-batch weighted-average cost allocation model (AUDIT.md FWA-006).

Mocks the Supabase query builder table-by-table (one fake per table
queried) rather than a real DB — the property under test is "does the
model compute the right numbers from a given set of rows", which is a
pure function of its inputs and doesn't need Postgres to verify.
"""

from unittest.mock import MagicMock

import crud.finance as finance_crud


def _fake_supabase(tables: dict):
    """tables: {"table_name": [{...row...}, ...]} — every .select().eq()...().execute()
    chain against that table returns exactly those rows, regardless of
    which/how many .eq() filters are chained (the filtering is assumed
    correct — already covered by the farm-authorization test suite; this
    file tests the arithmetic on top of whatever rows come back)."""
    def _table(name):
        m = MagicMock()
        chain = m.select.return_value
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=tables.get(name, []))
        return m
    return MagicMock(table=MagicMock(side_effect=_table))


def _patch_get_batch(monkeypatch, batch: dict):
    monkeypatch.setattr(finance_crud, "get_batch", lambda farm_id, batch_id: batch)


# ── The brief's own worked example ───────────────────────────────────────

def test_worked_example_500_bought_100_sold_400_remain(monkeypatch):
    """From the original brief, verbatim: 'A farmer buys 500 chickens. 100
    are sold. The remaining 400 are still alive. The report must not
    treat the full purchase cost as the cost of the 100 sold birds
    without accounting for the remaining inventory.'

    500 bought @ total $1000 purchase cost, no feed/medication/other
    costs for simplicity. 100 sold for $300 total revenue. 400 still
    alive, 0 mortality (500 = 100 + 400 + 0).

    cost_per_unit = 1000 / 500 = $2/bird
    cost_of_goods_sold (for the 100 sold) = 100 * 2 = $200 — NOT $1000.
    remaining_inventory_value (for the 400 still alive) = 400 * 2 = $800.
    gross_profit = $300 revenue - $200 COGS = $100 (NOT a $700 loss,
    which is what "revenue minus the FULL purchase cost" would wrongly show).
    """
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 500,
              "quantity_current": 400, "purchase_price_total": 1000.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [{"quantity": 100, "total_amount": 300.0}],
        "mortality_records": [],
        "medication_records": [],
        "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 0.0, "total_purchased_kg": 0.0, "total_consumed_kg": 0.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")

    assert result["quantity_sold"] == 100
    assert result["quantity_current"] == 400
    assert result["quantity_mortality"] == 0
    assert result["quantity_reconciles"] is True

    assert result["cost_per_unit"] == 2.0
    assert result["cost_of_goods_sold"] == 200.0          # NOT 1000 — the bug the brief describes
    assert result["remaining_inventory_value"] == 800.0
    assert result["total_accumulated_cost"] == 1000.0
    assert result["gross_profit"] == 100.0                 # NOT -700
    assert result["net_profit"] == 100.0                   # no mortality, so same as gross here

    # The accounting identity that makes this model self-consistent:
    assert round(
        result["cost_of_goods_sold"] + result["remaining_inventory_value"] + result["mortality_loss"], 6
    ) == result["total_accumulated_cost"]


def test_mortality_loss_is_recognized_and_not_silently_absorbed(monkeypatch):
    """500 bought @ $1000, 50 die, 100 sold, 350 remain.
    cost_per_unit = 1000/500 = $2.
    mortality_loss = 50 * 2 = $100 — real, lost capital, not folded into
    the survivors' cost basis and not folded into COGS either."""
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 500,
              "quantity_current": 350, "purchase_price_total": 1000.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [{"quantity": 100, "total_amount": 500.0}],
        "mortality_records": [{"quantity": 50}],
        "medication_records": [],
        "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 0.0, "total_purchased_kg": 0.0, "total_consumed_kg": 0.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")

    assert result["quantity_reconciles"] is True  # 100 + 350 + 50 == 500
    assert result["mortality_loss"] == 100.0
    assert result["cost_of_goods_sold"] == 200.0
    assert result["gross_profit"] == 300.0            # revenue 500 - COGS 200
    assert result["net_profit"] == 200.0              # gross 300 - mortality_loss 100


# ── Full cost stack: purchase + feed + medication + allocated expense ───

def test_all_cost_components_combine_correctly(monkeypatch):
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 100,
              "quantity_current": 100, "purchase_price_total": 200.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [],
        "mortality_records": [],
        "medication_records": [{"cost": 10.0}, {"cost": 5.0}],
        "expenses": [{"amount": 15.0}],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 20.0, "total_purchased_kg": 50.0, "total_consumed_kg": 40.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")

    # 200 purchase + 20 feed + 15 medication + 15 allocated expense = 250
    assert result["purchase_cost"] == 200.0
    assert result["feed_cost"] == 20.0
    assert result["medication_cost"] == 15.0
    assert result["allocated_expenses_cost"] == 15.0
    assert result["total_accumulated_cost"] == 250.0
    assert result["cost_per_unit"] == 2.5


# ── Data-completeness flags ───────────────────────────────────────────────

def test_flags_incomplete_feed_pricing_instead_of_reporting_a_confident_zero(monkeypatch):
    """Feed was consumed but never purchased/priced — feed_cost reads 0.0,
    but the caller needs to know that's "unknown", not "genuinely free"."""
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 100,
              "quantity_current": 100, "purchase_price_total": 0.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [], "mortality_records": [], "medication_records": [], "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 0.0, "total_purchased_kg": 0.0, "total_consumed_kg": 30.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")
    assert result["feed_cost_incomplete"] is True
    assert result["feed_cost"] == 0.0


def test_does_not_flag_feed_incomplete_when_nothing_was_consumed(monkeypatch):
    """No feed consumption logged at all is a normal, complete state
    (e.g. a brand-new batch) — not the same as "consumed but unpriced"."""
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 100,
              "quantity_current": 100, "purchase_price_total": 0.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [], "mortality_records": [], "medication_records": [], "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": None, "total_purchased_kg": 0.0, "total_consumed_kg": 0.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")
    assert result["feed_cost_incomplete"] is False
    assert result["feed_cost"] == 0.0


def test_flags_medication_records_missing_cost(monkeypatch):
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 100,
              "quantity_current": 100, "purchase_price_total": 0.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [], "mortality_records": [],
        "medication_records": [{"cost": 10.0}, {"cost": None}, {"cost": None}],
        "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 0.0, "total_purchased_kg": 0.0, "total_consumed_kg": 0.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")
    assert result["medication_cost"] == 10.0          # only the priced one counted
    assert result["medication_records_missing_cost"] == 2


def test_raises_for_unknown_batch(monkeypatch):
    _patch_get_batch(monkeypatch, None)
    try:
        finance_crud.batch_profit_summary("f1", "nonexistent")
        assert False, "expected ValueError for a batch that doesn't exist on this farm"
    except ValueError:
        pass


def test_quantity_reconciles_flag_catches_inconsistent_batch_history(monkeypatch):
    """If sold + current + mortality doesn't add up to quantity_initial
    (e.g. a data issue predating this feature), the flag must say so
    rather than silently reporting numbers that don't actually add up."""
    batch = {"id": "b1", "batch_name": "Batch 1", "quantity_initial": 500,
              "quantity_current": 400, "purchase_price_total": 1000.0}
    _patch_get_batch(monkeypatch, batch)
    monkeypatch.setattr(finance_crud, "supabase", _fake_supabase({
        "sales": [{"quantity": 50, "total_amount": 150.0}],  # only 50 sold, not 100 — 50+400 != 500
        "mortality_records": [],
        "medication_records": [],
        "expenses": [],
    }))
    monkeypatch.setattr(finance_crud, "feed_cost_summary", lambda farm_id, batch_id: {
        "cost_per_batch": 0.0, "total_purchased_kg": 0.0, "total_consumed_kg": 0.0,
    })

    result = finance_crud.batch_profit_summary("f1", "b1")
    assert result["quantity_reconciles"] is False


# ── Endpoint-level: role restriction and cross-farm protection ──────────

def test_worker_cannot_view_batch_profit(client, make_token, membership_store):
    """Batch cost/profit is financial data — matches finance_routes.py's
    existing worker exclusion on /finance-summary."""
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="worker")
    token = make_token("u1")
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": "f1"}):
        r = client.get("/api/v1/farms/f1/animals/batches/b1/profit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_farmer_can_view_batch_profit(client, make_token, membership_store):
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="farmer")
    token = make_token("u1")
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": "f1"}), \
         patch("routes.animal_routes.crud.batch_profit_summary", return_value={"batch_id": "b1", "net_profit": 42.0}):
        r = client.get("/api/v1/farms/f1/animals/batches/b1/profit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["net_profit"] == 42.0


def test_cannot_view_another_farms_batch_profit(client, make_token, membership_store):
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="farmer")  # member of f1 only
    token = make_token("u1")
    with patch("routes.animal_routes.crud.batch_profit_summary") as mocked:
        r = client.get("/api/v1/farms/f2/animals/batches/b1/profit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    mocked.assert_not_called()


# ── Idempotency / duplicate sale requests (AUDIT.md FWA-007) ─────────────

def test_duplicate_sale_request_rejected_with_same_idempotency_key(client, make_token, membership_store):
    """A farmer double-tapping 'Record Sale' (or a client retrying after a
    timeout) sends the same Idempotency-Key twice — the second request
    must be rejected, not create a second sale."""
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="farmer")
    token = make_token("u1")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "test-key-abc-123"}
    payload = {"quantity": 5, "unit_price": 10.0, "sale_date": "2026-01-01"}

    with patch("routes.finance_routes.crud.create_sale", return_value={"id": "s1"}) as mocked:
        r1 = client.post("/api/v1/farms/f1/sales", headers=headers, json=payload)
        r2 = client.post("/api/v1/farms/f1/sales", headers=headers, json=payload)

    assert r1.status_code == 201
    assert r2.status_code == 409
    mocked.assert_called_once()  # the DB write only happened once, not twice


def test_sale_without_idempotency_key_is_unaffected(client, make_token, membership_store):
    """The header is optional — omitting it keeps today's exact behavior,
    including allowing two genuinely separate identical-looking sales
    (e.g. the same buyer buying the same quantity twice in one day)."""
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="farmer")
    token = make_token("u1")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"quantity": 5, "unit_price": 10.0, "sale_date": "2026-01-01"}

    with patch("routes.finance_routes.crud.create_sale", return_value={"id": "s1"}) as mocked:
        r1 = client.post("/api/v1/farms/f1/sales", headers=headers, json=payload)
        r2 = client.post("/api/v1/farms/f1/sales", headers=headers, json=payload)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert mocked.call_count == 2


def test_different_idempotency_keys_both_succeed(client, make_token, membership_store):
    """Different keys mean genuinely different submissions — both go through."""
    from unittest.mock import patch
    membership_store.add("f1", "u1", role="farmer")
    token = make_token("u1")
    payload = {"quantity": 5, "unit_price": 10.0, "sale_date": "2026-01-01"}

    with patch("routes.finance_routes.crud.create_sale", return_value={"id": "s1"}) as mocked:
        r1 = client.post("/api/v1/farms/f1/sales",
                          headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-1"}, json=payload)
        r2 = client.post("/api/v1/farms/f1/sales",
                          headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-2"}, json=payload)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert mocked.call_count == 2
