"""
ServiceNow Incident Push — creates and updates SN incidents from platform workflows.

Mapping:
  Platform Workflow → ServiceNow Incident
  ─────────────────────────────────────────────────────────
  title            → short_description
  AI summary       → description
  severity         → impact + urgency + priority  (via SEVERITY_TO_SN)
  lifecycle_state  → state (numeric)
  service/CI name  → cmdb_ci (looked up by name in SN, with fallback variants)
  AI summary +
  root cause +
  remediation +
  agent trace      → work_notes

Created/updated SN sys_id is stored in SNowIncidentMapModel for
idempotent updates.
"""

from __future__ import annotations
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

# Strip emoji / non-ASCII pictographic characters from text coming from
# external sources (agent outputs, trace steps, etc.) so ServiceNow work
# notes remain plain-text friendly.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FFFF"   # supplemental planes (most emoji)
    "\U00002300-\U000023FF"   # Miscellaneous Technical (⚙ etc.)
    "\U00002600-\U000026FF"   # Miscellaneous Symbols
    "\U00002700-\U000027BF"   # Dingbats (✅ ✓ ✗ etc.)
    "\U0000FE00-\U0000FE0F"   # Variation Selectors
    "]+",
    flags=re.UNICODE,
)

def _strip_emoji(text: str) -> str:
    """Remove emoji / pictographic characters from a string."""
    return _EMOJI_RE.sub("", text).strip()

from agentic_os.connectors.servicenow.client import ServiceNowClient, ServiceNowError
from agentic_os.connectors.servicenow.field_maps import (
    SEVERITY_TO_SN,
    LIFECYCLE_TO_SN_STATE,
    get_incident_sync_config,
)
from agentic_os.db.models import SNowIncidentMapModel, ConnectorConfigModel

logger = logging.getLogger(__name__)

SN_INCIDENT_TABLE = "incident"

# Neo4j connection — shared credentials with the rest of the platform
_NEO4J_URI  = os.getenv("NEO4J_URI", os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687"))
_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
_NEO4J_PASS = os.getenv("NEO4J_PASSWORD")
_NEO4J_DRIVER = None  # per-worker lazy singleton; recreated if connection drops


def _neo4j_ci_lookup(name: str) -> Optional[str]:
    """
    Return the ServiceNow sys_id stored on a Neo4j CI node, or None.
    Neo4j is the source of truth for CMDB — if the CI isn't there, it
    isn't in SNOW either (cmdb_sync writes sn_sys_id when it imports from SN).
    Uses a per-worker driver singleton to avoid reconnecting on every call.
    """
    global _NEO4J_DRIVER
    try:
        from neo4j import GraphDatabase
        if _NEO4J_DRIVER is None:
            _NEO4J_DRIVER = GraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASS))
        with _NEO4J_DRIVER.session() as session:
            record = session.run(
                "MATCH (ci:ConfigurationItem {name: $name}) RETURN ci.sn_sys_id AS sn_sys_id",
                {"name": name},
            ).single()
            return record["sn_sys_id"] if record else None
    except Exception as exc:
        logger.warning(f"Neo4j CI lookup failed for '{name}': {exc}")
        _NEO4J_DRIVER = None  # force reconnect next call
        return None


class IncidentPush:
    """Handles push of platform incidents to ServiceNow."""

    # Per-worker process cache. Neo4j lookups are fast (indexed), but caching avoids
    # repeated bolt round-trips for the same CI within a single worker session.
    _ci_cache: dict[str, Optional[str]] = {}

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password

    # ── Public methods ───────────────────────────────────────────────────

    async def create_incident(
        self,
        db_session: Any,
        workflow_id: str,
        title: str,
        description: str,
        work_notes: str,
        severity: Optional[str],
        lifecycle_state: Optional[str],
        service_name: Optional[str],
        platform_url: str = "http://localhost:3000",
        incident_number_str: str = "",
    ) -> dict[str, Any]:
        """Create a new SN incident and record the mapping."""
        existing = db_session.query(SNowIncidentMapModel).filter_by(
            platform_workflow_id=workflow_id
        ).first()
        if existing and existing.snow_sys_id:
            logger.info(f"Incident {workflow_id} already mapped to {existing.snow_number}")
            return {"snow_sys_id": existing.snow_sys_id, "snow_number": existing.snow_number, "status": "already_exists"}

        sev_map  = SEVERITY_TO_SN.get(severity or "medium", SEVERITY_TO_SN["medium"])
        sn_state = LIFECYCLE_TO_SN_STATE.get(lifecycle_state or "open", "1")

        platform_link = f"{platform_url.rstrip('/')}/incidents/{workflow_id}"
        header = (
            f"[Agentic Platform] Incident Created\n"
            f"{'-' * 50}\n"
            f"Platform ref: {incident_number_str or workflow_id}\n"
            f"Link: {platform_link}\n"
        )
        full_work_notes = header + ("\n" + work_notes if work_notes else "")

        payload: dict[str, Any] = {
            "short_description": title[:160],
            "description":       description,
            "impact":            sev_map["impact"],
            "urgency":           sev_map["urgency"],
            "priority":          sev_map["priority"],
            "state":             sn_state,
            "work_notes":        full_work_notes,
            "caller_id":         self.username,
        }

        if service_name:
            ci_sys_id = await self._lookup_ci(service_name)
            if ci_sys_id:
                payload["cmdb_ci"] = ci_sys_id
            else:
                logger.warning(f"CI '{service_name}' not found in SN CMDB — cmdb_ci left blank")

        async with ServiceNowClient(self.base_url, self.username, self.password) as client:
            try:
                result      = await client.create_record(SN_INCIDENT_TABLE, payload)
                snow_sys_id = result.get("sys_id", "")
                snow_number = result.get("number", "")
                push_status = "created"
            except ServiceNowError as e:
                logger.error(f"Failed to create SN incident for {workflow_id}: {e}")
                snow_sys_id = ""
                snow_number = ""
                push_status = "error"

        mapping = SNowIncidentMapModel(
            platform_workflow_id = workflow_id,
            snow_sys_id          = snow_sys_id,
            snow_number          = snow_number,
            last_pushed_at       = datetime.utcnow(),
            push_status          = push_status,
        )
        db_session.merge(mapping)
        db_session.commit()

        logger.info(f"{'✓' if push_status == 'created' else '✗'} SN incident {snow_number} ← {workflow_id}")
        return {"snow_sys_id": snow_sys_id, "snow_number": snow_number, "status": push_status}

    async def update_incident(
        self,
        db_session: Any,
        workflow_id: str,
        updates: dict[str, Any],
        work_notes: str = "",
        platform_url: str = "http://localhost:3000",
        incident_number_str: str = "",
        new_lifecycle_state: str = "",
    ) -> dict[str, Any]:
        """Update an existing SN incident."""
        mapping = db_session.query(SNowIncidentMapModel).filter_by(
            platform_workflow_id=workflow_id
        ).first()

        if not mapping or not mapping.snow_sys_id:
            return {"status": "not_found", "message": f"No SN mapping for {workflow_id}"}

        sn_payload: dict[str, Any] = {}

        if "severity" in updates:
            sev_map = SEVERITY_TO_SN.get(updates["severity"], SEVERITY_TO_SN["medium"])
            sn_payload.update(sev_map)

        if "lifecycle_state" in updates:
            sn_payload["state"] = LIFECYCLE_TO_SN_STATE.get(updates["lifecycle_state"], "2")

        if "title" in updates:
            sn_payload["short_description"] = updates["title"][:160]

        if "description" in updates:
            sn_payload["description"] = updates["description"]

        if work_notes:
            platform_link = f"{platform_url.rstrip('/')}/incidents/{workflow_id}"
            state_label = (new_lifecycle_state or updates.get("lifecycle_state", "")).upper().replace("_", " ")
            header = (
                f"[Agentic Platform] Lifecycle Update -> {state_label}\n"
                f"{'-' * 50}\n"
                f"Platform ref: {incident_number_str or workflow_id}\n"
                f"Link: {platform_link}\n"
            )
            sn_payload["work_notes"] = header + "\n" + work_notes

        if not sn_payload:
            return {"status": "noop", "message": "Nothing to update"}

        async with ServiceNowClient(self.base_url, self.username, self.password) as client:
            try:
                await client.update_record(SN_INCIDENT_TABLE, mapping.snow_sys_id, sn_payload)
                push_status = "updated"
            except ServiceNowError as e:
                logger.error(f"Failed to update SN incident {mapping.snow_number}: {e}")
                push_status = "error"

        mapping.last_pushed_at = datetime.utcnow()
        mapping.push_status    = push_status
        db_session.commit()

        return {"snow_sys_id": mapping.snow_sys_id, "snow_number": mapping.snow_number, "status": push_status}

    async def get_snow_status(self, db_session: Any, workflow_id: str) -> dict[str, Any]:
        """Return the current SN incident state for a platform workflow."""
        mapping = db_session.query(SNowIncidentMapModel).filter_by(
            platform_workflow_id=workflow_id
        ).first()
        if not mapping:
            return {"mapped": False}

        async with ServiceNowClient(self.base_url, self.username, self.password) as client:
            try:
                record = await client.get_record(
                    SN_INCIDENT_TABLE,
                    mapping.snow_sys_id,
                    fields=["sys_id", "number", "state", "short_description", "priority", "assigned_to", "resolved_at"],
                )
                return {
                    "mapped":         True,
                    "snow_sys_id":    mapping.snow_sys_id,
                    "snow_number":    mapping.snow_number,
                    "push_status":    mapping.push_status,
                    "last_pushed_at": mapping.last_pushed_at.isoformat() if mapping.last_pushed_at else None,
                    "snow_record":    record,
                }
            except ServiceNowError as e:
                return {"mapped": True, "snow_number": mapping.snow_number, "error": str(e)}

    # ── Auto-push entry point (called by Celery task) ────────────────────

    @classmethod
    async def auto_push_if_configured(
        cls,
        db_session: Any,
        workflow_id: str,
        trigger_event: str,
        new_lifecycle_state: str,
    ) -> dict[str, Any]:
        cfg_row = db_session.query(ConnectorConfigModel).filter_by(id="servicenow").first()
        if not cfg_row or not cfg_row.enabled:
            return {"status": "skipped", "reason": "connector not configured or disabled"}

        sync_cfg = get_incident_sync_config(cfg_row.config_json or {})
        if not sync_cfg["enabled"]:
            return {"status": "skipped", "reason": "incident_sync disabled"}

        from agentic_os.security.crypto import decrypt_if_encrypted

        creds    = cfg_row.config_json or {}
        base_url = creds.get("base_url", "")
        username = creds.get("username", "")
        password = decrypt_if_encrypted(creds.get("password", ""))
        if not base_url:
            return {"status": "skipped", "reason": "no credentials"}

        platform_url = sync_cfg.get("platform_url", "http://localhost:3000")
        pusher       = cls(base_url, username, password)

        workflow = cls._load_workflow(db_session, workflow_id)
        if not workflow:
            return {"status": "skipped", "reason": "workflow not found"}

        inc_num = getattr(workflow, "incident_number_str", "") or str(workflow_id)[:8]

        if trigger_event == "created" and sync_cfg["auto_create"]:
            title, description, work_notes, service_name = cls._build_create_fields(workflow, sync_cfg)
            logger.info(f"[SNOW] Auto-push created: workflow={workflow_id}")
            result = await pusher.create_incident(
                db_session          = db_session,
                workflow_id         = workflow_id,
                title               = title,
                description         = description,
                work_notes          = work_notes,
                severity            = getattr(workflow, "severity", None),
                lifecycle_state     = new_lifecycle_state,
                service_name        = service_name,
                platform_url        = platform_url,
                incident_number_str = inc_num,
            )

            # If the workflow already progressed past "open" by the time this task
            # ran (e.g. task was delayed), do an immediate catch-up update so the
            # SN state + work notes reflect the actual current state.
            actual_state = str(
                workflow.lifecycle_state.value
                if hasattr(workflow.lifecycle_state, "value")
                else workflow.lifecycle_state
            )
            if (
                result.get("status") == "created"
                and actual_state not in ("open", "")
                and actual_state in sync_cfg.get("auto_update_on_states", [])
            ):
                logger.info(f"[SNOW] Catch-up state update → {actual_state}: workflow={workflow_id}")
                upd_description, upd_notes = cls._build_update_fields(workflow, sync_cfg, actual_state)
                await pusher.update_incident(
                    db_session          = db_session,
                    workflow_id         = workflow_id,
                    updates             = {
                        "lifecycle_state": actual_state,
                        "description":     upd_description,
                        **({"severity": workflow.severity} if getattr(workflow, "severity", None) else {}),
                    },
                    work_notes          = upd_notes,
                    platform_url        = platform_url,
                    incident_number_str = inc_num,
                    new_lifecycle_state = actual_state,
                )

            return result

        if trigger_event == "state_changed" and new_lifecycle_state in sync_cfg["auto_update_on_states"]:
            description, work_notes = cls._build_update_fields(workflow, sync_cfg, new_lifecycle_state)
            updates: dict[str, Any] = {
                "lifecycle_state": new_lifecycle_state,
                "description":     description,
            }
            if getattr(workflow, "severity", None):
                updates["severity"] = workflow.severity
            logger.info(f"[SNOW] Auto-push state_changed → {new_lifecycle_state}: workflow={workflow_id}")
            return await pusher.update_incident(
                db_session          = db_session,
                workflow_id         = workflow_id,
                updates             = updates,
                work_notes          = work_notes,
                platform_url        = platform_url,
                incident_number_str = inc_num,
                new_lifecycle_state = new_lifecycle_state,
            )

        return {"status": "skipped", "reason": f"trigger_event={trigger_event} state={new_lifecycle_state} not configured"}

    # ── Field builders ───────────────────────────────────────────────────

    @staticmethod
    def _build_create_fields(workflow: Any, sync_cfg: dict) -> tuple[str, str, str, Optional[str]]:
        """Return (title, description, work_notes, service_name) for incident creation."""
        ctx:   dict = workflow.context or {}
        alert: dict = ctx.get("alert_payload") or {}
        cmdb:  dict = ctx.get("cmdb") or {}

        # ── Title ────────────────────────────────────────────────────────
        raw_title = (
            getattr(workflow, "title", None)
            or ctx.get("title")
            or alert.get("title")
            or alert.get("short_description")
            or alert.get("description", "")[:120]
            or f"Platform Incident {getattr(workflow, 'incident_number_str', workflow.workflow_id)}"
        )
        title = raw_title.strip()

        # ── AI summary — stored as top-level DB column, not in context ───
        ai_summary = (
            getattr(workflow, "summary", None)
            or ctx.get("summary") or ctx.get("ai_summary") or ""
        )

        # ── Description ───────────────────────────────────────────────────
        desc_parts: list[str] = []
        if sync_cfg.get("include_ai_summary") and ai_summary:
            desc_parts.append(ai_summary)
        if not desc_parts:
            desc_parts.append(alert.get("description") or title)
        description = "\n\n".join(desc_parts)

        # ── Work notes ────────────────────────────────────────────────────
        notes_parts: list[str] = []

        if sync_cfg.get("include_ai_summary") and ai_summary:
            notes_parts.append(f"AI SUMMARY\n{ai_summary}")

        # Remediation steps — use human-readable execution results, not raw runbook JSON
        exec_results: list = ctx.get("execution_results") or ctx.get("runbook_execution_results") or []
        if exec_results:
            lines = []
            for r in exec_results:
                tool      = r.get("tool", "step")
                stype     = r.get("step_type", "")
                status    = r.get("status") or ("success" if (r.get("result") or {}).get("success") else "failed")
                output    = r.get("output") or (r.get("result") or {}).get("output") or (r.get("result") or {}).get("message") or ""
                first_line = _strip_emoji(output.split("\n")[0][:120]) if output else ""
                icon      = "[OK]" if status == "success" else "[FAIL]"
                label     = f"{tool} ({stype})" if stype else tool
                lines.append(f"  {icon} {label}: {first_line}")
            notes_parts.append("REMEDIATION STEPS\n" + "\n".join(lines))

        if sync_cfg.get("append_agent_notes"):
            # Full trace stored as top-level DB column workflow.reasoning_trace
            trace: list = (
                getattr(workflow, "reasoning_trace", None)
                or ctx.get("reasoning_trace") or ctx.get("trace") or []
            )
            if trace:
                recent = trace[-10:]
                steps  = "\n".join(f"  {i+1}. {_strip_emoji(t)}" for i, t in enumerate(recent))
                notes_parts.append(f"AGENT TRACE (last {len(recent)} steps)\n{steps}")

        work_notes = "\n\n".join(notes_parts)

        # ── CI / service name ─────────────────────────────────────────────
        service_name = (
            cmdb.get("resource_name")
            or alert.get("resource_name")
            or ctx.get("service_name")
        )

        return title, description, work_notes, service_name

    @staticmethod
    def _build_update_fields(workflow: Any, sync_cfg: dict, new_state: str) -> tuple[str, str]:
        """Return (description, work_notes) for a lifecycle state update."""
        ctx:   dict = workflow.context or {}
        alert: dict = ctx.get("alert_payload") or {}

        # ── AI summary — top-level DB column ─────────────────────────────
        ai_summary = (
            getattr(workflow, "summary", None)
            or ctx.get("summary") or ctx.get("ai_summary") or ""
        )

        # ── Updated description ───────────────────────────────────────────
        desc_parts: list[str] = []
        if sync_cfg.get("include_ai_summary") and ai_summary:
            desc_parts.append(ai_summary)
        if not desc_parts:
            desc_parts.append(alert.get("description") or "See platform for details")
        description = "\n\n".join(desc_parts)

        # ── Work notes ────────────────────────────────────────────────────
        notes_parts: list[str] = []

        if sync_cfg.get("include_ai_summary") and ai_summary:
            notes_parts.append(f"AI SUMMARY\n{ai_summary}")

        exec_results: list = ctx.get("execution_results") or ctx.get("runbook_execution_results") or []
        if exec_results:
            lines = []
            for r in exec_results:
                tool      = r.get("tool", "step")
                stype     = r.get("step_type", "")
                status    = r.get("status") or ("success" if (r.get("result") or {}).get("success") else "failed")
                output    = r.get("output") or (r.get("result") or {}).get("output") or (r.get("result") or {}).get("message") or ""
                first_line = _strip_emoji(output.split("\n")[0][:120]) if output else ""
                icon      = "[OK]" if status == "success" else "[FAIL]"
                label     = f"{tool} ({stype})" if stype else tool
                lines.append(f"  {icon} {label}: {first_line}")
            notes_parts.append("REMEDIATION STEPS\n" + "\n".join(lines))

        if sync_cfg.get("append_agent_notes"):
            trace: list = (
                getattr(workflow, "reasoning_trace", None)
                or ctx.get("reasoning_trace") or ctx.get("trace") or []
            )
            if trace:
                recent = trace[-10:]
                steps  = "\n".join(f"  {i+1}. {_strip_emoji(t)}" for i, t in enumerate(recent))
                notes_parts.append(f"AGENT ACTIONS (last {len(recent)} steps)\n{steps}")

        work_notes = "\n\n".join(notes_parts)

        return description, work_notes

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_workflow(db_session: Any, workflow_id: str) -> Any:
        try:
            from agentic_os.db.models import WorkflowStateModel
            import uuid
            return db_session.query(WorkflowStateModel).filter_by(
                workflow_id=uuid.UUID(workflow_id)
            ).first()
        except Exception:
            return None

    async def _lookup_ci(self, name: str) -> Optional[str]:
        """
        Return the SNOW sys_id for a CI by looking it up in Neo4j.

        Neo4j is the CMDB source of truth. cmdb_sync writes sn_sys_id onto
        each CI node when it imports records from ServiceNow, so if it isn't
        in Neo4j it isn't in SNOW and there is nothing to link.
        """
        if name in IncidentPush._ci_cache:
            return IncidentPush._ci_cache[name]

        sys_id = _neo4j_ci_lookup(name)
        IncidentPush._ci_cache[name] = sys_id
        if sys_id:
            logger.info(f"CI lookup: '{name}' → {sys_id}")
        else:
            logger.debug(f"CI lookup: '{name}' → not in CMDB, cmdb_ci left blank")
        return sys_id
