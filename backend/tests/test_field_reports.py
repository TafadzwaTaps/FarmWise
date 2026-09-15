"""
tests/test_field_reports.py — PATCH/DELETE on field reports, added this
pass to close a gap the mobile app's client code already assumed was
closed (see routes/field_report_routes.py's _require_editable_own_report
docstring): only the report's own author, only while still 'pending'.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
AUTHOR = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_USER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _report(**overrides):
    r = {
        "id": "r1", "farm_id": FARM_A, "worker_id": AUTHOR, "report_type": "general",
        "notes": "Original notes", "subject": "Original subject", "status": "pending",
        "media": [], "created_at": "t", "updated_at": "t",
    }
    r.update(overrides)
    return r


def test_author_can_edit_own_pending_report(client, make_token, membership_store):
    membership_store.add(FARM_A, AUTHOR, role="worker")
    token = make_token(AUTHOR)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report()), \
         patch("routes.field_report_routes.crud.update_report", return_value=_report(notes="Updated notes")), \
         patch("routes.field_report_routes.crud.get_user_by_id", return_value={"full_name": "Worker One"}):
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/field-reports/r1",
            headers={"Authorization": f"Bearer {token}"},
            json={"notes": "Updated notes"},
        )
    assert r.status_code == 200
    assert r.json()["notes"] == "Updated notes"


def test_non_author_cannot_edit_report(client, make_token, membership_store):
    """Even a manager (who CAN give feedback) cannot rewrite someone
    else's report — feedback and authorship are different powers."""
    membership_store.add(FARM_A, OTHER_USER, role="farm_manager")
    token = make_token(OTHER_USER)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report()), \
         patch("routes.field_report_routes.crud.update_report") as mocked:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/field-reports/r1",
            headers={"Authorization": f"Bearer {token}"},
            json={"notes": "Hijacked"},
        )
    assert r.status_code == 403
    mocked.assert_not_called()


def test_cannot_edit_already_reviewed_report(client, make_token, membership_store):
    membership_store.add(FARM_A, AUTHOR, role="worker")
    token = make_token(AUTHOR)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report(status="reviewed")), \
         patch("routes.field_report_routes.crud.update_report") as mocked:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/field-reports/r1",
            headers={"Authorization": f"Bearer {token}"},
            json={"notes": "Too late"},
        )
    assert r.status_code == 400
    mocked.assert_not_called()


def test_author_can_delete_own_pending_report(client, make_token, membership_store):
    membership_store.add(FARM_A, AUTHOR, role="worker")
    token = make_token(AUTHOR)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report()), \
         patch("routes.field_report_routes.crud.delete_report") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/field-reports/r1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204
    mocked.assert_called_once_with(FARM_A, "r1")


def test_non_author_cannot_delete_report(client, make_token, membership_store):
    membership_store.add(FARM_A, OTHER_USER, role="farmer")
    token = make_token(OTHER_USER)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report()), \
         patch("routes.field_report_routes.crud.delete_report") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/field-reports/r1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    mocked.assert_not_called()


def test_cannot_delete_already_reviewed_report(client, make_token, membership_store):
    membership_store.add(FARM_A, AUTHOR, role="worker")
    token = make_token(AUTHOR)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report(status="reviewed")), \
         patch("routes.field_report_routes.crud.delete_report") as mocked:
        r = client.delete(f"/api/v1/farms/{FARM_A}/field-reports/r1", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
    mocked.assert_not_called()


def test_update_with_no_fields_rejected(client, make_token, membership_store):
    membership_store.add(FARM_A, AUTHOR, role="worker")
    token = make_token(AUTHOR)
    with patch("routes.field_report_routes.crud.get_report", return_value=_report()), \
         patch("routes.field_report_routes.crud.update_report") as mocked:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/field-reports/r1",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
    assert r.status_code == 400
    mocked.assert_not_called()
