"""
services/ai_service.py — the AI Farm Assistant's actual "brain".

Grounds every answer in the farm's own real data (pulled through the same
crud functions every other route uses — nothing new to keep in sync) rather
than letting the model guess. Degrades gracefully: no API key configured,
or a network/API error, returns a clear, honest message instead of crashing
the request — same philosophy as services/notification_service.py.

Uses Google's Gemini API rather than a paid provider — its free tier (as of
2026) needs no credit card and comfortably covers a farm assistant's usage.
Get a key at https://aistudio.google.com (no billing setup required for the
free tier). Swapping to a different provider later only means changing this
file — routes/assistant_routes.py and crud/assistant.py don't know or care
which model answers the question.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import httpx

import crud
from crud.dashboard import dashboard_summary

log = logging.getLogger("farmwise.ai")

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Verify this against Google AI Studio's current model list before relying
# on it in production — Gemini model names/versions move fairly often, and
# this was set at writing time, not fetched live. gemini-2.5-flash is the
# free-tier baseline as of mid-2026; check aistudio.google.com for anything
# newer with an active free tier.
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 1024
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
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.warning("ai_chat_unconfigured farm_id=%s", farm_id)
        return (
            "The AI assistant isn't set up yet on this deployment — an administrator needs to add "
            "a GEMINI_API_KEY in the backend's environment variables to turn this on. "
            "Free keys are available at aistudio.google.com."
        )

    # Catches the single most common misconfiguration before ever making a
    # network call: pasting the WRONG provider's key into GEMINI_API_KEY.
    # Google AI Studio keys always start with "AIza"; Anthropic keys start
    # with "sk-ant-", OpenAI keys with "sk-", etc. A wrong-provider key
    # would otherwise fail as an opaque 401 from Google with no hint why.
    if not api_key.startswith("AIza"):
        log.error("ai_chat_key_wrong_format farm_id=%s prefix=%s", farm_id, api_key[:8])
        raise AssistantUnavailableError(
            "GEMINI_API_KEY doesn't look like a Google AI Studio key (those start with 'AIza...'). "
            "This looks like it might be a key from a different provider — double check the value "
            "in Render's environment variables against the key shown at aistudio.google.com."
        )

    context = _build_farm_context(farm_id)
    system = _system_prompt(farm_name, currency, context)

    history = crud.list_ai_messages(farm_id, user_id, limit=CONTEXT_HISTORY_TURNS)
    # Gemini uses "model" for the assistant's own turns, not "assistant" —
    # translating at the wire boundary keeps the app's own role vocabulary
    # (used in the DB and everywhere else) provider-agnostic.
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in history
    ]
    contents.append({"role": "user", "parts": [{"text": message}]})

    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    url = GEMINI_API_URL.format(model=model)

    try:
        res = httpx.post(
            url,
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
            params={"key": api_key},  # some proxies/environments only honor the query param — send both
            json={
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
            },
            timeout=30.0,
        )
        res.raise_for_status()
        data = res.json()

        candidates = data.get("candidates") or []
        if not candidates:
            # The prompt itself may have been blocked (safety filters, etc.)
            # rather than a transport failure — worth a distinct message.
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            log.warning("ai_chat_no_candidates farm_id=%s block_reason=%s", farm_id, reason)
            raise AssistantUnavailableError(f"No response candidates (blockReason={reason})")

        parts = candidates[0].get("content", {}).get("parts", [])
        reply = "".join(p.get("text", "") for p in parts)
        if not reply:
            raise AssistantUnavailableError("Empty response from model")
        return reply
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        log.error("ai_chat_http_error farm_id=%s status=%s body=%s", farm_id, status, exc.response.text[:300])
        if status in (401, 403):
            raise AssistantUnavailableError(
                f"Gemini rejected the API key (status {status}). Verify GEMINI_API_KEY in Render's "
                "environment matches an active key from aistudio.google.com, and that the key hasn't "
                "been deleted or regenerated since it was set."
            ) from exc
        raise AssistantUnavailableError(f"AI service returned {status}") from exc
    except httpx.HTTPError as exc:
        log.error("ai_chat_network_error farm_id=%s error=%s", farm_id, exc)
        raise AssistantUnavailableError("Could not reach the AI service") from exc
