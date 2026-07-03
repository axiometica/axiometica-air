-- ─────────────────────────────────────────────────────────────────────────────
-- Approved actions for all disabled runbook tools.
-- Tools with real shell commands get command + command_variants.
-- Platform API tools (load balancer, feature flags, alerting) get no command
-- and will simulate — their description explains the required integration.
-- ─────────────────────────────────────────────────────────────────────────────

-- ════════════════════════════════════════════════════════
-- DISK FULL runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'disk_usage_analysis', 'Disk Usage Analysis',
  'Show disk space per filesystem and top directories consuming space.',
  'diagnostic', 1, false, true,
  'docker exec {container} df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20',
  '{"docker":"docker exec {container} df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- df -h {path}",
    "ssh":"ssh {host} df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"path","type":"string","required":false,"default":"/","description":"Root path to analyse"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'log_compression', 'Compress Old Logs',
  'Gzip log files older than a given number of days to reclaim disk space.',
  'remediation_safe', 1, false, true,
  'docker exec {container} find {path} -name "*.log" -mtime +{days} -exec gzip -k {} \;',
  '{"docker":"docker exec {container} find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;",
    "ssh":"ssh {host} find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"path","type":"string","required":false,"default":"/var/log","description":"Log directory"},
    {"name":"days","type":"integer","required":false,"default":"7","description":"Compress logs older than N days"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'temp_cleanup', 'Clean Temp Files',
  'Delete files from /tmp and /var/tmp older than a specified age.',
  'remediation_safe', 1, false, true,
  'docker exec {container} find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete',
  '{"docker":"docker exec {container} find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete",
    "ssh":"ssh {host} find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"older_than_minutes","type":"integer","required":false,"default":"60","description":"Delete files older than N minutes"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'log_rotation', 'Rotate / Delete Old Logs',
  'Delete log files older than a given number of days, or trigger logrotate.',
  'remediation_safe', 1, false, true,
  'docker exec {container} find {path} -name "*.log" -mtime +{days} -delete',
  '{"docker":"docker exec {container} find {path} -name \"*.log\" -mtime +{days} -delete",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- find {path} -name \"*.log\" -mtime +{days} -delete",
    "ssh":"ssh {host} find {path} -name \"*.log\" -mtime +{days} -delete"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"path","type":"string","required":false,"default":"/var/log","description":"Log directory"},
    {"name":"days","type":"integer","required":false,"default":"30","description":"Delete logs older than N days"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'package_cache_clean', 'Clean Package Manager Cache',
  'Remove apt/apk/yum cached packages to free disk space.',
  'remediation_safe', 1, false, true,
  'docker exec {container} sh -c "apt-get clean 2>/dev/null || apk cache clean 2>/dev/null || yum clean all 2>/dev/null"',
  '{"docker":"docker exec {container} sh -c \"apt-get clean 2>/dev/null || apk cache clean 2>/dev/null || yum clean all 2>/dev/null\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- sh -c \"apt-get clean 2>/dev/null || apk cache clean 2>/dev/null || yum clean all 2>/dev/null\"",
    "ssh":"ssh {host} sh -c \"apt-get clean 2>/dev/null || apk cache clean 2>/dev/null || yum clean all 2>/dev/null\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'compression', 'Compress Files',
  'Compress files matching a pattern using gzip to reclaim disk space.',
  'remediation_safe', 1, false, true,
  'docker exec {container} find {path} -name "{pattern}" -exec gzip {} \;',
  '{"docker":"docker exec {container} find {path} -name \"{pattern}\" -exec gzip {} \\;",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- find {path} -name \"{pattern}\" -exec gzip {} \\;",
    "ssh":"ssh {host} find {path} -name \"{pattern}\" -exec gzip {} \\;"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"path","type":"string","required":false,"default":"/var/log","description":"Directory to search"},
    {"name":"pattern","type":"string","required":false,"default":"*.log","description":"File glob pattern"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- HIGH MEMORY runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'memory_profiler', 'Memory Profiler',
  'Show memory usage breakdown: free memory, cached, swap, and virtual memory stats.',
  'diagnostic', 1, false, true,
  'docker exec {container} free -h && cat /proc/meminfo | grep -E "Mem|Swap|Cached"',
  '{"docker":"docker exec {container} free -h && cat /proc/meminfo | grep -E \"Mem|Swap|Cached\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- free -h",
    "ssh":"ssh {host} free -h && vmstat 1 3"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'cache_stats', 'Cache Statistics',
  'Show Redis cache hit/miss rates, memory usage, and keyspace info.',
  'diagnostic', 1, false, true,
  'docker exec {container} redis-cli info stats | grep -E "keyspace|hit|miss|used_memory"',
  '{"docker":"docker exec {container} redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\"",
    "ssh":"ssh {host} redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_redis","description":"Redis container name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'cache_clear', 'Clear Cache (current DB)',
  'Flush the current Redis database. Less destructive than redis_flush — only clears DB 0.',
  'remediation_safe', 2, true, true,
  'docker exec {container} redis-cli FLUSHDB',
  '{"docker":"docker exec {container} redis-cli FLUSHDB",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- redis-cli FLUSHDB",
    "ssh":"ssh {host} redis-cli FLUSHDB"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_redis","description":"Redis container name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'redis_flush', 'Flush Redis (all DBs)',
  'Flush all Redis databases. Use with caution — clears all cached data.',
  'remediation_intrusive', 3, true, true,
  'docker exec {container} redis-cli FLUSHALL',
  '{"docker":"docker exec {container} redis-cli FLUSHALL",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- redis-cli FLUSHALL",
    "ssh":"ssh {host} redis-cli FLUSHALL"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_redis","description":"Redis container name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'gc_trigger', 'Trigger Garbage Collection',
  'Send SIGUSR1 to the process to trigger GC (JVM, Python with jemalloc, etc.). Falls back to malloc_trim.',
  'remediation_safe', 1, false, true,
  'docker exec {container} kill -USR1 {process_name}',
  '{"docker":"docker exec {container} kill -USR1 {process_name}",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- kill -USR1 {process_name}",
    "ssh":"ssh {host} kill -USR1 {process_name}"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"process_name","type":"string","required":true,"description":"Process name (e.g. java, python3)"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- DB CONNECTION POOL runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'db_connection_analysis', 'DB Connection Analysis',
  'Show active, idle, and waiting database connections grouped by state.',
  'diagnostic', 1, false, true,
  'docker exec {container} psql -U {db_user} -d {db_name} -c "SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;"',
  '{"docker":"docker exec {container} psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\"",
    "ssh":"ssh {host} psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_postgres","description":"PostgreSQL container"},
    {"name":"db_user","type":"string","required":false,"default":"postgres","description":"DB username"},
    {"name":"db_name","type":"string","required":false,"default":"agentic_os","description":"Database name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'query_analyzer', 'Query Analyzer',
  'Show long-running queries and their duration to identify connection hogs.',
  'diagnostic', 1, false, true,
  'docker exec {container} psql -U {db_user} -d {db_name} -c "SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};"',
  '{"docker":"docker exec {container} psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\"",
    "ssh":"ssh {host} psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_postgres","description":"PostgreSQL container"},
    {"name":"db_user","type":"string","required":false,"default":"postgres","description":"DB username"},
    {"name":"db_name","type":"string","required":false,"default":"agentic_os","description":"Database name"},
    {"name":"limit","type":"integer","required":false,"default":"10","description":"Number of rows to return"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'db_command', 'DB Command',
  'Run a targeted PostgreSQL command: terminate idle connections or cancel long-running queries.',
  'remediation_safe', 2, true, true,
  'docker exec {container} psql -U {db_user} -d {db_name} -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';"',
  '{"docker":"docker exec {container} psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';\"",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';\"",
    "ssh":"ssh {host} psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_postgres","description":"PostgreSQL container"},
    {"name":"db_user","type":"string","required":false,"default":"postgres","description":"DB username"},
    {"name":"db_name","type":"string","required":false,"default":"agentic_os","description":"Database name"},
    {"name":"idle_seconds","type":"integer","required":false,"default":"300","description":"Terminate connections idle longer than N seconds"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- service_restart is an alias for restart_service used in the DB runbook
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'service_restart', 'Service Restart',
  'Restart a named service. Alias for restart_service used in DB connection runbook.',
  'remediation_safe', 2, false, true,
  'docker restart --time {timeout_sec} {target}',
  '{"docker":"docker restart --time {timeout_sec} {target}",
    "kubernetes":"kubectl rollout restart deployment/{target} -n {namespace}",
    "ssh":"ssh {host} systemctl restart {target}"}'::json,
  '[{"name":"target","type":"string","required":true,"description":"Container / service / deployment name"},
    {"name":"timeout_sec","type":"integer","required":false,"default":"10","description":"Graceful shutdown timeout"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- connection_pool_config — requires app-level config reload, no generic shell equivalent
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'connection_pool_config', 'Update Connection Pool Config',
  'Adjust connection pool size at runtime. Requires app-specific integration (pgBouncer, HikariCP env var, etc.). No generic shell command — simulates until integration is wired.',
  'remediation_safe', 2, true, true,
  NULL, NULL,
  '[{"name":"pool_size","type":"integer","required":true,"description":"New max pool size"},
    {"name":"max_overflow","type":"integer","required":false,"default":"10","description":"Max overflow connections"},
    {"name":"service","type":"string","required":true,"description":"Service to reconfigure"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- HIGH ERROR RATE runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'error_analysis', 'Error Rate Analysis',
  'Count and sample recent error log entries to quantify and characterise the failure rate.',
  'diagnostic', 1, false, true,
  'docker logs {container} --since {window_min}m 2>&1 | grep -cE "(ERROR|WARN|FATAL)"',
  '{"docker":"docker logs {container} --since {window_min}m 2>&1 | grep -cE \"(ERROR|WARN|FATAL)\"",
    "kubernetes":"kubectl logs {pod} -n {namespace} --since={window_min}m | grep -cE \"(ERROR|WARN|FATAL)\"",
    "ssh":"ssh {host} journalctl -u {service} --since \"{window_min} min ago\" | grep -cE \"(ERROR|WARN|FATAL)\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container / pod name"},
    {"name":"window_min","type":"integer","required":false,"default":"5","description":"Minutes to look back"},
    {"name":"service","type":"string","required":false,"default":"","description":"systemd service (SSH mode)"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'dependency_health', 'Dependency Health Check',
  'Probe upstream/downstream service health endpoints to identify cascading failures.',
  'diagnostic', 1, false, true,
  'curl -Is --connect-timeout {timeout_sec} {url} | head -1',
  '{"any":"curl -Is --connect-timeout {timeout_sec} {url} | head -1"}'::json,
  '[{"name":"url","type":"string","required":true,"description":"Health endpoint URL"},
    {"name":"timeout_sec","type":"integer","required":false,"default":"3","description":"Connect timeout seconds"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- These are platform-policy actions with no generic shell command
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'circuit_breaker', 'Enable Circuit Breaker',
  'Open a circuit breaker for a service dependency to stop cascading failures. Requires integration with Hystrix, Resilience4j, or feature flag service.',
  'remediation_intrusive', 2, true, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service to circuit-break"},
    {"name":"timeout_ms","type":"integer","required":false,"default":"5000","description":"Request timeout"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'fallback_policy', 'Enable Fallback Policy',
  'Switch a service to use its fallback/degraded-mode response. Requires feature flag or config integration.',
  'remediation_safe', 2, true, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"fallback_type","type":"string","required":false,"default":"cached","description":"cached | static | empty"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'rate_limiter', 'Apply Rate Limiting',
  'Throttle inbound request rate to protect a service. Requires NGINX, HAProxy, or API gateway integration.',
  'remediation_safe', 2, true, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service to rate-limit"},
    {"name":"rate_rps","type":"integer","required":false,"default":"100","description":"Max requests per second"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'retry_policy', 'Configure Retry Policy',
  'Adjust retry backoff/attempts for a service client. Requires app config or service mesh integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"max_retries","type":"integer","required":false,"default":"3","description":"Max retry attempts"},
    {"name":"backoff_ms","type":"integer","required":false,"default":"500","description":"Backoff interval ms"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- HIGH LATENCY runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'latency_analysis', 'Latency Analysis',
  'Measure actual end-to-end HTTP response time to a service endpoint.',
  'diagnostic', 1, false, true,
  'curl -o /dev/null -s -w "dns:%{time_namelookup} connect:%{time_connect} ttfb:%{time_starttransfer} total:%{time_total}\n" {url}',
  '{"any":"curl -o /dev/null -s -w \"dns:%{time_namelookup} connect:%{time_connect} ttfb:%{time_starttransfer} total:%{time_total}\\n\" {url}"}'::json,
  '[{"name":"url","type":"string","required":true,"description":"Target URL to measure"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'trace_analysis', 'Distributed Trace Analysis',
  'Query distributed tracing for slow spans on the affected service. Requires Jaeger, Zipkin, or OTEL backend integration.',
  'diagnostic', 1, false, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"min_duration_ms","type":"integer","required":false,"default":"1000","description":"Min span duration ms"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'caching_policy', 'Update Caching Policy',
  'Enable or tune caching for a service to reduce upstream latency. Requires app config or CDN integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service to configure"},
    {"name":"ttl_seconds","type":"integer","required":false,"default":"300","description":"Cache TTL seconds"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'traffic_routing', 'Traffic Routing',
  'Adjust traffic routing weights between service instances. Requires load balancer / service mesh integration.',
  'remediation_intrusive', 2, true, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"weight","type":"integer","required":false,"default":"50","description":"Traffic weight 0-100"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'connection_pooling', 'Tune Connection Pooling',
  'Adjust database or HTTP connection pool settings for a service. Requires app config integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"pool_size","type":"integer","required":false,"default":"20","description":"Pool size"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- QUEUE DEPTH runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'queue_analysis', 'Queue Depth Analysis',
  'Report current queue depth for a named Redis / Celery queue.',
  'diagnostic', 1, false, true,
  'docker exec {container} redis-cli LLEN {queue_name}',
  '{"docker":"docker exec {container} redis-cli LLEN {queue_name}",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- redis-cli LLEN {queue_name}",
    "ssh":"ssh {host} redis-cli LLEN {queue_name}"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_redis","description":"Redis container"},
    {"name":"queue_name","type":"string","required":true,"description":"Queue / list key name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'worker_metrics', 'Worker Metrics',
  'Show active Celery worker stats: active tasks, reserved, and revoked.',
  'diagnostic', 1, false, true,
  'docker exec {container} celery -A agentic_os.tasks inspect stats 2>/dev/null | head -40',
  '{"docker":"docker exec {container} celery -A agentic_os.tasks inspect stats 2>/dev/null | head -40",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- celery -A agentic_os.tasks inspect stats 2>/dev/null | head -40"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_celery_worker","description":"Celery worker container"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- kubectl_scale — direct kubectl alias (parallel to k8s_scale)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'kubectl_scale', 'kubectl Scale Deployment',
  'Scale a Kubernetes deployment to a given replica count.',
  'remediation_safe', 2, false, true,
  'kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}',
  '{"kubernetes":"kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}",
    "any":"kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}"}'::json,
  '[{"name":"deployment","type":"string","required":true,"description":"Deployment name"},
    {"name":"replicas","type":"integer","required":true,"description":"Target replica count"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'worker_config', 'Update Worker Config',
  'Adjust Celery worker concurrency or queue assignment. Requires container restart to take effect — triggers a rolling restart.',
  'remediation_safe', 2, true, true,
  'docker compose up -d --scale {worker_service}={count}',
  '{"docker":"docker compose up -d --scale {worker_service}={count}",
    "kubernetes":"kubectl scale deployment/{worker_service} --replicas={count} -n {namespace}"}'::json,
  '[{"name":"worker_service","type":"string","required":false,"default":"celery_worker","description":"Worker service name"},
    {"name":"count","type":"integer","required":true,"description":"Number of worker instances"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'queue_reorder', 'Reorder Queue Priority',
  'Move high-priority tasks to the front of a Redis queue. Requires knowledge of queue key structure.',
  'remediation_safe', 2, true, true, NULL, NULL,
  '[{"name":"queue_name","type":"string","required":true,"description":"Queue key name"},
    {"name":"priority_key","type":"string","required":false,"description":"Task key to promote"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'dlq_enable', 'Enable Dead Letter Queue',
  'Configure a dead letter queue for failed task routing. Requires Celery / RabbitMQ configuration integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"queue_name","type":"string","required":true,"description":"Source queue name"},
    {"name":"dlq_name","type":"string","required":false,"default":"dead_letter","description":"DLQ name"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- DEPENDENCY SERVICE DOWN runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'multi_location_health_check', 'Multi-Location Health Check',
  'Probe a service health endpoint from the watcher. For true multi-region checks, integrate with an external monitoring service.',
  'diagnostic', 1, false, true,
  'curl -Is --connect-timeout {timeout_sec} {url} | head -1',
  '{"any":"curl -Is --connect-timeout {timeout_sec} {url} | head -1"}'::json,
  '[{"name":"url","type":"string","required":true,"description":"Service health endpoint URL"},
    {"name":"timeout_sec","type":"integer","required":false,"default":"5","description":"Connect timeout seconds"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'service_discovery', 'Service Discovery',
  'List running containers / pods matching a service name to verify what is actually running.',
  'diagnostic', 1, false, true,
  'docker ps --filter "name={service}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"',
  '{"docker":"docker ps --filter \"name={service}\" --format \"table {{.Names}}\\t{{.Status}}\\t{{.Ports}}\"",
    "kubernetes":"kubectl get pods -n {namespace} -l app={service} -o wide",
    "ssh":"ssh {host} systemctl list-units --type=service --state=running | grep {service}"}'::json,
  '[{"name":"service","type":"string","required":true,"description":"Service name to search for"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'lb_remove_backend', 'Remove Backend from Load Balancer',
  'Remove a failing backend from the load balancer pool. Requires HAProxy, NGINX upstream, or Kubernetes service integration.',
  'remediation_intrusive', 3, true, true, NULL, NULL,
  '[{"name":"backend","type":"string","required":true,"description":"Backend / pod name to remove"},
    {"name":"lb_name","type":"string","required":false,"description":"Load balancer name or address"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'traffic_reroute', 'Reroute Traffic',
  'Redirect traffic from a failing service to a standby or fallback instance. Requires load balancer / DNS integration.',
  'remediation_intrusive', 3, true, true, NULL, NULL,
  '[{"name":"source","type":"string","required":true,"description":"Source service"},
    {"name":"destination","type":"string","required":true,"description":"Failover destination"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'degradation_mode', 'Enable Degradation Mode',
  'Switch service to degraded/read-only mode to maintain partial availability. Requires feature flag integration.',
  'remediation_safe', 2, true, true, NULL, NULL,
  '[{"name":"service","type":"string","required":true,"description":"Service name"},
    {"name":"mode","type":"string","required":false,"default":"read_only","description":"read_only | minimal | safe"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'alert_escalate', 'Escalate Alert',
  'Escalate the incident to on-call team via PagerDuty, OpsGenie, or Slack. Requires notification integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"team","type":"string","required":false,"default":"on-call","description":"Team or rotation to escalate to"},
    {"name":"severity","type":"string","required":false,"default":"high","description":"Escalation severity"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- CERTIFICATE EXPIRY runbook tools
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'cert_analysis', 'Certificate Analysis',
  'Show TLS certificate expiry date, subject, and issuer for a host.',
  'diagnostic', 1, false, true,
  'echo | openssl s_client -connect {host}:{port} 2>/dev/null | openssl x509 -noout -subject -issuer -dates',
  '{"any":"echo | openssl s_client -connect {host}:{port} 2>/dev/null | openssl x509 -noout -subject -issuer -dates"}'::json,
  '[{"name":"host","type":"string","required":true,"description":"Hostname to check"},
    {"name":"port","type":"integer","required":false,"default":"443","description":"TLS port"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'connectivity_check', 'Connectivity Check',
  'Verify network reachability to a host/port combination.',
  'diagnostic', 1, false, true,
  'curl -Is --connect-timeout {timeout_sec} {url} | head -1',
  '{"any":"curl -Is --connect-timeout {timeout_sec} {url} | head -1"}'::json,
  '[{"name":"url","type":"string","required":true,"description":"URL or host:port to probe"},
    {"name":"timeout_sec","type":"integer","required":false,"default":"5","description":"Connect timeout seconds"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'acme_renewal', 'ACME Certificate Renewal',
  'Trigger Let''s Encrypt / ACME certificate renewal via certbot.',
  'remediation_safe', 2, true, true,
  'certbot renew --cert-name {domain} --non-interactive',
  '{"any":"certbot renew --cert-name {domain} --non-interactive"}'::json,
  '[{"name":"domain","type":"string","required":true,"description":"Domain / cert name to renew"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'cert_deploy', 'Deploy Certificate',
  'Copy a renewed certificate to the NGINX/service config path and reload the server.',
  'remediation_safe', 2, true, true,
  'docker exec {container} sh -c "nginx -t && nginx -s reload"',
  '{"docker":"docker exec {container} sh -c \"nginx -t && nginx -s reload\"",
    "ssh":"ssh {host} sh -c \"nginx -t && systemctl reload nginx\""}'::json,
  '[{"name":"container","type":"string","required":false,"default":"agentic_os_nginx","description":"NGINX container"},
    {"name":"cert_path","type":"string","required":false,"default":"/etc/letsencrypt/live/{domain}/fullchain.pem","description":"Cert path"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'ssl_verify', 'SSL Certificate Verify',
  'Verify the deployed TLS certificate is valid and matches the expected domain.',
  'diagnostic', 1, false, true,
  'echo | openssl s_client -connect {host}:{port} 2>/dev/null | openssl x509 -noout -checkend {seconds}',
  '{"any":"echo | openssl s_client -connect {host}:{port} 2>/dev/null | openssl x509 -noout -checkend {seconds}"}'::json,
  '[{"name":"host","type":"string","required":true,"description":"Hostname to verify"},
    {"name":"port","type":"integer","required":false,"default":"443","description":"TLS port"},
    {"name":"seconds","type":"integer","required":false,"default":"2592000","description":"Fail if cert expires within N seconds (default 30d)"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'alert_update', 'Update Alert / Notification',
  'Update alert status or send a notification. Requires PagerDuty, OpsGenie, or Slack integration.',
  'remediation_safe', 1, false, true, NULL, NULL,
  '[{"name":"alert_id","type":"string","required":false,"description":"Alert ID to update"},
    {"name":"status","type":"string","required":false,"default":"resolved","description":"New status"},
    {"name":"message","type":"string","required":false,"description":"Update message"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- ════════════════════════════════════════════════════════
-- HIGH SYSCALL INTENSITY (remaining)
-- ════════════════════════════════════════════════════════

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'syscall_profiler', 'Syscall Profiler',
  'Profile system calls made by a process using strace. Shows syscall frequency and timing.',
  'diagnostic', 1, false, true,
  'docker exec {container} strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50',
  '{"docker":"docker exec {container} strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50",
    "kubernetes":"kubectl exec {pod} -n {namespace} -- strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50",
    "ssh":"ssh {host} strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50"}'::json,
  '[{"name":"container","type":"string","required":false,"default":"","description":"Container name"},
    {"name":"process_name","type":"string","required":true,"description":"Process to profile"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'dependency_check', 'Dependency Connectivity Check',
  'Verify TCP connectivity to a named host and port to detect broken upstream dependencies.',
  'diagnostic', 1, false, true,
  'nc -zv {host} {port} 2>&1',
  '{"any":"nc -zv {host} {port} 2>&1"}'::json,
  '[{"name":"host","type":"string","required":true,"description":"Target hostname or IP"},
    {"name":"port","type":"integer","required":true,"description":"Target port"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (gen_random_uuid(), 'container_monitor', 'Container Live Monitor',
  'Show live resource usage stats for a container (CPU, memory, net I/O, block I/O).',
  'diagnostic', 1, false, true,
  'docker stats {container} --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"',
  '{"docker":"docker stats {container} --no-stream --format \"table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.NetIO}}\\t{{.BlockIO}}\"",
    "kubernetes":"kubectl top pod {pod} -n {namespace}"}'::json,
  '[{"name":"container","type":"string","required":true,"description":"Container / pod name"},
    {"name":"namespace","type":"string","required":false,"default":"default","description":"K8s namespace"}]'::json,
  NOW(), NOW()) ON CONFLICT (tool_name) DO NOTHING;

-- Also add variants to query_metrics (currently has no variants)
UPDATE approved_actions
SET command_variants = '{
  "any": "curl -s {url}/metrics | grep \"^{metric_name}\""
}'::json
WHERE tool_name = 'query_metrics' AND (command_variants IS NULL OR command_variants::text = '{}');
