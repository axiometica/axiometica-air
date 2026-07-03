"""
Risk Configuration API Routes

Endpoints for managing risk assessment weights and thresholds.
These control how monitoring events are scored and qualified as incidents.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from agentic_os.db.database import get_session
from agentic_os.db.repositories import RiskWeightConfigRepository
from agentic_os.db.risk_weights_seed import DEFAULT_RISK_WEIGHTS
from agentic_os.services.event_qualification import reload_qualification_service

router = APIRouter()


# ========== Pydantic Models ==========

class RiskConfigUpdate(BaseModel):
    """Update risk weight configuration"""
    weights: dict


class RiskConfigResponse(BaseModel):
    """Risk configuration response"""
    config_id: str
    config_key: str
    weights: dict
    created_at: str
    updated_at: str


# ========== Endpoints ==========

@router.get("/risk-config", response_model=RiskConfigResponse)
async def get_risk_config(
    config_key: str = "default",
    db: Session = Depends(get_session),
):
    """
    Get current risk weight configuration.

    Args:
        config_key: Configuration key (default: "default")

    Returns:
        Current risk weights and thresholds
    """
    try:
        repo = RiskWeightConfigRepository(db)
        config = repo.get_by_key(config_key)

        if not config:
            raise HTTPException(status_code=404, detail=f"Config '{config_key}' not found")

        return RiskConfigResponse(
            config_id=str(config.config_id),
            config_key=config.config_key,
            weights=config.weights,
            created_at=config.created_at.isoformat(),
            updated_at=config.updated_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/risk-config/all", response_model=list[RiskConfigResponse])
async def list_risk_configs(
    db: Session = Depends(get_session),
):
    """
    List all risk configurations.

    Returns:
        List of all risk weight configs
    """
    try:
        repo = RiskWeightConfigRepository(db)
        configs = repo.list_all()

        return [
            RiskConfigResponse(
                config_id=str(c.config_id),
                config_key=c.config_key,
                weights=c.weights,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
            )
            for c in configs
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/risk-config", response_model=RiskConfigResponse)
async def update_risk_config(
    config_key: str,
    update: RiskConfigUpdate,
    db: Session = Depends(get_session),
):
    """
    Update risk weight configuration.

    Args:
        config_key: Configuration key to update
        update: New weights configuration

    Returns:
        Updated risk config
    """
    try:
        repo = RiskWeightConfigRepository(db)

        # Create or update config
        config = repo.create_or_update(config_key, update.weights)

        if config_key == "default":
            reload_qualification_service()

        return RiskConfigResponse(
            config_id=str(config.config_id),
            config_key=config.config_key,
            weights=config.weights,
            created_at=config.created_at.isoformat(),
            updated_at=config.updated_at.isoformat(),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/risk-config/reset", response_model=RiskConfigResponse)
async def reset_risk_config(
    config_key: str = "default",
    db: Session = Depends(get_session),
):
    """
    Reset risk configuration to defaults.

    Args:
        config_key: Configuration key to reset (default: "default")

    Returns:
        Reset risk config (with default weights)
    """
    try:
        repo = RiskWeightConfigRepository(db)

        # Use default weights
        default_weights = DEFAULT_RISK_WEIGHTS.get("weights", {})

        # Update config with defaults
        config = repo.create_or_update(config_key, default_weights)

        if config_key == "default":
            reload_qualification_service()

        return RiskConfigResponse(
            config_id=str(config.config_id),
            config_key=config.config_key,
            weights=config.weights,
            created_at=config.created_at.isoformat(),
            updated_at=config.updated_at.isoformat(),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/risk-config")
async def delete_risk_config(
    config_key: str = "default",
    db: Session = Depends(get_session),
):
    """
    Delete a risk configuration (cannot delete 'default').

    Args:
        config_key: Configuration key to delete

    Returns:
        Success message
    """
    try:
        if config_key == "default":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete 'default' configuration"
            )

        repo = RiskWeightConfigRepository(db)
        success = repo.delete(config_key)

        if not success:
            raise HTTPException(status_code=404, detail=f"Config '{config_key}' not found")

        return {
            "message": f"Config '{config_key}' deleted successfully",
            "config_key": config_key,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
