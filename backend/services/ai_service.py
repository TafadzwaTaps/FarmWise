"""
services/ai_service.py — the AI Farm Assistant's actual "brain".

Grounds every answer in the farm's own real data (pulled through the same
crud functions every other route uses — nothing new to keep in sync) rather
than letting the model guess. Degrades gracefully: no API key configured,
or a network/API error, returns a clear, honest message instead of crashing
the request — same philosophy as services/notification_service.py.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import httpx

import crud
from crud.dashboard import dashboard_summary

log = logging.getLogger("farmwise.ai")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Verify this against Anthropic's current model list before relying on it in
# production — model names/aliases change over time and this was set at
# writing time, not fetched live.
DEFAULT_MODEL = "claude-3-5-sonnet-latest"
MAX_TOKENS = 1024
CONTEXT_HISTORY_TURNS = 10  # recent turns fed back to the model as conversation context


class AssistantUnavailableError(Exception):
    """Raised when the assistant can't be reached — caller decides how to
    surface this (the route turns it into a normal chat reply, not a 500,
    since 'the AI is briefly unavailable' isn't a server error)."""


def _build_farm_context(farm_id: str) -> str:
    """A compact, structured snapshot of the farm — grounds every answer in
    real numbers instead of the model inventing plausible-sounding ones."""
    end = date.today()
    start = end - timedelta(days=30)
    summary = dashboard_summary(farm_id, start.isoformat(), end.isoformat())
    batches = crud.list_batches(farm_id)
    workers = crud.list_workers(farm_id, status_filter="active")

    batch_lines = "\n".join(
        f"  - {b['batch_name']} ({b['species']}): {b['quantity_current']}/{b['quantity_initial']} remaining, status={b['status']}"
        for b in batches
    ) or "  (no batches yet)"

    expense_lines = "\n".join(
        f"  - {cat}: {amt:.2f}" for cat, amt in (summary["finance"]["expenses_by_category"] or {}).items()
    ) or "  (none this period)"

    return f"""FARM SNAPSHOT (last 30 days, as of {end.isoformat()})

Animals: {summary['current_animals']} across {summary['active_batch_count']} active batch(es)
Batches:
{batch_lines}

Feed: {summary['feed']['total_purchased_kg']:.1f}kg purchased, {summary['feed']['total_consumed_kg']:.1f}kg consumed, {summary['feed']['remaining_kg']:.1f}kg remaining

Finance:
  - Total income: {summary['finance']['total_income']:.2f} (sales: {summary['finance']['total_sales_revenue']:.2f}, other: {summary['finance']['total_other_income']:.2f})
  - Total expenses: {summary['finance']['total_expenses']:.2f}
  - Net profit: {summary['finance']['net_profit']:.2f}
  - Expenses by category:
{expense_lines}

Mortality: {summary['mortality']['deaths_this_period']} deaths this period ({summary['mortality']['rate_pct']}% of starting headcount)

Inventory: {summary['inventory']['total_items']} items tracked, {summary['inventory']['low_stock_count']} running low
Upcoming vaccinations (next 14 days): {len(summary['upcoming_vaccinations'])}

Active workers: {len(workers)}
"""


def _system_prompt(farm_name: str, currency: str, context: str) -> str:
    return f"""You are the FarmWise AI assistant for "{farm_name}", a farm management app used by \
farmers across Africa. Answer questions about THIS farm using only the data below — never invent \
numbers that aren't in it. If something isn't covered by the data, say so plainly and suggest what \
the farmer could log to get that answer next time (e.g. "log a mortality record" or "add an expense").

Be concise and practical — most users are on a phone. Use {currency} for money. Round sensibly. \
You are not a veterinarian or accountant; for anything requiring professional judgment, say so and \
suggest they consult one.

{context}"""


def chat(farm_id: str, user_id: str, farm_name: str, currency: str, message: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("ai_chat_unconfigured farm_id=%s", farm_id)
        return (
            "The AI assistant isn't set up yet on this deployment — an administrator needs to add "
            "an ANTHROPIC_API_KEY in the backend's environment variables to turn this on."
        )

    context = _build_farm_context(farm_id)
    system = _system_prompt(farm_name, currency, context)

    history = crud.list_ai_messages(farm_id, user_id, limit=CONTEXT_HISTORY_TURNS)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})

    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)

    try:
        res = httpx.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": messages},
            timeout=30.0,
        )
        res.raise_for_status()
        data = res.json()
        reply = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if not reply:
            raise AssistantUnavailableError("Empty response from model")
        return reply
    except httpx.HTTPStatusError as exc:
        log.error("ai_chat_http_error farm_id=%s status=%s body=%s", farm_id, exc.response.status_code, exc.response.text[:300])
        raise AssistantUnavailableError(f"AI service returned {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        log.error("ai_chat_network_error farm_id=%s error=%s", farm_id, exc)
        raise AssistantUnavailableError("Could not reach the AI service") from exc
