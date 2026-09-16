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
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 5, "updated_at": "t1"}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 10, "updated_at": "t0", "status": "active"}
    result = animals_crud.decrement_batch_quantity(batch, 5)

    assert result["quantity_current"] == 5
    # Guarded on the optimistic-concurrency version (updated_at), not a
    # WHERE-clause quantity comparison — see the function's docstring for
    # why the old .gte() approach could lose an update under concurrency.
    chain.eq.assert_any_call("id", "b1")
    chain.eq.assert_any_call("updated_at", "t0")


def test_decrement_batch_quantity_raises_when_insufficient_stock():
    """The sufficiency check now happens in Python against the batch dict
    passed in (or freshly re-read on retry), before any query is even
    issued — a request for more than what's on hand is rejected
    immediately rather than round-tripping to the DB first."""
    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 3, "updated_at": "t0", "status": "active"}
    try:
        animals_crud.decrement_batch_quantity(batch, 5)
        assert False, "expected ValueError for insufficient stock"
    except ValueError as e:
        assert "insufficient_stock" in str(e)


def test_decrement_batch_quantity_closes_batch_at_zero(monkeypatch):
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 0, "status": "closed", "updated_at": "t1"}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 5, "updated_at": "t0", "status": "active"}
    animals_crud.decrement_batch_quantity(batch, 5)

    called_fields = table_mock.update.call_args[0][0]
    assert called_fields["quantity_current"] == 0
    assert called_fields["status"] == "closed"


def test_decrement_batch_quantity_retries_after_lost_race_then_succeeds(monkeypatch):
    """The actual regression test for the fix: a concurrent writer changes
    the row between this function's first read and its write — the first
    attempt must lose cleanly (zero rows matched, not a wrong value
    written) and retry against a freshly re-read row, landing on the
    CORRECT final quantity rather than silently overwriting with a
    stale-derived one. This is exactly the batch-at-10/two-sales-of-3
    scenario from the docstring: this call is the "second" sale, racing
    against a concurrent first sale that already landed."""
    table_mock = MagicMock()
    chain = table_mock.update.return_value
    chain.eq.return_value = chain
    # First .execute(): lost the race (another writer already changed
    # updated_at). Second .execute(): succeeds against the fresh value.
    chain.execute.side_effect = [_FakeResult([]), _FakeResult([{"id": "b1", "quantity_current": 4, "updated_at": "t2"}])]

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.is_.return_value = select_chain
    select_chain.limit.return_value = select_chain
    # The concurrent sale already landed: quantity dropped from 10 to 7.
    select_chain.execute.return_value = _FakeResult([{"id": "b1", "farm_id": "f1", "quantity_current": 7, "updated_at": "t1", "status": "active"}])

    def _table_router(name):
        m = MagicMock()
        m.update.return_value = chain
        m.select.return_value = select_chain
        return m
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(side_effect=_table_router)))

    # This call started with a stale in-memory batch (quantity_current=10,
    # as if read before the concurrent sale landed) and asks to take 3.
    stale_batch = {"id": "b1", "farm_id": "f1", "quantity_current": 10, "updated_at": "t0", "status": "active"}
    result = animals_crud.decrement_batch_quantity(stale_batch, 3)

    # Correct final answer is 7 - 3 = 4, NOT 10 - 3 = 7 (which is what the
    # old buggy version would have silently written, discarding the
    # concurrent sale's effect entirely).
    assert result["quantity_current"] == 4
    assert chain.execute.call_count == 2


def test_decrement_batch_quantity_gives_up_after_exhausting_retries(monkeypatch):
    table_mock = MagicMock()
    chain = table_mock.update.return_value
    chain.eq.return_value = chain
    chain.execute.return_value = _FakeResult([])  # always loses

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.is_.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = _FakeResult([{"id": "b1", "farm_id": "f1", "quantity_current": 7, "updated_at": "t-changed", "status": "active"}])

    def _table_router(name):
        m = MagicMock()
        m.update.return_value = chain
        m.select.return_value = select_chain
        return m
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(side_effect=_table_router)))

    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 10, "updated_at": "t0", "status": "active"}
    try:
        animals_crud.decrement_batch_quantity(batch, 3)
        assert False, "expected ValueError after exhausting retries"
    except ValueError:
        pass


def test_decrement_batch_quantity_negative_amount_restores_stock(monkeypatch):
    """Used by sale/mortality edit-and-delete to give stock back — see
    routes/finance_routes.py's delete_sale and
    routes/animal_routes.py's delete_mortality."""
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 8, "updated_at": "t1"}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 5, "updated_at": "t0", "status": "active"}
    result = animals_crud.decrement_batch_quantity(batch, -3)  # negative = give back

    assert result["quantity_current"] == 8
    called_fields = table_mock.update.call_args[0][0]
    assert called_fields["quantity_current"] == 8


def test_decrement_batch_quantity_reopens_closed_batch_on_restore(monkeypatch):
    """A batch that auto-closed at zero, then had a sale/mortality record
    against it deleted, should reopen — a 'closed' batch with a positive
    headcount is an inconsistent state that could never happen before
    this pass added stock-restoring callers."""
    table_mock, chain = _mock_chain([{"id": "b1", "quantity_current": 3, "status": "active", "updated_at": "t1"}])
    monkeypatch.setattr(animals_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    batch = {"id": "b1", "farm_id": "f1", "quantity_current": 0, "updated_at": "t0", "status": "closed"}
    animals_crud.decrement_batch_quantity(batch, -3)

    called_fields = table_mock.update.call_args[0][0]
    assert called_fields["status"] == "active"


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


# ── worker_attendance (crud/workers.py) — DB-level duplicate backstop ───

def test_record_attendance_succeeds_normally(monkeypatch):
    import crud.workers as workers_crud
    table_mock = MagicMock()
    table_mock.insert.return_value.execute.return_value = _FakeResult(
        [{"id": "a1", "worker_id": "w1", "date": "2026-01-01", "status": "present"}]
    )
    monkeypatch.setattr(workers_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    result = workers_crud.record_attendance("w1", {"date": "2026-01-01", "status": "present"})
    assert result["id"] == "a1"


def test_record_attendance_raises_clean_error_on_db_unique_violation(monkeypatch):
    """The application-level pre-check in routes/worker_routes.py is a
    check-then-act race by itself — this proves the DB-level backstop
    (the UNIQUE(worker_id, date) constraint added in
    farmwise_indexes_and_constraints_migration.sql) is actually caught
    and turned into a clean ValueError, not a raw 500-causing exception,
    when two concurrent submissions both get past the pre-check."""
    import crud.workers as workers_crud
    from postgrest.exceptions import APIError

    table_mock = MagicMock()
    table_mock.insert.return_value.execute.side_effect = APIError(
        {"code": "23505", "message": "duplicate key value violates unique constraint"}
    )
    monkeypatch.setattr(workers_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    try:
        workers_crud.record_attendance("w1", {"date": "2026-01-01", "status": "present"})
        assert False, "expected ValueError for a unique-constraint violation"
    except ValueError:
        pass


def test_record_attendance_reraises_unrelated_db_errors(monkeypatch):
    """Only a 23505 (unique violation) should be swallowed into a
    ValueError — any other DB error must propagate normally so it isn't
    silently misreported as 'duplicate'."""
    import crud.workers as workers_crud
    from postgrest.exceptions import APIError

    table_mock = MagicMock()
    table_mock.insert.return_value.execute.side_effect = APIError(
        {"code": "23503", "message": "foreign key violation"}
    )
    monkeypatch.setattr(workers_crud, "supabase", MagicMock(table=MagicMock(return_value=table_mock)))

    try:
        workers_crud.record_attendance("w1", {"date": "2026-01-01", "status": "present"})
        assert False, "expected the original APIError to propagate"
    except ValueError:
        assert False, "a non-unique-violation error must not be reported as a duplicate"
    except APIError:
        pass
