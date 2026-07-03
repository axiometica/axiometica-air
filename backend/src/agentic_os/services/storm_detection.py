"""
Storm Detection Service

Detects correlated event storms: a burst of incidents across multiple resources
in a short time window that suggests a shared root cause (e.g., a network partition
causing multiple service health checks to fail simultaneously).

Detection criteria (tunable via platform settings or environment variables):
    storm.enabled         — enable/disable detection entirely (default: true)
    storm.window_seconds  — look-back window (default: 120 s / 2 minutes)
    storm.min_incidents   — minimum qualifying incidents in window (default: 3)
    storm.min_resources   — minimum distinct resources affected (default: 2)

Settings are read from the platform_settings table on every detect() call.
Environment variables are used as fallback when the DB row is absent.

An incident is excluded from storm consideration if:
    - It is already resolved or closed
    - It already belongs to a storm (storm_id IS NOT NULL)
    - It is a storm parent itself (context.is_storm_parent = true)

Usage:
    from agentic_os.services.storm_detection import get_storm_detection_service

    svc = get_storm_detection_service()
    candidate = svc.detect(db)
    if candidate:
        # Storm detected — hand off to execute_storm_analysis_task
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Env-var fallback defaults ──────────────────────────────────────────────────
# These are used when the platform_settings table hasn't been seeded yet.
_ENV_WINDOW_SECONDS: int = int(os.getenv("STORM_WINDOW_SECONDS", "300"))
_ENV_MIN_INCIDENTS:  int = int(os.getenv("STORM_MIN_INCIDENTS",  "3"))
_ENV_MIN_RESOURCES:  int = int(os.getenv("STORM_MIN_RESOURCES",  "2"))

# Event type correlation groups.
# If 2+ types from the same group are observed across resources → correlated storm.
CORRELATED_GROUPS: List[set] = [
    # Network / connectivity failures — includes app-tier cascades (high_error_rate
    # from services that can't reach a failed data-tier resource, connection_spike
    # when connection pools exhaust due to upstream failure).
    {"service_unresponsive", "health_check_failed", "high_latency",
     "connection_spike", "network_anomaly", "service_down",
     "high_error_rate"},
    # Resource exhaustion
    {"high_cpu", "high_memory", "disk_full", "high_syscall_intensity"},
    # Generic service cascade
    {"service_down", "service_unresponsive", "health_check_failed"},
]


@dataclass
class StormCandidate:
    """
    Describes a detected storm: the set of incidents that appear correlated.

    Attributes:
        incident_ids    — workflow_id strings for each child incident
        resource_names  — distinct resource names in the cluster
        event_types     — distinct event type strings in the cluster
        earliest_at     — timestamp of the oldest incident in the window
        latest_at       — timestamp of the most recent incident in the window
    """
    incident_ids:   List[str]            = field(default_factory=list)
    resource_names: List[str]            = field(default_factory=list)
    event_types:    List[str]            = field(default_factory=list)
    earliest_at:    Optional[datetime]   = None
    latest_at:      Optional[datetime]   = None


def _types_are_correlated(types: set) -> bool:
    """
    Return True if the observed event types suggest a shared root cause.

    Considers two signals:
      1. Two or more types from a known correlation group appear together.
      2. A single type appears on multiple resources (checked by caller via
         STORM_MIN_RESOURCES, always True here so caller decides).
    """
    for group in CORRELATED_GROUPS:
        if len(types & group) >= 2:
            return True
    # Single event type blasting across multiple resources is also a storm
    # (e.g., service_unresponsive on 4 hosts) — threshold enforced by caller
    return True


def _load_storm_settings(db: Session) -> Dict[str, Any]:
    """
    Read storm detection settings from the platform_settings table.

    Returns a dict with typed values.  Falls back to env-var defaults for any
    key that is not yet in the DB (e.g. before the seed has run).
    """
    defaults: Dict[str, Any] = {
        "enabled":                 True,
        "window_seconds":          _ENV_WINDOW_SECONDS,
        "min_incidents":           _ENV_MIN_INCIDENTS,
        "min_resources":           _ENV_MIN_RESOURCES,
        # When True: incidents from any external connector are excluded from
        # storm detection entirely.  A targeted alternative to per-connector
        # allow_storm_detection; useful when all external sources are batch-sync.
        "exclude_external_events": False,
    }
    try:
        rows = db.execute(sql_text("""
            SELECT key, value, value_type
            FROM   platform_settings
            WHERE  category = 'storm'
              AND  key IN (
                       'storm.enabled',
                       'storm.window_seconds',
                       'storm.min_incidents',
                       'storm.min_resources',
                       'storm.exclude_external_events'
                   )
        """)).fetchall()
        for row in rows:
            raw_key, raw_val, val_type = row[0], row[1], row[2]
            short_key = raw_key.split(".", 1)[1]   # strip "storm." prefix
            if val_type == "int":
                defaults[short_key] = int(raw_val)
            elif val_type == "float":
                defaults[short_key] = float(raw_val)
            elif val_type == "bool":
                defaults[short_key] = raw_val.lower() in ("true", "1", "yes")
            else:
                defaults[short_key] = raw_val
    except Exception as exc:
        logger.debug(f"[STORM DETECT] Could not load settings from DB (using defaults): {exc}")

    return defaults


class StormDetectionService:
    """
    Lightweight service that scans the database for storm conditions.

    The check is intentionally simple and fast: it runs a single SQL query
    and applies in-Python thresholds. It is called as a FastAPI background
    task immediately after every new incident is created, so it must not block.

    Detection thresholds are loaded from the platform_settings table on every
    call so that operator changes take effect without a service restart.
    """

    def detect(
        self,
        db: Session,
        exclude_workflow_id: Optional[str] = None,
    ) -> Optional[StormCandidate]:
        """
        Scan recent incidents for storm conditions.

        Args:
            db: SQLAlchemy session (read-only query).
            exclude_workflow_id: Optional — skip this workflow ID in the scan
                (used to exclude the *current* incident before it is committed).

        Returns:
            StormCandidate if a storm is detected, else None.
        """
        # ── Load settings (live from DB, env-var fallback) ────────────────────
        settings = _load_storm_settings(db)

        if not settings["enabled"]:
            logger.debug("[STORM DETECT] Storm detection disabled via platform settings")
            return None

        window_seconds          = settings["window_seconds"]
        min_incidents           = settings["min_incidents"]
        min_resources           = settings["min_resources"]
        exclude_external_events = settings["exclude_external_events"]

        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)

        try:
            rows = db.execute(sql_text("""
                SELECT
                    workflow_id::text                                    AS wf_id,
                    context -> 'alert_payload' ->> 'type'               AS event_type,
                    context -> 'alert_payload' ->> 'resource_name'      AS resource_name,
                    created_at
                FROM workflow_states
                WHERE workflow_type    = 'incident'
                  AND lifecycle_state NOT IN ('resolved', 'closed', 'storm_hold')
                  AND storm_id        IS NULL
                  AND (context ->> 'is_storm_parent' IS NULL
                       OR (context ->> 'is_storm_parent')::boolean IS DISTINCT FROM true)
                  AND (:exclude_id IS NULL OR workflow_id::text != :exclude_id)

                  -- ── Time-window filter ───────────────────────────────────────
                  -- Use the original alert timestamp (source_alert_time) when
                  -- available, falling back to created_at.  This prevents bulk
                  -- syncs from external connectors (all inserted at the same
                  -- moment but originating hours apart) from clustering into a
                  -- false storm: their source_alert_time values will be spread
                  -- across the original alert period, outside the storm window.
                  AND COALESCE(
                          (context -> 'alert_payload' ->> 'source_alert_time')::timestamp,
                          created_at
                      ) > :cutoff

                  -- ── Per-incident storm eligibility ───────────────────────────
                  -- Incidents from connectors with allow_storm_detection=false
                  -- have storm_eligible=false written into their context at
                  -- ingestion time.  NULL means eligible (default / internal).
                  AND (
                      context -> 'alert_payload' ->> 'storm_eligible' IS NULL
                      OR (context -> 'alert_payload' ->> 'storm_eligible')::boolean IS NOT FALSE
                  )

                  -- ── Global external-source exclusion ────────────────────────
                  -- When storm.exclude_external_events=true, skip ALL incidents
                  -- that came from an external connector (source_connector IS NOT
                  -- NULL).  A blunt instrument — prefer per-connector
                  -- allow_storm_detection for finer control.
                  AND (
                      :exclude_external = false
                      OR context -> 'alert_payload' ->> 'source_connector' IS NULL
                  )

                ORDER BY created_at DESC
            """), {
                "cutoff":           cutoff,
                "exclude_id":       exclude_workflow_id,
                "exclude_external": exclude_external_events,
            }).fetchall()

        except Exception as exc:
            logger.error(f"[STORM DETECT] DB query failed: {exc}", exc_info=True)
            return None

        if not rows:
            logger.debug("[STORM DETECT] No recent incidents in window")
            return None

        incident_ids   = [r[0] for r in rows]
        event_types    = list({r[1] for r in rows if r[1]})
        resource_names = list({r[2] for r in rows if r[2]})
        timestamps     = [r[3] for r in rows if r[3]]

        n_incidents = len(incident_ids)
        n_resources = len(resource_names)
        types_set   = set(event_types)

        logger.debug(
            f"[STORM DETECT] Window scan: {n_incidents} incidents across "
            f"{n_resources} resources, types={types_set} "
            f"(window={window_seconds}s, min_inc={min_incidents}, min_res={min_resources})"
        )

        # ── Threshold checks ──────────────────────────────────────────────────
        if n_incidents < min_incidents:
            logger.debug(
                f"[STORM DETECT] Below incident threshold "
                f"({n_incidents} < {min_incidents})"
            )
            return None

        if n_resources < min_resources:
            logger.debug(
                f"[STORM DETECT] Below resource threshold "
                f"({n_resources} < {min_resources})"
            )
            return None

        if not _types_are_correlated(types_set):
            logger.debug(f"[STORM DETECT] Event types not correlated: {types_set}")
            return None

        logger.info(
            f"[STORM DETECT] Storm detected: {n_incidents} incidents across "
            f"{n_resources} resources, types={types_set}"
        )

        return StormCandidate(
            incident_ids=incident_ids,
            resource_names=resource_names,
            event_types=event_types,
            earliest_at=min(timestamps) if timestamps else None,
            latest_at=max(timestamps) if timestamps else None,
        )


# ── Singleton ──────────────────────────────────────────────────────────────────
_svc: Optional[StormDetectionService] = None


def get_storm_detection_service() -> StormDetectionService:
    """Return the shared StormDetectionService instance."""
    global _svc
    if _svc is None:
        _svc = StormDetectionService()
    return _svc
