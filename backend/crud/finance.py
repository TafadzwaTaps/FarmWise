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
from crud.animals import get_batch


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


# ── Per-batch cost allocation (AUDIT.md FWA-006) ─────────────────────────

def batch_profit_summary(farm_id: str, batch_id: str) -> dict:
    """Real per-batch profit, using a weighted-average costing method —
    NOT the naive "all farm income minus all farm expenses in a date
    window" that profit_loss_summary() above computes (that's a whole-
    farm period P&L, which is a genuinely different and still-useful
    report; this is the per-batch one AUDIT.md FWA-006 was about).

    The method: every unit ever placed into the batch (quantity_initial)
    is assumed to carry an equal share of the batch's total accumulated
    cost — purchase price + feed consumed + medication + any expense the
    farmer explicitly attributed to this batch. That gives a single
    cost-per-unit, applied to whatever happened to each unit since:

        sold        → cost_of_goods_sold        (revenue vs. this batch's actual cost)
        still alive → remaining_inventory_value (capital tied up, not yet a profit or loss)
        died        → mortality_loss            (cost incurred that no sale will ever recover)

    By construction, cost_of_goods_sold + remaining_inventory_value +
    mortality_loss == total_accumulated_cost, and quantity_sold +
    quantity_current + quantity_mortality == quantity_initial — this is
    the exact worked example from the original brief: 500 bought, 100
    sold, 400 remain. The full purchase cost is NOT charged against the
    100 sold; only 100/500 of it is, and the other 400/500 sits in
    `remaining_inventory_value` instead of `net_profit`.

    This mirrors WaziBot's overall approach of aggregating in Python
    rather than in SQL, and reuses feed_cost_summary()'s existing
    average-cost-per-kg logic rather than recomputing it, so this stays
    consistent with what that endpoint already reports.

    Two known data-completeness gaps are surfaced, not hidden:
      - feed_cost_incomplete: feed was consumed by this batch but no
        feed purchase (any feed_type) has ever been logged, so there's
        no price to apply — feed_cost silently reads as 0.0 without
        this flag, which would be a confidently wrong number.
      - medication_records_missing_cost: count of medication rows for
        this batch that have no `cost` value — medication_cost only
        sums what's actually priced, so this tells the caller how much
        of the true cost isn't captured yet.
    """
    batch = get_batch(farm_id, batch_id)
    if batch is None:
        raise ValueError("batch_not_found")

    quantity_initial = batch["quantity_initial"]
    quantity_current = batch["quantity_current"]

    sales_res = supabase.table("sales").select("quantity,total_amount").eq("farm_id", farm_id).eq("batch_id", batch_id).execute()
    sales = _many(sales_res)
    quantity_sold = sum(s["quantity"] for s in sales)
    total_sales_revenue = sum(float(s["total_amount"]) for s in sales)

    mortality_res = supabase.table("mortality_records").select("quantity").eq("batch_id", batch_id).execute()
    quantity_mortality = sum(r["quantity"] for r in _many(mortality_res))

    purchase_cost = float(batch["purchase_price_total"] or 0)

    feed = feed_cost_summary(farm_id, batch_id)
    feed_cost = feed["cost_per_batch"] or 0.0
    feed_cost_incomplete = feed["total_consumed_kg"] > 0 and feed["total_purchased_kg"] == 0

    medication_res = supabase.table("medication_records").select("cost").eq("batch_id", batch_id).execute()
    medication_records = _many(medication_res)
    medication_cost = sum(float(m["cost"]) for m in medication_records if m.get("cost") is not None)
    medication_records_missing_cost = sum(1 for m in medication_records if m.get("cost") is None)

    allocated_res = supabase.table("expenses").select("amount").eq("farm_id", farm_id).eq("batch_id", batch_id).execute()
    allocated_expenses_cost = sum(float(e["amount"]) for e in _many(allocated_res))

    total_accumulated_cost = purchase_cost + feed_cost + medication_cost + allocated_expenses_cost
    cost_per_unit = (total_accumulated_cost / quantity_initial) if quantity_initial else 0.0

    cost_of_goods_sold = cost_per_unit * quantity_sold
    remaining_inventory_value = cost_per_unit * quantity_current
    mortality_loss = cost_per_unit * quantity_mortality

    gross_profit = total_sales_revenue - cost_of_goods_sold
    net_profit = total_sales_revenue - cost_of_goods_sold - mortality_loss

    # Sanity check on the model, not a live guard: if these don't add up,
    # something about the batch's history is inconsistent (e.g. a sale or
    # mortality record predating this feature, or a manual DB edit) —
    # surfaced so the farmer/support can investigate, not silently ignored.
    quantity_reconciles = (quantity_sold + quantity_current + quantity_mortality) == quantity_initial

    return {
        "batch_id": batch_id,
        "batch_name": batch["batch_name"],
        "quantity_initial": quantity_initial,
        "quantity_sold": quantity_sold,
        "quantity_current": quantity_current,
        "quantity_mortality": quantity_mortality,
        "quantity_reconciles": quantity_reconciles,
        "purchase_cost": round(purchase_cost, 2),
        "feed_cost": round(feed_cost, 2),
        "feed_cost_incomplete": feed_cost_incomplete,
        "medication_cost": round(medication_cost, 2),
        "medication_records_missing_cost": medication_records_missing_cost,
        "allocated_expenses_cost": round(allocated_expenses_cost, 2),
        "total_accumulated_cost": round(total_accumulated_cost, 2),
        "cost_per_unit": round(cost_per_unit, 4),
        "total_sales_revenue": round(total_sales_revenue, 2),
        "cost_of_goods_sold": round(cost_of_goods_sold, 2),
        "remaining_inventory_value": round(remaining_inventory_value, 2),
        "mortality_loss": round(mortality_loss, 2),
        "gross_profit": round(gross_profit, 2),
        "net_profit": round(net_profit, 2),
    }
