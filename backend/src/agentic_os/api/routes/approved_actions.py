"""
Approved Actions catalog — CRUD endpoints.
GET    /api/approved-actions                   list all (optional ?category=)
GET    /api/approved-actions/{id}              get one
POST   /api/approved-actions                   create custom action
PUT    /api/approved-actions/{id}              update (rules, enabled, etc.)
DELETE /api/approved-actions/{id}              delete custom action
POST   /api/approved-actions/validate-process  validate process name against rules
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any
from uuid import UUID
import re

from agentic_os.db.database import get_session
from agentic_os.db.repositories import ApprovedActionRepository

router = APIRouter(prefix="/approved-actions", tags=["approved-actions"])


# ─── Request / Response schemas ────────────────────────────────────────────────

class ProcessRule(BaseModel):
    priority:    int
    allow:       bool
    pattern:     str
    description: str = ""


class ActionCreateRequest(BaseModel):
    tool_name:         str
    name:              str
    description:       Optional[str] = ""
    command:           Optional[str] = None   # default/fallback command
    command_variants:  Optional[dict] = None  # {"docker": "...", "kubernetes": "...", "ssh": "...", "any": "..."}
    category:          str           # diagnostic | remediation_safe | remediation_intrusive
    blast_radius:      int = 1
    requires_approval: bool = False
    enabled:           bool = True
    parameters:        List[Any] = []
    process_rules:     Optional[List[Any]] = None
    output_fields:     List[Any] = []


class ActionUpdateRequest(BaseModel):
    name:              Optional[str]       = None
    description:       Optional[str]       = None
    command:           Optional[str]       = None
    command_variants:  Optional[dict]      = None  # null = keep existing; {} = clear all variants
    blast_radius:      Optional[int]       = None
    requires_approval: Optional[bool]      = None
    enabled:           Optional[bool]      = None
    parameters:        Optional[List[Any]] = None
    process_rules:     Optional[List[Any]] = None   # null = keep existing; [] = clear rules
    output_fields:     Optional[List[Any]] = None   # null = keep existing; [] = clear — rejected for built-in tools


class ProcessValidateRequest(BaseModel):
    tool_name:    str
    process_name: str


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_actions(
    category: Optional[str] = None,
    enabled_only: bool = False,
    db: Session = Depends(get_session),
):
    repo  = ApprovedActionRepository(db)
    items = repo.list(category=category, enabled_only=enabled_only)
    return [repo.to_dict(a) for a in items]


@router.get("/{action_id}")
def get_action(action_id: UUID, db: Session = Depends(get_session)):
    repo   = ApprovedActionRepository(db)
    action = repo.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return repo.to_dict(action)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_action(body: ActionCreateRequest, db: Session = Depends(get_session)):
    repo = ApprovedActionRepository(db)
    existing = repo.get_by_tool_name(body.tool_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action with tool_name '{body.tool_name}' already exists",
        )
    action = repo.create(body.model_dump())
    return repo.to_dict(action)


@router.put("/{action_id}")
def update_action(
    action_id: UUID,
    body: ActionUpdateRequest,
    db: Session = Depends(get_session),
):
    repo = ApprovedActionRepository(db)
    existing = repo.get(action_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")
    if body.output_fields is not None and existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="output_fields is locked for built-in tools and cannot be edited",
        )

    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # Allow explicitly setting process_rules/command_variants to empty collection
    if body.process_rules is not None:
        data["process_rules"] = body.process_rules
    if body.command_variants is not None:
        data["command_variants"] = body.command_variants
    updated = repo.update(action_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Action not found")
    return repo.to_dict(updated)


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action(action_id: UUID, db: Session = Depends(get_session)):
    repo = ApprovedActionRepository(db)
    existing = repo.get(action_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in actions cannot be deleted. Disable the action instead.",
        )
    if not repo.delete(action_id):
        raise HTTPException(status_code=404, detail="Action not found")


@router.post("/validate-process")
def validate_process(body: ProcessValidateRequest, db: Session = Depends(get_session)):
    """
    Check whether a process name is permitted for a given action.
    Returns { allowed: bool, matched_rule: {...} | null, reason: str }
    """
    repo   = ApprovedActionRepository(db)
    action = repo.get_by_tool_name(body.tool_name)

    if not action:
        return {"allowed": False, "matched_rule": None, "reason": f"Action '{body.tool_name}' not found or disabled"}

    if not action.process_rules:
        return {"allowed": True,  "matched_rule": None, "reason": "No process rules configured — allow by default"}

    rules = sorted(action.process_rules, key=lambda r: r.get("priority", 99))
    for rule in rules:
        try:
            if re.match(rule["pattern"], body.process_name):
                return {
                    "allowed":      rule["allow"],
                    "matched_rule": rule,
                    "reason": (
                        f"{'Allowed' if rule['allow'] else 'Denied'} by rule "
                        f"(priority {rule.get('priority', '?')}): {rule.get('description', rule['pattern'])}"
                    ),
                }
        except re.error:
            continue  # skip malformed regex

    return {
        "allowed":      False,
        "matched_rule": None,
        "reason": f"Process '{body.process_name}' did not match any allow rule — denied by default (whitelist policy)",
    }
