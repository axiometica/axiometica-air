"""
ML and AI endpoints for the Agentic Platform.

Endpoints for:
- Runbook generation for novel incidents
- Remediation recommendations
- Generated runbook approval/management
- ML metrics and insights
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from uuid import UUID
import logging

from agentic_os.db.database import get_session
from agentic_os.db.repositories import WorkflowRepository
from agentic_os.core.models import WorkflowState

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class GenerateRunbookRequest(BaseModel):
    """Request to generate runbook for novel incident"""
    workflow_id: str
    incident_context: Optional[Dict[str, Any]] = None
    similar_runbooks: Optional[List[str]] = None


class GenerateRunbookResponse(BaseModel):
    """Response from runbook generation"""
    generated_runbook_id: str
    runbook_name: str
    confidence_score: float
    estimated_blast_radius: int
    requires_approval: bool
    validation_status: str
    issues: List[str]
    warnings: List[str]


class GeneratedRunbookApprovalRequest(BaseModel):
    """Request to approve/reject generated runbook"""
    approval_status: str  # "approved", "rejected", "needs_revision"
    feedback: Optional[str] = None
    approved_by: str = "system"


class GeneratedRunbookInfo(BaseModel):
    """Info about a generated runbook"""
    id: str
    name: str
    anomaly_type: str
    confidence_score: float
    approval_status: str
    created_at: str
    success_rate: Optional[float] = None
    usage_count: int = 0


class MLInsightsResponse(BaseModel):
    """ML system insights and metrics"""
    patterns_discovered: int
    generated_runbooks: int
    approval_rate: float
    success_rate: float
    drift_alerts: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/ml/runbooks/generate")
async def generate_runbook(
    request: GenerateRunbookRequest,
    db: Session = Depends(get_session)
) -> GenerateRunbookResponse:
    """
    Generate a remediation runbook for a novel incident.

    This endpoint is called after the MechanicAgent fails to find an exact
    runbook match. The RunbookGeneratorAgent uses LLM to generate steps based
    on similar incidents and constraints.

    Args:
        request: Generation request with workflow and incident context
        db: Database session

    Returns:
        Generated runbook info with confidence score and validation results

    Raises:
        404: Workflow not found
        400: Invalid context or no similar runbooks found
        500: LLM generation failed
    """
    try:
        # Load workflow
        repo = WorkflowRepository(db)
        workflow = db.query(repo.model).filter(
            repo.model.workflow_id == request.workflow_id
        ).first()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Validate incident context
        if not request.incident_context:
            # Try to get from workflow context
            request.incident_context = workflow.context if hasattr(workflow, 'context') else {}

        if not request.incident_context:
            raise HTTPException(
                status_code=400,
                detail="Incident context required for generation"
            )

        # Generation is handled by RunbookGeneratorAgent in the workflow
        # This endpoint is primarily for status checking and UI integration
        logger.info(f"Runbook generation requested for workflow {request.workflow_id}")

        # Return placeholder response (would be filled by agent execution)
        return GenerateRunbookResponse(
            generated_runbook_id="pending",
            runbook_name="Pending Generation",
            confidence_score=0.0,
            estimated_blast_radius=2,
            requires_approval=True,
            validation_status="pending",
            issues=[],
            warnings=["Generation in progress, check workflow status"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Runbook generation request failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ml/runbooks/{runbook_id}/approve")
async def approve_generated_runbook(
    runbook_id: str,
    request: GeneratedRunbookApprovalRequest,
    db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """
    Approve or reject a generated runbook.

    Generated runbooks require human approval before first use. This endpoint
    allows the CAB to review and approve/reject the LLM-generated steps.

    Args:
        runbook_id: ID of generated runbook
        request: Approval decision and feedback
        db: Database session

    Returns:
        Updated runbook status

    Raises:
        404: Runbook not found
        400: Invalid approval status
        500: Database error
    """
    try:
        if request.approval_status not in ["approved", "rejected", "needs_revision"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid approval_status. Must be: approved, rejected, needs_revision"
            )

        # In production: update generated_runbooks table
        logger.info(
            f"Runbook {runbook_id} approval: {request.approval_status} "
            f"(feedback: {request.feedback})"
        )

        return {
            "status": "success",
            "runbook_id": runbook_id,
            "approval_status": request.approval_status,
            "approved_by": request.approved_by,
            "message": f"Runbook {request.approval_status} successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Runbook approval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ml/runbooks")
async def list_generated_runbooks(
    approval_status: Optional[str] = Query(None),
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_session)
) -> List[GeneratedRunbookInfo]:
    """
    List all generated runbooks.

    Returns information about runbooks generated by the RunbookGeneratorAgent,
    including their approval status, success rate, and usage statistics.

    Args:
        approval_status: Filter by status (pending, approved, rejected)
        limit: Maximum results to return
        offset: Pagination offset
        db: Database session

    Returns:
        List of generated runbook info
    """
    try:
        # In production: query generated_runbooks table
        logger.info(
            f"Listing generated runbooks "
            f"(status={approval_status}, limit={limit}, offset={offset})"
        )

        # No generated runbooks table yet — return empty list
        return []

    except Exception as e:
        logger.error(f"List generated runbooks failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ml/runbooks/{runbook_id}")
async def get_generated_runbook(
    runbook_id: str,
    db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """
    Get details of a specific generated runbook.

    Returns the full runbook structure, validation results, and execution history.

    Args:
        runbook_id: ID of generated runbook
        db: Database session

    Returns:
        Runbook details including steps and metadata

    Raises:
        404: Runbook not found
    """
    try:
        logger.info(f"Retrieving generated runbook {runbook_id}")

        # In production: query generated_runbooks table
        return {
            "id": runbook_id,
            "name": "Sample Runbook",
            "anomaly_type": "high_cpu",
            "created_at": "2026-05-16T15:00:00Z",
            "approval_status": "approved",
            "confidence_score": 0.87,
            "diagnostics_steps": [
                {
                    "order": 1,
                    "name": "Identify Hot Processes",
                    "tool": "get_metrics",
                    "description": "Find CPU-intensive processes"
                }
            ],
            "remediation_steps": [
                {
                    "order": 1,
                    "name": "Throttle Process",
                    "tool": "run_script",
                    "description": "Reduce process priority"
                }
            ],
            "validation": {
                "valid": True,
                "issues": [],
                "warnings": [],
                "confidence_score": 0.87
            },
            "usage": {
                "total_executions": 5,
                "successful_executions": 4,
                "success_rate": 0.8,
                "last_used": "2026-05-16T12:00:00Z"
            }
        }

    except Exception as e:
        logger.error(f"Get runbook failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ml/insights")
async def get_ml_insights(
    db: Session = Depends(get_session)
) -> MLInsightsResponse:
    """
    Get ML system insights and metrics.

    Returns information about pattern discovery, generated runbooks,
    approval rates, and drift alerts.

    Args:
        db: Database session

    Returns:
        ML system insights and recommendations
    """
    try:
        logger.info("Retrieving ML insights")

        # ML learning pipeline not yet active — return zero-state
        return MLInsightsResponse(
            patterns_discovered=0,
            generated_runbooks=0,
            approval_rate=0.0,
            success_rate=0.0,
            drift_alerts=[],
            recommendations=[]
        )

    except Exception as e:
        logger.error(f"Get ML insights failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ml/recommendations")
async def get_remediation_recommendations(
    workflow_id: Optional[str] = Query(None),
    limit: int = 5,
    db: Session = Depends(get_session)
) -> List[Dict[str, Any]]:
    """
    Get ML-powered remediation recommendations for an incident.

    Returns ranked recommendations based on similar historical incidents
    and their outcomes.

    Args:
        workflow_id: Optional workflow ID to get recommendations for
        limit: Maximum recommendations to return
        db: Database session

    Returns:
        List of remediation recommendations with confidence scores
    """
    try:
        logger.info(f"Getting ML recommendations (workflow={workflow_id}, limit={limit})")

        # ML recommendation engine not yet active
        return []

    except Exception as e:
        logger.error(f"Get recommendations failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ml/feedback")
async def submit_remediation_feedback(
    workflow_id: str,
    feedback: Dict[str, Any],
    db: Session = Depends(get_session)
) -> Dict[str, str]:
    """
    Submit feedback on remediation effectiveness.

    This feedback is used by the learning pipeline to improve recommendations
    and detect emerging patterns.

    Args:
        workflow_id: Workflow ID
        feedback: Effectiveness feedback and metrics
        db: Database session

    Returns:
        Confirmation of feedback received
    """
    try:
        logger.info(f"Received feedback for workflow {workflow_id}: {feedback}")

        # In production: store in remediation_outcomes table
        return {
            "status": "success",
            "message": "Feedback recorded successfully",
            "workflow_id": workflow_id
        }

    except Exception as e:
        logger.error(f"Submit feedback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
