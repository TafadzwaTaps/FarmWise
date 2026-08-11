"""routes/assistant_routes.py — AI Farm Assistant chat.
Routes: POST /farms/{farm_id}/assistant/chat, GET/DELETE .../assistant/history
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import get_current_user, require_farm_role
from services.ai_service import chat as ai_chat, AssistantUnavailableError

router = APIRouter(prefix="/farms/{farm_id}/assistant", tags=["AI Assistant"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/chat")
def send_message(
    farm_id: str,
    data: ChatRequest,
    _member: dict = Depends(require_farm_role()),
    user: dict = Depends(get_current_user),
):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")

    # Store the user's message regardless of whether the model call
    # succeeds — the conversation history should reflect what was actually
    # asked, and a failed AI call shouldn't silently erase that.
    crud.create_ai_message(farm_id, user["user_id"], "user", data.message)

    try:
        reply = ai_chat(farm_id, user["user_id"], farm["name"], farm.get("currency", "USD"), data.message)
    except AssistantUnavailableError:
        reply = "I'm having trouble reaching the AI service right now. Please try again in a moment."

    saved = crud.create_ai_message(farm_id, user["user_id"], "assistant", reply)
    return {"reply": reply, "created_at": saved["created_at"]}


@router.get("/history")
def get_history(farm_id: str, _member: dict = Depends(require_farm_role()), user: dict = Depends(get_current_user)):
    return crud.list_ai_messages(farm_id, user["user_id"])


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def delete_history(farm_id: str, _member: dict = Depends(require_farm_role()), user: dict = Depends(get_current_user)):
    crud.clear_ai_history(farm_id, user["user_id"])
