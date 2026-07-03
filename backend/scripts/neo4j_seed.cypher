// ═══════════════════════════════════════════════════════════════════════════
// Neo4j CMDB Seed — AgenticPlatform v2
//
// Models the actual running platform: Docker container names are used as CI
// names because that is exactly what the Watcher submits as resource_name.
//
// Idempotent — uses MERGE throughout, safe to re-run at any time.
//
// Run with:
//   docker exec -i agentic_os_neo4j cypher-shell -u neo4j -p password \
//     < backend/scripts/neo4j_seed.cypher
// ═══════════════════════════════════════════════════════════════════════════


// ── 1. Unique constraint (creates index automatically) ────────────────────
CREATE CONSTRAINT ci_name_unique IF NOT EXISTS
FOR (ci:ConfigurationItem) REQUIRE ci.name IS UNIQUE;


// ── 2. ConfigurationItem nodes ────────────────────────────────────────────
//
// Required risk fields (queried by EventQualificationService):
//   ci_tier              : int   1=critical, 2=important, 3=standard
//   business_criticality : str   'tier_1' | 'tier_2' | 'tier_3'
//   user_count           : int   users/workflows impacted if this CI fails
//   is_spof              : bool  single point of failure?
//   sla_percent          : float target availability (e.g. 99.9)
//   failover_available   : bool  automatic failover exists?


// FastAPI backend — core API, WebSocket, agent orchestration
MERGE (backend:ConfigurationItem {name: 'agentic_os_backend'})
SET   backend.type                 = 'microservice',
      backend.status               = 'operational',
      backend.owner                = 'platform-team',
      backend.environment          = 'production',
      backend.description          = 'AgenticOS FastAPI backend — REST API, WebSocket, agent pipeline orchestration',
      backend.ci_tier              = 1,
      backend.business_criticality = 'tier_1',
      backend.user_count           = 10000,
      backend.is_spof              = true,
      backend.sla_percent          = 99.9,
      backend.failover_available   = false,
      backend.compliance_scope     = 'SOC2';

// PostgreSQL — event sourcing, workflow state, approvals, audit trail
MERGE (postgres:ConfigurationItem {name: 'agentic_os_postgres'})
SET   postgres.type                 = 'database',
      postgres.status               = 'operational',
      postgres.owner                = 'platform-team',
      postgres.environment          = 'production',
      postgres.description          = 'PostgreSQL — workflow state store, event sourcing, approvals, audit trail',
      postgres.ci_tier              = 1,
      postgres.business_criticality = 'tier_1',
      postgres.user_count           = 10000,
      postgres.is_spof              = true,
      postgres.sla_percent          = 99.9,
      postgres.failover_available   = false,
      postgres.compliance_scope     = 'SOC2';

// Redis — Celery broker + result backend, caching
MERGE (redis:ConfigurationItem {name: 'agentic_os_redis'})
SET   redis.type                 = 'cache',
      redis.status               = 'operational',
      redis.owner                = 'platform-team',
      redis.environment          = 'production',
      redis.description          = 'Redis — Celery task broker and result backend, API caching layer',
      redis.ci_tier              = 1,
      redis.business_criticality = 'tier_1',
      redis.user_count           = 10000,
      redis.is_spof              = true,
      redis.sla_percent          = 99.5,
      redis.failover_available   = false,
      redis.compliance_scope     = 'internal';

// Neo4j — Configuration Management Database, dependency graph
MERGE (neo4j:ConfigurationItem {name: 'agentic_os_neo4j'})
SET   neo4j.type                 = 'database',
      neo4j.status               = 'operational',
      neo4j.owner                = 'platform-team',
      neo4j.environment          = 'production',
      neo4j.description          = 'Neo4j — CMDB, infrastructure topology, dependency graph (this database)',
      neo4j.ci_tier              = 2,
      neo4j.business_criticality = 'tier_2',
      neo4j.user_count           = 5000,
      neo4j.is_spof              = true,
      neo4j.sla_percent          = 99.0,
      neo4j.failover_available   = false,
      neo4j.compliance_scope     = 'internal';

// Celery worker — background workflow and agent execution
MERGE (worker:ConfigurationItem {name: 'agentic_os_celery_worker'})
SET   worker.type                 = 'worker',
      worker.status               = 'operational',
      worker.owner                = 'platform-team',
      worker.environment          = 'production',
      worker.description          = 'Celery worker — executes incident/change workflow tasks asynchronously',
      worker.ci_tier              = 1,
      worker.business_criticality = 'tier_1',
      worker.user_count           = 10000,
      worker.is_spof              = false,
      worker.sla_percent          = 99.5,
      worker.failover_available   = true,
      worker.compliance_scope     = 'internal';

// React frontend — operator dashboard (nginx)
MERGE (frontend:ConfigurationItem {name: 'agentic_os_frontend'})
SET   frontend.type                 = 'frontend',
      frontend.status               = 'operational',
      frontend.owner                = 'platform-team',
      frontend.environment          = 'production',
      frontend.description          = 'React/nginx frontend — operator dashboard, incident management UI',
      frontend.ci_tier              = 2,
      frontend.business_criticality = 'tier_2',
      frontend.user_count           = 500,
      frontend.is_spof              = false,
      frontend.sla_percent          = 99.0,
      frontend.failover_available   = false,
      frontend.compliance_scope     = 'internal';

// Celery Flower — task queue monitoring UI
MERGE (flower:ConfigurationItem {name: 'agentic_os_flower'})
SET   flower.type                 = 'monitoring',
      flower.status               = 'operational',
      flower.owner                = 'platform-team',
      flower.environment          = 'production',
      flower.platform             = 'linux',
      flower.description          = 'Celery Flower — real-time task queue monitoring UI (port 5555)',
      flower.ci_tier              = 3,
      flower.business_criticality = 'tier_3',
      flower.user_count           = 50,
      flower.is_spof              = false,
      flower.sla_percent          = 95.0,
      flower.failover_available   = false,
      flower.compliance_scope     = 'internal';

// Sentinel — eBPF kernel-level syscall monitor
MERGE (sentinel:ConfigurationItem {name: 'sentinel_senses'})
SET   sentinel.type                 = 'monitoring',
      sentinel.status               = 'operational',
      sentinel.owner                = 'platform-team',
      sentinel.environment          = 'production',
      sentinel.description          = 'Sentinel — privileged eBPF container, kernel syscall telemetry for all containers on host',
      sentinel.ci_tier              = 2,
      sentinel.business_criticality = 'tier_2',
      sentinel.user_count           = 10000,
      sentinel.is_spof              = true,
      sentinel.sla_percent          = 99.0,
      sentinel.failover_available   = false,
      sentinel.compliance_scope     = 'internal';

// Watcher — AI-driven incident orchestration brain
MERGE (watcher:ConfigurationItem {name: 'watcher_brain'})
SET   watcher.type                 = 'ai-agent',
      watcher.status               = 'operational',
      watcher.owner                = 'platform-team',
      watcher.environment          = 'production',
      watcher.description          = 'Watcher Brain — polls Sentinel + Docker stats, orchestrates automated incident response',
      watcher.ci_tier              = 1,
      watcher.business_criticality = 'tier_1',
      watcher.user_count           = 10000,
      watcher.is_spof              = true,
      watcher.sla_percent          = 99.5,
      watcher.failover_available   = false,
      watcher.compliance_scope     = 'internal';


// ── 3. Dependency relationships (DEPENDS_ON) ─────────────────────────────
//
// Direction: (consumer)-[:DEPENDS_ON]->(dependency)
//
// get_dependencies()      traverses OUT  → what does this CI rely on?
// get_impacted_services() traverses IN   → what breaks if this CI fails?

// Backend depends on: postgres, redis, neo4j
MATCH (a:ConfigurationItem {name: 'agentic_os_backend'}),
      (b:ConfigurationItem {name: 'agentic_os_postgres'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'agentic_os_backend'}),
      (b:ConfigurationItem {name: 'agentic_os_redis'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'agentic_os_backend'}),
      (b:ConfigurationItem {name: 'agentic_os_neo4j'})
MERGE (a)-[:DEPENDS_ON]->(b);

// Celery worker depends on: redis (broker), postgres (state), neo4j (CMDB lookups in agents), backend (health checks)
MATCH (a:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (b:ConfigurationItem {name: 'agentic_os_redis'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (b:ConfigurationItem {name: 'agentic_os_postgres'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (b:ConfigurationItem {name: 'agentic_os_neo4j'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (b:ConfigurationItem {name: 'agentic_os_backend'})
MERGE (a)-[:DEPENDS_ON]->(b);

// Frontend depends on: backend (all API calls proxied through nginx)
MATCH (a:ConfigurationItem {name: 'agentic_os_frontend'}),
      (b:ConfigurationItem {name: 'agentic_os_backend'})
MERGE (a)-[:DEPENDS_ON]->(b);

// Flower depends on: redis (broker it monitors)
MATCH (a:ConfigurationItem {name: 'agentic_os_flower'}),
      (b:ConfigurationItem {name: 'agentic_os_redis'})
MERGE (a)-[:DEPENDS_ON]->(b);

// Flower — topology membership
MATCH (ci:ConfigurationItem {name: 'agentic_os_flower'}),
      (app:ConfigurationItem {name: 'agentic-platform'})
MERGE (ci)-[:PART_OF]->(app);

MATCH (ci:ConfigurationItem {name: 'agentic_os_flower'}),
      (host:ConfigurationItem {name: 'agenticplatform-host'})
MERGE (ci)-[:HOSTED_ON]->(host);

// Watcher depends on: sentinel (telemetry source), backend (event submission), redis (indirectly via Celery)
MATCH (a:ConfigurationItem {name: 'watcher_brain'}),
      (b:ConfigurationItem {name: 'sentinel_senses'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'watcher_brain'}),
      (b:ConfigurationItem {name: 'agentic_os_backend'})
MERGE (a)-[:DEPENDS_ON]->(b);

MATCH (a:ConfigurationItem {name: 'watcher_brain'}),
      (b:ConfigurationItem {name: 'agentic_os_redis'})
MERGE (a)-[:DEPENDS_ON]->(b);


// ── 4. Historical incidents ───────────────────────────────────────────────

MERGE (inc1:Incident {id: 'INC-AGT-001'})
SET   inc1.resource                = 'agentic_os_backend',
      inc1.severity                = 'high',
      inc1.description             = 'High CPU during incident storm — 15 concurrent workflows overwhelmed workers',
      inc1.root_cause              = 'Celery concurrency limit too low; tasks queuing on backend threads',
      inc1.resolved_at             = '2026-05-10T11:30:00Z',
      inc1.resolution_time_minutes = 25;

MERGE (inc2:Incident {id: 'INC-AGT-002'})
SET   inc2.resource                = 'agentic_os_postgres',
      inc2.severity                = 'critical',
      inc2.description             = 'PostgreSQL connection pool exhausted under high workflow load',
      inc2.root_cause              = 'Missing index on workflow_states.lifecycle_state — sequential scans at scale',
      inc2.resolved_at             = '2026-05-09T08:15:00Z',
      inc2.resolution_time_minutes = 40;

MERGE (inc3:Incident {id: 'INC-AGT-003'})
SET   inc3.resource                = 'agentic_os_redis',
      inc3.severity                = 'high',
      inc3.description             = 'Redis hit maxmemory limit — Celery result keys evicted, tasks lost',
      inc3.root_cause              = 'Result backend keys not expiring; task results accumulating over days',
      inc3.resolved_at             = '2026-05-08T16:00:00Z',
      inc3.resolution_time_minutes = 15;

MERGE (inc4:Incident {id: 'INC-AGT-004'})
SET   inc4.resource                = 'sentinel_senses',
      inc4.severity                = 'medium',
      inc4.description             = 'bpftrace process itself generating high syscall count during tracing',
      inc4.root_cause              = 'bpftrace 5-second sampling window accumulating too many kernel events at once',
      inc4.resolved_at             = '2026-05-07T13:45:00Z',
      inc4.resolution_time_minutes = 10;

MERGE (inc5:Incident {id: 'INC-AGT-005'})
SET   inc5.resource                = 'agentic_os_neo4j',
      inc5.severity                = 'medium',
      inc5.description             = 'Neo4j query timeout during mass incident triage (10 concurrent CMDB lookups)',
      inc5.root_cause              = 'Missing index on ConfigurationItem.name — full graph scan per lookup',
      inc5.resolved_at             = '2026-05-06T09:20:00Z',
      inc5.resolution_time_minutes = 20;

MERGE (inc6:Incident {id: 'INC-AGT-006'})
SET   inc6.resource                = 'agentic_os_celery_worker',
      inc6.severity                = 'high',
      inc6.description             = 'Celery worker restarted repeatedly — OOM killed by kernel',
      inc6.root_cause              = 'LLM provider response caching missing; each incident loaded full model context',
      inc6.resolved_at             = '2026-05-05T17:00:00Z',
      inc6.resolution_time_minutes = 30;


// ── 5. Link incidents to affected CIs ────────────────────────────────────

MATCH (ci:ConfigurationItem {name: 'agentic_os_backend'}),
      (inc:Incident {id: 'INC-AGT-001'})
MERGE (ci)-[:AFFECTED_BY]->(inc);

MATCH (ci:ConfigurationItem {name: 'agentic_os_postgres'}),
      (inc:Incident {id: 'INC-AGT-002'})
MERGE (ci)-[:AFFECTED_BY]->(inc);

MATCH (ci:ConfigurationItem {name: 'agentic_os_redis'}),
      (inc:Incident {id: 'INC-AGT-003'})
MERGE (ci)-[:AFFECTED_BY]->(inc);

MATCH (ci:ConfigurationItem {name: 'sentinel_senses'}),
      (inc:Incident {id: 'INC-AGT-004'})
MERGE (ci)-[:AFFECTED_BY]->(inc);

MATCH (ci:ConfigurationItem {name: 'agentic_os_neo4j'}),
      (inc:Incident {id: 'INC-AGT-005'})
MERGE (ci)-[:AFFECTED_BY]->(inc);

MATCH (ci:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (inc:Incident {id: 'INC-AGT-006'})
MERGE (ci)-[:AFFECTED_BY]->(inc);


// ── 6. Remediation playbooks ──────────────────────────────────────────────

MERGE (pb1:Playbook {id: 'PB-AGT-001'})
SET   pb1.name               = 'Process Kill — High Syscall Intensity',
      pb1.applies_to         = 'microservice',
      pb1.incident_type      = 'high_syscall_intensity',
      pb1.steps              = ['Identify offending process via bpftrace telemetry',
                                'Confirm process is running in target container via pgrep',
                                'Send SIGKILL via pkill through Watcher kill-API',
                                'Verify process terminated (pgrep exit 1)',
                                'Monitor syscall rate returns below threshold'],
      pb1.success_rate       = 0.94,
      pb1.estimated_time_min = 2,
      pb1.last_executed      = '2026-05-10T10:00:00Z';

MERGE (pb2:Playbook {id: 'PB-AGT-002'})
SET   pb2.name               = 'CPU Spike — Identify and Terminate Runaway Process',
      pb2.applies_to         = 'microservice',
      pb2.incident_type      = 'high_cpu',
      pb2.steps              = ['Get top CPU process via docker stats',
                                'Verify process is not expected infrastructure process',
                                'Graceful SIGTERM first; SIGKILL if still running after 10s',
                                'Check CPU returns below threshold',
                                'Review logs for root cause'],
      pb2.success_rate       = 0.91,
      pb2.estimated_time_min = 5,
      pb2.last_executed      = '2026-05-09T14:00:00Z';

MERGE (pb3:Playbook {id: 'PB-AGT-003'})
SET   pb3.name               = 'PostgreSQL — Connection Pool Recovery',
      pb3.applies_to         = 'database',
      pb3.incident_type      = 'high_cpu',
      pb3.steps              = ['Check pg_stat_activity for idle/blocked connections',
                                'Kill idle connections older than 5 minutes',
                                'Restart SQLAlchemy connection pool via backend restart',
                                'Run ANALYZE on high-traffic tables',
                                'Monitor connection count stabilises'],
      pb3.success_rate       = 0.89,
      pb3.estimated_time_min = 10,
      pb3.last_executed      = '2026-05-09T08:15:00Z';

MERGE (pb4:Playbook {id: 'PB-AGT-004'})
SET   pb4.name               = 'Redis — Memory Pressure Recovery',
      pb4.applies_to         = 'cache',
      pb4.incident_type      = 'high_memory',
      pb4.steps              = ['Check redis INFO memory for used_memory vs maxmemory',
                                'Scan for large keys with SCAN + OBJECT ENCODING',
                                'Flush expired Celery result keys (pattern: celery-task-meta-*)',
                                'Restart Redis if memory still critical',
                                'Monitor memory usage over next 5 minutes'],
      pb4.success_rate       = 0.93,
      pb4.estimated_time_min = 5,
      pb4.last_executed      = '2026-05-08T16:00:00Z';

MERGE (pb5:Playbook {id: 'PB-AGT-005'})
SET   pb5.name               = 'Service Unresponsive — Health Check Recovery',
      pb5.applies_to         = 'microservice',
      pb5.incident_type      = 'service_unresponsive',
      pb5.steps              = ['Verify service is actually unreachable (not transient)',
                                'Check container status via docker inspect',
                                'Tail last 50 lines of container logs for crash reason',
                                'Attempt graceful restart via docker restart',
                                'Verify /health endpoint returns 200',
                                'Alert if restart fails — escalate to manual'],
      pb5.success_rate       = 0.87,
      pb5.estimated_time_min = 8,
      pb5.last_executed      = '2026-05-07T09:00:00Z';

MERGE (pb6:Playbook {id: 'PB-AGT-006'})
SET   pb6.name               = 'Celery Worker — Memory Surge Recovery',
      pb6.applies_to         = 'worker',
      pb6.incident_type      = 'high_memory',
      pb6.steps              = ['Check active task count and memory per task',
                                'Revoke long-running tasks exceeding memory limit',
                                'Restart worker with reduced concurrency',
                                'Monitor task queue depth after restart',
                                'Review task for memory leak if recurring'],
      pb6.success_rate       = 0.88,
      pb6.estimated_time_min = 7,
      pb6.last_executed      = '2026-05-05T17:00:00Z';


// ── 7. Link playbooks to CIs ──────────────────────────────────────────────

MATCH (ci:ConfigurationItem {name: 'agentic_os_backend'}),
      (pb:Playbook {id: 'PB-AGT-001'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_backend'}),
      (pb:Playbook {id: 'PB-AGT-002'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_backend'}),
      (pb:Playbook {id: 'PB-AGT-005'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (pb:Playbook {id: 'PB-AGT-001'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_celery_worker'}),
      (pb:Playbook {id: 'PB-AGT-006'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_postgres'}),
      (pb:Playbook {id: 'PB-AGT-003'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_redis'}),
      (pb:Playbook {id: 'PB-AGT-004'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'sentinel_senses'}),
      (pb:Playbook {id: 'PB-AGT-001'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'watcher_brain'}),
      (pb:Playbook {id: 'PB-AGT-005'})
MERGE (ci)-[:CAN_USE]->(pb);

MATCH (ci:ConfigurationItem {name: 'agentic_os_frontend'}),
      (pb:Playbook {id: 'PB-AGT-005'})
MERGE (ci)-[:CAN_USE]->(pb);
