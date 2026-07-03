-- Common Runbooks - Seed Data
-- 10 inactive templates for most common incident scenarios
-- These serve as templates that can be adapted for specific services

-- 1. HIGH CPU - Diagnostic analysis and process termination
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440101'::uuid,
  'High CPU - Scale and Throttle',
  'Handles elevated CPU usage through comprehensive diagnostics and targeted process termination',
  'high_cpu',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Check CPU per core", "description": "Analyze CPU utilization across all cores", "tool": "check_cpu", "args_json": {}},
    {"order": 2, "type": "diagnostic", "name": "Identify hot processes", "description": "Find top 10 processes consuming CPU", "tool": "top_processes", "args_json": {"limit": 10, "sort_by": "cpu"}},
    {"order": 3, "type": "diagnostic", "name": "Get process details", "description": "Detailed info on the top CPU consumer", "tool": "get_process_info", "args_json": {}},
    {"order": 4, "type": "diagnostic", "name": "Query CPU metrics", "description": "Check CPU trends from metrics endpoint", "tool": "query_metrics", "args_json": {"metrics": ["cpu_usage_percent", "load_average"]}},
    {"order": 5, "type": "diagnostic", "name": "Check application logs", "description": "Review recent log entries for errors", "tool": "get_logs", "args_json": {"lines": 50, "level": "error"}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Kill CPU hog process", "description": "Terminate the highest CPU consuming process", "tool": "process_kill", "args_json": {"signal": "SIGTERM"}},
    {"order": 2, "type": "remediation", "name": "Restart service", "description": "Gracefully restart the affected service", "tool": "restart_service", "args_json": {"graceful": true, "timeout_seconds": 30}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify CPU reduced", "description": "Check CPU usage is below 70%", "metric": "cpu_usage_percent", "threshold": 70},
    {"order": 2, "name": "Verify process restarted", "description": "Service should be responding", "metric": "service_health", "check": "healthy"}
  ]'::jsonb,
  0.75, 1, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 2. HIGH MEMORY - Clear caches and restart
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440102'::uuid,
  'High Memory - Clear and Restart',
  'Handles memory pressure by clearing caches and restarting pods',
  'high_memory',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Analyze memory usage", "description": "Check heap usage and growth rate", "tool": "memory_profiler", "args": {"show_allocations": true}},
    {"order": 2, "type": "diagnostic", "name": "Check cache hit rates", "description": "Verify caching efficiency", "tool": "cache_stats", "args": {"cache_types": ["redis", "memcached", "process"]}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Clear application caches", "description": "Flush in-process and distributed caches", "tool": "cache_clear", "args": {"targets": ["all"]}},
    {"order": 2, "type": "remediation", "name": "Clear Redis cache", "description": "Flush Redis to free memory", "tool": "redis_flush", "args": {"db": "*", "async": false}},
    {"order": 3, "type": "remediation", "name": "Trigger garbage collection", "description": "Force full GC to free unused objects", "tool": "gc_trigger", "args": {"full": true}},
    {"order": 4, "type": "remediation", "name": "Rolling pod restart", "description": "Restart pods one at a time with drain", "tool": "pod_restart", "args": {"strategy": "rolling", "max_unavailable": "10%"}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify memory freed", "description": "Check memory usage below 80%", "metric": "memory_usage_percent", "threshold": 80},
    {"order": 2, "name": "Verify pod health", "description": "All pods should be ready", "metric": "pod_ready_count", "check": "equals_replicas"}
  ]'::jsonb,
  0.88, 3, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 3. DISK FULL - Cleanup logs and temp files
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440103'::uuid,
  'Disk Full - Cleanup Logs and Temps',
  'Handles disk space issues by cleaning up logs, temp files, and old data',
  'disk_full',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Analyze disk usage", "description": "Identify which directories are consuming space", "tool": "disk_usage_analysis", "args": {"top_n": 10, "min_size_mb": 100}},
    {"order": 2, "type": "diagnostic", "name": "Check log sizes", "description": "Find largest log files", "tool": "log_analysis", "args": {"min_size_mb": 50}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Compress old logs", "description": "Gzip logs older than 7 days", "tool": "log_compression", "args": {"older_than_days": 7}},
    {"order": 2, "type": "remediation", "name": "Delete temp files", "description": "Remove /tmp and cache directories", "tool": "temp_cleanup", "args": {"dirs": ["/tmp", "/var/tmp", "/dev/shm"]}},
    {"order": 3, "type": "remediation", "name": "Truncate old logs", "description": "Remove logs older than 30 days", "tool": "log_rotation", "args": {"older_than_days": 30}},
    {"order": 4, "type": "remediation", "name": "Clean package manager cache", "description": "Remove apt/yum/apk caches", "tool": "package_cache_clean", "args": {}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify disk usage", "description": "Check disk usage below 75%", "metric": "disk_usage_percent", "threshold": 75},
    {"order": 2, "name": "Verify services running", "description": "All services should be operational", "metric": "service_health_check", "check": "all_healthy"}
  ]'::jsonb,
  0.90, 1, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 4. DATABASE CONNECTION POOL EXHAUSTED - Reset connections
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440104'::uuid,
  'DB Connection Pool Exhausted - Reset',
  'Handles database connection pool exhaustion by resetting connections and increasing limits',
  'db_connection_pool_exhausted',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Check connection pool status", "description": "Analyze active, idle, and waiting connections", "tool": "db_connection_analysis", "args": {"show_queries": true}},
    {"order": 2, "type": "diagnostic", "name": "Identify long-running queries", "description": "Find queries holding connections", "tool": "query_analyzer", "args": {"min_duration_sec": 30}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Kill idle connections", "description": "Terminate idle database connections", "tool": "db_command", "args": {"command": "kill_idle_connections", "idle_seconds": 300}},
    {"order": 2, "type": "remediation", "name": "Kill long-running queries", "description": "Cancel queries running > 5 minutes", "tool": "db_command", "args": {"command": "kill_queries", "older_than_minutes": 5}},
    {"order": 3, "type": "remediation", "name": "Increase pool size", "description": "Temporarily increase connection pool", "tool": "connection_pool_config", "args": {"pool_size": 200, "max_overflow": 50}},
    {"order": 4, "type": "remediation", "name": "Restart database clients", "description": "Restart application connection pools", "tool": "service_restart", "args": {"graceful": true, "drain_timeout": 30}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify connections available", "description": "Check pool has available connections", "metric": "available_connections", "threshold": 10},
    {"order": 2, "name": "Verify query latency", "description": "Check queries completing normally", "metric": "query_latency_p95", "threshold": 5000}
  ]'::jsonb,
  0.89, 2, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 5. SERVICE UNRESPONSIVE — Smart process-aware restart
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440105'::uuid,
  'Service Unresponsive - Process Signal & Restart',
  'Smart remediation: signals the specific process on the failing port. SIGTERM first (graceful shutdown, Docker restart policy revives it), then SIGKILL, then full container restart as last resort. Process and container auto-discovered from alert context.',
  'service_unresponsive',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Review alert context", "description": "Check process name, failure reason (hung vs crashed), port, and container from the alert payload", "tool": "log_analysis", "args": {"source": "alert_context", "fields": ["process_name", "failure_reason", "port", "container", "check_url"]}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Graceful SIGTERM", "description": "Send SIGTERM to the process — graceful shutdown. Docker restart policy will revive it.", "tool": "process_signal", "args": {"process_name": "{process_name}", "container": "{container}", "signal": "SIGTERM"}},
    {"order": 2, "type": "remediation", "name": "Force SIGKILL", "description": "Force-kill the process if SIGTERM was insufficient.", "tool": "process_signal", "args": {"process_name": "{process_name}", "container": "{container}", "signal": "SIGKILL"}},
    {"order": 3, "type": "remediation", "name": "Container restart (last resort)", "description": "Full container restart if process signals failed or no process was found.", "tool": "pod_restart", "args": {"container": "{container}", "force": true}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Service responding on port", "description": "HTTP health probe should return expected status", "metric": "http_status", "threshold": 200}
  ]'::jsonb,
  0.90, 2, true, NOW(), NOW()
) ON CONFLICT (id) DO UPDATE SET
  name               = EXCLUDED.name,
  description        = EXCLUDED.description,
  diagnostics        = EXCLUDED.diagnostics,
  actions            = EXCLUDED.actions,
  verification_steps = EXCLUDED.verification_steps,
  confidence         = EXCLUDED.confidence,
  enabled            = EXCLUDED.enabled,
  updated_at         = NOW();

-- 6. HIGH LATENCY - Traffic reroute and caching
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440106'::uuid,
  'High Latency - Reroute and Cache',
  'Handles high latency by rerouting traffic and improving caching',
  'high_latency',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Analyze latency distribution", "description": "Check p50, p95, p99 latencies", "tool": "latency_analysis", "args": {"percentiles": [50, 95, 99, 99.9]}},
    {"order": 2, "type": "diagnostic", "name": "Check dependency latencies", "description": "Profile upstream service calls", "tool": "trace_analysis", "args": {"sample_rate": 0.1}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Enable response caching", "description": "Cache GET requests for 60 seconds", "tool": "caching_policy", "args": {"ttl_seconds": 60, "methods": ["GET"]}},
    {"order": 2, "type": "remediation", "name": "Compress responses", "description": "Enable gzip compression", "tool": "compression", "args": {"enabled": true, "min_size_bytes": 1024}},
    {"order": 3, "type": "remediation", "name": "Reroute to secondary region", "description": "Shift traffic to less-loaded region", "tool": "traffic_routing", "args": {"primary_weight": 50, "secondary_weight": 50}},
    {"order": 4, "type": "remediation", "name": "Enable connection reuse", "description": "Enable HTTP keep-alive and connection pooling", "tool": "connection_pooling", "args": {"keep_alive": true, "pool_size": 100}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify latency improved", "description": "P95 latency should drop", "metric": "latency_p95", "check": "improved_by_percent", "threshold": 20},
    {"order": 2, "name": "Verify cache hit rate", "description": "Cache hits should increase", "metric": "cache_hit_rate", "threshold": 0.5}
  ]'::jsonb,
  0.82, 2, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 7. HIGH ERROR RATE - Circuit breaker and fallback
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440107'::uuid,
  'High Error Rate - Circuit Breaker',
  'Handles high error rates by enabling circuit breakers and fallback responses',
  'high_error_rate',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Analyze error types", "description": "Categorize errors by type and endpoint", "tool": "error_analysis", "args": {"top_endpoints": 10}},
    {"order": 2, "type": "diagnostic", "name": "Check upstream services", "description": "Verify downstream service health", "tool": "dependency_health", "args": {"timeout": 5}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Enable circuit breaker", "description": "Stop calling failing dependencies", "tool": "circuit_breaker", "args": {"failure_threshold": 0.5, "timeout_seconds": 30}},
    {"order": 2, "type": "remediation", "name": "Enable request fallback", "description": "Return cached/default responses", "tool": "fallback_policy", "args": {"use_cache": true, "default_response": true}},
    {"order": 3, "type": "remediation", "name": "Reduce request rate", "description": "Lower traffic to failing service", "tool": "rate_limiter", "args": {"reduce_by_percent": 50}},
    {"order": 4, "type": "remediation", "name": "Enable retry with backoff", "description": "Implement exponential backoff", "tool": "retry_policy", "args": {"max_retries": 3, "backoff_ms": 100}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify error rate reduced", "description": "Error rate should drop below 1%", "metric": "error_rate_percent", "threshold": 1},
    {"order": 2, "name": "Verify service recovery", "description": "Circuit breaker should open to failing service", "metric": "circuit_breaker_state", "check": "is_open"}
  ]'::jsonb,
  0.80, 2, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 8. QUEUE DEPTH CRITICAL - Scale workers
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440108'::uuid,
  'Queue Depth Critical - Scale Workers',
  'Handles message queue backlog by scaling workers and optimizing processing',
  'queue_depth_critical',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Analyze queue depth", "description": "Check pending messages and age", "tool": "queue_analysis", "args": {"queues": "all"}},
    {"order": 2, "type": "diagnostic", "name": "Check worker throughput", "description": "Measure messages processed per second", "tool": "worker_metrics", "args": {"include_latency": true}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Scale worker pods", "description": "Increase worker replicas by 100%", "tool": "kubectl_scale", "args": {"deployment": "worker", "scale_percent": 100}},
    {"order": 2, "type": "remediation", "name": "Optimize message processing", "description": "Increase batch size and parallel processing", "tool": "worker_config", "args": {"batch_size": 100, "concurrency": 50}},
    {"order": 3, "type": "remediation", "name": "Prioritize critical messages", "description": "Reorder queue to process critical items first", "tool": "queue_reorder", "args": {"priority_rules": "user_action > system_task"}},
    {"order": 4, "type": "remediation", "name": "Enable dead letter queue", "description": "Move failing messages to DLQ", "tool": "dlq_enable", "args": {"max_retries": 3}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify queue draining", "description": "Queue depth should decrease", "metric": "queue_depth", "check": "decreasing"},
    {"order": 2, "name": "Verify worker health", "description": "All workers should be processing", "metric": "worker_healthy_count", "check": "equals_replicas"}
  ]'::jsonb,
  0.86, 2, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 9. CERT EXPIRY SOON - Renew certificate
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440109'::uuid,
  'Certificate Expiry - Renew',
  'Proactively renews certificates before expiry to prevent outages',
  'certificate_expiry_soon',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Check certificate expiry", "description": "Verify all certs and remaining days", "tool": "cert_analysis", "args": {"check_domains": "all"}},
    {"order": 2, "type": "diagnostic", "name": "Verify ACME connectivity", "description": "Ensure cert authority is reachable", "tool": "connectivity_check", "args": {"targets": ["letsencrypt.org", "acme-v02.api.letsencrypt.org"]}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Request new certificate", "description": "Initiate ACME renewal", "tool": "acme_renewal", "args": {"provider": "letsencrypt", "method": "dns01"}},
    {"order": 2, "type": "remediation", "name": "Deploy certificate", "description": "Update ingress/load balancer with new cert", "tool": "cert_deploy", "args": {"services": "all"}},
    {"order": 3, "type": "remediation", "name": "Verify SSL chain", "description": "Ensure full chain is installed", "tool": "ssl_verify", "args": {}},
    {"order": 4, "type": "remediation", "name": "Update monitoring", "description": "Reset cert expiry alert", "tool": "alert_update", "args": {"metric": "cert_expiry_days"}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify cert valid", "description": "Certificate should be valid for 90 days", "metric": "cert_validity_days", "threshold": 90},
    {"order": 2, "name": "Verify HTTPS working", "description": "HTTPS connections should work", "metric": "https_health_check", "check": "passing"}
  ]'::jsonb,
  0.95, 1, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- 10. DEPENDENCY SERVICE DOWN - Failover and fallback
INSERT INTO runbooks (id, name, description, event_type, service, environment, diagnostics, actions, verification_steps, confidence, blast_radius, enabled, created_at, updated_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440110'::uuid,
  'Dependency Service Down - Failover',
  'Handles downstream service failures with failover and graceful degradation',
  'dependency_service_down',
  NULL,
  'prod',
  '[
    {"order": 1, "type": "diagnostic", "name": "Verify service is truly down", "description": "Multiple health checks from different locations", "tool": "multi_location_health_check", "args": {"locations": ["us-east", "us-west", "eu"], "timeout": 5}},
    {"order": 2, "type": "diagnostic", "name": "Check for alternative backends", "description": "Identify available fallback services", "tool": "service_discovery", "args": {"filter": "healthy"}}
  ]'::jsonb,
  '[
    {"order": 1, "type": "remediation", "name": "Remove from load balancer", "description": "Stop routing traffic to failed service", "tool": "lb_remove_backend", "args": {"graceful_drain": 30}},
    {"order": 2, "type": "remediation", "name": "Failover to backup service", "description": "Route traffic to secondary instance", "tool": "traffic_reroute", "args": {"primary_weight": 0, "secondary_weight": 100}},
    {"order": 3, "type": "remediation", "name": "Enable service degradation", "description": "Use cached/offline mode if available", "tool": "degradation_mode", "args": {"mode": "cached", "ttl": 3600}},
    {"order": 4, "type": "remediation", "name": "Alert operations team", "description": "Page on-call engineer for investigation", "tool": "alert_escalate", "args": {"severity": "critical", "service": "dependency"}}
  ]'::jsonb,
  '[
    {"order": 1, "name": "Verify traffic rerouted", "description": "Traffic should flow to healthy backend", "metric": "backup_service_request_count", "threshold": 1},
    {"order": 2, "name": "Verify user experience", "description": "Degraded mode should be functional", "metric": "degraded_mode_response_latency", "threshold": 2000}
  ]'::jsonb,
  0.92, 3, false, NOW(), NOW()
) ON CONFLICT DO NOTHING;

-- Verify all runbooks were inserted
SELECT COUNT(*) as total_runbooks, SUM(CASE WHEN enabled = true THEN 1 ELSE 0 END) as active, SUM(CASE WHEN enabled = false THEN 1 ELSE 0 END) as inactive FROM runbooks;
