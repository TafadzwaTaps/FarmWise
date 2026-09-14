"""
tests/test_farm_authorization.py — Phase 2: automated tests for cross-farm
access attempts.

Exercises the real `require_farm_role` dependency (core/auth.py) end to
end through the FastAPI app: real JWTs, real routing, only the
`crud.farms.get_membership` DB read is faked (see conftest.py). The
point of these tests is to prove a user cannot read or write another
farm's data by changing the `farm_id` in the URL — the exact concern
FWA-003/FWA-004 in AUDIT.md investigated and verified against, now
pinned down as a regression test rather than a one-time manual check.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
FARM_B = "22222222-2222-2222-2222-222222222222"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── No token at all ──────────────────────────────────────────────────────

def test_no_token_rejected(client):
    r = client.get(f"/api/v1/farms/{FARM_A}/animals/batches")
    assert r.status_code == 401


# ── The core cross-farm case ─────────────────────────────────────────────

def test_member_of_farm_a_cannot_read_farm_b(client, make_token, membership_store):
    """User A is a farmer on farm A only. Same valid token, same user —
    just a different farm_id in the URL — must not read farm B's data."""
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    with patch("routes.animal_routes.crud.list_batches", return_value=[{"fake": "farm_a_data"}]) as mocked:
        r_own_farm = client.get(f"/api/v1/farms/{FARM_A}/animals/batches", headers=auth_header(token))
        r_other_farm = client.get(f"/api/v1/farms/{FARM_B}/animals/batches", headers=auth_header(token))

    assert r_own_farm.status_code == 200
    assert r_own_farm.json() == [{"fake": "farm_a_data"}]

    assert r_other_farm.status_code == 403
    # crud.list_batches must never even be reached for the farm the user
    # doesn't belong to — the 403 has to come from the auth dependency,
    # not from an empty result the handler happened to return.
    mocked.assert_called_once_with(FARM_A, None)


def test_non_member_cannot_read_any_farm(client, make_token, membership_store):
    """User B has no membership row anywhere — every farm must 403, not
    just ones it's never even heard of."""
    token = make_token(USER_B)
    for farm_id in (FARM_A, FARM_B):
        r = client.get(f"/api/v1/farms/{farm_id}/animals/batches", headers=auth_header(token))
        assert r.status_code == 403


def test_cannot_access_another_farm_via_finance_routes(client, make_token, membership_store):
    """Same cross-farm check, exercised against a different router
    (finance) with role-restricted GET, to catch a router-specific gap
    rather than only testing one route module."""
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    with patch("routes.finance_routes.crud.list_sales", return_value=[]) as mocked:
        r = client.get(f"/api/v1/farms/{FARM_B}/sales", headers=auth_header(token))
    assert r.status_code == 403
    mocked.assert_not_called()


def test_cannot_delete_another_farm(client, make_token, membership_store):
    """Destructive action, not just a read — a non-owner-of-farm-B token
    must not be able to soft-delete farm B."""
    membership_store.add(FARM_A, USER_A, role="farmer")  # owner of A, nothing on B
    token = make_token(USER_A)

    with patch("routes.farm_routes.crud.soft_delete_farm") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_B}", headers=auth_header(token))
    assert r.status_code == 403
    mocked.assert_not_called()


# ── Role checks within a farm the user DOES belong to ────────────────────

def test_worker_role_cannot_create_batch(client, make_token, membership_store):
    """Being a legitimate member of the farm isn't enough for a
    manage-only action — role still has to match."""
    membership_store.add(FARM_A, USER_A, role="worker")
    token = make_token(USER_A)

    with patch("routes.animal_routes.crud.create_batch") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches",
            headers=auth_header(token),
            json={"batch_name": "Batch 1", "species": "chicken_broiler", "quantity_initial": 100},
        )
    assert r.status_code == 403
    mocked.assert_not_called()


def test_manager_role_can_create_batch(client, make_token, membership_store):
    membership_store.add(FARM_A, USER_A, role="farm_manager")
    token = make_token(USER_A)

    with patch("routes.animal_routes.crud.create_batch", return_value={"id": "b1"}) as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches",
            headers=auth_header(token),
            json={"batch_name": "Batch 1", "species": "chicken_broiler", "quantity_initial": 100},
        )
    assert r.status_code == 201
    mocked.assert_called_once()


# ── The farm_id must come from the URL, never from the request body ─────

def test_request_body_farm_id_is_ignored_path_id_is_authoritative(client, make_token, membership_store):
    """User A belongs to farm A only. Sending a body that *mentions*
    farm B (which no endpoint here actually reads farm_id from — it's
    always a path param) must still be evaluated against the PATH's
    farm_id, not anything in the payload."""
    membership_store.add(FARM_A, USER_A, role="farmer")
    token = make_token(USER_A)

    with patch("routes.finance_routes.crud.create_expense", return_value={"id": "e1"}) as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/expenses",
            headers=auth_header(token),
            json={
                "category": "feed", "amount": 10.0, "expense_date": "2026-01-01",
                "farm_id": FARM_B,  # extra/unexpected field — must be ignored, not honored
            },
        )
    assert r.status_code == 201
    called_farm_id = mocked.call_args[0][0]
    assert called_farm_id == FARM_A
