"""routes/farm_routes.py — Farm CRUD + membership. Routes: /farms, /farms/{farm_id}"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import get_current_user, require_farm_role
from routes._deps import audit

router = APIRouter(prefix="/farms", tags=["Farms"])

_MANAGE_ROLES = ("farmer", "farm_manager")
_OWNER_ONLY = ("farmer",)
_VALID_MEMBER_ROLES = ("farmer", "farm_manager", "worker", "accountant")  # matches farm_members' CHECK constraint


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


class MemberInvite(BaseModel):
    identifier: str = Field(min_length=1)  # the invitee's email or phone number — must already have a FarmWise account
    role: str = Field(pattern="^(farmer|farm_manager|worker|accountant)$")


class MemberRoleUpdate(BaseModel):
    role: str = Field(pattern="^(farmer|farm_manager|worker|accountant)$")


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
def update_farm(farm_id: str, data: FarmUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES)), user: dict = Depends(get_current_user)):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    updated = crud.update_farm(farm_id, fields)
    audit("farm_updated", farm_id=farm_id, by_user=user["user_id"], fields=list(fields.keys()))
    return updated


@router.delete("/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm_id: str, _member: dict = Depends(require_farm_role(*_OWNER_ONLY)), user: dict = Depends(get_current_user)):
    farm = crud.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    crud.soft_delete_farm(farm_id)  # soft delete — preserves historical records
    audit("farm_deleted", farm_id=farm_id, farm_name=farm.get("name"), by_user=user["user_id"])


def _enrich_members(members: list[dict]) -> list[dict]:
    for m in members:
        user = crud.get_user_by_id(m["user_id"])
        m["user_full_name"] = user["full_name"] if user else "Unknown user"
        m["user_email"] = user["email"] if user else None
    return members


@router.get("/{farm_id}/members")
def list_members(farm_id: str, _member: dict = Depends(require_farm_role())):
    # Enrich with the info actually worth displaying — a bare user_id UUID
    # isn't useful in a team list.
    return _enrich_members(crud.list_members(farm_id))


@router.post("/{farm_id}/members", status_code=status.HTTP_201_CREATED)
def invite_member(farm_id: str, data: MemberInvite, member: dict = Depends(require_farm_role(*_OWNER_ONLY)), user: dict = Depends(get_current_user)):
    """Adds an EXISTING FarmWise user to this farm by their email or phone
    number — there's no separate pending-invite/accept flow (no new table,
    no email-sending integration required). If nobody with that identifier
    has an account yet, the caller is told to have that person sign up
    first and try again, rather than silently doing nothing or creating a
    stub account on their behalf."""
    invitee = crud.get_user_by_identifier(data.identifier)
    if invitee is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No FarmWise account found with that email or phone number. Ask them to sign up first, then try again.",
        )
    try:
        new_member = crud.add_member(farm_id, invitee["id"], role=data.role, invited_by=user["user_id"])
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member of this farm.")
    audit("member_invited", farm_id=farm_id, invited_user_id=invitee["id"], role=data.role, by_user=user["user_id"])
    return _enrich_members([new_member])[0]


def _require_target_member_not_owner(farm_id: str, member_id: str) -> dict:
    """Shared guard for role-change/removal: the farm's schema-level owner
    (farms.owner_id) can never be demoted or removed through this
    endpoint — doing so would leave the farm without the one account
    every other permission check in this codebase ultimately traces back
    to, with no ownership-transfer flow to recover from it. Transferring
    ownership deliberately isn't built here — it's a bigger, separate
    decision than "manage my team" and deserves its own explicit flow."""
    target = crud.get_member_by_id(farm_id, member_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    farm = crud.get_farm(farm_id)
    if farm is not None and target["user_id"] == farm["owner_id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The farm's owner cannot be removed or have their role changed here.")
    return target


@router.patch("/{farm_id}/members/{member_id}")
def update_member_role(farm_id: str, member_id: str, data: MemberRoleUpdate, member: dict = Depends(require_farm_role(*_OWNER_ONLY)), user: dict = Depends(get_current_user)):
    target = _require_target_member_not_owner(farm_id, member_id)
    updated = crud.update_member_role(farm_id, member_id, data.role)
    audit("member_role_changed", farm_id=farm_id, target_user_id=target["user_id"],
          old_role=target["role"], new_role=data.role, by_user=user["user_id"])
    return _enrich_members([updated])[0]


@router.delete("/{farm_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(farm_id: str, member_id: str, member: dict = Depends(require_farm_role(*_OWNER_ONLY)), user: dict = Depends(get_current_user)):
    target = _require_target_member_not_owner(farm_id, member_id)
    crud.remove_member(farm_id, member_id)
    audit("member_removed", farm_id=farm_id, target_user_id=target["user_id"], role=target["role"], by_user=user["user_id"])
