"""
tests/test_member_management.py — team member management: invite an
existing user by identifier, change a member's role, remove a member.
Owner-protection safeguards (the farm's schema-level owner can never be
demoted or removed here) are the main thing worth pinning down with
tests, since getting that wrong could orphan a farm.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
OWNER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MANAGER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
WORKER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
INVITEE = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _farm(**overrides):
    f = {"id": FARM_A, "name": "Test Farm", "owner_id": OWNER, "currency": "USD"}
    f.update(overrides)
    return f


def _member_row(**overrides):
    m = {"id": "m1", "farm_id": FARM_A, "user_id": WORKER, "role": "worker", "created_at": "t", "updated_at": "t"}
    m.update(overrides)
    return m


# ── Invite ────────────────────────────────────────────────────────────

def test_owner_can_invite_existing_user(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_user_by_identifier", return_value={"id": INVITEE, "full_name": "New Person", "email": "new@example.com"}), \
         patch("routes.farm_routes.crud.add_member", return_value=_member_row(id="m2", user_id=INVITEE, role="worker")), \
         patch("routes.farm_routes.crud.get_user_by_id", return_value={"full_name": "New Person", "email": "new@example.com"}):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/members",
            headers={"Authorization": f"Bearer {token}"},
            json={"identifier": "new@example.com", "role": "worker"},
        )
    assert r.status_code == 201
    assert r.json()["user_full_name"] == "New Person"


def test_manager_cannot_invite_members(client, make_token, membership_store):
    """Invite/role-change/removal are owner (farmer role) only — a
    farm_manager can do a lot, but not reshape who's on the team."""
    membership_store.add(FARM_A, MANAGER, role="farm_manager")
    token = make_token(MANAGER)
    with patch("routes.farm_routes.crud.add_member") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/members",
            headers={"Authorization": f"Bearer {token}"},
            json={"identifier": "new@example.com", "role": "worker"},
        )
    assert r.status_code == 403
    mocked.assert_not_called()


def test_invite_unknown_identifier_rejected(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_user_by_identifier", return_value=None):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/members",
            headers={"Authorization": f"Bearer {token}"},
            json={"identifier": "nobody@example.com", "role": "worker"},
        )
    assert r.status_code == 404


def test_invite_already_a_member_rejected_cleanly(client, make_token, membership_store):
    """crud.add_member raises ValueError on the DB's unique-constraint
    violation (farm_id, user_id) — must surface as a clean 409, not a 500."""
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_user_by_identifier", return_value={"id": WORKER, "full_name": "Already Here"}), \
         patch("routes.farm_routes.crud.add_member", side_effect=ValueError("already_a_member")):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/members",
            headers={"Authorization": f"Bearer {token}"},
            json={"identifier": "already@example.com", "role": "worker"},
        )
    assert r.status_code == 409


def test_invite_rejects_invalid_role(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"identifier": "x@example.com", "role": "superadmin"},
    )
    assert r.status_code == 422


# ── Role change ───────────────────────────────────────────────────────

def test_owner_can_change_a_members_role(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_member_by_id", return_value=_member_row()), \
         patch("routes.farm_routes.crud.get_farm", return_value=_farm()), \
         patch("routes.farm_routes.crud.update_member_role", return_value=_member_row(role="farm_manager")), \
         patch("routes.farm_routes.crud.get_user_by_id", return_value={"full_name": "Worker One", "email": "w@example.com"}):
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/members/m1",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "farm_manager"},
        )
    assert r.status_code == 200
    assert r.json()["role"] == "farm_manager"


def test_cannot_change_the_owners_own_role(client, make_token, membership_store):
    """The membership row targeted belongs to farms.owner_id — must be
    rejected before any update happens, regardless of who's asking."""
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    owner_membership = _member_row(id="m_owner", user_id=OWNER, role="farmer")
    with patch("routes.farm_routes.crud.get_member_by_id", return_value=owner_membership), \
         patch("routes.farm_routes.crud.get_farm", return_value=_farm(owner_id=OWNER)), \
         patch("routes.farm_routes.crud.update_member_role") as mocked:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/members/m_owner",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "worker"},
        )
    assert r.status_code == 400
    mocked.assert_not_called()


def test_manager_cannot_change_roles(client, make_token, membership_store):
    membership_store.add(FARM_A, MANAGER, role="farm_manager")
    token = make_token(MANAGER)
    with patch("routes.farm_routes.crud.update_member_role") as mocked:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/members/m1",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "farm_manager"},
        )
    assert r.status_code == 403
    mocked.assert_not_called()


# ── Removal ───────────────────────────────────────────────────────────

def test_owner_can_remove_a_member(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_member_by_id", return_value=_member_row()), \
         patch("routes.farm_routes.crud.get_farm", return_value=_farm()), \
         patch("routes.farm_routes.crud.remove_member") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/members/m1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "m1")


def test_cannot_remove_the_owner(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    owner_membership = _member_row(id="m_owner", user_id=OWNER, role="farmer")
    with patch("routes.farm_routes.crud.get_member_by_id", return_value=owner_membership), \
         patch("routes.farm_routes.crud.get_farm", return_value=_farm(owner_id=OWNER)), \
         patch("routes.farm_routes.crud.remove_member") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/members/m_owner", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
    mocked.assert_not_called()


def test_worker_cannot_remove_members(client, make_token, membership_store):
    membership_store.add(FARM_A, WORKER, role="worker")
    token = make_token(WORKER)
    with patch("routes.farm_routes.crud.remove_member") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/members/m1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    mocked.assert_not_called()


def test_remove_nonexistent_member_returns_404(client, make_token, membership_store):
    membership_store.add(FARM_A, OWNER, role="farmer")
    token = make_token(OWNER)
    with patch("routes.farm_routes.crud.get_member_by_id", return_value=None):
        r = client.delete(f"/api/v1/farms/{FARM_A}/members/nonexistent", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
