"""
routes/field_report_routes.py — worker field reports: notes, live data,
and photo/video of livestock and crops, reviewed by farm managers/owners.

Routes: POST/GET /farms/{farm_id}/field-reports,
        GET /farms/{farm_id}/field-reports/{report_id},
        POST /farms/{farm_id}/field-reports/media,
        POST /farms/{farm_id}/field-reports/{report_id}/feedback

Visibility rule (enforced here, not just at the DB level):
  - Any farm member can SUBMIT a report — a manager logging a field note
    themselves is just as valid as a worker doing it.
  - Workers/accountants can only ever see their OWN reports.
  - Only farmer/farm_manager roles see every report on the farm, and only
    they can add feedback.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role
from routes._deps import log

router = APIRouter(prefix="/farms/{farm_id}/field-reports", tags=["Field Reports"])

_MANAGE_ROLES = ("farmer", "farm_manager")
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB per file
# An explicit allowlist, not a prefix match — "image/" would also let
# "image/svg+xml" through, and SVG can embed <script>, making it a stored-
# XSS vector if the "image" is ever rendered/opened directly from its
# public Supabase Storage URL. Every entry here is a real photo/video
# format with no active-content risk.
ALLOWED_MEDIA_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "video/mp4", "video/quicktime", "video/webm",
}


class MediaItem(BaseModel):
    url: str
    type: str  # "image" | "video"


class ReportCreate(BaseModel):
    report_type: str = Field(pattern="^(livestock|crop|general)$")
    notes: str = Field(min_length=1, max_length=4000)
    batch_id: str | None = None
    subject: str | None = Field(default=None, max_length=200)
    media: list[MediaItem] = Field(default_factory=list, max_length=10)


class FeedbackCreate(BaseModel):
    feedback: str = Field(min_length=1, max_length=2000)


def _is_manager(role: str) -> bool:
    return role in _MANAGE_ROLES


def _enrich_with_worker_names(reports: list[dict]) -> list[dict]:
    """A bare worker_id UUID isn't useful in a manager's review list — same
    reasoning as list_members' enrichment in farm_routes.py."""
    for r in reports:
        user = crud.get_user_by_id(r["worker_id"])
        r["worker_full_name"] = user["full_name"] if user else "Unknown user"
    return reports


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report(farm_id: str, data: ReportCreate, member: dict = Depends(require_farm_role())):
    if data.report_type == "livestock" and not data.batch_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "batch_id is required for livestock reports")

    report = crud.create_report(
        farm_id=farm_id,
        worker_id=member["user_id"],
        report_type=data.report_type,
        notes=data.notes,
        batch_id=data.batch_id,
        subject=data.subject,
        media=[m.model_dump() for m in data.media],
    )
    return _enrich_with_worker_names([report])[0]


@router.get("")
def list_reports(farm_id: str, status_filter: str | None = None, member: dict = Depends(require_farm_role())):
    if _is_manager(member["role"]):
        reports = crud.list_reports(farm_id, worker_id=None, status_filter=status_filter)
    else:
        # Workers (and any other non-manager role) only ever see their own submissions.
        reports = crud.list_reports(farm_id, worker_id=member["user_id"], status_filter=status_filter)
    return _enrich_with_worker_names(reports)


@router.get("/{report_id}")
def get_report(farm_id: str, report_id: str, member: dict = Depends(require_farm_role())):
    report = crud.get_report(farm_id, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not _is_manager(member["role"]) and report["worker_id"] != member["user_id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own reports")
    return _enrich_with_worker_names([report])[0]


@router.post("/media")
async def upload_media(farm_id: str, file: UploadFile = File(...), _member: dict = Depends(require_farm_role())):
    if not file.content_type or file.content_type.lower() not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only JPEG, PNG, WEBP, HEIC images or MP4/MOV/WEBM videos are allowed",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_MEDIA_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 25 MB)")
    if not file_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    try:
        return crud.upload_media(farm_id, file_bytes, file.filename or "upload", file.content_type)
    except Exception as exc:
        log.error("field_report_media_upload_failed farm_id=%s error=%s", farm_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not upload media right now — make sure the 'field-reports' storage bucket exists in Supabase.",
        )


@router.post("/{report_id}/feedback")
def add_feedback(farm_id: str, report_id: str, data: FeedbackCreate, member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    report = crud.get_report(farm_id, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    updated = crud.add_feedback(farm_id, report_id, data.feedback, reviewed_by=member["user_id"])
    return _enrich_with_worker_names([updated])[0]
