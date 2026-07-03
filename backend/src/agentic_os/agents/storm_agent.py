"""
Storm Agent

Meta-orchestrator that sits ABOVE the 7-agent pipeline.
Called when a correlated event storm is detected — NOT for individual incidents.

Responsibilities:
  1. Build a topology map from Neo4j for all affected resources.
  2. Find common upstream dependencies → root cause candidates.
  3. Generate an LLM-powered (or rule-based) root cause hypothesis.
  4. Return a structured analysis dict for the Celery task to act on.

The agent does NOT write to the database — that is the responsibility of the
execute_storm_analysis_task in celery_app.py.

Usage (from Celery task):
    agent = StormAgent()
    analysis = agent.analyze(
        affected_resources=["payment-svc", "auth-svc", "api-gateway"],
        event_types=["service_unresponsive", "health_check_failed"],
        incident_ids=["uuid1", "uuid2", "uuid3"],
    )
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_agent_settings() -> Dict[str, Any]:
    """
    Read storm agent behaviour settings from the platform_settings table.

    Called once at the start of analyze() so that operator changes are
    picked up without a service restart.  Returns safe defaults if the DB
    is unavailable.
    """
    defaults: Dict[str, Any] = {
        "llm_hypothesis_enabled":  True,
        "neo4j_topology_enabled":  True,
    }
    try:
        from agentic_os.db.database import SessionLocal
        db = SessionLocal()
        try:
            from sqlalchemy import text as _sql_text
            rows = db.execute(_sql_text("""
                SELECT key, value, value_type
                FROM   platform_settings
                WHERE  category = 'storm'
                  AND  key IN (
                           'storm.llm_hypothesis_enabled',
                           'storm.neo4j_topology_enabled'
                       )
            """)).fetchall()
            for row in rows:
                short_key = row[0].split(".", 1)[1]
                defaults[short_key] = row[1].lower() in ("true", "1", "yes")
        finally:
            db.close()
    except Exception as exc:
        logger.debug(f"[STORM AGENT] Could not load settings (using defaults): {exc}")
    return defaults

# Event pattern classification
PATTERN_MAP = [
    ({"service_unresponsive", "health_check_failed", "network_anomaly",
      "high_latency", "connection_spike", "service_down"},
     "network_partition"),
    ({"high_cpu", "high_memory", "disk_full", "high_syscall_intensity"},
     "resource_exhaustion"),
    ({"service_down", "service_unresponsive"},
     "service_cascade"),
]


class StormAgent:
    """
    Analyzes correlated incidents to identify storm root cause.

    Output schema:
        {
            "root_cause_candidates": [
                {
                    "name": "...",
                    "type": "...",
                    "affected_count": N,
                    "affected_resources": [...],
                    "criticality": "...",
                }
            ],
            "topology_evidence": {
                "resource_name": [{"name": ..., "type": ..., ...}]
            },
            "llm_hypothesis": "Human-readable root cause hypothesis",
            "affected_resources": [...],
            "event_type_pattern": "network_partition | resource_exhaustion | ...",
            "confidence": 0.0–1.0,
            "incident_count": N,
            "neo4j_available": bool,
            "llm_used": bool,
        }
    """

    def __init__(self):
        self._cmdb = None  # lazy init

    def _get_cmdb(self):
        if self._cmdb is None:
            from agentic_os.services.cmdb import CMDBService
            self._cmdb = CMDBService(
                uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
                user=os.getenv("NEO4J_USER", "neo4j"),
                password=os.getenv("NEO4J_PASSWORD"),
            )
        return self._cmdb

    # ── Public entry point ────────────────────────────────────────────────────

    def analyze(
        self,
        affected_resources: List[str],
        event_types: List[str],
        incident_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Run storm root cause analysis synchronously.

        Designed to be called from a Celery task (sync context).
        LLM call is made via asyncio.run() if an LLM is configured.
        Settings (llm_hypothesis_enabled, neo4j_topology_enabled) are read
        live from the platform_settings table so operator changes take effect
        without a service restart.
        """
        logger.info(
            f"[STORM AGENT] Analyzing storm: {len(incident_ids)} incidents, "
            f"{len(affected_resources)} resources, types={event_types}"
        )

        # Load live settings
        settings = _load_agent_settings()
        use_neo4j = settings["neo4j_topology_enabled"]
        use_llm   = settings["llm_hypothesis_enabled"]

        logger.info(
            f"[STORM AGENT] Settings: neo4j={use_neo4j}, llm={use_llm}"
        )

        # 1. Topology via Neo4j (gated by setting)
        if use_neo4j:
            topology, neo4j_ok = self._build_topology(affected_resources)
        else:
            logger.info("[STORM AGENT] Neo4j topology disabled — skipping")
            topology  = {r: [] for r in affected_resources}
            neo4j_ok  = False

        # 2. Common upstream → root cause candidates
        root_cause_candidates = self._find_common_upstream(topology, affected_resources)

        # 3. Event pattern
        pattern = self._classify_pattern(event_types)

        # 4. LLM hypothesis (gated by setting)
        hypothesis, llm_used = self._generate_hypothesis(
            affected_resources, event_types, root_cause_candidates, pattern,
            llm_enabled=use_llm,
        )

        # 5. Confidence
        confidence = self._confidence(root_cause_candidates, affected_resources, event_types)

        return {
            "root_cause_candidates": root_cause_candidates,
            "topology_evidence": topology,
            "llm_hypothesis": hypothesis,
            "affected_resources": affected_resources,
            "event_type_pattern": pattern,
            "confidence": confidence,
            "incident_count": len(incident_ids),
            "neo4j_available": neo4j_ok,
            "llm_used": llm_used,
        }

    # ── Topology ──────────────────────────────────────────────────────────────

    def _build_topology(
        self, resources: List[str]
    ) -> tuple[Dict[str, Any], bool]:
        """Query Neo4j dependencies for each resource. Returns (topology, neo4j_ok)."""
        cmdb = self._get_cmdb()
        topology: Dict[str, Any] = {}
        neo4j_ok = cmdb.driver is not None

        for resource in resources:
            try:
                deps = cmdb.get_dependencies(resource, depth=2)
                topology[resource] = deps
            except Exception as exc:
                logger.warning(f"[STORM AGENT] CMDB query failed for {resource}: {exc}")
                topology[resource] = []

        return topology, neo4j_ok

    def _find_common_upstream(
        self,
        topology: Dict[str, Any],
        affected_resources: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Find upstream CIs that appear in the dependency chain of 2+ affected resources.
        These are the most likely root cause candidates.
        """
        if len(affected_resources) < 2:
            return []

        upstream_hits: Dict[str, Dict[str, Any]] = {}
        for resource, deps in topology.items():
            for dep in deps:
                dep_name = dep.get("name", "")
                if not dep_name or dep_name in affected_resources:
                    continue  # skip self-references
                if dep_name not in upstream_hits:
                    upstream_hits[dep_name] = {
                        "name": dep_name,
                        "type": dep.get("type", "unknown"),
                        "criticality": dep.get("criticality", "unknown"),
                        "status": dep.get("status", "unknown"),
                        "environment": dep.get("environment", "unknown"),
                        "affected_count": 0,
                        "affected_resources": [],
                    }
                upstream_hits[dep_name]["affected_count"] += 1
                upstream_hits[dep_name]["affected_resources"].append(resource)

        # Only candidates shared by 2+ resources
        candidates = [
            info for info in upstream_hits.values()
            if info["affected_count"] >= 2
        ]
        candidates.sort(key=lambda x: x["affected_count"], reverse=True)
        return candidates[:5]  # cap at 5 for readability

    # ── Pattern classification ────────────────────────────────────────────────

    def _classify_pattern(self, event_types: List[str]) -> str:
        """Classify storm pattern from observed event types."""
        types = set(event_types)
        for group, label in PATTERN_MAP:
            if len(types & group) >= 1:
                return label
        if len(types) == 1:
            return f"distributed_{next(iter(types))}"
        return "mixed_signal_storm"

    # ── Hypothesis generation ─────────────────────────────────────────────────

    def _generate_hypothesis(
        self,
        affected_resources: List[str],
        event_types: List[str],
        root_cause_candidates: List[Dict[str, Any]],
        pattern: str,
        llm_enabled: bool = True,
    ) -> tuple[str, bool]:
        """Return (hypothesis_text, llm_used)."""
        # Try LLM first (if enabled by settings)
        if llm_enabled:
            try:
                hypothesis = self._llm_hypothesis(
                    affected_resources, event_types, root_cause_candidates, pattern
                )
                if hypothesis:
                    return hypothesis, True
            except Exception as exc:
                logger.warning(f"[STORM AGENT] LLM hypothesis skipped: {exc}")
        else:
            logger.info("[STORM AGENT] LLM hypothesis disabled via platform settings")

        # Rule-based fallback
        return self._rule_based_hypothesis(
            affected_resources, event_types, root_cause_candidates, pattern
        ), False

    def _llm_hypothesis(
        self,
        affected_resources: List[str],
        event_types: List[str],
        root_cause_candidates: List[Dict[str, Any]],
        pattern: str,
    ) -> Optional[str]:
        """
        Call the configured LLM to generate a storm root-cause hypothesis.

        Uses a dedicated storm prompt (not the generic single-incident summary)
        so the model reasons about shared infrastructure, container relationships,
        and platform context rather than summarising a single service outage.
        """
        from agentic_os.services.summary_service import get_summary_service
        from agentic_os.services.llm_provider import _resolve_service_role

        svc = get_summary_service()
        if not svc.is_provider_configured():
            return None

        # ── Infer application / infra grouping ───────────────────────────────
        # Find dominant name prefix (e.g. "agentic_os" shared by all containers).
        prefixes: Dict[str, int] = {}
        for r in affected_resources:
            parts = r.split("_")
            # Try two-part prefix (e.g. "agentic_os")
            prefix = "_".join(parts[:2]) if len(parts) >= 3 else parts[0]
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
        dominant_prefix = max(prefixes, key=prefixes.get) if prefixes else None
        coverage = prefixes.get(dominant_prefix, 0) / max(len(affected_resources), 1)
        same_app = coverage >= 0.6

        if same_app and dominant_prefix:
            app_context = (
                f"All {len(affected_resources)} affected services share the name "
                f"prefix '{dominant_prefix}', indicating they are components of the "
                f"same application deployed as a Docker Compose stack on a single "
                f"host VM. They share the host's disk, memory, and network namespace."
            )
        else:
            app_context = (
                f"{len(affected_resources)} services from potentially multiple "
                f"applications are affected."
            )

        # ── Detect shared-infrastructure pattern ─────────────────────────────
        type_set = set(event_types)
        dominant_type = max(type_set, key=lambda t: event_types.count(t)) if event_types else None
        all_same_type = len(type_set) == 1

        shared_infra_hint = ""
        if all_same_type and dominant_type == "disk_full" and len(affected_resources) >= 3:
            shared_infra_hint = (
                "CRITICAL INSIGHT: disk_full is reported by EVERY container in the "
                "stack simultaneously. When all containers on the same Docker host "
                "report disk_full at the same percentage, the HOST machine's physical "
                "disk (or the Docker data root partition) is full — not each container "
                "independently. Restarting or fixing individual containers will NOT "
                "resolve this; the host disk must be freed or expanded."
            )
        elif all_same_type and dominant_type == "memory_surge" and len(affected_resources) >= 3:
            shared_infra_hint = (
                "CRITICAL INSIGHT: memory_surge across the entire container stack "
                "simultaneously suggests host-level memory pressure, an OOM event, "
                "or a single runaway process consuming shared RAM (e.g. Java heap "
                "leak in a database container affecting available memory for all others)."
            )
        elif all_same_type and dominant_type in ("health_check_failed", "service_unresponsive") \
                and len(affected_resources) >= 3:
            shared_infra_hint = (
                "CRITICAL INSIGHT: health check failures across all services simultaneously "
                "point to a network-layer or host-level failure rather than individual "
                "service bugs — a network partition, DNS failure, or Docker daemon issue "
                "is more likely than coincident independent failures."
            )

        # ── Build per-resource role descriptions ─────────────────────────────
        resource_lines = []
        for r in affected_resources:
            role = _resolve_service_role(r)
            resource_lines.append(f"{r}: {role}")

        # ── Topology evidence ─────────────────────────────────────────────────
        if root_cause_candidates:
            top = root_cause_candidates[0]
            topo_context = (
                f"Neo4j CMDB topology (2-hop traversal) found '{top['name']}' "
                f"({top.get('type', 'infrastructure component')}, "
                f"criticality: {top.get('criticality', 'unknown')}) as a shared "
                f"upstream dependency for {top['affected_count']} of {len(affected_resources)} "
                f"affected resources."
            )
        else:
            topo_context = (
                "Neo4j CMDB topology (2-hop traversal) found NO common upstream "
                "service dependency — the root cause is most likely at the host "
                "or physical infrastructure layer (disk, RAM, network) rather than "
                "an application-level shared service."
            )

        storm_context = {
            "pattern":            pattern,
            "n_resources":        len(affected_resources),
            "resource_lines":     resource_lines,
            "event_types":        list(type_set),
            "all_same_type":      all_same_type,
            "dominant_type":      dominant_type,
            "topo_context":       topo_context,
            "app_context":        app_context,
            "shared_infra_hint":  shared_infra_hint,
        }

        # asyncio.run() creates, runs, and cleanly tears down a fresh event loop
        # (including flushing httpx connection-pool cleanup tasks).
        # Avoids the "Event loop is closed" errors that appear with the
        # manual asyncio.new_event_loop() / loop.close() pattern.
        result = asyncio.run(
            svc.provider.generate_storm_hypothesis(storm_context)
        )

        return result.strip() if result else None

    def _rule_based_hypothesis(
        self,
        affected_resources: List[str],
        event_types: List[str],
        root_cause_candidates: List[Dict[str, Any]],
        pattern: str,
    ) -> str:
        """Generate a deterministic hypothesis when LLM is unavailable."""
        n = len(affected_resources)
        res_list = ", ".join(affected_resources[:4])
        if n > 4:
            res_list += f" (+{n - 4} more)"

        if root_cause_candidates:
            top = root_cause_candidates[0]
            dep_name = top.get("name", "an upstream dependency")
            dep_type = top.get("type", "infrastructure component")
            return (
                f"A failure or degradation in '{dep_name}' ({dep_type}) is the most "
                f"probable root cause. It appears in the dependency chain of "
                f"{top['affected_count']} affected resource(s) ({res_list}). "
                f"Storm pattern: {pattern.replace('_', ' ')}. "
                f"Remediation should target '{dep_name}' rather than individual services."
            )

        pattern_text: Dict[str, str] = {
            "network_partition": (
                f"{n} resources ({res_list}) are simultaneously reporting connectivity "
                f"failures. This is consistent with a network-layer issue — potential "
                f"switch failure, load balancer misconfiguration, or DNS outage affecting "
                f"a shared network segment. Individual service restarts will not resolve this."
            ),
            "resource_exhaustion": (
                f"{n} resources ({res_list}) are simultaneously reporting resource "
                f"exhaustion. A shared storage substrate, memory pressure from a runaway "
                f"process, or a noisy-neighbour condition on a shared host is suspected. "
                f"Restarting individual services may provide temporary relief but will "
                f"not address the underlying cause."
            ),
            "service_cascade": (
                f"{n} services ({res_list}) have failed in rapid succession. "
                f"This cascade pattern suggests a shared dependency that failed first, "
                f"causing downstream services to become unresponsive. "
                f"Identify and restore the shared dependency before restarting consumers."
            ),
        }

        return pattern_text.get(
            pattern,
            f"{n} resources ({res_list}) are experiencing correlated failures "
            f"of type(s): {', '.join(set(event_types))}. "
            f"A shared infrastructure root cause is suspected. "
            f"Storm pattern: {pattern.replace('_', ' ')}. Manual investigation required."
        )

    # ── Confidence ────────────────────────────────────────────────────────────

    def _confidence(
        self,
        root_cause_candidates: List[Dict[str, Any]],
        affected_resources: List[str],
        event_types: List[str],
    ) -> float:
        """
        Calculate a confidence score 0.0–1.0 for the storm hypothesis.

        Increases with:
          - Strong topology evidence (common upstream dependency for many resources)
          - Type coherence (fewer distinct event types = more coherent)
          - Scale (more resources affected)
        """
        score = 0.5  # baseline: "probably a storm"

        # Topology evidence
        if root_cause_candidates:
            top = root_cause_candidates[0]
            frac = top["affected_count"] / max(len(affected_resources), 1)
            score += 0.30 * frac  # up to +0.30

        # Type coherence
        n_types = len(set(event_types))
        if n_types == 1:
            score += 0.15
        elif n_types <= 3:
            score += 0.05

        # Scale bonus
        if len(affected_resources) >= 5:
            score += 0.05

        # Mass same-type bonus: a single event type firing across ≥5 resources
        # simultaneously is extremely unlikely to be coincidental — much stronger
        # signal than the base +0.15 coherence covers.
        if n_types == 1 and len(affected_resources) >= 5:
            score += 0.20

        return min(round(score, 2), 1.0)
