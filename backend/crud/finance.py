"""crud/finance.py — feed_purchases, feed_consumption, sales, expenses, income.

Summary aggregation is done client-side (pull rows, sum in Python) rather
than via SQL — same approach as WaziBot's crud/analytics.py, since we're
going through supabase-py/PostgREST instead of raw SQL.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many


# ── Feed purchases ───────────────────────────────────────────────────────

def create_feed_purchase(farm_id: str, data: dict) -> dict:
    total_cost = data["quantity_kg"] * data["unit_cost"]
    row = {"id": _new_id(), "farm_id": farm_id, "total_cost": total_cost, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("feed_purchases").insert(row).execute()
    return _one(res)


def list_feed_purchases(farm_id: str) -> list[dict]:
    res = supabase.table("feed_purchases").select("*").eq("farm_id", farm_id).order("purchase_date", desc=True).execute()
    return _many(res)


# ── Feed consumption ─────────────────────────────────────────────────────

def create_feed_consumption(farm_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "farm_id": farm_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("feed_consumption").insert(row).execute()
    return _one(res)


def list_feed_consumption(farm_id: str, batch_id: str | None = None) -> list[dict]:
    query = supabase.table("feed_consumption").select("*").eq("farm_id", farm_id)
    if batch_id:
        query = query.eq("batch_id", batch_id)
    res = query.order("date", desc=True).execute()
    return _many(res)


def feed_cost_summary(farm_id: str, batch_id: str | None = None) -> dict:
    """Cost-per-kg / cost-per-batch / cost-per-animal, computed on the fly
    from purchases + consumption so a corrected unit_cost is reflected
    immediately without a backfill."""
    purchases = list_feed_purchases(farm_id)
    total_purchased_kg = sum(float(p["quantity_kg"]) for p in purchases)
    total_purchase_cost = sum(float(p["total_cost"]) for p in purchases)
    avg_cost_per_kg = (total_purchase_cost / total_purchased_kg) if total_purchased_kg else 0.0

    consumption = list_feed_consumption(farm_id, batch_id)
    total_consumed_kg = sum(float(c["quantity_kg"]) for c in consumption)

    cost_per_batch = total_consumed_kg * avg_cost_per_kg if batch_id else None
    cost_per_animal = None
    if batch_id and cost_per_batch is not None:
        batch_res = supabase.table("animal_batches").select("quantity_current").eq("id", batch_id).limit(1).execute()
        batch = _one(batch_res)
        if batch and batch["quantity_current"]:
            cost_per_animal = cost_per_batch / batch["quantity_current"]

    return {
        "total_purchased_kg": total_purchased_kg,
        "total_purchase_cost": total_purchase_cost,
        "average_cost_per_kg": round(avg_cost_per_kg, 4),
        "total_consumed_kg": total_consumed_kg,
        "cost_per_animal": round(cost_per_animal, 2) if cost_per_animal is not None else None,
        "cost_per_batch": round(cost_per_batch, 2) if cost_per_batch is not None else None,
    }


# ── Sales ────────────────────────────────────────────────────────────────

def create_sale(farm_id: str, data: dict) -> dict:
    total_amount = (data["quantity"] * data["unit_price"]) - data.get("discount", 0)
    row = {
        "id": _new_id(), "farm_id": farm_id, "total_amount": total_amount,
        "created_at": _now(), "updated_at": _now(), **data,
    }
    res = supabase.table("sales").insert(row).execute()
    return _one(res)


def list_sales(farm_id: str, period_start: str | None = None, period_end: str | None = None) -> list[dict]:
    query = supabase.table("sales").select("*").eq("farm_id", farm_id)
    if period_start:
        query = query.gte("sale_date", period_start)
    if period_end:
        query = query.lte("sale_date", period_end)
    res = query.order("sale_date", desc=True).execute()
    return _many(res)


# ── Expenses ─────────────────────────────────────────────────────────────

def create_expense(farm_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "farm_id": farm_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("expenses").insert(row).execute()
    return _one(res)


def list_expenses(farm_id: str, period_start: str | None = None, period_end: str | None = None) -> list[dict]:
    query = supabase.table("expenses").select("*").eq("farm_id", farm_id)
    if period_start:
        query = query.gte("expense_date", period_start)
    if period_end:
        query = query.lte("expense_date", period_end)
    res = query.order("expense_date", desc=True).execute()
    return _many(res)


# ── Income ───────────────────────────────────────────────────────────────

def create_income(farm_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "farm_id": farm_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("income").insert(row).execute()
    return _one(res)


def list_income(farm_id: str, period_start: str | None = None, period_end: str | None = None) -> list[dict]:
    query = supabase.table("income").select("*").eq("farm_id", farm_id)
    if period_start:
        query = query.gte("income_date", period_start)
    if period_end:
        query = query.lte("income_date", period_end)
    res = query.order("income_date", desc=True).execute()
    return _many(res)


# ── Dashboard summary ────────────────────────────────────────────────────

def profit_loss_summary(farm_id: str, period_start: str, period_end: str) -> dict:
    sales = list_sales(farm_id, period_start, period_end)
    total_sales_revenue = sum(float(s["total_amount"]) for s in sales)

    income_rows = list_income(farm_id, period_start, period_end)
    income_by_category: dict[str, float] = defaultdict(float)
    for row in income_rows:
        income_by_category[row["category"]] += float(row["amount"])
    total_other_income = sum(income_by_category.values())

    expense_rows = list_expenses(farm_id, period_start, period_end)
    expenses_by_category: dict[str, float] = defaultdict(float)
    for row in expense_rows:
        expenses_by_category[row["category"]] += float(row["amount"])
    total_expenses = sum(expenses_by_category.values())

    total_income = total_sales_revenue + total_other_income

    return {
        "period_start": period_start,
        "period_end": period_end,
        "total_sales_revenue": total_sales_revenue,
        "total_other_income": total_other_income,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_profit": total_income - total_expenses,
        "expenses_by_category": dict(expenses_by_category),
        "income_by_category": dict(income_by_category),
    }
