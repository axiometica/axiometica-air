"""Batch migration service for generating summaries on old incidents"""

import logging
import asyncio
from typing import Optional
from sqlalchemy import update, and_
from sqlalchemy.orm import Session
from datetime import datetime

from agentic_os.db.database import SessionLocal
from agentic_os.db.models import WorkflowStateModel, WorkflowType
from agentic_os.services.summary_service import get_summary_service

logger = logging.getLogger(__name__)


class BatchSummaryMigration:
    """Service to batch-generate summaries for existing incidents"""

    def __init__(self):
        self.batch_size = 50  # Process 50 at a time
        self.max_retries = 3

    async def migrate_old_incidents(self, limit: Optional[int] = None) -> dict:
        """
        Generate summaries for incidents missing them.

        Returns:
            {
                "total_processed": int,
                "successful": int,
                "failed": int,
                "skipped": int,
            }
        """
        db = SessionLocal()
        try:
            stats = {
                "total_processed": 0,
                "successful": 0,
                "failed": 0,
                "skipped": 0,
            }

            # Find all incidents without summaries
            query = db.query(WorkflowStateModel).filter(
                and_(
                    WorkflowStateModel.workflow_type == WorkflowType.INCIDENT,
                    WorkflowStateModel.summary.is_(None)  # NULL summary
                )
            ).order_by(WorkflowStateModel.created_at.desc())

            if limit:
                query = query.limit(limit)

            total_to_process = query.count()
            logger.info(f"Starting batch migration for {total_to_process} incidents without summaries")

            # Process in batches
            offset = 0
            while offset < total_to_process:
                batch = query.offset(offset).limit(self.batch_size).all()
                if not batch:
                    break

                logger.info(f"Processing batch: {offset}-{offset + len(batch)} of {total_to_process}")

                for incident in batch:
                    try:
                        summary = await self._generate_summary_for_incident(incident)

                        if summary:
                            # Update database
                            db.execute(
                                update(WorkflowStateModel).where(
                                    WorkflowStateModel.workflow_id == incident.workflow_id
                                ).values(
                                    summary=summary,
                                    summary_generated_at=datetime.utcnow()
                                )
                            )
                            db.commit()
                            stats["successful"] += 1
                            logger.debug(f"Generated summary for {incident.workflow_id}")
                        else:
                            stats["failed"] += 1
                            logger.warning(f"Failed to generate summary for {incident.workflow_id}")

                    except Exception as e:
                        stats["failed"] += 1
                        logger.error(f"Error processing incident {incident.workflow_id}: {e}")

                    stats["total_processed"] += 1

                offset += self.batch_size

            logger.info(f"Batch migration complete: {stats}")
            return stats

        finally:
            db.close()

    async def _generate_summary_for_incident(self, incident: WorkflowStateModel) -> Optional[str]:
        """Generate summary for a single incident using platform context first, then LLM"""

        alert_payload = incident.context.get("alert_payload", {})
        event_type = alert_payload.get("type", "Unknown")
        resource_name = alert_payload.get("resource_name", "Unknown")
        severity = alert_payload.get("severity", "Unknown")
        description = alert_payload.get("description", "")

        # Try LLM first if configured
        summary_service = get_summary_service()
        if summary_service.is_provider_configured():
            try:
                logger.debug(f"Attempting LLM summary for {incident.workflow_id}")
                summary = await summary_service.generate_summary_async(
                    incident_id=str(incident.workflow_id),
                    event_type=event_type,
                    resource_name=resource_name,
                    severity=severity,
                    impact_description=description,
                    classification_reasoning="Batch migration summary",
                )

                if summary and not summary.startswith("Summary"):
                    logger.debug(f"LLM summary generated for {incident.workflow_id}")
                    return summary
            except Exception as e:
                logger.debug(f"LLM summary failed for {incident.workflow_id}, falling back to context: {e}")

        # Fallback: Use platform context
        summary = f"{event_type} on {resource_name} (Severity: {severity})"
        if description:
            summary += f" - {description[:100]}"

        logger.debug(f"Platform context summary for {incident.workflow_id}")
        return summary


# Singleton instance
_migration_service: Optional[BatchSummaryMigration] = None


def get_batch_migration_service() -> BatchSummaryMigration:
    """Get batch migration service instance"""
    global _migration_service
    if _migration_service is None:
        _migration_service = BatchSummaryMigration()
    return _migration_service
