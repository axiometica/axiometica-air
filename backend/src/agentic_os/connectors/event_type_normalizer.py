"""
Event-type normalizer for external connector alerts.

External monitoring tools use their own alert naming conventions.
This module translates them to the platform's canonical event types so that
the correct runbook is selected by MechanicAgent._lookup_runbook().

Resolution cascade (applied in order, first match wins):
  1. Exact match in operator-configured event_type_mappings dict
  2. Case-insensitive match in operator mappings
  3. Already a canonical platform event type — pass through unchanged
  4. Alias match — legacy flat type (e.g. 'high_cpu') maps to hierarchical code
  5. Keyword heuristics on raw type + hint text (title/alert name)
  6. LLM classification (async, optional — requires LLM configured)
  7. Return raw type as-is (best effort; may not match a runbook)

Usage (synchronous, in parse functions):
    from agentic_os.connectors.event_type_normalizer import normalize_event_type
    event_type = normalize_event_type(raw_type, config.get("event_type_mappings", {}), hint_text)

Usage (async, with LLM fallback):
    from agentic_os.connectors.event_type_normalizer import normalize_event_type_async
    event_type = await normalize_event_type_async(raw_type, mappings, hint_text)
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Taxonomy import ───────────────────────────────────────────────────────────
# Prefer the in-process data module; fall back to a minimal frozen set if the
# package import fails (e.g., during migration tooling runs).

try:
    from agentic_os.db.event_type_taxonomy_data import (
        CANONICAL_CODES as _CANONICAL_CODES,
        ALIAS_MAP       as _ALIAS_MAP,
    )
    CANONICAL_EVENT_TYPES: frozenset[str] = _CANONICAL_CODES
    _ALIAS_TO_CODE: dict[str, str]        = _ALIAS_MAP
except ImportError:  # pragma: no cover
    # Minimal fallback — covers legacy flat types only
    CANONICAL_EVENT_TYPES = frozenset({
        "infrastructure.compute.cpu_high",
        "infrastructure.compute.memory_high",
        "infrastructure.storage.disk_full",
        "application.availability.service_down",
        "application.availability.service_unresponsive",
        "application.performance.error_rate_high",
        "application.performance.latency_high",
        "database.availability.down",
        "database.connections.pool_exhausted",
        "network.tls.certificate_expiring",
        "container.pod.crash_looping",
        "infrastructure.compute.syscall_intensity_high",
        "application.messaging.queue_depth_critical",
        "log.error.spike",
        "custom",
    })
    _ALIAS_TO_CODE = {
        "high_cpu":                   "infrastructure.compute.cpu_high",
        "high_memory":                "infrastructure.compute.memory_high",
        "disk_full":                  "infrastructure.storage.disk_full",
        "service_down":               "application.availability.service_down",
        "service_unresponsive":       "application.availability.service_unresponsive",
        "high_error_rate":            "application.performance.error_rate_high",
        "high_latency":               "application.performance.latency_high",
        "database_error":             "database.availability.down",
        "db_connection_pool_exhausted":"database.connections.pool_exhausted",
        "certificate_expiry":         "network.tls.certificate_expiring",
        "pod_crash":                  "container.pod.crash_looping",
        "high_syscall_intensity":     "infrastructure.compute.syscall_intensity_high",
        "queue_depth_critical":       "application.messaging.queue_depth_critical",
        "log_error_detected":         "log.error.spike",
    }


# ── Keyword heuristics ────────────────────────────────────────────────────────
# Each entry: (set_of_keywords_all_must_match, canonical_type)
# Keywords are matched against the lowercased combination of raw_type + hint_text.
# More specific rules should come first.

_HEURISTICS: list[tuple[frozenset[str], str]] = [
    # CPU
    (frozenset({"cpu", "throttl"}),                  "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "high"}),                     "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "spike"}),                    "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "saturat"}),                  "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "overload"}),                 "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "usage"}),                    "infrastructure.compute.cpu_high"),
    (frozenset({"cpu", "iowait"}),                   "infrastructure.compute.cpu_iowait_high"),
    (frozenset({"cpu", "steal"}),                    "infrastructure.compute.cpu_steal_high"),
    (frozenset({"processor", "high"}),               "infrastructure.compute.cpu_high"),
    (frozenset({"load", "average", "high"}),         "infrastructure.compute.load_high"),
    # Memory
    (frozenset({"memory", "high"}),                  "infrastructure.compute.memory_high"),
    (frozenset({"memory", "pressure"}),              "infrastructure.compute.memory_high"),
    (frozenset({"memory", "leak"}),                  "infrastructure.compute.memory_high"),
    (frozenset({"oom", "kill"}),                     "infrastructure.compute.memory_oom_kill"),
    (frozenset({"oom"}),                             "infrastructure.compute.memory_oom_kill"),
    (frozenset({"out", "memory"}),                   "infrastructure.compute.memory_oom_kill"),
    (frozenset({"swap", "high"}),                    "infrastructure.compute.swap_high"),
    (frozenset({"heap", "high"}),                    "application.runtime.jvm_heap_high"),
    (frozenset({"jvm", "heap"}),                     "application.runtime.jvm_heap_high"),
    (frozenset({"gc", "pressure"}),                  "application.runtime.jvm_gc_pressure"),
    # Disk / Storage
    (frozenset({"disk", "full"}),                    "infrastructure.storage.disk_full"),
    (frozenset({"disk", "space"}),                   "infrastructure.storage.disk_full"),
    (frozenset({"storage", "full"}),                 "infrastructure.storage.disk_full"),
    (frozenset({"filesystem", "full"}),              "infrastructure.storage.disk_full"),
    (frozenset({"inode", "exhaust"}),                "infrastructure.storage.inode_exhausted"),
    (frozenset({"volume", "full"}),                  "infrastructure.storage.disk_full"),
    # disk_filling_fast — must come AFTER disk_full rules (more specific check)
    (frozenset({"disk", "will", "fill"}),            "infrastructure.storage.disk_filling_fast"),
    (frozenset({"disk", "4h"}),                      "infrastructure.storage.disk_filling_fast"),
    (frozenset({"disk", "24h"}),                     "infrastructure.storage.disk_filling_fast"),
    (frozenset({"disk", "io", "latency"}),           "infrastructure.storage.io_latency_high"),
    (frozenset({"disk", "io", "saturat"}),           "infrastructure.storage.io_saturation"),
    # Network (host-level)
    (frozenset({"interface", "down"}),               "infrastructure.network.interface_down"),
    (frozenset({"link", "down"}),                    "infrastructure.network.interface_down"),
    (frozenset({"packet", "loss"}),                  "infrastructure.network.packet_loss"),
    (frozenset({"bandwidth", "saturat"}),            "infrastructure.network.bandwidth_saturation"),
    (frozenset({"dns", "fail"}),                     "infrastructure.network.dns_failure"),
    (frozenset({"port", "unreachable"}),             "infrastructure.network.port_unreachable"),
    (frozenset({"bond", "degrad"}),                  "infrastructure.network.bond_degraded"),
    # Service down / unresponsive
    (frozenset({"service", "down"}),                 "application.availability.service_down"),
    (frozenset({"service", "unavailable"}),          "application.availability.service_down"),
    (frozenset({"container", "down"}),               "application.availability.service_down"),
    (frozenset({"process", "crash"}),                "application.availability.service_down"),
    (frozenset({"application", "down"}),             "application.availability.service_down"),
    (frozenset({"endpoint", "unreachable"}),         "application.availability.service_down"),
    (frozenset({"service", "unresponsive"}),         "application.availability.service_unresponsive"),
    (frozenset({"health", "check", "fail"}),         "application.availability.health_check_failing"),
    (frozenset({"readiness", "fail"}),               "application.availability.health_check_failing"),
    (frozenset({"liveness", "fail"}),                "application.availability.health_check_failing"),
    (frozenset({"circuit", "breaker"}),              "application.availability.circuit_breaker_open"),
    # Error rate
    (frozenset({"error", "rate", "high"}),           "application.performance.error_rate_high"),
    (frozenset({"error", "rate", "spike"}),          "application.performance.error_rate_spike"),
    (frozenset({"5xx", "high"}),                     "application.performance.error_rate_high"),
    (frozenset({"failure", "rate"}),                 "application.performance.error_rate_high"),
    # Latency
    (frozenset({"latency", "high"}),                 "application.performance.latency_high"),
    (frozenset({"latency", "spike"}),                "application.performance.latency_spike"),
    (frozenset({"response", "time", "high"}),        "application.performance.latency_high"),
    (frozenset({"slow", "response"}),                "application.performance.latency_high"),
    (frozenset({"p99", "high"}),                     "application.performance.latency_spike"),
    (frozenset({"timeout", "rate"}),                 "application.performance.timeout_rate_high"),
    (frozenset({"throughput", "low"}),               "application.performance.throughput_low"),
    (frozenset({"throughput", "drop"}),              "application.performance.throughput_drop"),
    # Deployment
    (frozenset({"deploy", "fail"}),                  "application.deployment.deploy_failed"),
    (frozenset({"rollback"}),                        "application.deployment.rollback_triggered"),
    # Messaging / Queues
    (frozenset({"queue", "depth"}),                  "application.messaging.queue_depth_critical"),
    (frozenset({"queue", "backlog"}),                "application.messaging.queue_depth_critical"),
    (frozenset({"consumer", "lag"}),                 "application.messaging.consumer_lag_high"),
    (frozenset({"dead", "letter"}),                  "application.messaging.dead_letter_high"),
    (frozenset({"broker", "down"}),                  "application.messaging.broker_down"),
    # Database
    (frozenset({"database", "down"}),                "database.availability.down"),
    (frozenset({"db", "down"}),                      "database.availability.down"),
    (frozenset({"database", "error"}),               "database.availability.down"),
    (frozenset({"postgres", "down"}),                "database.availability.down"),
    (frozenset({"mysql", "down"}),                   "database.availability.down"),
    (frozenset({"connection", "pool", "exhaust"}),   "database.connections.pool_exhausted"),
    (frozenset({"db", "connection", "limit"}),       "database.connections.max_connections_reached"),
    (frozenset({"max", "connection"}),               "database.connections.max_connections_reached"),
    (frozenset({"slow", "query"}),                   "database.performance.slow_query"),
    (frozenset({"deadlock"}),                        "database.performance.deadlock"),
    (frozenset({"lock", "contention"}),              "database.performance.lock_contention"),
    (frozenset({"replication", "lag"}),              "database.replication.lag_high"),
    (frozenset({"replica", "lag"}),                  "database.replication.lag_high"),
    (frozenset({"replication", "stop"}),             "database.replication.replica_not_running"),
    (frozenset({"table", "bloat"}),                  "database.storage.table_bloat_high"),
    (frozenset({"index", "bloat"}),                  "database.storage.index_bloat_high"),
    (frozenset({"backup", "fail"}),                  "database.storage.backup_failed"),
    (frozenset({"cache", "hit", "ratio"}),           "database.cache.hit_ratio_low"),
    (frozenset({"redis", "memory"}),                 "database.cache.memory_high"),
    (frozenset({"elasticsearch", "red"}),            "database.cluster.unassigned_shards"),
    # TLS / Certificates
    (frozenset({"cert", "expir"}),                   "network.tls.certificate_expiring"),
    (frozenset({"ssl", "expir"}),                    "network.tls.certificate_expiring"),
    (frozenset({"tls", "expir"}),                    "network.tls.certificate_expiring"),
    (frozenset({"cert", "invalid"}),                 "network.tls.certificate_invalid"),
    # Network devices / proxy
    (frozenset({"bgp", "down"}),                     "network.bgp.session_down"),
    (frozenset({"nginx", "5xx"}),                    "network.proxy.http5xx_rate_high"),
    (frozenset({"haproxy", "error"}),                "network.proxy.http5xx_rate_high"),
    (frozenset({"uptime", "probe"}),                 "network.uptime.probe_failed"),
    (frozenset({"blackbox", "fail"}),                "network.uptime.probe_failed"),
    # Kubernetes / Container
    (frozenset({"pod", "crash"}),                    "container.pod.crash_looping"),
    (frozenset({"pod", "restart"}),                  "container.pod.crash_looping"),
    (frozenset({"crashloopbackoff"}),                "container.pod.crash_looping"),
    (frozenset({"pod", "not", "ready"}),             "container.pod.not_ready"),
    (frozenset({"pod", "oom"}),                      "container.pod.oom_killed"),
    (frozenset({"image", "pull"}),                   "container.pod.image_pull_error"),
    (frozenset({"pod", "pending"}),                  "container.pod.pending_stuck"),
    (frozenset({"deployment", "replicas"}),          "container.deployment.replicas_mismatch"),
    (frozenset({"rollout", "stuck"}),                "container.deployment.rollout_stuck"),
    (frozenset({"node", "not", "ready"}),            "container.node.not_ready"),
    (frozenset({"node", "memory", "pressure"}),      "container.node.memory_pressure"),
    (frozenset({"node", "disk", "pressure"}),        "container.node.disk_pressure"),
    (frozenset({"pvc", "fill"}),                     "container.pvc.filling_up"),
    (frozenset({"pvc", "error"}),                    "container.pvc.errors"),
    (frozenset({"job", "fail"}),                     "container.job.failed"),
    (frozenset({"hpa", "max"}),                      "container.hpa.maxed_out"),
    (frozenset({"etcd", "leader"}),                  "container.controlplane.etcd_no_leader"),
    # Security
    (frozenset({"auth", "failure", "spike"}),        "security.auth.failure_spike"),
    (frozenset({"brute", "force"}),                  "security.auth.brute_force"),
    (frozenset({"credential", "stuff"}),             "security.auth.credential_stuffing"),
    (frozenset({"malware"}),                         "security.endpoint.malware_detected"),
    (frozenset({"ransomware"}),                      "security.endpoint.ransomware_behavior"),
    (frozenset({"port", "scan"}),                    "security.network.port_scan"),
    (frozenset({"lateral", "movement"}),             "security.network.lateral_movement"),
    (frozenset({"cve", "critical"}),                 "security.vulnerability.critical_cve_detected"),
    (frozenset({"cve", "high"}),                     "security.vulnerability.high_cve_detected"),
    # Log monitoring
    (frozenset({"syscall", "high"}),                 "infrastructure.compute.syscall_intensity_high"),
    (frozenset({"syscall", "rate"}),                 "infrastructure.compute.syscall_intensity_high"),
    (frozenset({"log", "error", "spike"}),           "log.error.spike"),
    (frozenset({"log", "pattern"}),                  "log.error.pattern_detected"),
    # SLO / Synthetic
    (frozenset({"slo", "burn"}),                     "synthetic.slo.error_budget_burn_fast"),
    (frozenset({"error", "budget"}),                 "synthetic.slo.error_budget_burn_fast"),
    (frozenset({"availability", "breach"}),          "synthetic.slo.availability_breach"),
    # Cloud-specific
    (frozenset({"ec2", "status", "check"}),          "cloud.aws.ec2.status_check_failed"),
    (frozenset({"rds", "storage"}),                  "cloud.aws.rds.storage_low"),
    (frozenset({"alb", "unhealthy"}),                "cloud.aws.alb.unhealthy_hosts"),
    (frozenset({"lambda", "throttl"}),               "cloud.aws.lambda.throttled"),
    (frozenset({"lambda", "error"}),                 "cloud.aws.lambda.error_rate_high"),
]


def _heuristic_match(text: str) -> Optional[str]:
    """Apply keyword heuristics. Returns first canonical match or None."""
    lower = text.lower()
    for keywords, canonical in _HEURISTICS:
        if all(kw in lower for kw in keywords):
            return canonical
    return None


def normalize_event_type(
    raw_type: str,
    mappings: dict[str, str],
    hint_text: str = "",
) -> str:
    """
    Synchronous event-type normalization (steps 1–5).

    Args:
        raw_type:   The event_type string from the connector parser.
        mappings:   Operator-configured dict from connector config_json
                    (key = external alert name, value = canonical platform type).
        hint_text:  Additional context — alert title or alert name — for
                    heuristic matching when raw_type alone is ambiguous.

    Returns:
        A canonical platform event type, or raw_type if no match found.
    """
    if not raw_type:
        return "custom"

    # Step 1: Exact match in operator mappings
    if raw_type in mappings:
        mapped = mappings[raw_type]
        logger.debug("event_type_normalizer: exact mapping '%s' → '%s'", raw_type, mapped)
        return mapped

    # Step 2: Case-insensitive match in operator mappings
    raw_lower = raw_type.lower()
    for k, v in mappings.items():
        if k.lower() == raw_lower:
            logger.debug("event_type_normalizer: ci mapping '%s' → '%s'", raw_type, v)
            return v

    # Step 3: Already canonical — pass through
    if raw_type in CANONICAL_EVENT_TYPES:
        return raw_type

    # Step 4: Alias / legacy flat-type lookup
    if raw_lower in _ALIAS_TO_CODE:
        resolved = _ALIAS_TO_CODE[raw_lower]
        logger.debug("event_type_normalizer: alias '%s' → '%s'", raw_type, resolved)
        return resolved

    # Step 5: Keyword heuristics on combined text
    combined = raw_type + " " + hint_text
    match = _heuristic_match(combined)
    if match:
        logger.info(
            "event_type_normalizer: heuristic '%s' → '%s' (hint=%r)",
            raw_type, match, hint_text[:60],
        )
        return match

    # No match — return as-is
    logger.debug(
        "event_type_normalizer: no match for '%s' (hint=%r) — using raw value",
        raw_type, hint_text[:60],
    )
    return raw_type


async def normalize_event_type_async(
    raw_type: str,
    mappings: dict[str, str],
    hint_text: str = "",
    timeout_seconds: float = 3.0,
) -> str:
    """
    Async event-type normalization (steps 1–6, includes LLM fallback).

    Runs the synchronous cascade first.  If the result is still not canonical,
    attempts LLM classification with a timeout.  Falls back to the synchronous
    result if LLM is unavailable or times out.
    """
    # Run synchronous steps first
    sync_result = normalize_event_type(raw_type, mappings, hint_text)

    # If already resolved to a canonical type, no LLM needed
    if sync_result in CANONICAL_EVENT_TYPES:
        return sync_result

    # Step 6: LLM classification
    try:
        import asyncio
        llm_result = await asyncio.wait_for(
            _classify_with_llm(raw_type, hint_text),
            timeout=timeout_seconds,
        )
        if llm_result and llm_result in CANONICAL_EVENT_TYPES:
            logger.info(
                "event_type_normalizer: LLM classified '%s' → '%s'",
                raw_type, llm_result,
            )
            return llm_result
    except Exception as llm_err:
        logger.debug("event_type_normalizer: LLM fallback skipped (%s)", llm_err)

    return sync_result


async def _classify_with_llm(raw_type: str, hint_text: str) -> Optional[str]:
    """
    Use the platform's LLM to classify an unknown alert type into the canonical taxonomy.
    Returns a canonical event type string or None.
    """
    try:
        from agentic_os.services.llm_client import get_llm_client
        client = get_llm_client()
        if not client or not client.is_configured():
            return None
    except Exception:
        return None

    # Build a grouped list of canonical codes to keep the prompt readable
    try:
        from agentic_os.db.event_type_taxonomy_data import ALL_ENTRIES
        by_domain: dict[str, list[str]] = {}
        for entry in ALL_ENTRIES:
            by_domain.setdefault(entry["category"], []).append(entry["code"])
        canonical_grouped = "\n".join(
            f"  {domain}: {', '.join(codes)}"
            for domain, codes in sorted(by_domain.items())
        )
    except Exception:
        canonical_grouped = ", ".join(sorted(CANONICAL_EVENT_TYPES))

    prompt = (
        "Classify this monitoring alert into exactly one canonical event type from the list below.\n"
        "Reply with ONLY the event type code string, nothing else.\n\n"
        f"Canonical types by domain:\n{canonical_grouped}\n\n"
        f"Alert name / raw type: {raw_type}\n"
        f"Alert title/description: {hint_text or '(none)'}\n\n"
        "If none fit, reply with: custom"
    )

    try:
        response = await client.complete_async(
            prompt=prompt,
            max_tokens=30,
            temperature=0.0,
        )
        result = response.strip().lower().replace('"', "").replace("'", "")
        if result in CANONICAL_EVENT_TYPES:
            return result
        return None
    except Exception as e:
        logger.debug("event_type_normalizer: LLM call failed: %s", e)
        return None
