"""
Event Qualification Service - Lightweight pre-check for event→incident threshold.

Three-factor scoring model:
  1. Event signal: raw_criticality × event_type_multiplier (always known)
  2. CI existence gate: configurable policy when resource not in CMDB
  3. Environment multiplier: production=1.0, staging=0.6, dev=0.3

Score = min(100, criticality_score × event_type_multiplier × 100) × environment_multiplier

CMDB-dependent factors (ci_tier, business_criticality, user_count, spof, sla, failover)
are dropped from qualification — those belong in RiskAssessorAgent post-creation scoring
where CMDB relationship traversal is appropriate.
"""

import logging
from typing import Dict, Any, Optional
from agentic_os.services.cmdb import CMDBService
from agentic_os.db.database import SessionLocal
from agentic_os.db.repositories import RiskWeightConfigRepository

logger = logging.getLogger(__name__)


class EventQualificationService:
    """
    Qualifies raw monitoring events against configurable threshold.

    Scoring:
      base_score = min(100, criticality_score × event_type_multiplier × 100)
      final_score = base_score × environment_multiplier

    Unknown CI policy (unknown_ci_behavior):
      "dismiss"        — CI not in CMDB → immediately dismiss
      "qualify_as_low" — CI not in CMDB → cap base_score at unknown_ci_score_cap
      "qualify_normal" — CI not in CMDB → score normally (default)
    """

    DEFAULT_WEIGHTS = {
        # Domain-level defaults applied when no per-type override exists.
        # Covers all ~200 taxonomy types automatically via prefix match.
        "domain_multipliers": {
            # Kept ≤ 1.0 so critical events score 50–100 instead of all saturating at 100.
            # Per-type overrides below handle the "always-max" events (service_down, ransomware, etc).
            "security":       1.0,   # critical→100, warning→60
            "database":       0.9,   # critical→90,  warning→54
            "synthetic":      0.85,  # critical→85,  warning→51
            "application":    0.85,  # critical→85,  warning→51
            "container":      0.8,   # critical→80,  warning→48
            "network":        0.8,   # critical→80,  warning→48
            "infrastructure": 0.7,   # critical→70,  warning→42
            "cloud":          0.75,  # critical→75,  warning→45
            "log":            0.5,   # critical→50,  warning→30
            "custom":         0.75,  # critical→75,  warning→45
        },
        # Per-type overrides (canonical dot-notation codes) that deviate from
        # their domain default. Lookup priority: exact code > alias > domain > default.
        "event_type_multipliers": {
            "application.availability.service_down":         1.8,
            "application.availability.service_unresponsive": 1.6,
            "application.availability.health_check_failing": 1.6,
            "application.performance.error_rate_high":       1.2,
            "application.performance.apdex_degraded":        1.0,
            "infrastructure.storage.disk_full":              1.6,
            "infrastructure.storage.disk_high":              1.3,
            "database.availability.down":                    2.0,
            "database.replication.split_brain":              2.0,
            "network.tls.certificate_expired":               1.8,
            "network.tls.certificate_expiring":              1.0,
            "security.endpoint.ransomware_behavior":         2.5,
            "security.compliance.audit_log_cleared":         2.0,
            "log.error.spike":                               1.0,   # explicit configured pattern match — treat as normal severity
            "log.error.pattern_detected":                    1.0,   # same: configured regex match, not statistical noise
            "log.warning.pattern_detected":                  0.75,
            "container.runtime.oom_kill":                    0.85,  # warning oom → 51, qualifies
        },
        "default_event_multiplier": 1.0,
        # Per-resource overrides — most specific tier, consulted before event_type/domain
        # multipliers. Keyed by resource_name; value is {"multiplier": float, "reason": str}.
        # Populated by Platform Intelligence's resource-noise check (category=resource_specific)
        # when a single resource accounts for most of a domain's noise, rather than penalizing
        # every other resource in that domain via a blanket multiplier change.
        "resource_overrides": {},
        "environment_multipliers": {
            "production":  1.0,
            "prod":        1.0,
            "staging":     0.6,
            "stage":       0.6,
            "development": 0.3,
            "dev":         0.3,
            "test":        0.2,
            "qa":          0.4,
            "unknown":     0.75,
        },
        "unknown_ci_behavior":  "qualify_normal",
        "unknown_ci_score_cap": 40.0,
        "qualification_threshold": 50.0,
        "criticality_min_score": {
            "info":     75.0,
            "warning":  50.0,
            "high":     40.0,
            "critical": 30.0,
        },
    }

    def __init__(self, cmdb: Optional[CMDBService] = None, db_session=None):
        self.cmdb = cmdb or CMDBService()
        self.db_session = db_session
        self.weights = self._load_weights()

    def reload_weights(self) -> None:
        """Re-read weights from the database, replacing the in-memory copy.

        The singleton is constructed with db_session=None (it normally relies
        on the per-request session passed into qualify_event's callers), so a
        reload needs its own short-lived session rather than reusing one tied
        to a request that may have already closed.
        """
        if self.db_session is not None:
            self.weights = self._load_weights()
            return
        from agentic_os.db.database import SessionLocal
        session = SessionLocal()
        try:
            self.db_session = session
            self.weights = self._load_weights()
        finally:
            self.db_session = None
            session.close()

    def _load_weights(self) -> Dict[str, Any]:
        import copy
        weights = copy.deepcopy(self.DEFAULT_WEIGHTS)
        try:
            if self.db_session:
                repo = RiskWeightConfigRepository(self.db_session)
                config = repo.get_by_key("default")
                if config:
                    self._deep_merge(weights, config.weights)
                    logger.info("✓ Loaded risk weights from database (merged with defaults)")
        except Exception as e:
            logger.warning(f"⚠ Failed to load weights from DB: {e}")
        return weights

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        for key, val in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(val, dict):
                EventQualificationService._deep_merge(base[key], val)
            else:
                base[key] = val

    def _get_event_multiplier(self, event_type: str, resource_name: Optional[str] = None) -> float:
        """
        Tiered multiplier lookup:
          1. Resource-specific override (resource_overrides[resource_name]) — most specific,
             lets a single noisy-but-otherwise-healthy resource be tuned down without
             penalizing every other resource sharing its domain/event type.
          2. Resolve old flat alias → canonical dot-notation code
          3. Exact match in event_type_multipliers (canonical code wins first)
          4. Domain prefix match in domain_multipliers
          5. default_event_multiplier (fallback, default 1.0)
        """
        if resource_name:
            resource_overrides = self.weights.get("resource_overrides", {})
            override = resource_overrides.get(resource_name)
            if override is not None:
                if isinstance(override, dict) and "multiplier" in override:
                    return float(override["multiplier"])
                if isinstance(override, (int, float)):
                    return float(override)

        from agentic_os.db.event_type_taxonomy_data import ALIAS_MAP
        canonical = ALIAS_MAP.get(event_type, event_type)
        overrides = self.weights.get("event_type_multipliers", {})
        if canonical in overrides:
            return overrides[canonical]
        if event_type in overrides:
            return overrides[event_type]
        domain_mults = self.weights.get("domain_multipliers", {})
        code = canonical if "." in canonical else event_type
        if "." in code:
            domain = code.split(".")[0]
            if domain in domain_mults:
                return domain_mults[domain]
        return self.weights.get("default_event_multiplier", 1.0)

    def qualify_event(
        self,
        event_type: str,
        resource_name: str,
        raw_criticality: str,
        signal_value: Optional[float] = None,
        signal_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Qualify a monitoring event.

        Returns:
            {
                "qualified": bool,
                "score": float,        # 0-100 final score
                "confidence": float,   # 100 if CI found, 60 if unknown
                "reason": str,
                "factors": {           # rich breakdown for display
                    "event_type_multiplier": float,
                    "criticality_score": float,
                    "base_event_score": float,
                    "ci_found": bool,
                    "unknown_ci_policy": str,
                    "environment": str,
                    "environment_multiplier": float,
                    "final_score": float,
                    "qualification_threshold": float,
                    "criticality_floor": float,
                },
                "ci_info": dict,
                "unknown_fields": list,
            }
        """
        unknown_ci_behavior = self.weights.get("unknown_ci_behavior", "qualify_normal")
        threshold = self.weights.get("qualification_threshold", 50.0)
        crit_min_scores = self.weights.get("criticality_min_score", {})
        crit_floor = crit_min_scores.get(raw_criticality, threshold)

        # Fetch resource info from CMDB
        ci_info = self.cmdb.get_resource_info(resource_name)
        ci_found = ci_info is not None

        # Criticality score
        criticality_score = self._criticality_to_score(raw_criticality)

        # Event type multiplier (tiered: resource override → event type → domain → default)
        event_multiplier = self._get_event_multiplier(event_type, resource_name)

        # Base score
        base_event_score = min(100.0, criticality_score * event_multiplier * 100.0)

        # Unknown CI gate
        if not ci_found:
            if unknown_ci_behavior == "dismiss":
                factors = {
                    "event_type_multiplier": event_multiplier,
                    "criticality_score": criticality_score,
                    "base_event_score": round(base_event_score, 2),
                    "ci_found": False,
                    "unknown_ci_policy": "dismiss",
                    "environment": "unknown",
                    "environment_multiplier": 0.0,
                    "final_score": 0.0,
                    "qualification_threshold": threshold,
                    "criticality_floor": crit_floor,
                }
                return {
                    "qualified": False,
                    "score": 0.0,
                    "confidence": 0.0,
                    "reason": (
                        f"DISMISSED: CI '{resource_name}' not found in CMDB "
                        f"— policy: dismiss unknown CIs"
                    ),
                    "factors": factors,
                    "ci_info": {},
                    "unknown_fields": [resource_name],
                }
            elif unknown_ci_behavior == "qualify_as_low":
                score_cap = self.weights.get("unknown_ci_score_cap", 40.0)
                base_event_score = min(base_event_score, score_cap)

        # Environment multiplier
        environment = (ci_info or {}).get("environment") or "unknown"
        env_multipliers = self.weights.get(
            "environment_multipliers",
            self.weights.get("environment_multiplier", {}),
        )
        environment_multiplier = env_multipliers.get(
            environment, env_multipliers.get("unknown", 0.75)
        )

        final_score = min(100.0, base_event_score * environment_multiplier)

        # Qualification check
        qualified = final_score >= threshold
        if qualified and final_score < crit_floor:
            qualified = False

        factors = {
            "event_type_multiplier": event_multiplier,
            "resource_override_applied": resource_name in self.weights.get("resource_overrides", {}),
            "criticality_score": criticality_score,
            "base_event_score": round(base_event_score, 2),
            "ci_found": ci_found,
            "unknown_ci_policy": unknown_ci_behavior,
            "environment": environment,
            "environment_multiplier": environment_multiplier,
            "final_score": round(final_score, 2),
            "qualification_threshold": threshold,
            "criticality_floor": crit_floor,
        }

        reason = self._generate_reason(
            qualified=qualified,
            final_score=final_score,
            threshold=threshold,
            ci_found=ci_found,
            environment=environment,
            event_type=event_type,
            raw_criticality=raw_criticality,
            crit_floor=crit_floor,
            event_multiplier=event_multiplier,
            env_multiplier=environment_multiplier,
            base_event_score=base_event_score,
        )

        return {
            "qualified": qualified,
            "score": final_score,
            "confidence": 100.0 if ci_found else 60.0,
            "reason": reason,
            "factors": factors,
            "ci_info": ci_info or {},
            "unknown_fields": [] if ci_found else [resource_name],
        }

    @staticmethod
    def _criticality_to_score(criticality: str) -> float:
        return {"info": 0.3, "warning": 0.6, "high": 0.8, "critical": 1.0}.get(criticality, 0.5)

    @staticmethod
    def _generate_reason(
        qualified: bool,
        final_score: float,
        threshold: float,
        ci_found: bool,
        environment: str,
        event_type: str,
        raw_criticality: str,
        crit_floor: float,
        event_multiplier: float,
        env_multiplier: float,
        base_event_score: float,
    ) -> str:
        status = "QUALIFIED" if qualified else "DISMISSED"
        derivation = (
            f"{raw_criticality}(×{event_multiplier}) → base {base_event_score:.0f} "
            f"× {environment}(×{env_multiplier}) = {final_score:.1f}"
        )
        if not ci_found:
            ci_note = " [CI not in CMDB]"
        else:
            ci_note = ""

        reason = f"{status}: {final_score:.1f}/{threshold:.1f} — {derivation}{ci_note}"

        if qualified and final_score < crit_floor:
            reason = (
                f"DISMISSED: {final_score:.1f} below {raw_criticality} floor "
                f"({crit_floor:.0f}) — {derivation}{ci_note}"
            )

        return reason


# Singleton instance
_qualification_service: Optional[EventQualificationService] = None


def get_qualification_service() -> EventQualificationService:
    global _qualification_service
    if _qualification_service is None:
        _qualification_service = EventQualificationService()
    return _qualification_service


def reload_qualification_service() -> None:
    """Refresh the singleton's weights from the database.

    Call this after any change to the "default" risk-weight config so the
    change takes effect immediately instead of requiring a backend restart.
    No-op if no event has been qualified yet this process — the next
    get_qualification_service() call will construct it with fresh weights
    anyway.
    """
    if _qualification_service is not None:
        _qualification_service.reload_weights()
