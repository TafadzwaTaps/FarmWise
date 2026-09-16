"""routes/worker_routes.py — Worker management: employees, attendance, payroll.
Routes: /farms/{farm_id}/workers, .../{worker_id}, .../attendance, .../payments
"""

from datetime import date
from datetime import date as _date  # alias for fields literally named "date" that also carry a default — a bare `date: date | None = None` self-shadows; see AttendanceUpdate below and AUDIT.md
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role, get_current_user
from routes._deps import audit

router = APIRouter(prefix="/farms/{farm_id}/workers", tags=["Workers"])

_MANAGE_ROLES = ("farmer", "farm_manager")
_RECORD_ROLES = ("farmer", "farm_manager")  # attendance/payroll stay manager+ only — not a worker-facing feature yet

WageType = Literal["daily", "weekly", "monthly"]
WorkerStatus = Literal["active", "inactive"]
AttendanceStatus = Literal["present", "absent", "half_day"]


class WorkerCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    position: str | None = None
    phone_number: str | None = None
    wage_amount: float | None = Field(default=None, ge=0)
    wage_type: WageType | None = None
    hire_date: date | None = None
    notes: str | None = None


class WorkerUpdate(BaseModel):
    full_name: str | None = None
    position: str | None = None
    phone_number: str | None = None
    wage_amount: float | None = Field(default=None, ge=0)
    wage_type: WageType | None = None
    status: WorkerStatus | None = None
    notes: str | None = None


class AttendanceCreate(BaseModel):
    date: date
    status: AttendanceStatus
    notes: str | None = None


class AttendanceUpdate(BaseModel):
    date: _date | None = None
    status: AttendanceStatus | None = None
    notes: str | None = None


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_date: date
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = None


class PaymentUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    payment_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = None


def _get_worker_or_404(farm_id: str, worker_id: str) -> dict:
    worker = crud.get_worker(farm_id, worker_id)
    if worker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    return worker


# ── Workers ──────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
def create_worker(farm_id: str, data: WorkerCreate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    payload = data.model_dump()
    payload["hire_date"] = payload["hire_date"].isoformat() if payload["hire_date"] else None
    return crud.create_worker(farm_id, payload)


@router.get("")
def list_workers(farm_id: str, status_filter: str | None = None, _member: dict = Depends(require_farm_role())):
    return crud.list_workers(farm_id, status_filter)


@router.get("/{worker_id}")
def get_worker(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role())):
    return _get_worker_or_404(farm_id, worker_id)


@router.patch("/{worker_id}")
def update_worker(farm_id: str, worker_id: str, data: WorkerUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    return crud.update_worker(farm_id, worker_id, fields)


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_worker(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES)), user: dict = Depends(get_current_user)):
    worker = _get_worker_or_404(farm_id, worker_id)
    crud.delete_worker(farm_id, worker_id)
    audit("worker_deleted", farm_id=farm_id, worker_id=worker_id, worker_name=worker.get("full_name"), by_user=user["user_id"])


# ── Attendance ───────────────────────────────────────────────────────────

@router.post("/{worker_id}/attendance", status_code=status.HTTP_201_CREATED)
def record_attendance(farm_id: str, worker_id: str, data: AttendanceCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_worker_or_404(farm_id, worker_id)

    # Fast-path, friendly message for the common case — but this check
    # alone doesn't prevent a duplicate under concurrent requests; the DB's
    # UNIQUE(worker_id, date) constraint does (see crud/workers.py).
    existing = [a for a in crud.list_attendance(worker_id) if a["date"] == data.date.isoformat()]
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Attendance for {data.date.isoformat()} is already recorded for this worker",
        )

    payload = data.model_dump()
    payload["date"] = payload["date"].isoformat()
    try:
        return crud.record_attendance(worker_id, payload)
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Attendance for {data.date.isoformat()} is already recorded for this worker",
        )


@router.get("/{worker_id}/attendance")
def list_attendance(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role())):
    _get_worker_or_404(farm_id, worker_id)
    return crud.list_attendance(worker_id)


@router.patch("/{worker_id}/attendance/{record_id}")
def update_attendance(farm_id: str, worker_id: str, record_id: str, data: AttendanceUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    if crud.get_attendance_record(worker_id, record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attendance record not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "date" in fields:
        fields["date"] = fields["date"].isoformat()
    try:
        return crud.update_attendance_record(worker_id, record_id, fields)
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This worker already has an attendance record for that date")


@router.delete("/{worker_id}/attendance/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attendance(farm_id: str, worker_id: str, record_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    if crud.get_attendance_record(worker_id, record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attendance record not found")
    crud.delete_attendance_record(worker_id, record_id)


# ── Payments (payroll / salary history) ───────────────────────────────────

@router.post("/{worker_id}/payments", status_code=status.HTTP_201_CREATED)
def create_payment(farm_id: str, worker_id: str, data: PaymentCreate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    payload = data.model_dump()
    payload["payment_date"] = payload["payment_date"].isoformat()
    payload["period_start"] = payload["period_start"].isoformat() if payload["period_start"] else None
    payload["period_end"] = payload["period_end"].isoformat() if payload["period_end"] else None
    return crud.create_payment(worker_id, payload)


@router.get("/{worker_id}/payments")
def list_payments(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role())):
    _get_worker_or_404(farm_id, worker_id)
    return crud.list_payments(worker_id)


@router.patch("/{worker_id}/payments/{payment_id}")
def update_payment(farm_id: str, worker_id: str, payment_id: str, data: PaymentUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    if crud.get_payment(worker_id, payment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment record not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    for date_field in ("payment_date", "period_start", "period_end"):
        if date_field in fields:
            fields[date_field] = fields[date_field].isoformat()
    return crud.update_payment(worker_id, payment_id, fields)


@router.delete("/{worker_id}/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payment(farm_id: str, worker_id: str, payment_id: str, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    if crud.get_payment(worker_id, payment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment record not found")
    crud.delete_payment(worker_id, payment_id)
