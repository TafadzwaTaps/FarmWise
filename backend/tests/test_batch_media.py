"""
tests/test_batch_media.py — evidence photo/video attachments on mortality
and medication records: the upload endpoint's validation, and that a
`media` list actually flows through into create/update payloads.
"""

from unittest.mock import patch

FARM_A = "11111111-1111-1111-1111-111111111111"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _auth(client, make_token, membership_store, role="farmer"):
    membership_store.add(FARM_A, USER_A, role=role)
    token = make_token(USER_A)
    return {"Authorization": f"Bearer {token}"}


# ── Upload endpoint ───────────────────────────────────────────────────────

def test_upload_media_succeeds_for_allowed_image_type(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.upload_batch_media", return_value={"url": "https://x/y.jpg", "type": "image"}) as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("carcass.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        )
    assert r.status_code == 200
    assert r.json() == {"url": "https://x/y.jpg", "type": "image"}
    mocked.assert_called_once()


def test_upload_media_rejects_svg_stored_xss_vector(client, make_token, membership_store):
    """Same defense as field_report_routes.py's upload endpoint — an
    explicit allowlist, not a prefix match, since 'image/' would also
    admit 'image/svg+xml' and SVG can embed <script>."""
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.upload_batch_media") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
        )
    assert r.status_code == 400
    mocked.assert_not_called()


def test_upload_media_rejects_oversized_file(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    oversized = b"x" * (26 * 1024 * 1024)
    with patch("routes.animal_routes.crud.upload_batch_media") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("big.jpg", oversized, "image/jpeg")},
        )
    assert r.status_code == 413
    mocked.assert_not_called()


def test_upload_media_rejects_empty_file(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.upload_batch_media") as mocked:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
    assert r.status_code == 400
    mocked.assert_not_called()


def test_upload_media_accepts_video(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.upload_batch_media", return_value={"url": "https://x/y.mp4", "type": "video"}):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("clip.mp4", b"fake-mp4-bytes", "video/mp4")},
        )
    assert r.status_code == 200
    assert r.json()["type"] == "video"


def test_upload_media_returns_503_when_bucket_missing(client, make_token, membership_store):
    """A missing Supabase Storage bucket shouldn't surface as an opaque
    500 — same graceful-degradation pattern as field_report_routes.py."""
    headers = _auth(client, make_token, membership_store)
    with patch("routes.animal_routes.crud.upload_batch_media", side_effect=Exception("bucket not found")):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("x.jpg", b"data", "image/jpeg")},
        )
    assert r.status_code == 503
    assert "batch-media" in r.json()["error"]["message"]


def test_worker_can_upload_media(client, make_token, membership_store):
    """Matches _RECORD_ROLES — the same roles allowed to log mortality/
    medication in the first place should be able to attach evidence to it."""
    headers = _auth(client, make_token, membership_store, role="worker")
    with patch("routes.animal_routes.crud.upload_batch_media", return_value={"url": "https://x/y.jpg", "type": "image"}):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/media",
            headers=headers,
            files={"file": ("x.jpg", b"data", "image/jpeg")},
        )
    assert r.status_code == 200


# ── media flows through into mortality/medication create+update ─────────

def test_create_mortality_with_media(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 10, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.create_mortality_record", return_value={"id": "m1"}) as mocked_create, \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value=batch):
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality",
            headers=headers,
            json={"date": "2026-01-01", "quantity": 2, "media": [{"url": "https://x/y.jpg", "type": "image"}]},
        )
    assert r.status_code == 201
    called_payload = mocked_create.call_args[0][1]
    assert called_payload["media"] == [{"url": "https://x/y.jpg", "type": "image"}]


def test_create_mortality_without_media_defaults_to_empty_list(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A, "quantity_current": 10, "updated_at": "t0"}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.create_mortality_record", return_value={"id": "m1"}) as mocked_create, \
         patch("routes.animal_routes.crud.decrement_batch_quantity", return_value=batch):
        client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality",
            headers=headers,
            json={"date": "2026-01-01", "quantity": 2},
        )
    called_payload = mocked_create.call_args[0][1]
    assert called_payload["media"] == []


def test_update_mortality_can_change_media(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "m1", "batch_id": "b1", "quantity": 3}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_mortality_record", return_value=record), \
         patch("routes.animal_routes.crud.update_mortality_record", return_value=record) as mocked_update:
        r = client.patch(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality/m1",
            headers=headers,
            json={"media": [{"url": "https://x/new.jpg", "type": "image"}]},
        )
    assert r.status_code == 200
    called_fields = mocked_update.call_args[0][2]
    assert called_fields["media"] == [{"url": "https://x/new.jpg", "type": "image"}]


def test_update_mortality_without_media_field_leaves_it_untouched(client, make_token, membership_store):
    """media wasn't in the request at all — must not overwrite existing
    attachments with an empty list just because the field is omitted."""
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    record = {"id": "m1", "batch_id": "b1", "quantity": 3}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.get_mortality_record", return_value=record), \
         patch("routes.animal_routes.crud.update_mortality_record", return_value=record) as mocked_update:
        client.patch(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality/m1",
            headers=headers,
            json={"cause": "cold"},
        )
    called_fields = mocked_update.call_args[0][2]
    assert "media" not in called_fields


def test_create_medication_with_media(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    batch = {"id": "b1", "farm_id": FARM_A}
    with patch("routes.animal_routes.crud.get_batch", return_value=batch), \
         patch("routes.animal_routes.crud.create_medication_record", return_value={"id": "med1"}) as mocked_create:
        r = client.post(
            f"/api/v1/farms/{FARM_A}/animals/batches/b1/medication",
            headers=headers,
            json={"type": "vaccine", "name": "Newcastle", "media": [{"url": "https://x/vial.jpg", "type": "image"}]},
        )
    assert r.status_code == 201
    called_payload = mocked_create.call_args[0][1]
    assert called_payload["media"] == [{"url": "https://x/vial.jpg", "type": "image"}]


def test_media_item_requires_url_and_type(client, make_token, membership_store):
    headers = _auth(client, make_token, membership_store)
    r = client.post(
        f"/api/v1/farms/{FARM_A}/animals/batches/b1/mortality",
        headers=headers,
        json={"date": "2026-01-01", "quantity": 2, "media": [{"url": "https://x/y.jpg"}]},  # missing "type"
    )
    assert r.status_code == 422
