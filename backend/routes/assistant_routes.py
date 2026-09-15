"""routes/assistant_routes.py — AI Farm Assistant chat.
Routes: POST /farms/{farm_id}/assistant/chat, POST .../assistant/diagnose,
        GET/DELETE .../assistant/history
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

import crud
from core.auth import get_current_user, require_farm_role
from services.ai_service import chat as ai_chat, diagnose_image as ai_diagnose_image, AssistantUnavailableError
from services.security import check as _rate_check, RateLimitExceeded
from routes._deps import audit

router = APIRouter(prefix="/farms/{farm_id}/assistant", tags=["AI Assistant"])

# Same allowlist principle as field_report_routes.py's ALLOWED_MEDIA_TYPES —
# an explicit list of real photo formats, not a "image/*" prefix match
# (which would also admit image/svg+xml, a stored-XSS vector — see
# AUDIT.md FWA-018). Diagnosis is photo-only, no video: a vet-style visual
# read doesn't need motion, and stills keep the request small/cheap.
ALLOWED_DIAGNOSIS_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_DIAGNOSIS_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB — a phone photo, not a batch of them; keeps each Gemini call cheap


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


@router.post("/diagnose")
async def diagnose(
    farm_id: str,
    request: Request,
    member: dict = Depends(require_farm_role()),
    user: dict = Depends(get_current_user),
    photo: UploadFile = File(...),
    note: str | None = Form(default=None),
):
    """Photo-based animal health triage — not a real diagnosis, an
    observational read from Gemini's vision model with an explicit
    not-a-vet disclaimer (see services/ai_service.py's system prompt).
    Reuses the same free-tier Gemini model as regular chat — same cost
    structure, no separate paid product — but images use more of that
    free tier's quota per call than plain text, so this has its own,
    tighter rate limit rather than sharing regular chat's budget.
    """
    try:
        _rate_check(f"ai-diagnose-user:{user['user_id']}", request, max_calls=10, window_seconds=3600)
        _rate_check(f"ai-diagnose-farm:{farm_id}", request, max_calls=30, window_seconds=3600)
    except RateLimitExceeded:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You've reached the photo-diagnosis hourly limit. Please try again in a bit, or use regular chat.",
        )

    if not photo.content_type or photo.content_type.lower() not in ALLOWED_DIAGNOSIS_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only JPEG, PNG, WEBP, or HEIC photos are supported for diagnosis.",
        )

    image_bytes = await photo.read()
    if not image_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(image_bytes) > MAX_DIAGNOSIS_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photo too large (max 8 MB)")

    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")

    # ai_messages.content is a text column — there's no binary/image storage
    # here (and no new table/bucket needed for this feature, per the "keep
    # it low-cost" brief). The user's turn is recorded as a plain-text
    # marker so the conversation history in GET /history stays coherent
    # and honest about what happened, without trying to persist the photo
    # itself — the diagnosis text is what's worth keeping, not the image.
    user_turn = f"[Photo submitted for diagnosis]{' — ' + note if note else ''}"
    crud.create_ai_message(farm_id, user["user_id"], "user", user_turn)

    try:
        reply = await ai_diagnose_image(
            farm_id, user["user_id"], farm["name"], image_bytes, photo.content_type, note,
        )
        audit("ai_diagnose_completed", farm_id=farm_id, user_id=user["user_id"])
    except AssistantUnavailableError as exc:
        reply = str(exc) or "I'm having trouble reaching the AI service right now. Please try again in a moment."
        audit("ai_diagnose_failed", farm_id=farm_id, user_id=user["user_id"], reason=str(exc)[:200])

    saved = crud.create_ai_message(farm_id, user["user_id"], "assistant", reply)
    return {"reply": reply, "created_at": saved["created_at"]}
