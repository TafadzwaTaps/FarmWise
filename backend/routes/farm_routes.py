"""routes/farm_routes.py — Farm CRUD + membership. Routes: /farms, /farms/{farm_id}"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

import crud
from core.auth import get_current_user, require_farm_role
from routes._deps import log

router = APIRouter(prefix="/farms", tags=["Farms"])

_MANAGE_ROLES = ("farmer", "farm_manager")
_OWNER_ONLY = ("farmer",)


class FarmCreate(BaseModel):
    name: str
    location: str | None = None
    size_hectares: float | None = None
    description: str | None = None
    currency: str = "USD"


class FarmUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    size_hectares: float | None = None
    description: str | None = None
    currency: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_farm(data: FarmCreate, user: dict = Depends(get_current_user)):
    """Creating a farm makes the caller its owner (farmer role) automatically."""
    return crud.create_farm(
        name=data.name, owner_id=user["user_id"], location=data.location,
        size_hectares=data.size_hectares, description=data.description, currency=data.currency,
    )


@router.get("")
def list_my_farms(user: dict = Depends(get_current_user)):
    return crud.list_farms_for_user(user["user_id"])


@router.get("/{farm_id}")
def get_farm(farm_id: str, _member: dict = Depends(require_farm_role())):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    return farm


@router.patch("/{farm_id}")
def update_farm(farm_id: str, data: FarmUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    return crud.update_farm(farm_id, fields)


@router.delete("/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm_id: str, _member: dict = Depends(require_farm_role(*_OWNER_ONLY))):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    crud.soft_delete_farm(farm_id)  # soft delete — preserves historical records


@router.get("/{farm_id}/members")
def list_members(farm_id: str, _member: dict = Depends(require_farm_role())):
    members = crud.list_members(farm_id)
    # Enrich with the info actually worth displaying — a bare user_id UUID
    # isn't useful in a team list.
    for m in members:
        user = crud.get_user_by_id(m["user_id"])
        m["user_full_name"] = user["full_name"] if user else "Unknown user"
        m["user_email"] = user["email"] if user else None
    return members
