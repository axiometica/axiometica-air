"""Incident management agents with real CMDB integration"""

from typing import Dict, Optional
from agentic_os.agents.base import Agent
from agentic_os.core.models import WorkflowState, Severity, EventType, LifecycleState
from agentic_os.core.context_schema import (
    IncidentWorkflowContext,
    SentinelContext,
    AlertPayload,
    CMDBContext,
    ResourceInfo,
    RiskContext,
    RiskBreakdown,
    Proposal,
    RunbookStep,
    GovernanceContext,
    VerificationContext,
    VerificationResult,
)
from agentic_os.services.cmdb import get_cmdb
import logging
import re
import uuid

logger = logging.getLogger(__name__)


def _resolve_kill_api_base(watcher_name: str) -> str:
    """Return the Kill-API base URL for the watcher that detected the incident."""
    return _resolve_watcher_info(watcher_name)[0]


def _resolve_watcher_info(watcher_name: str) -> tuple:
    """
    Return (kill_api_url, adapter_mode) for the named watcher.

    Looks up both fields from watcher_registrations in one DB call so the
    execution layer can choose the right command variant for the environment.
    Falls back to (http://<watcher_name>:8080, "docker") if the row is missing.
    """
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import WatcherRegistrationModel
        db = SessionLocal()
        try:
            row = db.query(WatcherRegistrationModel).filter_by(
                watcher_name=watcher_name
            ).first()
            if row:
                url = (getattr(row, "kill_api_url", "") or "").rstrip("/") \
                      or f"http://{watcher_name}:8080"
                mode = getattr(row, "adapter_mode", "docker") or "docker"
                return url, mode
        finally:
            db.close()
    except Exception as _e:
        logger.debug(f"[WATCHER-ROUTE] DB lookup failed for '{watcher_name}': {_e}")
    return f"http://{watcher_name}:8080", "docker"


def _derive_platform(resource_type: str, cmdb_platform: str = "") -> str:
    """Map CMDB resource type + discovered platform property to a canonical platform token.

    Returns one of: docker | linux | windows | kubernetes | any

    Smart logic:
      - Containerized resource types (database, microservice, etc.) are assumed to be Docker
      - The cmdb_platform field indicates the OS (linux/windows), not the deployment model
      - In cloud-native environments, services run in containers regardless of OS
    """
    t = (resource_type or "").lower()
    p = (cmdb_platform or "").lower()

    # Explicitly named deployment platforms take precedence
    _KNOWN_PLATFORMS = {"docker", "kubernetes", "k8s", "windows"}
    if p in _KNOWN_PLATFORMS:
        if p in ("k8s", "kubernetes"):
            return "kubernetes"
        return p  # "docker" | "windows" returned verbatim

    # Check for explicit platform keywords in resource type
    if "windows" in t or p == "windows":
        return "windows"
    if t in ("pod", "k8s") or "kubernetes" in t:
        return "kubernetes"
    if t == "container" or "container" in t:
        return "docker"

    # Smart inference: containerized services (even if OS is 'linux')
    # are deployed in Docker unless explicitly marked as VMs/bare-metal
    _CONTAINERIZED_TYPES = {
        "microservice", "worker", "web-application", "frontend",
        "ai-agent", "database", "cache", "graph-database",
        "monitoring", "api", "service", "job",
    }
    if t in _CONTAINERIZED_TYPES:
        # Containerized services are Docker-hosted in cloud-native deployments
        # (cmdb_platform like 'linux' just means the container OS, not the deployment)
        return "docker"

    # Explicit bare-metal/VM indicators
    if t in ("vm", "host", "server") or "linux" in t or "server" in t:
        return "linux"

    # Secondary fallback: if cmdb_platform says linux/unix, assume bare metal
    if p in ("linux", "unix"):
        return "linux"

    # Unknown platform string → Docker (safest for cloud-native)
    if p:
        return "docker"

    return "any"


def _lookup_runbook(event_type: str, service: str, platform: str = "any") -> Optional[object]:
    """4-pass cascade runbook lookup with platform awareness.

    Pass order (most specific → most generic):
      1. event_type + service + platform   (exact service & platform match)
      2. event_type + service + 'any'      (service match, platform-agnostic runbook)
      3. event_type + no-service + platform (generic for this platform)
      4. event_type + no-service + 'any'   (fully generic fallback)

    Within each pass, runbooks ranked by: success_rate DESC (nulls last), confidence DESC.
    """
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import RunbookModel
        from sqlalchemy import desc, case, or_
        from agentic_os.connectors.event_type_utils import normalize_event_type
        event_type = normalize_event_type(event_type)
        db = SessionLocal()
        try:
            # Build ordered list of (service_val, platform_val) to try
            passes: list = []
            if service:
                if platform and platform != "any":
                    passes.append((service, platform))   # Pass 1
                passes.append((service, "any"))          # Pass 2
            if platform and platform != "any":
                passes.append((None, platform))          # Pass 3
            passes.append((None, "any"))                 # Pass 4

            for svc_val, plat_val in passes:
                q = db.query(RunbookModel).filter(
                    RunbookModel.event_type == event_type,
                    RunbookModel.enabled == True,
                    RunbookModel.status == "published",
                    RunbookModel.platform == plat_val,
                )
                if svc_val is not None:
                    q = q.filter(RunbookModel.service == svc_val)
                else:
                    # Treat both NULL and '' as "no specific service"
                    q = q.filter(or_(RunbookModel.service == None, RunbookModel.service == ""))

                # Rank: success_rate DESC (treat NULL as 0), then confidence DESC
                rb = q.order_by(
                    case(
                        (RunbookModel.success_rate == None, 0),
                        else_=RunbookModel.success_rate
                    ).desc(),
                    desc(RunbookModel.confidence),
                ).first()

                if rb:
                    return rb

            return None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Runbook lookup failed: {e}")
        return None


def _lookup_runbook_by_id(runbook_id) -> Optional[object]:
    """Fetch a specific runbook by ID — used when a policy pins the confidence
    gate to one named runbook instead of the event_type/service/platform cascade."""
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import RunbookModel
        db = SessionLocal()
        try:
            # A confidence gate must never evaluate against a runbook that's
            # mid-draft-edit — only published content is eligible.
            return db.query(RunbookModel).filter(
                RunbookModel.id == runbook_id,
                RunbookModel.status == "published",
            ).first()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Runbook lookup by id failed: {e}")
        return None


class SentinelAgent(Agent):
    """Classifies incident severity based on alert payload.

    Phase 10: Refactored to use typed IncidentWorkflowContext.
    Creates sentinel context layer with alert payload.
    """

    def __init__(self):
        super().__init__("sentinel")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Triage incident and set severity. Create sentinel context layer."""
        # Get or create typed context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # Read alert from untyped context (input)
        alert = state.context.get("alert_payload", {})
        alert_severity = alert.get("severity", "medium").lower()
        alert_type = alert.get("type", "unknown")
        description = alert.get("description", "")
        # "anomaly_process" for eBPF/syscall alerts; "process_name" for HTTP/external alerts
        anomaly_process = alert.get("anomaly_process") or alert.get("process_name")

        severity_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
            "info": Severity.INFO,
        }

        state.severity = severity_map.get(alert_severity, Severity.MEDIUM)

        # Build a meaningful title from the alert payload fields.
        # monitoring_events.py sets state.title before queuing but SentinelAgent
        # is the first Celery step and would overwrite it with "Unknown Incident"
        # if we blindly fall back to that default.
        title = alert.get("title")
        if not title:
            event_type = alert.get("type", "incident")
            resource = alert.get("resource_name", "unknown resource")
            process = alert.get("anomaly_process")
            type_display = event_type.replace("_", " ").title()
            title = (
                f"{type_display} on {resource} (process: {process})"
                if process
                else f"{type_display} on {resource}"
            )
        state.title = title

        # Create AlertPayload (typed)
        alert_payload = AlertPayload(
            type=alert_type,
            message=description,
            severity=alert_severity,
            anomaly_process=anomaly_process,
        )

        # Create SentinelContext (typed) and add to workflow context
        import datetime
        ctx.sentinel = SentinelContext(
            detected_anomaly=alert_type,
            anomaly_type=alert_type,
            alert_payload=alert_payload,
            timestamp=datetime.datetime.utcnow().isoformat(),
            confidence=0.95,  # Default confidence for direct alerts
        )

        # Persist typed context
        state = self._set_typed_context(state, ctx)

        reasoning = (
            f"[SENTINEL AGENT] Classified incident as {state.severity.value.upper()}\n"
            f"  Alert Type: {alert_type}\n"
            f"  Description: {description}\n"
            f"  Reasoning: Incident severity was explicitly set to '{alert_severity}' in the alert payload"
        )
        state = self._add_trace(state, reasoning)
        return state


class LibrarianAgent(Agent):
    """Enriches incident with context from Neo4j CMDB.

    Phase 10: Refactored to use typed CMDBContext.
    CRITICAL FIX: Extracts environment to top level of cmdb_context for policy matching.
    """

    def __init__(self):
        super().__init__("librarian")
        self.cmdb = get_cmdb()

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Enrich incident with CMDB context. Create cmdb context layer."""
        # Get existing typed context from SentinelAgent
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # ISSUE A FIX: Validate that Sentinel context exists (previous agent succeeded)
        if not self._validate_context_layer(state, "sentinel", ["alert_payload"]):
            return self._handle_missing_context(
                state, "sentinel",
                "Sentinel triage must complete before CMDB enrichment"
            )

        # CRITICAL: Read resource_name from untyped context alert_payload
        # (preserved in set_context for backward compatibility)
        alert = state.context.get("alert_payload", {})
        resource_name = alert.get("resource_name", "unknown")

        # Query real CMDB
        logger.debug(f"[LIBRARIAN] Querying CMDB for resource: {resource_name}")
        resource_info = self.cmdb.get_resource_info(resource_name)
        logger.debug(f"[LIBRARIAN] CMDB query result: {resource_info}")

        if not resource_info and resource_name != "unknown":
            logger.warning(f"[LIBRARIAN] Resource '{resource_name}' not found in CMDB")

        dependencies = self.cmdb.get_dependencies(resource_name, depth=2)
        impacted = self.cmdb.get_impacted_services(resource_name)
        historical = self.cmdb.get_historical_incidents(resource_name, limit=3)

        # CRITICAL FIX: Extract environment from resource_info for policy matching
        # Default to "production" when the CI node exists but lacks the environment
        # property — all seeded platform services are production deployments.
        # Use "unknown" only when the CI is completely absent from the CMDB.
        if resource_info and isinstance(resource_info, dict):
            raw_env = (resource_info.get("environment") or "").strip()
            # Normalise "prod" shorthand → "production"
            environment = "production" if raw_env in ("prod", "") else raw_env
        else:
            environment = "unknown"  # CI genuinely not in CMDB — don't assume env

        # Write degraded health status back to Neo4j immediately so the CMDB graph
        # reflects the active incident before any agent runs remediation.
        if resource_name != "unknown":
            alert_severity = alert.get("severity", "medium")
            anomaly_type = alert.get("type", "unknown")
            try:
                self.cmdb.mark_ci_degraded(
                    resource_name=resource_name,
                    workflow_id=str(state.workflow_id),
                    severity=alert_severity,
                    anomaly_type=anomaly_type,
                )
            except Exception as _cmdb_err:
                logger.warning(f"[LIBRARIAN] CMDB health write-back failed (non-fatal): {_cmdb_err}")

        # Create typed ResourceInfo
        resource_info_dict = resource_info or {}

        # Derive target platform from CMDB resource type + Docker-discovered platform field
        # Derive deployment platform from resource_type + CMDB platform
        # Smart logic: 'graph-database' with cmdb_platform='linux' → infers Docker deployment
        derived_platform = _derive_platform(
            resource_type=resource_info_dict.get("type", ""),
            cmdb_platform=resource_info_dict.get("platform", ""),
        )

        resource_obj = ResourceInfo(
            name=resource_info_dict.get("name", resource_name),
            type=resource_info_dict.get("type", "unknown"),
            status=resource_info_dict.get("status", "unknown"),
            owner=resource_info_dict.get("owner", "unknown"),
            environment=environment,
            criticality=resource_info_dict.get("business_criticality"),  # correct field name
            platform=derived_platform,
        )

        # Preserve all raw CMDB fields for RiskAssessor scoring
        # (ResourceInfo only carries the typed subset; the full dict goes in cmdb_context)
        raw_cmdb = {
            "name":                resource_info_dict.get("name", resource_name),
            "type":                resource_info_dict.get("type", "unknown"),
            "status":              resource_info_dict.get("status", "unknown"),
            "owner":               resource_info_dict.get("owner", "unknown"),
            "environment":         environment,
            "platform":            derived_platform,
            "business_criticality": resource_info_dict.get("business_criticality"),
            "ci_tier":             resource_info_dict.get("ci_tier"),
            "user_count":          resource_info_dict.get("user_count"),
            "is_spof":             resource_info_dict.get("is_spof"),
            "sla_percent":         resource_info_dict.get("sla_percent"),
            "failover_available":  resource_info_dict.get("failover_available"),
            "compliance_scope":    resource_info_dict.get("compliance_scope"),
            "description":         resource_info_dict.get("description"),
        }

        # Create CMDBContext with environment and platform at top level
        # logger.info(
        #     f"[LIBRARIAN] Creating CMDBContext: resource_type={resource_info.get('type')}, "
        #     f"derived_platform={derived_platform}, environment={environment}"
        # )
        ctx.cmdb = CMDBContext(
            resource_name=resource_name,
            resource_info=resource_obj,
            environment=environment,      # Top level for easy policy matching
            platform=derived_platform,    # Top level for runbook selection
            dependencies=dependencies or [],
            impacted_services=impacted or [],
            cmdb_context=raw_cmdb,        # Full raw dict for downstream scoring
        )

        # Also keep untyped context for backward compatibility (includes all scoring fields)
        cmdb_context_dict = {
            "resource_name":        resource_name,
            "resource_info":        resource_info or {"name": resource_name, "type": "unknown"},
            "environment":          environment,       # CRITICAL: at top level
            "platform":             derived_platform,  # for runbook selection
            "dependencies":         dependencies or [],
            "impacted_services":    impacted or [],
            "historical_incidents": historical or [],
            # Scoring fields from CMDB — carried forward for LLM prompt enrichment
            "business_criticality": resource_info_dict.get("business_criticality"),
            "ci_tier":              resource_info_dict.get("ci_tier"),
            "user_count":           resource_info_dict.get("user_count"),
            "is_spof":              resource_info_dict.get("is_spof"),
            "sla_percent":          resource_info_dict.get("sla_percent"),
            "failover_available":   resource_info_dict.get("failover_available"),
            "compliance_scope":     resource_info_dict.get("compliance_scope"),
        }
        state = self._set_context(state, "cmdb_context", cmdb_context_dict)

        # Persist typed context
        state = self._set_typed_context(state, ctx)

        # Build detailed reasoning
        dep_count = len(dependencies) if dependencies else 0
        impact_count = len(impacted) if impacted else 0

        # Handle None resource_info safely
        resource_info_safe = resource_info or {}
        resource_summary = f"Service: {resource_info_safe.get('name', resource_name)}"
        if resource_info:
            resource_summary += f"\n    Type: {resource_info.get('type', 'unknown')}"
            biz_crit = resource_info.get("business_criticality") or resource_info.get("criticality")
            if biz_crit:
                resource_summary += f"\n    Criticality: {biz_crit}"
            if resource_info.get("ci_tier") is not None:
                resource_summary += f"\n    CI Tier: {resource_info.get('ci_tier')}"
            if resource_info.get("user_count") is not None:
                resource_summary += f"\n    User Count: {resource_info.get('user_count'):,}"

        reasoning = (
            f"[LIBRARIAN AGENT] Enriched incident with CMDB context\n"
            f"  Resource: {resource_summary}\n"
            f"  Environment: {environment}\n"
            f"  Platform: {derived_platform}\n"
            f"  Dependencies (depth=2): {dep_count} services"
        )

        if dep_count > 0:
            dep_names = [d.get('name', 'unknown') for d in (dependencies or [])]
            reasoning += f"\n    - {', '.join(dep_names)}"

        reasoning += f"\n  Dependent Services (Blast Radius): {impact_count} services"

        if impact_count > 0:
            impact_names = [s.get('name', 'unknown') for s in (impacted or [])]
            reasoning += f"\n    - {', '.join(impact_names)}"

        if historical:
            reasoning += f"\n  Historical Incidents: {len(historical)} past occurrences"
            reasoning += "\n    Reasoning: Historical data helps predict likely root causes and successful remediation strategies"

        state = self._add_trace(state, reasoning)
        return state


class RiskAssessorAgent(Agent):
    """
    Full risk assessment with 10 weighted factors, confidence scoring, and severity/priority determination.

    Factors:
     1. Event severity         (raw criticality from monitoring)
     2. CI tier                (1=presentation, 2=application, 3=data — CMDB layer)
     3. Deployment environment (production > staging > development)
     4. Business criticality   (tier_1=mission-critical, tier_2=core, tier_3=infrastructure)
     5. User impact            (number of affected users)
     6. Blast radius           (dependent service count)
     7. Failover availability  (reduces risk when redundancy exists)
     8. SPOF status            (increases risk for single points of failure)
     9. SLA impact             (higher SLA commitments raise urgency)
    10. Historical incident frequency (repeat problems increase risk)
    """

    def __init__(self):
        super().__init__("risk_assessor")
        # Load weights from config on demand
        self._weights_cache = None

    def _get_weights(self):
        """Get risk weights, with fallback to defaults"""
        if self._weights_cache:
            return self._weights_cache

        from agentic_os.db.database import SessionLocal
        from agentic_os.db.repositories import RiskWeightConfigRepository
        from agentic_os.db.risk_weights_seed import DEFAULT_RISK_WEIGHTS

        try:
            db = SessionLocal()
            repo = RiskWeightConfigRepository(db)
            config = repo.get_by_key("default")
            db.close()

            if config:
                self._weights_cache = config.weights
                return config.weights
        except Exception:
            pass

        return DEFAULT_RISK_WEIGHTS.get("weights", {})

    async def run(self, state: WorkflowState) -> WorkflowState:
        """
        Assess incident risk with full weighted factor analysis.

        Updates context with:
        - assessed_severity: severity after CMDB enrichment (may differ from raw)
        - incident_priority: P1-P5 based on severity × business_criticality
        - risk_breakdown: detailed scoring by factor
        - confidence_score: % of weight from known CMDB data
        """

        weights = self._get_weights()

        # CRITICAL: Read from typed context populated by LibrarianAgent, not direct CMDB query
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # ISSUE A FIX: Validate that CMDB context exists
        if not self._validate_context_layer(state, "cmdb", ["resource_name", "environment"]):
            return self._handle_missing_context(
                state, "cmdb",
                "CMDB enrichment must complete before risk assessment"
            )

        initial_severity = state.severity or Severity.MEDIUM

        # Get CMDB data from typed context (populated by LibrarianAgent)
        ci_info = {}
        impacted_services = []
        historical_incidents = []

        if ctx.cmdb and ctx.cmdb.resource_info:
            # Start with typed ResourceInfo fields
            ci_info = {
                "name":        ctx.cmdb.resource_info.name,
                "type":        ctx.cmdb.resource_info.type,
                "status":      ctx.cmdb.resource_info.status,
                "environment": ctx.cmdb.environment,
            }
            # Overlay all raw CMDB fields — this is where the 6 scoring fields live
            if ctx.cmdb.cmdb_context:
                for field_name in [
                    "business_criticality", "ci_tier", "user_count",
                    "is_spof", "sla_percent", "failover_available", "compliance_scope",
                ]:
                    val = ctx.cmdb.cmdb_context.get(field_name)
                    if val is not None:
                        ci_info[field_name] = val
                logger.debug(
                    f"[RISK] ci_info after CMDB merge: { {k: ci_info[k] for k in ci_info if k not in ('name','type','status')} }"
                )
            impacted_services = ctx.cmdb.impacted_services or []

        resource_name = ci_info.get("name", "unknown")
        event_type = state.context.get("alert_payload", {}).get("type", "unknown")

        # ── Read v2 per-factor config (falls back to legacy factor_weights) ─────────
        factors_cfg = weights.get("factors", {})
        fw_legacy   = weights.get("factor_weights", {})

        def _fcfg(name: str, default_weight: float) -> dict:
            """Return merged factor config with safe defaults."""
            cfg = factors_cfg.get(name, {})
            return {
                "enabled":      bool(cfg.get("enabled", True)),
                "weight":       float(cfg.get("weight", fw_legacy.get(name, default_weight))),
                "missing_data": cfg.get("missing_data", "neutral"),
                "label":        cfg.get("label", name.replace("_", " ").title()),
                "cmdb_sourced": bool(cfg.get("cmdb_sourced", True)),
            }

        sev_cfg   = _fcfg("severity",             20)
        tier_cfg  = _fcfg("ci_tier",              15)
        env_cfg   = _fcfg("environment",          15)
        crit_cfg  = _fcfg("business_criticality", 20)
        user_cfg  = _fcfg("user_impact",          15)
        blast_cfg = _fcfg("blast_radius",         15)
        fail_cfg  = _fcfg("failover",              5)
        spof_cfg  = _fcfg("spof",                 10)
        sla_cfg   = _fcfg("sla",                  10)
        hist_cfg  = _fcfg("history",              10)

        # ── Detect which CMDB fields are actually present (vs defaulted) ──────────
        cmdb_raw = (ctx.cmdb.cmdb_context or {}) if (ctx and ctx.cmdb and ctx.cmdb.cmdb_context) else {}
        _has = lambda f: f in cmdb_raw and cmdb_raw[f] is not None

        # ── FACTOR 1: Event Severity ──────────────────────────────────────────────
        # Always computed from alert payload — no CMDB dependency.
        severity_weights_map = {
            Severity.CRITICAL: 1.0,
            Severity.HIGH:     0.75,
            Severity.MEDIUM:   0.5,
            Severity.LOW:      0.25,
            Severity.INFO:     0.1,
        }
        if sev_cfg["enabled"]:
            severity_factor   = severity_weights_map.get(initial_severity, 0.5) * sev_cfg["weight"]
            severity_src      = "computed"
            severity_excluded = False
        else:
            severity_factor, severity_src, severity_excluded = 0.0, "disabled", True

        # ── FACTOR 2: CI Tier ─────────────────────────────────────────────────────
        # Flat weight contribution; the tier value feeds the priority matrix separately.
        ci_tier      = int(ci_info.get("ci_tier", 2))
        tier_present = _has("ci_tier")
        if not tier_cfg["enabled"]:
            tier_factor, tier_src, tier_excluded = 0.0, "disabled", True
        elif tier_present:
            tier_factor, tier_src, tier_excluded = tier_cfg["weight"], "cmdb", False
        elif tier_cfg["missing_data"] == "exclude":
            tier_factor, tier_src, tier_excluded = 0.0, "excluded", True
        elif tier_cfg["missing_data"] == "pessimistic":
            tier_factor, tier_src, tier_excluded = tier_cfg["weight"], "pessimistic", False
        else:  # neutral
            tier_factor, tier_src, tier_excluded = tier_cfg["weight"], "default", False

        # ── FACTOR 3: Deployment Environment ─────────────────────────────────────
        environment    = (
            ci_info.get("environment")
            or state.context.get("alert_payload", {}).get("environment")
            or "unknown"
        ).lower()
        # environment comes from ctx.cmdb.environment (typed field), NOT cmdb_context,
        # so _has("environment") will always be False. Check ci_info directly instead.
        env_present    = (
            _has("environment")
            or ci_info.get("environment", "unknown").lower() not in ("unknown", "", "none")
        )
        env_multiplier = weights.get("environment_multiplier", {}).get(environment, 0.75)
        if not env_cfg["enabled"]:
            env_factor, env_src, env_excluded = 0.0, "disabled", True
        elif env_present:
            env_factor, env_src, env_excluded = env_cfg["weight"] * env_multiplier, "cmdb", False
        elif env_cfg["missing_data"] == "exclude":
            env_factor, env_src, env_excluded = 0.0, "excluded", True
        elif env_cfg["missing_data"] == "pessimistic":
            env_factor, env_src, env_excluded = env_cfg["weight"] * 1.0, "pessimistic", False  # production (worst)
        else:  # neutral — "unknown" multiplier (0.75)
            env_factor, env_src, env_excluded = env_cfg["weight"] * 0.75, "default", False

        # ── FACTOR 4: Business Criticality ───────────────────────────────────────
        business_crit   = ci_info.get("business_criticality", "tier_2")
        crit_present    = _has("business_criticality")
        crit_multiplier = weights.get("business_criticality_multiplier", {}).get(business_crit, 1.0)
        crit_label_map  = {
            "tier_1": "Mission Critical",
            "tier_2": "Core Service",
            "tier_3": "Infrastructure",
        }
        crit_label = crit_label_map.get(business_crit, business_crit.replace("_", " ").title())
        if not crit_cfg["enabled"]:
            crit_factor, crit_src, crit_excluded = 0.0, "disabled", True
        elif crit_present:
            crit_factor, crit_src, crit_excluded = crit_cfg["weight"] * crit_multiplier, "cmdb", False
        elif crit_cfg["missing_data"] == "exclude":
            crit_factor, crit_src, crit_excluded = 0.0, "excluded", True
        elif crit_cfg["missing_data"] == "pessimistic":
            tier1_mult = weights.get("business_criticality_multiplier", {}).get("tier_1", 1.5)
            crit_factor, crit_src, crit_excluded = crit_cfg["weight"] * tier1_mult, "pessimistic", False
        else:  # neutral — tier_2 (1.0 multiplier)
            crit_factor, crit_src, crit_excluded = crit_cfg["weight"] * 1.0, "default", False

        # ── FACTOR 5: User Impact ─────────────────────────────────────────────────
        # 1-5 tier scale: avoids the raw-count trap where 100 users ≈ 0 on a 10k scale.
        # Tiers are derived from user_count so no CMDB schema change is needed.
        _USER_TIERS = [
            (25,    1, "Minimal",  0.20),   # 1–25 users
            (250,   2, "Small",    0.40),   # 26–250
            (2_000, 3, "Medium",   0.60),   # 251–2 000
            (10_000,4, "Large",    0.80),   # 2 001–10 000
            (None,  5, "Broad",    1.00),   # 10 000+
        ]
        def _user_tier(count: int) -> tuple[int, str, float]:
            for ceiling, tier, label, frac in _USER_TIERS:
                if ceiling is None or count <= ceiling:
                    return tier, label, frac
            return 5, "Broad", 1.00

        user_count   = ci_info.get("user_count", 0)
        user_present = _has("user_count")
        if not user_cfg["enabled"]:
            user_factor, user_src, user_excluded = 0.0, "disabled", True
            user_tier, user_tier_label = 0, "disabled"
        elif user_present:
            user_tier, user_tier_label, user_frac = _user_tier(int(user_count))
            user_factor, user_src, user_excluded = user_cfg["weight"] * user_frac, "cmdb", False
        elif user_cfg["missing_data"] == "exclude":
            user_factor, user_src, user_excluded = 0.0, "excluded", True
            user_tier, user_tier_label = 0, "excluded"
        elif user_cfg["missing_data"] == "pessimistic":
            user_tier, user_tier_label = 5, "Broad"
            user_factor, user_src, user_excluded = user_cfg["weight"], "pessimistic", False
        else:  # neutral — assume Tier 3 (Medium)
            user_tier, user_tier_label = 3, "Medium"
            user_factor, user_src, user_excluded = user_cfg["weight"] * 0.60, "default", False

        # ── FACTOR 6: Blast Radius ────────────────────────────────────────────────
        # Computed from the service dependency graph — not a raw CMDB field.
        dependent_count = len(impacted_services)
        critical_deps   = sum(1 for svc in impacted_services if svc.get("criticality") == "critical")
        blast_raw       = (dependent_count * 1.0) + (critical_deps * 3.0)
        if blast_cfg["enabled"]:
            blast_factor, blast_src, blast_excluded = min(blast_cfg["weight"], blast_raw), "computed", False
        else:
            blast_factor, blast_src, blast_excluded = 0.0, "disabled", True

        # ── FACTOR 7: Failover Availability (additive risk factor) ──────────────
        # No failover = full weight added to score (exposed).
        # Failover available = 0 contribution (protected).
        # Pessimistic: unknown → assume no failover → full weight.
        # Neutral: assume protected → 0 contribution.
        fail_present = _has("failover_available")
        has_failover = ci_info.get("failover_available", False)
        if not fail_cfg["enabled"]:
            failover_factor, fail_src, fail_excluded = 0.0, "disabled", True
        elif fail_present:
            failover_factor, fail_src, fail_excluded = (0.0 if has_failover else fail_cfg["weight"]), "cmdb", False
        elif fail_cfg["missing_data"] == "exclude":
            failover_factor, fail_src, fail_excluded = 0.0, "excluded", True
        elif fail_cfg["missing_data"] == "pessimistic":
            failover_factor, fail_src, fail_excluded = fail_cfg["weight"], "pessimistic", False  # assume no failover
        else:  # neutral — assume protected
            failover_factor, fail_src, fail_excluded = 0.0, "default", False

        # ── FACTOR 8: Single Point of Failure ────────────────────────────────────
        # Pessimistic: unknown SPOF → assume it IS a SPOF → full weight added.
        spof_present = _has("is_spof")
        is_spof      = ci_info.get("is_spof", False)
        if not spof_cfg["enabled"]:
            spof_factor, spof_src, spof_excluded = 0.0, "disabled", True
        elif spof_present:
            spof_factor, spof_src, spof_excluded = (spof_cfg["weight"] if is_spof else 0.0), "cmdb", False
        elif spof_cfg["missing_data"] == "exclude":
            spof_factor, spof_src, spof_excluded = 0.0, "excluded", True
        elif spof_cfg["missing_data"] == "pessimistic":
            spof_factor, spof_src, spof_excluded = spof_cfg["weight"], "pessimistic", False  # assume SPOF
        else:  # neutral
            spof_factor, spof_src, spof_excluded = 0.0, "default", False

        # ── FACTOR 9: SLA Compliance ──────────────────────────────────────────────
        # Lower SLA% = higher risk.  90% SLA → full weight; 99.9% → near zero.
        sla_present = _has("sla_percent")
        sla_percent = float(ci_info.get("sla_percent", 95.0))
        if not sla_cfg["enabled"]:
            sla_factor, sla_src, sla_excluded = 0.0, "disabled", True
        elif sla_present:
            sla_factor, sla_src, sla_excluded = (100.0 - sla_percent) * (sla_cfg["weight"] / 100.0), "cmdb", False
        elif sla_cfg["missing_data"] == "exclude":
            sla_factor, sla_src, sla_excluded = 0.0, "excluded", True
        elif sla_cfg["missing_data"] == "pessimistic":
            sla_factor, sla_src, sla_excluded = sla_cfg["weight"], "pessimistic", False  # assume 0% SLA
        else:  # neutral — assume 95% SLA
            sla_factor, sla_src, sla_excluded = (100.0 - 95.0) * (sla_cfg["weight"] / 100.0), "default", False

        # ── FACTOR 10: Incident History ───────────────────────────────────────────
        # Computed from the incident repository — no CMDB dependency.
        history_count = len(historical_incidents)
        if not hist_cfg["enabled"]:
            history_factor, hist_src, hist_excluded = 0.0, "disabled", True
        elif history_count > 0:
            history_factor, hist_src, hist_excluded = min(hist_cfg["weight"], history_count * 2.0), "computed", False
        elif hist_cfg["missing_data"] == "exclude":
            history_factor, hist_src, hist_excluded = 0.0, "excluded", True
        else:  # neutral / pessimistic: no history = no recurrence penalty
            history_factor, hist_src, hist_excluded = 0.0, "default", False

        # ── Score normalisation ───────────────────────────────────────────────────
        # Sum base weights of all enabled, non-excluded factors.
        positive_entries = [
            (sev_cfg,  severity_excluded),
            (tier_cfg, tier_excluded),
            (env_cfg,  env_excluded),
            (crit_cfg, crit_excluded),
            (user_cfg, user_excluded),
            (blast_cfg, blast_excluded),
            (fail_cfg, fail_excluded),
            (spof_cfg, spof_excluded),
            (sla_cfg,  sla_excluded),
            (hist_cfg, hist_excluded),
        ]
        active_weight_sum = sum(
            cfg["weight"] for cfg, excl in positive_entries
            if cfg["enabled"] and not excl
        )
        # Scale so max achievable positive score = 100 regardless of which factors are active
        scale = 100.0 / active_weight_sum if active_weight_sum > 0 else 1.0

        severity_factor  *= scale
        tier_factor      *= scale
        env_factor       *= scale
        crit_factor      *= scale
        user_factor      *= scale
        blast_factor     *= scale
        failover_factor  *= scale
        spof_factor      *= scale
        sla_factor       *= scale
        history_factor   *= scale

        # ── Total score ───────────────────────────────────────────────────────────
        total_score = (
            severity_factor + tier_factor   + env_factor  + crit_factor +
            user_factor     + blast_factor  + failover_factor +
            spof_factor     + sla_factor    + history_factor
        )
        total_score = min(100.0, max(0.0, total_score))

        # ── Confidence — fraction of CMDB fields actually present ─────────────────
        # env_present already accounts for both cmdb_context and the typed context field.
        cmdb_fields = [
            "ci_tier", "environment", "business_criticality",
            "user_count", "failover_available", "is_spof", "sla_percent",
        ]
        known_fields = sum([
            1 if _has("ci_tier")                else 0,
            1 if env_present                    else 0,
            1 if _has("business_criticality")   else 0,
            1 if _has("user_count")             else 0,
            1 if _has("failover_available")     else 0,
            1 if _has("is_spof")                else 0,
            1 if _has("sla_percent")            else 0,
        ])
        confidence_score = (known_fields / len(cmdb_fields)) * 100.0

        # ── Severity re-assessment from total score ───────────────────────────────
        severity_threshold_map = {
            Severity.CRITICAL: 80.0,
            Severity.HIGH:     60.0,
            Severity.MEDIUM:   40.0,
            Severity.LOW:      20.0,
            Severity.INFO:      0.0,
        }
        assessed_severity = Severity.MEDIUM
        for sev, threshold in sorted(severity_threshold_map.items(), key=lambda x: x[1], reverse=True):
            if total_score >= threshold:
                assessed_severity = sev
                break

        state.severity = assessed_severity

        # ── Priority via matrix ───────────────────────────────────────────────────
        priority_matrix = weights.get("priority_matrix", {})
        priority_key    = f"{assessed_severity.value}:{business_crit}"
        priority        = priority_matrix.get(priority_key, "P3")
        state           = self._set_context(state, "incident_priority", priority)

        # ── Rich factor breakdown (consumed by RiskSummaryPage) ───────────────────
        risk_breakdown = {
            "total_score":      total_score,
            "confidence_score": confidence_score,
            "factors": {
                "severity": {
                    "value":       severity_factor,
                    "weight":      sev_cfg["weight"],
                    "max_pts":     round(sev_cfg["weight"] * scale, 1),
                    "label":       sev_cfg["label"],
                    "data_source": severity_src,
                    "detail":      initial_severity.value.upper(),
                },
                "ci_tier": {
                    "value":       tier_factor,
                    "weight":      tier_cfg["weight"],
                    "max_pts":     round(tier_cfg["weight"] * scale, 1),
                    "label":       tier_cfg["label"],
                    "data_source": tier_src,
                    "detail":      f"Tier {ci_tier}",
                },
                "environment": {
                    "value":       env_factor,
                    "weight":      env_cfg["weight"],
                    "max_pts":     round(env_cfg["weight"] * scale, 1),
                    "label":       env_cfg["label"],
                    "data_source": env_src,
                    "multiplier":  env_multiplier,
                    "detail":      environment.upper(),
                },
                "business_criticality": {
                    "value":       crit_factor,
                    "weight":      crit_cfg["weight"],
                    "max_pts":     round(crit_cfg["weight"] * scale, 1),
                    "label":       crit_cfg["label"],
                    "data_source": crit_src,
                    "multiplier":  crit_multiplier,
                    "detail":      crit_label,
                },
                "user_impact": {
                    "value":       user_factor,
                    "weight":      user_cfg["weight"],
                    "max_pts":     round(user_cfg["weight"] * scale, 1),
                    "label":       user_cfg["label"],
                    "data_source": user_src,
                    "users":       user_count,
                    "tier":        user_tier,
                    "tier_label":  user_tier_label,
                    "detail":      f"Tier {user_tier} / 5 — {user_tier_label}" if user_tier > 0 else user_tier_label,
                },
                "blast_radius": {
                    "value":       blast_factor,
                    "weight":      blast_cfg["weight"],
                    "max_pts":     round(blast_cfg["weight"] * scale, 1),
                    "label":       blast_cfg["label"],
                    "data_source": blast_src,
                    "dependents":  dependent_count,
                    "critical":    critical_deps,
                },
                "failover": {
                    "value":       failover_factor,
                    "weight":      fail_cfg["weight"],
                    "max_pts":     round(fail_cfg["weight"] * scale, 1),
                    "label":       fail_cfg["label"],
                    "data_source": fail_src,
                    "available":   has_failover,
                    "detail":      ("Available — protected" if has_failover else "Not available — exposed"),
                },
                "spof": {
                    "value":       spof_factor,
                    "weight":      spof_cfg["weight"],
                    "max_pts":     round(spof_cfg["weight"] * scale, 1),
                    "label":       spof_cfg["label"],
                    "data_source": spof_src,
                    "is_spof":     is_spof,
                    "detail":      ("Single Point of Failure" if is_spof else "Redundant"),
                },
                "sla": {
                    "value":       sla_factor,
                    "weight":      sla_cfg["weight"],
                    "max_pts":     round(sla_cfg["weight"] * scale, 1),
                    "label":       sla_cfg["label"],
                    "data_source": sla_src,
                    "sla_percent": sla_percent,
                },
                "history": {
                    "value":       history_factor,
                    "weight":      hist_cfg["weight"],
                    "max_pts":     round(hist_cfg["weight"] * scale, 1),
                    "label":       hist_cfg["label"],
                    "data_source": hist_src,
                    "incidents":   history_count,
                },
            },
            "initial_severity":  initial_severity.value,
            "assessed_severity": assessed_severity.value,
            "priority":          priority,
            "normalisation": {
                "scale":             round(scale, 4),
                "active_weight_sum": active_weight_sum,
            },
            "ci_info": {
                "ci_tier":              ci_tier,
                "environment":          environment,
                "business_criticality": business_crit,
                "user_count":           user_count,
                "failover_available":   has_failover,
                "is_spof":              is_spof,
                "sla_percent":          sla_percent,
            },
        }
        state = self._set_context(state, "risk_breakdown", risk_breakdown)
        state.risk_score = total_score

        # Phase 10: Create typed RiskContext and add to workflow context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # Calculate blast radius from impacted services
        blast_radius = len(impacted_services) if impacted_services else 1
        if impacted_services:
            critical_count = sum(1 for svc in impacted_services if svc.get("criticality") == "critical")
            if critical_count > 0:
                blast_radius = min(4, 2 + (critical_count // 2))  # 1-4 scale

        # Create RiskBreakdown (typed)
        risk_breakdown_obj = RiskBreakdown(
            severity_score=severity_factor,
            resource_criticality_score=crit_factor,
            dependency_impact_score=float(blast_factor),
            business_impact_score=float(total_score),
        )

        # Create RiskContext (typed) and add to workflow context
        ctx.risk = RiskContext(
            risk_score=total_score,
            risk_breakdown=risk_breakdown_obj,
            blast_radius=blast_radius,
            remediation_complexity="simple" if total_score < 30 else ("moderate" if total_score < 60 else "complex"),
        )

        # Persist typed context
        state = self._set_typed_context(state, ctx)

        # Generate detailed reasoning
        def _src_tag(src: str) -> str:
            return {"cmdb": "[CMDB]", "computed": "[calc]", "pessimistic": "[worst]",
                    "default": "[dflt]", "excluded": "[skip]", "disabled": "[off]"}.get(src, f"[{src}]")

        reasoning = (
            f"[RISK ASSESSOR AGENT] Full weighted risk assessment (v2 schema)\n"
            f"  Resource: {resource_name} (Tier {ci_tier} — {crit_label})\n"
            f"  Scale factor: {scale:.4f} (active weight sum: {active_weight_sum:.0f})\n"
            f"\n  FACTOR SCORES (normalised to 0-100):\n"
            f"    1. {sev_cfg['label']:<28} {severity_factor:>5.1f}  {_src_tag(severity_src)} {initial_severity.value.upper()}\n"
            f"    2. {tier_cfg['label']:<28} {tier_factor:>5.1f}  {_src_tag(tier_src)} Tier {ci_tier}\n"
            f"    3. {env_cfg['label']:<28} {env_factor:>5.1f}  {_src_tag(env_src)} {environment.upper()} × {env_multiplier:.2f}\n"
            f"    4. {crit_cfg['label']:<28} {crit_factor:>5.1f}  {_src_tag(crit_src)} {crit_label} × {crit_multiplier:.2f}\n"
            f"    5. {user_cfg['label']:<28} {user_factor:>5.1f}  {_src_tag(user_src)} Tier {user_tier}/5 ({user_tier_label}) — {user_count:,.0f} users\n"
            f"    6. {blast_cfg['label']:<28} {blast_factor:>5.1f}  {_src_tag(blast_src)} {dependent_count} deps, {critical_deps} critical\n"
            f"    7. {fail_cfg['label']:<28} {failover_factor:>5.1f}  {_src_tag(fail_src)} {'available — protected' if has_failover else 'not available — exposed'}\n"
            f"    8. {spof_cfg['label']:<28} {spof_factor:>5.1f}  {_src_tag(spof_src)} {'SPOF' if is_spof else 'redundant'}\n"
            f"    9. {sla_cfg['label']:<28} {sla_factor:>5.1f}  {_src_tag(sla_src)} {sla_percent:.0f}% SLA\n"
            f"   10. {hist_cfg['label']:<28} {history_factor:>5.1f}  {_src_tag(hist_src)} {history_count} prev incidents\n"
            f"\n  ASSESSMENT:\n"
            f"    Total Risk Score:   {total_score:.1f}/100\n"
            f"    Initial Severity:   {initial_severity.value.upper()}\n"
            f"    Assessed Severity:  {assessed_severity.value.upper()}\n"
            f"    Priority:           {priority}\n"
            f"    Confidence:         {confidence_score:.0f}%\n"
            f"    Known CMDB Fields:  {known_fields}/{len(cmdb_fields)}"
        )
        state = self._add_trace(state, reasoning)
        return state


class MechanicAgent(Agent):
    """Selects remediation from CMDB playbooks.

    Phase 10: Refactored to use typed Proposal context.
    Creates proposal with resolved main_args (anomaly_process substitution).
    """

    def __init__(self):
        super().__init__("mechanic")
        self.cmdb = get_cmdb()

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Select best remediation — runbooks take priority over CMDB playbooks"""
        # Get or create typed context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # logger.info(
        #     f"[MECHANIC] Retrieved context: ctx.cmdb={ctx.cmdb is not None}, "
        #     f"cmdb_platform={ctx.cmdb.platform if ctx.cmdb else 'N/A'}"
        # )

        # ISSUE A FIX: Validate that required context layers exist
        if not self._validate_context_layer(state, "sentinel", ["alert_payload"]):
            return self._handle_missing_context(
                state, "sentinel",
                "Cannot select remediation without alert information"
            )

        if not self._validate_context_layer(state, "cmdb", ["resource_name"]):
            return self._handle_missing_context(
                state, "cmdb",
                "Cannot select remediation without resource information"
            )

        if not self._validate_context_layer(state, "risk", ["risk_score"]):
            return self._handle_missing_context(
                state, "risk",
                "Cannot select remediation without risk assessment"
            )

        alert = state.context.get("alert_payload", {})
        cmdb = state.context.get("cmdb_context", {})
        resource_info = cmdb.get("resource_info", {})
        alert_type = alert.get("type", "unknown")
        resource_name = alert.get("resource_name", "unknown")
        resource_type = resource_info.get("type", "service")

        # Resolve platform from typed context (set by LibrarianAgent)
        platform = ctx.get_platform()  # docker | linux | windows | kubernetes | any
        # logger.info(
        #     f"[MECHANIC] Platform resolution: ctx.cmdb={ctx.cmdb}, "
        #     f"ctx.cmdb.platform={ctx.cmdb.platform if ctx.cmdb else 'N/A'}, "
        #     f"resolved_platform={platform}, resource_type={resource_type}"
        # )

        # Get anomaly process for process_kill actions
        anomaly_process = ctx.get_anomaly_process() or alert.get("anomaly_process")

        # ── Tier 1: Runbooks DB (highest confidence — ops-authored) ──
        runbook = _lookup_runbook(alert_type, resource_name, platform)
        if runbook:
            # Parse runbook steps into typed structures
            diagnostics_steps = []
            for step in (runbook.diagnostics or []):
                diagnostics_steps.append(RunbookStep(
                    order=step.get("order", 0),
                    type="diagnostic",
                    name=step.get("name", ""),
                    description=step.get("description", ""),
                    tool=step.get("tool", ""),
                    args_json=dict(step.get("args_json", {})) if hasattr(step, "args_json") else dict(step.get("args", {})),
                ))

            remediation_steps = []
            main_args = {}
            for step in (runbook.actions or []):
                args_json = dict(step.get("args_json", {})) if hasattr(step, "args_json") else dict(step.get("args", {}))
                # CRITICAL: Substitute anomaly_process for process_kill actions
                if anomaly_process and "process_kill" in step.get("tool", ""):
                    args_json["process_name"] = anomaly_process
                    if not main_args:  # Set main_args from first action
                        main_args = args_json.copy()
                remediation_steps.append(RunbookStep(
                    order=step.get("order", 0),
                    type="remediation",
                    name=step.get("name", ""),
                    description=step.get("description", ""),
                    tool=step.get("tool", ""),
                    args_json=args_json,
                ))

            verification_steps = []
            for step in (runbook.verification_steps or []):
                # For verification steps, preserve metric/check/value
                # These are metric-based checks, not tool executions
                verification_steps.append(RunbookStep(
                    order=step.get("order", 0),
                    type="verification",
                    name=step.get("name", ""),
                    description=step.get("description", ""),
                    tool=step.get("tool", ""),
                    args_json=dict(step.get("args_json", {})) if hasattr(step, "args_json") else dict(step.get("args", {})),
                    metric=step.get("metric"),
                    check=step.get("check"),
                    value=step.get("value"),
                ))

            # Create typed Proposal — Tier 1: ops-authored runbook from library
            ctx.proposal = Proposal(
                runbook_id=str(runbook.id),
                runbook_name=runbook.name,
                diagnostics_steps=diagnostics_steps,
                remediation_steps=remediation_steps,
                verification_steps=verification_steps,
                confidence=float(runbook.confidence or 0.9),
                blast_radius=int(runbook.blast_radius or 1),
                approval_required=True,
                main_args=main_args or {"process_name": anomaly_process} if anomaly_process else {},
                source="runbook_library",
                target=resource_name,
            )

            # Persist typed context (syncs to untyped dict for backward compatibility)
            state = self._set_typed_context(state, ctx)

            # Set runbook_steps separately for tool_registry agent (multi-step execution)
            # NOTE: Don't overwrite "proposal" - let _set_typed_context handle that via to_dict()
            state = self._set_context(state, "runbook_steps", {
                "diagnostics": runbook.diagnostics or [],
                "actions": runbook.actions or [],
                "verification": runbook.verification_steps or [],
            })
            # Preserve the full visual-editor graph so ToolRegistryAgent can use
            # the same graph-aware walk as the editor's Test button, honouring
            # DECISION nodes and their branch conditions at execution time.
            if runbook.source_steps:
                state = self._set_context(state, "runbook_graph", runbook.source_steps)

            reasoning = (
                f"[MECHANIC AGENT] Remediation strategy selected\n"
                f"  Resource Type: {resource_type}\n"
                f"  Incident Type: {alert_type}\n"
                f"  Platform: {platform}\n"
                f"  RUNBOOK MATCHED: {runbook.name} (platform={getattr(runbook, 'platform', 'any')})\n"
                f"  Confidence: {float(runbook.confidence):.0%} (ops-authored, highest priority)\n"
                f"  Blast Radius: {runbook.blast_radius}\n"
                f"  Diagnostics: {len(diagnostics_steps)} step(s)\n"
                f"  Actions: {len(remediation_steps)} step(s)"
            )
            if anomaly_process:
                reasoning += f"\n  Process to Kill: {anomaly_process}"

            state = self._add_trace(state, reasoning)
            return state

        # ── Tier 2: CMDB Playbooks ──
        playbooks = self.cmdb.get_playbooks(resource_type, alert_type)
        selected_playbook = None
        if playbooks:
            selected_playbook = max(playbooks, key=lambda p: p.get("success_rate", 0))

        # For CMDB playbooks, create generic Proposal — Tier 2: CMDB-derived playbook
        if selected_playbook:
            ctx.proposal = Proposal(
                runbook_id=selected_playbook.get("id", "pb-unknown"),
                runbook_name=selected_playbook.get("name", "Unknown Playbook"),
                diagnostics_steps=[],
                remediation_steps=[],
                confidence=float(selected_playbook.get("success_rate", 0.5)),
                blast_radius=int(selected_playbook.get("blast_radius", 1)),
                approval_required=True,
                main_args={},
                source="cmdb_playbook",
            )
        else:
            # ── Tier 3: Fallback escalation proposal ──
            # No runbook or playbook found — create a minimal escalation proposal
            # so PolicyBroker and ToolRegistry can still run and log the incident.
            resource_name = cmdb.get("resource_name", "unknown")
            ctx.proposal = Proposal(
                runbook_id="fallback-escalate",
                runbook_name=f"Escalate: {alert_type} on {resource_name}",
                diagnostics_steps=[],
                remediation_steps=[],
                confidence=0.3,
                blast_radius=1,
                approval_required=True,  # Unknown incident type → conservative: require approval
                main_args={"action": "escalate", "target": resource_name},
                source="fallback_escalation",
            )

        # Persist typed context
        state = self._set_typed_context(state, ctx)

        # Also set untyped context
        state = self._set_context(state, "selected_playbook", selected_playbook)

        reasoning = f"[MECHANIC AGENT] Remediation strategy selected\n"
        reasoning += f"  Resource Type: {resource_type}\n"
        reasoning += f"  Incident Type: {alert_type}\n"

        if playbooks:
            reasoning += f"  Available Playbooks: {len(playbooks)}\n"
            for pb in playbooks:
                success_rate = pb.get("success_rate", 0)
                rating = "⭐⭐⭐⭐⭐" if success_rate >= 0.9 else "⭐⭐⭐⭐" if success_rate >= 0.80 else "⭐⭐⭐"
                reasoning += f"    - {pb.get('name')}: {success_rate:.0%} success rate {rating}\n"

        if selected_playbook:
            reasoning += (
                f"  SELECTED: {selected_playbook.get('name')}\n"
                f"  Success Rate: {selected_playbook.get('success_rate', 0):.1%}\n"
                f"  Estimated Duration: {selected_playbook.get('estimated_time_min', '?')} minutes"
            )
        else:
            reasoning += (
                f"  No specific runbook or playbook found.\n"
                f"  Created fallback escalation proposal — PolicyBroker will evaluate and may require manual review."
            )

        state = self._add_trace(state, reasoning)
        return state

    @staticmethod
    def _generate_runbook_proposal(runbook, cmdb: Dict, alert: Dict = None) -> Dict:
        """Build a proposal dict from a matched runbook.

        If the alert contains an 'anomaly_process' field (set by the watcher when
        it detects the actual offending process), that overrides any hardcoded
        process_name in the runbook action args — so the kill always targets the
        real culprit rather than whatever was baked into the runbook.
        """
        resource_name = cmdb.get("resource_name", "unknown")
        alert = alert or {}

        # Derive the action name from the first action's tool (dots → underscores)
        main_action = "escalate"
        main_args: Dict = {}
        if runbook.actions:
            first = runbook.actions[0]
            raw_tool = first.get("tool", "escalate")
            main_action = raw_tool.replace(".", "_")
            # Runbook actions store args in "args_json" field
            main_args = dict(first.get("args_json", {}))  # copy so we can mutate safely

        # If the alert carries the actual detected process, prefer it over the
        # hardcoded runbook arg — avoids killing the wrong process.
        detected_process = alert.get("anomaly_process")
        if detected_process and main_action == "process_kill":
            main_args["process_name"] = detected_process
            logger.info(
                f"[MECHANIC] Runbook process_name overridden by alert anomaly_process: '{detected_process}'"
            )

        return {
            "action": main_action,
            "target": resource_name,
            "rationale": f"Runbook: {runbook.name}",
            "runbook_id": str(runbook.id),
            "runbook_name": runbook.name,
            "runbook_steps": {
                "diagnostics": runbook.diagnostics or [],
                "actions": runbook.actions or [],
                "verification": runbook.verification_steps or [],
            },
            **main_args,
        }

    @staticmethod
    def _generate_proposal(alert_type: str, cmdb: Dict, playbook: Dict = None) -> Dict:
        """Generate remediation proposal based on playbook or defaults"""
        resource_name = cmdb.get("resource_name", "unknown")

        # Map playbook IDs to executable actions
        playbook_id_to_action = {
            "pb-scale": "scale_up",
            "pb-restart": "restart_service",
            "pb-optimize": "cleanup_logs",
        }

        # If playbook found, use it
        if playbook:
            playbook_id = playbook.get("id", "escalate")
            action = playbook_id_to_action.get(playbook_id, "escalate")
            return {
                "action": action,
                "playbook_name": playbook.get("name", "Unknown"),
                "target": resource_name,
                "steps": playbook.get("steps", []),
                "estimated_time_min": playbook.get("estimated_time_min", 30),
                "rationale": f"Using playbook: {playbook.get('name')}",
            }

        # Fallback to default proposals
        proposals = {
            "high_cpu": {
                "action": "scale_up",
                "target": resource_name,
                "replicas": 3,
                "rationale": "High CPU usage detected, scaling up to handle load",
            },
            "disk_full": {
                "action": "cleanup_logs",
                "target": resource_name,
                "days_to_retain": 7,
                "rationale": "Disk space critical, archiving old logs",
            },
            "service_down": {
                "action": "restart_service",
                "target": cmdb.get("resource_name", "unknown"),
                "restart_mode": "graceful",
                "rationale": "Service unresponsive, attempting graceful restart",
            },
        }
        return proposals.get(
            alert_type,
            {
                "action": "escalate",
                "target": cmdb.get("resource_name", "unknown"),
                "rationale": f"Unknown alert type {alert_type}, requires manual review",
            },
        )


class PolicyBrokerAgent(Agent):
    """Checks if remediation is approved per governance policies.

    Phase 10: Refactored to use typed GovernanceContext.
    Uses ctx.get_environment() for guaranteed policy matching.
    Uses ctx.risk.blast_radius for constraint checks.
    """

    def __init__(self):
        super().__init__("policy_broker")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Check governance policies and determine approval requirement"""
        from agentic_os.db.database import SessionLocal
        from agentic_os.services.governance_broker import GovernanceBroker

        # Get typed context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # ISSUE A FIX: Validate that all previous context layers exist
        if not self._validate_context_layer(state, "sentinel"):
            return self._handle_missing_context(state, "sentinel")

        if not self._validate_context_layer(state, "cmdb"):
            return self._handle_missing_context(state, "cmdb")

        if not self._validate_context_layer(state, "risk"):
            return self._handle_missing_context(state, "risk")

        if not self._validate_context_layer(state, "proposal"):
            return self._handle_missing_context(
                state, "proposal",
                "Cannot apply governance without remediation proposal"
            )

        # Get values using safe accessors (CRITICAL FIX: use get_environment())
        environment = ctx.get_environment()  # Guaranteed to work, defaults to "dev"
        risk_score = ctx.get_risk_score() or state.risk_score or 0.0
        blast_radius = ctx.get_blast_radius() or 1

        # Get other required values
        proposal = state.context.get("proposal", {}) or (ctx.proposal.__dict__ if ctx.proposal else {})
        action = proposal.get("action", "unknown")
        severity = state.severity
        alert = state.context.get("alert_payload", {}) or (ctx.sentinel.alert_payload.__dict__ if ctx.sentinel else {})
        resource_name = alert.get("resource_name", "unknown")
        service_name = resource_name

        reasoning = f"[POLICY BROKER AGENT] Governance policy evaluation\n"
        reasoning += f"  Proposed Action: {action.upper()}\n"
        reasoning += f"  Target Resource: {resource_name}\n"
        reasoning += f"  Incident Severity: {severity.value.upper() if severity else 'UNKNOWN'}\n"
        reasoning += f"  Risk Score: {risk_score:.1f}\n"
        reasoning += f"  Blast Radius: {blast_radius}\n"
        reasoning += f"  Environment: {environment}\n"
        reasoning += f"  \n"

        # Initialize governance broker and check policies
        try:
            db = SessionLocal()
            from agentic_os.db.repositories import PolicyRepository

            severity_str = severity.value if severity else "medium"
            severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}

            # ── PRIMARY: Check user-facing policies table (created via Policy Editor) ──
            policy_repo = PolicyRepository(db)
            all_policies = policy_repo.list_all(enabled_only=True, published_only=True)

            # Match policies against current incident
            matched_user_policies = []
            for p in all_policies:
                rules = p.rules or {}
                # Check min_severity
                min_sev = rules.get("min_severity")
                if min_sev:
                    if severity_order.get(severity_str, 0) < severity_order.get(min_sev, 0):
                        continue  # severity too low → skip
                # Check min_risk_score
                min_risk = rules.get("min_risk_score")
                if min_risk is not None and risk_score < min_risk:
                    continue  # risk score too low → skip
                # Check environment
                env_rule = rules.get("environment")
                if env_rule and env_rule != environment:
                    continue
                # Check anomaly_type (if rule specifies) — normalised on both sides so a
                # policy built from the taxonomy dropdown (canonical codes) still matches
                # incidents whose alert_payload carries a legacy/aliased type string, and
                # so domain wildcards (e.g. "infrastructure.*") work the same way runbook
                # event_type matching already does.
                type_rule = rules.get("anomaly_type")
                if type_rule:
                    from agentic_os.connectors.event_type_utils import normalize_event_type, event_type_matches
                    alert_type_val = normalize_event_type(alert.get("type", ""))
                    types = type_rule if isinstance(type_rule, list) else [type_rule]
                    if not any(event_type_matches(normalize_event_type(t), alert_type_val) for t in types):
                        continue
                # Check service
                svc_rule = rules.get("service")
                if svc_rule and svc_rule != service_name:
                    continue
                matched_user_policies.append(p)

            # Sort by approval_priority (lower number = higher priority)
            matched_user_policies.sort(key=lambda p: (p.approval_priority or 50))

            # ── SECONDARY: Check legacy governance_policies table ──
            broker = GovernanceBroker(db)
            gov_matching_policy = broker.evaluate_policies(
                workflow_id=state.workflow_id,
                proposed_action=action,
                blast_radius=blast_radius,
                risk_score=risk_score,
                severity=severity_str,
                environment=environment,
                service_name=service_name
            )

            # Determine approval requirement
            # Priority: user-facing policies → legacy governance policies → conservative default
            approval_required = True
            allowed_actions = []
            matching_policies_list = []
            blast_radius_limit = None
            approval_priority = 50
            requires_post_monitoring = False

            if matched_user_policies:
                # Use best-matching user-facing policy (highest priority = lowest number)
                best_policy = matched_user_policies[0]
                approval_required = bool(best_policy.requires_manual_approval)
                approval_priority = best_policy.approval_priority or 50

                # Confidence gate — bypass manual approval if runbook has proven reliable
                if approval_required and best_policy.confidence_gate_threshold is not None:
                    gate_threshold = best_policy.confidence_gate_threshold
                    gate_min_runs  = best_policy.confidence_gate_min_runs or 0
                    alert_type_val = alert.get("type", "unknown")
                    if best_policy.confidence_gate_runbook_id:
                        # Policy pins the gate to one specific, named runbook
                        # instead of whichever the lookup cascade resolves.
                        gate_runbook = _lookup_runbook_by_id(best_policy.confidence_gate_runbook_id)
                    else:
                        gate_runbook = _lookup_runbook(alert_type_val, resource_name)
                    if gate_runbook is not None:
                        rb_confidence = gate_runbook.confidence or 0.0
                        rb_successes  = gate_runbook.successful_executions or 0
                        if rb_confidence >= gate_threshold and rb_successes >= gate_min_runs:
                            approval_required = False
                            reasoning += (
                                f"  ✅ CONFIDENCE GATE PASSED: runbook confidence={rb_confidence:.0%} "
                                f"(≥{gate_threshold:.0%}), successful_runs={rb_successes} "
                                f"(≥{gate_min_runs}) — manual approval bypassed\n"
                            )
                        else:
                            reasoning += (
                                f"  🔒 CONFIDENCE GATE NOT MET: confidence={rb_confidence:.0%} "
                                f"(need {gate_threshold:.0%}), successful_runs={rb_successes} "
                                f"(need {gate_min_runs})\n"
                            )

                # approved_actions is the list of allowed actions ("*" = all)
                approved = best_policy.approved_actions or []
                allowed_actions = approved

                # Check constraints
                constraints = best_policy.constraints or {}
                if constraints.get("max_blast_radius"):
                    blast_radius_limit = constraints["max_blast_radius"]
                requires_post_monitoring = bool(constraints.get("requires_post_monitoring", False))

                for p in matched_user_policies:
                    matching_policies_list.append({
                        "policy_id": str(p.policy_id),
                        "name": p.name,
                        "rules": p.rules,
                        "approved_actions": p.approved_actions,
                        "requires_manual_approval": p.requires_manual_approval,
                        "approval_priority": p.approval_priority,
                    })

            elif gov_matching_policy:
                # Fallback: legacy governance policy
                allowed_actions = getattr(gov_matching_policy, 'actions_requiring_approval', [])
                approval_required = True  # legacy governance policies always require approval
                matching_policies_list = [{
                    "policy_id": str(getattr(gov_matching_policy, 'policy_id', '')),
                    "name": getattr(gov_matching_policy, 'name', ''),
                    "approved_actions": [],
                    "requires_manual_approval": True,
                    "approval_priority": 50,
                }]
                if hasattr(gov_matching_policy, 'blast_radius_limit') and gov_matching_policy.blast_radius_limit:
                    blast_radius_limit = gov_matching_policy.blast_radius_limit

            ctx.governance = GovernanceContext(
                matching_policies=matching_policies_list,
                approval_required=approval_required,
                approval_priority=approval_priority,
                allowed_actions=allowed_actions,
                blast_radius_limit=blast_radius_limit,
                requires_post_monitoring=requires_post_monitoring,
                decision_notes=f"Evaluated against environment={environment}, risk={risk_score:.1f}, blast_radius={blast_radius}"
            )

            # Persist typed context
            state = self._set_typed_context(state, ctx)

            if matched_user_policies or gov_matching_policy:
                best = matched_user_policies[0] if matched_user_policies else gov_matching_policy
                policy_name = getattr(best, 'name', 'Unknown')
                reasoning += f"  📋 POLICY MATCHED: {policy_name}\n"
                reasoning += f"  Matched {len(matched_user_policies)} user policy(ies)\n"
                if matched_user_policies:
                    reasoning += f"  Approved Actions: {', '.join(allowed_actions)}\n"
                    reasoning += f"  Requires Manual Approval: {approval_required}\n"
                reasoning += f"\n"

            # ── External-source gate ─────────────────────────────────────────
            # If this incident came from an external connector (Datadog, Splunk,
            # Dynatrace, etc.) AND that connector does not have allow_auto_remediation
            # enabled: DO NOT create an approval gate.  There is nothing to approve —
            # the platform will not execute anything.  Instead:
            #   1. Document the enrichment and recommendation in three work notes.
            #   2. Set lifecycle → in_progress (operator is working it manually).
            #   3. Set decision_result → external_source_documented.
            # If allow_auto_remediation IS enabled the incident falls through to the
            # normal auto-approve / policy-gate path below.
            source_connector = alert.get("source_connector")
            _external_documented = False    # set True when we write notes and skip the gate
            _connector_allows_auto = False  # set True when connector has allow_auto_remediation=True

            if source_connector:
                try:
                    from agentic_os.db.models import ConnectorConfigModel, IncidentNoteModel
                    ext_cfg = db.query(ConnectorConfigModel).filter_by(id=source_connector).first()
                    allows_auto = bool((ext_cfg.config_json or {}).get("allow_auto_remediation", False)) if ext_cfg else False
                    _connector_allows_auto = allows_auto

                    if not allows_auto:
                        # ── Write the three enrichment work notes ─────────────
                        _external_documented = True
                        state.context["external_source_connector"] = source_connector

                        wf_uuid = state.workflow_id if hasattr(state.workflow_id, "bytes") else __import__("uuid").UUID(str(state.workflow_id))

                        # ── Note 1: Sentinel Analysis ─────────────────────────
                        sent_ctx   = ctx.sentinel
                        alert_pay  = ctx.sentinel.alert_payload if sent_ctx and ctx.sentinel.alert_payload else None
                        sev_label  = (state.severity.value.title() if state.severity else "Unknown")
                        conf_pct   = f"{int((sent_ctx.confidence or 0) * 100)}%" if sent_ctx and sent_ctx.confidence is not None else "—"
                        _anomaly   = (sent_ctx.anomaly_type if sent_ctx else None) or alert.get("type", "unknown")
                        _msg       = (alert_pay.message if alert_pay else None) or alert.get("message", "")
                        sentinel_body = (
                            f"SENTINEL ANALYSIS — External Alert Received\n"
                            f"{'=' * 44}\n"
                            f"Source Connector : {source_connector}\n"
                            f"Alert Type       : {_anomaly}\n"
                            f"Severity         : {sev_label}\n"
                            f"Risk Score       : {risk_score:.1f} / 100\n"
                            f"Blast Radius     : {blast_radius}\n"
                            f"Confidence       : {conf_pct}\n"
                        )
                        if _msg:
                            sentinel_body += f"\nAlert Message:\n  {_msg}\n"
                        sentinel_body += (
                            f"\nAuto-remediation is DISABLED for the '{source_connector}' connector.\n"
                            f"This incident has been enriched and documented for operator review.\n"
                            f"To enable auto-execution, toggle 'Allow Auto-Remediation' in\n"
                            f"Connector Hub → {source_connector} → Configuration."
                        )
                        db.add(IncidentNoteModel(
                            workflow_id=wf_uuid,
                            author="Sentinel AI",
                            note_type="system",
                            body=sentinel_body,
                        ))

                        # ── Note 2: Librarian Assessment ──────────────────────
                        cmdb_ctx    = ctx.cmdb
                        res_info    = cmdb_ctx.resource_info if cmdb_ctx else None
                        res_name    = (cmdb_ctx.resource_name if cmdb_ctx else None) or resource_name
                        env_label   = (cmdb_ctx.environment if cmdb_ctx else None) or environment
                        owner       = (res_info.owner if res_info else None) or "—"
                        res_type    = (res_info.type  if res_info else None) or "—"
                        criticality = (res_info.criticality if res_info else None) or "—"
                        deps        = (cmdb_ctx.dependencies        if cmdb_ctx else []) or []
                        impacts     = (cmdb_ctx.impacted_services   if cmdb_ctx else []) or []
                        prop        = ctx.proposal
                        rb_name     = (prop.runbook_name if prop else None) or "—"
                        rb_id       = (prop.runbook_id   if prop else None) or "—"
                        rb_conf     = f"{int((prop.confidence or 0) * 100)}%" if prop and prop.confidence is not None else "—"

                        def _fmt_list(items: list, key: str = "name") -> str:
                            if not items:
                                return "  None\n"
                            return "".join(f"  • {i.get(key, str(i))}\n" for i in items[:6])

                        librarian_body = (
                            f"LIBRARIAN ASSESSMENT — CMDB Enrichment & Runbook Match\n"
                            f"{'=' * 54}\n"
                            f"Resource         : {res_name}\n"
                            f"Type             : {res_type}\n"
                            f"Environment      : {env_label}\n"
                            f"Owner            : {owner}\n"
                            f"Criticality      : {criticality}\n"
                            f"\nDependencies:\n{_fmt_list(deps)}"
                            f"\nImpacted Services:\n{_fmt_list(impacts)}"
                            f"\nMatched Runbook  : {rb_name}\n"
                            f"Runbook ID       : {rb_id}\n"
                            f"Match Confidence : {rb_conf}\n"
                            f"Blast Radius     : {blast_radius}\n"
                        )
                        db.add(IncidentNoteModel(
                            workflow_id=wf_uuid,
                            author="Librarian AI",
                            note_type="system",
                            body=librarian_body,
                        ))

                        # ── Note 3: Mechanic Recommendation ───────────────────
                        diag_steps = (prop.diagnostics_steps  if prop else []) or []
                        rem_steps  = (prop.remediation_steps  if prop else []) or []

                        def _fmt_steps(steps: list) -> str:
                            if not steps:
                                return "  None documented.\n"
                            lines = []
                            for i, s in enumerate(steps, 1):
                                name  = getattr(s, "name",        None) or s.get("name",        "Step") if isinstance(s, dict) else getattr(s, "name", "Step")
                                tool  = getattr(s, "tool",        None) or s.get("tool",        "—")    if isinstance(s, dict) else getattr(s, "tool", "—")
                                desc  = getattr(s, "description", None) or s.get("description", "")     if isinstance(s, dict) else getattr(s, "description", "")
                                args  = getattr(s, "args",        None) or s.get("args",        {})     if isinstance(s, dict) else getattr(s, "args", {})
                                lines.append(f"  {i}. {name}")
                                if desc:
                                    lines.append(f"     {desc}")
                                lines.append(f"     Tool: {tool}")
                                if args:
                                    import json as _json
                                    lines.append(f"     Args: {_json.dumps(args)}")
                                lines.append("")
                            return "\n".join(lines)

                        mechanic_body = (
                            f"MECHANIC RECOMMENDED REMEDIATION STEPS\n"
                            f"{'=' * 38}\n"
                            f"Runbook : {rb_name}\n"
                            f"Target  : {res_name}\n"
                            f"\nNOTE: Auto-remediation is DISABLED for '{source_connector}' alerts.\n"
                            f"These steps are for operator reference and manual execution.\n"
                            f"\nDIAGNOSTIC STEPS:\n{_fmt_steps(diag_steps)}"
                            f"\nREMEDIATION STEPS:\n{_fmt_steps(rem_steps)}"
                        )
                        db.add(IncidentNoteModel(
                            workflow_id=wf_uuid,
                            author="Mechanic AI",
                            note_type="action",
                            body=mechanic_body,
                        ))
                        db.commit()

                        # ── Transition to in_progress ─────────────────────────
                        state.lifecycle_state = LifecycleState.IN_PROGRESS
                        state.context["decision_result"] = "external_source_documented"
                        reasoning += f"\n  [EXTERNAL SOURCE] Alert from '{source_connector}' connector.\n"
                        reasoning += f"  Auto-remediation DISABLED — enrichment documented to work notes.\n"
                        reasoning += f"  DECISION: IN PROGRESS — operator notified via 3 work notes.\n"
                        reasoning += f"    Note 1 (Sentinel AI):  anomaly analysis + risk context\n"
                        reasoning += f"    Note 2 (Librarian AI): CMDB enrichment + runbook match\n"
                        reasoning += f"    Note 3 (Mechanic AI):  recommended remediation steps\n"

                except Exception as _ext_err:
                    logger.warning(f"External-source documentation failed: {_ext_err}", exc_info=True)
                    # Safe fallback: still mark in_progress but don't block on note failure
                    _external_documented = True
                    state.lifecycle_state = LifecycleState.IN_PROGRESS
                    state.context["decision_result"] = "external_source_documented"
                    state.context["external_source_connector"] = source_connector
                    reasoning += f"  [EXTERNAL SOURCE] Note writing failed ({_ext_err}) — set in_progress.\n"

            if _external_documented:
                pass  # lifecycle and decision_result already set above
            elif _connector_allows_auto and approval_required:
                # Connector explicitly authorizes auto-remediation — override the policy gate.
                # The connector admin accepted the risk when they enabled allow_auto_remediation;
                # governance policies reflect platform-wide defaults that the connector trust
                # level supersedes for this alert source.
                reasoning += f"  DECISION: ✅ AUTO-APPROVED (connector override)\n"
                reasoning += f"  Rationale: Source connector '{source_connector}' has allow_auto_remediation=True.\n"
                reasoning += f"  Connector trust level supersedes the policy approval requirement.\n"
                state.context["decision_result"] = "approved"
                # CRITICAL: also clear the governance approval_required flag so ToolRegistryAgent
                # does not block execution at its secondary check (ctx.governance.approval_required).
                if ctx.governance:
                    ctx.governance.approval_required = False
                    state = self._set_typed_context(state, ctx)
            elif not approval_required:
                # Policy says auto-approve
                policy_name = matched_user_policies[0].name if matched_user_policies else "governance policy"
                reasoning += f"  DECISION: ✅ AUTO-APPROVED\n"
                reasoning += f"  Rationale: Policy '{policy_name}' allows automatic remediation.\n"
                reasoning += f"  Proceeds directly to execution."
                state.context["decision_result"] = "approved"
            elif approval_required and (matched_user_policies or gov_matching_policy):
                # Policy matched but requires manual approval → halt; resume after approval
                reasoning += f"  DECISION: ⏸ MANUAL APPROVAL REQUIRED\n"
                reasoning += f"  Rationale: Matched policy requires human review.\n"
                state.lifecycle_state = LifecycleState.WAITING_APPROVAL
                state.context["decision_result"] = "pending_approval"
                # Record where resume_workflow_task must restart (tool_registry is next step)
                state.context["resume_from_step"] = "tool_registry"
            else:
                # No policy matched → conservative default: require approval
                reasoning += f"  ⚠️  NO MATCHING POLICIES FOUND\n"
                reasoning += f"  DECISION: ⏳ APPROVAL REQUIRED (conservative default)\n"
                reasoning += f"  Rationale: No policies matched — defaulting to require approval for safety.\n"
                reasoning += f"  Tip: Create a policy with matching rules to enable auto-remediation."
                state.lifecycle_state = LifecycleState.WAITING_APPROVAL
                state.context["decision_result"] = "pending"
                # Record where resume_workflow_task must restart (tool_registry is next step)
                state.context["resume_from_step"] = "tool_registry"

            db.close()
        except Exception as e:
            logger.error(f"Error evaluating governance policies: {e}")
            reasoning += f"  ⚠️ Policy evaluation error: {e}\n"
            reasoning += f"  DECISION: ⏳ APPROVAL REQUIRED (safe default on evaluation error)\n"
            reasoning += f"  Rationale: Cannot skip approval when policy evaluation fails — failing closed."
            # Fail closed: require manual approval so ToolRegistryAgent's gate blocks execution
            ctx.governance = GovernanceContext(
                matching_policies=[],
                approval_required=True,
                approval_priority=50,
                allowed_actions=[],
                decision_notes=f"Policy evaluation error — approval required as safe default: {e}",
            )
            state.lifecycle_state = LifecycleState.WAITING_APPROVAL
            state.context["decision_result"] = "pending_approval"
            state.context["resume_from_step"] = "tool_registry"
            # Persist typed context with the safe-default governance
            state = self._set_typed_context(state, ctx)

        state = self._add_trace(state, reasoning)
        return state


class ToolRegistryAgent(Agent):
    """Executes remediation actions via tool registry.

    Phase 10: Refactored to use typed context.
    Uses ctx.get_anomaly_process() for process_kill routing.
    Records execution results in ctx.execution_results.
    """

    # Catalog tools handled natively by _execute_notify_action (no shell command).
    # Used both to special-case dispatch in _execute_tool_impl and to tag these
    # steps' reported step_type as "notify" rather than "remediation" — the array
    # a step is stored in (diagnostics/actions/verification) only has 3 buckets
    # and collapses notify into "actions" at save time, so the tool name is the
    # only reliable signal left by the time a step actually executes.
    _NOTIFY_TOOL_NAMES = ("notify", "alert_escalate", "alert_update", "send_alert")

    def __init__(self):
        super().__init__("tool_registry")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Execute remediation action(s) — handles both single actions and multi-step runbooks"""
        from agentic_os.db.database import SessionLocal
        from agentic_os.services.governance_broker import GovernanceBroker

        # Get typed context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # ISSUE A FIX: Validate that governance decision was made
        if not self._validate_context_layer(state, "governance"):
            return self._handle_missing_context(
                state, "governance",
                "Cannot execute without governance decision"
            )

        # Get proposal from context (typed or untyped fallback)
        proposal = state.context.get("proposal", {})
        if not proposal and ctx.proposal:
            proposal = ctx.proposal.__dict__

        action = proposal.get("action", "unknown")

        # CRITICAL FIX: Get target resource from CMDB context if not in proposal
        target = proposal.get("target", "unknown")
        if target == "unknown" and ctx.cmdb:
            target = ctx.cmdb.resource_name

        # Resolve runbook_steps from multiple possible locations:
        #   1. proposal["runbook_steps"] (old flat format from untyped path)
        #   2. state.context["runbook_steps"] (set separately by MechanicAgent Tier 1)
        #   3. proposal["remediation_steps"] / proposal["diagnostics_steps"] (new typed format)
        #   4. proposal["steps"] (new editor unified-steps format — translated on the fly)
        runbook_steps = proposal.get("runbook_steps") or state.context.get("runbook_steps") or {}

        # Full visual-editor graph (steps + edges) for graph-aware execution.
        # Preferred source: MechanicAgent stores it in context during runbook selection.
        # Fallback: load directly from the DB when context is stale or MechanicAgent
        # hit an early-return path (validation failure / fast-path re-run).
        runbook_graph = state.context.get("runbook_graph") or {}
        if not runbook_graph:
            _runbook_id = proposal.get("runbook_id") or state.context.get("runbook_id")
            if _runbook_id and str(_runbook_id) not in ("fallback-escalate", "pb-unknown"):
                try:
                    from agentic_os.db.models import RunbookModel
                    from agentic_os.db.database import SessionLocal as _SessionLocal
                    _db = _SessionLocal()
                    try:
                        _rb = _db.query(RunbookModel).filter(
                            RunbookModel.id == str(_runbook_id)
                        ).first()
                        if _rb and _rb.source_steps:
                            runbook_graph = _rb.source_steps
                            logger.info(
                                f"[TOOL REGISTRY] Loaded runbook graph from DB "
                                f"(runbook_id={_runbook_id}, nodes={len(runbook_graph.get('steps') or [])})"
                            )
                    finally:
                        _db.close()
                except Exception as _e:
                    logger.warning(f"[TOOL REGISTRY] Could not load runbook graph from DB: {_e}")
        _graph_steps    = runbook_graph.get("steps") or []
        _graph_edges    = runbook_graph.get("edges") or []
        # Only use graph walk when there is at least one DECISION node.
        # Without decisions the flat executor is always equivalent and safer —
        # it avoids skipping steps when the visual-editor graph is an older,
        # simplified version that does not reflect all flat-array steps.
        _has_decision   = any(s.get("type") == "decision" for s in _graph_steps)
        _use_graph_walk = bool(_graph_steps and _graph_edges and _has_decision)

        # ── New editor format: unified "steps" array ──────────────────────────
        # The runbook editor exports a single "steps" list with all node types
        # (diagnostic, action, verification, decision, notify, start, end).
        # Translate to the legacy {diagnostics, actions, verification} dict so the
        # existing sequential executor runs unchanged. Decision / start / end nodes
        # are skipped — the routing logic lives in each step's "run_if" condition.
        # This path only triggers when runbook_steps is empty (new runbooks); existing
        # DB runbooks already arrive in the legacy format and are unaffected.
        if not runbook_steps and proposal.get("steps"):
            runbook_steps = ToolRegistryAgent._translate_editor_steps(proposal["steps"])

        # If no runbook_steps but proposal has new-format remediation_steps, convert them
        if not runbook_steps:
            typed_remediation = proposal.get("remediation_steps", [])
            typed_diagnostics = proposal.get("diagnostics_steps", [])
            typed_verification = proposal.get("verification_steps", [])
            if typed_remediation or typed_diagnostics or typed_verification:
                # Convert RunbookStep dicts (or dataclass instances) to the legacy format ToolRegistry expects
                def step_to_legacy(s, is_verification=False):
                    if isinstance(s, dict):
                        legacy = {
                            "tool": s.get("tool", "unknown"),
                            "name": s.get("name", ""),
                            "description": s.get("description", ""),
                            "args_json": s.get("args_json", s.get("args", {})),
                            "order": s.get("order", 0),
                        }
                        # For verification steps, preserve metric/check/value
                        if is_verification:
                            legacy.update({
                                "metric": s.get("metric"),
                                "check": s.get("check"),
                                "value": s.get("value"),
                            })
                        return legacy
                    # RunbookStep dataclass
                    legacy = {
                        "tool": getattr(s, "tool", "unknown"),
                        "name": getattr(s, "name", ""),
                        "description": getattr(s, "description", ""),
                        "args_json": getattr(s, "args_json", None) or getattr(s, "args", {}),
                        "order": getattr(s, "order", 0),
                    }
                    # For verification steps, preserve metric/check/value
                    if is_verification:
                        legacy.update({
                            "metric": getattr(s, "metric", None),
                            "check": getattr(s, "check", None),
                            "value": getattr(s, "value", None),
                        })
                    return legacy
                runbook_steps = {
                    "diagnostics": [step_to_legacy(s) for s in typed_diagnostics],
                    "actions": [step_to_legacy(s) for s in typed_remediation],
                    "verification": [step_to_legacy(s, is_verification=True) for s in typed_verification],
                }

        reasoning = f"[TOOL REGISTRY AGENT] Remediation execution\n"
        reasoning += f"  Target Resource: {target}\n"
        reasoning += f"  Action: {action.upper()}\n"

        # EXTERNAL-SOURCE DOCUMENTED: policy_broker already wrote enrichment to work notes;
        # no automated execution should happen.  Lifecycle stays in_progress.
        if state.context.get("decision_result") == "external_source_documented":
            reasoning += (
                f"\n  SKIP: External-source incident — remediation steps were documented to\n"
                f"  work notes by Mechanic AI. No automated execution will be performed.\n"
                f"  Lifecycle: in_progress — operator will handle manually.\n"
            )
            state = self._add_trace(state, reasoning)
            return state

        # APPROVAL GATE: if governance required approval, only proceed when it was granted.
        # Check uses a POSITIVE assertion (approved record must exist) rather than the
        # absence of a pending record — the old negative check passed whenever no record
        # existed at all, which is exactly the missing-record bypass that caused this bug.
        try:
            db = SessionLocal()
            broker = GovernanceBroker(db)
            approval_required = bool(ctx.governance and ctx.governance.approval_required)

            if approval_required:
                decision = broker.get_approval_decision(state.workflow_id)
                db.close()

                if decision not in ("approved", "diagnostics_only"):
                    reasoning += f"  \n"
                    reasoning += f"  ⛔ REMEDIATION BLOCKED: APPROVAL REQUIRED BUT NOT GRANTED\n"
                    reasoning += f"  governance.approval_required=True but approval decision is: {decision!r}\n"
                    reasoning += f"  Workflow must not proceed until a human approves via the approvals API."
                    state = self._add_trace(state, reasoning)
                    return state

                # Propagate execution mode so runbook execution knows what to run
                diagnostics_only_mode = (decision == "diagnostics_only")
            else:
                db.close()
                diagnostics_only_mode = False
        except Exception as e:
            logger.error(f"Error checking approval status: {e}", exc_info=True)
            # Fail-closed: if we cannot determine approval status and governance context
            # says approval is required, block execution rather than risk a bypass.
            if ctx.governance and ctx.governance.approval_required:
                reasoning += f"  ⛔ REMEDIATION BLOCKED: could not verify approval status ({e})\n"
                state = self._add_trace(state, reasoning)
                return state
            diagnostics_only_mode = False

        # Approval check passed — update lifecycle state and log execution mode
        state.lifecycle_state = LifecycleState.EXECUTING

        # Build the ordered list of (step_dict, step_type) tuples to execute.
        # Diagnostic steps always come first; remediation + verification steps only run in full-approval mode.
        # runbook_steps["diagnostics"], runbook_steps["actions"], and runbook_steps["verification"] are raw DB dicts (not dataclasses).
        diag_step_list  = runbook_steps.get("diagnostics") or []
        action_step_list = runbook_steps.get("actions") or []
        verify_step_list = runbook_steps.get("verification") or []

        # Safety check: if the visual-editor graph has fewer executable nodes than the
        # flat arrays, the graph is out of sync (e.g. simplified in the editor without
        # re-saving all steps). Fall back to the flat executor to avoid silently skipping steps.
        if _use_graph_walk:
            _graph_exec_count = sum(
                1 for s in _graph_steps
                if (s.get("type") or "").lower() not in ("start", "end", "decision")
            )
            _flat_exec_count = len(diag_step_list) + len(action_step_list) + len(verify_step_list)
            if _graph_exec_count < _flat_exec_count:
                logger.warning(
                    f"[TOOL REGISTRY] Graph has {_graph_exec_count} executable nodes but "
                    f"flat arrays have {_flat_exec_count} steps — graph out of sync, "
                    f"using flat executor"
                )
                _use_graph_walk = False

        if diagnostics_only_mode:
            steps_to_run = [(s, "diagnostic") for s in diag_step_list]
            reasoning += (
                f"  🔍 DIAGNOSTICS-ONLY APPROVAL — {len(steps_to_run)} diagnostic step(s) "
                f"will execute, remediation suppressed\n"
            )
        else:
            steps_to_run = (
                [(s, "diagnostic") for s in diag_step_list]
                + [(s, "remediation") for s in action_step_list]
                + [(s, "verification") for s in verify_step_list]
            )
            reasoning += (
                f"  ✅ FULL APPROVAL GRANTED — {len(diag_step_list)} diagnostic + "
                f"{len(action_step_list)} remediation + {len(verify_step_list)} verification step(s)\n"
            )

        # Extract and display action parameters (if any)
        # Exclude known metadata keys that aren't action parameters
        metadata_keys = {"action", "target", "rationale", "runbook_id", "runbook_name", "runbook_steps"}
        action_params = {k: v for k, v in proposal.items() if k not in metadata_keys and v is not None}

        if action_params:
            reasoning += f"  Parameters:\n"
            for key, value in action_params.items():
                if isinstance(value, (list, dict)):
                    reasoning += f"    - {key}: {len(value)} item(s)\n"
                else:
                    reasoning += f"    - {key}: {value}\n"

        # ──────────────────────────────────────────────────────────────
        # Resolve Kill-API base URL + adapter_mode for the detecting watcher
        # ──────────────────────────────────────────────────────────────
        _alert_payload_ctx = state.context.get("alert_payload", {})
        _watcher_name_ctx = _alert_payload_ctx.get("watcher_name", "watcher_brain")
        _watcher_base, _adapter_mode = _resolve_watcher_info(_watcher_name_ctx)
        logger.info(f"[TOOL REGISTRY] Kill-API routed to {_watcher_base} adapter={_adapter_mode} (watcher: {_watcher_name_ctx})")

        # ──────────────────────────────────────────────────────────────
        # Execute: Single action OR multi-step runbook
        # ──────────────────────────────────────────────────────────────
        if steps_to_run or _use_graph_walk:
            # Multi-step runbook execution (diagnostics + remediation, or diagnostics-only)
            if _use_graph_walk:
                reasoning += f"\n  📋 Multi-Step Runbook Execution (graph-guided, {len(_graph_steps)} nodes):\n"
            else:
                reasoning += f"\n  📋 Multi-Step Runbook Execution ({len(steps_to_run)} steps):\n"
            all_results = []
            overall_success = True

            # Incident number + title, fetched once and auto-injected into every step's
            # args below — notify-type steps (notify/alert_escalate/alert_update/send_alert)
            # prepend them to whatever message a runbook author writes, so a notification
            # can never go out as an unattributed one-liner with no incident reference.
            from agentic_os.db.database import SessionLocal as _NotifySessionLocal
            from agentic_os.services.enumeration_service import EnumerationService
            _notify_db = _NotifySessionLocal()
            try:
                _incident_number = EnumerationService.get_incident_number_str(_notify_db, str(state.workflow_id))
            finally:
                _notify_db.close()
            _incident_title = state.title or ""

            # Extract substitution values using typed context (CRITICAL FIX: use get_anomaly_process())
            # alert_payload uses "anomaly_process" for eBPF alerts and "process_name" for HTTP/external alerts
            _alert_ctx = state.context.get("alert_payload", {})
            anomaly_process = (
                ctx.get_anomaly_process()
                or _alert_ctx.get("anomaly_process", "")
                or _alert_ctx.get("process_name", "")
            )
            # container comes from alert_payload["container"] for external/HTTP alerts;
            # fall back to cmdb_context.resource_name for eBPF/syscall alerts.
            # Treat "unknown" as absent — it is the sentinel for "not resolved yet".
            _resolved_target = target if (target and target != "unknown") else ""
            container_name = (
                _resolved_target
                or _alert_ctx.get("resource_name", "")
                or _alert_ctx.get("container", "")
                or state.context.get("cmdb_context", {}).get("resource_name", "")
            )

            # step_outputs: keyed by step_idx → dict of named outputs from that step.
            # Diagnostic steps can deposit values (e.g. "top_process") that later steps
            # automatically receive via parameter substitution — no template syntax needed.
            step_outputs: Dict[int, Dict] = {}

            # Tracks whether a diagnostic step (e.g. top_processes) has actively
            # discovered the offending process.  When True, process_kill always uses
            # the discovered process — even if the runbook has a different static default.
            _process_discovered_by_diag = False

            _aborted_early = False
            _step_iter = (
                ToolRegistryAgent._walk_graph(_graph_steps, _graph_edges, step_outputs)
                if _use_graph_walk
                else iter(steps_to_run)
            )
            step_idx = 0

            # ── Per-target distributed lock ─────────────────────────────────
            # Only acquired when this run includes at least one mutating step.
            # Pure-diagnostic runs must never be blocked by another incident's
            # remediation on the same target.
            _needs_target_lock = bool(action_step_list) and not diagnostics_only_mode
            _target_lock_held = False
            if _needs_target_lock:
                import time as _lock_time
                TARGET_LOCK_TTL_SECONDS = 900        # 15 min — longer than the 5-min step timeout (core/workflow_engine.py:181)
                TARGET_LOCK_RETRY_DELAY_S = 5
                TARGET_LOCK_MAX_WAIT_SECONDS = 300    # matches the step-timeout convention

                from agentic_os.db.repositories import DistributedLockRepository
                _lock_db = SessionLocal()
                try:
                    _lock_repo = DistributedLockRepository(_lock_db)
                    _wait_start = _lock_time.time()
                    _target_lock_held = _lock_repo.acquire(target, state.workflow_id, TARGET_LOCK_TTL_SECONDS)
                    _attempt = 1
                    while not _target_lock_held and (_lock_time.time() - _wait_start) < TARGET_LOCK_MAX_WAIT_SECONDS:
                        state.add_trace(
                            f"[TARGET LOCK] '{target}' busy — retry {_attempt}, "
                            f"waiting {TARGET_LOCK_RETRY_DELAY_S}s"
                        )
                        _lock_time.sleep(TARGET_LOCK_RETRY_DELAY_S)
                        _target_lock_held = _lock_repo.acquire(target, state.workflow_id, TARGET_LOCK_TTL_SECONDS)
                        _attempt += 1
                finally:
                    _lock_db.close()

                if not _target_lock_held:
                    state.add_trace(
                        f"[TARGET LOCK] Could not acquire lock on '{target}' after "
                        f"{TARGET_LOCK_MAX_WAIT_SECONDS}s — escalating to manual approval."
                    )
                    reasoning += (
                        f"\n  ⏳ TARGET LOCKED: another incident is remediating '{target}'.\n"
                        f"  DECISION: MANUAL APPROVAL REQUIRED (conservative default)\n"
                    )
                    if ctx.governance:
                        ctx.governance.approval_required = True
                    state = self._set_typed_context(state, ctx)
                    state.lifecycle_state = LifecycleState.WAITING_APPROVAL
                    state.context["decision_result"] = "pending"
                    state.context["resume_from_step"] = "tool_registry"
                    state = self._add_trace(state, reasoning)
                    return state

            for _raw_step, _raw_type in _step_iter:
                # Renew the per-target lease each iteration so a long-running but
                # still-legitimate remediation doesn't lose its lock to the TTL
                # sweep mid-flight. If renewal fails, the lease is already gone
                # (TTL fired despite renewal, or reclaimed) — another incident may
                # now be acting on this target, so abort remaining mutating steps
                # rather than risk colliding with it.
                if _target_lock_held:
                    _renew_db = SessionLocal()
                    try:
                        _renewed = DistributedLockRepository(_renew_db).renew(
                            target, state.workflow_id, TARGET_LOCK_TTL_SECONDS
                        )
                    except Exception as _renew_err:
                        logger.error(f"[TARGET LOCK] Failed to renew '{target}': {_renew_err}", exc_info=True)
                        _renewed = False
                    finally:
                        _renew_db.close()
                    if not _renewed:
                        state.add_trace(
                            f"[TARGET LOCK] Lost lease on '{target}' mid-execution — "
                            f"aborting remaining steps (possible concurrent remediation)."
                        )
                        reasoning += (
                            f"\n  ⛔ LOST TARGET LOCK on '{target}' mid-execution — "
                            f"aborting remaining steps.\n"
                        )
                        _aborted_early = True
                        _target_lock_held = False  # already gone — skip the release block below
                        break

                # Decision nodes (graph walk only): evaluate branch condition and log.
                # No tool executes; the generator has already chosen the next node.
                if _raw_type == "decision":
                    _cond   = _raw_step.get("_condition", "")
                    _result = _raw_step.get("_decision_result")
                    _branch = _raw_step.get("_decision_branch", "")
                    reasoning += f"\n    ⬡ Decision: '{_cond}' → {_result} → {_branch!r} branch\n"
                    logger.info(f"[TOOL REGISTRY] Decision node: '{_cond}' → {_result} (branch: {_branch!r})")
                    continue

                # Wait nodes: pause execution for a fixed duration — no tool needed.
                if _raw_type == "wait":
                    import time as _wt
                    _dur = int(_raw_step.get("duration_seconds", 0))
                    _wname = _raw_step.get("name", "wait")
                    if _dur > 0:
                        reasoning += f"\n    ⏳ Wait: {_wname} ({_dur}s)\n"
                        logger.info(f"[TOOL REGISTRY] Wait node '{_wname}': sleeping {_dur}s")
                        _wt.sleep(_dur)
                    continue

                # Incident-update nodes: explicitly declare the incident's resolution
                # state. No tool executes — this only ever runs if every step before
                # it (including verification) succeeded, since on_failure=abort (the
                # default on every step) halts the loop before reaching it otherwise.
                # VerifierAgent reads this signal as the sole basis for marking an
                # incident resolved; its absence defaults to AWAITING_MANUAL.
                if _raw_type == "incident_update":
                    _iu_state = _raw_step.get("state", "resolved")
                    _iu_name = _raw_step.get("name", "Incident Update")
                    state.context["incident_update_requested"] = {"state": _iu_state}
                    reasoning += f"\n    ✎ Incident Update: {_iu_name} → state={_iu_state!r}\n"
                    logger.info(f"[TOOL REGISTRY] Incident update node '{_iu_name}': state={_iu_state!r}")
                    # Record a structured result too — otherwise this step is invisible
                    # in the incident detail UI, which renders from all_results/execution_results.
                    step_idx += 1
                    all_results.append({
                        "step": step_idx,
                        "tool": "incident_update",
                        "step_type": "incident_update",
                        "args": {"state": _iu_state},
                        "result": {
                            "success": True,
                            "message": f"{_iu_name}: incident marked '{_iu_state}'",
                            "output": f"{_iu_name}: incident marked '{_iu_state}'",
                        },
                    })
                    continue

                # Map graph node type ("action") to the incident workflow canonical type.
                # notify/notification get their own step_type — not remediation — so they
                # render as a distinct "Notifications" section in the incident UI instead
                # of looking like a remediation action, and so they aren't blocked below.
                if _raw_type in ("notify", "notification"):
                    step_type = "notify"
                elif _raw_type == "action":
                    step_type = "remediation"
                else:
                    step_type = _raw_type
                step = _raw_step
                # Linear (non-graph-walk) runbooks lose the notify/action distinction at
                # save time — _translate_editor_steps stores both in the same "actions"
                # array — so by this point step_type may already be the wrong "remediation".
                # The tool name is the only signal still reliable regardless of which path
                # got us here; re-check it before the diagnostics-only gate below.
                if step_type == "remediation" and step.get("tool") in ToolRegistryAgent._NOTIFY_TOOL_NAMES:
                    step_type = "notify"
                # In diagnostics-only mode, stop before any remediation step. Notify steps
                # are exempt — sending a notification carries no remediation risk, and
                # "diagnose only, but still tell the team" is a reasonable thing to want.
                if diagnostics_only_mode and step_type == "remediation":
                    break
                step_idx += 1
                import time as _time
                # For verification steps there is no "tool" key — derive a meaningful label
                # from the metric so the UI shows e.g. "verify:disk_percent" instead of "unknown"
                if step_type == "verification":
                    _metric = step.get("metric") or step.get("name") or "check"
                    step_tool = f"verify:{_metric}"
                else:
                    step_tool = step.get("tool", "unknown")
                # DB runbook steps use "args"; converted steps use "args_json".
                # Support both so MechanicAgent's raw DB path doesn't lose parameters.
                step_args = step.get("args_json") or step.get("args") or {}
                step_description = step.get("description", "")

                # ── Inter-step delay (declarative, set in runbook step args) ────
                # Runbook authors can add "delay_before_seconds": N to any step
                # to pause before execution.  Useful when a prior step needs time
                # to take effect (e.g. process_kill → process_verify).
                delay_secs = step_args.get("delay_before_seconds", 0)
                if delay_secs > 0:
                    logger.info(f"[TOOL] Step {step_idx} ({step_tool}): waiting {delay_secs}s (delay_before_seconds)")
                    _time.sleep(delay_secs)

                # ── Step-output chaining: resolve references to previous steps ──
                # Any arg with key "<field>_from_step": N is replaced with the named
                # output field from step N (e.g. "process_name_from_step": 1 pulls
                # step_outputs[1]["top_process"]).
                # Also handles "{{steps.N.field}}" template strings.
                step_args = self._resolve_step_references(step_args, step_outputs, extra_context=_alert_ctx)

                # ── Inject anomaly_process when process_name is missing or useless ──
                # Three scenarios:
                #   1. Diagnostic tools (e.g. get_metrics {"metric":"syscall_rate"}) have no
                #      process_name arg at all → inject it so they can show the known process.
                #   2. Step-chaining resolved process_name to "unknown" (diagnostic returned
                #      generic data) → override with the alert's known anomaly_process so
                #      the subsequent process_kill targets the right process.
                #   3. A diagnostic step (e.g. top_processes) actively discovered the
                #      offending process — always override process_kill regardless of any
                #      static default in the runbook args (e.g. hardcoded "yes").
                _static_proc = step_args.get("process_name", "")
                _is_kill_step = "process_kill" in step_tool or "process_signal" in step_tool
                _diag_override = _process_discovered_by_diag and _is_kill_step and anomaly_process
                if anomaly_process and (_static_proc in ("", "unknown") or _diag_override):
                    step_args = {**step_args, "process_name": anomaly_process}
                    logger.info(
                        f"[CHAIN] Step {step_idx} ({step_tool}): process_name set to "
                        f"'{anomaly_process}' "
                        f"({'diagnostic discovery' if _diag_override else 'fallback inject'})"
                    )

                # ── Inject service_url / service_port from incident context ───
                step_args = self._inject_incident_context(step_args, _alert_ctx)

                # ── Last-resort: unresolved target-identity placeholder fallback ──
                step_args = self._fill_unresolved_target_aliases(step_args, target)

                # ── run_if: skip this step if its condition is not met ────────
                # Evaluated after step-output chaining so conditions can reference
                # values discovered by earlier diagnostic steps (e.g. top_process).
                run_if = step.get("run_if", "").strip()
                if run_if:
                    should_run = ToolRegistryAgent._evaluate_condition(
                        run_if, step_outputs, anomaly_process, container_name, ctx
                    )
                    if not should_run:
                        skip_msg = f"Condition not met: {run_if}"
                        logger.info(f"[RUN_IF] Step {step_idx} ({step_tool}) SKIPPED — {skip_msg}")
                        reasoning += f"\n    Step {step_idx}: {step_tool} — ⏭ SKIPPED ({skip_msg})\n"
                        all_results.append({
                            "step": step_idx,
                            "tool": step_tool,
                            "step_type": step_type,
                            "args": step_args,
                            "result": {
                                "success": True,
                                "skipped": True,
                                "message": skip_msg,
                                "output": skip_msg,
                                "run_if": run_if,
                            },
                        })
                        continue  # move to next step without executing

                # ── Parameter substitution: Replace placeholders with actual values ──
                substituted_args = self._substitute_runbook_parameters(
                    step_args,
                    process_name=anomaly_process,
                    container=container_name
                )
                # Authoritative — not a catalog-exposed param, always overwritten so a
                # step can't accidentally (or by stale template value) carry a stale one.
                substituted_args["incident_number"] = _incident_number
                substituted_args["incident_title"] = _incident_title
                substituted_args["runbook_name"] = proposal.get("runbook_name", "")

                reasoning += f"\n    Step {step_idx}: {step_tool}\n"
                if step_description:
                    reasoning += f"      Description: {step_description}\n"
                if run_if:
                    reasoning += f"      Condition: {run_if} → ✅ met\n"
                if delay_secs > 0:
                    reasoning += f"      Delay: {delay_secs}s before execution\n"
                if substituted_args:
                    reasoning += f"      Parameters: {substituted_args}\n"

                # ── Special handling for verification steps (metric checks) ────────────
                # Verification steps evaluate conditions against collected metrics (e.g.,
                # "container_status equals running"). If the node carries its own tool
                # (the standard authoring pattern — re-run the same diagnostic post-fix
                # and capture the result under an "_after"-style name), re-execute it now
                # to get a fresh post-remediation measurement, mirroring what the editor's
                # Test Run harness already does. Without this, "_after" metrics that only
                # this node's own output_capture ever produces are never actually measured.
                if step_type == "verification":
                    metric = step.get("metric")
                    # DB stores as threshold_type/threshold; API layer converts to check/value
                    # Support both field names so the executor works regardless of source
                    check = step.get("check") or step.get("threshold_type")
                    expected_value = step.get("value") or step.get("threshold")
                    verify_tool = step.get("tool", "")
                    _fresh_structured: dict = {}

                    if verify_tool:
                        _fresh_result = ToolRegistryAgent._execute_tool(
                            verify_tool, substituted_args, container_name, _watcher_base, _adapter_mode
                        )
                        _fresh_structured = _fresh_result.get("structured") or {}
                        if not _fresh_structured and _fresh_result.get("success") and _fresh_result.get("raw_output"):
                            _fresh_structured = ToolRegistryAgent._parse_tool_output(verify_tool, _fresh_result["raw_output"]) or {}
                        _verify_output_capture = step.get("output_capture") or {}
                        if _fresh_structured and _verify_output_capture:
                            _fresh_structured = ToolRegistryAgent._apply_output_capture(_fresh_structured, _verify_output_capture)
                        if _fresh_structured:
                            step_outputs[step_idx] = _fresh_structured
                            _verify_step_id = step.get("id")
                            if _verify_step_id:
                                step_outputs[_verify_step_id] = _fresh_structured

                    if metric and check:
                        # Check if any prior step that produced this metric reported measurement_failed.
                        # If so, we cannot trust a zero/low value — mark verification as failed rather
                        # than incorrectly resolving the incident.
                        _prior_failed = any(
                            (step_outputs.get(k) or {}).get("measurement_failed")
                            for k in step_outputs
                        )
                        # Try to read the actual metric value — prefer this step's own
                        # fresh post-remediation measurement over any older step_outputs
                        # entry. A prior diagnostic step (e.g. the pre-remediation health
                        # check) commonly captures a field with the SAME name (e.g.
                        # "reachable") under its own output_capture; scanning step_outputs
                        # in insertion order would find that stale, pre-fix value first and
                        # never reach this step's own just-measured result.
                        _actual_val = None
                        if metric in _fresh_structured:
                            _actual_val = _fresh_structured[metric]
                        else:
                            for _so in step_outputs.values():
                                if isinstance(_so, dict) and metric in _so:
                                    _actual_val = _so[metric]
                                    break

                        if _prior_failed:
                            step_result = {
                                "success": False,
                                "message": f"Verification skipped: prior diagnostic step could not measure '{metric}' (permission error or tool failure). Cannot confirm remediation succeeded.",
                                "output": f"Verification FAILED — measurement unavailable for {metric}",
                                "command": f"verify {metric} {check} {expected_value}",
                                "metric": metric,
                                "check": check,
                                "expected_value": expected_value,
                                "verified": False,
                                "measurement_failed": True,
                                "structured": {"verified": False},
                            }
                        elif _actual_val is not None:
                            # Evaluate against the actual captured value
                            try:
                                _v = float(_actual_val)
                                _e = float(expected_value) if expected_value is not None else None
                                if _e is not None:
                                    if check in ("less_than", "<"):
                                        _passed = _v < _e
                                    elif check in ("greater_than", ">"):
                                        _passed = _v > _e
                                    elif check in ("equal", "==", "equals"):
                                        _passed = _v == _e
                                    elif check in ("not_equal", "!="):
                                        _passed = _v != _e
                                    else:
                                        _passed = True
                                else:
                                    _passed = True
                            except (TypeError, ValueError):
                                _passed = True
                            step_result = {
                                "success": _passed,
                                "message": f"Verification: {metric}={_actual_val} {check} {expected_value} → {'PASSED' if _passed else 'FAILED'}",
                                "output": f"Verification {'passed' if _passed else 'FAILED'}: {metric}={_actual_val} {check} {expected_value}",
                                "command": f"verify {metric} {check} {expected_value}",
                                "metric": metric, "check": check,
                                "expected_value": expected_value,
                                "actual_value": _actual_val,
                                "verified": _passed,
                                "structured": {"verified": _passed, metric: _actual_val},
                            }
                        else:
                            # No captured value and no measurement failure — fail closed.
                            # A verification step that can't measure its own metric (no tool
                            # wired, or the tool ran but never produced this field) must not
                            # silently grant resolution — that was the root cause of incidents
                            # being marked resolved without the underlying problem ever being
                            # confirmed fixed. Previously this fell back to "assumed pass".
                            step_result = {
                                "success": False,
                                "message": f"Verification: {metric} {check} {expected_value} — metric was never measured (no tool wired on this step, or the tool ran but didn't produce '{metric}'). Cannot confirm remediation succeeded.",
                                "output": f"Verification FAILED — '{metric}' not measured",
                                "command": f"verify {metric} {check} {expected_value}",
                                "metric": metric, "check": check,
                                "expected_value": expected_value,
                                "verified": False,
                                "structured": {"verified": False},
                            }
                    else:
                        step_result = {
                            "success": False,
                            "error": "Verification step missing metric/check fields",
                            "command": "unknown",
                        }
                else:
                    # Execute this step with substituted parameters using real tool executor
                    step_result = ToolRegistryAgent._execute_tool(
                        step_tool, substituted_args, container_name, _watcher_base, _adapter_mode
                    )

                # ── Capture step outputs for chaining ────────────────────────────
                # Store structured output from any successful step so downstream
                # steps can reference values via run_if / _resolve_step_references.
                # _parse_tool_output produces structured from raw_output for known tools.
                if step_result.get("success"):
                    structured = step_result.get("structured") or {}
                    if structured:
                        output_capture = step.get("output_capture") or {}
                        if output_capture:
                            structured = ToolRegistryAgent._apply_output_capture(structured, output_capture)
                        step_outputs[step_idx] = structured           # int key  (legacy run_if: step_N.field)
                        _step_id = step.get("id")
                        if _step_id:
                            step_outputs[_step_id] = structured       # str key  (editor format: diag_11.field)
                        logger.info(f"[CHAIN] Step {step_idx} ({step_tool}) output captured: {list(structured.keys())}")

                    # If a diagnostic step discovers a better process name, propagate it
                    # forward so all remaining steps (including process_kill) use it.
                    # _process_discovered_by_diag ensures process_kill always uses this
                    # even if the runbook has a different static default.
                    discovered = step_result.get("top_process") or structured.get("top_process")
                    if discovered and discovered not in ("", "unknown"):
                        if discovered != anomaly_process:
                            logger.info(
                                f"[CHAIN] Step {step_idx} ({step_tool}) identified process: "
                                f"'{discovered}' (was: '{anomaly_process}') — updating for remaining steps"
                            )
                            reasoning += f"      → Identified process: '{discovered}' (will be used in subsequent steps)\n"
                        anomaly_process = discovered
                        _process_discovered_by_diag = True

                all_results.append({
                    "step": step_idx,
                    "tool": step_tool,
                    "step_type": step_type,     # "diagnostic" or "remediation" from steps_to_run tuple
                    "args": substituted_args,   # resolved args — passed to frontend for command display
                    "result": step_result
                })

                if step_result.get("success"):
                    reasoning += f"      Status: ✅ SUCCESS - {step_result.get('message', 'Completed')}\n"
                else:
                    # Verification-step results carry their reason under 'message'/
                    # 'output' (e.g. "Verification FAILED: reachable=False equals
                    # True"), not 'error' — tool-execution results use 'error'. Fall
                    # back through both rather than showing the unhelpful default,
                    # which previously hid genuinely-measured failures (a real
                    # reachable=False, not a measurement problem) behind "Unknown error".
                    error_msg = (
                        step_result.get('error')
                        or step_result.get('message')
                        or step_result.get('output')
                        or 'Unknown error'
                    )
                    reasoning += f"      Status: ❌ FAILED - {error_msg}\n"
                    overall_success = False

                    # on_failure policy (per-step, defined in runbook):
                    #   abort    (default) — stop here, do not execute remaining steps.
                    #                        A failed/timed-out step is often a prerequisite
                    #                        for subsequent steps; proceeding blindly can make
                    #                        things worse (as seen in INC0001: process_kill
                    #                        timed out, then restart_service triggered INC0002).
                    #   continue — skip this step's failure and keep going.
                    #              Use only for non-critical, independent clean-up steps.
                    #   Legacy: "required: true" is treated as on_failure=abort.
                    on_failure = step.get("on_failure", "abort" if step.get("required", True) else "continue")

                    if on_failure != "continue":
                        _remaining = (
                            len(steps_to_run) - step_idx if not _use_graph_walk else "?"
                        )
                        _total_str = (
                            str(len(steps_to_run)) if not _use_graph_walk else "?"
                        )
                        reasoning += (
                            f"      ⛔ Aborting — on_failure={on_failure}. "
                            f"{_remaining} remaining step(s) will NOT execute.\n"
                        )
                        state.add_trace(
                            f"Step {step_idx}/{_total_str} ({step_tool}) failed: "
                            f"{error_msg} — aborting (on_failure={on_failure})"
                        )
                        _aborted_early = True
                        break
                    else:
                        reasoning += f"      ⚠️  Continuing despite failure (on_failure=continue)\n"

            # Release the per-target lock acquired above — covers normal loop
            # completion and early `break` on abort (both fall through to this
            # point). The renewal-failure break already cleared
            # _target_lock_held, since that lease is gone before we get here.
            # An uncaught exception escaping the loop body is the one path
            # this doesn't cover; that's bounded by the lock's TTL sweep
            # (cleanup_expired_target_locks) instead, consistent with this
            # lock being a lease, not a hard mutex.
            if _target_lock_held:
                _release_db = SessionLocal()
                try:
                    DistributedLockRepository(_release_db).release(target, state.workflow_id)
                except Exception as _release_err:
                    logger.error(f"[TARGET LOCK] Failed to release '{target}': {_release_err}", exc_info=True)
                finally:
                    _release_db.close()

            # Store all step results in typed context.
            # "step_type" is the canonical "diagnostic"/"remediation" tag from steps_to_run.
            # "parameters" carries the fully-resolved args for frontend command display.
            # "command" is the real CLI string returned by the tool (e.g. watcher_brain).
            # "output"  is the interpreted summary; prefer over "message" (short summaries).
            ctx.execution_results = [
                {
                    "step": r["step"],
                    "tool": r["tool"],
                    "step_type": r["step_type"],
                    "parameters": r.get("args", {}),
                    "command": r["result"].get("command", ""),
                    "raw_output": r["result"].get("raw_output"),
                    # skipped steps get their own status so the UI can render them distinctly
                    "status": (
                        "skipped" if r["result"].get("skipped")
                        else "success" if r["result"].get("success")
                        else "failed"
                    ),
                    "run_if": r["result"].get("run_if", ""),   # condition string for UI tooltip
                    "output": (
                        r["result"].get("output")
                        or r["result"].get("message")
                        or r["result"].get("error", "")
                    ),
                }
                for r in all_results
            ]
            state = self._set_context(state, "runbook_execution_results", all_results)

            # Persist per-step outcomes for Platform Intelligence (Enhancement 1) — every
            # step here already produced a pass/fail signal that's normally discarded
            # after the abort/continue decision above; this keeps it instead. Best-effort
            # and never allowed to affect the live incident pipeline: any failure here is
            # logged and swallowed, not raised.
            _step_db = None
            try:
                from agentic_os.db.repositories import RunbookStepOutcomeRepository
                from agentic_os.agents.tuning_agent import classify_failure
                _rb_id_for_steps = None
                if _runbook_id and str(_runbook_id) not in ("fallback-escalate", "pb-unknown"):
                    try:
                        _rb_id_for_steps = uuid.UUID(str(_runbook_id))
                    except (ValueError, TypeError):
                        _rb_id_for_steps = None
                _step_db = SessionLocal()
                step_repo = RunbookStepOutcomeRepository(_step_db)
                for r in all_results:
                    res = r["result"]
                    error_msg = res.get("error") or None
                    if res.get("skipped"):
                        status = "skipped"
                    elif res.get("success"):
                        status = "succeeded"
                    elif "timeout" in str(error_msg or "").lower() or "timed out" in str(error_msg or "").lower():
                        status = "timed_out"
                    else:
                        status = "failed"
                    step_repo.create({
                        "workflow_id": state.workflow_id,
                        "runbook_id":  _rb_id_for_steps,
                        "step_index":  r["step"],
                        "step_type":   r["step_type"],
                        "tool":        r["tool"],
                        "status":      status,
                        "error_message": error_msg,
                        "failure_category": classify_failure(error_msg) if status in ("failed", "timed_out") else None,
                    })
                _step_db.commit()
            except Exception as _step_persist_err:
                logger.warning(f"[ToolRegistryAgent] Could not persist step outcomes (non-fatal): {_step_persist_err}")
                if _step_db:
                    try:
                        _step_db.rollback()
                    except Exception:
                        pass
            finally:
                if _step_db:
                    try:
                        _step_db.close()
                    except Exception:
                        pass

            # In graph-guided mode, the runbook "succeeded" if it completed its decision
            # path without a hard abort — steps that failed with on_failure=continue are
            # informational and do not constitute an execution failure.
            if _use_graph_walk and not _aborted_early:
                overall_success = True

            execution_result = {
                "success": overall_success,
                "execution_id": f"exec-{uuid.uuid4().hex[:8]}",
                "message": f"Executed {len(all_results)} runbook steps",
                "step_results": all_results
            }

            steps_run = len(all_results)
            steps_total = len(steps_to_run) if not _use_graph_walk else steps_run
            steps_succeeded = sum(1 for r in all_results if r['result'].get('success'))
            aborted_early = _aborted_early if _use_graph_walk else steps_run < steps_total

            if overall_success:
                if diagnostics_only_mode:
                    reasoning += (
                        f"\n  Overall Status: 🔍 DIAGNOSTICS COMPLETE ({steps_run} step(s))\n"
                        f"  Remediation suppressed — workflow halted. Human review required to proceed.\n"
                    )
                    state.context["decision_result"] = "diagnostics_only"
                    state.lifecycle_state = LifecycleState.IN_PROGRESS
                    state.remediation_outcome = "diagnostics_only"
                else:
                    reasoning += f"\n  Overall Status: ✅ RUNBOOK EXECUTED SUCCESSFULLY ({steps_run} steps completed)\n"
                    reasoning += f"  Next Step: Verification phase will confirm remediation effectiveness."
                    state.context["decision_result"] = "approved"
                    state.remediation_outcome = "pending"  # Verifier will confirm succeeded/failed
            else:
                if aborted_early:
                    reasoning += (
                        f"\n  Overall Status: ❌ RUNBOOK ABORTED — step failure stopped execution "
                        f"({steps_succeeded}/{steps_run} steps succeeded, "
                        f"{steps_total - steps_run} step(s) not attempted)\n"
                    )
                    state.remediation_outcome = "aborted"
                else:
                    reasoning += (
                        f"\n  Overall Status: ⚠️ RUNBOOK PARTIALLY COMPLETED "
                        f"({steps_succeeded}/{steps_run} steps succeeded)\n"
                    )
                    state.remediation_outcome = "pending"  # Verifier will set final outcome
                state.context["decision_result"] = "approved"  # Still run verifier to confirm state
        elif diagnostics_only_mode:
            # diagnostics_only with no matching diagnostic steps in this runbook — nothing to execute
            execution_result = {
                "success": True,
                "message": "Diagnostics-only approval: no diagnostic steps matched in runbook",
            }
            ctx.execution_results = []
            reasoning += f"\n  🔍 DIAGNOSTICS COMPLETE — no diagnostic steps matched in runbook\n"
            reasoning += f"  Remediation suppressed — workflow halted. Human review required to proceed.\n"
            state.context["decision_result"] = "diagnostics_only"
            state.lifecycle_state = LifecycleState.IN_PROGRESS
            state.remediation_outcome = "diagnostics_only"

        else:
            # Single action execution (full approval, no runbook steps)
            execution_result = await self._execute_action(action, proposal, _watcher_base, _adapter_mode)

            # Store single action result in typed context
            if execution_result.get("success"):
                ctx.execution_results = [{
                    "step": 1,
                    "tool": action,
                    "step_type": "remediation",
                    "status": "success",
                    "output": execution_result.get("message", "Completed"),
                }]
            else:
                ctx.execution_results = [{
                    "step": 1,
                    "tool": action,
                    "step_type": "remediation",
                    "status": "failed",
                    "output": execution_result.get("error", "Unknown error"),
                }]

            if execution_result.get("success"):
                reasoning += f"  Execution ID: {execution_result.get('execution_id', 'N/A')}\n"
                reasoning += (
                    f"  Status: ✅ SUCCESS\n"
                    f"  Result: {execution_result.get('message', 'Action completed')}\n"
                    f"  Reasoning: Action was successfully applied to the target resource.\n"
                    f"  Next Step: Verification phase will confirm remediation effectiveness."
                )
                state.context["decision_result"] = "approved"
            else:
                reasoning += f"  Execution ID: {execution_result.get('execution_id', 'N/A')}\n"
                reasoning += (
                    f"  Status: ❌ FAILED\n"
                    f"  Error: {execution_result.get('error', 'Unknown error')}\n"
                    f"  Reasoning: Action could not be executed. May indicate permission issues,\n"
                    f"  resource unavailability, or configuration problems."
                )
                state.context["decision_result"] = "rejected"

        # Persist typed context with execution results
        state = self._set_typed_context(state, ctx)
        state = self._set_context(state, "execution_result", execution_result)
        state = self._add_trace(state, reasoning)
        return state

    @staticmethod
    def _resolve_step_references(args: Dict, step_outputs: Dict[int, Dict], extra_context: Dict = None) -> Dict:
        """
        Resolve references to previous step outputs before parameter substitution.

        Supports two reference patterns:

        1. ``"<field>_from_step": N``
           The key is replaced with ``<field>`` whose value is looked up in
           step_outputs[N].  The mapping prefers a key that matches <field>
           exactly; if not found it falls back to the first value in that
           step's output dict.

           Example (runbook JSON):
             { "process_name_from_step": 1 }
           Resolves to:
             { "process_name": "yes" }   # step 1 set {"top_process": "yes"}

        2. ``"{{steps.N.field}}"`` template in string values
           Supports Jinja-like inline references inside any string argument.

           Example:
             { "target_process": "{{steps.1.top_process}}" }
           Resolves to:
             { "target_process": "yes" }
        """
        import re

        if not args or not step_outputs:
            return args

        resolved = dict(args)
        keys_to_remove = []

        # Pattern 1: "<field>_from_step": N
        for key, value in list(resolved.items()):
            if key.endswith("_from_step") and isinstance(value, int):
                step_out = step_outputs.get(value, {})
                field_name = key[: -len("_from_step")]  # strip suffix
                # Look up field by exact name first, then take the first value
                if field_name in step_out:
                    resolved[field_name] = step_out[field_name]
                elif step_out:
                    resolved[field_name] = next(iter(step_out.values()))
                keys_to_remove.append(key)
                logger.debug(f"[CHAIN] Resolved '{key}' → '{field_name}': {resolved.get(field_name)}")

        for k in keys_to_remove:
            del resolved[k]

        # Pattern 2: "{{steps.N.field}}" in string values
        def _sub_indexed(match: "re.Match") -> str:
            step_num = int(match.group(1))
            field = match.group(2)
            return str(step_outputs.get(step_num, {}).get(field, match.group(0)))

        template_re = re.compile(r"\{\{steps\.(\d+)\.(\w+)\}\}")
        for key, value in resolved.items():
            if isinstance(value, str) and "{{steps." in value:
                resolved[key] = template_re.sub(_sub_indexed, value)

        # Pattern 3: "{{variable_name}}" — flat reference to any output_capture variable
        # across ALL prior steps (both int and str keys in step_outputs).
        # This covers the common seed-data pattern: {"process_name": "{{top_process_name}}"}
        # where top_process_name was captured by output_capture in a prior diagnostic step.
        flat_re = re.compile(r"\{\{(\w+)\}\}")
        # Build a flat lookup dict: incident context is the base, step outputs override
        _flat_vars: Dict = dict(extra_context or {})
        for _so in step_outputs.values():
            if isinstance(_so, dict):
                _flat_vars.update(_so)

        for key, value in resolved.items():
            if isinstance(value, str) and "{{" in value and "steps." not in value:
                def _sub_flat(match: "re.Match") -> str:
                    var = match.group(1)
                    v = _flat_vars.get(var)
                    return str(v) if v is not None else match.group(0)
                resolved[key] = flat_re.sub(_sub_flat, value)

        # Pattern 4: "{{step_id.field}}" — named reference to a specific step's output by
        # its editor-assigned id (e.g. {{verify_service.http_code}}). This is the same
        # step_id.field syntax run_if/decision conditions already use (and the
        # VariableHelper chips in the graph editor already display) — bare there since
        # that mini-language doesn't use braces, wrapped in {{}} here since this is plain
        # string substitution. Disjoint from Pattern 2 ({{steps.N.field}}, two dots, numeric)
        # and Pattern 3 ({{field}}, no dot), so all four can coexist in the same string.
        named_re = re.compile(r"\{\{(\w+)\.(\w+)\}\}")
        def _sub_named(match: "re.Match") -> str:
            step_id, field = match.group(1), match.group(2)
            step_out = step_outputs.get(step_id, {})
            if isinstance(step_out, dict) and field in step_out:
                return str(step_out[field])
            return match.group(0)

        for key, value in resolved.items():
            if isinstance(value, str) and "{{" in value:
                resolved[key] = named_re.sub(_sub_named, value)

        return resolved

    @staticmethod
    def _apply_output_capture(structured: Dict, output_capture: Dict) -> Dict:
        """
        Merge a step's output_capture mapping (variable_name -> JSONPath, e.g. "$.field" or
        "$.nested.field") into its structured output, so decision/run_if conditions can
        reference the runbook author's chosen variable names (e.g. "memory_pct") instead of
        the tool's native field names (e.g. "used_percent"). Falls back to treating var_name
        itself as a literal key when the JSONPath doesn't resolve.

        Shared by the real incident-execution loop and the editor's /execute-editor preview
        endpoint — they must agree, or a runbook that "works" in Test Run silently breaks on
        a real incident (decision conditions referencing capture-only variable names would
        always resolve to None).
        """
        if not output_capture or not structured:
            return structured
        captured: Dict = {}
        for var_name, jpath in output_capture.items():
            field = jpath.lstrip("$").lstrip(".") if isinstance(jpath, str) else ""
            if "." in field:
                val = structured
                for p in field.split("."):
                    val = val.get(p) if isinstance(val, dict) else None
            else:
                val = structured.get(field) if field else None
            if val is None:
                val = structured.get(var_name)  # fallback: try var_name directly
            captured[var_name] = val
        return {**structured, **captured}

    @staticmethod
    def _substitute_runbook_parameters(args: Dict, process_name: str = "", container: str = "") -> Dict:
        """
        Substitute placeholders in runbook step parameters.

        Replaces:
          - <PID> with actual process name (e.g., 'yes')
          - <CONTAINER> with actual container name (e.g., 'sentinel_senses')
          - process_name_from_context: "anomaly_process" → process_name: "yes"
          - container_from_context: any key → actual container name

        Args:
            args: Step parameters dict
            process_name: Actual process name from anomaly detection
            container: Actual container name from alert payload

        Returns:
            Substituted parameters dict
        """
        if not args:
            return {}

        import json

        # Make a copy to avoid mutating the original
        substituted = dict(args)

        # Handle special keys that reference context values
        if "process_name_from_context" in substituted and process_name:
            substituted["process_name"] = process_name
            del substituted["process_name_from_context"]
            logger.debug(f"[SUBST] Replaced process_name_from_context with actual process: {process_name}")

        # Handle container reference
        if "container_from_context" in substituted and container:
            substituted["container"] = container
            del substituted["container_from_context"]
            logger.debug(f"[SUBST] Replaced container_from_context with actual container: {container}")

        # Also do placeholder replacement for backward compatibility.
        # Handles multiple template styles:
        #   <PID>               → process_name  (original)
        #   <CONTAINER>         → container     (original)
        #   {anomaly_process}   → process_name  (LLM-generated runbooks)
        #   {process_name}      → process_name  (alternate LLM style)
        #   {container}         → container
        args_json = json.dumps(substituted)

        if process_name:
            args_json = args_json.replace("<PID>", process_name)
            args_json = args_json.replace("{anomaly_process}", process_name)
            args_json = args_json.replace("{process_name}", process_name)

        if container:
            args_json = args_json.replace("<CONTAINER>", container)
            args_json = args_json.replace("{container}", container)
            args_json = args_json.replace("{target}", container)
            args_json = args_json.replace("{resource_name}", container)
            args_json = args_json.replace("{service}", container)
            args_json = args_json.replace("{host}", container)

        result = json.loads(args_json)

        return result

    @staticmethod
    def _inject_incident_context(step_args: Dict, alert_ctx: Dict) -> Dict:
        """
        Inject structured incident context fields as fallback values into step args.

        Three behaviours:
        1. service_url / service_port — always injected as extra subs so {service_url}
           placeholders in command templates resolve to real values at runtime.
        2. url arg — if the step's url value looks like a placeholder (empty, or contains
           the literal text "service-url", or is the bare template "{service_url}"), replace
           it with the incident's service_url so the tool hits the real endpoint.
        3. process_name — if the step's process_name is empty or a bare unresolved
           placeholder (e.g. "{{web_service}}" left over from an AI-generated step whose
           variable name was never actually captured by any diagnostic step), fall back to
           the incident context's own process_name field.
        """
        injected = dict(step_args)
        # "check_url"/"port" are the keys the watcher's external-check alerts use
        # (create_external_anomaly_alert) — "service_url"/"service_port" are what
        # manually-created incidents (routes/workflows.py IncidentCreate) use. Accept
        # either so {{service_url}} resolves regardless of incident source.
        service_url = alert_ctx.get("service_url") or alert_ctx.get("check_url") or ""
        service_port = alert_ctx.get("service_port") or alert_ctx.get("port") or ""
        ctx_process_name = alert_ctx.get("process_name", "")

        # Always inject as named subs so {service_url} in command templates resolves.
        if service_url and not injected.get("service_url"):
            injected["service_url"] = str(service_url)
        if service_port and not injected.get("service_port"):
            injected["service_port"] = str(service_port)

        # Fall back for process_name when it's empty or still a bare placeholder.
        if ctx_process_name:
            proc_val = injected.get("process_name", "")
            if not proc_val or ToolRegistryAgent._PLACEHOLDER_RE.match(str(proc_val).strip()):
                injected["process_name"] = str(ctx_process_name)

        # Also fix the 'url' arg when it's a known placeholder pattern.
        if service_url:
            url_val = injected.get("url", "")
            _is_placeholder = (
                not url_val
                or "service-url" in url_val        # literal template text used by AI
                or url_val.strip() in ("{service_url}", "{{service_url}}")  # unresolved placeholder —
                # double-brace is the mustache-style convention used by runbook authors/AI
                # generation everywhere else; _resolve_step_references' Pattern 3 only
                # resolves it when extra_context has a "service_url" key, which alert
                # payloads from watcher external checks never do (they use "check_url")
                or ("localhost" in url_val and "/health" in url_val)  # generic localhost health url
            )
            if _is_placeholder:
                injected["url"] = service_url

        return injected

    _TARGET_ALIAS_KEYS = {"target", "container", "container_name", "pod", "host", "resource_name", "service_name"}
    _PLACEHOLDER_RE = re.compile(r"^\{+\s*\w+\s*\}+$")

    @staticmethod
    def _fill_unresolved_target_aliases(step_args: Dict, target: str) -> Dict:
        """
        Last-resort fallback: if a "target identity" arg (target/container/container_name/
        pod/host/resource_name/service_name) is still a bare, unresolved placeholder after
        _resolve_step_references and _inject_incident_context have both run — e.g. a runbook
        step configured with target: "{{container_name}}" where no diagnostic step ever
        captured an output_capture variable literally named container_name — fill it with
        the run's actual target instead of letting the literal placeholder text reach the
        tool (which then fails with errors like "No such container: {container_name}").

        Real captured values (e.g. {{steps.1.container_name}} resolving via output_capture)
        always win — this only fires when the placeholder is still bare/unresolved.
        """
        if not target:
            return step_args
        filled = dict(step_args)
        for key, value in list(filled.items()):
            if (
                key in ToolRegistryAgent._TARGET_ALIAS_KEYS
                and isinstance(value, str)
                and ToolRegistryAgent._PLACEHOLDER_RE.match(value.strip())
            ):
                logger.info(
                    f"[CHAIN] Arg '{key}' was an unresolved placeholder ({value!r}) — "
                    f"falling back to run target '{target}'"
                )
                filled[key] = target
        return filled

    @staticmethod
    def _resolve_command(action_obj, adapter_mode: str) -> str:
        """
        Pick the best command for the detected adapter environment.

        Resolution order:
          1. command_variants[adapter_mode]   — exact env match
          2. command_variants["any"]           — explicit fallback in variants dict
          3. command (legacy / default field)  — backward-compat catch-all
        """
        variants = getattr(action_obj, "command_variants", None) or {}
        return (
            variants.get(adapter_mode)
            or variants.get("any")
            or getattr(action_obj, "command", None)
            or ""
        )

    @staticmethod
    def _execute_via_exec(
        command_template: str,
        proposal: Dict,
        target: str,
        watcher_base: str,
        action_name: str = "",
        adapter_mode: str = "docker",
        execution_mode: str = None,
    ) -> Dict:
        """
        Execute a shell command on the target via the watcher's Kill-API /exec endpoint.
        {param} placeholders in command_template are interpolated from proposal.
        The watcher's adapter (Docker/SSH/K8s/SSM) handles the actual transport.
        """
        import httpx, uuid
        exec_id = f"exec-{uuid.uuid4().hex[:8]}"

        # Build substitution dict: merge proposal args + common convenience keys.
        # {target}, {container}, {pod}, {host} are all aliases for the same runtime value
        # (the detected resource). {namespace} is watcher-injected for Kubernetes adapters.
        subs = {k: v for k, v in proposal.items() if isinstance(v, (str, int, float, bool))}
        subs.setdefault("target", target)
        subs.setdefault("container", target)       # Docker alias
        subs.setdefault("pod", target)             # K8s alias
        subs.setdefault("host", target)            # SSH alias
        subs.setdefault("resource_name", target)   # generic alias

        # Inject Kubernetes namespace from the watcher registration if available.
        # Falls back to "default" so kubectl commands never have a literal {namespace}.
        if "namespace" not in subs:
            _ns = "default"
            try:
                from agentic_os.db.database import SessionLocal
                from agentic_os.db.models import WatcherRegistrationModel
                _watcher_name = watcher_base.rstrip("/").split("/")[-1].split(":")[0]
                _db = SessionLocal()
                try:
                    _row = _db.query(WatcherRegistrationModel).filter_by(
                        watcher_name=_watcher_name
                    ).first()
                    if _row and getattr(_row, "targets", None):
                        # targets JSON may carry a k8s_namespace key
                        _targets = _row.targets if isinstance(_row.targets, dict) else {}
                        _ns = _targets.get("k8s_namespace", "default")
                finally:
                    _db.close()
            except Exception:
                pass
            subs["namespace"] = _ns

        # SafeDict: unknown keys (e.g. curl's %{http_code}, awk's {print $1}) are left
        # as-is instead of raising KeyError and falling back to the raw unsubstituted
        # template.  This means {timeout_sec}, {url}, etc. are always replaced even
        # when the command contains non-Python format specifiers.
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        subs_str = {k: str(v) for k, v in subs.items()}
        try:
            import re as _re
            # Shell ${var} and ${var:-default} use the same {..} delimiters as Python format_map.
            # The embedded colon/dash causes ValueError ("Sign not allowed in string format
            # specifier"). Double-brace them before each format_map call so they pass through
            # as literals: ${code:-000} -> ${{code:-000}} -> format_map -> ${code:-000}.
            # Must be re-applied before Pass 2 because Pass 1 consumes the double braces.
            def _protect_shell(s: str) -> str:
                return _re.sub(r'\$\{([^}]*)\}', r'${{' + r'\1' + r'}}', s)
            # An arg value can still hold an unresolved runbook-chaining placeholder
            # like "{{top_process_name}}" (e.g. its diagnostic source step returned no
            # data this run). Pass 1 inserts that value literally; without protection,
            # Pass 2's format_map would then interpret its "{{"/"}}" as Python's
            # brace-escaping syntax and silently collapse it to "{top_process_name}"
            # (single brace) — turning an obviously-unresolved placeholder into a
            # confusing one. Doubling each brace here makes Pass 2's escaping unwrap
            # back to the original "{{var}}" instead of stripping a layer.
            def _protect_chain_placeholders(s: str) -> str:
                return _re.sub(r'\{\{(\w+)\}\}', r'{{{{\1}}}}', s)
            # Pass 1: substitute command-template placeholders ({url}, {timeout_sec}, etc.)
            command = _protect_shell(command_template).format_map(_SafeDict(subs_str))
            # Pass 2: substitute runtime context vars that appear inside arg values.
            # e.g. url="http://{target}:8080/health" -> after pass 1 the literal
            # "{target}" is still in the command; pass 2 replaces it.
            command = _protect_chain_placeholders(command)
            command = _protect_shell(command).format_map(_SafeDict(subs_str))
        except (ValueError, AttributeError, IndexError, KeyError) as _fmt_err:
            # ValueError = malformed format string (e.g. lone '{')
            # AttributeError/IndexError = Go template {{.Names}} conflicts with Python format_map
            # KeyError = missing key not handled by _SafeDict (should not happen)
            logger.warning(f"[EXEC] Malformed command template for '{action_name}': {_fmt_err}. Running as-is.")
            command = command_template

        # Execution mode:
        #   "target": command is raw shell code to run inside the target (container, VM, etc.)
        #   "host":   command is run on the watcher host (includes docker restart, kubectl rollout, etc.)
        # If execution_mode not specified, use adapter-based default:
        #   "target" adapters (vcenter, aws_ssm, azure): always use "target" mode
        #   "host"   adapters (docker, ssh, kubernetes): default to "host" mode
        if execution_mode is None:
            _TARGET_MODE_ADAPTERS = {"vcenter", "aws_ssm", "azure"}
            execution_mode = "target" if adapter_mode in _TARGET_MODE_ADAPTERS else "host"
        payload = {"target": target, "command": command, "timeout": 30, "mode": execution_mode}
        watcher_url = f"{watcher_base}/exec"
        logger.info(f"[TOOL] {action_name or 'exec'} → {watcher_url} cmd={command!r} target={target!r}")

        try:
            response = httpx.post(watcher_url, json=payload, timeout=35.0)
            data = response.json()
            if data.get("success"):
                return {
                    "success":      True,
                    "execution_id": exec_id,
                    "command":      command,
                    "message":      data.get("message") or f"Ran: {command}",
                    "raw_output":   data.get("raw_output") or data.get("stdout"),
                    "parameters":   subs,
                }
            else:
                return {
                    "success":      False,
                    "execution_id": exec_id,
                    "command":      command,
                    "error":        data.get("error") or data.get("stderr") or "Command failed",
                    "raw_output":   data.get("raw_output") or data.get("stdout"),
                    "parameters":   subs,
                }
        except Exception as exc:
            logger.error(f"[TOOL] exec failed ({action_name}): {exc}")
            return {
                "success":      False,
                "execution_id": exec_id,
                "command":      command,
                "error":        str(exc),
            }

    @staticmethod
    async def _execute_action(
        action: str,
        proposal: Dict,
        watcher_base: str = "http://watcher_brain:8080",
        adapter_mode: str = "docker",
    ) -> Dict:
        """
        Execute a tool action.

        process_kill → dedicated Kill-API /kill (existing, validated, real)
        any action with a command (or command_variants) → Kill-API /exec (real)
        no command → simulate (backward compat for abstract/platform actions)
        """
        import uuid

        if action == "process_kill":
            return ToolRegistryAgent._execute_process_kill(proposal, watcher_base)

        # Look up action from the approved_actions catalog
        action_obj = None
        try:
            from agentic_os.db.database import SessionLocal
            from agentic_os.db.repositories import ApprovedActionRepository
            from agentic_os.db.models import ApprovedActionModel

            db = SessionLocal()
            try:
                repo = ApprovedActionRepository(db)
                tool_name_normalized = action.lower().replace(" ", "_").replace("(", "").replace(")", "")
                action_obj = repo.get_by_tool_name(tool_name_normalized)
                if not action_obj:
                    all_actions = db.query(ApprovedActionModel).all()
                    action_obj = next((a for a in all_actions if a.name == action), None)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[TOOL] DB lookup for '{action}' failed: {e}")

        if action_obj and not action_obj.enabled:
            return {"success": False, "error": f"Action '{action}' is disabled"}

        if action_obj:
            command = ToolRegistryAgent._resolve_command(action_obj, adapter_mode)
            if command:
                # Real execution via /exec → adapter transport
                target = proposal.get("target", "")
                # Use execution_mode from action definition, fall back to adapter-based logic
                execution_mode = getattr(action_obj, "execution_mode", None) or (
                    "target" if adapter_mode in {"vcenter", "aws_ssm", "azure"} else "host"
                )
                return ToolRegistryAgent._execute_via_exec(
                    command_template=command,
                    proposal=proposal,
                    target=target,
                    watcher_base=watcher_base,
                    action_name=action_obj.name,
                    adapter_mode=adapter_mode,
                    execution_mode=execution_mode,
                )
            # Action is known but has no command — simulate (e.g. abstract actions like kubectl_scale
            # that don't yet have a command wired; avoids silent failure)
            logger.info(f"[TOOL] '{action}' has no command defined — simulating (add command to enable real execution)")
            return {
                "success":      True,
                "execution_id": f"exec-{uuid.uuid4().hex[:8]}",
                "message":      f"Simulated: {action_obj.name} — add a command to run for real",
                "simulated":    True,
            }

        # Unknown action — hardcoded backward-compat fallback
        execution_map = {
            "escalate": {
                "success": True,
                "execution_id": f"exec-{uuid.uuid4().hex[:8]}",
                "message": f"Escalated incident to on-call team for {proposal.get('target')}",
            },
        }
        return execution_map.get(
            action,
            {
                "success": False,
                "error": f"Unknown action: {action}",
            },
        )

    @staticmethod
    def _validate_process_rules(process_name: str) -> tuple:
        """
        Check the approved_actions catalog to see if process_name is permitted
        for the process_kill action.  Returns (allowed: bool, reason: str).
        """
        import re
        try:
            from agentic_os.db.database import SessionLocal
            from agentic_os.db.repositories import ApprovedActionRepository
            db = SessionLocal()
            try:
                action = ApprovedActionRepository(db).get_by_tool_name("process_kill")
                if not action or not action.process_rules:
                    return True, "No process rules configured"
                rules = sorted(action.process_rules, key=lambda r: r.get("priority", 99))
                for rule in rules:
                    try:
                        if re.match(rule["pattern"], process_name):
                            allowed = rule.get("allow", False)
                            desc    = rule.get("description", rule["pattern"])
                            return allowed, (
                                f"{'Allowed' if allowed else 'DENIED'} by rule "
                                f"(priority {rule.get('priority','?')}): {desc}"
                            )
                    except re.error:
                        continue
                return False, f"No allow rule matched '{process_name}' — denied by whitelist policy"
            finally:
                db.close()
        except Exception as exc:
            logger.warning(f"[TOOL] process rule check failed: {exc} — allowing by default")
            return True, "Rule check error — defaulting to allow"

    @staticmethod
    def _execute_process_kill(proposal: Dict, watcher_base: str = "http://watcher_brain:8080") -> Dict:
        """
        Real implementation: delegate process kill to the detecting watcher via its Kill-API
        (POST <watcher_base>/kill).  The watcher container has the Docker socket mounted
        and docker.io installed, so it can run 'docker exec pkill'.
        Always validates the process name against the approved_actions process rules first.
        """
        import httpx, uuid

        process_name = proposal.get("process_name", "")
        container    = proposal.get("target", "sentinel_senses")
        signal       = proposal.get("signal", "SIGKILL")
        exec_id      = f"exec-kill-{uuid.uuid4().hex[:8]}"

        if not process_name:
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": proposal,
                "error": "process_kill action missing 'process_name'",
            }

        # ── Validate against approved process rules ──────────────────────────
        allowed, rule_reason = ToolRegistryAgent._validate_process_rules(process_name)
        logger.info(f"[TOOL] process_kill rule check for '{process_name}': {rule_reason}")
        if not allowed:
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": {"process_name": process_name, "container": container, "signal": signal},
                "error": f"Process '{process_name}' blocked by policy — {rule_reason}",
            }

        watcher_url = f"{watcher_base}/kill"
        payload = {"process_name": process_name, "container": container, "signal": signal}
        logger.info(f"[TOOL] process_kill → {watcher_url} payload={payload}")

        # Timeout budget: watcher's subprocess runs in an executor with 12 s;
        # add headroom for Docker overhead.  30 s is generous but prevents the
        # event-loop-blocking race that caused INC0019's false failure.
        try:
            response = httpx.post(watcher_url, json=payload, timeout=30.0)
            data = response.json()
            if data.get("success"):
                return {
                    "success": True,
                    "execution_id": exec_id,
                    "message": data.get("message", f"Killed '{process_name}' in '{container}'"),
                    "command": data.get("command", ""),      # actual pkill command string
                    "raw_output": data.get("raw_output"),    # captured stdout/stderr from pkill
                    "parameters": {"process_name": process_name, "container": container, "signal": signal},
                }
            else:
                return {
                    "success": False,
                    "execution_id": exec_id,
                    "parameters": {"process_name": process_name, "container": container, "signal": signal},
                    "command": data.get("command", ""),
                    "raw_output": data.get("raw_output"),
                    "error": data.get("error", "Kill-API returned failure"),
                }
        except httpx.ConnectError:
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": {"process_name": process_name, "container": container, "signal": signal},
                "error": f"Cannot reach {watcher_base} — is the watcher container running?",
            }
        except httpx.TimeoutException:
            # The kill-API timed out — but the kill command may have still executed
            # (e.g. Docker was slow to respond but pkill did run).  Verify the
            # process state before reporting failure: if it's gone, that's a success.
            logger.warning(f"[TOOL] process_kill timed out — verifying whether '{process_name}' is still running")
            try:
                check = httpx.post(
                    f"{watcher_base}/check-process",
                    json={"process_name": process_name, "container": container},
                    timeout=8.0,
                )
                if check.status_code == 200 and not check.json().get("running", True):
                    msg = (
                        f"Sent {signal} to '{process_name}' in '{container}' "
                        f"(kill-API timeout, but process confirmed gone)"
                    )
                    logger.info(f"[TOOL] ✓ {msg}")
                    return {
                        "success": True,
                        "execution_id": exec_id,
                        "message": msg,
                        "command": "",
                        "raw_output": "verified via check-process after timeout",
                        "parameters": {"process_name": process_name, "container": container, "signal": signal},
                    }
            except Exception as ve:
                logger.debug(f"[TOOL] post-timeout verify failed: {ve}")
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": {"process_name": process_name, "container": container, "signal": signal},
                "error": "Timed out waiting for kill-API response from watcher_brain",
            }
        except Exception as exc:
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": {"process_name": process_name, "container": container, "signal": signal},
                "error": f"Unexpected error calling kill-API: {exc}",
            }

    # Patterns matching credential-shaped substrings that diagnostic tools can
    # surface verbatim (most notably `docker inspect`'s Args/Env, which echoes back
    # whatever was passed on a container's command line or environment — e.g. a
    # `--basic_auth=admin:REALPASSWORD` flag). Redacted wherever raw tool output
    # reaches the UI or gets persisted (runbook_step_outcomes.error_message,
    # workflow execution history), not just display — this is a leak, not a
    # cosmetic concern. Root cause (secrets passed via CLI args/env at all) is a
    # deployment-config fix; this is the defense-in-depth backstop regardless of
    # where the secret-in-config anti-pattern originates.
    # Each entry is (pattern, replacement) — explicit per-pattern, not inferred from
    # group count, since "how much of the match to keep vs. redact" differs per
    # pattern (e.g. the AWS key pattern has a capture group but the *entire* match
    # is the secret, unlike the others where group 1 is a prefix to preserve).
    _SECRET_PATTERNS = [
        # key=value / --flag=value — value excludes trailing quote/punctuation so we
        # don't eat the closing `"` of a JSON string the secret happens to sit inside.
        (re.compile(r'(--?[\w-]*(?:basic[_-]?auth|password|passwd|pwd|secret|token|api[_-]?key|auth)[\w-]*[=:]\s*)[^\s"\'),\]}]+', re.IGNORECASE), r'\1***REDACTED***'),
        # "Authorization: Bearer xyz" / "Authorization: xyz" — redact everything to
        # end of line, not just the first token (a bare \S+ left "xyz" exposed after
        # a scheme word like "Bearer").
        (re.compile(r'(Authorization:\s*).+$', re.IGNORECASE | re.MULTILINE), r'\1***REDACTED***'),
        # userinfo password in URLs, e.g. redis://:pass@host or redis://user:pass@host
        # — username segment is optional (redis://:pass@host has an empty username).
        (re.compile(r'(://[^:/\s]*:)[^@\s]+(@)'), r'\1***REDACTED***\2'),
        (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '***REDACTED***'),          # AWS access key id — whole match is the secret
    ]

    @staticmethod
    def _redact_secrets(text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        redacted = text
        for pattern, replacement in ToolRegistryAgent._SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    @staticmethod
    def _redact_secrets_from_result(result: Dict) -> Dict:
        """Apply _redact_secrets to every string field of a tool result dict
        (top-level and one level into 'structured'/'parameters') before it's
        returned to any caller — the single choke point _execute_tool routes
        through, so every diagnostic/action tool is covered automatically."""
        if not isinstance(result, dict):
            return result
        redacted = dict(result)
        for key in ("raw_output", "output", "error", "message", "command", "stderr", "stdout"):
            if isinstance(redacted.get(key), str):
                redacted[key] = ToolRegistryAgent._redact_secrets(redacted[key])
        for nested_key in ("structured", "parameters"):
            nested = redacted.get(nested_key)
            if isinstance(nested, dict):
                redacted[nested_key] = {
                    k: (ToolRegistryAgent._redact_secrets(v) if isinstance(v, str) else v)
                    for k, v in nested.items()
                }
        return redacted

    @staticmethod
    def _execute_tool(tool_name: str, args: Dict, container: str = "sentinel_senses",
                      watcher_base: str = "http://watcher_brain:8080",
                      adapter_mode: str = "docker") -> Dict:
        """Thin wrapper around _execute_tool_impl that redacts credential-shaped
        substrings from the result before any caller sees it — see
        _redact_secrets_from_result / _SECRET_PATTERNS above."""
        result = ToolRegistryAgent._execute_tool_impl(tool_name, args, container, watcher_base, adapter_mode)
        return ToolRegistryAgent._redact_secrets_from_result(result)

    @staticmethod
    def _resolve_notify_team(team_name: Optional[str], db):
        """Look up a NotificationTeamModel by case-insensitive name. Returns
        None (→ caller falls back to global defaults) if no name was given,
        no team matches, or the matched team is disabled."""
        if not team_name:
            return None
        from agentic_os.db.models import NotificationTeamModel
        team = db.query(NotificationTeamModel).filter(
            NotificationTeamModel.name.ilike(team_name)
        ).first()
        if not team:
            logger.warning(f"[NOTIFY] team '{team_name}' not found — using default channels")
            return None
        if not team.enabled:
            logger.warning(f"[NOTIFY] team '{team_name}' is disabled — using default channels")
            return None
        return team

    @staticmethod
    def _execute_notify_action(action: str, args: Dict, target: str, exec_id: str) -> Dict:
        """Versatile notification dispatch shared by notify/alert_escalate/alert_update/send_alert.

        action: "escalate" | "acknowledge" | "resolve" | "message"

        Resolves a team by exact name (args["team"], explicit only — no CMDB
        lookup) and fans out to whichever of that team's configured channels
        make sense for the action, falling back to the global PagerDuty/Slack/
        SMTP defaults when no team is given or the named team isn't found or
        is disabled. Team channels replace, not add to, the default set, so a
        resolved team is never double-notified through the global defaults.
        """
        from agentic_os.db.database import SessionLocal
        from agentic_os.api.routes.connectors import get_pagerduty_client
        from agentic_os.services.notifications import _post_slack
        from agentic_os.services.email_service import EmailService
        from agentic_os.connectors.pagerduty.events_client import PagerDutyEventsClient
        from agentic_os.connectors.webhook.outbound_client import OutboundWebhookClient, OutboundWebhookError

        team_name = args.get("team") or args.get("service") or args.get("service_name")
        severity  = (args.get("severity") or ("high" if action == "escalate" else "warning")).lower()
        raw_message = args.get("message") or f"{action} for {target} (severity={severity})"

        # Every outbound notification must identify which incident it's about — a bare
        # "Service is now responsive" with no number/title is useless to whoever reads
        # it. Sequence matches the rest of the platform's incident notifications:
        # number, title, runbook, message — never "Automated Runbook" leading, so this
        # reads consistently next to the regular non-runbook incident Slack/email alerts.
        # Degrades by joining only whichever pieces are present, rather than silently
        # dropping to a bare message (e.g. a Test Run in the editor has no real incident
        # behind it at all, and may have no runbook_name either).
        incident_number = args.get("incident_number") or ""
        incident_title  = args.get("incident_title") or ""
        runbook_name    = args.get("runbook_name") or ""

        context_parts = [p for p in (incident_number, incident_title, runbook_name) if p]
        if context_parts:
            message = " - ".join(context_parts + [raw_message])
        else:
            message = f"[no incident context] - {raw_message}"

        db = SessionLocal()
        try:
            team = ToolRegistryAgent._resolve_notify_team(team_name, db)

            if team:
                pd_client    = PagerDutyEventsClient(team.pagerduty_routing_key) if team.pagerduty_routing_key else None
                slack_target = team.slack_channel if team.slack_channel else None
                use_slack    = bool(team.slack_channel)
                email_recips = [a.strip() for a in team.email_recipients.split(",") if a.strip()] if team.email_recipients else None
                webhook      = OutboundWebhookClient(team.webhook_url, team.webhook_secret) if team.webhook_url else None
                source       = f"team '{team.name}'"
            else:
                pd_client    = get_pagerduty_client(db)
                slack_target = None   # use configured slack.default_channel
                use_slack    = True
                email_svc    = EmailService(db)
                email_recips = email_svc.get_recipients() if email_svc.is_configured() else None
                webhook      = None   # no default outbound-webhook concept
                source       = "default channels" if not team_name else f"default channels (team '{team_name}' not found/disabled)"

            # Which channel types make sense for this action.
            try_pagerduty = action in ("escalate", "acknowledge", "resolve")
            try_slack     = action in ("escalate", "message") and use_slack
            try_email     = action in ("escalate", "message") and bool(email_recips)
            try_webhook   = action in ("escalate", "message") and bool(webhook)

            successes: list[str] = []
            failures:  list[str] = []
            attempted = False

            if try_pagerduty and pd_client:
                attempted = True
                try:
                    if action == "escalate":
                        dedup_key = args.get("dedup_key") or f"axiometica-{exec_id}"
                        result = pd_client.trigger_sync(
                            summary=message, severity=severity, dedup_key=dedup_key,
                            custom_details={
                                "team": team_name or "default", "target": target,
                                "incident_number": incident_number or None, "incident_title": incident_title or None,
                                "runbook_name": runbook_name or None,
                            },
                        )
                        successes.append(f"PagerDuty (dedup_key={result.get('dedup_key', dedup_key)})")
                    else:
                        alert_id = args.get("dedup_key") or args.get("alert_id")
                        if not alert_id:
                            failures.append("PagerDuty: acknowledge/resolve requires dedup_key (or alert_id)")
                        else:
                            result = (pd_client.resolve_sync(dedup_key=alert_id) if action == "resolve"
                                      else pd_client.acknowledge_sync(dedup_key=alert_id))
                            successes.append(f"PagerDuty incident {alert_id} marked {action} (status={result.get('status')})")
                except Exception as exc:
                    logger.error(f"[NOTIFY] PagerDuty {action} failed: {exc}")
                    failures.append(f"PagerDuty: {exc}")
            elif try_pagerduty and action in ("acknowledge", "resolve"):
                attempted = True
                failures.append(f"PagerDuty: no PagerDuty channel resolved ({source}) — nothing to {action}")

            if try_slack:
                attempted = True
                try:
                    slack_errors: list = []
                    if _post_slack(message, channel=slack_target, error_out=slack_errors):
                        successes.append(f"Slack ({slack_target or 'default channel'})")
                    else:
                        failures.append(f"Slack: {slack_errors[0] if slack_errors else 'unknown error'}")
                except Exception as exc:
                    logger.error(f"[NOTIFY] Slack {action} failed: {exc}")
                    failures.append(f"Slack: {exc}")

            if try_email:
                attempted = True
                try:
                    subject = f"[{severity.upper()}] {target}: {action}"
                    if EmailService(db).send_incident_notification(email_recips, subject, message):
                        successes.append(f"Email ({', '.join(email_recips)})")
                    else:
                        failures.append("Email: send failed")
                except Exception as exc:
                    logger.error(f"[NOTIFY] Email {action} failed: {exc}")
                    failures.append(f"Email: {exc}")

            if try_webhook:
                attempted = True
                try:
                    webhook.send_sync({
                        "event": action, "message": message, "severity": severity,
                        "team": team_name or "default", "target": target,
                        "incident_number": incident_number or None, "incident_title": incident_title or None,
                                "runbook_name": runbook_name or None,
                    })
                    successes.append(f"Webhook ({webhook.url})")
                except OutboundWebhookError as exc:
                    logger.error(f"[NOTIFY] Webhook {action} failed: {exc}")
                    failures.append(f"Webhook: {exc}")
                except Exception as exc:
                    logger.error(f"[NOTIFY] Webhook {action} failed: {exc}")
                    failures.append(f"Webhook: {exc}")
        finally:
            db.close()

        cmd = f"notify --action={action} --team={team_name or 'default'}"
        if not attempted:
            msg = (f"No channel configured for {action} via {source} — "
                   f"set up a notification team or the relevant connector (PagerDuty/Slack/SMTP).")
            return {"success": False, "execution_id": exec_id, "command": cmd,
                    "raw_output": "", "message": msg, "output": msg}

        success = bool(successes)
        parts = [f"routed via {source}"]
        if successes:
            parts.append(f"{action} succeeded via {', '.join(successes)}")
        if failures:
            parts.append(f"failed: {'; '.join(failures)}")
        msg = " — ".join(parts)
        return {"success": success, "execution_id": exec_id, "command": cmd,
                "raw_output": msg, "message": msg, "output": msg}

    @staticmethod
    def _execute_alert_action(tool_key: str, args: Dict, target: str, exec_id: str) -> Dict:
        """Thin legacy-name adapter over _execute_notify_action.

        alert_escalate/alert_update/send_alert/notify all reduce to the same
        (action, args) call — kept as separate catalog tool names so existing
        seeded runbooks (backend/seeds/common_runbooks.sql) keep working
        unchanged; `notify` is the new primary tool exposing all four actions
        directly via an `action` arg.
        """
        if tool_key == "notify":
            action = (args.get("action") or "message").lower()
        elif tool_key == "alert_escalate":
            action = "escalate"
        elif tool_key == "send_alert":
            action = "message"
        elif tool_key == "alert_update":
            status = (args.get("status") or "resolved").lower()
            action = "acknowledge" if status == "acknowledged" else "resolve"
        else:
            action = "message"
        return ToolRegistryAgent._execute_notify_action(action, args, target, exec_id)

    @staticmethod
    def _execute_tool_impl(tool_name: str, args: Dict, container: str = "sentinel_senses",
                      watcher_base: str = "http://watcher_brain:8080",
                      adapter_mode: str = "docker") -> Dict:
        """
        Execute a tool via the detecting watcher's HTTP API (which has Docker access).
        Returns actual command output or error message.
        Special handling for process termination via Kill-API.
        """
        import httpx

        exec_id = f"exec-{uuid.uuid4().hex[:8]}"

        try:
            # Normalize tool name for command routing
            tool_key = tool_name.lower().replace(" ", "_").replace("(", "").replace(")", "")

            # Debug: log what we're checking
            logger.debug(f"[TOOL] tool_name={tool_name}, tool_key={tool_key}, action={args.get('action')}")

            # Special handling: process_kill tool → use Kill-API (real execution)
            if "process_kill" in tool_key or (tool_key == "kill" or tool_key.startswith("kill_process") or tool_key.endswith("_kill")):
                # Extract process_name from args, handling both direct and context-based references
                process_name = args.get("process_name", "")
                if not process_name and "process_name_from_context" in args:
                    # Will be substituted by the calling code, but if it wasn't, we need it
                    logger.warning(f"[TOOL] process_kill called without resolved process_name, args={args}")
                    return {
                        "success": False,
                        "execution_id": exec_id,
                        "error": "process_name not resolved from context",
                    }

                signal = args.get("signal", "SIGKILL")
                logger.info(f"[TOOL] Detected process_kill tool, using Kill-API for process={process_name}, signal={signal}")
                return ToolRegistryAgent._execute_process_kill({
                    "process_name": process_name,
                    "target": container,
                    "signal": signal,
                }, watcher_base)

            # Special handling: Process Detail with terminate action → use Kill-API
            if "process_detail" in tool_key and args.get("action") == "terminate":
                process_name = args.get("process", "")
                signal = args.get("signal", "SIGTERM")
                logger.info(f"[TOOL] Detected process termination in {tool_name}, using Kill-API directly for process={process_name}")
                return ToolRegistryAgent._execute_process_kill({
                    "process_name": process_name,
                    "target": container,
                    "signal": signal,
                }, watcher_base)

            # Special handling: notify / alert_escalate / alert_update / send_alert →
            # route to whichever outbound notification channel is configured (a named
            # notification team if args["team"] resolves to one, else the global
            # PagerDuty/Slack/SMTP defaults). All four are catalog entries with no
            # command/command_variants (command=NULL — they're not shell actions), so
            # without this branch they fall through to the "no command for adapter"
            # skip below and silently report success without notifying anyone.
            if tool_key in ToolRegistryAgent._NOTIFY_TOOL_NAMES:
                logger.info(f"[TOOL] Detected {tool_key}, routing to outbound notification channel")
                return ToolRegistryAgent._execute_alert_action(tool_key, args, container, exec_id)

            # ── Command variant resolution → real /exec ───────────────────────
            # Look up the action in the approved_actions catalog.
            # If it has a command (or a command_variants entry for this adapter),
            # route to the watcher's /exec endpoint for real execution.
            #
            # ── Phase 1: DB catalog lookup (isolated try — DB errors only) ──────
            # All attribute access on _action_obj (including JSON columns like
            # command_variants) MUST happen while the session is still open to
            # avoid SQLAlchemy DetachedInstanceError on lazy-loaded columns.
            # CRITICAL: _execute_via_exec must NOT be inside this try block —
            # httpx.ConnectError from the execution would otherwise be caught
            # by the lookup except and silently converted to "not in catalog".
            _resolved_cmd       = None
            _action_name        = None
            _action_found       = False
            _action_enabled     = False
            _action_exec_mode   = None   # 'host' or 'target' — extracted before session closes
            _param_defaults: dict = {}   # catalog default values for optional params
            _action_output_fields: list = []   # schema-driven output-extraction rules from the catalog
            try:
                from agentic_os.db.database import SessionLocal
                from agentic_os.db.repositories import ApprovedActionRepository
                from agentic_os.db.models import ApprovedActionModel

                _tool_key_norm = tool_key.replace("-", "_")
                _db = SessionLocal()
                try:
                    _repo = ApprovedActionRepository(_db)
                    _action_obj = _repo.get_by_tool_name(_tool_key_norm)
                    if not _action_obj:
                        _all = _db.query(ApprovedActionModel).all()
                        _action_obj = next(
                            (a for a in _all if a.tool_name == _tool_key_norm or a.name == tool_name),
                            None,
                        )

                    if _action_obj:
                        _action_found   = True
                        _action_enabled = bool(_action_obj.enabled)
                        _action_name    = _action_obj.name
                        _action_exec_mode = getattr(_action_obj, "execution_mode", None)
                        _action_output_fields = getattr(_action_obj, "output_fields", None) or []
                        # Extract parameter defaults while session is open.
                        # These fill in missing optional args (e.g. pattern="." for get_logs)
                        # so command templates never contain un-substituted {placeholders}.
                        for _p in (_action_obj.parameters or []):
                            _pname = _p.get("name", "")
                            _pdef  = _p.get("default")
                            if _pname and _pdef is not None:
                                _param_defaults[_pname] = str(_pdef)
                        if _action_enabled:
                            _resolved_cmd = ToolRegistryAgent._resolve_command(_action_obj, adapter_mode)
                finally:
                    _db.close()

            except Exception as _lookup_err:
                logger.warning(f"[TOOL] Catalog lookup failed for '{tool_name}': {_lookup_err}", exc_info=True)

            # Apply catalog parameter defaults for any keys missing from the step args.
            # Step args always win; defaults only fill gaps so templates substitute cleanly.
            if _param_defaults:
                _enriched_args = {**_param_defaults, **args}   # defaults first, step args override
            else:
                _enriched_args = args

            # ── Phase 2: Execution — outside lookup try so httpx errors propagate ──
            # ConnectError / TimeoutException now reach the outer except handlers below.
            if _action_found and not _action_enabled:
                logger.warning(f"[TOOL] '{tool_name}' is disabled in the approved_actions catalog")
                return {
                    "success": False,
                    "execution_id": exec_id,
                    "error": f"Tool '{tool_name}' is disabled. Enable it via Approved Actions.",
                    "parameters": args,
                }

            if _action_found and _action_enabled and not _resolved_cmd:
                logger.info(f"[TOOL] '{tool_name}' has no command for adapter '{adapter_mode}' — skipping")
                return {
                    "success": True,
                    "execution_id": exec_id,
                    "stdout": f"[SKIPPED] Tool '{tool_name}' has no command for adapter '{adapter_mode}'.",
                    "stderr": "",
                    "returncode": 0,
                    "note": "no_command_for_adapter",
                }

            if _action_found and _action_enabled and _resolved_cmd:
                logger.info(f"[TOOL] '{tool_name}' → /exec (adapter={adapter_mode}, cmd={_resolved_cmd!r})")
                # Use execution_mode from action definition, fall back to adapter-based logic
                _exec_mode = _action_exec_mode or (
                    "target" if adapter_mode in {"vcenter", "aws_ssm", "azure"} else "host"
                )
                _result = ToolRegistryAgent._execute_via_exec(
                    command_template=_resolved_cmd,
                    proposal=_enriched_args,   # includes catalog param defaults
                    target=container,
                    watcher_base=watcher_base,
                    action_name=_action_name or tool_name,
                    adapter_mode=adapter_mode,
                    execution_mode=_exec_mode,
                )
                # Parse raw text output into a structured dict so downstream steps
                # can reference captured values via output_capture / run_if conditions.
                if _result.get("success") and _result.get("raw_output"):
                    _parsed = ToolRegistryAgent._parse_tool_output(tool_key, _result["raw_output"], _action_output_fields)
                    if _parsed:
                        _result["structured"] = _parsed
                        logger.info(f"[OUTPUT] '{tool_name}' structured: {_parsed}")
                return _result

            # Tool not found in catalog at all.
            logger.warning(f"[TOOL] '{tool_name}' not found in approved_actions catalog (adapter={adapter_mode})")
            return {
                "success": False,
                "execution_id": exec_id,
                "error": (
                    f"Tool '{tool_name}' is not in the approved_actions catalog. "
                    f"Add it via Approved Actions or update the runbook step to use an existing tool."
                ),
                "parameters": args,
            }

        except httpx.ConnectError as e:
            # Watcher unreachable — fail explicitly rather than pretending to succeed.
            # Previously returned success=True which caused the incident timeline to show
            # a green checkmark when nothing was actually executed.
            logger.warning(f"[TOOL] Cannot reach watcher at {watcher_base} for '{tool_name}': {e}")
            return {
                "success": False,
                "execution_id": exec_id,
                "error": (
                    f"Watcher unreachable at {watcher_base}. "
                    f"Verify the watcher container is running and the Kill-API port (8080) is accessible."
                ),
                "parameters": args,
            }
        except httpx.TimeoutException as e:
            logger.warning(f"[TOOL] Watcher timed out for {tool_name}: {e}")
            return {
                "success": False,
                "execution_id": exec_id,
                "parameters": args,  # Include the actual parameters that were used
                "error": f"Tool execution timed out on watcher_brain",
            }
        except Exception as exc:
            logger.error(f"[TOOL] Exception executing {tool_name} with args {args}: {type(exc).__name__}: {exc}", exc_info=True)
            # Fallback to simulated results for now
            return {
                "success": True,
                "execution_id": exec_id,
                "message": f"Executed {tool_name} (simulated - {type(exc).__name__})",
                "parameters": args,  # Include the actual parameters that were used
                "output": f"Error details: {str(exc)}",
            }

    @staticmethod
    def _translate_editor_steps(steps: list) -> dict:
        """
        Translate the new runbook editor's unified "steps" array into the legacy
        {diagnostics, actions, verification, incident_updates} dict the sequential
        (non-graph-walk) executor expects.

        Editor step types and their mapping:
          start / end / decision → skipped (not executable; routing lives in run_if)
          diagnostic             → diagnostics list
          action / notify        → actions list
          verification           → verification list
          incident_update        → incident_updates list (run after verification —
                                    see the run() construction of steps_to_run)

        Key differences handled:
          - Editor uses "args" (dict);  executor uses "args_json" — both accepted
          - Editor step IDs are strings ("step_1");  order is derived from position
          - output_capture is preserved for diagnostic/verification steps
          - run_if conditions on action/verification steps are preserved verbatim
          - verification steps keep their "tool" — a verification node that carries
            its own tool re-runs it for a fresh post-remediation measurement (see
            the verification branch in run()); only tool-less verification steps
            (checking a value an earlier diagnostic already captured) have none.
        """
        _SKIP_TYPES = {"start", "end", "decision"}
        diagnostics:      list = []
        actions:          list = []
        verification:     list = []
        incident_updates: list = []

        for pos, step in enumerate(steps, 1):
            step_type = (step.get("type") or "").lower()
            if step_type in _SKIP_TYPES:
                continue

            # Base fields common to all executable steps
            base: dict = {
                "id":          step.get("id", f"step_{pos}"),
                "name":        step.get("name", ""),
                "description": step.get("description", ""),
                "tool":        step.get("tool", ""),
                # Accept both "args" (editor) and "args_json" (legacy DB) — executor
                # reads args_json first, falls back to args, so either works.
                "args_json":   step.get("args") or step.get("args_json") or {},
                "run_if":      step.get("run_if", ""),
                "order":       pos,
                # Step failure policy — read by the real incident workflow executor.
                # "abort" (default) stops the runbook on failure; "continue" keeps going.
                # Preserved here so editor-authored runbooks behave as intended.
                "on_failure":  step.get("on_failure") or "abort",
            }

            if step_type == "diagnostic":
                base["output_capture"] = step.get("output_capture") or step.get("outputCapture") or {}
                diagnostics.append(base)

            elif step_type in ("action", "notify", "notification"):
                actions.append(base)

            elif step_type == "verification":
                base.update({
                    "metric": step.get("metric", ""),
                    "check":  step.get("check", ""),
                    "value":  step.get("value", ""),
                    "output_capture": step.get("output_capture") or step.get("outputCapture") or {},
                })
                verification.append(base)

            elif step_type == "incident_update":
                base["state"] = step.get("state", "resolved")
                incident_updates.append(base)

        logger.info(
            f"[TRANSLATE] Editor steps → {len(diagnostics)} diagnostic, {len(actions)} action, "
            f"{len(verification)} verification, {len(incident_updates)} incident_update"
        )
        return {
            "diagnostics": diagnostics, "actions": actions,
            "verification": verification, "incident_updates": incident_updates,
        }

    @staticmethod
    def _walk_graph(steps: list, edges: list, step_outputs: dict):
        """
        Graph-aware traversal of a visual-editor runbook node graph.

        Yields (step_dict, step_type_raw) for every executable node in graph order.
        step_type_raw is the raw node type string from the editor: "diagnostic",
        "action", "notify", "notification", or "verification".

        Decision nodes are yielded as step_type_raw="decision" with the step_dict
        augmented by "_condition", "_decision_result", "_decision_branch", and
        "_next_node" keys.  The condition is evaluated on-the-fly against
        step_outputs so the correct branch is taken based on actual tool output.

        step_outputs is read by reference — the caller populates it as steps
        execute so conditions referencing earlier step outputs resolve correctly.
        """
        step_by_id = {s.get("id"): s for s in steps}
        adj: dict = {}
        for e in edges:
            src    = e.get("source") or ""
            tgt    = e.get("target") or ""
            handle = e.get("sourceHandle") or "default"
            adj.setdefault(src, {})[handle] = tgt

        current = "start"
        visited: set = set()

        while current and current not in visited:
            visited.add(current)
            if current == "end":
                break

            step = step_by_id.get(current)
            nexts = adj.get(current, {})
            next_default = nexts.get("default") or next(iter(nexts.values()), None)

            if not step:
                current = next_default
                continue

            step_type_raw = (step.get("type") or "").lower()

            if step_type_raw in ("start", "end"):
                current = next_default
                continue

            if step_type_raw == "decision":
                condition = (step.get("condition") or "").strip()
                result    = ToolRegistryAgent._evaluate_condition(condition, step_outputs) if condition else True
                branch    = "true" if result else "false"
                next_node = nexts.get(branch) or next_default
                yield {
                    **step,
                    "_condition":       condition,
                    "_decision_result": result,
                    "_decision_branch": branch,
                    "_next_node":       next_node or "end",
                }, "decision"
                current = next_node
                continue

            yield step, step_type_raw
            current = next_default

    @staticmethod
    def _extract_output_fields(out: str, output_fields: list) -> dict:
        """
        Generic, schema-driven output extraction — replaces a hardcoded per-tool parser
        with rules carried on the tool's own catalog definition (approved_actions.output_fields).

        Each rule: {"field": str, "kind": "regex"|"jsonpath", "pattern": str, "type": "boolean"|"integer"|"float"|"string"}
          - regex + boolean: presence-based — True if pattern matches anywhere, else False.
          - regex + other types: first capture group (or whole match if no group), cast to type.
          - jsonpath: dotted/bracket-index path (e.g. "$[0].State.Health.Status") walked over json.loads(out).
        A rule that fails to match/resolve is simply omitted from the result (except presence-based booleans,
        which resolve to False on no match).
        """
        import re
        import json

        def _cast(raw, type_):
            if raw is None:
                return None
            try:
                if type_ == "integer":
                    return int(raw)
                if type_ == "float":
                    return float(raw)
                if type_ == "boolean":
                    if isinstance(raw, bool):
                        return raw
                    return str(raw).strip().lower() in ("true", "1", "yes")
                return str(raw)
            except (TypeError, ValueError):
                return None

        def _jsonpath(path: str):
            try:
                data = json.loads(out)
            except (ValueError, TypeError):
                return None
            tokens = re.findall(r'\[(\d+)\]|\.?([^.\[\]]+)', path.lstrip('$'))
            cur = data
            for idx, key in tokens:
                if cur is None:
                    return None
                if idx != "":
                    cur = cur[int(idx)] if isinstance(cur, list) and int(idx) < len(cur) else None
                elif key != "":
                    cur = cur.get(key) if isinstance(cur, dict) else None
            return cur

        result = {}
        for rule in output_fields or []:
            field   = rule.get("field")
            kind    = rule.get("kind")
            pattern = rule.get("pattern", "")
            type_   = rule.get("type", "string")
            if not field:
                continue

            if kind == "regex":
                if type_ == "boolean":
                    result[field] = bool(re.search(pattern, out))
                else:
                    m = re.search(pattern, out)
                    if m:
                        raw = m.group(1) if m.groups() else m.group(0)
                        casted = _cast(raw, type_)
                        if casted is not None:
                            result[field] = casted
            elif kind == "jsonpath":
                raw = _jsonpath(pattern)
                if raw is not None:
                    casted = _cast(raw, type_)
                    if casted is not None:
                        result[field] = casted

        return result

    @staticmethod
    def _parse_tool_output(tool_key: str, raw_output: str, output_fields: list = None) -> dict:
        """
        Parse raw shell stdout into a structured dict for downstream step chaining.

        If the tool's catalog definition carries `output_fields` (schema-driven extraction
        rules), those are used exclusively via `_extract_output_fields`. Otherwise this falls
        back to the legacy hardcoded per-tool parsers below, preserved for tools not yet
        migrated to the new mechanism.

        Returns an empty dict if the output can't be parsed or the tool is unknown.
        """
        import re

        out = (raw_output or "").strip()
        if not out:
            return {}

        if output_fields:
            return ToolRegistryAgent._extract_output_fields(out, output_fields)

        # ── Disk usage ────────────────────────────────────────────────────────
        if tool_key in ("check_disk_usage", "host_disk_usage"):
            # df -h: "/dev/sda1   100G   87G   13G   87%   /"
            pct_match = re.search(r'(\d+)%', out)
            disk_percent = int(pct_match.group(1)) if pct_match else None
            # Avail column: Size Used Avail Use% — grab value before the percent
            avail_match = re.search(r'\S+\s+\S+\s+(\d+\.?\d*\S*)\s+\d+%', out)
            avail = avail_match.group(1) if avail_match else None
            result: dict = {}
            if disk_percent is not None:
                result["disk_percent"] = disk_percent
            if avail:
                result["available"] = avail
            return result

        # ── Memory ───────────────────────────────────────────────────────────
        if tool_key == "check_memory":
            def _to_gb(s: str) -> float | None:
                s = s.strip().upper()
                try:
                    if s.endswith('G'):   return round(float(s[:-1]), 2)
                    if s.endswith('M'):   return round(float(s[:-1]) / 1024, 2)
                    if s.endswith('K'):   return round(float(s[:-1]) / (1024 ** 2), 4)
                    if s.endswith('T'):   return round(float(s[:-1]) * 1024, 2)
                    return round(float(s), 2)
                except ValueError:
                    return None

            # free -h: "Mem:  7.7G  5.2G  1.0G  256M  1.5G  2.3G"
            match = re.search(
                r'Mem:\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\S+\s+\S+\s+(\S+))?', out
            )
            if match:
                total_gb  = _to_gb(match.group(1))
                used_gb   = _to_gb(match.group(2))
                avail_gb  = _to_gb(match.group(4) or match.group(3))
                if total_gb and used_gb and total_gb > 0:
                    return {
                        "mem_percent":      round(used_gb / total_gb * 100),
                        "mem_used_gb":      used_gb,
                        "mem_total_gb":     total_gb,
                        "mem_available_gb": avail_gb,
                    }

            # /proc/meminfo fallback: "MemTotal: 3799264 kB" etc.
            def _get_kb(key: str) -> int | None:
                m = re.search(rf'^{key}:\s+(\d+)\s+kB', out, re.MULTILINE | re.IGNORECASE)
                return int(m.group(1)) if m else None
            total_kb = _get_kb("MemTotal")
            avail_kb = _get_kb("MemAvailable")
            free_kb  = _get_kb("MemFree")
            if total_kb and total_kb > 0:
                used_kb  = total_kb - (avail_kb or free_kb or 0)
                total_gb = round(total_kb / (1024 ** 2), 2)
                used_gb  = round(used_kb  / (1024 ** 2), 2)
                avail_gb = round((avail_kb or 0) / (1024 ** 2), 2)
                return {
                    "mem_percent":      round(used_kb / total_kb * 100),
                    "mem_used_gb":      used_gb,
                    "mem_total_gb":     total_gb,
                    "mem_available_gb": avail_gb,
                }
            return {}

        # ── CPU ──────────────────────────────────────────────────────────────
        if tool_key == "check_cpu":
            # top: "%Cpu(s):  5.0 us,  2.1 sy,  0.0 ni, 92.4 id"
            m = re.search(r'%Cpu\(s\):\s+([\d.]+)\s+us,\s+([\d.]+)\s+sy', out)
            if m:
                user_pct = float(m.group(1))
                sys_pct  = float(m.group(2))
                return {
                    "cpu_percent":      round(user_pct + sys_pct, 1),
                    "cpu_user_percent": user_pct,
                    "cpu_sys_percent":  sys_pct,
                }
            # Compact format: "Cpu: 5.0% us"
            m2 = re.search(r'(\d+\.?\d*)[%\s]+us', out)
            if m2:
                return {"cpu_percent": float(m2.group(1))}
            return {}

        # ── HTTP health check ─────────────────────────────────────────────────
        if tool_key == "check_health_endpoint":
            # output format: http_code=200  (from approved_actions command)
            m = re.search(r'http_code=(\d{3})', out)
            if not m:
                m = re.search(r'(\d{3})', out)
            if m:
                code = int(m.group(1))
                reachable = bool(re.match(r'[1-4]\d\d', str(code)))
                return {"http_code": code, "reachable": reachable, "healthy": reachable}
            return {}

        # ── Ping / reachability ───────────────────────────────────────────────
        if tool_key == "ping_service":
            # curl -Is: first line "HTTP/1.1 200 OK"
            m = re.search(r'HTTP/[\d.]+\s+(\d{3})', out)
            if m:
                code = int(m.group(1))
                return {"http_code": code, "reachable": code < 500}
            return {"reachable": False}

        # ── Error rate ────────────────────────────────────────────────────────
        if tool_key == "get_error_rate":
            # grep -c returns a single integer
            m = re.search(r'^(\d+)$', out, re.MULTILINE)
            if m:
                count = int(m.group(1))
                return {"error_count": count, "has_errors": count > 0}
            return {}

        # ── Top processes ─────────────────────────────────────────────────────
        if tool_key in ("top_processes", "host_top_processes", "win_top_processes"):
            for line in out.split('\n'):
                cols = line.split()
                # GNU top/ps -f: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND (12 cols)
                if len(cols) >= 12 and cols[0].isdigit():
                    try:
                        return {
                            "top_process":     cols[11],
                            "top_process_pid": int(cols[0]),
                            "top_cpu_percent": float(cols[8]),
                            "top_mem_percent": float(cols[9]),
                        }
                    except (ValueError, IndexError):
                        pass
                # busybox `top -bn1`: PID PPID USER STAT VSZ %VSZ CPU %CPU COMMAND (9 cols)
                # Used by the catalog's default docker top_processes command — Alpine/slim
                # images ship busybox top, not the GNU ps/top format above.
                elif len(cols) >= 9 and cols[0].isdigit():
                    try:
                        return {
                            "top_process":     cols[8],
                            "top_process_pid": int(cols[0]),
                            "top_cpu_percent": float(cols[7].rstrip('%')),
                            "top_mem_percent": float(cols[5].rstrip('%')),
                        }
                    except (ValueError, IndexError):
                        pass
            return {}

        # ── Service status ────────────────────────────────────────────────────
        if tool_key in ("host_service_status", "win_service_status"):
            lower = out.lower()
            if "active (running)" in lower or "running" in lower:
                status = "running"
            elif "inactive (dead)" in lower or "stopped" in lower:
                status = "stopped"
            elif "failed" in lower:
                status = "failed"
            elif "activating" in lower or "starting" in lower:
                status = "starting"
            else:
                status = "unknown"
            return {"service_status": status, "service_running": status == "running"}

        # ── Prometheus / query_metrics ────────────────────────────────────────
        if tool_key == "query_metrics":
            # "metric_name{labels} 87.5 timestamp"
            m = re.search(r'^[^\s{#]+(?:\{[^}]*\})?\s+([\d.]+)', out, re.MULTILINE)
            if m:
                return {"metric_value": float(m.group(1))}
            return {}

        # ── Swap ──────────────────────────────────────────────────────────────
        if tool_key == "check_swap":
            # /proc/swaps: filename type size used priority
            m = re.search(r'\S+\s+\S+\s+(\d+)\s+(\d+)', out)
            if m:
                total_kb = int(m.group(1))
                used_kb  = int(m.group(2))
                if total_kb > 0:
                    return {
                        "swap_percent":  round(used_kb / total_kb * 100),
                        "swap_used_kb":  used_kb,
                        "swap_total_kb": total_kb,
                    }
            return {}

        # ── Queue depth ───────────────────────────────────────────────────────
        if tool_key == "check_queue_depth":
            m = re.search(r'^(\d+)$', out, re.MULTILINE)
            if m:
                return {"queue_depth": int(m.group(1))}
            return {}

        # NOTE: list_connections is handled later in this function (format-tolerant
        # ss/netstat parser, alongside host_netstat) — there was a stale duplicate
        # branch here written for a different "idle:/active:" connection-pool-style
        # output that doesn't match what this tool's actual command (ss -tunaop /
        # netstat) produces; it shadowed the correct parser and was removed.

        # ── k8s_pod_status ────────────────────────────────────────────────────
        if tool_key == "k8s_pod_status":
            status = "Unknown"
            for phase in ("Running", "Pending", "Failed", "Succeeded", "CrashLoopBackOff", "OOMKilled", "Error"):
                if phase.lower() in out.lower():
                    status = phase
                    break
            return {"pod_status": status, "pod_running": status == "Running"}

        # ── check_container_status ──────────────────────────────────────────
        # `docker inspect {target}` returns a JSON array with one element.
        if tool_key == "check_container_status":
            import json
            try:
                data = json.loads(out)
                info = data[0] if isinstance(data, list) and data else {}
                state = info.get("State", {}) or {}
                health = state.get("Health") or {}
                return {
                    "container_status":        state.get("Status", "unknown"),
                    "container_running":       bool(state.get("Running", False)),
                    "container_restart_count": info.get("RestartCount", 0),
                    "container_health":        health.get("Status", "none"),
                    "container_exit_code":     state.get("ExitCode", 0),
                }
            except (ValueError, TypeError, IndexError, KeyError):
                return {
                    "container_status": "unknown", "container_running": False,
                    "container_restart_count": 0, "container_health": "none",
                    "container_exit_code": 0,
                }

        # ── get_thread_dump ──────────────────────────────────────────────────
        # Command sends SIGQUIT/SIGABRT then tails recent container logs, since
        # the actual dump text is written to the target process's own stdout, not
        # returned by the kill command — raw_output now contains both the
        # confirmation message and (if the signal worked) the dump text.
        # thread_deadlocks is ALWAYS a real bool, never left absent: a decision
        # step checking "thread_deadlocks != null" must see a genuine answer, not
        # a missing field that could be misread as "not null" by some evaluators.
        if tool_key == "get_thread_dump":
            _process_not_found = "no process matching" in out.lower() or "process not found" in out.lower()
            _java_deadlock = "found one java-level deadlock" in out.lower()
            _go_deadlock = "all goroutines are asleep" in out.lower() and "deadlock" in out.lower()
            _blocked_count = len(re.findall(r'\bBLOCKED\b', out))
            return {
                "thread_dump_captured": not _process_not_found,
                "thread_deadlocks": bool(_java_deadlock or _go_deadlock),
                "blocked_thread_count": _blocked_count,
            }

        # ── check_file ───────────────────────────────────────────────────────
        # Command already self-formats as key=value: "exists=true size=1234"
        if tool_key == "check_file":
            m = re.search(r'exists=(true|false)\s+size=(\d+)', out)
            if m:
                return {"file_exists": m.group(1) == "true", "file_size_bytes": int(m.group(2))}
            return {"file_exists": False, "file_size_bytes": 0}

        # ── http_request ─────────────────────────────────────────────────────
        # Command already self-formats as key=value: "http_code=200" / "response_body=..."
        if tool_key == "http_request":
            code_m = re.search(r'http_code=(\d+)', out)
            body_m = re.search(r'response_body=(.*)', out, re.DOTALL)
            code = int(code_m.group(1)) if code_m else 0
            return {
                "http_code": code,
                "healthy": 200 <= code < 300,
                "response_body": body_m.group(1).strip() if body_m else "",
            }

        # ── check_dns ────────────────────────────────────────────────────────
        # nslookup: "Address: 1.2.3.4" lines (first is often the resolver itself,
        # not the answer) and explicit failure phrases.
        if tool_key == "check_dns":
            _failed = any(p in out for p in (
                "can't find", "NXDOMAIN", "server can't find", "timed out", "No answer",
            ))
            addrs = re.findall(r'^Address:\s*([\d.:a-fA-F]+)', out, re.MULTILINE)
            # First "Address:" line in nslookup's default output is usually the DNS
            # server's own address, not the answer — prefer the last one found.
            resolved_ip = addrs[-1] if len(addrs) > 1 else (addrs[0] if addrs else None)
            return {
                "resolved": bool(resolved_ip) and not _failed,
                "resolved_ip": resolved_ip,
            }

        # ── check_ports ──────────────────────────────────────────────────────
        # nc -zv: "succeeded" / "open" on success, "refused" / "timed out" on failure.
        if tool_key == "check_ports":
            _open = bool(re.search(r'succeeded|open\b', out, re.IGNORECASE))
            _refused = bool(re.search(r'refused|timed out|no route', out, re.IGNORECASE))
            return {
                "port_open": _open and not _refused,
                "connection_message": out.strip().splitlines()[-1] if out.strip() else "",
            }

        # ── check_env_vars ───────────────────────────────────────────────────
        # `env | sort` — one KEY=VALUE per line. Secret-shaped values are already
        # redacted globally by _redact_secrets_from_result before this ever runs.
        if tool_key == "check_env_vars":
            lines = [l for l in out.splitlines() if "=" in l]
            return {"env_var_count": len(lines)}

        # ── check_ssl_cert ───────────────────────────────────────────────────
        # openssl x509 -dates: "notBefore=Jun  1 00:00:00 2026 GMT" / "notAfter=..."
        if tool_key == "check_ssl_cert":
            m = re.search(r'notAfter=(.+)', out)
            if not m:
                return {"cert_valid": False, "days_remaining": None, "expiring_soon": False}
            try:
                from datetime import datetime as _dt
                expiry = _dt.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                days_remaining = (expiry - _dt.utcnow()).days
                return {
                    "cert_valid": True,
                    "days_remaining": days_remaining,
                    "expiring_soon": days_remaining < 30,
                }
            except ValueError:
                return {"cert_valid": True, "days_remaining": None, "expiring_soon": False}

        # ── get_process_info / host_process_info ────────────────────────────
        # `ps -fp PID` (header + one data line) followed by `cat /proc/PID/status`
        # ("State:\tS (sleeping)", "VmRSS:\t12345 kB").
        if tool_key in ("get_process_info", "host_process_info"):
            if "not found" in out.lower() or "may have already exited" in out.lower():
                return {"process_found": False, "pid": None, "process_state": None, "mem_rss_kb": None}
            pid_m = re.search(r'^\D*(\d+)', out.splitlines()[1]) if len(out.splitlines()) > 1 else None
            state_m = re.search(r'State:\s*\S\s*\(([^)]+)\)', out)
            rss_m = re.search(r'VmRSS:\s*(\d+)\s*kB', out)
            return {
                "process_found": True,
                "pid": int(pid_m.group(1)) if pid_m else None,
                "process_state": state_m.group(1) if state_m else None,
                "mem_rss_kb": int(rss_m.group(1)) if rss_m else None,
            }

        # ── host_logs ────────────────────────────────────────────────────────
        # journalctl --no-pager — raw log lines, no fixed schema; surface volume
        # and a coarse error signal rather than trying to parse arbitrary app logs.
        if tool_key == "host_logs":
            lines = [l for l in out.splitlines() if l.strip()]
            _has_errors = bool(re.search(r'\b(error|fail(?:ed|ure)?|critical|fatal)\b', out, re.IGNORECASE))
            return {"log_line_count": len(lines), "has_errors": _has_errors}

        # ── list_connections / host_netstat ──────────────────────────────────
        # Format-tolerant: docker/kubernetes try `ss` first, ssh/vcenter/aws_ssm/
        # azure fall back to `netstat` if `ss` isn't installed — genuinely
        # different column layouts, detected from the text itself rather than
        # from knowing which adapter ran.
        if tool_key in ("list_connections", "host_netstat"):
            lines = [l for l in out.splitlines() if l.strip()]
            data_lines = [l for l in lines if not re.match(r'^(Netid|Proto|Active|Local)', l, re.IGNORECASE)]
            established = sum(1 for l in data_lines if "ESTAB" in l.upper())
            listening   = sum(1 for l in data_lines if "LISTEN" in l.upper())
            return {
                "connection_count": len(data_lines),
                "established_count": established,
                "listening_count": listening,
            }

        # ── list_open_files ──────────────────────────────────────────────────
        # lsof: header line ("COMMAND PID USER ...") + one line per open file/socket.
        if tool_key == "list_open_files":
            if "not found" in out.lower() and "showing all open files" not in out.lower():
                return {"open_file_count": 0, "process_found": False}
            lines = [l for l in out.splitlines() if l.strip() and not l.upper().startswith("COMMAND")]
            return {"open_file_count": len(lines), "process_found": True}

        # ── query_metrics ────────────────────────────────────────────────────
        # curl .../metrics | grep '^metric_name' — Prometheus exposition format:
        # `metric_name{label="x"} 123.45`
        if tool_key == "query_metrics":
            m = re.search(r'^\S+\s+([\d.eE+-]+)\s*$', out.strip(), re.MULTILINE)
            value = None
            if m:
                try:
                    value = float(m.group(1))
                except ValueError:
                    value = None
            return {"metric_value": value, "metric_found": value is not None}

        # ── list_containers ──────────────────────────────────────────────────
        # docker ps -a --format table: NAMES / STATUS / RUNNINGFOR / IMAGE
        if tool_key == "list_containers":
            lines = [l for l in out.splitlines() if l.strip()]
            data_lines = lines[1:] if lines and re.match(r'^NAMES?\s', lines[0], re.IGNORECASE) else lines
            running   = sum(1 for l in data_lines if re.search(r'\bUp\b', l))
            unhealthy = sum(1 for l in data_lines if "unhealthy" in l.lower())
            return {
                "container_count": len(data_lines),
                "running_count": running,
                "unhealthy_count": unhealthy,
            }

        # ── k8s_events ───────────────────────────────────────────────────────
        # kubectl get events: header + rows, TYPE column is Normal|Warning.
        if tool_key == "k8s_events":
            lines = [l for l in out.splitlines() if l.strip()]
            data_lines = lines[1:] if lines and re.match(r'^(LAST SEEN|TYPE)', lines[0], re.IGNORECASE) else lines
            warning_count = sum(1 for l in data_lines if re.search(r'\bWarning\b', l))
            return {"event_count": len(data_lines), "warning_count": warning_count}

        # ── k8s_pod_describe ─────────────────────────────────────────────────
        if tool_key == "k8s_pod_describe":
            status_m = re.search(r'^Status:\s*(\S+)', out, re.MULTILINE)
            restart_m = re.search(r'Restart Count:\s*(\d+)', out)
            return {
                "pod_status": status_m.group(1) if status_m else None,
                "restart_count": int(restart_m.group(1)) if restart_m else None,
            }

        # ── k8s_rollout_status ───────────────────────────────────────────────
        if tool_key == "k8s_rollout_status":
            _complete = "successfully rolled out" in out.lower()
            return {
                "rollout_complete": _complete,
                "status_message": out.strip().splitlines()[-1] if out.strip() else "",
            }

        # ── k8s_top_pods ─────────────────────────────────────────────────────
        # kubectl top pods --sort-by=cpu: header + rows ("NAME CPU(cores) MEMORY(bytes)")
        if tool_key == "k8s_top_pods":
            lines = [l for l in out.splitlines() if l.strip()]
            data_lines = lines[1:] if lines and re.match(r'^NAME\s', lines[0], re.IGNORECASE) else lines
            top = data_lines[0].split() if data_lines else []
            return {
                "pod_count": len(data_lines),
                "top_pod_name": top[0] if len(top) > 0 else None,
                "top_pod_usage": top[1] if len(top) > 1 else None,
            }

        # ── Windows tools (WinRM / Invoke-Command + Select-Object) ───────────
        # PowerShell's default table formatting: header row, "----" dashes row,
        # then data rows. Column widths can vary (single- or multi-space padding
        # depending on the widest value PS needed to fit) — splitting on a fixed
        # whitespace-run count breaks as soon as a header/value happens to be
        # single-spaced. Instead, use the dash row's own character positions as
        # the true column boundaries and slice every row (including the header)
        # at those exact offsets — robust regardless of padding width.
        def _parse_ps_table(text: str) -> list:
            lines = [l for l in text.splitlines() if l.strip()]
            dash_idx = next((i for i, l in enumerate(lines) if re.match(r'^-+(\s+-+)*$', l.strip())), None)
            if dash_idx is None or dash_idx == 0:
                return []
            dash_line = lines[dash_idx]
            spans = [m.span() for m in re.finditer(r'-+', dash_line)]
            if not spans:
                return []
            header_line = lines[dash_idx - 1]
            headers = [header_line[start:end].strip() for start, end in spans]
            rows = []
            for line in lines[dash_idx + 1:]:
                cols = [line[start:end].strip() for start, end in spans]
                # PS doesn't truncate the final column — extend it to end-of-line
                # so a long value (e.g. a file path) isn't cut off mid-string.
                cols[-1] = line[spans[-1][0]:].strip()
                rows.append(dict(zip(headers, cols)))
            return rows

        if tool_key == "win_disk_usage":
            rows = _parse_ps_table(out)
            free_vals = [float(r["Free(GB)"]) for r in rows if r.get("Free(GB)", "").replace(".", "", 1).isdigit()]
            return {
                "drive_count": len(rows),
                "lowest_free_gb": min(free_vals) if free_vals else None,
            }

        if tool_key == "win_event_log":
            rows = _parse_ps_table(out)
            return {"event_count": len(rows)}

        if tool_key == "win_iis_status":
            rows = _parse_ps_table(out)
            stopped = sum(1 for r in rows if r.get("State", "").strip().lower() == "stopped")
            return {"pool_count": len(rows), "stopped_count": stopped}

        if tool_key == "win_memory":
            rows = _parse_ps_table(out)
            row = rows[0] if rows else {}
            try:
                total = float(row.get("TotalRAM_GB", "")) if row.get("TotalRAM_GB") else None
                free = float(row.get("FreeRAM_GB", "")) if row.get("FreeRAM_GB") else None
            except ValueError:
                total = free = None
            pct_used = round((1 - free / total) * 100, 1) if total and free is not None and total > 0 else None
            return {"total_ram_gb": total, "free_ram_gb": free, "mem_percent_used": pct_used}

        if tool_key == "win_netstat":
            lines = [l for l in out.splitlines() if l.strip() and re.search(r'\bTCP\b|\bUDP\b', l)]
            return {"connection_count": len(lines)}

        if tool_key == "win_process_info":
            rows = _parse_ps_table(out)
            if not rows:
                return {"process_found": False, "pid": None, "working_set_mb": None}
            row = rows[0]
            pid = int(row["Id"]) if row.get("Id", "").isdigit() else None
            ws_mb = None
            if row.get("WorkingSet", "").isdigit():
                ws_mb = round(int(row["WorkingSet"]) / (1024 ** 2), 1)
            return {"process_found": True, "pid": pid, "working_set_mb": ws_mb}

        # ── trace_syscalls ────────────────────────────────────────────────────
        if tool_key == "trace_syscalls":
            # Detect permission/strace errors — cannot trust a zero count in this case
            _perm_error = any(
                phrase in out
                for phrase in ("Operation not permitted", "ptrace", "Permission denied",
                               "attach: ptrace", "PTRACE_SEIZE")
            )
            _flag_conflict = "has no effect with" in out  # -T with -c conflict

            count = 0
            for line in out.splitlines():
                if "total" in line.lower():
                    try: count = int(line.split()[-1])
                    except (ValueError, IndexError): pass

            # Fallback: /proc/PID/io syscr+syscw when strace unavailable
            _used_proc_fallback = False
            if count == 0 and not _perm_error:
                syscr = syscw = 0
                for line in out.splitlines():
                    s = line.strip()
                    if s.startswith("syscr:"):
                        try: syscr = int(s.split(":")[1])
                        except (ValueError, IndexError): pass
                    elif s.startswith("syscw:"):
                        try: syscw = int(s.split(":")[1])
                        except (ValueError, IndexError): pass
                count = syscr + syscw
                if count > 0:
                    _used_proc_fallback = True

            # measurement_failed=True means "we got zero but can't trust it"
            # Verification steps must treat this as indeterminate, not success
            measurement_failed = _perm_error and count == 0
            # PID of the traced process — now echoed by the command itself ("PID=$PID")
            # before strace/proc-fallback output, since pgrep resolves it internally
            # but it never otherwise appeared anywhere in the raw output.
            pid_m = re.search(r'^PID=(\d+)', out, re.MULTILINE)
            return {
                "syscall_count": count,
                "top_syscall_count": count,
                "pid": int(pid_m.group(1)) if pid_m else None,
                "measurement_failed": measurement_failed,
                "measurement_source": "proc_io" if _used_proc_fallback else ("none" if measurement_failed else "strace"),
            }

        # NOTE: get_process_info is handled earlier in this function (the
        # `ps -fp PID` + `/proc/PID/status` parser, shared with host_process_info)
        # — there was a stale duplicate branch here that misread `ps -f` columns
        # (treating PPID as cpu% and the "C" column as mem%) and was unreachable
        # anyway since the earlier branch always matches first. Removed.

        if tool_key == "restart_process":
            restarted = "restarted:" in out or "signal sent" in out
            new_pid = None
            for part in out.split():
                if part.isdigit():
                    new_pid = int(part)
                    break
            return {"restarted": restarted, "new_pid": new_pid}

        # Unknown tool — nothing to parse
        return {}

    @staticmethod
    def _evaluate_condition(
        condition: str,
        step_outputs: Dict,
        anomaly_process: str = "",
        container: str = "",
        ctx=None,
    ) -> bool:
        """
        Evaluate a run_if condition string on a runbook step.

        Returns True  → step should execute.
        Returns False → step should be skipped.

        Supported syntax (case-insensitive operators):
            <field> IN     [val1, val2, ...]
            <field> NOT IN [val1, val2, ...]
            <field> ==     "value"
            <field> !=     "value"
            <field> >      number
            <field> <      number

        Available field names:
            top_process          Most recent 'top_process' from any diagnostic step output,
                                 falling back to anomaly_process from the alert.
            anomaly_process      Raw anomaly_process from the alert payload.
            container            The incident's target container name.
            step_N.field         Named output from step N (e.g. step_2.top_process).
            context.severity     Incident severity string.
            context.risk_score   Risk score (float).

        Unknown / unparseable conditions default to True (fail open — step runs).
        """
        import re

        cond = (condition or "").strip()
        if not cond:
            return True

        # ── Field resolver ──────────────────────────────────────────────────
        def resolve(field_name: str):
            # step_N.field  (legacy integer-index format, e.g. step_1.cpu_percent)
            m = re.match(r'^step_(\d+)\.(\w+)$', field_name, re.IGNORECASE)
            if m:
                return (step_outputs.get(int(m.group(1))) or {}).get(m.group(2))

            # alphanumeric_id.field  (editor format, e.g. diagnostic_11.cpu_percent)
            m = re.match(r'^([a-zA-Z][a-zA-Z0-9_]*)\.(\w+)$', field_name)
            if m:
                sid   = m.group(1)
                field = m.group(2)
                # Try by string step-id key (set when graph-walk stores outputs)
                out = step_outputs.get(sid)
                if isinstance(out, dict):
                    return out.get(field)
                # Fallback: scan all dict outputs for this field
                for v in step_outputs.values():
                    if isinstance(v, dict) and field in v:
                        return v[field]
                return None

            # context.field
            if field_name.startswith("context."):
                attr = field_name[8:]
                if ctx is None:
                    return None
                if attr == "severity":
                    try:
                        return ctx.sentinel.alert_payload.severity
                    except Exception:
                        return None
                if attr == "risk_score":
                    try:
                        return ctx.risk.risk_score
                    except Exception:
                        return None
                return None

            # shorthand: top_process — most recent diagnostic step that set it
            if field_name == "top_process":
                for idx in sorted((k for k in step_outputs.keys() if isinstance(k, int)), reverse=True):
                    val = step_outputs[idx].get("top_process")
                    if val:
                        return val
                return anomaly_process or ""

            # shorthand: top_process_pid — PID of the top CPU process (int or None)
            if field_name == "top_process_pid":
                for idx in sorted((k for k in step_outputs.keys() if isinstance(k, int)), reverse=True):
                    val = step_outputs[idx].get("top_process_pid")
                    if val is not None:
                        try:
                            return int(val)
                        except (ValueError, TypeError):
                            pass
                return None

            # shorthand: top_process_is_main_service — bool
            # True  → top CPU process is PID 1 or a direct child of it
            #         (uvicorn worker, redis worker, etc.) → legitimate load → scale
            # False → process has a deeper lineage (rogue shell session, injected cmd)
            #         → potentially dangerous → kill
            if field_name == "top_process_is_main_service":
                for idx in sorted((k for k in step_outputs.keys() if isinstance(k, int)), reverse=True):
                    val = step_outputs[idx].get("top_process_is_main_service")
                    if val is not None:
                        return bool(val)
                return None

            if field_name == "anomaly_process":
                return anomaly_process or ""

            if field_name == "container":
                return container or ""

            # Bare field name (e.g. "memory_pct") — scan all captured step outputs.
            # This handles decision conditions generated by the graph editor where
            # output_capture assigns a plain variable name without a step-id prefix.
            for v in step_outputs.values():
                if isinstance(v, dict) and field_name in v:
                    return v[field_name]

            return None

        # ── Operator parsers ────────────────────────────────────────────────
        def _list_vals(raw: str):
            """Parse 'val1, val2, ...' from inside brackets."""
            return [v.strip().strip("'\"") for v in raw.split(",") if v.strip()]

        def _str(v) -> str:
            return str(v).lower() if v is not None else ""

        def _eval_single(c: str) -> bool:
            """Evaluate a single (non-compound) condition token."""
            c = c.strip()

            # field NOT IN [...]
            m = re.match(r'^(\S+)\s+NOT\s+IN\s+\[([^\]]*)\]$', c, re.IGNORECASE)
            if m:
                val = _str(resolve(m.group(1)))
                return val not in [v.lower() for v in _list_vals(m.group(2))]

            # field IN [...]
            m = re.match(r'^(\S+)\s+IN\s+\[([^\]]*)\]$', c, re.IGNORECASE)
            if m:
                val = _str(resolve(m.group(1)))
                return val in [v.lower() for v in _list_vals(m.group(2))]

            # field >= number
            m = re.match(r'^(\S+)\s*>=\s*(\d+(?:\.\d+)?)$', c, re.IGNORECASE)
            if m:
                try:
                    return float(resolve(m.group(1)) or 0) >= float(m.group(2))
                except (ValueError, TypeError):
                    return False

            # field <= number
            m = re.match(r'^(\S+)\s*<=\s*(\d+(?:\.\d+)?)$', c, re.IGNORECASE)
            if m:
                try:
                    return float(resolve(m.group(1)) or 0) <= float(m.group(2))
                except (ValueError, TypeError):
                    return False

            # field == "value"  (single "=" accepted too — authors/AI-generation
            # sometimes write it that way; silently falling through to the
            # "unparseable -> True" default below turned a typo into a fail-open
            # decision branch instead of a fail-closed one)
            m = re.match(r'^(\S+)\s*={1,2}\s*["\']?([^"\']+)["\']?$', c, re.IGNORECASE)
            if m:
                return _str(resolve(m.group(1))) == m.group(2).strip().lower()

            # field != "value"
            m = re.match(r'^(\S+)\s*!=\s*["\']?([^"\']+)["\']?$', c, re.IGNORECASE)
            if m:
                return _str(resolve(m.group(1))) != m.group(2).strip().lower()

            # field > number
            m = re.match(r'^(\S+)\s*>\s*(\d+(?:\.\d+)?)$', c, re.IGNORECASE)
            if m:
                try:
                    return float(resolve(m.group(1)) or 0) > float(m.group(2))
                except (ValueError, TypeError):
                    return False

            # field < number
            m = re.match(r'^(\S+)\s*<\s*(\d+(?:\.\d+)?)$', c, re.IGNORECASE)
            if m:
                try:
                    return float(resolve(m.group(1)) or 0) < float(m.group(2))
                except (ValueError, TypeError):
                    return False

            logger.warning(f"[CONDITION] Could not parse token '{c}' — defaulting True")
            return True

        # ── Compound: AND  (&&  or  "and") ──────────────────────────────────
        and_parts = re.split(r'\s*&&\s*|\s+and\s+', cond, flags=re.IGNORECASE)
        if len(and_parts) > 1:
            return all(_eval_single(p) for p in and_parts)

        # ── Compound: OR   (||  or  "or") ───────────────────────────────────
        or_parts = re.split(r'\s*\|\|\s*|\s+or\s+', cond, flags=re.IGNORECASE)
        if len(or_parts) > 1:
            return any(_eval_single(p) for p in or_parts)

        # ── Single condition ─────────────────────────────────────────────────
        result = _eval_single(cond)
        if result is True and not re.search(r'[><=!]|\bIN\b|\bNOT\b', cond, re.IGNORECASE):
            logger.warning(
                f"[CONDITION] Could not parse run_if condition '{condition}' — defaulting to RUN"
            )
        return result

    @staticmethod
    def _watcher_exec(container: str, command: str, timeout: int = 12,
                      mode: str = "container",
                      watcher_base: str = "http://watcher_brain:8080") -> Dict:
        """
        Call the detecting watcher's /exec endpoint to run a shell command.

        mode="container": docker exec {container} sh -c "{command}"
        mode="host":      runs directly on the watcher (for docker logs, docker ps, etc.)

        Returns a dict with keys: success, stdout, stderr, returncode, command
        Never raises — returns success=False with an error message on failure.
        """
        import httpx as _httpx
        try:
            resp = _httpx.post(
                f"{watcher_base}/exec",
                json={"container": container, "command": command,
                      "timeout": timeout, "mode": mode},
                timeout=timeout + 5,
            )
            return resp.json()
        except Exception as exc:
            cmd_str = (
                f"docker exec {container} sh -c '{command}'"
                if mode == "container" else command
            )
            return {
                "success": False, "stdout": "", "stderr": str(exc),
                "returncode": -1, "command": cmd_str,
            }


class VerifierAgent(Agent):
    """Verifies that incident is resolved.

    Phase 10: Refactored to use typed VerificationContext.
    Creates verification results from execution outcomes.
    Updates lifecycle_state to terminal states (RESOLVED or FAILED).
    """

    def __init__(self):
        super().__init__("verifier")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Verify incident resolution based on real execution outcome"""
        # Get typed context
        ctx = self._get_typed_context(state)
        if ctx is None:
            ctx = IncidentWorkflowContext()

        # EXTERNAL-SOURCE DOCUMENTED: no execution was performed — nothing to verify.
        # Leave lifecycle and decision_result untouched so mark_failed doesn't trigger.
        if state.context.get("decision_result") == "external_source_documented":
            reasoning = (
                "[VERIFIER AGENT] Skipped — external source incident.\n"
                "  No automated execution was performed; verification not applicable.\n"
                "  Lifecycle remains in_progress for operator follow-up.\n"
            )
            state = self._add_trace(state, reasoning)
            return state

        # Get proposal and execution results from typed context
        proposal = state.context.get("proposal", {}) or (ctx.proposal.__dict__ if ctx.proposal else {})
        execution_result = state.context.get("execution_result", {})

        target = proposal.get("target", "unknown")
        action = proposal.get("action", "unknown")
        process_name = proposal.get("process_name", "") or ctx.get_anomaly_process()

        # Real check: did the runbook explicitly request an incident state update?
        # An "incident_update" step only executes if everything before it — including
        # verification — succeeded; the executor's on_failure=abort default (set on
        # every step) halts the run before reaching it otherwise. Its absence is
        # itself meaningful: a runbook with no incident_update step has no basis to
        # claim resolution, regardless of what execution_result.success says. This
        # replaces the previous hardcoded heuristic (trust execution_result.success,
        # with a process_kill-specific re-check) — that heuristic is what let
        # incidents resolve without the underlying problem ever being confirmed fixed.
        _incident_update = state.context.get("incident_update_requested")
        requested_state = _incident_update.get("state") if _incident_update else None
        is_resolved = requested_state == "resolved"

        exec_msg = execution_result.get("message") or execution_result.get("error") or "N/A"
        exec_id = execution_result.get("execution_id", "N/A")

        # Create VerificationResult for execution check
        execution_check = VerificationResult(
            step_name="Execution Completed",
            status="passed" if execution_result.get("success") else "failed",
            metric="execution_success",
            actual_value=float(execution_result.get("success", 0)),
            threshold=1.0,
            message=exec_msg,
        )

        verification_results = [execution_check]

        # Add process termination check if applicable
        if process_name:
            process_check = VerificationResult(
                step_name="Process Termination",
                status="passed" if is_resolved else "failed",
                metric="process_running",
                actual_value=float(not is_resolved),  # 1 if still running, 0 if terminated
                threshold=0.0,
                message=f"Process '{process_name}' {'still running' if not is_resolved else 'terminated'}",
            )
            verification_results.append(process_check)

        # Create VerificationContext (typed)
        ctx.verification = VerificationContext(
            verification_results=verification_results,
            overall_success=is_resolved,
            remediation_effective=is_resolved,
            issues_resolved=is_resolved,
        )

        # Persist typed context
        state = self._set_typed_context(state, ctx)

        # Update lifecycle state based on verification
        if is_resolved:
            state.lifecycle_state = LifecycleState.RESOLVED
            # Only overwrite remediation_outcome if it wasn't already marked aborted
            # (aborted = steps were stopped early, not a full attempt)
            if state.remediation_outcome != "aborted":
                state.remediation_outcome = "succeeded"
            state.resolution_source = "automated_remediation"
        else:
            # Remediation did not succeed — hand off to a human.
            # lifecycle_state → AWAITING_MANUAL (not FAILED; that's for pipeline errors in base.py)
            state.lifecycle_state = LifecycleState.AWAITING_MANUAL
            # Preserve "aborted" if set — it's more specific than "failed"
            if state.remediation_outcome != "aborted":
                state.remediation_outcome = "failed"

        # Write final health status back to Neo4j CMDB so the graph reflects reality.
        # resolved=True  → node turns green (healthy), incident marked resolved
        # resolved=False → node stays red (degraded), incident marked failed
        if target != "unknown":
            try:
                from agentic_os.services.cmdb import get_cmdb
                get_cmdb().mark_ci_recovered(
                    resource_name=target,
                    workflow_id=str(state.workflow_id),
                    resolved=is_resolved,
                )
            except Exception as _cmdb_err:
                logger.warning(f"[VERIFIER] CMDB health write-back failed (non-fatal): {_cmdb_err}")

        reasoning = (
            f"[VERIFIER AGENT] Post-remediation verification\n"
            f"  Target Resource: {target}\n"
            f"  Remediation Applied: {action.upper()}\n"
            f"  Execution ID: {exec_id}\n"
            f"  Execution Outcome: {'✅ Succeeded' if execution_result.get('success') else '❌ Failed'}\n"
            f"  Execution Message: {exec_msg}\n"
            f"  \n"
            f"  Verification Checks:\n"
            f"    • Execution completed: {'✓' if execution_result.get('success') else '✗'}\n"
        )
        if process_name:
            reasoning += f"    • Process targeted: {process_name}\n"
        reasoning += f"  \n"

        if is_resolved:
            reasoning += (
                f"  RESULT: ✅ RESOLVED\n"
                f"  Confidence: HIGH\n"
                f"  Rationale: Runbook explicitly confirmed resolution via an incident_update step\n"
                f"  (only reachable because verification passed). {action.upper()} applied to {target}.\n"
                f"  {f'Process {process_name!r} terminated.' if process_name else ''}\n"
                f"  Incident status: Marked RESOLVED. Post-incident: document root cause."
            )
            state.context["decision_result"] = "success"   # routes to mark_resolved
        else:
            _no_update_reason = (
                "Runbook has no incident_update step confirming resolution"
                if not _incident_update
                else f"Runbook explicitly requested state '{requested_state}', not 'resolved'"
            )
            reasoning += (
                f"  RESULT: ⚠️ STILL ACTIVE\n"
                f"  Confidence: HIGH\n"
                f"  Rationale: {_no_update_reason}. {execution_result.get('error') or ''}\n"
                f"  Recommended action: Escalate to human expert for investigation."
            )
            state.context["decision_result"] = "failure"   # routes to mark_failed

        state = self._add_trace(state, reasoning)
        return state
