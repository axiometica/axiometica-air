"""
Policy Management Endpoints

APIs for creating, retrieving, updating, and deleting incident response policies.
Policies define matching rules and approved actions for automated incident response.
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

from agentic_os.db.database import get_session
from agentic_os.db.repositories import PolicyRepository

router = APIRouter()


# Request/Response models
class PolicySubmit(BaseModel):
    """Create or update a policy"""
    name: str
    rules: dict = {}
    approved_actions: list[str] = []
    requires_manual_approval: bool = False
    approval_priority: int = 50
    constraints: dict = {}
    description: Optional[str] = None
    enabled: Optional[bool] = True
    # Confidence gate — bypass approval when runbook confidence + run count are met
    confidence_gate_threshold: Optional[float] = None  # 0.0–1.0 (e.g. 0.90)
    confidence_gate_min_runs:  Optional[int]   = None  # minimum successful executions
    # Pin the gate to one specific runbook instead of the event_type/service/
    # platform lookup cascade resolving whichever runbook matches at execution
    # time. None keeps the cascade-lookup (auto-select best match) behavior.
    confidence_gate_runbook_id: Optional[str] = None


class PolicyResponse(BaseModel):
    """Policy response"""
    policy_id: str
    name: str
    rules: dict
    approved_actions: list[str]
    requires_manual_approval: bool
    approval_priority: int
    constraints: dict
    enabled: bool
    description: Optional[str]
    created_at: str
    updated_at: str
    confidence_gate_threshold: Optional[float] = None
    confidence_gate_min_runs:  Optional[int]   = None
    confidence_gate_runbook_id: Optional[str] = None
    status: str = "published"
    published_at: Optional[str] = None
    has_unpublished_changes: bool = False
    draft_snapshot: Optional[dict] = None


# Endpoints
@router.get("/policies", response_model=list[PolicyResponse])
async def list_policies(
    enabled_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    """List policies with optional filtering"""
    try:
        repo = PolicyRepository(db)
        policies = repo.list_all(enabled_only=enabled_only)

        # Apply offset and limit
        policies = policies[offset : offset + limit]

        return [PolicyResponse(**repo.to_dict(p)) for p in policies]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/policies/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: str,
    db: Session = Depends(get_session),
):
    """Get policy by ID"""
    try:
        repo = PolicyRepository(db)
        policy = repo.get(UUID(policy_id))

        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        return PolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policies", response_model=PolicyResponse, status_code=201)
async def create_policy(
    policy: PolicySubmit,
    db: Session = Depends(get_session),
):
    """Create a new policy"""
    try:
        repo = PolicyRepository(db)
        new_policy = repo.create(
            name=policy.name,
            rules=policy.rules,
            approved_actions=policy.approved_actions,
            requires_manual_approval=policy.requires_manual_approval,
            approval_priority=policy.approval_priority,
            constraints=policy.constraints,
            description=policy.description,
            confidence_gate_threshold=policy.confidence_gate_threshold,
            confidence_gate_min_runs=policy.confidence_gate_min_runs,
            confidence_gate_runbook_id=UUID(policy.confidence_gate_runbook_id) if policy.confidence_gate_runbook_id else None,
        )

        return PolicyResponse(**repo.to_dict(new_policy))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/policies/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: str,
    policy: PolicySubmit,
    db: Session = Depends(get_session),
):
    """Update a policy — writes to draft_snapshot only; live columns (what
    PolicyBrokerAgent reads) are untouched until POST .../publish. `enabled`
    is an instant kill-switch, applied immediately outside draft/publish."""
    try:
        repo = PolicyRepository(db)
        repo.set_enabled(UUID(policy_id), policy.enabled)
        updated_policy = repo.save_draft(
            UUID(policy_id),
            {
                "name": policy.name,
                "rules": policy.rules,
                "approved_actions": policy.approved_actions,
                "requires_manual_approval": policy.requires_manual_approval,
                "approval_priority": policy.approval_priority,
                "constraints": policy.constraints,
                "description": policy.description,
                "confidence_gate_threshold": policy.confidence_gate_threshold,
                "confidence_gate_min_runs": policy.confidence_gate_min_runs,
                "confidence_gate_runbook_id": UUID(policy.confidence_gate_runbook_id) if policy.confidence_gate_runbook_id else None,
            },
        )

        if not updated_policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        return PolicyResponse(**repo.to_dict(updated_policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/policies/{policy_id}/publish", response_model=PolicyResponse)
async def publish_policy(policy_id: str, body: dict = Body(default={}), db: Session = Depends(get_session)):
    """Promote the current draft to live and record a version-history row."""
    try:
        repo = PolicyRepository(db)
        policy = repo.publish(UUID(policy_id), change_note=body.get("change_note"))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return PolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")


@router.post("/policies/{policy_id}/discard-draft", response_model=PolicyResponse)
async def discard_policy_draft(policy_id: str, db: Session = Depends(get_session)):
    """Discard pending draft edits — resets draft_snapshot to mirror live state."""
    try:
        repo = PolicyRepository(db)
        policy = repo.discard_draft(UUID(policy_id))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return PolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")


@router.patch("/policies/{policy_id}/enabled", response_model=PolicyResponse)
async def set_policy_enabled(policy_id: str, body: dict = Body(...), db: Session = Depends(get_session)):
    """Instant kill-switch — bypasses draft/publish entirely."""
    if "enabled" not in body:
        raise HTTPException(status_code=422, detail="enabled is required")
    try:
        repo = PolicyRepository(db)
        policy = repo.set_enabled(UUID(policy_id), bool(body["enabled"]))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return PolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")


@router.get("/policies/{policy_id}/versions")
async def list_policy_versions(policy_id: str, db: Session = Depends(get_session)):
    try:
        repo = PolicyRepository(db)
        if not repo.get(UUID(policy_id)):
            raise HTTPException(status_code=404, detail="Policy not found")
        versions = repo.list_versions(UUID(policy_id))
        return [
            {
                "version": v.version,
                "created_at": v.created_at.isoformat(),
                "created_by": v.created_by,
                "change_note": v.change_note,
                "snapshot": v.snapshot,
            }
            for v in versions
        ]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")


@router.post("/policies/{policy_id}/versions/{version}/restore", response_model=PolicyResponse)
async def restore_policy_version(policy_id: str, version: int, db: Session = Depends(get_session)):
    """Load a historical version into draft_snapshot for review — does not
    publish it directly. A separate POST .../publish call is required."""
    try:
        repo = PolicyRepository(db)
        policy = repo.restore_version_to_draft(UUID(policy_id), version)
        if not policy:
            raise HTTPException(status_code=404, detail="Policy or version not found")
        return PolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    db: Session = Depends(get_session),
):
    """Delete a policy"""
    try:
        repo = PolicyRepository(db)
        success = repo.delete(UUID(policy_id))

        if not success:
            raise HTTPException(status_code=404, detail="Policy not found")

        return {"message": "Policy deleted successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
