"""
tests/test_stock_concurrency.py — regression tests for the race conditions
fixed in crud/animals.py (decrement_batch_quantity) and
crud/inventory.py (adjust_stock). See AUDIT.md FWA-005.

These mock the Supabase query builder directly rather than hitting a real
database — the property under test is "does the code issue the right
atomic, conditional query and handle both outcomes correctly", which
doesn't need a live Postgres to verify.
"""

from unittest.mock import MagicMock

import crud.animals as animals_crud
import crud.inventory as inventory_crud


class _FakeResult:
    def __init__(self, data):
        self.data = data


def _mock_chain(final_result_data):
    """Builds a MagicMock that supports .table().update().eq().eq()...().execute()
    with an arbitrary number of chained .eq()/.gte() calls, always returning
    `final_result_data` from .execute()."""
    table_mock = MagicMock()
    # Every chained call returns the same mock so .eq().eq().gte() all work
    # regardless of call order/count.
    chain = table_mock.update.return_value
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.execute.return_value = _FakeResult(final_result_data)
    return table_mock, chain


# ── animal_batches (crud/animals.py) ─────────────────────────────────────

def test_decrement_batch_quantity_succeeds_with_enough_stock(monkeypatch):
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 5}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    result = animals_crud.decrement_batch_quantity({"id": "b1", "quantity_current": 10}, 5)

    assert result["quantity_current"] == 5
    # The WHERE clause must include the stock guard, not just the id.
    chain.eq.assert_any_call("id", "b1")
    chain.gte.assert_called_once_with("quantity_current", 5)


def test_decrement_batch_quantity_raises_on_lost_race(monkeypatch):
    """Simulates: between the caller's own pre-check and this UPDATE, a
    concurrent request already dropped the row's quantity below what's
    being requested — the WHERE clause matches zero rows."""
    table_mock, chain = _mock_chain([])  # no rows matched
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    try:
        animals_crud.decrement_batch_quantity({"id": "b1", "quantity_current": 10}, 5)
        assert False, "expected ValueError for a lost race, got a normal return"
    except ValueError:
        pass  # correct — caller (routes/finance_routes.py, routes/animal_routes.py) turns this into a 409


def test_decrement_batch_quantity_closes_batch_at_zero(monkeypatch):
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 0, "status": "closed"}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    animals_crud.decrement_batch_quantity({"id": "b1", "quantity_current": 5}, 5)

    called_fields = table_mock.update.call_args[0][0]
    assert called_fields["quantity_current"] == 0
    assert called_fields["status"] == "closed"


# ── inventory_items (crud/inventory.py) ──────────────────────────────────

def test_adjust_stock_succeeds_on_first_try(monkeypatch):
    table_mock, chain = _mock_chain([{"id": "i1", "quantity_on_hand": 15, "updated_at": "t1"}])
    monkeypatch.setattr(inventory_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    item = {"id": "i1", "farm_id": "f1", "quantity_on_hand": 10, "updated_at": "t0"}
    result = inventory_crud.adjust_stock("f1", item, delta=5)

    assert result["quantity_on_hand"] == 15
    # Guarded on the optimistic-concurrency version (updated_at), not a
    # float equality check on the quantity itself.
    chain.eq.assert_any_call("updated_at", "t0")
    chain.eq.assert_any_call("farm_id", "f1")


def test_adjust_stock_retries_after_lost_race_then_succeeds(monkeypatch):
    """First attempt loses the race (another writer changed updated_at in
    between); the function must re-read the row and retry rather than
    silently dropping the delta or raising immediately."""
    table_mock = MagicMock()
    chain = table_mock.update.return_value
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    # First .execute() call: lost race (empty). Second: succeeds.
    chain.execute.side_effect = [_FakeResult([]), _FakeResult([{"id": "i1", "quantity_on_hand": 15, "updated_at": "t2"}])]

    fake_supabase = MagicMock(table=MagicMock(return_value=table_mock))
    monkeypatch.setattr(inventory_crud, "supabase", fake_supabase)

    # get_item() (called on retry) needs its own select chain returning the freshly-changed row.
    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = _FakeResult([{"id": "i1", "farm_id": "f1", "quantity_on_hand": 12, "updated_at": "t1"}])

    def _table_router(name):
        return select_chain if False else table_mock  # both update and select go through the same mocked table here

    # Route .select(...) calls through select_chain, .update(...) through table_mock's own chain.
    table_mock.select.return_value = select_chain

    item = {"id": "i1", "farm_id": "f1", "quantity_on_hand": 10, "updated_at": "t0"}
    result = inventory_crud.adjust_stock("f1", item, delta=5)

    assert result["quantity_on_hand"] == 15
    assert chain.execute.call_count == 2  # first attempt lost, second succeeded


def test_adjust_stock_gives_up_after_exhausting_retries(monkeypatch):
    """Pathological case: every attempt loses the race. Must raise rather
    than loop forever or silently return a wrong value."""
    table_mock = MagicMock()
    chain = table_mock.update.return_value
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.execute.return_value = _FakeResult([])  # always loses

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = _FakeResult([{"id": "i1", "farm_id": "f1", "quantity_on_hand": 10, "updated_at": "t-changed"}])
    table_mock.select.return_value = select_chain

    monkeypatch.setattr(inventory_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    item = {"id": "i1", "farm_id": "f1", "quantity_on_hand": 10, "updated_at": "t0"}
    try:
        inventory_crud.adjust_stock("f1", item, delta=5)
        assert False, "expected ValueError after exhausting retries"
    except ValueError:
        pass
