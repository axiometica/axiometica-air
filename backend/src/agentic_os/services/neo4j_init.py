"""
Neo4j CMDB initialization — schema v3.1

Node label hierarchy (every node carries :ConfigurationItem + one sub-label):

  :ConfigurationItem
    :Application   — top-level application umbrella
    :Service       — logical service (microservice, web app, queue, …)
      :Database    — data-tier service (postgres, redis, neo4j, …)
    :Server        — physical / virtual host
    :Container     — Docker container (sub-label added by discovery agent)

Relationship types
──────────────────
  :DEPENDS_ON   Service → Service     logical call dependency
  :HOSTED_ON    Service → Server      service lives on this host
  :RUNS_ON      Container → Server    container placed on host (added by discovery)
  :PART_OF      * → Application       ownership / membership

AgenticPlatform stack (single service, all CIs map to running containers)
──────────────────────────────────────────────────────────────────────────
  Application  agentic-platform           (logical umbrella — no container)
  Server       agenticplatform-host       (Docker host — no container)
  Services     agentic_os_backend         docker container: agentic_os_backend
               agentic_os_celery_worker   docker container: agentic_os_celery_worker
               agentic-frontend           React dev/build — no running container
  Databases    agentic_os_postgres        docker container: agentic_os_postgres
               agentic_os_neo4j           docker container: agentic_os_neo4j
               agentic_os_redis           docker container: agentic_os_redis
  Monitoring   sentinel_senses            docker container: sentinel_senses
               watcher_brain              docker container: watcher_brain

CI names match Docker container names so the discovery agent reconciles
live metrics directly onto the seeded nodes with no mapping layer needed.
"""

import os

from neo4j import GraphDatabase
import logging

logger = logging.getLogger(__name__)

CMDB_SCHEMA_VERSION = "3.2"


def seed_neo4j_database(
    uri: str = None,
    user: str = None,
    password: str = None,
):
    """
    Seed Neo4j with the v3.1 AgenticPlatform CMDB schema.

    Bumping CMDB_SCHEMA_VERSION triggers a full wipe + re-seed so all
    labels, properties, and relationships land cleanly on a fresh install.
    """
    # Default to environment variables if not provided
    uri = uri or os.getenv("NEO4J_URI", os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687"))
    user = user or os.getenv("NEO4J_USER", "neo4j")
    password = password or os.getenv("NEO4J_PASSWORD")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        logger.info("✓ Connected to Neo4j")

        with driver.session() as session:
            # ── Version check — skip if already at this version ───────────────
            rec = session.run(
                "MATCH (sv:SchemaVersion) RETURN sv.version AS version"
            ).single()
            current = rec["version"] if rec else None

            if current == CMDB_SCHEMA_VERSION:
                n = session.run(
                    "MATCH (ci:ConfigurationItem) RETURN COUNT(ci) AS n"
                ).single()["n"]
                logger.info(
                    f"✓ Neo4j already seeded (schema v{current}) — {n} CIs"
                )
                driver.close()
                return

            if current:
                logger.info(
                    f"⚙ Schema {current} → {CMDB_SCHEMA_VERSION}: wiping and re-seeding…"
                )
                session.run("MATCH (n) DETACH DELETE n")

            logger.info(f"⚙ Seeding CMDB v{CMDB_SCHEMA_VERSION}…")

            # ── Constraints ───────────────────────────────────────────────────
            session.run(
                "CREATE CONSTRAINT unique_ci_name IF NOT EXISTS "
                "FOR (ci:ConfigurationItem) REQUIRE ci.name IS UNIQUE"
            )

            # =================================================================
            # INFRASTRUCTURE — server host
            # =================================================================

            session.run("""
                CREATE (:ConfigurationItem:Server {
                    name:                 'agenticplatform-host',
                    type:                 'linux-server',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              1,
                    business_criticality: 'tier_1',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     60,
                    user_count:           500,
                    sla_percent:          99.5,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'Docker host running the AgenticPlatform stack'
                })
            """)

            # =================================================================
            # APPLICATION — logical umbrella
            # =================================================================

            session.run("""
                CREATE (:ConfigurationItem:Application {
                    name:                 'agentic-platform',
                    type:                 'application',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              1,
                    business_criticality: 'tier_1',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     15,
                    user_count:           500,
                    sla_percent:          99.0,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'AgenticPlatform — AI-driven ITSM automation'
                })
            """)

            # =================================================================
            # SERVICES — container names match Docker container names exactly
            # =================================================================

            # Backend API  (container: agentic_os_backend)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'agentic_os_backend',
                    type:                 'microservice',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              2,
                    business_criticality: 'tier_2',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     10,
                    user_count:           500,
                    sla_percent:          99.0,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'FastAPI backend — ITSM and workflow REST API'
                })
            """)

            # Celery worker  (container: agentic_os_celery_worker)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'agentic_os_celery_worker',
                    type:                 'microservice',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              2,
                    business_criticality: 'tier_2',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     10,
                    user_count:           500,
                    sla_percent:          99.0,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'Celery async worker — executes incident agents and remediations'
                })
            """)

            # Frontend  (no running container — React dev/build served by host)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'agentic-frontend',
                    type:                 'web-application',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              2,
                    business_criticality: 'tier_2',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              false,
                    avg_mttr_minutes:     10,
                    user_count:           500,
                    sla_percent:          99.0,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'React dashboard — ITSM UI for incidents, changes, CMDB'
                })
            """)

            # =================================================================
            # DATABASES  (:Service:Database)
            # =================================================================

            _dbs = [
                (
                    "agentic_os_postgres",
                    "database",
                    "PostgreSQL — workflow state, approvals, event log",
                ),
                (
                    "agentic_os_neo4j",
                    "graph-database",
                    "Neo4j — CMDB graph, CI topology and relationships",
                ),
                (
                    "agentic_os_redis",
                    "cache",
                    "Redis — Celery broker, session cache",
                ),
            ]
            for name, svc_type, desc in _dbs:
                session.run("""
                    CREATE (:ConfigurationItem:Service:Database {
                        name:                 $name,
                        type:                 $type,
                        status:               'operational',
                        owner:                'platform-team',
                        environment:          'production',
                        ci_tier:              3,
                        business_criticality: 'tier_3',
                        compliance_scope:     'general',
                        failover_available:   false,
                        is_spof:              true,
                        avg_mttr_minutes:     20,
                        user_count:           500,
                        sla_percent:          99.5,
                        support_group:        'platform-ops',
                        assignment_group:     'platform-ops',
                        managed_by:           'platform-team',
                        data_center:          'production-dc',
                        discovery_source:     'manually_seeded',
                        description:          $desc
                    })
                """, name=name, type=svc_type, desc=desc)

            # =================================================================
            # MONITORING AGENTS
            # =================================================================

            # Sentinel Senses  (container: sentinel_senses)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'sentinel_senses',
                    type:                 'monitoring-agent',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              3,
                    business_criticality: 'tier_3',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     5,
                    user_count:           500,
                    sla_percent:          99.95,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'Sentinel Senses — eBPF kernel monitoring agent'
                })
            """)

            # Watcher Brain  (container: watcher_brain)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'watcher_brain',
                    type:                 'monitoring-agent',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              3,
                    business_criticality: 'tier_3',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     5,
                    user_count:           500,
                    sla_percent:          99.95,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'Watcher Brain — anomaly detection and incident orchestrator'
                })
            """)

            # Celery Flower  (container: agentic_os_flower, port 5555)
            session.run("""
                CREATE (:ConfigurationItem:Service {
                    name:                 'agentic_os_flower',
                    type:                 'monitoring-ui',
                    status:               'operational',
                    owner:                'platform-team',
                    environment:          'production',
                    ci_tier:              3,
                    business_criticality: 'tier_3',
                    compliance_scope:     'general',
                    failover_available:   false,
                    is_spof:              true,
                    avg_mttr_minutes:     5,
                    user_count:           100,
                    sla_percent:          99.0,
                    support_group:        'platform-ops',
                    assignment_group:     'platform-ops',
                    managed_by:           'platform-team',
                    data_center:          'production-dc',
                    discovery_source:     'manually_seeded',
                    description:          'Celery Flower — real-time task queue monitoring dashboard (port 5555)'
                })
            """)

            # =================================================================
            # RELATIONSHIPS
            # =================================================================

            def rel(cypher, **params):
                session.run(cypher, **params)

            # ── PART_OF — everything belongs to agentic-platform ──────────────
            for svc in (
                "agenticplatform-host",
                "agentic_os_backend",
                "agentic_os_celery_worker",
                "agentic-frontend",
                "agentic_os_postgres",
                "agentic_os_neo4j",
                "agentic_os_redis",
                "sentinel_senses",
                "watcher_brain",
                "agentic_os_flower",
            ):
                session.run(
                    "MATCH (s:ConfigurationItem {name:$s}), "
                    "(a:ConfigurationItem {name:'agentic-platform'}) "
                    "CREATE (s)-[:PART_OF]->(a)",
                    s=svc,
                )

            # ── HOSTED_ON — services run on the agenticplatform-host ──────────
            for svc in (
                "agentic_os_backend",
                "agentic_os_celery_worker",
                "agentic-frontend",
                "agentic_os_postgres",
                "agentic_os_neo4j",
                "agentic_os_redis",
                "sentinel_senses",
                "watcher_brain",
                "agentic_os_flower",
            ):
                session.run(
                    "MATCH (s:ConfigurationItem {name:$s}), "
                    "(h:ConfigurationItem {name:'agenticplatform-host'}) "
                    "CREATE (s)-[:HOSTED_ON]->(h)",
                    s=svc,
                )

            # ── DEPENDS_ON — internal call dependencies ───────────────────────
            _deps = [
                ("agentic-frontend",       "agentic_os_backend"),
                ("agentic_os_backend",     "agentic_os_postgres"),
                ("agentic_os_backend",     "agentic_os_neo4j"),
                ("agentic_os_backend",     "agentic_os_redis"),
                ("agentic_os_backend",     "sentinel_senses"),
                ("agentic_os_backend",     "watcher_brain"),
                ("agentic_os_celery_worker", "agentic_os_postgres"),
                ("agentic_os_celery_worker", "agentic_os_redis"),
                ("agentic_os_flower",      "agentic_os_redis"),   # Flower monitors the Redis broker
            ]
            for src, tgt in _deps:
                session.run(
                    "MATCH (a:ConfigurationItem {name:$s}), "
                    "(b:ConfigurationItem {name:$t}) "
                    "CREATE (a)-[:DEPENDS_ON]->(b)",
                    s=src, t=tgt,
                )

            # ── Schema version marker ─────────────────────────────────────────
            session.run(
                "CREATE (:SchemaVersion {version:$v, "
                "created_at:datetime(), updated_at:datetime()})",
                v=CMDB_SCHEMA_VERSION,
            )

            n = session.run(
                "MATCH (ci:ConfigurationItem) RETURN COUNT(ci) AS n"
            ).single()["n"]
            logger.info(
                f"✓ Neo4j CMDB v{CMDB_SCHEMA_VERSION} seeded — "
                f"{n} CIs, {len(_deps)} DEPENDS_ON relationships"
            )

        driver.close()

    except Exception as e:
        logger.error(f"✗ Failed to seed Neo4j: {e}")


# Backward-compat alias — callers that imported initialize_neo4j still work
initialize_neo4j = seed_neo4j_database
