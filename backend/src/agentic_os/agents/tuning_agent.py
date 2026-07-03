"""
Platform Intelligence — TuningAgent

Analyses resolved incidents from the last N days and generates concrete,
actionable recommendations for tuning the risk scoring configuration.

Flow:
  1. clear_pending()          — wipe previous unreviewed recommendations (fresh set each run)
  2. _aggregate_outcomes()    — collapse N incidents into a ~2KB statistical summary
                                (token budget stays flat regardless of incident volume)
  3. _llm_analysis()          — ask the configured LLM to reason over the summary
  4. _rule_based_fallback()   — if LLM unavailable or returns nothing, run deterministic checks

Recommendations require human accept/reject via the Platform Intelligence UI —
*unless* a parameter has earned auto-apply trust (3 consecutive accepted+applied
cycles, each independently verified to have improved its targeted metric), in
which case new recommendations for that parameter apply themselves
(status="auto_applied") and skip the review queue. A single verified regression
reverts the pattern to mandatory review on the next run. See
_verify_applied_recommendations / _is_pattern_auto_apply_eligible.

Triggered on-demand via POST /api/platform-intelligence/analyze, and once daily
via Celery Beat (verify_recommendation_outcomes) so the verification step runs
even on days nobody manually triggers analysis.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from agentic_os.db.models import WorkflowStateModel, OptimizationRecommendationModel, PlatformSettingModel
from agentic_os.db.repositories import (
    RiskWeightConfigRepository,
    OptimizationRecommendationRepository,
    PlatformIntelRunRepository,
)

logger = logging.getLogger(__name__)


def classify_failure(error_message: Optional[str]) -> str:
    """
    Enhancement 2 — rule-based root-cause classification for a step/remediation
    failure, by pattern-matching the error message text. Mirrors the LLM-first/
    rules-fallback split used elsewhere in this agent, but rules-only for now —
    unclassified errors return "unknown" rather than an LLM call per failure.

    Imported by incident_agents.py at write time so failure_category is populated
    as data flows in, not retroactively computed at analysis time.
    """
    if not error_message:
        return "unknown"
    msg = error_message.lower()
    if any(p in msg for p in ("connection refused", "econnrefused", "connect error", "could not connect", "connection reset")):
        return "tool_error"
    if any(p in msg for p in ("404", "not found", "no such", "does not exist")):
        return "target_not_found"
    if any(p in msg for p in ("permission denied", "forbidden", "403", "access denied", "unauthorized", "401")):
        return "permission_denied"
    if any(p in msg for p in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"
    if any(p in msg for p in ("precondition", "prerequisite", "required state", "not ready", "not in expected state")):
        return "precondition_unmet"
    if any(p in msg for p in ("partial", "partially completed", "incomplete")):
        return "partial_completion"
    return "unknown"


# ── tuneable constants ───────────────────────────────────────────────────────
ANALYSIS_DAYS            = 30
MIN_INCIDENTS_FOR_SIGNAL = 5
FP_RATE_THRESHOLD        = 0.20
AUTO_RATE_LOW            = 0.30
MTTR_HIGH_HOURS          = 4.0
TOP_EVENT_TYPES          = 20   # max event types sent to LLM
VERIFICATION_DELAY_DAYS  = 7    # wait this long after applying before checking if the metric improved
AUTO_APPLY_MIN_CYCLES    = 3    # consecutive verified-improved cycles needed to earn auto-apply trust
# ─────────────────────────────────────────────────────────────────────────────


class TuningAgent:
    """
    Analyses incident history and generates config recommendations.

    Each run:
      - Clears all pending (unreviewed) recommendations — no pile-up
      - Tries LLM analysis over an aggregated summary
      - Falls back to rule-based checks if LLM is not configured or returns nothing
    """

    def __init__(self, db: Session):
        self.db        = db
        self.risk_repo = RiskWeightConfigRepository(db)
        self.rec_repo  = OptimizationRecommendationRepository(db)
        # Captured by _llm_analysis for the run-history "raw output" view —
        # _llm_analysis returns only the parsed/validated recs, this preserves
        # what the model actually saw and said.
        self.last_llm_raw_response: Optional[str] = None

    def _pi_setting(self, suffix: str, default):
        """
        Read a platform_intelligence.<suffix> setting, falling back to `default`
        if unset or the DB is unavailable. Mirrors the value_type coercion used by
        api/routes/platform_settings.py so this stays consistent with the Settings UI.
        """
        try:
            row = self.db.get(PlatformSettingModel, f"platform_intelligence.{suffix}")
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
            return default
        if row is None or row.value is None:
            return default
        if row.value_type == "bool":
            return row.value.lower() in ("true", "1", "yes")
        if row.value_type == "int":
            try:
                return int(row.value)
            except ValueError:
                return default
        if row.value_type == "float":
            try:
                return float(row.value)
            except ValueError:
                return default
        return row.value

    # ── public entry point ──────────────────────────────────────────────────

    def run_analysis(
        self,
        period_days: int = ANALYSIS_DAYS,
        ignore_cooldown: bool = False,
        trigger: str = "manual",
    ) -> dict:
        """
        trigger: "scheduled" | "manual" | "force_refresh" — recorded on the
        persisted PlatformIntelRunModel row purely for the run-history view;
        does not change analysis behaviour itself (ignore_cooldown does that).
        """
        logger.info(f"[TuningAgent] Starting analysis (period={period_days}d, ignore_cooldown={ignore_cooldown}, trigger={trigger})")
        cutoff = datetime.utcnow() - timedelta(days=period_days)
        self.last_llm_raw_response = None

        # Close the loop on previously-applied recommendations before generating
        # new ones — cheap (only touches rows ready for verification).
        verified = self._verify_applied_recommendations()
        if verified:
            logger.info(f"[TuningAgent] Verified outcome for {verified} previously-applied recommendation(s)")

        # Replace pending recommendations — fresh set each run
        cleared = self.rec_repo.clear_pending()
        logger.info(f"[TuningAgent] Cleared {cleared} pending recommendations")

        cfg     = self.risk_repo.get_by_key("default")
        weights = cfg.weights if cfg else {}

        resolved = (
            self.db.query(WorkflowStateModel)
            .filter(
                WorkflowStateModel.lifecycle_state.in_(["resolved", "closed"]),
                WorkflowStateModel.updated_at >= cutoff,
                WorkflowStateModel.workflow_type == "incident",
            )
            .all()
        )

        total   = len(resolved)
        source  = "insufficient_data"
        generated = 0
        skipped   = 0

        logger.info(f"[TuningAgent] {total} resolved incidents in window")

        if total >= MIN_INCIDENTS_FOR_SIGNAL:
            summary  = self._aggregate_outcomes(resolved, weights, period_days)
            history  = self._load_decision_history()

            # LLM first
            recs = self._llm_analysis(summary, period_days)
            if recs:
                source = "llm"
            else:
                # Rule-based fallback
                logger.info("[TuningAgent] LLM produced nothing — running rule-based fallback")
                recs = self._rule_based_fallback(resolved, weights, period_days)
                source = "rules" if recs else "healthy"

            # Always run regardless of which path produced `recs` above — see
            # _always_on_checks docstring for why each one is here.
            always_on_recs = self._always_on_checks(resolved, weights, period_days)
            if always_on_recs:
                recs = list(recs) + always_on_recs
                if source in ("healthy", "insufficient_data"):
                    source = "rules"

            skipped = 0
            for rec in recs:
                skip_reason = self._should_skip(rec, history, weights, ignore_cooldown=ignore_cooldown)
                if skip_reason:
                    logger.info(f"[TuningAgent] Skipping '{rec.get('parameter')}': {skip_reason}")
                    skipped += 1
                    continue
                rec = self._annotate_rejection_history(rec, history)

                # Auto-apply gate: disabled by default (platform_intelligence.auto_apply_enabled).
                # Even when enabled, a parameter only applies itself after it has earned trust
                # (consecutive accepted+applied+verified-improved cycles). Still creates a full
                # audit row either way.
                auto_apply_on = self._pi_setting("auto_apply_enabled", False)
                if auto_apply_on and self._is_pattern_auto_apply_eligible(rec.get("parameter", "")):
                    applied = self._apply_rec_to_weights(rec, weights)
                    rec["status"]                      = "auto_applied"
                    rec["auto_apply_eligible"]          = True
                    rec["auto_apply_threshold_met_at"]  = datetime.utcnow()
                    rec["applied"]                      = applied
                    rec["applied_at"]                   = datetime.utcnow() if applied else None
                    rec["reviewed_by"]                  = "system (auto-apply)"
                    rec["reviewed_at"]                  = datetime.utcnow()
                    if applied:
                        # Re-read so subsequent recs in this same run see the new value
                        cfg     = self.risk_repo.get_by_key("default")
                        weights = cfg.weights if cfg else weights
                    logger.info(f"[TuningAgent] Auto-applied '{rec.get('parameter')}' (trust earned)")

                self.rec_repo.create(rec)
                generated += 1
            if skipped:
                logger.info(f"[TuningAgent] Suppressed {skipped} duplicate/cooldown recommendation(s)")
            if generated == 0 and skipped > 0:
                source = "suppressed"

        logger.info(
            f"[TuningAgent] Done — generated={generated}, cleared={cleared}, source={source}"
        )

        # Persist a snapshot of this run — KPIs, source, raw LLM output — so the
        # frontend can plot a trend over time and audit why a cycle behaved a
        # certain way, instead of that reasoning being discarded the moment this
        # function returns (the previous state of affairs).
        try:
            kpis = self._compute_kpis(resolved, weights, period_days)
            PlatformIntelRunRepository(self.db).create({
                "period_days":                period_days,
                "trigger":                    trigger,
                "source":                     source,
                "incidents_analysed":         total,
                "recommendations_generated":  generated,
                "recommendations_skipped":    skipped,
                "llm_raw_response":           self.last_llm_raw_response,
                "kpis":                       kpis,
            })
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not persist run history: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        return {
            "incidents_analysed":              total,
            "recommendations_generated":       generated,
            "recommendations_cleared":         cleared,
            "recommendations_skipped_duplicate": skipped if total >= MIN_INCIDENTS_FOR_SIGNAL else 0,
            "period_days":                     period_days,
            "min_incidents_needed":            MIN_INCIDENTS_FOR_SIGNAL,
            "source":                          source,
            # "reason" mirrors "source" — used by the frontend message builder
            "reason":                          source,
        }

    # ── aggregation ─────────────────────────────────────────────────────────

    def _aggregate_outcomes(self, resolved: list, weights: dict, period_days: int) -> dict:
        """
        Collapse resolved incidents into a compact summary.
        Groups by domain and event type; output size is ~2KB regardless of volume.
        """
        try:
            from agentic_os.db.event_type_taxonomy_data import ALIAS_MAP
        except ImportError:
            ALIAS_MAP = {}

        domain_acc = defaultdict(lambda: {"count": 0, "noise": 0, "automated": 0, "scores": [], "mttr_h": []})
        et_acc     = defaultdict(lambda: {"count": 0, "noise": 0, "automated": 0, "scores": []})

        for w in resolved:
            ctx = w.context or {}
            et  = ctx.get("anomaly_type") or ctx.get("event_type") or "unknown"

            canonical = ALIAS_MAP.get(et, et)
            domain    = canonical.split(".")[0] if "." in canonical else "unknown"

            is_noise = (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
            is_auto  = w.resolution_source == "automated_remediation"

            qual = ctx.get("qualification_factors") or {}
            score = qual.get("final_score") or ctx.get("qualification_score")

            mttr_h = None
            if w.created_at and (w.resolved_at or w.updated_at):
                secs = ((w.resolved_at or w.updated_at) - w.created_at).total_seconds()
                if secs > 0:
                    mttr_h = round(secs / 3600, 2)

            d = domain_acc[domain]
            d["count"] += 1
            if is_noise: d["noise"] += 1
            if is_auto:  d["automated"] += 1
            if score is not None: d["scores"].append(float(score))
            if mttr_h is not None: d["mttr_h"].append(mttr_h)

            e = et_acc[et]
            e["count"] += 1
            if is_noise: e["noise"] += 1
            if is_auto:  e["automated"] += 1
            if score is not None: e["scores"].append(float(score))

        def _pct(num, den): return round(num / den, 3) if den else 0.0
        def _avg(lst):      return round(sum(lst) / len(lst), 2) if lst else None
        def _sat(lst):      return round(sum(1 for s in lst if s >= 99.5) / len(lst) * 100, 1) if lst else None

        domain_stats = {
            dom: {
                "volume":          d["count"],
                "noise_rate":      _pct(d["noise"], d["count"]),
                "automation_rate": _pct(d["automated"], d["count"]),
                "avg_score":       _avg(d["scores"]),
                "saturation_pct":  _sat(d["scores"]),
                "avg_mttr_h":      _avg(d["mttr_h"]),
                "current_domain_mult": weights.get("domain_multipliers", {}).get(dom),
            }
            for dom, d in domain_acc.items()
        }

        top_et = sorted(et_acc.items(), key=lambda x: x[1]["count"], reverse=True)[:TOP_EVENT_TYPES]
        et_mults = weights.get("event_type_multipliers", {})
        dom_mults = weights.get("domain_multipliers", {})
        event_type_stats = {}
        for et, e in top_et:
            canonical = ALIAS_MAP.get(et, et)
            mult = et_mults.get(canonical) or et_mults.get(et)
            if mult is None and "." in canonical:
                mult = dom_mults.get(canonical.split(".")[0])
            event_type_stats[et] = {
                "volume":          e["count"],
                "noise_rate":      _pct(e["noise"], e["count"]),
                "automation_rate": _pct(e["automated"], e["count"]),
                "avg_score":       _avg(e["scores"]),
                "current_mult":    mult,
            }

        total = len(resolved)
        all_noise = sum(1 for w in resolved if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate"))
        all_auto  = sum(1 for w in resolved if w.resolution_source == "automated_remediation")

        recent_decisions = []
        try:
            rows = (
                self.db.query(OptimizationRecommendationModel)
                .filter(OptimizationRecommendationModel.status.in_(["accepted", "rejected"]))
                .order_by(OptimizationRecommendationModel.reviewed_at.desc())
                .limit(8)
                .all()
            )
            recent_decisions = [
                {
                    "parameter": r.parameter,
                    "suggested": r.suggested_value,
                    "status":    r.status,
                    "reason":    r.review_reason or "",
                }
                for r in rows
            ]
        except Exception:
            pass

        return {
            "period_days":       period_days,
            "total_incidents":   total,
            "system_health": {
                "false_positive_rate": _pct(all_noise, total),
                "automation_rate":     _pct(all_auto, total),
            },
            "current_config": {
                "qualification_threshold":  weights.get("qualification_threshold", 50.0),
                "domain_multipliers":       dom_mults,
                "event_type_multipliers":   et_mults,
                "default_event_multiplier": weights.get("default_event_multiplier", 1.0),
            },
            "domain_stats":      domain_stats,
            "top_event_types":   event_type_stats,
            "recent_decisions":  recent_decisions,
        }

    # ── KPI snapshot ─────────────────────────────────────────────────────────

    def _compute_kpis(self, resolved: list, weights: dict, period_days: int) -> dict:
        """
        Compute the full Platform Intelligence KPI snapshot for this analysis run.

        Persisted alongside each run (see PlatformIntelRunModel) so the frontend can
        plot a trend over time instead of only ever seeing the current instantaneous
        value. Reuses the same `resolved` query run_analysis() already has — this
        does not re-query incidents.
        """
        from agentic_os.db.models import (
            ApprovalModel, RemediationOutcomeModel, RunbookStepOutcomeModel,
            MonitoringEventModel,
        )
        from collections import defaultdict

        def _pct(num, den):
            return round(num / den, 4) if den else None

        cutoff = datetime.utcnow() - timedelta(days=period_days)
        total = len(resolved)

        # ── Automation / resolution / false-positive (already known basics) ──
        automated = sum(1 for w in resolved if w.resolution_source == "automated_remediation")
        noise     = sum(1 for w in resolved if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate"))

        all_open_or_resolved = (
            self.db.query(WorkflowStateModel)
            .filter(
                WorkflowStateModel.updated_at >= cutoff,
                WorkflowStateModel.workflow_type == "incident",
            )
            .count()
        )

        # ── MTTR (all + P1/P2) — mirrors _check_mttr's own filter/window logic ──
        mttr_all = [
            ((w.resolved_at or w.updated_at) - w.created_at).total_seconds() / 3600
            for w in resolved if w.created_at and (w.resolved_at or w.updated_at)
        ]
        p1p2 = [
            w for w in resolved
            if w.context and str(w.context.get("incident_priority", "")).upper() in ("P1", "P2")
            and w.created_at and (w.resolved_at or w.updated_at)
        ]
        mttr_p1p2 = [
            ((w.resolved_at or w.updated_at) - w.created_at).total_seconds() / 3600
            for w in p1p2
        ]

        # ── CMDB coverage — average confidence_score across resolved incidents ──
        cmdb_scores = []
        for w in resolved:
            ctx = w.context or {}
            score = (ctx.get("qualification_factors") or {}).get("final_score") or ctx.get("qualification_score")
            if score is not None:
                cmdb_scores.append(float(score))

        # ── Qualification rate — % of raw monitoring events that became a
        # tracked incident, not just "% of incidents qualification-scored
        # above threshold" (that's a much weaker signal — it can't see events
        # that never made it to a WorkflowStateModel row at all). Needs
        # MonitoringEventModel, the pre-workflow signal log.
        qualification_rate = None
        try:
            mon_total = (
                self.db.query(MonitoringEventModel)
                .filter(MonitoringEventModel.created_at >= cutoff)
                .count()
            )
            mon_qualified = (
                self.db.query(MonitoringEventModel)
                .filter(
                    MonitoringEventModel.created_at >= cutoff,
                    MonitoringEventModel.qualified_as_incident.is_(True),
                )
                .count()
            )
            qualification_rate = _pct(mon_qualified, mon_total)
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute qualification_rate: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Mean time to approval — governance queue latency, distinct from MTTR ──
        mean_time_to_approval_minutes = None
        try:
            approvals = (
                self.db.query(ApprovalModel)
                .filter(
                    ApprovalModel.approval_type == "governance",
                    ApprovalModel.requested_at >= cutoff,
                    ApprovalModel.decided_at.isnot(None),
                )
                .all()
            )
            if approvals:
                mins = [
                    (a.decided_at - a.requested_at).total_seconds() / 60
                    for a in approvals
                ]
                mean_time_to_approval_minutes = round(sum(mins) / len(mins), 1)
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute mean_time_to_approval: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Governance bypass rate — % of resolved incidents that never hit a
        # governance approval gate at all (confidence-gate bypass or no policy
        # match), vs. ones that went through manual review. Proxy: absence of
        # any ApprovalModel(approval_type='governance') row for the workflow.
        governance_bypass_rate = None
        try:
            gated_workflow_ids = {
                a.workflow_id for a in
                self.db.query(ApprovalModel.workflow_id)
                .filter(ApprovalModel.approval_type == "governance", ApprovalModel.requested_at >= cutoff)
                .all()
            }
            if total:
                bypassed = sum(1 for w in resolved if w.workflow_id not in gated_workflow_ids)
                governance_bypass_rate = _pct(bypassed, total)
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute governance_bypass_rate: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Recommendation acceptance rate — is Platform Intel itself well-calibrated? ──
        recommendation_acceptance_rate = None
        try:
            decided = (
                self.db.query(OptimizationRecommendationModel)
                .filter(
                    OptimizationRecommendationModel.status.in_(["accepted", "rejected"]),
                    OptimizationRecommendationModel.created_at >= cutoff,
                )
                .all()
            )
            if decided:
                accepted = sum(1 for r in decided if r.status == "accepted")
                recommendation_acceptance_rate = _pct(accepted, len(decided))
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute recommendation_acceptance_rate: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Auto-apply trust coverage — % of tunable parameters that have earned
        # auto-apply trust vs. still requiring manual review every cycle. ──
        auto_apply_trust_coverage = None
        try:
            tunable = (
                self.db.query(OptimizationRecommendationModel)
                .filter(~OptimizationRecommendationModel.category.in_(["general", "governance", "runbook_step"]))
                .all()
            )
            params = defaultdict(bool)
            for r in tunable:
                params[r.parameter] = params[r.parameter] or bool(r.auto_apply_eligible)
            if params:
                auto_apply_trust_coverage = _pct(sum(params.values()), len(params))
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute auto_apply_trust_coverage: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Remediation failure rate — whole-attempt level (did automation work) ──
        remediation_failure_rate = None
        try:
            outcomes = (
                self.db.query(RemediationOutcomeModel)
                .filter(RemediationOutcomeModel.created_at >= cutoff)
                .all()
            )
            decided_outcomes = [o for o in outcomes if o.remediation_successful is not None]
            if decided_outcomes:
                failed = sum(1 for o in decided_outcomes if not o.remediation_successful)
                remediation_failure_rate = _pct(failed, len(decided_outcomes))
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute remediation_failure_rate: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        # ── Runbook step failure rate + category breakdown — step level (where
        # exactly is it breaking), distinct from remediation_failure_rate (whole
        # attempt level, is it working at all). ──
        runbook_step_failure_rate = None
        runbook_step_failure_categories: dict = {}
        try:
            steps = (
                self.db.query(RunbookStepOutcomeModel)
                .filter(RunbookStepOutcomeModel.created_at >= cutoff)
                .all()
            )
            if steps:
                failed_steps = [s for s in steps if s.status in ("failed", "timed_out")]
                runbook_step_failure_rate = _pct(len(failed_steps), len(steps))
                cat_counts: dict = defaultdict(int)
                for s in failed_steps:
                    cat_counts[s.failure_category or "unknown"] += 1
                runbook_step_failure_categories = dict(cat_counts)
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not compute runbook_step_failure_rate: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        return {
            "automation_rate":                 _pct(automated, total),
            "resolution_rate":                 _pct(total, all_open_or_resolved),
            "mttr_all_hours":                  round(sum(mttr_all) / len(mttr_all), 2) if mttr_all else None,
            "mttr_p1p2_hours":                 round(sum(mttr_p1p2) / len(mttr_p1p2), 2) if mttr_p1p2 else None,
            "false_positive_rate":              _pct(noise, total),
            "cmdb_coverage":                    round(sum(cmdb_scores) / len(cmdb_scores), 1) if cmdb_scores else None,
            "qualification_rate":              qualification_rate,
            "mean_time_to_approval_minutes":   mean_time_to_approval_minutes,
            "governance_bypass_rate":          governance_bypass_rate,
            "recommendation_acceptance_rate":  recommendation_acceptance_rate,
            "auto_apply_trust_coverage":       auto_apply_trust_coverage,
            "remediation_failure_rate":        remediation_failure_rate,
            "runbook_step_failure_rate":       runbook_step_failure_rate,
            "runbook_step_failure_categories": runbook_step_failure_categories,
            "incidents_analysed":              total,
        }

    # ── LLM analysis ────────────────────────────────────────────────────────

    def _get_llm_provider(self):
        try:
            from agentic_os.db.llm_config_repository import LLMConfigRepository
            from agentic_os.services.llm_provider import get_llm_provider
            cfg = LLMConfigRepository(self.db).get_config("default")
            if not cfg:
                return None
            return get_llm_provider(
                cfg.get("provider", "anthropic"),
                api_key=cfg.get("api_key"),
                model=cfg.get("model"),
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not load LLM provider: {e}")
            # Rollback so a schema mismatch or query error doesn't leave the
            # session in InFailedSqlTransaction state for subsequent INSERTs.
            try:
                self.db.rollback()
            except Exception:
                pass
            return None

    def _llm_analysis(self, summary: dict, period_days: int) -> list:
        """
        Call the configured LLM with an aggregated summary (~600 tokens input).
        Runs the async provider call in a dedicated thread so this method stays
        synchronous — safe from both FastAPI (running event loop) and Celery (no loop).
        Returns a list of validated rec dicts, or [] if LLM is unavailable / returns nothing.
        """
        provider = self._get_llm_provider()
        if not provider or not provider.is_configured():
            logger.info("[TuningAgent] LLM not configured — skipping AI analysis")
            return []

        system_prompt = (
            "You are a Platform Intelligence tuning agent for an IT operations platform. "
            "You analyse incident outcome statistics and recommend specific configuration changes "
            "to improve event qualification accuracy — reducing false positives and ensuring "
            "genuinely critical events always qualify.\n\n"
            "Return a JSON array of recommendation objects. Each object must have:\n"
            "  category        : \"threshold\" | \"event_multiplier\" | \"domain_multiplier\" | \"resource_specific\" | \"governance\" | \"runbook_step\" | \"general\"\n"
            "  parameter       : dot-notation config path, e.g. \"domain_multipliers.infrastructure\",\n"
            "                    \"qualification_threshold\", \"event_type_multipliers.log.error.spike\"\n"
            "  current_value   : current numeric value (or null)\n"
            "  suggested_value : suggested numeric value (or null for general/informational)\n"
            "  title           : short title under 80 characters\n"
            "  rationale       : 2-3 sentences explaining WHY based on the data\n"
            "  impact          : one sentence on expected effect\n"
            "  confidence      : float 0.0–1.0\n"
            "  priority        : \"high\" | \"medium\" | \"low\"\n\n"
            "Bounds: domain_multipliers 0.3–1.5 | qualification_threshold 30–75 | "
            "event_type_multipliers 0.3–3.0\n"
            "Only recommend changes the data clearly supports. "
            "If everything looks healthy, return []. "
            "Return JSON only — no markdown, no prose outside the array."
        )

        user_content = (
            f"Analyse this incident outcome data and recommend qualification config changes.\n\n"
            f"DATA:\n{json.dumps(summary, indent=2)}\n\n"
            f"Return a JSON array of recommendations ([] if no changes are needed)."
        )

        async def _call():
            return await provider.generate_agent_completion(
                system_prompt, user_content, max_tokens=1500, temperature=0.1
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                raw = pool.submit(asyncio.run, _call()).result(timeout=45)
            self.last_llm_raw_response = raw
            recs = self._parse_llm_recommendations(raw, summary)
            logger.info(f"[TuningAgent] LLM returned {len(recs)} recommendations")
            return recs
        except Exception as e:
            logger.warning(f"[TuningAgent] LLM call failed: {e}")
            self.last_llm_raw_response = f"[LLM call failed: {e}]"
            return []

    def _parse_llm_recommendations(self, raw: str, summary: dict) -> list:
        """Parse and bounds-validate the LLM JSON response."""
        if not raw:
            return []
        try:
            text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("[TuningAgent] LLM returned non-list JSON")
                return []

            validated = []
            for r in data:
                if not isinstance(r, dict):
                    continue
                category  = r.get("category", "general")
                suggested = r.get("suggested_value")

                # The system prompt instructs the LLM to return [] when everything looks
                # healthy, but it sometimes returns a placeholder "nothing to do" object
                # instead (e.g. title "No Configuration Changes Needed", no parameter, no
                # suggested_value). That isn't a recommendation — it has no `parameter`,
                # unlike every genuine informational finding from the rule-based checks
                # (which always name a parameter like "automation_rate" even when
                # suggested_value is None). Drop it here rather than persisting a fake
                # row that pointlessly requires an Accept/Dismiss decision.
                if not r.get("parameter") and suggested is None:
                    continue

                # Clamp to safe bounds
                if suggested is not None:
                    try:
                        suggested = float(suggested)
                        if category == "domain_multiplier":
                            suggested = max(0.3, min(1.5, suggested))
                        elif category == "event_multiplier":
                            suggested = max(0.3, min(3.0, suggested))
                        elif category == "threshold":
                            suggested = max(30.0, min(75.0, suggested))
                    except (TypeError, ValueError):
                        suggested = None

                validated.append({
                    "category":        category,
                    "parameter":       str(r.get("parameter", "")),
                    "current_value":   r.get("current_value"),
                    "suggested_value": suggested,
                    "title":           str(r.get("title", ""))[:200],
                    "rationale":       str(r.get("rationale", "")),
                    "impact":          r.get("impact"),
                    "confidence":      float(r.get("confidence", 0.7)),
                    "priority":        r.get("priority", "medium"),
                    "evidence": {
                        "source":           "llm_analysis",
                        "total_incidents":  summary.get("total_incidents"),
                        "period_days":      summary.get("period_days"),
                    },
                    "expires_at": self._expires_at(),
                })
            return validated
        except Exception as e:
            logger.warning(f"[TuningAgent] Failed to parse LLM response: {e}. Raw[:200]: {raw[:200]}")
            return []

    # ── rule-based fallback ─────────────────────────────────────────────────

    def _rule_based_fallback(self, resolved: list, weights: dict, period_days: int) -> list:
        """
        Run deterministic checks when LLM is unavailable or returns nothing. The
        checks below reason over data that's also present in the ~2KB summary
        handed to the LLM (domain/event-type stats, overall rates) — so it's a
        legitimate fallback when the LLM produces the same kind of recommendation
        from the same data. See _always_on_checks for the checks that must run
        regardless of LLM success — either because they use data the LLM never
        sees at all, or because relying on the LLM to volunteer them on its own
        initiative proved unreliable in practice (automation_rate, mttr).
        """
        recs = []
        checks = [
            self._check_false_positive_rate,
            self._check_priority_automation_coverage,
            self._check_cmdb_priority_coverage,
            self._check_event_type_multipliers,
            self._check_domain_multipliers,
            self._check_cmdb_coverage,
        ]
        for check in checks:
            try:
                recs.extend(check(resolved, weights, period_days))
            except Exception as exc:
                logger.warning(f"[TuningAgent] Rule check {check.__name__} failed: {exc}")
        return recs

    def _always_on_checks(self, resolved: list, weights: dict, period_days: int) -> list:
        """
        Checks that must run on every analysis pass regardless of whether the LLM
        succeeded.

        resource_noise / policy_effectiveness / runbook_step_health reason over data
        that never makes it into the LLM's ~2KB summary at all (runbook step outcomes,
        approval/policy history, per-resource breakdowns) — the LLM has no way to
        produce these on its own.

        automation_rate / mttr DO appear in the LLM's summary, so in principle the LLM
        could surface a breach itself — but in practice a working LLM provider can
        return a handful of narrower recommendations (e.g. one event-type multiplier)
        without ever mentioning a 5.6% automation rate or an 11.1h P1/P2 MTTR breaching
        its own threshold. Since run_analysis only falls back to rules when the LLM
        returns *nothing*, any non-empty LLM response previously suppressed these two
        checks completely, even though the System Health tab independently flags the
        exact same breach every time — a confidence gate this important shouldn't
        depend on whether the LLM happened to mention it.
        """
        recs = []
        checks = [
            self._check_resource_noise,
            self._check_policy_effectiveness,
            self._check_runbook_step_health,
            self._check_automation_rate,
            self._check_mttr,
        ]
        for check in checks:
            try:
                recs.extend(check(resolved, weights, period_days))
            except Exception as exc:
                logger.warning(f"[TuningAgent] Always-on check {check.__name__} failed: {exc}")
        return recs

    # ── helpers ─────────────────────────────────────────────────────────────

    def _expires_at(self) -> datetime:
        return datetime.utcnow() + timedelta(days=30)

    def _load_decision_history(self) -> dict:
        """
        Load accepted and rejected recommendations from the last 60 days,
        keyed by parameter. Used to suppress duplicates and annotate re-surfaces.

        Returns:
            {
              "<parameter>": {
                "accepted": [{"suggested_value": ..., "reviewed_at": datetime, "applied": bool}, ...],
                "rejected":  [{"suggested_value": ..., "reviewed_at": datetime}, ...],
              }
            }
        """
        cutoff = datetime.utcnow() - timedelta(days=60)
        try:
            rows = (
                self.db.query(OptimizationRecommendationModel)
                .filter(
                    OptimizationRecommendationModel.status.in_(["accepted", "rejected"]),
                    OptimizationRecommendationModel.reviewed_at >= cutoff,
                )
                .all()
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not load decision history: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
            return {}

        history: dict = {}
        for r in rows:
            param = r.parameter or ""
            if param not in history:
                history[param] = {"accepted": [], "rejected": []}
            entry = {"suggested_value": r.suggested_value, "reviewed_at": r.reviewed_at, "applied": r.applied}
            history[param][r.status].append(entry)
        return history

    def _should_skip(self, rec: dict, history: dict, weights: dict, ignore_cooldown: bool = False) -> str:
        """
        Return a non-empty skip reason string if this recommendation should be
        suppressed, or '' to allow it through.

        Rules:
          ACCEPTED + APPLIED   → suppress for 30 days (change was made)
          ACCEPTED + not yet   → suppress while still in-flight
          REJECTED + same val  → suppress for 14 days (respect the decision)
          REJECTED + diff val  → allow (new data, new suggested value)
          suggested == current → suppress (already at the target value)

        ignore_cooldown bypasses the history-based checks (accepted/rejected
        cooldown) only — the "already at target value" guard always applies
        regardless, since re-recommending a value that's already live is never
        useful even on a forced refresh.
        """
        param      = rec.get("parameter", "")
        suggested  = rec.get("suggested_value")
        now        = datetime.utcnow()

        # Already at target? Skip. (Not a cooldown — applies even with ignore_cooldown.)
        if suggested is not None:
            current_in_config = self._resolve_config_value(param, weights)
            if current_in_config is not None:
                try:
                    if abs(float(suggested) - float(current_in_config)) < 0.001:
                        return f"suggested value {suggested} already matches current config"
                except (TypeError, ValueError):
                    if str(suggested) == str(current_in_config):
                        return f"suggested value already matches current config"

        if ignore_cooldown or param not in history:
            return ""

        decisions = history[param]

        # Accepted decisions
        for d in decisions["accepted"]:
            age_days = (now - d["reviewed_at"]).days if d["reviewed_at"] else 999
            if d["applied"]:
                if age_days < 30:
                    return f"accepted & applied {age_days}d ago — 30d cooldown active"
            else:
                # Accepted but not yet applied (still in-flight)
                return "accepted recommendation pending application"

        # Rejected decisions — only suppress if same suggested value
        for d in decisions["rejected"]:
            age_days = (now - d["reviewed_at"]).days if d["reviewed_at"] else 999
            if age_days < 14:
                try:
                    same_value = (
                        suggested is not None
                        and d["suggested_value"] is not None
                        and abs(float(suggested) - float(d["suggested_value"])) < 0.001
                    )
                except (TypeError, ValueError):
                    same_value = str(suggested) == str(d["suggested_value"])
                if same_value:
                    return f"rejected {age_days}d ago with same value — 14d cooldown active"

        return ""

    def _resolve_config_value(self, parameter: str, weights: dict):
        """
        Resolve a dot-notation parameter path against the current weights dict.
        e.g. "domain_multipliers.infrastructure" → weights["domain_multipliers"]["infrastructure"]

        See _FLAT_KEY_PARAM_MAPS: for event_type_multipliers/resource_overrides, the
        remainder after the first segment is one flat key (itself possibly containing
        dots, e.g. "log.error.spike") — splitting it further would never match, since
        the underlying dict stores it as a single key, not nested levels.
        """
        parts = parameter.split(".")
        if not parts:
            return None
        if parts[0] in self._FLAT_KEY_PARAM_MAPS and len(parts) >= 2:
            flat_key = ".".join(parts[1:])
            node = weights.get(parts[0])
            return node.get(flat_key) if isinstance(node, dict) else None

        node = weights
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def _annotate_rejection_history(self, rec: dict, history: dict) -> dict:
        """
        If this parameter was previously rejected (but is now past the 14d cooldown
        or has a different suggested value), prepend a note to the rationale so the
        reviewer knows it's a recurring pattern.
        """
        param     = rec.get("parameter", "")
        suggested = rec.get("suggested_value")
        now       = datetime.utcnow()

        rejections = history.get(param, {}).get("rejected", [])
        past_rejections = [
            d for d in rejections
            if d["reviewed_at"] and (now - d["reviewed_at"]).days >= 14
        ]
        if not past_rejections:
            return rec

        latest = max(past_rejections, key=lambda d: d["reviewed_at"])
        age    = (now - latest["reviewed_at"]).days
        note   = (
            f"[Previously rejected {age}d ago"
            + (f" (suggested {latest['suggested_value']})" if latest["suggested_value"] != suggested else "")
            + f" — pattern persists] "
        )
        rec = dict(rec)
        rec["rationale"] = note + rec.get("rationale", "")
        return rec

    # ── closed-loop auto-apply (Enhancement 5) ──────────────────────────────

    def _verify_applied_recommendations(self) -> int:
        """
        For recommendations applied >= VERIFICATION_DELAY_DAYS ago that haven't
        been verified yet, re-measure the metric named in their evidence and
        record whether it actually improved. This is what makes a recommendation
        category eligible for auto-apply — see _is_pattern_auto_apply_eligible.
        """
        delay_days    = self._pi_setting("verification_delay_days", VERIFICATION_DELAY_DAYS)
        verify_cutoff = datetime.utcnow() - timedelta(days=delay_days)
        try:
            pending = (
                self.db.query(OptimizationRecommendationModel)
                .filter(
                    OptimizationRecommendationModel.applied.is_(True),
                    OptimizationRecommendationModel.applied_at <= verify_cutoff,
                    OptimizationRecommendationModel.outcome_verified_at.is_(None),
                )
                .all()
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not load recs pending verification: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
            return 0

        if not pending:
            return 0

        data_cutoff = datetime.utcnow() - timedelta(days=ANALYSIS_DAYS)
        resolved = (
            self.db.query(WorkflowStateModel)
            .filter(
                WorkflowStateModel.lifecycle_state.in_(["resolved", "closed"]),
                WorkflowStateModel.updated_at >= data_cutoff,
                WorkflowStateModel.workflow_type == "incident",
            )
            .all()
        )
        cfg     = self.risk_repo.get_by_key("default")
        weights = cfg.weights if cfg else {}
        fresh_summary = self._aggregate_outcomes(resolved, weights, ANALYSIS_DAYS) if resolved else None

        verified = 0
        for rec in pending:
            rec.outcome_improved   = self._measure_outcome_improved(rec, fresh_summary, resolved)
            rec.outcome_verified_at = datetime.utcnow()
            verified += 1

        try:
            self.db.commit()
        except Exception as e:
            logger.warning(f"[TuningAgent] Failed to commit verification results: {e}")
            self.db.rollback()
            return 0
        return verified

    def _measure_outcome_improved(
        self,
        rec: OptimizationRecommendationModel,
        fresh_summary: Optional[dict],
        resolved: Optional[list] = None,
    ) -> Optional[bool]:
        """
        Compare the metric named in rec.evidence (metric_name/metric_value/metric_scope,
        snapshotted at recommendation time) against its current value. All metrics
        tracked today (false_positive_rate, noise_rate) are "lower is better" — returns
        True if the current value is lower than the snapshot. Returns None if there
        isn't enough data to judge either way.
        """
        evidence = rec.evidence or {}
        metric_name  = evidence.get("metric_name")
        before_value = evidence.get("metric_value")
        if metric_name is None or before_value is None or fresh_summary is None:
            return None

        scope = evidence.get("metric_scope")
        current_value = None
        if metric_name == "false_positive_rate":
            current_value = fresh_summary.get("system_health", {}).get("false_positive_rate")
        elif metric_name == "noise_rate" and scope:
            domain_stats = fresh_summary.get("domain_stats", {})
            if scope in domain_stats:
                current_value = domain_stats[scope].get("noise_rate")
            else:
                et_stats = fresh_summary.get("top_event_types", {})
                if scope in et_stats:
                    current_value = et_stats[scope].get("noise_rate")
                elif resolved is not None:
                    # Resource-scoped recs (Enhancement 3) aren't in the ~2KB LLM
                    # summary by design — recompute this one resource's noise rate
                    # directly rather than bloating that payload for every resource.
                    resource_incidents = [
                        w for w in resolved
                        if ((w.context or {}).get("cmdb") or {}).get("resource_name") == scope
                        or ((w.context or {}).get("alert_payload") or {}).get("resource_name") == scope
                    ]
                    if len(resource_incidents) >= MIN_INCIDENTS_FOR_SIGNAL:
                        noise = sum(
                            1 for w in resource_incidents
                            if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
                        )
                        current_value = noise / len(resource_incidents)

        if current_value is None:
            return None

        try:
            return float(current_value) < float(before_value)
        except (TypeError, ValueError):
            return None

    def _is_pattern_auto_apply_eligible(self, parameter: str) -> bool:
        """
        True once this exact parameter has earned auto-apply trust: the most
        recent AUTO_APPLY_MIN_CYCLES decisions were all accepted-or-auto-applied,
        applied, and independently verified to have improved their metric, with
        no break in that streak.

        Recomputed fresh from history on every call rather than stored as a sticky
        flag — so a single outcome_improved=False breaks the streak count on the
        very next check and the pattern falls back to mandatory review automatically.
        No separate "revoke" step needed; this is deliberate, not an oversight.
        """
        if not parameter:
            return False
        min_cycles = self._pi_setting("auto_apply_min_cycles", AUTO_APPLY_MIN_CYCLES)
        try:
            rows = (
                self.db.query(OptimizationRecommendationModel)
                .filter(
                    OptimizationRecommendationModel.parameter == parameter,
                    OptimizationRecommendationModel.status.in_(["accepted", "auto_applied"]),
                )
                .order_by(OptimizationRecommendationModel.created_at.desc())
                .limit(min_cycles)
                .all()
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not check auto-apply eligibility for '{parameter}': {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
            return False

        if len(rows) < min_cycles:
            return False
        return all(r.applied and r.outcome_verified_at is not None and r.outcome_improved is True for r in rows)

    # Mirrors api/routes/platform_intelligence.py's _FLAT_KEY_PARAM_MAPS — kept local
    # to this agent to avoid a routes→agent→routes circular import. See that module's
    # _write_param docstring: event_type_multipliers/resource_overrides/domain_multipliers
    # are FLAT maps whose keys may themselves contain dots (taxonomy codes, hostnames),
    # and must be written as one literal key, not split into nested dicts.
    _FLAT_KEY_PARAM_MAPS = {"event_type_multipliers", "resource_overrides", "domain_multipliers", "environment_multipliers"}

    def _write_weights_param(self, weights: dict, param: str, value) -> bool:
        """Write a dotted-path parameter into the weights dict in place — see
        _FLAT_KEY_PARAM_MAPS above for why flat taxonomy-code/resource-name keys
        must not be split further."""
        parts = param.split(".")
        if not parts or not parts[0]:
            logger.warning(f"[TuningAgent] Cannot auto-apply empty param path: {param!r}")
            return False

        if parts[0] in self._FLAT_KEY_PARAM_MAPS and len(parts) >= 2:
            flat_key = ".".join(parts[1:])
            weights.setdefault(parts[0], {})[flat_key] = value
            return True

        node = weights
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                logger.warning(f"[TuningAgent] Cannot auto-apply param path {param!r} — '{part}' is not a dict in weights")
                return False
        node[parts[-1]] = value
        return True

    def _apply_rec_to_weights(self, rec: dict, weights: dict) -> bool:
        """Apply an auto-eligible rec dict to the live risk weight config. Supports the
        same two modes as the manual accept path: single-parameter (rec['parameter'] +
        rec['suggested_value']) and multi-parameter (evidence.parameter_changes list)."""
        if rec.get("category") in ("runbook_step", "governance"):
            # These categories describe changes to RunbookModel/PolicyModel rows,
            # which are gated by the draft/publish workflow (see
            # RunbookRepository.publish / PolicyRepository.publish in
            # db/repositories.py) — never a direct column write. This function only
            # writes RiskWeightConfigModel.weights and must never be extended to
            # setattr a runbook/policy directly. If a future change makes these
            # categories carry a concrete suggested_value, route it through
            # save_draft()+publish() instead of through this function.
            return False
        if rec.get("category") == "general" or rec.get("suggested_value") is None:
            return False

        import copy
        updated = copy.deepcopy(weights)

        param_changes = (rec.get("evidence") or {}).get("parameter_changes")
        if param_changes:
            applied_any = False
            for change in param_changes:
                if self._write_weights_param(updated, change["parameter"], change["suggested_value"]):
                    applied_any = True
            if not applied_any:
                return False
            self.risk_repo.create_or_update("default", updated)
            return True

        if not self._write_weights_param(updated, rec["parameter"], rec["suggested_value"]):
            return False
        self.risk_repo.create_or_update("default", updated)
        return True

    # ── deterministic checks (fallback) ─────────────────────────────────────

    def _check_false_positive_rate(self, resolved, weights, period_days):
        recs  = []
        total = len(resolved)
        if total < MIN_INCIDENTS_FOR_SIGNAL:
            return recs

        fp_count = sum(
            1 for w in resolved
            if (
                (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
                or (
                    w.created_at
                    and ((w.resolved_at or w.updated_at) - w.created_at).total_seconds() < 120
                    and w.resolution_source not in ("automated_remediation", "watcher_all_clear")
                )
            )
        )
        fp_rate = fp_count / total
        if fp_rate < FP_RATE_THRESHOLD:
            return recs

        current   = float(weights.get("qualification_threshold", 50.0))
        suggested = min(70.0, round(current + fp_rate * 25, 1))
        if suggested <= current:
            return recs

        recs.append({
            "category":        "threshold",
            "parameter":       "qualification_threshold",
            "current_value":   current,
            "suggested_value": suggested,
            "title":           "Raise qualification threshold to reduce false positives",
            "rationale": (
                f"{fp_count} of {total} incidents ({fp_rate*100:.0f}%) closed as noise or "
                f"self-healed in under 2 minutes over the past {period_days} days. "
                f"Raising the threshold from {current} to {suggested} should filter these out."
            ),
            "impact":     f"Estimated {fp_rate*100:.0f}% reduction in noise incidents",
            "confidence": min(0.9, 0.5 + fp_rate),
            "priority":   "high" if fp_rate >= 0.35 else "medium",
            "evidence": {
                "incidents_analysed": total, "false_positive_count": fp_count,
                "false_positive_rate": round(fp_rate, 3), "period_days": period_days,
                "metric_name": "false_positive_rate", "metric_value": round(fp_rate, 3), "metric_scope": None,
            },
            "expires_at": self._expires_at(),
        })
        return recs

    def _check_automation_rate(self, resolved, weights, period_days):
        recs  = []
        total = len(resolved)
        if total < MIN_INCIDENTS_FOR_SIGNAL:
            return recs

        automated = sum(1 for w in resolved if w.resolution_source == "automated_remediation")
        auto_rate = automated / total
        if auto_rate >= AUTO_RATE_LOW:
            return recs

        recs.append({
            "category":        "general",
            "parameter":       "automation_rate",
            "current_value":   round(auto_rate, 3),
            "suggested_value": None,
            "title":           "Low automated remediation rate — review runbook coverage",
            "rationale": (
                f"Only {automated}/{total} incidents ({auto_rate*100:.0f}%) were resolved by "
                f"automated remediation over the past {period_days} days. "
                f"Review runbook coverage for the most common event types."
            ),
            "impact":     "Improved automation reduces MTTR for recurring event types",
            "confidence": 0.85,
            "priority":   "medium" if auto_rate >= 0.15 else "high",
            "evidence": {
                "incidents_analysed": total, "automated_count": automated,
                "automation_rate": round(auto_rate, 3), "period_days": period_days,
            },
            "expires_at": self._expires_at(),
        })
        return recs

    def _check_mttr(self, resolved, weights, period_days):
        recs = []
        p1p2 = [
            w for w in resolved
            if w.context
            and str(w.context.get("incident_priority", "")).upper() in ("P1", "P2")
            and w.created_at and (w.resolved_at or w.updated_at)
        ]
        if not p1p2:
            return recs

        avg_h = (
            sum(((w.resolved_at or w.updated_at) - w.created_at).total_seconds() for w in p1p2)
            / len(p1p2) / 3600
        )
        if avg_h <= MTTR_HIGH_HOURS:
            return recs

        recs.append({
            "category":        "general",
            "parameter":       "mttr_p1p2",
            "current_value":   round(avg_h, 2),
            "suggested_value": None,
            "title":           f"High average MTTR for P1/P2 incidents ({avg_h:.1f}h)",
            "rationale": (
                f"P1/P2 incidents averaged {avg_h:.1f}h to resolve over the past {period_days} days "
                f"(across {len(p1p2)} incidents). Target is under {MTTR_HIGH_HOURS}h. "
                f"Review governance thresholds, runbook quality, and escalation paths."
            ),
            "impact":     "Faster P1/P2 resolution reduces business impact and SLA penalties",
            "confidence": 0.80,
            "priority":   "high" if avg_h >= MTTR_HIGH_HOURS * 2 else "medium",
            "evidence": {
                "p1p2_incidents": len(p1p2), "avg_mttr_hours": round(avg_h, 2),
                "threshold_hours": MTTR_HIGH_HOURS, "period_days": period_days,
            },
            "expires_at": self._expires_at(),
        })
        return recs

    def _check_priority_automation_coverage(self, resolved, weights, period_days):
        recs = []
        p1p2 = [
            w for w in resolved
            if w.context and str(w.context.get("incident_priority", "")).upper() in ("P1", "P2")
        ]
        if len(p1p2) < 2:
            return recs

        automated = sum(1 for w in p1p2 if w.resolution_source == "automated_remediation")
        auto_rate = automated / len(p1p2)
        if auto_rate >= 0.50:
            return recs

        recs.append({
            "category":        "general",
            "parameter":       "p1p2_automation_rate",
            "current_value":   round(auto_rate, 3),
            "suggested_value": None,
            "title": (
                f"Only {automated}/{len(p1p2)} P1/P2 incidents auto-remediated "
                f"({auto_rate*100:.0f}%) — expand high-priority runbook coverage"
            ),
            "rationale": (
                f"Over the past {period_days} days, {len(p1p2) - automated} of {len(p1p2)} P1/P2 incidents "
                f"required manual resolution. For your highest-priority incidents this increases MTTR. "
                f"Review whether runbooks exist for the top P1/P2 event types."
            ),
            "impact":     "Faster P1/P2 resolution; reduced on-call burden",
            "confidence": 0.85,
            "priority":   "high" if auto_rate < 0.25 else "medium",
            "evidence": {
                "p1p2_total": len(p1p2), "automated_count": automated,
                "automation_rate": round(auto_rate, 3), "period_days": period_days,
            },
            "expires_at": self._expires_at(),
        })
        return recs

    def _check_cmdb_priority_coverage(self, resolved, weights, period_days):
        recs = []
        scores = [
            float((w.context or {}).get("risk_breakdown", {}).get("confidence_score", 0))
            for w in resolved
            if w.context
            and str(w.context.get("incident_priority", "")).upper() in ("P1", "P2")
            and (w.context.get("risk_breakdown") or {}).get("confidence_score") is not None
        ]
        if len(scores) < 2:
            return recs

        avg = sum(scores) / len(scores)
        if avg >= 50.0:
            return recs

        factors   = weights.get("factors", {})
        pessimistic = [
            (name, cfg) for name, cfg in factors.items()
            if isinstance(cfg, dict) and cfg.get("cmdb_sourced") and cfg.get("missing_data") == "pessimistic"
        ]
        if not pessimistic:
            return recs

        param_changes = [
            {"parameter": f"factors.{name}.missing_data", "current_value": "pessimistic", "suggested_value": "neutral",
             "label": f"{name} ({cfg.get('weight', '?')} pts)"}
            for name, cfg in pessimistic
        ]
        total_pts = sum(cfg.get("weight", 0) for _, cfg in pessimistic)
        n = len(pessimistic)

        recs.append({
            "category":        "missing_data",
            "parameter":       "factors.missing_data",
            "current_value":   {name: "pessimistic" for name, _ in pessimistic},
            "suggested_value": {name: "neutral"     for name, _ in pessimistic},
            "title": (
                f"Switch {n} pessimistic factor{'s' if n > 1 else ''} to neutral — "
                f"P1/P2 CMDB coverage is only {avg:.0f}%"
            ),
            "rationale": (
                f"P1/P2 incidents averaged only {avg:.0f}% CMDB coverage over {period_days} days. "
                f"Pessimistic missing_data adds up to {total_pts} pts to risk scores when CMDB is absent, "
                f"potentially forcing unnecessary manual approval gates."
            ),
            "impact": f"Up to {total_pts} pts removed from P1/P2 scores with incomplete CMDB data",
            "confidence": 0.80,
            "priority": "high" if avg < 30 else "medium",
            "evidence": {
                "p1p2_incidents_with_cmdb": len(scores), "avg_coverage_pct": round(avg, 1),
                "period_days": period_days, "parameter_changes": param_changes,
            },
            "expires_at": self._expires_at(),
        })
        return recs

    def _check_event_type_multipliers(self, resolved, weights, period_days):
        """Flag per-type overrides with high noise rates."""
        recs = []
        multipliers = weights.get("event_type_multipliers", {})
        if not multipliers:
            return recs

        by_type: dict[str, list] = {}
        for w in resolved:
            et = (w.context or {}).get("anomaly_type") or (w.context or {}).get("event_type")
            if et:
                by_type.setdefault(et, []).append(w)

        for et, incidents in by_type.items():
            if len(incidents) < 5:
                continue
            multiplier = multipliers.get(et)
            if not multiplier or multiplier <= 1.0:
                continue

            noise = sum(1 for w in incidents if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate"))
            noise_rate = noise / len(incidents)
            if noise_rate < 0.40:
                continue

            suggested = round(max(0.5, multiplier * (1.0 - noise_rate * 0.5)), 2)
            if suggested >= multiplier:
                continue

            recs.append({
                "category":        "event_multiplier",
                "parameter":       f"event_type_multipliers.{et}",
                "current_value":   multiplier,
                "suggested_value": suggested,
                "title":           f"Reduce override multiplier for '{et}' — {noise_rate*100:.0f}% noise rate",
                "rationale": (
                    f"{noise}/{len(incidents)} ({noise_rate*100:.0f}%) of '{et}' incidents over "
                    f"{period_days} days were closed as noise or duplicates. "
                    f"Reducing the multiplier from {multiplier} to {suggested} will lower their "
                    f"qualification scores."
                ),
                "impact":     f"~{noise_rate*100:.0f}% fewer '{et}' incidents escalated",
                "confidence": min(0.85, 0.5 + noise_rate * 0.5),
                "priority":   "medium",
                "evidence": {
                    "event_type": et, "incident_count": len(incidents),
                    "noise_count": noise, "noise_rate": round(noise_rate, 3),
                    "current_multiplier": multiplier, "period_days": period_days,
                    "metric_name": "noise_rate", "metric_value": round(noise_rate, 3), "metric_scope": et,
                },
                "expires_at": self._expires_at(),
            })
        return recs

    def _check_domain_multipliers(self, resolved, weights, period_days):
        """
        Flag domains whose noise rate suggests the domain multiplier is too high
        or where saturation (all scores = 100) combined with noise indicates over-weighting.
        """
        recs = []
        try:
            from agentic_os.db.event_type_taxonomy_data import ALIAS_MAP
        except ImportError:
            ALIAS_MAP = {}

        domain_mults = weights.get("domain_multipliers", {})
        by_domain: dict[str, list] = {}
        for w in resolved:
            et = (w.context or {}).get("anomaly_type") or (w.context or {}).get("event_type") or ""
            canonical = ALIAS_MAP.get(et, et)
            domain    = canonical.split(".")[0] if "." in canonical else None
            if domain:
                by_domain.setdefault(domain, []).append(w)

        for domain, incidents in by_domain.items():
            if len(incidents) < MIN_INCIDENTS_FOR_SIGNAL:
                continue
            current_mult = domain_mults.get(domain)
            if current_mult is None:
                continue

            noise = sum(1 for w in incidents if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate"))
            noise_rate = noise / len(incidents)
            if noise_rate < 0.45:
                continue

            # Defer to the resource-specific check when one resource dominates this
            # domain's noise — a domain-wide multiplier change would needlessly
            # suppress signal from every other resource in the domain.
            if noise > 0:
                resource_noise: dict = defaultdict(int)
                for w in incidents:
                    if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate"):
                        ctx = w.context or {}
                        r = (ctx.get("cmdb") or {}).get("resource_name") or (ctx.get("alert_payload") or {}).get("resource_name")
                        if r:
                            resource_noise[r] += 1
                if resource_noise and max(resource_noise.values()) / noise >= 0.30:
                    continue

            # Only suggest lowering if it would actually change qualifying behaviour
            suggested = round(max(0.3, current_mult * (1.0 - noise_rate * 0.4)), 2)
            if suggested >= current_mult:
                continue

            recs.append({
                "category":        "domain_multiplier",
                "parameter":       f"domain_multipliers.{domain}",
                "current_value":   current_mult,
                "suggested_value": suggested,
                "title":           f"Reduce domain multiplier for '{domain}' — {noise_rate*100:.0f}% noise rate",
                "rationale": (
                    f"{noise}/{len(incidents)} ({noise_rate*100:.0f}%) of '{domain}' domain incidents "
                    f"over {period_days} days were closed as noise or duplicates. "
                    f"Lowering the domain multiplier from {current_mult} to {suggested} reduces "
                    f"qualification scores for all {domain}.* event types without specific overrides."
                ),
                "impact": f"Fewer false-positive incidents from the {domain} domain",
                "confidence": min(0.80, 0.45 + noise_rate * 0.5),
                "priority": "high" if noise_rate >= 0.65 else "medium",
                "evidence": {
                    "domain": domain, "incident_count": len(incidents),
                    "noise_count": noise, "noise_rate": round(noise_rate, 3),
                    "current_mult": current_mult, "period_days": period_days,
                    "metric_name": "noise_rate", "metric_value": round(noise_rate, 3), "metric_scope": domain,
                },
                "expires_at": self._expires_at(),
            })
        return recs

    def _check_resource_noise(self, resolved, weights, period_days):
        """
        Enhancement 3 — Resource-Level Noise Drill-Down.

        A single flapping resource often generates most of a domain's noise; lowering
        the domain multiplier to compensate suppresses signal for every OTHER resource
        in that domain too. This flags the specific resource instead, recommending a
        per-resource qualification override (weights["resource_overrides"]) — a new
        platform capability consulted by EventQualificationService before falling back
        to event-type/domain multipliers.
        """
        recs = []
        try:
            from agentic_os.db.event_type_taxonomy_data import ALIAS_MAP
        except ImportError:
            ALIAS_MAP = {}

        domain_mults        = weights.get("domain_multipliers", {})
        resource_overrides  = weights.get("resource_overrides", {})

        by_domain_resource: dict = defaultdict(lambda: defaultdict(list))
        for w in resolved:
            ctx = w.context or {}
            et = ctx.get("anomaly_type") or ctx.get("event_type") or ""
            canonical = ALIAS_MAP.get(et, et)
            domain    = canonical.split(".")[0] if "." in canonical else None
            resource  = (ctx.get("cmdb") or {}).get("resource_name") or (ctx.get("alert_payload") or {}).get("resource_name")
            if domain and resource:
                by_domain_resource[domain][resource].append(w)

        for domain, by_resource in by_domain_resource.items():
            domain_incidents = [w for incidents in by_resource.values() for w in incidents]
            domain_noise_total = sum(
                1 for w in domain_incidents if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
            )
            if domain_noise_total == 0:
                continue

            for resource, incidents in by_resource.items():
                if len(incidents) < MIN_INCIDENTS_FOR_SIGNAL or resource in resource_overrides:
                    continue
                resource_noise = sum(
                    1 for w in incidents if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
                )
                if resource_noise == 0:
                    continue
                share = resource_noise / domain_noise_total
                if share < 0.30:
                    continue

                current_mult = domain_mults.get(domain, weights.get("default_event_multiplier", 1.0))
                resource_noise_rate = resource_noise / len(incidents)
                suggested = round(max(0.3, current_mult * (1.0 - resource_noise_rate * 0.5)), 2)
                if suggested >= current_mult:
                    continue

                recs.append({
                    "category":        "resource_specific",
                    "parameter":       f"resource_overrides.{resource}",
                    "current_value":   current_mult,
                    "suggested_value": suggested,
                    "title": f"Resource '{resource}' drives {share*100:.0f}% of '{domain}' domain noise — add a resource override",
                    "rationale": (
                        f"'{resource}' accounted for {resource_noise} of {domain_noise_total} "
                        f"({share*100:.0f}%) noise incidents in the '{domain}' domain over {period_days} days, "
                        f"out of {len(incidents)} total incidents on that resource ({resource_noise_rate*100:.0f}% noise rate). "
                        f"A resource-specific override avoids suppressing signal from every other resource in "
                        f"the '{domain}' domain, which a domain-wide multiplier change would do."
                    ),
                    "impact": f"Reduces noise from '{resource}' without affecting other {domain} domain resources",
                    "confidence": min(0.85, 0.5 + share * 0.4),
                    "priority": "high" if share >= 0.6 else "medium",
                    "evidence": {
                        "domain": domain, "resource_name": resource,
                        "resource_incident_count": len(incidents), "resource_noise_count": resource_noise,
                        "resource_noise_rate": round(resource_noise_rate, 3),
                        "domain_noise_total": domain_noise_total, "noise_share_of_domain": round(share, 3),
                        "current_domain_mult": current_mult, "period_days": period_days,
                        "metric_name": "noise_rate", "metric_value": round(resource_noise_rate, 3), "metric_scope": resource,
                    },
                    "expires_at": self._expires_at(),
                })
        return recs

    def _check_runbook_step_health(self, resolved, weights, period_days):
        """
        Enhancement 1 — Runbook Step-Level Failure Analysis.

        RunbookModel.success_rate exists only at the whole-runbook level, so a 5-step
        runbook failing 30% of the time gives no indication of *which* step fails —
        which is the actual fix. Groups runbook_step_outcomes by (runbook_id,
        step_index) and flags steps with a high failure rate, naming the specific
        step rather than the runbook as a whole.
        """
        recs = []
        from agentic_os.db.models import RunbookStepOutcomeModel, RunbookModel

        cutoff = datetime.utcnow() - timedelta(days=period_days)
        try:
            rows = (
                self.db.query(RunbookStepOutcomeModel)
                .filter(
                    RunbookStepOutcomeModel.runbook_id.isnot(None),
                    RunbookStepOutcomeModel.created_at >= cutoff,
                )
                .all()
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not load runbook step outcomes: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
            return recs

        if not rows:
            return recs

        by_step: dict = defaultdict(list)
        for r in rows:
            by_step[(r.runbook_id, r.step_index)].append(r)

        for (runbook_id, step_index), outcomes in by_step.items():
            if len(outcomes) < MIN_INCIDENTS_FOR_SIGNAL:
                continue
            failed = [o for o in outcomes if o.status in ("failed", "timed_out")]
            failure_rate = len(failed) / len(outcomes)
            if failure_rate < 0.25:
                continue

            sample_error = next((o.error_message for o in failed if o.error_message), None)
            tool_name = next((o.tool for o in outcomes if o.tool), "unknown")

            # Enhancement 2 — root-cause breakdown distinguishes "this runbook is badly
            # written" from "this runbook is fine but fed stale/wrong data", which point
            # at completely different fixes.
            category_counts: dict = defaultdict(int)
            for o in failed:
                category_counts[o.failure_category or "unknown"] += 1
            dominant_category = max(category_counts, key=category_counts.get) if category_counts else "unknown"
            FIX_HINTS = {
                "target_not_found":   "likely a CMDB staleness issue (stale/wrong target resolved), not a runbook bug",
                "tool_error":         "likely a tool/connector reliability issue, not a runbook logic problem",
                "precondition_unmet": "likely a missing diagnostic-step gap in the runbook itself",
                "timeout":            "likely a performance/timeout issue with the target or the tool's execution",
                "permission_denied":  "likely a credentials/permissions issue in the tool's execution context",
                "partial_completion": "the action partially completes — check for idempotency on retry",
            }
            fix_hint = FIX_HINTS.get(dominant_category)

            runbook_name = str(runbook_id)
            try:
                rb = self.db.query(RunbookModel).filter(RunbookModel.id == runbook_id).first()
                if rb:
                    runbook_name = rb.name
            except Exception:
                pass

            recs.append({
                "category":        "runbook_step",
                "parameter":       f"runbook.{runbook_id}.step.{step_index}",
                "current_value":   None,
                "suggested_value": None,
                "title": f"Step {step_index} ({tool_name}) in '{runbook_name}' fails {failure_rate*100:.0f}% of the time",
                "rationale": (
                    f"Step {step_index} ('{tool_name}') of runbook '{runbook_name}' failed or timed out "
                    f"{len(failed)} of {len(outcomes)} times ({failure_rate*100:.0f}%) over the past "
                    f"{period_days} days — not the whole runbook, just this specific step. "
                    + (f"Most recent error: \"{sample_error}\". " if sample_error else "No error message was captured. ")
                    + (f"{dominant_category.replace('_', ' ').title()} accounts for most failures — {fix_hint}." if fix_hint else "")
                ),
                "impact": f"Fixing step {step_index} directly addresses this runbook's reliability, rather than treating the whole runbook as unreliable",
                "confidence": min(0.85, 0.5 + failure_rate * 0.4),
                "priority": "high" if failure_rate >= 0.5 else "medium",
                "evidence": {
                    "runbook_id": str(runbook_id), "runbook_name": runbook_name,
                    "step_index": step_index, "tool": tool_name,
                    "execution_count": len(outcomes), "failure_count": len(failed),
                    "failure_category_breakdown": dict(category_counts), "dominant_failure_category": dominant_category,
                    "failure_rate": round(failure_rate, 3),
                    "sample_error_message": sample_error,
                    "affected_workflow_ids": [str(o.workflow_id) for o in failed[:10]],
                    "period_days": period_days,
                },
                "expires_at": self._expires_at(),
            })
        return recs

    def _check_policy_effectiveness(self, resolved, weights, period_days):
        """
        Enhancement 4 — Governance Effectiveness Measurement.

        For each PolicyModel matched by at least MIN_INCIDENTS_FOR_SIGNAL approvals in
        the window, surface approval rate, average latency, and a best-effort catch-rate
        proxy. A policy approved near-100% of the time over a meaningful sample is a
        latency cost with no apparent safety value — flagged for a confidence-gate
        recommendation. Informational only (suggested_value=None): setting a confidence
        gate touches PolicyModel columns directly, not the RiskWeightConfig.weights JSON
        that the existing accept/apply path writes to, so this is surfaced for an operator
        to action manually rather than auto-applied.
        """
        recs = []
        from agentic_os.db.models import ApprovalModel, PolicyModel

        cutoff = datetime.utcnow() - timedelta(days=period_days)
        try:
            approvals = (
                self.db.query(ApprovalModel)
                .filter(
                    ApprovalModel.approval_type == "governance",
                    ApprovalModel.requested_at >= cutoff,
                )
                .all()
            )
        except Exception as e:
            logger.warning(f"[TuningAgent] Could not load approvals for policy effectiveness: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
            return recs

        if not approvals:
            return recs

        by_policy: dict = {}
        for a in approvals:
            policies = (a.extra_metadata or {}).get("matching_policies") or []
            if not policies:
                continue
            policy_id = policies[0].get("policy_id")
            if not policy_id:
                continue
            by_policy.setdefault(policy_id, []).append(a)

        if not by_policy:
            return recs

        # Built once, reused across all policies — resource_name → resolved incidents,
        # for the catch-rate proxy below.
        resource_incidents: dict = defaultdict(list)
        for w in resolved:
            ctx = w.context or {}
            resource = (ctx.get("cmdb") or {}).get("resource_name") or (ctx.get("alert_payload") or {}).get("resource_name")
            if resource:
                resource_incidents[resource].append(w)

        for policy_id, items in by_policy.items():
            if len(items) < MIN_INCIDENTS_FOR_SIGNAL:
                continue

            decided = [a for a in items if a.status in ("approved", "rejected")]
            if not decided:
                continue
            approved = [a for a in decided if a.status == "approved"]
            rejected = [a for a in decided if a.status == "rejected"]
            approval_rate = len(approved) / len(decided)

            latencies = [
                (a.decided_at - a.requested_at).total_seconds() / 60
                for a in decided if a.decided_at and a.requested_at
            ]
            avg_latency_min = round(sum(latencies) / len(latencies), 1) if latencies else None

            # Best-effort catch-rate proxy — weak signal only, not authoritative.
            catch_signals = 0
            for a in rejected:
                resource = (a.incident_summary or {}).get("resource")
                if not resource:
                    continue
                window_end = a.decided_at or a.requested_at
                for w in resource_incidents.get(resource, []):
                    if w.created_at and window_end and 0 <= (w.created_at - window_end).days <= 14:
                        if w.remediation_outcome in ("failed", "aborted"):
                            catch_signals += 1
                            break
            catch_rate_proxy = round(catch_signals / len(rejected), 3) if rejected else None

            policy_name = items[0].extra_metadata.get("matching_policies", [{}])[0].get("name", policy_id)

            policy_row = None
            try:
                policy_row = self.db.query(PolicyModel).filter(PolicyModel.policy_id == policy_id).first()
            except Exception:
                pass
            gate_already_set = bool(policy_row and policy_row.confidence_gate_threshold and policy_row.confidence_gate_min_runs)

            high_approval = approval_rate >= 0.90 and not gate_already_set
            latency_text = f"{avg_latency_min:.0f} min" if avg_latency_min is not None else "n/a"

            if high_approval:
                title = f"Policy '{policy_name}' approved {approval_rate*100:.0f}% of the time — consider a confidence gate"
                rationale = (
                    f"Policy '{policy_name}' was approved {len(approved)} of {len(decided)} times "
                    f"({approval_rate*100:.0f}%) over the past {period_days} days, with an average "
                    f"approval latency of {latency_text}. This near-unanimous approval rate suggests "
                    f"the gate is adding latency without much apparent safety value. A confidence gate "
                    f"(e.g. ≥90% runbook confidence, ≥10 successful runs) would let proven "
                    f"remediations bypass manual approval while still catching low-confidence cases."
                )
                priority = "medium"
            else:
                title = f"Policy '{policy_name}' governance effectiveness — {approval_rate*100:.0f}% approval rate"
                rationale = (
                    f"Policy '{policy_name}' was approved {len(approved)} of {len(decided)} times "
                    f"({approval_rate*100:.0f}%) over the past {period_days} days, average approval "
                    f"latency {latency_text}. " + (
                        "A confidence gate is already configured for this policy."
                        if gate_already_set else
                        "Approval/rejection mix suggests the gate is actively discriminating — no change recommended."
                    )
                )
                priority = "low"

            recs.append({
                "category":        "governance",
                "parameter":       f"policies.{policy_id}.confidence_gate",
                "current_value":   None,
                "suggested_value": None,
                "title":           title,
                "rationale":       rationale,
                "impact":          f"Removes manual approval latency (~{latency_text}) for proven-reliable actions" if high_approval else None,
                "confidence":      min(0.85, 0.5 + approval_rate * 0.4),
                "priority":        priority,
                "evidence": {
                    "policy_id": policy_id, "policy_name": policy_name,
                    "decided_count": len(decided), "approved_count": len(approved),
                    "rejected_count": len(rejected), "approval_rate": round(approval_rate, 3),
                    "avg_latency_minutes": avg_latency_min,
                    "catch_rate_proxy": catch_rate_proxy,
                    "catch_rate_proxy_note": (
                        "Best-effort signal only — correlates rejections against subsequent "
                        "same-resource failures within 14 days. Not authoritative."
                    ),
                    "period_days": period_days,
                },
                "expires_at": self._expires_at(),
            })
        return recs

    def _check_cmdb_coverage(self, resolved, weights, period_days):
        recs = []
        scores = [
            float((w.context or {}).get("risk_breakdown", {}).get("confidence_score", 0))
            for w in resolved
            if (w.context or {}).get("risk_breakdown", {}).get("confidence_score") is not None
        ]
        if len(scores) < MIN_INCIDENTS_FOR_SIGNAL:
            return recs

        avg = sum(scores) / len(scores)
        threshold = float(weights.get("confidence_threshold", 70.0))
        if avg >= threshold:
            return recs

        recs.append({
            "category":        "general",
            "parameter":       "cmdb_coverage",
            "current_value":   round(avg, 1),
            "suggested_value": None,
            "title":           f"Low average CMDB coverage ({avg:.0f}%) — review data quality",
            "rationale": (
                f"Average CMDB coverage across {len(scores)} incidents is {avg:.0f}%, "
                f"below the confidence threshold of {threshold}%. "
                f"Missing data triggers pessimistic defaults for SPOF and failover factors, "
                f"inflating risk scores."
            ),
            "impact":     "More accurate risk scores; fewer forced manual approval gates",
            "confidence": 0.75,
            "priority":   "low" if avg >= 50 else "medium",
            "evidence": {
                "incidents_with_coverage": len(scores), "avg_coverage_pct": round(avg, 1),
                "confidence_threshold_pct": threshold, "period_days": period_days,
            },
            "expires_at": self._expires_at(),
        })
        return recs
