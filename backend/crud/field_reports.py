"""
crud/field_reports.py — worker-submitted field reports (notes, live data,
photo/video of livestock and crops) and manager feedback.

Media goes to Supabase Storage (bucket: "field-reports") — see the note in
farmwise_field_reports_migration.sql for the one manual setup step SQL
can't do (creating the bucket itself).
"""

from __future__ import annotations

import mimetypes
import uuid as _uuid
from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

MEDIA_BUCKET = "field-reports"
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB — generous for a phone photo/short clip, not unbounded


def create_report(
    farm_id: str,
    worker_id: str,
    report_type: str,
    notes: str,
    batch_id: Optional[str] = None,
    subject: Optional[str] = None,
    media: Optional[list[dict]] = None,
) -> dict:
    row = {
        "id": _new_id(),
        "farm_id": farm_id,
        "worker_id": worker_id,
        "report_type": report_type,
        "batch_id": batch_id,
        "subject": subject,
        "notes": notes,
        "media": media or [],
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = supabase.table("field_reports").insert(row).execute()
    return _one(res)


def get_report(farm_id: str, report_id: str) -> Optional[dict]:
    res = (
        supabase.table("field_reports").select("*")
        .eq("id", report_id).eq("farm_id", farm_id).limit(1).execute()
    )
    return _one(res)


def list_reports(farm_id: str, worker_id: Optional[str] = None, status_filter: Optional[str] = None) -> list[dict]:
    """worker_id=None means "all reports on this farm" — callers must only
    pass None when the caller's role actually permits seeing everyone's
    reports (enforced in routes/field_report_routes.py, not here)."""
    query = supabase.table("field_reports").select("*").eq("farm_id", farm_id)
    if worker_id:
        query = query.eq("worker_id", worker_id)
    if status_filter:
        query = query.eq("status", status_filter)
    res = query.order("created_at", desc=True).execute()
    return _many(res)


def add_feedback(report_id: str, manager_feedback: str, reviewed_by: str) -> Optional[dict]:
    fields = {
        "manager_feedback": manager_feedback,
        "status": "reviewed",
        "reviewed_by": reviewed_by,
        "reviewed_at": _now(),
        "updated_at": _now(),
    }
    res = supabase.table("field_reports").update(fields).eq("id", report_id).execute()
    return _one(res)


def upload_media(farm_id: str, file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Uploads one photo/video to Supabase Storage and returns
    {"url": public_url, "type": "image"|"video"} ready to attach to a
    report's media list."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else mimetypes.guess_extension(content_type) or "bin")
    storage_path = f"{farm_id}/{_uuid.uuid4()}.{ext}"

    supabase.storage.from_(MEDIA_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type}
    )
    public_url = supabase.storage.from_(MEDIA_BUCKET).get_public_url(storage_path)

    media_type = "video" if content_type.startswith("video/") else "image"
    return {"url": public_url, "type": media_type}
