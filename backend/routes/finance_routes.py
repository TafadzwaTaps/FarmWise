"""routes/finance_routes.py — Sales, expenses, income, finance summary.
Routes: /farms/{farm_id}/sales, .../expenses, .../income, .../finance-summary
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role
from services.security import check_idempotency_key, DuplicateSubmission

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


class SaleUpdate(BaseModel):
    """batch_id is deliberately absent — see crud.update_sale's docstring
    for why moving a sale to a different batch isn't supported here."""
    buyer_name: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit_price: float | None = Field(default=None, ge=0)
    discount: float | None = Field(default=None, ge=0)
    payment_method: PaymentMethod | None = None
    sale_date: date | None = None
    notes: str | None = None


class ExpenseCreate(BaseModel):
    category: ExpenseCategory
    amount: float = Field(gt=0)
    expense_date: date
    vendor: str | None = None
    batch_id: str | None = None  # optional — attribute this expense to a specific batch for cost allocation (AUDIT.md FWA-006); omitted, it stays farm-level overhead as before
    notes: str | None = None


class ExpenseUpdate(BaseModel):
    category: ExpenseCategory | None = None
    amount: float | None = Field(default=None, gt=0)
    expense_date: date | None = None
    vendor: str | None = None
    batch_id: str | None = None
    notes: str | None = None


class IncomeCreate(BaseModel):
    category: IncomeCategory
    amount: float = Field(gt=0)
    income_date: date
    notes: str | None = None


class IncomeUpdate(BaseModel):
    category: IncomeCategory | None = None
    amount: float | None = Field(default=None, gt=0)
    income_date: date | None = None
    notes: str | None = None


# ── Sales ────────────────────────────────────────────────────────────────

@router.post("/sales", status_code=status.HTTP_201_CREATED)
def create_sale(
    farm_id: str,
    data: SaleCreate,
    _member: dict = Depends(require_farm_role(*_RECORD_ROLES)),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """A sale against a batch decrements that batch's live quantity, same as
    a mortality event — selling animals removes them from the flock.

    Optional Idempotency-Key header (AUDIT.md FWA-007): a client can send
    the same key on a retry (e.g. after a timeout) and get a clean 409
    instead of a second sale being recorded. Omitting the header keeps
    today's existing behavior exactly — this is opt-in, not required."""
    try:
        check_idempotency_key(f"create_sale:{farm_id}", idempotency_key)
    except DuplicateSubmission:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This sale was already submitted a moment ago (duplicate request detected).",
        )

    if data.batch_id:
        batch = crud.get_batch(farm_id, data.batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
        if data.quantity > batch["quantity_current"]:
            # Fast-path, friendly message for the common case — but this
            # check alone isn't what prevents overselling under concurrent
            # requests; the atomic decrement below is (see crud/animals.py).
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot sell {data.quantity} — only {batch['quantity_current']} remain in this batch",
            )
        try:
            crud.decrement_batch_quantity(batch, data.quantity)
        except ValueError:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This batch's stock just changed (likely another sale or mortality record "
                "happening at the same time). Please refresh and try again.",
            )

    payload = data.model_dump()
    payload["sale_date"] = payload["sale_date"].isoformat()
    return crud.create_sale(farm_id, payload)


@router.get("/sales")
def list_sales(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_sales(farm_id)


@router.patch("/sales/{sale_id}")
def update_sale(farm_id: str, sale_id: str, data: SaleUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    sale = crud.get_sale(farm_id, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")

    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "sale_date" in fields:
        fields["sale_date"] = fields["sale_date"].isoformat()

    if "quantity" in fields and sale["batch_id"]:
        # The sale already decremented the batch by its OLD quantity at
        # creation time — reconcile by the delta, not the new value
        # outright. old=5,new=3 → give back 2. old=5,new=8 → take 3 more
        # (and can still fail with "not enough stock" if there isn't any,
        # same atomic guard as a brand-new sale).
        batch = crud.get_batch(farm_id, sale["batch_id"])
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This sale's batch no longer exists")
        delta = sale["quantity"] - fields["quantity"]  # positive = give back, negative = take more
        try:
            crud.decrement_batch_quantity(batch, -delta)
        except ValueError:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot change quantity to {fields['quantity']} — only {batch['quantity_current']} remain in this batch.",
            )

    updated = crud.update_sale(farm_id, sale_id, fields)
    return updated


@router.delete("/sales/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale(farm_id: str, sale_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    sale = crud.get_sale(farm_id, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    if sale["batch_id"]:
        # Restore the stock this sale took — a deleted sale means those
        # animals were never actually sold, from the batch's point of view.
        batch = crud.get_batch(farm_id, sale["batch_id"])
        if batch is not None:
            crud.decrement_batch_quantity(batch, -sale["quantity"])  # negative amount = give back, see crud/animals.py
    crud.delete_sale(farm_id, sale_id)


# ── Expenses ─────────────────────────────────────────────────────────────

@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(farm_id: str, data: ExpenseCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    if data.batch_id and crud.get_batch(farm_id, data.batch_id) is None:
        # Same "never trust a client-supplied id without checking it belongs
        # to this farm" principle as create_sale's batch_id check.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    payload = data.model_dump()
    payload["expense_date"] = payload["expense_date"].isoformat()
    return crud.create_expense(farm_id, payload)


@router.get("/expenses")
def list_expenses(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_expenses(farm_id)


@router.patch("/expenses/{expense_id}")
def update_expense(farm_id: str, expense_id: str, data: ExpenseUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    if crud.get_expense(farm_id, expense_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found")
    if data.batch_id and crud.get_batch(farm_id, data.batch_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "expense_date" in fields:
        fields["expense_date"] = fields["expense_date"].isoformat()
    return crud.update_expense(farm_id, expense_id, fields)


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(farm_id: str, expense_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    if crud.get_expense(farm_id, expense_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found")
    crud.delete_expense(farm_id, expense_id)


# ── Income ───────────────────────────────────────────────────────────────

@router.post("/income", status_code=status.HTTP_201_CREATED)
def create_income(farm_id: str, data: IncomeCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    payload = data.model_dump()
    payload["income_date"] = payload["income_date"].isoformat()
    return crud.create_income(farm_id, payload)


@router.get("/income")
def list_income(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_income(farm_id)


@router.patch("/income/{income_id}")
def update_income(farm_id: str, income_id: str, data: IncomeUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    if crud.get_income(farm_id, income_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Income record not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "income_date" in fields:
        fields["income_date"] = fields["income_date"].isoformat()
    return crud.update_income(farm_id, income_id, fields)


@router.delete("/income/{income_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_income(farm_id: str, income_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    if crud.get_income(farm_id, income_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Income record not found")
    crud.delete_income(farm_id, income_id)


# ── Dashboard summary ────────────────────────────────────────────────────

@router.get("/finance-summary")
def finance_summary(farm_id: str, period_start: date, period_end: date, _member: dict = Depends(require_farm_role(*_FINANCE_VIEW_ROLES))):
    """Profit/Loss, income, and expense breakdown for the dashboard's charts."""
    return crud.profit_loss_summary(farm_id, period_start.isoformat(), period_end.isoformat())
