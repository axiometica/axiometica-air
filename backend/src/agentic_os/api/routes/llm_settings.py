"""
LLM Settings API Routes
Configure LLM providers and manage incident summaries
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import asyncio
import logging

from agentic_os.services.summary_service import (
    get_summary_service,
    set_summary_service_config,
    get_insights_enabled,
    set_insights_enabled,
)
from agentic_os.services.batch_summary_migration import get_batch_migration_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["llm-settings"])


class LLMConfig(BaseModel):
    """LLM provider configuration"""
    provider: str  # "openai", "anthropic", "custom"
    api_key: Optional[str] = None   # required for openai/anthropic; optional for custom endpoints
    base_url: Optional[str] = None  # required for custom; ignored for cloud providers
    model: Optional[str] = None
    insights_enabled: Optional[bool] = True


class LLMStatus(BaseModel):
    """LLM provider status"""
    provider: str
    model: Optional[str]
    configured: bool
    cached_summaries: int
    insights_enabled: bool


class InsightsToggle(BaseModel):
    enabled: bool


@router.get("/status", response_model=LLMStatus)
async def get_llm_status():
    """Get current LLM provider status"""
    service = get_summary_service()
    info = service.get_provider_info()

    return LLMStatus(
        provider=info["provider"],
        model=info["model"],
        configured=info["configured"],
        cached_summaries=info["cached_summaries"],
        insights_enabled=get_insights_enabled(),
    )


@router.post("/config")
async def set_llm_config(config: LLMConfig):
    """Configure LLM provider"""
    import logging
    logger = logging.getLogger(__name__)

    try:
        # Debug: Check what was received
        key_preview = config.api_key[:10] + "..." if config.api_key else "EMPTY"
        logger.info(f"LLM Config received: provider={config.provider}, api_key={key_preview}, model={config.model}")

        # Validate provider name
        if config.provider.lower() not in ["openai", "anthropic", "custom"]:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider: {config.provider}. Supported: openai, anthropic, custom"
            )

        # custom provider requires a base_url; cloud providers require an api_key
        if config.provider.lower() == "custom":
            if not config.base_url:
                raise HTTPException(status_code=400, detail="base_url is required for the custom provider")
        else:
            if not config.api_key:
                raise HTTPException(status_code=400, detail="api_key is required for cloud providers")

        # Update configuration
        logger.info(f"Calling set_summary_service_config with api_key={key_preview}")
        service = set_summary_service_config(
            provider_name=config.provider.lower(),
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            insights_enabled=config.insights_enabled if config.insights_enabled is not None else True,
        )
        logger.info(f"Service configured: is_configured={service.is_provider_configured()}")

        # Test configuration
        if not service.is_provider_configured():
            raise HTTPException(
                status_code=400,
                detail=f"Provider {config.provider} is not properly configured"
            )

        return {
            "status": "success",
            "message": f"LLM provider configured: {config.provider}",
            "provider": config.provider,
            "model": config.model or "default",
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class LLMTestRequest(BaseModel):
    """Optional credentials for test-before-save flow"""
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


@router.post("/test")
async def test_llm_config(body: LLMTestRequest = LLMTestRequest()):
    """
    Test LLM provider configuration.

    If provider/api_key are supplied in the request body, they are tested
    directly WITHOUT being saved — this supports the Test-before-Save UX
    so users can validate credentials before committing them to the database.

    If no body is supplied, the currently saved/in-memory config is tested.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        if body.api_key or body.base_url:
            # Test-before-save: use the supplied credentials without persisting
            from agentic_os.services.summary_service import SummaryService
            service = SummaryService(
                provider_name=body.provider or "openai",
                api_key=body.api_key,
                base_url=body.base_url,
                model=body.model,
            )
            key_hint = (body.api_key[:10] + "...") if body.api_key else body.base_url
            logger.info(f"Testing supplied credentials: provider={body.provider}, credential={key_hint}")
        else:
            # Test currently saved config
            service = get_summary_service()
            logger.info(f"Testing saved config: provider={service.provider_name}")

        if not service.is_provider_configured():
            raise HTTPException(status_code=400, detail="LLM provider not configured — supply an api_key")

        test_summary = await service.generate_summary_async(
            incident_id="test-123",
            event_type="CPU Spike",
            resource_name="test-node",
            severity="high",
            impact_description="Test incident for configuration validation",
            classification_reasoning="Testing LLM provider",
        )

        if test_summary is None:
            raise HTTPException(
                status_code=400,
                detail="LLM provider returned no output. Check your API key, model name, and token/quota limits.",
            )

        logger.info(f"LLM test passed: {test_summary[:50]}...")
        return {
            "status": "success",
            "message": "LLM provider is working correctly",
            "test_summary": test_summary[:100] + "..." if len(test_summary) > 100 else test_summary,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM test failed: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"LLM test failed: {type(e).__name__}: {str(e)}")


@router.patch("/insights")
async def toggle_insights(body: InsightsToggle):
    """Enable or disable AI insights generation without touching LLM credentials."""
    set_insights_enabled(body.enabled)
    return {"status": "success", "insights_enabled": body.enabled}


@router.post("/clear-cache")
async def clear_summary_cache():
    """Clear all cached summaries"""
    from agentic_os.services.summary_service import SummaryService

    SummaryService.clear_cache()

    return {
        "status": "success",
        "message": "Summary cache cleared",
    }


@router.get("/providers")
async def get_supported_providers():
    """Get list of supported LLM providers"""
    return {
        "providers": [
            {
                "name": "openai",
                "models": ["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"],
                "default_model": "gpt-4o",
                "description": "OpenAI GPT models",
                "requires_api_key": True,
            },
            {
                "name": "anthropic",
                "models": [
                    "claude-opus-4-8",
                    "claude-sonnet-4-6",
                    "claude-haiku-4-5-20251001",
                    "claude-3-5-sonnet-20241022",
                    "claude-3-haiku-20240307",
                ],
                "default_model": "claude-haiku-4-5-20251001",
                "description": "Anthropic Claude models",
                "requires_api_key": True,
            },
            {
                "name": "custom",
                "models": ["llama3", "llama3.1", "llama3.2", "mistral", "mixtral", "qwen2.5", "phi3", "gemma2", "deepseek-r1", "gpt-4o", "claude-3-haiku-20240307"],
                "default_model": "",
                "description": "Any OpenAI-compatible endpoint",
                "requires_api_key": False,
            },
        ]
    }


@router.post("/migrate-old-incidents")
async def migrate_old_incidents(background_tasks: BackgroundTasks, limit: int = None):
    """
    Trigger batch migration to generate summaries for old incidents.
    Runs in background - uses platform context as default, LLM if available.
    Only processes incidents with NULL summaries.
    """
    def run_migration():
        """Run migration in thread pool"""
        try:
            migration_service = get_batch_migration_service()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                stats = loop.run_until_complete(migration_service.migrate_old_incidents(limit=limit))
                logger.info(f"Batch migration completed: {stats}")
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Batch migration failed: {e}", exc_info=True)

    background_tasks.add_task(run_migration)

    return {
        "status": "migration_started",
        "message": "Batch summary migration started in background",
        "details": "Processing incidents without summaries. Defaults to platform context.",
    }
