"""
Summary Service - Generates incident summaries asynchronously
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

from .llm_provider import get_llm_provider, LLMProvider

logger = logging.getLogger(__name__)


class SummaryService:
    """Service for generating and caching incident summaries"""

    # In-memory cache for summaries (in production, use Redis or database)
    _cache = {}

    def __init__(self, provider_name: str = "openai", api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None):
        """Initialize summary service with LLM provider"""
        self.provider: LLMProvider = get_llm_provider(provider_name, api_key, model, base_url)
        self.provider_name = provider_name
        self.model = model

    def get_cached_summary(self, incident_id: str) -> Optional[str]:
        """Get cached summary for incident"""
        return self._cache.get(incident_id)

    def cache_summary(self, incident_id: str, summary: str) -> None:
        """Cache a summary"""
        self._cache[incident_id] = summary
        logger.info(f"Cached summary for incident {incident_id}")

    async def generate_summary_async(
        self,
        incident_id: str,
        event_type: str,
        resource_name: str,
        severity: str,
        impact_description: str = "",
        classification_reasoning: str = "",
    ) -> str:
        """
        Generate a summary asynchronously.
        Returns cached summary if available, otherwise generates new one.
        """
        # Check cache first
        cached = self.get_cached_summary(incident_id)
        if cached:
            return cached

        try:
            # Prepare incident data for LLM
            incident_data = {
                "event_type": event_type,
                "resource": resource_name,
                "severity": severity,
                "impact": impact_description or f"Severity: {severity}",
                "classification": classification_reasoning or event_type,
            }

            # Generate summary (async)
            summary = await self.provider.generate_summary(incident_data)

            if summary is None:
                logger.warning(f"LLM provider returned None for incident {incident_id} — likely token/quota error")
                return None

            # Cache the result
            self.cache_summary(incident_id, summary)

            logger.info(f"Generated summary for incident {incident_id}: {summary[:100]}...")
            return summary

        except Exception as e:
            logger.error(f"Error generating summary for incident {incident_id}: {str(e)}")
            # Return None so the caller (Celery task) can fall back to PlatformContextService
            # with full post-agent context rather than a useless short string.
            return None

    async def generate_rich_summary_async(
        self,
        incident_id: str,
        full_context: dict,
    ) -> dict:
        """
        Generate a rich two-section summary using full post-agent context.

        full_context keys (all optional, use whatever is available):
          event_type, resource, environment, severity, risk_score, blast_radius,
          remediation_complexity, anomaly_process, anomaly_metrics, runbook,
          actions_taken, execution_results, verification, lifecycle_state,
          dependencies, impacted_services, description

        Returns: {"summary": "<narrative>", "technical_summary": "<bullets>"}
          Either value may be None if the LLM call failed.
        """
        if not self.is_provider_configured():
            logger.debug(f"LLM provider not configured — skipping rich summary for {incident_id}")
            return {"summary": None, "technical_summary": None}

        try:
            result = await self.provider.generate_rich_summary(full_context)
            # Cache the executive summary (backward compat — cache keyed by incident_id)
            if result.get("summary"):
                self.cache_summary(incident_id, result["summary"])
            logger.info(
                f"Rich summary generated for {incident_id}: "
                f"summary={len(result.get('summary') or '')} chars, "
                f"technical={len(result.get('technical_summary') or '')} chars"
            )
            return result
        except Exception as e:
            logger.error(f"Error generating rich summary for {incident_id}: {e}", exc_info=True)
            return {"summary": None, "technical_summary": None}

    async def generate_summary_background(
        self,
        incident_id: str,
        event_type: str,
        resource_name: str,
        severity: str,
        impact_description: str = "",
        classification_reasoning: str = "",
        callback=None,
    ) -> None:
        """
        Generate summary in background without blocking.
        Optionally call callback when complete.
        """
        try:
            summary = await self.generate_summary_async(
                incident_id=incident_id,
                event_type=event_type,
                resource_name=resource_name,
                severity=severity,
                impact_description=impact_description,
                classification_reasoning=classification_reasoning,
            )

            # If callback provided, call it with the summary
            if callback:
                if asyncio.iscoroutinefunction(callback):
                    await callback(incident_id, summary)
                else:
                    callback(incident_id, summary)

        except Exception as e:
            logger.error(f"Background summary generation failed for {incident_id}: {str(e)}")

    def is_provider_configured(self) -> bool:
        """Check if LLM provider is properly configured"""
        return self.provider.is_configured()

    def get_provider_info(self) -> dict:
        """Get info about current provider"""
        return {
            "provider": self.provider_name,
            "model": self.model,
            "configured": self.is_provider_configured(),
            "cached_summaries": len(self._cache),
        }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear summary cache (useful for testing)"""
        cls._cache.clear()


# Singleton instance
_summary_service: Optional[SummaryService] = None
_insights_enabled: bool = True


def get_insights_enabled() -> bool:
    """Return whether AI insights generation is currently enabled.

    Always reads from DB so the toggle works across processes (backend vs. Celery worker).
    Falls back to the in-memory flag if the DB is unreachable.
    """
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.llm_config_repository import LLMConfigRepository
        db = SessionLocal()
        try:
            config = LLMConfigRepository(db).get_config("default")
            if config is not None:
                return bool(config.get("insights_enabled", True))
        finally:
            db.close()
    except Exception:
        pass
    return _insights_enabled


def set_insights_enabled(enabled: bool) -> None:
    """Toggle AI insights in-memory and persist to DB."""
    global _insights_enabled
    _insights_enabled = enabled
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.llm_config_repository import LLMConfigRepository
        db = SessionLocal()
        try:
            repo = LLMConfigRepository(db)
            repo.update_insights_enabled(enabled)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to persist insights_enabled={enabled}: {e}")


def get_summary_service(
    provider_name: str = "openai",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> SummaryService:
    """Get or create summary service instance"""
    global _summary_service, _insights_enabled

    if _summary_service is None:
        # Try to load from database first
        try:
            from agentic_os.db.database import SessionLocal
            from agentic_os.db.llm_config_repository import LLMConfigRepository

            db = SessionLocal()
            try:
                repo = LLMConfigRepository(db)
                db_config = repo.get_config("default")
                if db_config:
                    logger.info(f"Loaded LLM config from database: {db_config['provider']}")
                    _insights_enabled = db_config.get("insights_enabled", True)
                    _summary_service = SummaryService(
                        provider_name=db_config.get("provider", provider_name),
                        api_key=db_config.get("api_key", api_key),
                        model=db_config.get("model", model),
                        base_url=db_config.get("base_url"),
                    )
                else:
                    _summary_service = SummaryService(provider_name, api_key, model)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not load LLM config from database: {e}, using defaults")
            _summary_service = SummaryService(provider_name, api_key, model)

    return _summary_service


def set_summary_service_config(
    provider_name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    insights_enabled: bool = True,
    base_url: Optional[str] = None,
) -> SummaryService:
    """Update summary service configuration and save to database"""
    global _summary_service, _insights_enabled

    _summary_service = SummaryService(provider_name, api_key, model, base_url)
    _insights_enabled = insights_enabled

    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.llm_config_repository import LLMConfigRepository

        db = SessionLocal()
        try:
            repo = LLMConfigRepository(db)
            repo.save_config(provider_name, api_key, model, "default", insights_enabled=insights_enabled, base_url=base_url)
            logger.info(f"LLM config saved to database: {provider_name}, insights_enabled={insights_enabled}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to save LLM config to database: {e}")

    return _summary_service
