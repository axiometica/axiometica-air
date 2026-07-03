"""
Governance Policy Management Endpoints

APIs for creating, retrieving, updating, and deleting governance policies.
Governance policies control when remediation actions require approval.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

from agentic_os.db.database import get_session
from agentic_os.db.repositories import GovernancePolicyRepository

router = APIRouter()


# Request/Response models
class GovernancePolicySubmit(BaseModel):
    """Create or update a governance policy"""
    name: str
    description: Optional[str] = None
    conditions: dict = {}  # { "environment": "prod", "service_name": "database", "min_risk_score": 75 }
    actions_requiring_approval: list = []  # ["restart_service", "scale_pods", "*"]
    approval_groups: list = []  # ["dba-team", "on-call"]
    enabled: Optional[bool] = True


class GovernancePolicyResponse(BaseModel):
    """Governance policy response"""
    policy_id: str
    name: str
    description: Optional[str]
    conditions: dict
    actions_requiring_approval: list
    approval_groups: list
    enabled: bool
    created_at: str
    updated_at: str


# Endpoints
@router.get("/governance-policies", response_model=list[GovernancePolicyResponse])
async def list_governance_policies(
    enabled_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    """List governance policies"""
    try:
        repo = GovernancePolicyRepository(db)
        policies = repo.list_all(enabled_only=enabled_only)

        # Apply offset and limit
        policies = policies[offset : offset + limit]

        return [GovernancePolicyResponse(**repo.to_dict(p)) for p in policies]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/governance-policies/{policy_id}", response_model=GovernancePolicyResponse)
async def get_governance_policy(
    policy_id: str,
    db: Session = Depends(get_session),
):
    """Get governance policy by ID"""
    try:
        repo = GovernancePolicyRepository(db)
        policy = repo.get(UUID(policy_id))

        if not policy:
            raise HTTPException(status_code=404, detail="Governance policy not found")

        return GovernancePolicyResponse(**repo.to_dict(policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/governance-policies", response_model=GovernancePolicyResponse, status_code=201)
async def create_governance_policy(
    policy: GovernancePolicySubmit,
    db: Session = Depends(get_session),
):
    """Create a new governance policy"""
    try:
        repo = GovernancePolicyRepository(db)
        new_policy = repo.create(
            name=policy.name,
            conditions=policy.conditions,
            actions_requiring_approval=policy.actions_requiring_approval,
            approval_groups=policy.approval_groups,
            description=policy.description,
        )

        return GovernancePolicyResponse(**repo.to_dict(new_policy))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/governance-policies/{policy_id}", response_model=GovernancePolicyResponse)
async def update_governance_policy(
    policy_id: str,
    policy: GovernancePolicySubmit,
    db: Session = Depends(get_session),
):
    """Update a governance policy"""
    try:
        repo = GovernancePolicyRepository(db)
        updated_policy = repo.update(
            UUID(policy_id),
            name=policy.name,
            conditions=policy.conditions,
            actions_requiring_approval=policy.actions_requiring_approval,
            approval_groups=policy.approval_groups,
            description=policy.description,
            enabled=policy.enabled,
        )

        if not updated_policy:
            raise HTTPException(status_code=404, detail="Governance policy not found")

        return GovernancePolicyResponse(**repo.to_dict(updated_policy))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/governance-policies/{policy_id}")
async def delete_governance_policy(
    policy_id: str,
    db: Session = Depends(get_session),
):
    """Delete a governance policy"""
    try:
        repo = GovernancePolicyRepository(db)
        success = repo.delete(UUID(policy_id))

        if not success:
            raise HTTPException(status_code=404, detail="Governance policy not found")

        return {"message": "Governance policy deleted successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
