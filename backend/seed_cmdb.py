#!/usr/bin/env python3
"""
Seed Neo4j CMDB with AgenticPlatform v2 infrastructure data.

Uses MERGE throughout — idempotent, safe to re-run at any time.

Usage (inside Docker network):
    docker exec -i agentic_os_neo4j cypher-shell -u neo4j -p password \
        < backend/scripts/neo4j_seed.cypher

Or run this Python script directly from within the Docker network:
    docker exec agentic_os_backend python /app/scripts/seed_cmdb.py
"""

import logging
import sys
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── CI definitions ────────────────────────────────────────────────────────
# Each dict maps exactly to what CMDBService.get_resource_info() returns.
# Names must match Docker container names (what the Watcher sends as resource_name).

CONFIGURATION_ITEMS = [
    {
        "name": "agentic_os_backend",
        "type": "microservice",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "AgenticOS FastAPI backend — REST API, WebSocket, agent pipeline orchestration",
        "ci_tier": 1,
        "business_criticality": "tier_1",
        "user_count": 10000,
        "is_spof": True,
        "sla_percent": 99.9,
        "failover_available": False,
        "compliance_scope": "SOC2",
    },
    {
        "name": "agentic_os_postgres",
        "type": "database",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "PostgreSQL — workflow state store, event sourcing, approvals, audit trail",
        "ci_tier": 1,
        "business_criticality": "tier_1",
        "user_count": 10000,
        "is_spof": True,
        "sla_percent": 99.9,
        "failover_available": False,
        "compliance_scope": "SOC2",
    },
    {
        "name": "agentic_os_redis",
        "type": "cache",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Redis — Celery task broker and result backend, API caching layer",
        "ci_tier": 1,
        "business_criticality": "tier_1",
        "user_count": 10000,
        "is_spof": True,
        "sla_percent": 99.5,
        "failover_available": False,
        "compliance_scope": "internal",
    },
    {
        "name": "agentic_os_neo4j",
        "type": "database",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Neo4j — CMDB, infrastructure topology and dependency graph",
        "ci_tier": 2,
        "business_criticality": "tier_2",
        "user_count": 5000,
        "is_spof": True,
        "sla_percent": 99.0,
        "failover_available": False,
        "compliance_scope": "internal",
    },
    {
        "name": "agentic_os_celery_worker",
        "type": "worker",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Celery worker — executes incident/change workflow tasks asynchronously",
        "ci_tier": 1,
        "business_criticality": "tier_1",
        "user_count": 10000,
        "is_spof": False,
        "sla_percent": 99.5,
        "failover_available": True,
        "compliance_scope": "internal",
    },
    {
        "name": "agentic_os_frontend",
        "type": "frontend",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "React/nginx frontend — operator dashboard, incident management UI",
        "ci_tier": 2,
        "business_criticality": "tier_2",
        "user_count": 500,
        "is_spof": False,
        "sla_percent": 99.0,
        "failover_available": False,
        "compliance_scope": "internal",
    },
    {
        "name": "agentic_os_flower",
        "type": "monitoring",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Celery Flower — real-time task queue monitoring UI",
        "ci_tier": 3,
        "business_criticality": "tier_3",
        "user_count": 50,
        "is_spof": False,
        "sla_percent": 95.0,
        "failover_available": False,
        "compliance_scope": "internal",
    },
    {
        "name": "sentinel_senses",
        "type": "monitoring",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Sentinel — privileged eBPF container, kernel syscall telemetry for all containers on host",
        "ci_tier": 2,
        "business_criticality": "tier_2",
        "user_count": 10000,
        "is_spof": True,
        "sla_percent": 99.0,
        "failover_available": False,
        "compliance_scope": "internal",
    },
    {
        "name": "watcher_brain",
        "type": "ai-agent",
        "status": "operational",
        "owner": "platform-team",
        "environment": "production",
        "description": "Watcher Brain — polls Sentinel + Docker stats, orchestrates automated incident response",
        "ci_tier": 1,
        "business_criticality": "tier_1",
        "user_count": 10000,
        "is_spof": True,
        "sla_percent": 99.5,
        "failover_available": False,
        "compliance_scope": "internal",
    },
]

# ── Dependency relationships ──────────────────────────────────────────────
# (consumer, dependency) — consumer DEPENDS_ON dependency
DEPENDENCIES = [
    # Backend depends on postgres, redis, neo4j
    ("agentic_os_backend",       "agentic_os_postgres"),
    ("agentic_os_backend",       "agentic_os_redis"),
    ("agentic_os_backend",       "agentic_os_neo4j"),
    # Celery worker depends on redis (broker), postgres, neo4j (CMDB in agents), backend
    ("agentic_os_celery_worker", "agentic_os_redis"),
    ("agentic_os_celery_worker", "agentic_os_postgres"),
    ("agentic_os_celery_worker", "agentic_os_neo4j"),
    ("agentic_os_celery_worker", "agentic_os_backend"),
    # Frontend proxies all API calls through nginx to backend
    ("agentic_os_frontend",      "agentic_os_backend"),
    # Flower monitors the Redis broker
    ("agentic_os_flower",        "agentic_os_redis"),
    # Watcher consumes Sentinel telemetry, submits events to backend, indirectly uses redis
    ("watcher_brain",            "sentinel_senses"),
    ("watcher_brain",            "agentic_os_backend"),
    ("watcher_brain",            "agentic_os_redis"),
]

# ── Historical incidents ──────────────────────────────────────────────────
INCIDENTS = [
    {
        "id": "INC-AGT-001",
        "resource": "agentic_os_backend",
        "severity": "high",
        "description": "High CPU during incident storm — 15 concurrent workflows overwhelmed workers",
        "root_cause": "Celery concurrency limit too low; tasks queuing on backend threads",
        "resolved_at": "2026-05-10T11:30:00Z",
        "resolution_time_minutes": 25,
    },
    {
        "id": "INC-AGT-002",
        "resource": "agentic_os_postgres",
        "severity": "critical",
        "description": "PostgreSQL connection pool exhausted under high workflow load",
        "root_cause": "Missing index on workflow_states.lifecycle_state — sequential scans at scale",
        "resolved_at": "2026-05-09T08:15:00Z",
        "resolution_time_minutes": 40,
    },
    {
        "id": "INC-AGT-003",
        "resource": "agentic_os_redis",
        "severity": "high",
        "description": "Redis hit maxmemory limit — Celery result keys evicted, tasks lost",
        "root_cause": "Result backend keys not expiring; task results accumulating over days",
        "resolved_at": "2026-05-08T16:00:00Z",
        "resolution_time_minutes": 15,
    },
    {
        "id": "INC-AGT-004",
        "resource": "sentinel_senses",
        "severity": "medium",
        "description": "bpftrace itself generating high syscall count during tracing",
        "root_cause": "bpftrace 5-second sampling window accumulating too many kernel events at once",
        "resolved_at": "2026-05-07T13:45:00Z",
        "resolution_time_minutes": 10,
    },
    {
        "id": "INC-AGT-005",
        "resource": "agentic_os_neo4j",
        "severity": "medium",
        "description": "Neo4j query timeout during mass incident triage (10 concurrent CMDB lookups)",
        "root_cause": "Missing index on ConfigurationItem.name — full graph scan per lookup",
        "resolved_at": "2026-05-06T09:20:00Z",
        "resolution_time_minutes": 20,
    },
    {
        "id": "INC-AGT-006",
        "resource": "agentic_os_celery_worker",
        "severity": "high",
        "description": "Celery worker OOM killed repeatedly under heavy LLM workload",
        "root_cause": "LLM provider response caching missing; each incident loaded full model context",
        "resolved_at": "2026-05-05T17:00:00Z",
        "resolution_time_minutes": 30,
    },
]


def seed(session) -> None:
    # ── Unique constraint ─────────────────────────────────────────────────
    session.run(
        "CREATE CONSTRAINT ci_name_unique IF NOT EXISTS "
        "FOR (ci:ConfigurationItem) REQUIRE ci.name IS UNIQUE"
    )
    logger.info("✓ Constraint ready")

    # ── ConfigurationItems ────────────────────────────────────────────────
    for ci in CONFIGURATION_ITEMS:
        session.run(
            """
            MERGE (ci:ConfigurationItem {name: $name})
            SET   ci.type                 = $type,
                  ci.status               = $status,
                  ci.owner                = $owner,
                  ci.environment          = $environment,
                  ci.description          = $description,
                  ci.ci_tier              = $ci_tier,
                  ci.business_criticality = $business_criticality,
                  ci.user_count           = $user_count,
                  ci.is_spof              = $is_spof,
                  ci.sla_percent          = $sla_percent,
                  ci.failover_available   = $failover_available,
                  ci.compliance_scope     = $compliance_scope
            """,
            **ci,
        )
        logger.info(f"  ✓ CI: {ci['name']} ({ci['business_criticality']}, tier {ci['ci_tier']})")

    logger.info(f"✓ {len(CONFIGURATION_ITEMS)} configuration items upserted")

    # ── Dependencies ──────────────────────────────────────────────────────
    for consumer, dependency in DEPENDENCIES:
        session.run(
            """
            MATCH (a:ConfigurationItem {name: $consumer}),
                  (b:ConfigurationItem {name: $dependency})
            MERGE (a)-[:DEPENDS_ON]->(b)
            """,
            consumer=consumer,
            dependency=dependency,
        )
    logger.info(f"✓ {len(DEPENDENCIES)} dependency relationships upserted")

    # ── Historical incidents ──────────────────────────────────────────────
    for inc in INCIDENTS:
        session.run(
            """
            MERGE (inc:Incident {id: $id})
            SET   inc.resource                = $resource,
                  inc.severity                = $severity,
                  inc.description             = $description,
                  inc.root_cause              = $root_cause,
                  inc.resolved_at             = $resolved_at,
                  inc.resolution_time_minutes = $resolution_time_minutes
            """,
            **inc,
        )
        session.run(
            """
            MATCH (ci:ConfigurationItem {name: $resource}),
                  (inc:Incident {id: $id})
            MERGE (ci)-[:AFFECTED_BY]->(inc)
            """,
            resource=inc["resource"],
            id=inc["id"],
        )
    logger.info(f"✓ {len(INCIDENTS)} historical incidents upserted")


def main():
    uri = "bolt://neo4j:7687"
    user = "neo4j"
    password = "password"

    logger.info(f"Connecting to Neo4j at {uri} ...")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        logger.info("✓ Connected")
    except Exception as exc:
        logger.error(f"✗ Cannot connect to Neo4j: {exc}")
        sys.exit(1)

    with driver.session() as session:
        seed(session)

    driver.close()
    logger.info("")
    logger.info("═══════════════════════════════════════")
    logger.info("✅ CMDB seeded successfully")
    logger.info(f"   {len(CONFIGURATION_ITEMS)} CIs  |  {len(DEPENDENCIES)} deps  |  {len(INCIDENTS)} incidents")
    logger.info("   All 6 risk fields present on every CI")
    logger.info("═══════════════════════════════════════")


if __name__ == "__main__":
    main()
