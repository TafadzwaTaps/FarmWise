"""routes/finance_routes.py — Sales, expenses, income, finance summary.
Routes: /farms/{farm_id}/sales, .../expenses, .../income, .../finance-summary
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role

router = APIRouter(prefix="/farms/{farm_id}", tags=["Finance"])

_RECORD_ROLES = ("farmer", "farm_manager", "worker")
_FINANCE_VIEW_ROLES = ("farmer", "farm_manager", "accountant")

PaymentMethod = Literal["cash", "ecocash", "onemoney", "bank_transfer", "card", "other"]
ExpenseCategory = Literal[
    "feed", "transport", "electricity", "workers", "fuel", "equipment",
    "maintenance", "veterinary", "utilities", "other",
]
IncomeCategory = Literal["animal_sales", "egg_sales", "milk_sales", "manure", "breeding", "other"]

# Kept for anything that still wants the plain set (e.g. tests, docs).
PAYMENT_METHODS = set(PaymentMethod.__args__)
EXPENSE_CATEGORIES = set(ExpenseCategory.__args__)
INCOME_CATEGORIES = set(IncomeCategory.__args__)


class SaleCreate(BaseModel):
    batch_id: str | None = None
    buyer_name: str | None = None
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    discount: float = Field(default=0, ge=0)
    payment_method: PaymentMethod = "cash"
    sale_date: date
    notes: str | None = None


class ExpenseCreate(BaseModel):
    category: ExpenseCategory
    amount: float = Field(gt=0)
    expense_date: date
    vendor: str | None = None
    notes: str | None = None


class IncomeCreate(BaseModel):
    category: IncomeCategory
    amount: float = Field(gt=0)
    income_date: date
    notes: str | None = None


# ── Sales ────────────────────────────────────────────────────────────────

@router.post("/sales", status_code=status.HTTP_201_CREATED)
def create_sale(farm_id: str, data: SaleCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    """A sale against a batch decrements that batch's live quantity, same as
    a mortality event — selling animals removes them from the flock."""
    if data.batch_id:
        batch = crud.get_batch(farm_id, data.batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
        if data.quantity > batch["quantity_current"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot sell {data.quantity} — only {batch['quantity_current']} remain in this batch",
            )
        crud.decrement_batch_quantity(batch, data.quantity)

    payload = data.model_dump()
    payload["sale_date"] = payload["sale_date"].isoformat()
    return crud.create_sale(farm_id, payload)


@router.get("/sales")
def list_sales(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_sales(farm_id)


# ── Expenses ─────────────────────────────────────────────────────────────

@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(farm_id: str, data: ExpenseCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    payload = data.model_dump()
    payload["expense_date"] = payload["expense_date"].isoformat()
    return crud.create_expense(farm_id, payload)


@router.get("/expenses")
def list_expenses(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_expenses(farm_id)


# ── Income ───────────────────────────────────────────────────────────────

@router.post("/income", status_code=status.HTTP_201_CREATED)
def create_income(farm_id: str, data: IncomeCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    payload = data.model_dump()
    payload["income_date"] = payload["income_date"].isoformat()
    return crud.create_income(farm_id, payload)


@router.get("/income")
def list_income(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_income(farm_id)


# ── Dashboard summary ────────────────────────────────────────────────────

@router.get("/finance-summary")
def finance_summary(farm_id: str, period_start: date, period_end: date, _member: dict = Depends(require_farm_role(*_FINANCE_VIEW_ROLES))):
    """Profit/Loss, income, and expense breakdown for the dashboard's charts."""
    return crud.profit_loss_summary(farm_id, period_start.isoformat(), period_end.isoformat())
