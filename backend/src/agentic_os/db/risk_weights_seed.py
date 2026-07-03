"""
Default risk assessment weights and thresholds.

This seed is applied on first startup to populate the risk_weight_configs table
with sensible defaults for qualification scoring and incident prioritization.

Schema v2 — adds per-factor config under the "factors" key:
  enabled       : bool   — include this factor in scoring
  weight        : int    — max raw points before normalisation
  missing_data  : str    — "pessimistic" | "neutral" | "exclude"
                           behaviour when CMDB has no data for this factor
  label         : str    — display name
  description   : str    — tooltip / help text
  cmdb_sourced  : bool   — whether data comes from CMDB (shows missing_data selector)

The scoring engine reads "factors" when present and falls back to the legacy
"factor_weights" flat dict for installations that haven't migrated yet.
"""

DEFAULT_RISK_WEIGHTS = {
    "config_key": "default",
    "weights": {

        # ── Per-factor configuration (v2 schema) ────────────────────────────
        # The engine uses these in preference to the legacy factor_weights dict.
        # missing_data options:
        #   "pessimistic"  — assume worst case when CMDB has no value
        #   "neutral"      — use a conservative mid-range default
        #   "exclude"      — skip factor; its weight is redistributed proportionally
        "factors": {
            "severity": {
                "enabled": True,
                "weight": 20,
                "missing_data": "neutral",      # always present from alert payload
                "cmdb_sourced": False,
                "label": "Event Severity",
                "description": "Severity of the triggering alert (critical → 20 pts, info → 2 pts).",
            },
            "ci_tier": {
                "enabled": True,
                "weight": 15,
                "missing_data": "neutral",
                "cmdb_sourced": True,
                "label": "CI Tier",
                "description": "Numeric tier of the CI in the CMDB hierarchy (1 = top, 3 = infrastructure). Applied as a flat weight — tune the weight to raise/lower its contribution.",
            },
            "environment": {
                "enabled": True,
                "weight": 15,
                "missing_data": "neutral",
                "cmdb_sourced": True,
                "label": "Deployment Environment",
                "description": "Production carries full weight; staging and development are scaled down via the environment multiplier table.",
            },
            "business_criticality": {
                "enabled": True,
                "weight": 20,
                "missing_data": "neutral",
                "cmdb_sourced": True,
                "label": "Business Criticality",
                "description": "How business-critical the service is: Tier 1 = customer-facing, Tier 2 = core services, Tier 3 = infrastructure. Applied via the criticality multiplier table.",
            },
            "user_impact": {
                "enabled": True,
                "weight": 15,
                "missing_data": "neutral",
                "cmdb_sourced": True,
                "label": "User Impact",
                "description": "Number of users served by the affected CI. Scales linearly up to 10,000 users = full weight.",
            },
            "blast_radius": {
                "enabled": True,
                "weight": 15,
                "missing_data": "neutral",
                "cmdb_sourced": False,          # derived from dependency graph, not raw CMDB
                "label": "Blast Radius",
                "description": "Number and criticality of dependent services. Calculated from the service dependency graph at event time.",
            },
            "failover": {
                "enabled": True,
                "weight": 5,
                "missing_data": "pessimistic",  # unknown → assume no failover (no risk reduction)
                "cmdb_sourced": True,
                "label": "Failover Availability",
                "description": "When failover is available, the full weight is subtracted from the score (risk reduction). Pessimistic default: assume no failover when CMDB is silent.",
            },
            "spof": {
                "enabled": True,
                "weight": 10,
                "missing_data": "pessimistic",  # unknown → assume it IS a SPOF
                "cmdb_sourced": True,
                "label": "Single Point of Failure",
                "description": "SPOF CIs add the full weight to the score. Disable this factor if your CMDB does not track SPOF status — or set missing_data to 'neutral' to score 0 when unknown.",
            },
            "sla": {
                "enabled": True,
                "weight": 10,
                "missing_data": "neutral",
                "cmdb_sourced": True,
                "label": "SLA Compliance",
                "description": "Lower SLA percentages increase risk. At 90% SLA → full weight; at 99.9% → near zero. Neutral default: 95%.",
            },
            "history": {
                "enabled": True,
                "weight": 10,
                "missing_data": "exclude",      # no history = no recurrence penalty
                "cmdb_sourced": False,
                "label": "Incident History",
                "description": "Each previous incident on this CI adds 2 pts, up to the weight ceiling. Exclude behaviour: no historical data = no recurrence penalty.",
            },
        },

        # ── Legacy flat dict — kept for backward compatibility ───────────────
        # The engine reads "factors[x].weight" in preference; this dict is used
        # as a fallback on installations that have not migrated.
        "factor_weights": {
            "severity":             20,
            "ci_tier":              15,
            "environment":          15,
            "business_criticality": 20,
            "user_impact":          15,
            "blast_radius":         15,
            "failover":              5,
            "spof":                 10,
            "sla":                  10,
            "history":              10,
        },

        # ── Event type qualification multipliers ─────────────────────────────
        # Applied during the qualification gate ONLY — do not affect the
        # 0-100 risk score shown on the incident Risk tab.
        #
        # Lookup priority (EventQualificationService._get_event_multiplier):
        #   1. event_type_multipliers[canonical_code]  — per-type override
        #   2. domain_multipliers[domain]              — domain-level default
        #   3. default_event_multiplier                — global fallback

        # Domain-level defaults — covers all ~200 taxonomy types automatically.
        "domain_multipliers": {
            # Kept ≤ 1.0 so critical events score 50–100 instead of all saturating at 100.
            # Per-type overrides handle the "always-max" events (service_down, ransomware, etc).
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
        # Per-type overrides — canonical dot-notation codes that deviate from
        # their domain default. Users may add/remove these in Settings.
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
            "log.error.spike":                               0.75,  # above log domain (0.5), below security
            "log.warning.pattern_detected":                  0.55,
            "container.runtime.oom_kill":                    0.85,  # warning oom → 51, qualifies
        },
        "default_event_multiplier": 1.0,

        # ── Environment multipliers (used by qualification AND risk assessment) ─
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

        # ── Business criticality multipliers (risk assessment only) ──────────
        # Used by RiskAssessorAgent post-creation scoring.
        # NOT used by event qualification — qualification uses the 3-factor model.
        "business_criticality_multiplier": {
            "tier_1": 1.5,
            "tier_2": 1.0,
            "tier_3": 0.6,
        },

        # ── Qualification settings ────────────────────────────────────────────
        # unknown_ci_behavior: what to do when resource is not in CMDB
        #   "dismiss"        — reject the event immediately
        #   "qualify_as_low" — cap score at unknown_ci_score_cap
        #   "qualify_normal" — score normally (treats environment as unknown)
        "unknown_ci_behavior":  "qualify_normal",
        "unknown_ci_score_cap": 40.0,

        "qualification_threshold": 50.0,

        "criticality_min_score": {
            "info":     75.0,
            "warning":  50.0,
            "critical": 30.0,
        },

        # ── Priority matrix ──────────────────────────────────────────────────
        "priority_matrix": {
            "critical:tier_1": "P1",
            "critical:tier_2": "P1",
            "critical:tier_3": "P2",
            "high:tier_1":     "P1",
            "high:tier_2":     "P2",
            "high:tier_3":     "P2",
            "medium:tier_1":   "P2",
            "medium:tier_2":   "P3",
            "medium:tier_3":   "P3",
            "low:tier_1":      "P3",
            "low:tier_2":      "P4",
            "low:tier_3":      "P4",
            "info:tier_1":     "P4",
            "info:tier_2":     "P5",
            "info:tier_3":     "P5",
        },

        # ── SLA response times ───────────────────────────────────────────────
        "sla_response_times": {
            "P1": 15,
            "P2": 60,
            "P3": 240,
            "P4": 1440,
            "P5": 7200,
        },
    }
}


def seed_risk_weights(db_session):
    """
    Seed (or update) risk weights to the current platform defaults.

    Always upserts so that schema corrections in new releases are applied
    on restart. User customisations are preserved for keys not touched by
    the seed — only the structural defaults are written.
    """
    from agentic_os.db.repositories import RiskWeightConfigRepository
    import logging

    logger = logging.getLogger(__name__)
    repo = RiskWeightConfigRepository(db_session)
    existing = repo.get_by_key("default")

    try:
        repo.create_or_update("default", DEFAULT_RISK_WEIGHTS["weights"])
        if existing:
            logger.info("✓ Risk weight config updated to platform defaults (v2 schema)")
        else:
            logger.info("✓ Risk weight config seeded with defaults (v2 schema)")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to seed/update risk weights: {e}")
        return False
