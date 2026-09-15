"""
tests/test_financial_validation.py — Phase 8: the remaining "Financials"
and "AI" checklist items not already covered by test_batch_profit.py /
test_stock_concurrency.py / test_ai_assistant.py — specifically Pydantic
boundary validation and a regression test for the feed-consumption
batch_id ownership gap found during this pass.
"""

from unittest.mock import patch


# ── Feed consumption — batch_id ownership check (new this phase) ────────

def test_feed_consumption_rejects_batch_id_from_another_farm(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    with patch("routes.feed_routes.crud.get_batch", return_value=None) as mocked_get_batch, \
         patch("routes.feed_routes.crud.create_feed_consumption") as mocked_create:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/feed/consumption",
            headers=auth_header(token),
            json={"feed_type": "layer mash", "quantity_kg": 10, "date": "2026-01-01", "batch_id": "not-this-farms-batch"},
        )
    assert r.status_code == 404
    mocked_get_batch.assert_called_once_with(FARM_A, "not-this-farms-batch")
    mocked_create.assert_not_called()  # never reached the insert


def test_feed_consumption_without_batch_id_still_works(client, make_token, membership_store):
    """batch_id is optional — omitting it (farm-wide feed use, not tied to
    one batch) must not be affected by the new check."""
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    with patch("routes.feed_routes.crud.get_batch") as mocked_get_batch, \
         patch("routes.feed_routes.crud.create_feed_consumption", return_value={"id": "fc1"}) as mocked_create:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/feed/consumption",
            headers=auth_header(token),
            json={"feed_type": "layer mash", "quantity_kg": 10, "date": "2026-01-01"},
        )
    assert r.status_code == 201
    mocked_get_batch.assert_not_called()  # no batch_id given — nothing to check
    mocked_create.assert_called_once()


# ── Pydantic boundary validation (Phase 8's "Financials" checklist) ─────

def test_expense_rejects_zero_amount(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/expenses",
        headers=auth_header(token),
        json={"category": "feed", "amount": 0, "expense_date": "2026-01-01"},
    )
    assert r.status_code == 422


def test_expense_rejects_negative_amount(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/expenses",
        headers=auth_header(token),
        json={"category": "feed", "amount": -50, "expense_date": "2026-01-01"},
    )
    assert r.status_code == 422


def test_sale_rejects_zero_quantity(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/sales",
        headers=auth_header(token),
        json={"quantity": 0, "unit_price": 10.0, "sale_date": "2026-01-01"},
    )
    assert r.status_code == 422


def test_sale_rejects_negative_unit_price(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/sales",
        headers=auth_header(token),
        json={"quantity": 5, "unit_price": -1, "sale_date": "2026-01-01"},
    )
    assert r.status_code == 422


def test_feed_purchase_rejects_zero_quantity(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/feed/purchases",
        headers=auth_header(token),
        json={"feed_type": "layer mash", "quantity_kg": 0, "unit_cost": 1.0, "purchase_date": "2026-01-01"},
    )
    assert r.status_code == 422


def test_mortality_rejects_zero_quantity(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": FARM_A, "quantity_current": 10}):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality",
            headers=auth_header(token),
            json={"date": "2026-01-01", "quantity": 0},
        )
    assert r.status_code == 422


def test_mortality_rejects_more_than_batch_has(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)
    with patch("routes.animal_routes.crud.get_batch", return_value={"id": "b1", "farm_id": FARM_A, "quantity_current": 5}), \
         patch("routes.animal_routes.crud.create_mortality_record") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality",
            headers=auth_header(token),
            json={"date": "2026-01-01", "quantity": 10},
        )
    assert r.status_code == 400
    mocked.assert_not_called()


# ── AI usage limits (Phase 8's "AI" checklist) ───────────────────────────

def test_ai_chat_blocked_after_per_user_hourly_limit(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, USER_A, auth_header
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    async def _fake_ai_chat(*a, **k):
        return "ok"

    with patch("routes.assistant_routes.crud.get_farm", return_value={"id": FARM_A, "name": "F", "currency": "USD"}), \
         patch("routes.assistant_routes.crud.create_ai_message", return_value={"created_at": "t"}), \
         patch("routes.assistant_routes.ai_chat", _fake_ai_chat):
        last = None
        for _ in range(21):  # limit is 20/hour per user (routes/assistant_routes.py)
            last = client.post(
                f"/api/v1/farms/{FARM_A}/assistant/chat",
                headers=auth_header(token),
                json={"message": "hi"},
            )
    assert last.status_code == 429


def test_ai_chat_allowed_under_the_limit(client, make_token, membership_store):
    from tests.test_farm_authorization import FARM_A, auth_header
    # A different user than test_ai_chat_blocked_after_per_user_hourly_limit —
    # the rate limiter (services/security.py) is in-process global state
    # keyed by user id, so reusing USER_A here would inherit that test's
    # 21 calls within the same test run and fail for an unrelated reason.
    other_user = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    membership_store.add(FARM_A, other_user, role="farmer")
    token = make_token(other_user)

    async def _fake_ai_chat(*a, **k):
        return "ok"

    with patch("routes.assistant_routes.crud.get_farm", return_value={"id": FARM_A, "name": "F", "currency": "USD"}), \
         patch("routes.assistant_routes.crud.create_ai_message", return_value={"created_at": "t"}), \
         patch("routes.assistant_routes.ai_chat", _fake_ai_chat):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/assistant/chat",
            headers=auth_header(token),
            json={"message": "hi"},
        )
    assert r.status_code == 200
