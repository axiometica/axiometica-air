"""
Enumeration service for reading and managing incident/storm human IDs.

As of v1.1.0, INC and STRM numbers are assigned automatically by PostgreSQL
triggers (assign_workflow_human_id) at INSERT/UPDATE time.  This service
now reads those values back rather than generating them, with a manual
nextval() fallback for environments that haven't run the migration yet.
"""

import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class EnumerationService:
    """
    Read-back service for DB-trigger-assigned incident and storm numbers.

    Trigger: assign_workflow_human_id() fires BEFORE INSERT on workflow_states
             and BEFORE UPDATE OF is_storm_parent on workflow_states.

    INC format:  INC0001, INC0002, … (incident_seq)
    STRM format: STRM0001, STRM0002, … (storm_seq)
    """

    @staticmethod
    def generate_incident_number(db: Session, workflow_id: str) -> str:
        """
        Return the INC number for this workflow, assigned by the DB trigger.

        The trigger fires BEFORE INSERT so the number is already set by the
        time Python reads the row back.  Falls back to manual nextval() for
        deployments that haven't applied migration v1_1_0_schema_foundations.

        Args:
            db:          SQLAlchemy session
            workflow_id: UUID of the incident workflow

        Returns:
            Formatted incident number string: "INC0001", "INC0002", etc.
        """
        try:
            row = db.execute(text(
                "SELECT incident_number, incident_number_str "
                "FROM workflow_states WHERE workflow_id = :id"
            ), {"id": str(workflow_id)}).fetchone()

            # Trigger already assigned — just return it (most common path)
            if row and row.incident_number_str:
                return row.incident_number_str

            # ── Fallback: trigger not present (pre-migration environment) ────
            logger.warning(
                "[ENUM] Trigger did not assign INC number for %s — "
                "falling back to manual nextval (run v1_1_0 migration)", workflow_id
            )
            result = db.execute(text("SELECT nextval('incident_seq')"))
            incident_num = result.scalar()

            if incident_num is None:
                raise RuntimeError("incident_seq returned None")

            incident_str = f"INC{incident_num:04d}"

            db.execute(text("""
                UPDATE workflow_states
                SET incident_number     = :num,
                    incident_number_str = :str
                WHERE workflow_id = :id
            """), {"num": int(incident_num), "str": incident_str, "id": str(workflow_id)})
            db.commit()

            logger.info("[ENUM] Fallback assigned %s to workflow %s", incident_str, workflow_id)
            return incident_str

        except Exception as exc:
            logger.error("[ENUM] Error in generate_incident_number: %s", exc, exc_info=True)
            raise

    @staticmethod
    def get_incident_number_str(db: Session, workflow_id: str) -> str:
        """
        Return the incident_number_str for a workflow, or "" if not set.

        Args:
            db:          SQLAlchemy session
            workflow_id: UUID of the workflow

        Returns:
            Incident number string (e.g. "INC0001") or empty string.
        """
        try:
            result = db.execute(text(
                "SELECT incident_number_str FROM workflow_states WHERE workflow_id = :id"
            ), {"id": str(workflow_id)}).scalar()
            return result or ""
        except Exception as exc:
            logger.error("[ENUM] Error fetching incident_number_str for %s: %s", workflow_id, exc)
            return ""

    @staticmethod
    def get_storm_number_str(db: Session, workflow_id: str) -> str:
        """
        Return the storm_number_str for a storm-parent workflow, or "" if not set.

        Args:
            db:          SQLAlchemy session
            workflow_id: UUID of the storm parent workflow

        Returns:
            Storm number string (e.g. "STRM0001") or empty string.
        """
        try:
            result = db.execute(text(
                "SELECT storm_number_str FROM workflow_states WHERE workflow_id = :id"
            ), {"id": str(workflow_id)}).scalar()
            return result or ""
        except Exception as exc:
            logger.error("[ENUM] Error fetching storm_number_str for %s: %s", workflow_id, exc)
            return ""

    @staticmethod
    def get_next_incident_number(db: Session) -> int:
        """
        Peek at the next incident number without consuming the sequence.
        Useful for UI "next incident will be INC0042" display.

        Returns:
            Next incident number as integer (1-based).
        """
        try:
            result = db.execute(text("SELECT last_value FROM incident_seq")).scalar()
            return int(result) + 1 if result else 1
        except Exception as exc:
            logger.error("[ENUM] Error peeking next incident number: %s", exc)
            return 1
