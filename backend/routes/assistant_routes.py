"""routes/assistant_routes.py — AI Farm Assistant chat.
Routes: POST /farms/{farm_id}/assistant/chat, GET/DELETE .../assistant/history
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

import crud
from core.auth import get_current_user, require_farm_role
from services.ai_service import chat as ai_chat, AssistantUnavailableError
from services.security import check as _rate_check, RateLimitExceeded
from routes._deps import audit

router = APIRouter(prefix="/farms/{farm_id}/assistant", tags=["AI Assistant"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/chat")
async def send_message(
    farm_id: str,
    data: ChatRequest,
    request: Request,
    member: dict = Depends(require_farm_role()),
    user: dict = Depends(get_current_user),
):
    # Every call here hits Gemini's API (a shared, quota-limited, per-project
    # key — see services/ai_service.py) and now costs real money/quota once
    # past the free tier. Two limits, not one:
    #   - per-user: one chatty or malicious farm member can't exhaust the
    #     whole app's AI quota for every other farm.
    #   - per-farm: several different members of the SAME farm hammering it
    #     independently would each pass their own per-user limit while
    #     still adding up to disproportionate load against one farm.
    # Both scoped to the user/farm id (not just IP) since IPs are shared
    # behind NAT/mobile carriers in the regions this app targets.
    try:
        _rate_check(f"ai-chat-user:{user['user_id']}", request, max_calls=20, window_seconds=3600)
        _rate_check(f"ai-chat-farm:{farm_id}", request, max_calls=60, window_seconds=3600)
    except RateLimitExceeded:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You've reached the AI assistant's hourly message limit. Please try again in a bit.",
        )

    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")

    # Store the user's message regardless of whether the model call
    # succeeds — the conversation history should reflect what was actually
    # asked, and a failed AI call shouldn't silently erase that.
    crud.create_ai_message(farm_id, user["user_id"], "user", data.message)

    try:
        reply = await ai_chat(
            farm_id, user["user_id"], farm["name"], farm.get("currency", "USD"),
            data.message, member["role"],
        )
        audit("ai_chat_completed", farm_id=farm_id, user_id=user["user_id"], role=member["role"])
    except AssistantUnavailableError as exc:
        # Config errors (bad API key, wrong provider's key, etc.) come with
        # an actionable message worth showing directly — "try again" would
        # be actively misleading for something a retry can't fix.
        reply = str(exc) or "I'm having trouble reaching the AI service right now. Please try again in a moment."
        audit("ai_chat_failed", farm_id=farm_id, user_id=user["user_id"], role=member["role"], reason=str(exc)[:200])

    saved = crud.create_ai_message(farm_id, user["user_id"], "assistant", reply)
    return {"reply": reply, "created_at": saved["created_at"]}


@router.get("/history")
def get_history(farm_id: str, _member: dict = Depends(require_farm_role()), user: dict = Depends(get_current_user)):
    return crud.list_ai_messages(farm_id, user["user_id"])


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def delete_history(farm_id: str, _member: dict = Depends(require_farm_role()), user: dict = Depends(get_current_user)):
    crud.clear_ai_history(farm_id, user["user_id"])
