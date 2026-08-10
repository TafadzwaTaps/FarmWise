"""
crud/dashboard.py — executive dashboard aggregation.

Nothing here is a new source of truth: every number is computed on the fly
from the same tables the other crud modules already read/write (animal
batches, feed, sales/expenses/income, mortality, medication, inventory).
Kept separate from those modules because "roll several domains up into one
summary" is a different job than "read/write one domain" — same reasoning
as crud/finance.py's profit_loss_summary, just one level higher.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.db import supabase
from crud._helpers import _many
from crud.animals import list_batches
from crud.finance import (
    list_feed_purchases,
    list_feed_consumption,
    profit_loss_summary,
    list_sales,
    list_expenses,
)
from crud.inventory import list_items

UPCOMING_VACCINATION_WINDOW_DAYS = 14
RECENT_ACTIVITY_LIMIT = 8


def _batch_names(farm_id: str) -> dict[str, str]:
    return {b["id"]: b["batch_name"] for b in list_batches(farm_id)}


def upcoming_vaccinations(farm_id: str) -> list[dict]:
    """Medication records due in the next 14 days, across every batch on
    this farm — a single farm-wide reminder list instead of checking each
    batch's medication tab individually."""
    batch_ids = [b["id"] for b in list_batches(farm_id)]
    if not batch_ids:
        return []

    today = date.today()
    window_end = today + timedelta(days=UPCOMING_VACCINATION_WINDOW_DAYS)
    res = (
        supabase.table("medication_records")
        .select("*")
        .in_("batch_id", batch_ids)
        .gte("next_due_date", today.isoformat())
        .lte("next_due_date", window_end.isoformat())
        .order("next_due_date")
        .execute()
    )
    records = _many(res)
    names = _batch_names(farm_id)
    for r in records:
        r["batch_name"] = names.get(r["batch_id"], "Unknown batch")
    return records


def mortality_rate(farm_id: str, period_start: str, period_end: str) -> dict:
    """Deaths this period as a percentage of the farm's total starting
    headcount — a simple, farm-wide health signal rather than a
    per-batch figure buried in each batch's own tab."""
    batches = list_batches(farm_id)
    total_initial = sum(b["quantity_initial"] for b in batches)
    if not batches:
        return {"deaths_this_period": 0, "rate_pct": 0.0}

    batch_ids = [b["id"] for b in batches]
    res = (
        supabase.table("mortality_records")
        .select("quantity")
        .in_("batch_id", batch_ids)
        .gte("date", period_start)
        .lte("date", period_end)
        .execute()
    )
    deaths = sum(r["quantity"] for r in _many(res))
    rate = (deaths / total_initial * 100) if total_initial else 0.0
    return {"deaths_this_period": deaths, "rate_pct": round(rate, 2)}


def feed_remaining(farm_id: str) -> dict:
    purchased = sum(float(p["quantity_kg"]) for p in list_feed_purchases(farm_id))
    consumed = sum(float(c["quantity_kg"]) for c in list_feed_consumption(farm_id))
    return {
        "total_purchased_kg": purchased,
        "total_consumed_kg": consumed,
        "remaining_kg": round(purchased - consumed, 2),
    }


def recent_activity(farm_id: str) -> list[dict]:
    """A merged, most-recent-first feed of sales/expenses/mortality — the
    'what just happened on this farm' widget. Each source table uses its
    own date column name, so events are normalized to a common shape
    before merging and sorting."""
    events: list[dict] = []

    for s in list_sales(farm_id)[:RECENT_ACTIVITY_LIMIT]:
        events.append({
            "type": "sale", "date": s["sale_date"],
            "summary": f"Sold {s['quantity']} for ${float(s['total_amount']):.2f}",
        })
    for e in list_expenses(farm_id)[:RECENT_ACTIVITY_LIMIT]:
        events.append({
            "type": "expense", "date": e["expense_date"],
            "summary": f"{e['category'].title()} expense — ${float(e['amount']):.2f}",
        })

    batch_ids = [b["id"] for b in list_batches(farm_id)]
    if batch_ids:
        res = (
            supabase.table("mortality_records")
            .select("*")
            .in_("batch_id", batch_ids)
            .order("date", desc=True)
            .limit(RECENT_ACTIVITY_LIMIT)
            .execute()
        )
        names = _batch_names(farm_id)
        for m in _many(res):
            events.append({
                "type": "mortality", "date": m["date"],
                "summary": f"{m['quantity']} lost in {names.get(m['batch_id'], 'a batch')}"
                           + (f" — {m['cause']}" if m.get("cause") else ""),
            })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:RECENT_ACTIVITY_LIMIT]


def dashboard_summary(farm_id: str, period_start: str, period_end: str) -> dict:
    batches = list_batches(farm_id, status_filter="active")
    current_animals = sum(b["quantity_current"] for b in batches)

    items = list_items(farm_id)
    low_stock_items = [i for i in items if i["is_low_stock"]]

    finance = profit_loss_summary(farm_id, period_start, period_end)

    return {
        "period_start": period_start,
        "period_end": period_end,
        "current_animals": current_animals,
        "active_batch_count": len(batches),
        "feed": feed_remaining(farm_id),
        "finance": finance,
        "mortality": mortality_rate(farm_id, period_start, period_end),
        "inventory": {
            "total_items": len(items),
            "low_stock_count": len(low_stock_items),
            "low_stock_items": low_stock_items,
        },
        "upcoming_vaccinations": upcoming_vaccinations(farm_id),
        "recent_activity": recent_activity(farm_id),
    }
