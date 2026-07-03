"""Celery app and task definitions for background workflow execution"""

from celery import Celery
from kombu import Exchange, Queue
import os
import logging
import json
import asyncio
from uuid import UUID

logger = logging.getLogger(__name__)

# Celery configuration
app = Celery(
    "agentic_os",
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
)

# Configure Celery
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # Hard limit: 1 hour
    task_soft_time_limit=3300,  # Soft limit: 55 minutes
    broker_connection_retry_on_startup=True,
)

# Define queues
default_exchange = Exchange("agentic_os", type="direct")

app.conf.task_queues = (
    Queue("default", exchange=default_exchange, routing_key="default"),
    Queue("workflows", exchange=default_exchange, routing_key="workflows"),
    Queue("approvals", exchange=default_exchange, routing_key="approvals"),
)

# Default queue
app.conf.task_default_queue = "default"
app.conf.task_default_exchange = "default"
app.conf.task_default_routing_key = "default"


# ── System note helper ────────────────────────────────────────────────────────

def _write_system_note(db, workflow_id: str, final_state) -> None:
    """
    Write an automatic system note capturing the full remediation outcome.
    Covers: resolved/closed (success), awaiting_manual (failure),
    and diagnostics_only (partial approval — no remediation actions taken).
    Plain text only — no emoji characters.
    """
    from agentic_os.db.models import IncidentNoteModel
    from agentic_os.core.models import LifecycleState
    from datetime import datetime as _dt

    try:
        lc           = final_state.lifecycle_state
        ctx          = final_state.context or {}
        proposal     = ctx.get("proposal", {})
        exec_results = ctx.get("execution_results", [])
        runbook_name = (
            proposal.get("runbook_name")
            or proposal.get("action")
            or "automated runbook"
        )
        target           = (proposal.get("target") or "").strip()
        decision_result  = ctx.get("decision_result") or ctx.get("remediation_outcome") or ""
        is_diag_only     = decision_result == "diagnostics_only"

        def _fmt_steps(results, label="Steps"):
            if not results:
                return []
            succeeded = sum(1 for r in results if r.get("status") != "failed")
            out = [f"{label} ({succeeded} of {len(results)} OK):"]
            for r in results:
                num    = r.get("step", "?")
                tool   = r.get("tool", "unknown")
                status = "OK" if r.get("status") != "failed" else "FAILED"
                raw    = (r.get("output") or "").strip()
                # Trim and flatten output — cap at 400 chars, max 3 display lines
                raw_lines = [l for l in raw.splitlines() if l.strip()][:3]
                snippet   = " | ".join(raw_lines)[:400]
                step_line = f"  Step {num} [{tool}] ({status})"
                if snippet:
                    step_line += f": {snippet}"
                out.append(step_line)
            return out

        lines = []

        if is_diag_only:
            lines += [
                "Outcome: Diagnostics Only - no remediation actions were taken",
                f"Runbook: {runbook_name}",
            ]
            if target and target != "unknown":
                lines.append(f"Target: {target}")
            lines.append("")
            lines += _fmt_steps(exec_results, "Diagnostic Steps")
            lines += [
                "",
                "Next steps: Review the diagnostic findings above.",
                "Use Resolve Manually to close the incident with your findings.",
            ]

        elif lc in (LifecycleState.RESOLVED, LifecycleState.CLOSED):
            lines += [
                f"Outcome: {lc.value.title()}",
                f"Runbook: {runbook_name}",
            ]
            if target and target != "unknown":
                lines.append(f"Target: {target}")
            lines.append("")
            lines += _fmt_steps(exec_results, "Remediation Steps")
            if final_state.summary:
                lines += ["", f"Summary: {final_state.summary}"]
            lines += ["", f"Completed: {_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"]

        elif lc == LifecycleState.AWAITING_MANUAL:
            failed = [r for r in exec_results if r.get("status") == "failed"]
            lines += [
                "Outcome: Automated remediation failed - Awaiting Manual Intervention",
                f"Runbook: {runbook_name}",
            ]
            if target and target != "unknown":
                lines.append(f"Target: {target}")
            lines.append("")
            lines += _fmt_steps(exec_results, "Steps Attempted")
            if failed:
                lines += ["", f"Failed steps: {', '.join(str(r.get('step','?')) for r in failed)}"]
            lines += [
                "",
                "Next steps: Use Retry Automation to re-queue the runbook,",
                "or Resolve Manually to close with your own resolution.",
            ]

        else:
            return  # No automatic note for other states

        note = IncidentNoteModel(
            workflow_id=UUID(str(final_state.workflow_id)),
            author="system",
            note_type="system",
            body="\n".join(lines),
        )
        db.add(note)
        db.commit()
        logger.info("System note written for workflow %s (state: %s)", workflow_id, lc.value)

    except Exception as _err:
        logger.warning("Failed to write system note for %s: %s", workflow_id, _err)
        try:
            db.rollback()
        except Exception:
            pass


# Tasks
@app.task(bind=True, queue="workflows", acks_late=True, reject_on_worker_lost=True)
def execute_workflow_task(self, workflow_id: str, workflow_type: str):
    """Execute workflow in background"""
    from uuid import UUID
    from sqlalchemy.orm import Session
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import WorkflowRepository
    from agentic_os.core.workflow_engine import WorkflowEngine
    from agentic_os.core.definitions import WorkflowDefinitionLoader
    from agentic_os.core.models import WorkflowType, LifecycleState
    from agentic_os.bus.postgres_bus import PostgresEventBus
    from agentic_os.agents.registry import register_all_agents

    logger.info(f"[{self.request.id}] Executing {workflow_type} workflow {workflow_id}")

    try:
        # Initialize database and event bus
        db = SessionLocal()
        _db_url = os.getenv("DATABASE_URL", "postgresql://postgres:agentic_os@postgres:5432/agentic_os")
        event_bus = PostgresEventBus(_db_url)

        # Initialize workflow engine
        engine = WorkflowEngine(event_bus, db)
        register_all_agents(engine)

        # Load workflow state
        repo = WorkflowRepository(db)
        state = repo.get(UUID(workflow_id))

        if not state:
            logger.error(f"Workflow {workflow_id} not found")
            return {"status": "error", "reason": "Workflow not found"}

        # Load workflow definition (use absolute path in Docker)
        workflows_dir = "/app/workflows"
        loader = WorkflowDefinitionLoader(workflows_dir)
        definition = loader.load_definition(WorkflowType(workflow_type), "v1")

        if not definition:
            logger.error(f"Workflow definition not found for {workflow_type}")
            return {"status": "error", "reason": "Workflow definition not found"}

        # ── Pre-pipeline storm adoption guard ─────────────────────────────────
        # If this incident was adopted into a storm while the Celery task was
        # sitting in the queue (or while the worker was starting up), skip the
        # full pipeline — running individual remediation against a storm child is
        # both incorrect (addresses a symptom, not the root cause) and wasteful.
        if workflow_type == "incident":
            try:
                from sqlalchemy import text as _sql_t
                pre_check = db.execute(_sql_t("""
                    SELECT storm_id, lifecycle_state
                    FROM workflow_states
                    WHERE workflow_id = :wf_id
                """), {"wf_id": UUID(workflow_id)}).fetchone()
                if pre_check and pre_check[0]:
                    _storm_id_pre = str(pre_check[0])
                    logger.info(
                        f"[{self.request.id}] Incident already in storm "
                        f"{_storm_id_pre[:8]} before pipeline start — skipping execution"
                    )
                    db.close()
                    return {
                        "status":   "skipped_storm_hold",
                        "storm_id": _storm_id_pre,
                    }
            except Exception as _pre_err:
                logger.warning(
                    f"[{self.request.id}] Pre-pipeline storm check failed "
                    f"(proceeding normally): {_pre_err}"
                )

        # Execute workflow — explicit loop avoids asyncio.run() clearing the
        # thread-local event loop used by billiard's ForkPoolWorker result pipe.
        logger.info(f"[{self.request.id}] Starting workflow execution...")
        _loop = asyncio.new_event_loop()
        try:
            final_state = _loop.run_until_complete(engine.execute(definition, state))
        finally:
            _loop.close()

        logger.info(
            f"[{self.request.id}] Workflow completed with state: {final_state.lifecycle_state.value}"
        )

        # ── Summary generation (after all agents have run) ───────────────────────
        # Re-read DB — agents called repo.save() during execution so current_db
        # already has final risk_score, severity, lifecycle_state, execution_results.
        from datetime import datetime as _dt
        current_db = repo.get(UUID(workflow_id))

        # Build full context dict from the completed workflow state
        ctx            = final_state.context or {}
        sentinel_ctx   = ctx.get("sentinel", {})
        cmdb_ctx       = ctx.get("cmdb", {})
        risk_ctx       = ctx.get("risk", {})
        proposal_ctx   = ctx.get("proposal", {})
        governance_ctx = ctx.get("governance", {})
        alert_payload  = ctx.get("alert_payload", {})
        ap             = sentinel_ctx.get("alert_payload", alert_payload) if sentinel_ctx else alert_payload
        exec_results   = ctx.get("execution_results", [])
        verif_ctx      = ctx.get("verification", {})

        # Flatten actions taken from execution_results
        actions_taken = [
            f"{r.get('tool','?')} → {r.get('status','?')}"
            + (f": {str(r.get('output',''))[:100]}" if r.get('output') else "")
            for r in exec_results
        ]

        # Build verification string
        verif_str = ""
        if verif_ctx:
            overall = verif_ctx.get("overall_success", None)
            verif_str = "Success" if overall else ("Failed" if overall is False else "")
            verif_results = verif_ctx.get("verification_results", [])
            if verif_results:
                verif_str += " — " + "; ".join(
                    f"{v.get('step_name','?')}: {v.get('message','')}"
                    for v in verif_results[:3]
                )

        # Governance fields
        gov_approval_required = governance_ctx.get("approval_required", False)
        gov_notes             = governance_ctx.get("decision_notes", "")
        gov_decision_str      = final_state.governance_decision or ""
        matching_policies     = governance_ctx.get("matching_policies", [])

        full_context = {
            "event_type":             ap.get("type") or alert_payload.get("type", "Unknown"),
            "description":            ap.get("description") or alert_payload.get("description", ""),
            "resource":               cmdb_ctx.get("resource_name") or alert_payload.get("resource_name", "Unknown"),
            "environment":            cmdb_ctx.get("environment", "unknown"),
            "severity":               str(final_state.severity.value if final_state.severity else alert_payload.get("severity", "unknown")),
            "risk_score":             round(float(final_state.risk_score or risk_ctx.get("risk_score", 0) or 0), 1),
            "blast_radius":           risk_ctx.get("blast_radius", "N/A"),
            "remediation_complexity": risk_ctx.get("remediation_complexity", ""),
            "anomaly_process":        ap.get("anomaly_process") or alert_payload.get("anomaly_process", ""),
            "anomaly_metrics":        ap.get("anomaly_metrics") or ap.get("syscall_rate", ""),
            "runbook":                proposal_ctx.get("runbook_name", ""),
            "actions_taken":          actions_taken,
            "execution_results":      exec_results,
            "verification":           verif_str,
            "lifecycle_state":        final_state.lifecycle_state.value,
            "dependencies":           cmdb_ctx.get("dependencies", []),
            "impacted_services":      cmdb_ctx.get("impacted_services", []),
            # Governance (required by _build_rich_prompt)
            "governance_decision":    gov_decision_str,
            "approval_required":      gov_approval_required,
            "governance_notes":       gov_notes,
            "matching_policies":      matching_policies,
        }

        # ── Summary generation strategy (Fix 2) ─────────────────────────────
        # LLM enrichment (InsightAgent + RichSummary) now runs off the critical
        # path via enrich_incident_async dispatched after repo.save().
        # • waiting_approval removed from trigger states (Fix 2): the approver
        #   sees the fast platform-context summary instantly; the LLM version
        #   arrives seconds later via the background worker.
        # • All other terminal states dispatch enrich_incident_async (Fix 1).
        # • Platform-context summary is written here synchronously so the
        #   frontend gets an immediate update on repo.save().
        # ─────────────────────────────────────────────────────────────────────
        from agentic_os.services.summary_service import get_summary_service
        from agentic_os.services.platform_context_service import get_platform_context_service, PlatformContextService

        # States that warrant async LLM enrichment — waiting_approval excluded (Fix 2)
        _ENRICH_STATES = {"resolved", "failed", "awaiting_manual", "monitoring", "closed"}
        lc_val = final_state.lifecycle_state.value

        summary_service  = get_summary_service()
        platform_service = get_platform_context_service()

        # Fast synchronous platform-context summary — written to state before
        # repo.save() so the frontend never sees a blank summary field.
        new_summary = None
        try:
            if current_db:
                new_summary = platform_service.generate_summary(current_db)
            if not new_summary:
                new_summary = PlatformContextService.build_progressive_summary(final_state)
        except Exception as _pcs_err:
            logger.warning(f"[{self.request.id}] Platform-context summary failed: {_pcs_err}")
        if not new_summary:
            event_type  = full_context.get("event_type", "Incident")
            resource    = full_context.get("resource", "Unknown")
            new_summary = f"{event_type.replace('_', ' ').title()} on {resource}"

        final_state.summary            = new_summary
        final_state.technical_summary  = ""   # populated later by enrich_incident_async
        final_state.summary_generated_at = _dt.utcnow()

        logger.info(
            f"[{self.request.id}] Platform-context summary ready "
            f"({len(new_summary)} chars) — LLM enrichment queued async"
        )

        # Check for execution failures and transition to AWAITING_MANUAL if needed.
        # (LifecycleState.FAILED is reserved for internal pipeline errors in base.py;
        #  tool-execution failures are a remediation outcome — human must decide next steps.)
        execution_result = final_state.context.get("execution_result", {})
        if execution_result and not execution_result.get("success", True):
            logger.warning(f"[{self.request.id}] Tool execution failed - transitioning to AWAITING_MANUAL")
            final_state.lifecycle_state = LifecycleState.AWAITING_MANUAL
            if not final_state.remediation_outcome:
                final_state.remediation_outcome = "failed"

        # ── Storm adoption guard (end-of-pipeline) ───────────────────────────
        # Re-read storm_id from DB after the entire pipeline has run.
        # Two scenarios:
        #   A. Storm adopted this incident MID-pipeline → override lifecycle_state
        #      to storm_hold so repo.save() doesn't overwrite it.
        #   B. Tools already ran on this incident before it was adopted →
        #      write a note on the storm parent so operators know remediation
        #      was applied at the individual level before the storm was detected.
        if workflow_type == "incident":
            try:
                from sqlalchemy import text as _sql_t
                fresh = db.execute(_sql_t("""
                    SELECT storm_id FROM workflow_states WHERE workflow_id = :wf_id
                """), {"wf_id": UUID(workflow_id)}).fetchone()
                if fresh and fresh[0]:
                    _storm_id_end = str(fresh[0])
                    _had_execution = bool(
                        final_state.context.get("execution_results") or
                        final_state.context.get("execution_result", {}).get("success")
                    )
                    logger.info(
                        f"[{self.request.id}] Incident adopted into storm "
                        f"{_storm_id_end[:8]} mid-pipeline — "
                        f"overriding lifecycle_state to storm_hold "
                        f"(tools_ran={_had_execution})"
                    )
                    final_state.lifecycle_state = LifecycleState.STORM_HOLD

                    # Scenario B: document pre-storm remediation on storm parent
                    if _had_execution:
                        try:
                            from agentic_os.db.models import IncidentNoteModel
                            _child_res = final_state.context.get("cmdb", {}).get("resource_name",
                                         final_state.context.get("alert_payload", {}).get("resource_name", "unknown"))
                            _exec_res = final_state.context.get("execution_results", [])
                            _exec_summary = ", ".join(
                                f"{r.get('tool','?')}→{r.get('status','?')}"
                                for r in (_exec_res if isinstance(_exec_res, list) else [_exec_res])[:5]
                            ) or "see child incident"
                            db.add(IncidentNoteModel(
                                workflow_id=fresh[0],
                                author="storm_agent",
                                note_type="system",
                                body=(
                                    f"Pre-Storm Remediation Warning\n\n"
                                    f"Incident {workflow_id[:8]} (resource: {_child_res}) was adopted into this storm "
                                    f"AFTER individual remediation tools had already executed.\n\n"
                                    f"Remediations applied: {_exec_summary}\n\n"
                                    f"These actions targeted the symptom ({_child_res}) rather than the storm root cause. "
                                    f"Verify that individual remediations do not conflict with the storm-level remediation plan."
                                ),
                            ))
                            db.commit()
                        except Exception as _note_err:
                            logger.warning(
                                f"[{self.request.id}] Could not write pre-storm "
                                f"remediation note: {_note_err}"
                            )
            except Exception as _sg_err:
                logger.warning(f"[{self.request.id}] Storm adoption guard failed: {_sg_err}")

        # Persist final state to database
        repo.save(final_state)
        logger.info(f"[{self.request.id}] Workflow state persisted to database")

        # ── Fire-and-forget LLM enrichment (Fix 1) ───────────────────────────
        # Dispatched AFTER repo.save() so the fast platform-context summary is
        # already in the DB when enrich_incident_async overwrites it with the
        # richer LLM version a few seconds later.
        from agentic_os.services.summary_service import get_insights_enabled
        if summary_service.is_provider_configured() and get_insights_enabled() and lc_val in _ENRICH_STATES:
            try:
                enrich_incident_async.apply_async(
                    args=[str(workflow_id), full_context, lc_val],
                    queue="workflows",
                    countdown=0,
                )
                logger.info(
                    f"[{self.request.id}] LLM enrichment queued for "
                    f"{workflow_id[:8]} ({lc_val})"
                )
            except Exception as _enrich_err:
                logger.warning(
                    f"[{self.request.id}] LLM enrichment dispatch failed "
                    f"(non-fatal): {_enrich_err}"
                )

        # Auto system note (resolved, awaiting_manual, diagnostics_only)
        _write_system_note(db, str(final_state.workflow_id), final_state)

        # Record runbook execution feedback (updates confidence + trend)
        if workflow_type == "incident":
            try:
                from agentic_os.services.runbook_feedback import record_from_workflow
                record_from_workflow(db, final_state)
            except Exception as _fb_err:
                logger.warning(f"[{self.request.id}] Runbook feedback failed (non-fatal): {_fb_err}")

        # Clear event condition state on terminal resolution so the next alert
        # on this resource fires a fresh event rather than being deduplicated.
        if workflow_type == "incident" and lc_val in ("resolved", "closed"):
            try:
                from agentic_os.db.repositories import EventConditionStateRepository
                _cond_repo = EventConditionStateRepository(db)
                _closed = _cond_repo.close_for_workflow(str(final_state.workflow_id), db)
                if _closed:
                    logger.info(
                        f"[{self.request.id}] Cleared {_closed} condition-state row(s) "
                        f"for resolved workflow {str(final_state.workflow_id)[:8]}"
                    )
            except Exception as _cond_err:
                logger.warning(
                    f"[{self.request.id}] Condition-state clear failed (non-fatal): {_cond_err}"
                )

        # Auto-update ServiceNow incident on terminal state (fire-and-forget)
        if workflow_type == "incident":
            try:
                from agentic_os.tasks.snow_sync import snow_push_incident_state
                snow_push_incident_state.delay(workflow_id, final_state.lifecycle_state.value)
            except Exception as _sn_err:
                logger.warning(f"[{self.request.id}] SN state push scheduling failed: {_sn_err}")

        # Slack resolved / terminal-state notification.
        # Fired here (Celery) so it works regardless of whether any browser tab
        # is open — the WS poll loop in ws.py only runs while the UI is connected.
        _NOTIFY_TERMINAL = {"resolved", "deployed", "rolled_back", "rejected", "failed",
                            "awaiting_manual"}
        if workflow_type == "incident" and lc_val in _NOTIFY_TERMINAL:
            try:
                from agentic_os.services.notifications import notify_incident_resolved
                _inc_num  = str(final_state.incident_number_str or "")
                _title    = str(current_db.title if current_db else "") or "Untitled"
                _sev      = str(getattr(final_state.severity, "value",
                                        str(final_state.severity or "unknown")))
                _outcome  = str(final_state.remediation_outcome or "") or None
                _rsk      = float(current_db.risk_score) if (current_db and current_db.risk_score) else None
                notify_incident_resolved(_inc_num, _title, _sev, lc_val, _rsk, _outcome)
                logger.info(f"[{self.request.id}] Slack resolved notification sent for {_inc_num} ({lc_val})")
            except Exception as _slack_err:
                logger.warning(f"[{self.request.id}] Slack resolved notification failed (non-fatal): {_slack_err}")

        # Check if approval is required and create approval record if needed
        if final_state.lifecycle_state.value == "waiting_approval":
            from agentic_os.db.models import ApprovalModel
            from datetime import datetime

            # ── Pre-approval storm adoption guard ──────────────────────────────
            # Race condition: storm detection fires with a 3-second countdown and
            # may have adopted this incident (setting storm_id + storm_hold) AFTER
            # repo.save() committed but BEFORE we reach this point.  In that window
            # the storm task's cancellation step found 0 pending approvals (none
            # existed yet).  Without this guard we create an orphaned 'pending'
            # approval that nobody will ever cancel or action.
            _adopted_by_storm = False
            if workflow_type == "incident":
                try:
                    from sqlalchemy import text as _sql_t2
                    _storm_check = db.execute(_sql_t2("""
                        SELECT storm_id
                        FROM workflow_states
                        WHERE workflow_id = :wf_id
                    """), {"wf_id": UUID(workflow_id)}).fetchone()
                    if _storm_check and _storm_check[0] is not None:
                        _adopted_by_storm = True
                        logger.info(
                            f"[{self.request.id}] Pre-approval storm guard: incident "
                            f"was adopted into storm {str(_storm_check[0])[:8]} "
                            f"after repo.save() — skipping governance approval creation"
                        )
                except Exception as _spa_err:
                    logger.warning(
                        f"[{self.request.id}] Pre-approval storm guard failed "
                        f"(proceeding with approval creation): {_spa_err}"
                    )

            if not _adopted_by_storm:
                logger.info(f"[{self.request.id}] Workflow requires approval, creating approval record...")

                try:
                    # Extract incident context for approval summary
                    governance_ctx = final_state.context.get("governance", {})
                    cmdb_ctx = final_state.context.get("cmdb", {})
                    sentinel_ctx = final_state.context.get("sentinel", {})
                    proposal_ctx = final_state.context.get("proposal", {})

                    # Create incident summary for approval
                    incident_summary = {
                        "anomaly_type": sentinel_ctx.get("anomaly_type", "unknown"),
                        "severity": final_state.context.get("alert_payload", {}).get("severity", "unknown"),
                        "risk_score": final_state.risk_score or 0,
                        "resource": cmdb_ctx.get("resource_name", "unknown"),
                        "environment": cmdb_ctx.get("environment", "unknown"),
                    }

                    # Create proposed action details
                    # Determine primary action from first remediation step
                    remediation_steps = proposal_ctx.get("remediation_steps", [])
                    diagnostics_steps = proposal_ctx.get("diagnostics_steps", [])
                    primary_action = (
                        remediation_steps[0].get("tool", "unknown") if remediation_steps else "unknown"
                    )
                    proposed_action = {
                        "runbook": proposal_ctx.get("runbook_name", "unknown"),
                        "runbook_id": proposal_ctx.get("runbook_id", ""),
                        "action": primary_action,
                        "target": cmdb_ctx.get("resource_name", "unknown"),
                        "blast_radius": proposal_ctx.get("blast_radius", 1),
                        "confidence": proposal_ctx.get("confidence", 0.0),
                        "source": proposal_ctx.get("source", "runbook_library"),
                        "requires_post_monitoring": governance_ctx.get("requires_post_monitoring", False),
                        "decision_notes": governance_ctx.get("decision_notes", ""),
                        "remediation_steps": remediation_steps,
                        "diagnostics_steps": diagnostics_steps,
                    }

                    # Create approval record
                    approval = ApprovalModel(
                        workflow_id=UUID(workflow_id),
                        approval_type="governance",
                        status="pending",
                        requested_at=datetime.utcnow(),
                        proposed_action=proposed_action,
                        incident_summary=incident_summary,
                        extra_metadata={
                            "approval_priority": governance_ctx.get("approval_priority", 50),
                            "matching_policies": governance_ctx.get("matching_policies", []),
                            "allowed_actions": governance_ctx.get("allowed_actions", []),
                        }
                    )

                    # Save approval record
                    db.add(approval)
                    db.commit()

                    logger.info(
                        f"[{self.request.id}] Approval record created: {approval.approval_id}"
                    )

                except Exception as approval_err:
                    logger.error(
                        f"[{self.request.id}] Failed to create approval record: {approval_err}",
                        exc_info=True
                    )
                    db.rollback()
                    # Don't fail the workflow, just log the error

        return {
            "status": "completed",
            "workflow_id": workflow_id,
            "lifecycle_state": final_state.lifecycle_state.value,
            "traces": len(final_state.reasoning_trace),
        }

    except Exception as e:
        logger.error(f"[{self.request.id}] Workflow execution failed: {str(e)}", exc_info=True)
        # Fallback: transition workflow to AWAITING_MANUAL so operator can intervene
        try:
            if 'db' in locals() and 'repo' in locals():
                state = repo.get(UUID(workflow_id))
                if state:
                    error_msg = f"{type(e).__name__}: {str(e)[:500]}"
                    logger.info(f"[{self.request.id}] Transitioning {workflow_id[:8]} to AWAITING_MANUAL: {error_msg}")
                    state.lifecycle_state = LifecycleState.AWAITING_MANUAL
                    repo.save(state)
                    try:
                        from agentic_os.db.models import IncidentNoteModel
                        db.add(IncidentNoteModel(
                            workflow_id=UUID(workflow_id),
                            author="system",
                            note_type="system",
                            body=f"Workflow execution crashed: {error_msg}\n\nOperator manual intervention required.",
                        ))
                        db.commit()
                    except Exception as _note_err:
                        logger.warning(f"[{self.request.id}] Could not write error note: {_note_err}")
                        db.rollback()
        except Exception as fallback_err:
            logger.error(f"[{self.request.id}] Fallback to AWAITING_MANUAL failed: {fallback_err}", exc_info=True)

        return {"status": "error", "reason": str(e)}
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.task(bind=True, queue="approvals")
def handle_approval_timeout(self, approval_id: str, workflow_id: str):
    """Handle approval timeout (CAB approval expires after 3 days)"""
    from uuid import UUID
    from sqlalchemy.orm import Session
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import ApprovalRepository

    logger.info(f"Handling approval timeout: {approval_id}")

    try:
        db = SessionLocal()
        repo = ApprovalRepository(db)

        # Check if approval is still pending
        approval = db.query(repo.model).filter(repo.model.approval_id == UUID(approval_id)).first()

        if approval and approval.status == "pending":
            logger.warning(f"Approval {approval_id} timed out - auto-rejecting")
            repo.reject(
                approval_id=UUID(approval_id),
                decided_by="system",
                decision_notes="Approval timeout - auto-rejected after 72 hours",
            )

        return {"status": "completed", "approval_id": approval_id}

    except Exception as e:
        logger.error(f"Error handling approval timeout: {str(e)}", exc_info=True)
        return {"status": "error", "reason": str(e)}
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.task(queue="default")
def health_check():
    """Periodic health check task"""
    logger.debug("Celery health check - OK")
    return {"status": "healthy"}


# ── Off-critical-path LLM enrichment (Fixes 1, 3, 4) ─────────────────────────
#
# Fix 1: InsightAgent + RichSummary are dispatched here as a fire-and-forget
#        background task so execute/resume workflow tasks return in < 1 s.
# Fix 3: InsightAgent (runs in a thread) + RichSummary (async coroutine) are
#        started concurrently via asyncio.gather, halving the LLM wait time.
# Fix 4: InsightAgent result is cached per incident_id (FIFO, 256 entries) so
#        retries or double-dispatches never issue a second LLM call.
# ─────────────────────────────────────────────────────────────────────────────

_INSIGHT_CACHE: dict = {}
_INSIGHT_CACHE_ORDER: list = []
_INSIGHT_CACHE_MAX = 256


def _insight_cache_get(incident_id: str):
    return _INSIGHT_CACHE.get(incident_id)


def _insight_cache_set(incident_id: str, value) -> None:
    if incident_id not in _INSIGHT_CACHE:
        _INSIGHT_CACHE_ORDER.append(incident_id)
        while len(_INSIGHT_CACHE_ORDER) > _INSIGHT_CACHE_MAX:
            evict = _INSIGHT_CACHE_ORDER.pop(0)
            _INSIGHT_CACHE.pop(evict, None)
    _INSIGHT_CACHE[incident_id] = value


def _run_insight_agent_sync(workflow_id: str):
    """
    Run InsightAgent in a dedicated thread with its own DB session.
    InsightAgent creates its own asyncio event loop internally — safe to call
    from a thread even while an outer event loop is running in another thread.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import WorkflowRepository
    from agentic_os.agents.insight_agent import InsightAgent
    from uuid import UUID

    db_t = SessionLocal()
    try:
        state = WorkflowRepository(db_t).get(UUID(workflow_id))
        if not state:
            return None
        return InsightAgent().generate_insights(state, db_t)
    except Exception as exc:
        logger.warning(f"[ENRICH] InsightAgent thread failed: {exc}")
        return None
    finally:
        db_t.close()


@app.task(bind=True, queue="workflows", max_retries=1, acks_late=True)
def enrich_incident_async(self, workflow_id: str, full_context: dict, lc_val: str):
    """
    Off-critical-path LLM enrichment task (Fix 1).

    Dispatched fire-and-forget from execute_workflow_task / resume_workflow_task
    after repo.save() so those tasks return immediately instead of blocking
    ~10 s on sequential LLM calls.

    Internally (Fix 3): runs InsightAgent in a thread pool and RichSummary
    as an async coroutine concurrently via asyncio.gather — halves LLM wait.

    Internally (Fix 4): caches InsightAgent output per incident_id so retries
    or accidental double-dispatches skip the second LLM round-trip.
    """
    import asyncio
    import concurrent.futures
    from datetime import datetime as _dt_e
    from uuid import UUID
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import WorkflowRepository
    from agentic_os.services.summary_service import get_summary_service, get_insights_enabled

    logger.info(
        f"[ENRICH] Starting async enrichment for {workflow_id[:8]} (state={lc_val})"
    )
    db = SessionLocal()
    try:
        repo  = WorkflowRepository(db)
        state = repo.get(UUID(workflow_id))
        if not state:
            logger.warning(f"[ENRICH] Workflow {workflow_id} not found — skipping")
            return {"status": "skipped", "reason": "not_found"}

        svc = get_summary_service()
        if not svc.is_provider_configured():
            logger.debug("[ENRICH] LLM not configured — skipping")
            return {"status": "skipped", "reason": "no_llm"}

        if not get_insights_enabled():
            logger.debug("[ENRICH] AI insights disabled — skipping insight generation")
            return {"status": "skipped", "reason": "insights_disabled"}

        # Fix 4: serve from cache if insight was already generated for this incident
        cached_insights = _insight_cache_get(workflow_id)

        async def _concurrent_llm():
            """
            Fix 3: InsightAgent (thread) + RichSummary (async) run concurrently.
            Returns (insight_result, rich_result) — either may be an exception.
            """
            loop = asyncio.get_running_loop()

            if cached_insights is not None:
                logger.debug(f"[ENRICH] InsightAgent cache hit for {workflow_id[:8]}")
                insight_result = cached_insights
                try:
                    rich_result = await svc.generate_rich_summary_async(
                        incident_id=workflow_id,
                        full_context=full_context,
                    )
                except Exception as exc:
                    logger.warning(f"[ENRICH] RichSummary failed (cache-hit path): {exc}")
                    rich_result = {}
            else:
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    insight_result, rich_result = await asyncio.gather(
                        loop.run_in_executor(executor, _run_insight_agent_sync, workflow_id),
                        svc.generate_rich_summary_async(
                            incident_id=workflow_id,
                            full_context=full_context,
                        ),
                        return_exceptions=True,
                    )
                finally:
                    executor.shutdown(wait=False)

                if isinstance(insight_result, BaseException):
                    logger.warning(f"[ENRICH] InsightAgent raised: {insight_result}")
                    insight_result = None
                if isinstance(rich_result, BaseException):
                    logger.warning(f"[ENRICH] RichSummary raised: {rich_result}")
                    rich_result = {}

                # Fix 4: populate cache
                if isinstance(insight_result, dict):
                    _insight_cache_set(workflow_id, insight_result)

            return insight_result, rich_result

        # Use explicit loop instead of asyncio.run() — asyncio.run() calls
        # asyncio.set_event_loop(None) on exit, which clears the thread-local
        # event loop that billiard uses for result reporting in ForkPoolWorkers,
        # causing workers to exit and accumulate as zombies.
        _loop = asyncio.new_event_loop()
        try:
            insight_result, rich_result = _loop.run_until_complete(_concurrent_llm())
        finally:
            _loop.close()

        # ── Write results back — re-read state to avoid clobbering concurrent edits ──
        state = repo.get(UUID(workflow_id))
        if not state:
            logger.warning(f"[ENRICH] Workflow {workflow_id} vanished before write — aborting")
            return {"status": "skipped", "reason": "vanished"}

        changed = False

        if isinstance(insight_result, dict):
            if state.context is None:
                state.context = {}
            state.context["llm_insights"] = insight_result
            changed = True
            logger.info(
                f"[ENRICH] Insights written for {workflow_id[:8]}: "
                f"confidence={insight_result.get('confidence', '?')}, "
                f"rag_hits={insight_result.get('rag_similar_count', 0)}"
            )

        rich_dict     = rich_result if isinstance(rich_result, dict) else {}
        new_summary   = rich_dict.get("summary")
        new_technical = rich_dict.get("technical_summary")

        if new_summary:
            state.summary            = new_summary
            state.technical_summary  = new_technical or ""
            state.summary_generated_at = _dt_e.utcnow()
            changed = True
            logger.info(
                f"[ENRICH] Rich summary written for {workflow_id[:8]}: "
                f"{len(new_summary)} chars executive, "
                f"{len(new_technical or '')} chars technical"
            )

        if changed:
            repo.save(state)

        return {
            "status": "ok",
            "workflow_id": workflow_id,
            "insights_written": bool(isinstance(insight_result, dict)),
            "rich_summary_written": bool(new_summary),
        }

    except Exception as exc:
        logger.error(
            f"[ENRICH] Enrichment failed for {workflow_id}: {exc}", exc_info=True
        )
        try:
            self.retry(countdown=15, exc=exc)
        except self.MaxRetriesExceededError:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()


# ── Daily incident cleanup ─────────────────────────────────────────────────────

@app.task(bind=True, queue="default")
def cleanup_old_incidents(self, dry_run: bool = False):
    """
    Delete resolved/closed incidents older than the configured data_retention_days.

    Runs daily at 02:00 UTC via Celery Beat.
    Reads ``general.data_retention_days`` from platform_settings (default 90 days).
    Only deletes incidents in 'resolved' or 'closed' lifecycle state; active
    incidents are never touched regardless of age.
    """
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as sql_text
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.models import PlatformSettingModel

    db = SessionLocal()
    try:
        # Read retention period from general settings
        retention_days = 90  # default
        try:
            row = db.get(PlatformSettingModel, "general.data_retention_days")
            if row and row.value:
                retention_days = max(7, int(row.value))  # floor at 7 days
        except Exception as _e:
            logger.warning("[Cleanup] Could not read data_retention_days: %s", _e)

        cutoff = _dt.utcnow() - _td(days=retention_days)
        logger.info(
            "[Cleanup] Running — cutoff=%s retention=%d days dry_run=%s",
            cutoff.date(), retention_days, dry_run,
        )

        if dry_run:
            count_row = db.execute(sql_text("""
                SELECT COUNT(*) FROM workflow_states
                WHERE lifecycle_state IN ('resolved', 'closed')
                  AND created_at < :cutoff
            """), {"cutoff": cutoff}).fetchone()
            count = count_row[0] if count_row else 0
            logger.info("[Cleanup] Dry run: would delete %d incidents", count)
            return {"status": "dry_run", "would_delete": count, "cutoff": cutoff.isoformat()}

        result = db.execute(sql_text("""
            DELETE FROM workflow_states
            WHERE lifecycle_state IN ('resolved', 'closed')
              AND created_at < :cutoff
        """), {"cutoff": cutoff})
        deleted = result.rowcount
        db.commit()

        logger.info("[Cleanup] Deleted %d old incidents (cutoff=%s)", deleted, cutoff.date())
        return {
            "status":          "ok",
            "deleted":         deleted,
            "cutoff":          cutoff.isoformat(),
            "retention_days":  retention_days,
        }

    except Exception as exc:
        logger.error("[Cleanup] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Monitoring events retention ───────────────────────────────────────────────

@app.task(bind=True, queue="default")
def cleanup_old_monitoring_events(self, dry_run: bool = False):
    """
    Delete monitoring_events rows older than general.monitoring_retention_days
    (default 30 days).  Qualified events that became incidents are retained
    indirectly via the workflow_states table — this only purges raw event rows.
    """
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as sql_text
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.models import PlatformSettingModel

    db = SessionLocal()
    try:
        retention_days = 30
        try:
            row = db.get(PlatformSettingModel, "general.monitoring_retention_days")
            if row and row.value:
                retention_days = max(7, int(row.value))
        except Exception as _e:
            logger.warning("[MonitoringCleanup] Could not read monitoring_retention_days: %s", _e)

        cutoff = _dt.utcnow() - _td(days=retention_days)
        logger.info(
            "[MonitoringCleanup] cutoff=%s retention=%d days dry_run=%s",
            cutoff.date(), retention_days, dry_run,
        )

        if dry_run:
            count_row = db.execute(sql_text(
                "SELECT COUNT(*) FROM monitoring_events WHERE created_at < :cutoff"
            ), {"cutoff": cutoff}).fetchone()
            count = count_row[0] if count_row else 0
            logger.info("[MonitoringCleanup] Dry run: would delete %d events", count)
            return {"status": "dry_run", "would_delete": count}

        result = db.execute(sql_text(
            "DELETE FROM monitoring_events WHERE created_at < :cutoff"
        ), {"cutoff": cutoff})
        deleted = result.rowcount
        db.commit()
        logger.info("[MonitoringCleanup] Deleted %d monitoring events", deleted)
        return {"status": "ok", "deleted": deleted, "retention_days": retention_days}

    except Exception as exc:
        logger.error("[MonitoringCleanup] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Audit log retention ────────────────────────────────────────────────────────

@app.task(bind=True, queue="default")
def cleanup_old_audit_logs(self, dry_run: bool = False):
    """
    Delete principal_audit_log entries older than 365 days.
    Security-conscious deployments should archive to cold storage before deletion.
    """
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as sql_text
    from agentic_os.db.database import SessionLocal

    AUDIT_RETENTION_DAYS = 365
    db = SessionLocal()
    try:
        cutoff = _dt.utcnow() - _td(days=AUDIT_RETENTION_DAYS)
        logger.info(
            "[AuditCleanup] cutoff=%s retention=%d days dry_run=%s",
            cutoff.date(), AUDIT_RETENTION_DAYS, dry_run,
        )

        if dry_run:
            count_row = db.execute(sql_text(
                "SELECT COUNT(*) FROM principal_audit_log WHERE ts < :cutoff"
            ), {"cutoff": cutoff}).fetchone()
            count = count_row[0] if count_row else 0
            logger.info("[AuditCleanup] Dry run: would delete %d entries", count)
            return {"status": "dry_run", "would_delete": count}

        result = db.execute(sql_text(
            "DELETE FROM principal_audit_log WHERE ts < :cutoff"
        ), {"cutoff": cutoff})
        deleted = result.rowcount
        db.commit()
        logger.info("[AuditCleanup] Deleted %d audit log entries", deleted)
        return {"status": "ok", "deleted": deleted, "retention_days": AUDIT_RETENTION_DAYS}

    except Exception as exc:
        logger.error("[AuditCleanup] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Target lock sweep ──────────────────────────────────────────────────────────

@app.task(bind=True, queue="default")
def cleanup_expired_target_locks(self):
    """
    Deletes target_locks rows past their TTL.

    The TTL (15 min, set when ToolRegistryAgent acquires the lock) is a
    crash-safety valve, not the normal release path — normal release happens
    in ToolRegistryAgent's finally block the moment the step loop ends. This
    sweep only matters when a worker died (OOM, pod eviction, hard kill)
    while holding a lease, which would otherwise strand that target locked
    forever and block all future remediation against it.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import DistributedLockRepository

    db = SessionLocal()
    try:
        deleted = DistributedLockRepository(db).delete_expired()
        if deleted:
            logger.info("[TargetLockSweep] Reclaimed %d expired target lock(s)", deleted)
        return {"status": "ok", "deleted": deleted}
    except Exception as exc:
        logger.error("[TargetLockSweep] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Stuck-approval safety net ───────────────────────────────────────────────────

@app.task(bind=True, queue="default")
def resume_stuck_approvals(self):
    """
    Finds incidents stuck in 'approved' with no progress for 3+ minutes —
    meaning resume_workflow_task was supposed to fire after the approval
    decision but never actually ran (broker hiccup, backend restart at the
    wrong moment, worker crash). Unlike awaiting_manual, 'approved' has no
    operator-facing recovery button, so without this sweep the incident is
    stuck silently until someone notices and re-queues it by hand.

    Re-fires the resume task using the most recently approved approval for
    that workflow, up to RESUME_RETRY_CAP attempts. Past the cap, escalates
    to awaiting_manual instead of retrying forever, so a human eventually
    sees it.
    """
    from sqlalchemy import text as sql_text
    from agentic_os.db.database import SessionLocal

    STUCK_AFTER_MINUTES = 3
    RESUME_RETRY_CAP = 3

    db = SessionLocal()
    retried = 0
    escalated = 0
    try:
        # ── Retry candidates: still under the cap ──────────────────────────
        candidates = db.execute(sql_text("""
            SELECT ws.workflow_id, a.approval_id
            FROM workflow_states ws
            JOIN LATERAL (
                SELECT approval_id FROM approvals
                WHERE workflow_id = ws.workflow_id AND status = 'approved'
                ORDER BY decided_at DESC LIMIT 1
            ) a ON true
            WHERE ws.lifecycle_state = 'approved'
              AND ws.updated_at < now() - interval '3 minutes'
              AND ws.resume_retry_count < :retry_cap
        """), {"retry_cap": RESUME_RETRY_CAP}).fetchall()

        for workflow_id, approval_id in candidates:
            new_count_row = db.execute(sql_text("""
                UPDATE workflow_states
                SET resume_retry_count = resume_retry_count + 1, updated_at = now()
                WHERE workflow_id = :wf_id
                RETURNING resume_retry_count
            """), {"wf_id": str(workflow_id)}).fetchone()
            new_count = new_count_row[0] if new_count_row else None

            from agentic_os.db.models import IncidentNoteModel
            db.add(IncidentNoteModel(
                workflow_id=workflow_id,
                author="system",
                note_type="system",
                body=(
                    f"⏳ Resume did not progress this incident within "
                    f"{STUCK_AFTER_MINUTES} minutes of approval — auto-retrying "
                    f"(attempt {new_count}/{RESUME_RETRY_CAP})."
                ),
            ))
            db.commit()

            resume_workflow_task.delay(
                workflow_id=str(workflow_id), approval_id=str(approval_id),
            )
            logger.info(
                "[ResumeStuckApprovals] Re-fired resume for %s (attempt %d/%d)",
                workflow_id, new_count, RESUME_RETRY_CAP,
            )
            retried += 1

        # ── Escalation candidates: cap exhausted ───────────────────────────
        exhausted = db.execute(sql_text("""
            SELECT workflow_id FROM workflow_states
            WHERE lifecycle_state = 'approved'
              AND updated_at < now() - interval '3 minutes'
              AND resume_retry_count >= :retry_cap
        """), {"retry_cap": RESUME_RETRY_CAP}).fetchall()

        for (workflow_id,) in exhausted:
            db.execute(sql_text("""
                UPDATE workflow_states
                SET lifecycle_state = 'awaiting_manual',
                    remediation_outcome = 'escalated',
                    updated_at = now()
                WHERE workflow_id = :wf_id
            """), {"wf_id": str(workflow_id)})

            from agentic_os.db.models import IncidentNoteModel
            db.add(IncidentNoteModel(
                workflow_id=workflow_id,
                author="system",
                note_type="system",
                body=(
                    f"⛔ Resume still didn't progress this incident after "
                    f"{RESUME_RETRY_CAP} auto-retries — escalated to manual "
                    f"review. This usually means the Celery broker/worker was "
                    f"unavailable when the approval was decided; check worker "
                    f"health, then use Retry Automation once confirmed healthy."
                ),
            ))
            db.commit()
            logger.warning(
                "[ResumeStuckApprovals] Escalated %s to awaiting_manual — "
                "retry cap (%d) exhausted", workflow_id, RESUME_RETRY_CAP,
            )
            escalated += 1

        return {"status": "ok", "retried": retried, "escalated": escalated}
    except Exception as exc:
        logger.error("[ResumeStuckApprovals] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Platform Intelligence — scheduled analysis ─────────────────────────────────
#
# Celery Beat's crontab schedules are fixed at module load (the same limitation
# general.backup_schedule already has — changing the stored cron string requires
# a backend/celery_beat restart to take effect). To let platform_intelligence.
# analysis_schedule be genuinely settings-driven without a restart, this task is
# checked frequently (every 10 minutes) and does its own cron-field matching
# against a trailing window, rather than relying on an exact-minute match — which
# would be vulnerable to beat's check times drifting off the target minute.

def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if part.startswith("*/"):
            try:
                if value % int(part[2:]) == 0:
                    return True
            except (ValueError, ZeroDivisionError):
                continue
        elif "-" in part:
            try:
                lo, hi = part.split("-")
                if int(lo) <= value <= int(hi):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                continue
    return False


def _cron_matches_at(cron_expr: str, when) -> bool:
    try:
        minute, hour, dom, month, dow = cron_expr.strip().split()
    except (ValueError, AttributeError):
        return False
    return (
        _cron_field_matches(minute, when.minute)
        and _cron_field_matches(hour, when.hour)
        and _cron_field_matches(dom, when.day)
        and _cron_field_matches(month, when.month)
        and _cron_field_matches(dow, when.isoweekday() % 7)  # cron: 0/7=Sunday
    )


@app.task(bind=True, queue="default")
def run_scheduled_platform_intelligence_analysis(self, window_minutes: int = 15):
    """
    Runs every 10 minutes (see beat_schedule below). Checks platform_intelligence.
    analysis_schedule_enabled and .analysis_schedule, and triggers a full
    TuningAgent.run_analysis() if the cron expression matched any minute in the
    last `window_minutes` and we haven't already run for that window — the window
    (wider than the 10-minute check interval) absorbs any drift in exactly when
    Beat checks, so an exact-minute cron entry (e.g. "0 6 * * *") isn't missed.
    """
    from datetime import datetime as _dt, timedelta as _td
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.models import PlatformSettingModel
    from agentic_os.agents.tuning_agent import TuningAgent

    db = SessionLocal()
    try:
        enabled_row = db.get(PlatformSettingModel, "platform_intelligence.analysis_schedule_enabled")
        if not enabled_row or enabled_row.value.lower() not in ("true", "1", "yes"):
            return {"status": "skipped", "reason": "schedule disabled"}

        cron_row = db.get(PlatformSettingModel, "platform_intelligence.analysis_schedule")
        cron_expr = cron_row.value if cron_row else "0 6 * * *"

        last_run_row = db.get(PlatformSettingModel, "platform_intelligence.last_scheduled_analysis_at")
        now = _dt.utcnow()
        if last_run_row and last_run_row.value:
            try:
                last_run = _dt.fromisoformat(last_run_row.value)
                if (now - last_run) < _td(minutes=window_minutes - 1):
                    return {"status": "skipped", "reason": "already ran within this window"}
            except ValueError:
                pass

        due = any(
            _cron_matches_at(cron_expr, now - _td(minutes=m))
            for m in range(window_minutes + 1)
        )
        if not due:
            return {"status": "skipped", "reason": "not due"}

        logger.info("[PI-Schedule] Cron '%s' due — running analysis", cron_expr)
        result = TuningAgent(db).run_analysis(trigger="scheduled")

        now_iso = now.isoformat()
        if last_run_row:
            last_run_row.value = now_iso
        else:
            db.add(PlatformSettingModel(
                key="platform_intelligence.last_scheduled_analysis_at",
                value=now_iso, value_type="str", category="platform_intelligence",
                label="Last Scheduled Analysis At", description="Internal bookkeeping — not user-editable.",
            ))
        db.commit()
        return {"status": "ok", **result}
    except Exception as exc:
        logger.error("[PI-Schedule] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Platform Intelligence — on-demand analysis (Run Analysis / Force Refresh) ─

@app.task(bind=True, queue="default")
def run_platform_intelligence_analysis_task(self, period_days: int = 30, ignore_cooldown: bool = False, trigger: str = "manual"):
    """
    Background job backing the "Run Analysis Now" / "Force Refresh" buttons.
    Moved off the request thread because a large incident window can take long
    enough to analyze (LLM call + aggregation over months of data) that holding
    an HTTP request open for it risks gateway timeouts. The frontend dispatches
    this via POST /platform-intelligence/analyze (gets a job_id back) and polls
    GET /platform-intelligence/analyze/status/{job_id} until it's SUCCESS/FAILURE.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.agents.tuning_agent import TuningAgent

    db = SessionLocal()
    try:
        result = TuningAgent(db).run_analysis(
            period_days=period_days,
            ignore_cooldown=ignore_cooldown,
            trigger=trigger,
        )
        return {"status": "ok", **result}
    except Exception as exc:
        logger.error("[PI-Analyze] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Platform Intelligence — recommendation outcome verification ───────────────

@app.task(bind=True, queue="default")
def verify_recommendation_outcomes(self):
    """
    Closes the loop on applied Platform Intelligence recommendations: for any
    rec applied >= VERIFICATION_DELAY_DAYS ago without a verified outcome yet,
    re-measure its targeted metric and record whether it actually improved.
    This is what lets a parameter earn (or lose) auto-apply trust over time —
    see TuningAgent._verify_applied_recommendations / _is_pattern_auto_apply_eligible.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.agents.tuning_agent import TuningAgent

    db = SessionLocal()
    try:
        verified = TuningAgent(db)._verify_applied_recommendations()
        logger.info("[PI-Verify] Verified outcome for %d recommendation(s)", verified)
        return {"status": "ok", "verified": verified}
    except Exception as exc:
        logger.error("[PI-Verify] Failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Celery Beat Schedule ───────────────────────────────────────────────────────

from celery.schedules import crontab  # noqa: E402 — intentional late import

app.conf.beat_schedule = {
    # Monitoring events retention — raw events older than 30 days (configurable)
    # Note: These are low-level raw events; incidents themselves are retained indefinitely
    "cleanup-old-monitoring-events-daily": {
        "task":    "agentic_os.tasks.celery_app.cleanup_old_monitoring_events",
        "schedule": crontab(hour=2, minute=15),  # 02:15 UTC daily
        "options": {"queue": "default"},
    },
    # Audit log retention — principal audit entries older than 365 days
    "cleanup-old-audit-logs-weekly": {
        "task":    "agentic_os.tasks.celery_app.cleanup_old_audit_logs",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),  # Sundays 03:00 UTC
        "options": {"queue": "default"},
    },
    # Target-lock TTL sweep — crash-safety valve for the per-target remediation
    # lease (ToolRegistryAgent). Normal release is via finally; this only
    # reclaims locks whose holder crashed mid-remediation. TTL is 15 min, so
    # checking every 5 min bounds a stranded lock to ~20 min worst case.
    "cleanup-expired-target-locks": {
        "task":    "agentic_os.tasks.celery_app.cleanup_expired_target_locks",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "default"},
    },
    # Stuck-approval safety net — catches incidents where resume_workflow_task
    # never fired after approval was decided (broker hiccup, backend restart
    # mid-request, worker crash). Checked every 2 min; the task itself only
    # acts on incidents stuck 3+ min, so this just bounds how quickly a stuck
    # one gets noticed.
    "resume-stuck-approvals": {
        "task":    "agentic_os.tasks.celery_app.resume_stuck_approvals",
        "schedule": crontab(minute="*/2"),
        "options": {"queue": "default"},
    },
    # Rotating backup task — configured via platform settings
    # Enabled: general.backup_enabled (default: true)
    # Frequency: general.backup_schedule (default: "0 1 * * *" = 01:00 UTC daily)
    # Retention: general.backup_retention_days (default: 7 days)
    "platform-backup-rotating": {
        "task":    "backup.run",
        "schedule": crontab(hour=1, minute=0),   # 01:00 UTC daily (configurable via settings)
        "options": {"queue": "default"},
    },
    # Platform Intelligence — close the loop on applied recommendations so
    # parameters can earn (or lose) auto-apply trust without anyone manually
    # triggering analysis.
    "verify-recommendation-outcomes-daily": {
        "task":    "agentic_os.tasks.celery_app.verify_recommendation_outcomes",
        "schedule": crontab(hour=4, minute=0),   # 04:00 UTC daily
        "options": {"queue": "default"},
    },
    # Platform Intelligence — scheduled analysis check. Off by default
    # (platform_intelligence.analysis_schedule_enabled); checked every 10 minutes
    # so the configured cron (platform_intelligence.analysis_schedule) is honored
    # without requiring a restart when the cron string itself changes.
    "platform-intelligence-scheduled-analysis-check": {
        "task":    "agentic_os.tasks.celery_app.run_scheduled_platform_intelligence_analysis",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "default"},
    },
}


@app.task(bind=True, queue="default")
def generate_missing_summaries(self, limit: int = None, regenerate_short: bool = True):
    """
    Generate platform context summaries for incidents without them.

    Args:
        limit: Optional cap on number of incidents to process.
        regenerate_short: Also regenerate summaries that are shorter than 120 chars
                          (i.e. the old simple-fallback format like
                          'high_cpu on service (Severity: high)').
    """
    from sqlalchemy import and_, or_, func
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.models import WorkflowStateModel, WorkflowType
    from agentic_os.core.models import LifecycleState
    from agentic_os.services.platform_context_service import get_platform_context_service
    from datetime import datetime

    logger.info(
        f"[{self.request.id}] Starting summary generation "
        f"(regenerate_short={regenerate_short})"
    )

    db = SessionLocal()
    try:
        service = get_platform_context_service()
        batch_size = 50
        stats = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
        }

        # Find incidents without summaries (or with short fallback summaries)
        SHORT_SUMMARY_THRESHOLD = 120
        if regenerate_short:
            # Match: NULL summary OR summary shorter than threshold
            summary_filter = or_(
                WorkflowStateModel.summary.is_(None),
                func.length(WorkflowStateModel.summary) < SHORT_SUMMARY_THRESHOLD,
            )
        else:
            summary_filter = WorkflowStateModel.summary.is_(None)

        query = db.query(WorkflowStateModel).filter(
            and_(
                WorkflowStateModel.workflow_type == WorkflowType.INCIDENT,
                summary_filter,
            )
        ).order_by(WorkflowStateModel.created_at.desc())

        if limit:
            query = query.limit(limit)

        total_to_process = query.count()
        logger.info(f"Found {total_to_process} incidents needing summary generation")

        # Process in batches
        offset = 0
        while offset < total_to_process:
            batch = query.offset(offset).limit(batch_size).all()
            if not batch:
                break

            logger.info(f"Processing batch: {offset}-{offset + len(batch)} of {total_to_process}")

            for incident in batch:
                try:
                    # Generate platform context summary
                    summary = service.generate_summary(incident)

                    if summary:
                        # Update database
                        incident.summary = summary
                        incident.summary_generated_at = datetime.utcnow()
                        db.add(incident)
                        db.commit()
                        stats["successful"] += 1
                        logger.debug(f"Generated summary for {incident.workflow_id}")
                    else:
                        stats["failed"] += 1
                        logger.warning(f"Failed to generate summary for {incident.workflow_id}")

                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"Error processing incident {incident.workflow_id}: {e}")
                    db.rollback()

                stats["total_processed"] += 1

            offset += batch_size

        logger.info(f"Summary generation complete: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Error in summary generation task: {str(e)}", exc_info=True)
        return {"status": "error", "reason": str(e)}
    finally:
        db.close()


@app.task(bind=True, queue="workflows")
def resume_workflow_task(self, workflow_id: str, approval_id: str):
    """Resume workflow execution after approval is granted"""
    from uuid import UUID
    from sqlalchemy.orm import Session
    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import WorkflowRepository
    from agentic_os.core.workflow_engine import WorkflowEngine
    from agentic_os.core.definitions import WorkflowDefinitionLoader
    from agentic_os.core.models import WorkflowType, LifecycleState
    from agentic_os.db.models import IncidentNoteModel
    from agentic_os.bus.postgres_bus import PostgresEventBus
    from agentic_os.agents.registry import register_all_agents

    logger.info(f"[{self.request.id}] Resuming workflow {workflow_id} after approval {approval_id}")

    try:
        # Initialize database and event bus
        db = SessionLocal()
        _db_url = os.getenv("DATABASE_URL", "postgresql://postgres:agentic_os@postgres:5432/agentic_os")
        event_bus = PostgresEventBus(_db_url)

        # Initialize workflow engine
        engine = WorkflowEngine(event_bus, db)
        register_all_agents(engine)

        # Load workflow state
        repo = WorkflowRepository(db)
        state = repo.get(UUID(workflow_id))

        if not state:
            logger.error(f"Workflow {workflow_id} not found")
            return {"status": "error", "reason": "Workflow not found"}

        # Guard: if the workflow already reached a terminal state (e.g. the watcher
        # sent an all-clear that resolved the incident while the approval was in flight),
        # do NOT attempt remediation.  Executing on a self-healed system can cause harm
        # (e.g. restarting a service that just recovered on its own).
        TERMINAL_STATES = {"resolved", "closed"}
        if state.lifecycle_state.value in TERMINAL_STATES:
            logger.info(
                f"[RESUME] Skipping remediation for workflow {workflow_id} — "
                f"already in terminal state '{state.lifecycle_state.value}'. "
                f"Condition likely cleared before approval was processed."
            )
            return {
                "status": "skipped",
                "reason": f"Workflow already '{state.lifecycle_state.value}' — remediation not needed",
            }

        # Load workflow definition
        workflows_dir = "/app/workflows"
        loader = WorkflowDefinitionLoader(workflows_dir)
        definition = loader.load_definition(WorkflowType(state.workflow_type), "v1")

        if not definition:
            logger.error(f"Workflow definition not found for {state.workflow_type}")
            return {"status": "error", "reason": "Workflow definition not found"}

        # Mark state as approved before resuming
        state.add_trace(f"[APPROVAL GRANTED] Resuming workflow execution after approval {approval_id}")
        state.governance_decision = "approved"

        # Resume from the step recorded by PolicyBrokerAgent (defaults to tool_registry).
        # CRITICAL: must NOT restart from definition.start_step (sentinel) — that would
        # re-run all prior agents, causing PolicyBroker to set pending_approval again.
        resume_from = state.context.get("resume_from_step", "tool_registry")
        logger.info(f"[{self.request.id}] Resuming workflow from step: {resume_from}")
        _loop = asyncio.new_event_loop()
        try:
            final_state = _loop.run_until_complete(engine.execute(definition, state, start_step=resume_from))
        finally:
            _loop.close()

        logger.info(
            f"[{self.request.id}] Workflow resumed and completed with state: {final_state.lifecycle_state.value}"
        )

        # ── Summary generation (resume path, Fix 2) ──────────────────────────
        # Same approach as execute_workflow_task: write a fast platform-context
        # summary now, dispatch enrich_incident_async for LLM enrichment.
        # waiting_approval is not in _ENRICH_STATES_R — no LLM for pause states.
        from datetime import datetime as _dt
        from agentic_os.services.summary_service import get_summary_service
        from agentic_os.services.platform_context_service import get_platform_context_service, PlatformContextService

        _ENRICH_STATES_R = {"resolved", "failed", "awaiting_manual", "monitoring", "closed"}
        lc_val_r       = final_state.lifecycle_state.value
        svc_r          = None
        full_context_r = {}

        try:
            ctx_r          = final_state.context or {}
            sentinel_r     = ctx_r.get("sentinel", {})
            cmdb_r         = ctx_r.get("cmdb", {})
            risk_r         = ctx_r.get("risk", {})
            proposal_r     = ctx_r.get("proposal", {})
            governance_r   = ctx_r.get("governance", {})
            alert_r        = ctx_r.get("alert_payload", {})
            ap_r           = sentinel_r.get("alert_payload", alert_r) if sentinel_r else alert_r
            exec_results_r = ctx_r.get("execution_results", [])
            verif_r        = ctx_r.get("verification", {})

            verif_str_r = ""
            if verif_r:
                overall_r = verif_r.get("overall_success")
                verif_str_r = "Success" if overall_r else ("Failed" if overall_r is False else "")
                vrs = verif_r.get("verification_results", [])
                if vrs:
                    verif_str_r += " — " + "; ".join(
                        f"{v.get('step_name','?')}: {v.get('message','')}" for v in vrs[:3]
                    )

            full_context_r = {
                "event_type":             ap_r.get("type") or alert_r.get("type", "Unknown"),
                "description":            ap_r.get("description") or alert_r.get("description", ""),
                "resource":               cmdb_r.get("resource_name") or alert_r.get("resource_name", "Unknown"),
                "environment":            cmdb_r.get("environment", "unknown"),
                "severity":               str(final_state.severity.value if final_state.severity else alert_r.get("severity", "unknown")),
                "risk_score":             round(float(final_state.risk_score or risk_r.get("risk_score", 0) or 0), 1),
                "blast_radius":           risk_r.get("blast_radius", "N/A"),
                "remediation_complexity": risk_r.get("remediation_complexity", ""),
                "anomaly_process":        ap_r.get("anomaly_process") or alert_r.get("anomaly_process", ""),
                "anomaly_metrics":        ap_r.get("anomaly_metrics") or ap_r.get("syscall_rate", ""),
                "runbook":                proposal_r.get("runbook_name", ""),
                "execution_results":      exec_results_r,
                "verification":           verif_str_r,
                "lifecycle_state":        lc_val_r,
                "impacted_services":      cmdb_r.get("impacted_services", []),
                "governance_decision":    final_state.governance_decision or "",
                "approval_required":      governance_r.get("approval_required", False),
                "governance_notes":       governance_r.get("decision_notes", ""),
                "matching_policies":      governance_r.get("matching_policies", []),
            }

            svc_r  = get_summary_service()
            plat_r = get_platform_context_service()

            # Fast synchronous platform-context summary written before repo.save()
            current_db_r = repo.get(UUID(workflow_id))
            new_sum_r = None
            try:
                if current_db_r:
                    new_sum_r = plat_r.generate_summary(current_db_r)
                if not new_sum_r:
                    new_sum_r = PlatformContextService.build_progressive_summary(final_state)
            except Exception as _pcs_r_err:
                logger.warning(
                    f"[{self.request.id}] Platform-context summary (resume) failed: {_pcs_r_err}"
                )
            if not new_sum_r:
                new_sum_r = (
                    f"{full_context_r.get('event_type', 'Incident').replace('_', ' ').title()} "
                    f"on {full_context_r.get('resource', 'Unknown')}"
                )

            final_state.summary            = new_sum_r
            final_state.technical_summary  = ""   # populated later by enrich_incident_async
            final_state.summary_generated_at = _dt.utcnow()
            logger.info(
                f"[{self.request.id}] Platform-context summary ready (resume) "
                f"({len(new_sum_r)} chars) — LLM enrichment queued async"
            )
        except Exception as _summary_r_err:
            logger.error(
                f"[{self.request.id}] Summary generation (resumed) failed: {_summary_r_err}",
                exc_info=True,
            )

        # Persist final state to database
        repo.save(final_state)
        logger.info(f"[{self.request.id}] Resumed workflow state persisted to database")

        # ── Fire-and-forget LLM enrichment (Fix 1, resume path) ──────────────
        if svc_r is not None and svc_r.is_provider_configured() and lc_val_r in _ENRICH_STATES_R:
            try:
                enrich_incident_async.apply_async(
                    args=[str(workflow_id), full_context_r, lc_val_r],
                    queue="workflows",
                    countdown=0,
                )
                logger.info(
                    f"[{self.request.id}] LLM enrichment queued (resumed) for "
                    f"{workflow_id[:8]} ({lc_val_r})"
                )
            except Exception as _enrich_r_err:
                logger.warning(
                    f"[{self.request.id}] LLM enrichment dispatch (resume) failed "
                    f"(non-fatal): {_enrich_r_err}"
                )

        # ── Storm parent child cascade ────────────────────────────────────────
        # Resolve any children held in storm_hold under this workflow.
        # We do NOT check context.is_storm_parent — the pipeline overwrites
        # context, stripping storm metadata.  storm_id column is set once at
        # storm creation and is never modified by the pipeline, so it is the
        # only reliable signal.  For non-storm incidents storm_id is NULL →
        # WHERE matches 0 rows → no-op.
        _resume_lc = final_state.lifecycle_state.value
        if _resume_lc in ("resolved", "closed"):
            try:
                from sqlalchemy import text as _sql_cascade
                from datetime import datetime as _dt_cascade
                _cascade_n = db.execute(_sql_cascade("""
                    UPDATE workflow_states
                    SET lifecycle_state   = 'resolved',
                        resolution_source = 'manual',
                        resolution_notes  = :note,
                        updated_at        = :now
                    WHERE storm_id::text = :parent_id
                      AND workflow_id::text != :parent_id
                      AND lifecycle_state NOT IN ('resolved', 'closed')
                """), {
                    "parent_id": workflow_id,
                    "note": (
                        "Resolved as part of storm coordinated response "
                        "(parent approved and remediated)."
                    ),
                    "now": _dt_cascade.utcnow(),
                }).rowcount
                if _cascade_n:
                    db.commit()
                    logger.info(
                        f"[{self.request.id}] Storm child cascade: resolved "
                        f"{_cascade_n} child incident(s) after storm parent resolved"
                    )
            except Exception as _cascade_err:
                logger.warning(
                    f"[{self.request.id}] Storm child cascade failed "
                    f"(non-fatal): {_cascade_err}"
                )

        # Auto system note (resolved, awaiting_manual, diagnostics_only)
        _write_system_note(db, workflow_id, final_state)

        # Record runbook execution feedback — THIS is where the actual remediation runs
        # (ToolRegistryAgent + VerifierAgent execute here, after approval).
        # execute_workflow_task only reaches waiting_approval so its feedback call always
        # skips (remediation_outcome = "pending" at that point).
        try:
            from agentic_os.services.runbook_feedback import record_from_workflow
            record_from_workflow(db, final_state)
        except Exception as _fb_err:
            logger.warning(f"[{self.request.id}] Runbook feedback (resume) failed (non-fatal): {_fb_err}")

        # Slack resolved / terminal-state notification.
        _NOTIFY_TERMINAL_R = {"resolved", "deployed", "rolled_back", "rejected", "failed", "awaiting_manual"}
        _lc_val_r = final_state.lifecycle_state.value
        if _lc_val_r in _NOTIFY_TERMINAL_R:
            try:
                from agentic_os.services.notifications import notify_incident_resolved
                _inc_num_r  = str(getattr(final_state, "incident_number_str", "") or "")
                _title_r    = str(getattr(final_state, "title", "") or "Untitled")
                _sev_r      = str(getattr(final_state.severity, "value", str(final_state.severity or "unknown")))
                _rsk_r      = float(final_state.risk_score) if final_state.risk_score else None
                _outcome_r  = str(final_state.remediation_outcome or "") or None
                notify_incident_resolved(_inc_num_r, _title_r, _sev_r, _lc_val_r, _rsk_r, _outcome_r)
            except Exception as _notif_r_err:
                logger.warning(f"[{self.request.id}] Slack notify (resume) failed (non-fatal): {_notif_r_err}")

        # Auto-update ServiceNow incident on terminal state (fire-and-forget)
        try:
            from agentic_os.tasks.snow_sync import snow_push_incident_state
            snow_push_incident_state.delay(workflow_id, final_state.lifecycle_state.value)
        except Exception as _sn_err:
            logger.warning(f"[{self.request.id}] SN state push (resume) scheduling failed: {_sn_err}")

        return {
            "status": "completed",
            "workflow_id": workflow_id,
            "lifecycle_state": final_state.lifecycle_state.value,
            "traces": len(final_state.reasoning_trace),
        }

    except Exception as e:
        logger.error(f"[{self.request.id}] Workflow resumption failed: {str(e)}", exc_info=True)
        # Fallback: transition workflow to AWAITING_MANUAL so operator can intervene
        try:
            from uuid import UUID
            from agentic_os.core.models import LifecycleState
            from agentic_os.db.repositories import WorkflowRepository
            from agentic_os.db.models import IncidentNoteModel

            if 'db' in locals() and 'workflow_id' in locals():
                repo = WorkflowRepository(db)
                state = repo.get(UUID(workflow_id))
                if state:
                    # Record the error that caused the crash
                    error_msg = f"{type(e).__name__}: {str(e)[:500]}"
                    logger.info(f"[{self.request.id}] Transitioning workflow {workflow_id[:8]} to AWAITING_MANUAL due to resumption failure: {error_msg}")

                    # Transition to AWAITING_MANUAL
                    state.lifecycle_state = LifecycleState.AWAITING_MANUAL
                    repo.save(state)

                    # Add system note documenting the crash
                    try:
                        db.add(IncidentNoteModel(
                            workflow_id=UUID(workflow_id),
                            author="system",
                            note_type="system",
                            body=f"Workflow resumption crashed: {error_msg}\n\nOperator manual intervention required.",
                        ))
                        db.commit()
                    except Exception as note_err:
                        logger.warning(f"[{self.request.id}] Could not write error note: {note_err}")
                        db.rollback()
        except Exception as fallback_err:
            logger.error(f"[{self.request.id}] Fallback to AWAITING_MANUAL (resume) failed: {fallback_err}", exc_info=True)

        # Fallback: transition workflow to AWAITING_MANUAL so operator can intervene
        try:
            if 'db' in locals() and 'repo' in locals():
                state = repo.get(UUID(workflow_id))
                if state:
                    error_msg = f"{type(e).__name__}: {str(e)[:500]}"
                    logger.info(f"[{self.request.id}] Transitioning {workflow_id[:8]} to AWAITING_MANUAL: {error_msg}")
                    state.lifecycle_state = LifecycleState.AWAITING_MANUAL
                    repo.save(state)
                    try:
                        db.add(IncidentNoteModel(
                            workflow_id=UUID(workflow_id),
                            author="system",
                            note_type="system",
                            body=f"Workflow resumption crashed: {error_msg}\n\nOperator manual intervention required.",
                        ))
                        db.commit()
                    except Exception as _note_err:
                        logger.warning(f"[{self.request.id}] Could not write error note: {_note_err}")
                        db.rollback()
        except Exception as fallback_err:
            logger.error(f"[{self.request.id}] Fallback to AWAITING_MANUAL failed: {fallback_err}", exc_info=True)

        return {"status": "error", "reason": str(e)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# Import asyncio for execute_workflow_task
import asyncio


# ── Storm Phase 2: Dependency Expansion ──────────────────────────────────────

def _phase2_dependency_expansion(
    db,
    storm_id_uuid,
    storm_resource_names: list,
    window_minutes: int = 10,
) -> int:
    """
    Phase 2 storm expansion — adopt incidents on downstream-dependent services.

    After the initial storm is formed from Phase 1 (time window + event type
    similarity), this pass uses the Neo4j CMDB topology to find services that
    DEPEND ON the storm's resources.  Any open incidents on those dependent
    services within the storm time window are adopted into the storm.

    Typical scenario:
        Phase 1 clusters: db-primary, cache-cluster, msg-broker (data tier)
        Phase 2 finds:    api-gateway, payment-service, auth-service (app tier)
                          which all have DEPENDS_ON edges to the data tier CIs.

    Args:
        db:                   SQLAlchemy session (will commit internally).
        storm_id_uuid:        UUID object for the storm parent.
        storm_resource_names: Resources already in the storm (Phase 1).
        window_minutes:       How far back to search for dependent incidents.

    Returns:
        Number of newly adopted child incidents.
    """
    import json as _json
    import os as _os
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as sql_text

    storm_id_str = str(storm_id_uuid)
    logger.info(
        f"[STORM P2] Running dependency expansion for storm {storm_id_str[:8]}, "
        f"resources={storm_resource_names}, window={window_minutes}m"
    )

    # ── 1. Query Neo4j for downstream dependents of each storm resource ────────
    try:
        from agentic_os.services.cmdb import CMDBService
        cmdb = CMDBService(
            uri=_os.getenv("NEO4J_URI",      "bolt://neo4j:7687"),
            user=_os.getenv("NEO4J_USER",     "neo4j"),
            password=_os.getenv("NEO4J_PASSWORD"),
        )
    except Exception as _cmdb_err:
        logger.warning(f"[STORM P2] Cannot connect to CMDB: {_cmdb_err}")
        return 0

    storm_resource_set = set(storm_resource_names)
    candidate_resources: set = set()

    for resource in storm_resource_names:
        try:
            impacted = cmdb.get_impacted_services(resource)
            for svc in impacted:
                name = svc.get("name")
                if name and name not in storm_resource_set:
                    candidate_resources.add(name)
                    logger.debug(
                        f"[STORM P2] {resource} → dependent: {name} "
                        f"({svc.get('type', '?')})"
                    )
        except Exception as _q_err:
            logger.warning(f"[STORM P2] CMDB query failed for {resource}: {_q_err}")

    if not candidate_resources:
        logger.info("[STORM P2] No downstream dependents found — expansion skipped")
        return 0

    logger.info(f"[STORM P2] Downstream candidates: {sorted(candidate_resources)}")

    # ── 2. Find open incidents on those resources within the window ────────────
    # Build a parameterised IN clause to avoid string interpolation of user data.
    candidate_list = list(candidate_resources)
    cutoff = _dt.utcnow() - _td(minutes=window_minutes)
    in_params = {f"r{i}": r for i, r in enumerate(candidate_list)}
    in_params["cutoff"] = cutoff
    in_clause = ", ".join(f":r{i}" for i in range(len(candidate_list)))

    candidate_rows = db.execute(sql_text(f"""
        SELECT
            workflow_id::text,
            COALESCE(
                context -> 'alert_payload' ->> 'resource_name',
                context -> 'sentinel' -> 'alert_payload' ->> 'resource_name',
                context -> 'cmdb' ->> 'resource_name'
            ) AS resource_name
        FROM workflow_states
        WHERE workflow_type = 'incident'
          AND lifecycle_state NOT IN ('resolved', 'closed', 'storm_hold')
          AND storm_id IS NULL
          AND (context ->> 'is_storm_parent' IS NULL
               OR (context ->> 'is_storm_parent')::boolean IS DISTINCT FROM true)
          AND created_at > :cutoff
          AND COALESCE(
                context -> 'alert_payload' ->> 'resource_name',
                context -> 'sentinel' -> 'alert_payload' ->> 'resource_name',
                context -> 'cmdb' ->> 'resource_name'
              ) IN ({in_clause})
    """), in_params).fetchall()

    if not candidate_rows:
        logger.info("[STORM P2] No qualifying downstream incidents found")
        return 0

    new_child_ids  = [r[0] for r in candidate_rows]
    new_resources  = list({r[1] for r in candidate_rows if r[1]})
    logger.info(
        f"[STORM P2] Found {len(new_child_ids)} downstream incident(s) "
        f"on: {new_resources}"
    )

    now = _dt.utcnow()

    # ── 3. Move them to storm_hold under the existing storm ────────────────────
    for child_id in new_child_ids:
        updated = db.execute(sql_text("""
            UPDATE workflow_states
            SET lifecycle_state = 'storm_hold',
                storm_id        = :storm_uuid,
                updated_at      = :now
            WHERE workflow_id::text = :child_id
              AND storm_id IS NULL            -- race guard
        """), {
            "storm_uuid": storm_id_str,
            "child_id":   child_id,
            "now":        now,
        }).rowcount
        if updated:
            logger.info(f"[STORM P2] Adopted child {child_id[:8]} into storm")

    # ── 4. Cancel any pending approvals for newly adopted children ─────────────
    if new_child_ids:
        id_literal = ", ".join(f"'{cid}'" for cid in new_child_ids)
        cancelled = db.execute(sql_text(f"""
            UPDATE approvals
            SET status         = 'cancelled',
                decided_at     = :now,
                decided_by     = 'system',
                decision_notes = 'Auto-cancelled — incident adopted into storm via Phase 2 dependency expansion'
            WHERE workflow_id::text IN ({id_literal})
              AND status = 'pending'
        """), {"now": now}).rowcount
        if cancelled:
            logger.info(f"[STORM P2] Cancelled {cancelled} child approval(s)")

    # ── 5. Update storm parent: children list + affected resources ─────────────
    # Build updated context in Python then write it back as a full replacement.
    # This avoids jsonb_set() which requires a jsonb column (ours is json).
    current_row = db.execute(sql_text("""
        SELECT context
        FROM workflow_states
        WHERE workflow_id::text = :storm_id
    """), {"storm_id": storm_id_str}).fetchone()

    n_total = len(new_child_ids)   # fallback if no row found
    n_res   = len(new_resources)
    if current_row:
        ctx               = current_row[0] or {}
        current_children  = ctx.get("storm_children", [])
        current_resources = ctx.get("storm_analysis", {}).get("affected_resources", [])

        updated_children  = list(set(current_children + new_child_ids))
        updated_resources = list(set(current_resources + new_resources))
        n_total = len(updated_children)
        n_res   = len(updated_resources)

        updated_ctx = dict(ctx)
        updated_ctx["storm_children"] = updated_children
        # Deep-copy the nested storm_analysis dict to avoid mutating the
        # original ctx object (dict(ctx) is a shallow copy).
        updated_ctx["storm_analysis"] = dict(ctx.get("storm_analysis", {}))
        updated_ctx["storm_analysis"]["affected_resources"] = updated_resources

        db.execute(sql_text("""
            UPDATE workflow_states
            SET context    = CAST(:ctx AS json),
                title      = :title,
                updated_at = :now
            WHERE workflow_id::text = :storm_id
        """), {
            "storm_id": storm_id_str,
            "ctx":      _json.dumps(updated_ctx),
            "title": (
                f"[STORM] Cascading Failure — {n_total} incidents "
                f"across {n_res} resource(s)"
            ),
            "now": now,
        })

    # ── 6. Add system note to storm parent ─────────────────────────────────────
    try:
        from agentic_os.db.models import IncidentNoteModel
        db.add(IncidentNoteModel(
            workflow_id=storm_id_uuid,
            author="storm_agent",
            note_type="system",
            body=(
                f"Phase 2 Dependency Expansion\n\n"
                f"CMDB topology identified {len(new_child_ids)} additional "
                f"incident(s) on downstream-dependent services:\n"
                + "\n".join(f"  - {r}" for r in new_resources)
                + f"\n\nThese services depend on the storm's root resources "
                f"({', '.join(storm_resource_names[:4])}"
                + (f" +{len(storm_resource_names)-4} more" if len(storm_resource_names) > 4 else "")
                + f") and are experiencing cascading failures.\n"
                f"Incidents adopted into storm_hold: {', '.join(new_child_ids)}"
            ),
        ))
    except Exception as _note_err:
        logger.warning(f"[STORM P2] System note failed: {_note_err}")

    db.commit()
    logger.info(
        f"[STORM P2] Expansion complete — adopted {len(new_child_ids)} incident(s), "
        f"storm now covers {len(updated_children)} children"
    )
    return len(new_child_ids)


# ── Storm Analysis Task ───────────────────────────────────────────────────────

@app.task(bind=True, queue="workflows", acks_late=True, reject_on_worker_lost=True,
          max_retries=2, default_retry_delay=30)
def execute_storm_analysis_task(self, incident_ids: list, resource_names: list, event_types: list):
    """
    Analyse a correlated event storm and create the storm parent incident.

    Called by the monitoring_events route (via background task) after 3+ incidents
    are detected within the storm window on 2+ different resources.

    Steps:
      1. Re-validate that the child incidents are still candidates (not yet resolved).
      2. Run StormAgent (Neo4j topology + LLM hypothesis).
      3. Create the storm parent incident (WorkflowState with is_storm_parent=True).
      4. Link child incidents to the parent (storm_id column).
      5. Transition children to storm_hold lifecycle state.
      6. Cancel any pending individual approval records for children.

    The storm parent sits in awaiting_manual.  The operator acts on it from
    the Event Storms page (resolve as storm OR handle individually).
    No CAB approval record is created — the Storms page is the decision point.
    """
    import json as _json
    from datetime import datetime as _dt
    from uuid import UUID
    from sqlalchemy import text as sql_text

    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import WorkflowRepository
    from agentic_os.core.models import WorkflowState, WorkflowType, LifecycleState
    from agentic_os.agents.storm_agent import StormAgent
    from agentic_os.services.enumeration_service import EnumerationService

    logger.info(
        f"[STORM TASK {self.request.id}] Analysing storm: "
        f"{len(incident_ids)} incidents, resources={resource_names}"
    )

    db = SessionLocal()
    try:
        # ── 1. Re-validate children ───────────────────────────────────────────
        # Some incidents may have resolved or been storm-linked since detection.
        placeholders = ", ".join(f"'{wid}'" for wid in incident_ids)
        live_rows = db.execute(sql_text(f"""
            SELECT workflow_id::text, lifecycle_state
            FROM workflow_states
            WHERE workflow_id::text IN ({placeholders})
              AND lifecycle_state NOT IN ('resolved', 'closed', 'storm_hold')
              AND storm_id IS NULL
              AND (context ->> 'is_storm_parent' IS NULL
                   OR (context ->> 'is_storm_parent')::boolean IS DISTINCT FROM true)
        """)).fetchall()

        live_ids = [r[0] for r in live_rows]
        if len(live_ids) < 2:
            logger.info(
                f"[STORM TASK] Storm dissolved — only {len(live_ids)} live child(ren) remain. "
                f"Skipping storm parent creation."
            )
            return {"status": "dissolved", "live_children": len(live_ids)}

        logger.info(f"[STORM TASK] {len(live_ids)} live children confirmed")

        # ── 1b. Advisory lock — serialise concurrent storm creation ───────────
        # celery_worker runs with concurrency=4 (default), so a burst of storm-
        # detection tasks that fire within milliseconds of each other can all
        # reach the merge-check simultaneously.  Without a lock they all see
        # storm_id IS NULL, none finds an existing parent, and every task creates
        # its own storm — leaving the earlier ones with 0 children because the
        # last task to commit overwrites the storm_id FK on every child row.
        #
        # pg_advisory_xact_lock() blocks until the advisory lock is free, then
        # proceeds.  It is transaction-level: PostgreSQL releases it automatically
        # on the next COMMIT or ROLLBACK — no explicit unlock needed, and it
        # cannot leak back into the connection pool the way a session-level lock
        # would.  The lock is released at the db.commit() call below, allowing
        # subsequent sibling tasks to proceed immediately.
        STORM_LOCK_KEY = 7331748  # arbitrary unique bigint for storm serialisation
        db.execute(sql_text("SELECT pg_advisory_xact_lock(:k)"), {"k": STORM_LOCK_KEY})
        logger.debug("[STORM TASK] Advisory lock acquired — proceeding with merge/create")

        # Re-validate once more now that we hold the lock; a sibling task may
        # have already enrolled these incidents into a storm while we waited.
        live_rows = db.execute(sql_text(f"""
            SELECT workflow_id::text, lifecycle_state
            FROM workflow_states
            WHERE workflow_id::text IN ({placeholders})
              AND lifecycle_state NOT IN ('resolved', 'closed', 'storm_hold')
              AND storm_id IS NULL
              AND (context ->> 'is_storm_parent' IS NULL
                   OR (context ->> 'is_storm_parent')::boolean IS DISTINCT FROM true)
        """)).fetchall()
        live_ids = [r[0] for r in live_rows]
        if len(live_ids) < 2:
            logger.info(
                f"[STORM TASK] Storm dissolved after lock — only {len(live_ids)} "
                f"live child(ren) remain (already handled by sibling task)."
            )
            return {"status": "dissolved", "live_children": len(live_ids)}
        logger.info(f"[STORM TASK] {len(live_ids)} live children confirmed (post-lock)")

        # ── 1c. Merge into existing storm if one was created in the last 5 min ─
        # Multiple storm tasks can race when events arrive in rapid succession.
        # If a storm parent already exists for this window, adopt the remaining
        # uncovered incidents into it rather than creating a duplicate storm.
        existing_storm_row = db.execute(sql_text("""
            SELECT workflow_id::text, context
            FROM workflow_states
            WHERE workflow_type  = 'incident'
              AND (context ->> 'is_storm_parent')::boolean = true
              AND lifecycle_state NOT IN ('resolved', 'closed')
              AND created_at > NOW() - INTERVAL '10 minutes'
            ORDER BY created_at DESC
            LIMIT 1
        """)).fetchone()

        if existing_storm_row:
            existing_storm_id  = existing_storm_row[0]
            existing_ctx       = existing_storm_row[1] or {}
            existing_children  = existing_ctx.get("storm_children", [])
            new_additions      = [wid for wid in live_ids
                                  if wid not in existing_children]

            if not new_additions:
                logger.info(
                    f"[STORM TASK] All incidents already covered by storm "
                    f"{existing_storm_id[:8]}. Nothing to do."
                )
                return {"status": "already_covered", "storm_id": existing_storm_id}

            logger.info(
                f"[STORM TASK] Merging {len(new_additions)} incident(s) into "
                f"existing storm {existing_storm_id[:8]}"
            )

            # Move new additions to storm_hold under the existing storm
            for child_id in new_additions:
                db.execute(sql_text("""
                    UPDATE workflow_states
                    SET lifecycle_state = 'storm_hold',
                        storm_id        = :storm_uuid,
                        updated_at      = :now
                    WHERE workflow_id = :child_id
                """), {
                    "storm_uuid": existing_storm_id,
                    "child_id":   child_id,
                    "now":        _dt.utcnow(),
                })

            # Cancel any pending individual approvals for the newly merged children.
            # The storm parent on the Event Storms page is now the decision point.
            if new_additions:
                merge_placeholders = ", ".join(f"'{wid}'" for wid in new_additions)
                merge_cancelled = db.execute(sql_text(f"""
                    UPDATE approvals
                    SET status         = 'cancelled',
                        decided_at     = :now,
                        decided_by     = 'system',
                        decision_notes = 'Auto-cancelled — incident merged into existing storm {existing_storm_id}'
                    WHERE workflow_id::text IN ({merge_placeholders})
                      AND status = 'pending'
                """), {"now": _dt.utcnow()}).rowcount
                if merge_cancelled:
                    logger.info(
                        f"[STORM TASK] Cancelled {merge_cancelled} approval(s) for "
                        f"incidents merged into storm {existing_storm_id[:8]}"
                    )

            # Update storm parent's children list and resource list in context.
            # Build the updated context in Python then write it back as a full
            # replacement — avoids jsonb_set() which requires a jsonb column.
            updated_children  = list(set(existing_children + new_additions))
            new_resource_names = list({
                *existing_ctx.get("storm_analysis", {}).get("affected_resources", []),
                *resource_names,
            })
            n_updated     = len(updated_children)
            n_res_updated = len(new_resource_names)

            updated_ctx = dict(existing_ctx)
            updated_ctx["storm_children"] = updated_children
            # Deep-copy nested dict to avoid mutating the original existing_ctx.
            updated_ctx["storm_analysis"] = dict(existing_ctx.get("storm_analysis", {}))
            updated_ctx["storm_analysis"]["affected_resources"] = new_resource_names

            # Recalculate risk score using updated child/resource counts.
            _merge_conf = existing_ctx.get("storm_analysis", {}).get("confidence", 0.5)
            _merge_risk = min(100, int(
                50
                + _merge_conf * 30
                + min(n_updated, 10) * 1
                + min(n_res_updated, 10) * 1
            ))
            if _merge_risk >= 75:
                _merge_risk_level = "critical"
            elif _merge_risk >= 50:
                _merge_risk_level = "high"
            else:
                _merge_risk_level = "medium"

            db.execute(sql_text("""
                UPDATE workflow_states
                SET context    = CAST(:ctx AS json),
                    title      = :title,
                    severity   = 'critical',
                    risk_score = :risk_score,
                    risk_level = :risk_level,
                    updated_at = :now
                WHERE workflow_id = :storm_id
            """), {
                "storm_id":   existing_storm_id,
                "ctx":        _json.dumps(updated_ctx),
                "title": (
                    f"[STORM] Cascading Failure — {n_updated} incidents "
                    f"across {n_res_updated} resource(s)"
                ),
                "risk_score": _merge_risk,
                "risk_level": _merge_risk_level,
                "now":        _dt.utcnow(),
            })
            db.commit()
            logger.info(
                f"[STORM TASK] Merged into storm {existing_storm_id[:8]}: "
                f"now {n_updated} children across {n_res_updated} resources"
            )

            # Phase 2: expand the existing storm by downstream CMDB dependents
            try:
                _phase2_dependency_expansion(
                    db=db,
                    storm_id_uuid=existing_storm_id,
                    storm_resource_names=new_resource_names,
                )
            except Exception as _p2_err:
                logger.warning(f"[STORM TASK] Phase 2 expansion (merge path) failed: {_p2_err}")

            return {
                "status":    "merged",
                "storm_id":  existing_storm_id,
                "added":     len(new_additions),
                "total":     n_updated,
            }

        # ── 2. Run StormAgent analysis ────────────────────────────────────────
        agent = StormAgent()
        analysis = agent.analyze(
            affected_resources=resource_names,
            event_types=event_types,
            incident_ids=live_ids,
        )
        logger.info(
            f"[STORM TASK] Analysis complete: pattern={analysis['event_type_pattern']}, "
            f"confidence={analysis['confidence']}, "
            f"candidates={len(analysis['root_cause_candidates'])}"
        )

        # ── 3. Create storm parent incident ───────────────────────────────────
        n = len(live_ids)
        n_res = len(resource_names)
        pattern = analysis["event_type_pattern"].replace("_", " ").title()
        title = f"[STORM] {pattern} — {n} incidents across {n_res} resource(s)"

        top_candidate = (
            analysis["root_cause_candidates"][0].get("name", "unknown")
            if analysis["root_cause_candidates"] else "unknown"
        )

        summary = (
            f"Correlated event storm detected. {n} incidents across "
            f"{n_res} resource(s): {', '.join(resource_names[:4])}"
            + (f" (+{n_res - 4} more)" if n_res > 4 else "")
            + f". Suspected root cause: {top_candidate}."
        )

        storm_context = {
            "is_storm_parent": True,
            "storm_analysis": analysis,
            "storm_children": live_ids,
            "storm_detected_at": _dt.utcnow().isoformat(),
            "alert_payload": {
                "type": "event_storm",
                "severity": "critical",
                "resource_name": f"{n_res} resources",
                "description": analysis["llm_hypothesis"],
                "resource_list": resource_names,
                "event_types": list(set(event_types)),
                "pattern": analysis["event_type_pattern"],
            },
        }

        storm_state = WorkflowState(
            workflow_type=WorkflowType.INCIDENT,
            # Storm parents require human investigation and a coordinated
            # remediation decision — AWAITING_MANUAL reflects that ownership
            # has transferred to the operator rather than a pipeline approval.
            lifecycle_state=LifecycleState.AWAITING_MANUAL,
            title=title,
            summary=summary,
            severity=None,   # will be set below
            context=storm_context,
        )

        repo = WorkflowRepository(db)
        repo.save(storm_state)
        storm_id = str(storm_state.workflow_id)
        logger.info(f"[STORM TASK] Storm parent created: {storm_id}")

        # INC number is assigned automatically by DB trigger on INSERT (trg_workflow_human_id_insert).
        # Read it back to log it; no manual nextval() needed.
        try:
            inc_str = EnumerationService.generate_incident_number(db, storm_id)
            logger.info(f"[STORM TASK] Storm parent incident ID: {inc_str} ({storm_id})")
        except Exception as _enum_err:
            logger.warning(f"[STORM TASK] Could not read incident number: {_enum_err}")

        # ── Storm risk score ──────────────────────────────────────────────────
        # Storms are always high-impact events. Score = base 50 (guaranteed
        # significant) + confidence contribution + child-count scale +
        # resource blast-radius scale, capped at 100.
        _storm_risk = min(100, int(
            50                                  # every storm starts at 50
            + analysis["confidence"] * 30       # 0-30 from LLM confidence
            + min(n, 10) * 1                    # 1 pt per confirmed child (max 10)
            + min(n_res, 10) * 1                # 1 pt per affected resource (max 10)
        ))
        if _storm_risk >= 75:
            _storm_risk_level = "critical"
        elif _storm_risk >= 50:
            _storm_risk_level = "high"
        else:
            _storm_risk_level = "medium"

        # Update storm parent: set is_storm_parent=True (triggers STRM number assignment),
        # self-referencing storm_id, storm_detected_at timestamp, severity, and risk.
        db.execute(sql_text("""
            UPDATE workflow_states
            SET is_storm_parent    = TRUE,
                storm_id           = :storm_uuid,
                storm_detected_at  = :now,
                severity           = 'critical',
                risk_score         = :risk_score,
                risk_level         = :risk_level,
                updated_at         = :now
            WHERE workflow_id = :storm_uuid
        """), {
            "storm_uuid": storm_state.workflow_id,
            "now":        _dt.utcnow(),
            "risk_score":  _storm_risk,
            "risk_level":  _storm_risk_level,
        })

        # Read back the STRM number assigned by UPDATE trigger
        try:
            strm_str = EnumerationService.get_storm_number_str(db, storm_id)
            if strm_str:
                logger.info(f"[STORM TASK] Storm ID: {strm_str} ({storm_id})")
        except Exception as _strm_err:
            logger.warning(f"[STORM TASK] Could not read storm number: {_strm_err}")

        # ── 4 & 5. Link children + set storm_hold ────────────────────────────
        for child_id in live_ids:
            db.execute(sql_text("""
                UPDATE workflow_states
                SET lifecycle_state = 'storm_hold',
                    storm_id        = :storm_uuid,
                    updated_at      = :now
                WHERE workflow_id = :child_id
            """), {
                "storm_uuid": storm_state.workflow_id,
                "child_id":   child_id,
                "now":        _dt.utcnow(),
            })
        logger.info(f"[STORM TASK] {len(live_ids)} children moved to storm_hold")

        # ── 6. Cancel individual pending approvals for children ───────────────
        if live_ids:
            cancelled = db.execute(sql_text(f"""
                UPDATE approvals
                SET status         = 'cancelled',
                    decided_at     = :now,
                    decided_by     = 'system',
                    decision_notes = 'Auto-cancelled — incident grouped into storm {storm_id}'
                WHERE workflow_id::text IN ({placeholders})
                  AND status = 'pending'
            """), {"now": _dt.utcnow()}).rowcount
            if cancelled:
                logger.info(f"[STORM TASK] Cancelled {cancelled} individual approval(s)")

        db.commit()
        logger.info(f"[STORM TASK] Storm committed to DB. Parent={storm_id}")

        # Write storm system note on parent
        try:
            from agentic_os.db.models import IncidentNoteModel
            note_lines = [
                "Storm Agent Analysis",
                "",
                f"Pattern: {analysis['event_type_pattern'].replace('_', ' ').title()}",
                f"Confidence: {int(analysis['confidence'] * 100)}%",
                f"Affected resources ({n_res}): {', '.join(resource_names[:6])}"
                + (f" +{n_res - 6} more" if n_res > 6 else ""),
                f"Event types: {', '.join(set(event_types))}",
                "",
                "Root Cause Hypothesis:",
                analysis["llm_hypothesis"],
                "",
            ]
            if analysis["root_cause_candidates"]:
                note_lines.append("Topology Evidence (Neo4j):")
                for c in analysis["root_cause_candidates"][:3]:
                    note_lines.append(
                        f"  - '{c['name']}' ({c.get('type','?')}): "
                        f"shared by {c['affected_count']} resource(s)"
                    )
                note_lines.append("")
            note_lines += [
                f"Child incidents held in storm_hold: {len(live_ids)}",
                "Individual remediations are suppressed. Use the Event Storms page to resolve as a storm or handle incidents individually.",
                "",
                f"LLM used: {'Yes' if analysis['llm_used'] else 'No (rule-based fallback)'}",
                f"Neo4j available: {'Yes' if analysis['neo4j_available'] else 'No'}",
            ]
            db.add(IncidentNoteModel(
                workflow_id=storm_state.workflow_id,
                author="storm_agent",
                note_type="system",
                body="\n".join(note_lines),
            ))
            db.commit()
        except Exception as _note_err:
            logger.warning(f"[STORM TASK] System note failed: {_note_err}")

        # ── 8. Phase 2: Dependency-based expansion (immediate pass) ──────────────
        # Pull in incidents on downstream-dependent services that weren't caught
        # by Phase 1 (e.g., Splunk app-tier alerts that arrived at the same time
        # as watcher data-tier alerts but with different event type signatures).
        p2_adopted = 0
        try:
            p2_adopted = _phase2_dependency_expansion(
                db=db,
                storm_id_uuid=storm_state.workflow_id,
                storm_resource_names=resource_names,
            )
            if p2_adopted:
                logger.info(
                    f"[STORM TASK] Phase 2 (immediate) adopted {p2_adopted} "
                    f"additional incident(s) into storm {storm_id[:8]}"
                )
        except Exception as _p2_err:
            logger.warning(f"[STORM TASK] Phase 2 immediate expansion failed: {_p2_err}")

        # ── 9. Schedule a delayed Phase 2 sweep for late-arriving events ──────
        # Splunk/external events often arrive seconds after the storm is formed.
        # This delayed task catches anything that wasn't in the DB yet during
        # the immediate Phase 2 pass above.
        try:
            execute_storm_expansion_task.apply_async(
                kwargs={"storm_id": storm_id},
                countdown=45,  # 45 s — enough for Splunk events to arrive
            )
            logger.info(f"[STORM TASK] Scheduled Phase 2 sweep in 45 s for {storm_id[:8]}")
        except Exception as _sched_err:
            logger.warning(f"[STORM TASK] Could not schedule delayed expansion: {_sched_err}")

        return {
            "status":        "storm_created",
            "storm_id":      storm_id,
            "child_count":   len(live_ids) + p2_adopted,
            "pattern":       analysis["event_type_pattern"],
            "confidence":    analysis["confidence"],
            "llm_used":      analysis["llm_used"],
            "neo4j_ok":      analysis["neo4j_available"],
        }

    except Exception as exc:
        logger.error(
            f"[STORM TASK {self.request.id}] Failed: {exc}", exc_info=True
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Storm Expansion Task (delayed Phase 2 sweep) ──────────────────────────────

@app.task(bind=True, queue="workflows", acks_late=True, reject_on_worker_lost=True,
          max_retries=1, default_retry_delay=15)
def execute_storm_expansion_task(self, storm_id: str):
    """
    Delayed Phase 2 storm expansion sweep.

    Fired ~45 seconds after a storm is created so that events from external
    connectors (Splunk, PagerDuty, etc.) that arrive after the initial storm
    detection window can still be adopted into the storm.

    This complements the immediate Phase 2 pass in execute_storm_analysis_task:
    - Immediate pass: catches events already in DB when storm task runs
    - This delayed pass: catches events that arrived 5-45 s after the storm formed

    Args:
        storm_id: UUID string of the storm parent incident.
    """
    import json as _json
    from sqlalchemy import text as sql_text
    from agentic_os.db.database import SessionLocal

    logger.info(f"[STORM EXPAND {self.request.id}] Delayed Phase 2 sweep for storm {storm_id[:8]}")

    db = SessionLocal()
    try:
        # Verify storm is still active (not released or resolved by operator)
        row = db.execute(sql_text("""
            SELECT
                workflow_id,
                context -> 'storm_analysis' -> 'affected_resources' AS resources
            FROM workflow_states
            WHERE workflow_id::text = :storm_id
              AND workflow_type = 'incident'
              AND (context ->> 'is_storm_parent')::boolean = true
              AND lifecycle_state NOT IN ('resolved', 'closed')
        """), {"storm_id": storm_id}).fetchone()

        if not row:
            logger.info(
                f"[STORM EXPAND] Storm {storm_id[:8]} no longer active — expansion skipped"
            )
            return {"status": "skipped", "reason": "storm_inactive"}

        # Extract current resource list from context
        raw_resources = row[1]
        if isinstance(raw_resources, str):
            resource_names = _json.loads(raw_resources)
        elif isinstance(raw_resources, list):
            resource_names = raw_resources
        else:
            resource_names = []

        if not resource_names:
            logger.info(f"[STORM EXPAND] No resources in storm context — skipping")
            return {"status": "skipped", "reason": "no_resources"}

        # Run Phase 2 expansion with a wider window for the delayed sweep
        adopted = _phase2_dependency_expansion(
            db=db,
            storm_id_uuid=row[0],
            storm_resource_names=resource_names,
            window_minutes=15,
        )

        logger.info(
            f"[STORM EXPAND] Delayed sweep complete: {adopted} additional "
            f"incident(s) adopted into storm {storm_id[:8]}"
        )
        return {
            "status":   "expanded",
            "storm_id": storm_id,
            "adopted":  adopted,
        }

    except Exception as exc:
        logger.error(
            f"[STORM EXPAND {self.request.id}] Failed: {exc}", exc_info=True
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "error", "reason": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass
