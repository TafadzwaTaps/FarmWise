"""routes/worker_routes.py — Worker management: employees, attendance, payroll.
Routes: /farms/{farm_id}/workers, .../{worker_id}, .../attendance, .../payments
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role

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


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_date: date
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
    return crud.update_worker(worker_id, fields)


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_worker(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_worker_or_404(farm_id, worker_id)
    crud.delete_worker(worker_id)


# ── Attendance ───────────────────────────────────────────────────────────

@router.post("/{worker_id}/attendance", status_code=status.HTTP_201_CREATED)
def record_attendance(farm_id: str, worker_id: str, data: AttendanceCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_worker_or_404(farm_id, worker_id)

    # The DB has a UNIQUE(worker_id, date) constraint — checking first gives
    # a clean 409 instead of letting a raw postgrest unique-violation surface
    # as an unhandled 500 (the same failure mode the enum bug caused).
    existing = [a for a in crud.list_attendance(worker_id) if a["date"] == data.date.isoformat()]
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Attendance for {data.date.isoformat()} is already recorded for this worker",
        )

    payload = data.model_dump()
    payload["date"] = payload["date"].isoformat()
    return crud.record_attendance(worker_id, payload)


@router.get("/{worker_id}/attendance")
def list_attendance(farm_id: str, worker_id: str, _member: dict = Depends(require_farm_role())):
    _get_worker_or_404(farm_id, worker_id)
    return crud.list_attendance(worker_id)


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
