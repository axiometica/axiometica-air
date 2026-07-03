-- ─────────────────────────────────────────────────────────────────────────────
-- Add vcenter / aws_ssm / azure / kubernetes variants to all 44 real-exec tools.
--
-- vcenter / aws_ssm / azure → mode="target": command runs INSIDE the VM/instance
--   via the platform's control-plane transport (Guest Ops / SSM Run Command /
--   Azure Run Command). These are plain shell commands — no transport prefix.
--
-- kubernetes → mode="host": kubectl exec {pod} -- <cmd> runs from the watcher pod.
--   Control-plane ops (scale, rollout restart) remain as kubectl management commands.
--
-- Uses jsonb merge (||) for a non-destructive add — existing keys are preserved.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Helper: merge new keys into existing command_variants ────────────────────
-- Pattern:
--   SET command_variants = (COALESCE(command_variants::jsonb,'{}') || NEW_KEYS)::json

-- ════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC — system metrics
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- top -bn{interval_sec} | head -20",
    "vcenter":    "top -bn{interval_sec} | head -20",
    "aws_ssm":    "top -bn{interval_sec} | head -20",
    "azure":      "top -bn{interval_sec} | head -20"
  }'::jsonb )::json WHERE tool_name = 'check_cpu';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- free -h",
    "vcenter":    "free -h && vmstat 1 3",
    "aws_ssm":    "free -h && vmstat 1 3",
    "azure":      "free -h && vmstat 1 3"
  }'::jsonb )::json WHERE tool_name = 'check_memory';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- ps aux --sort=-{sort_by} | head -{limit}",
    "vcenter":    "ps aux --sort=-{sort_by} | head -{limit}",
    "aws_ssm":    "ps aux --sort=-{sort_by} | head -{limit}",
    "azure":      "ps aux --sort=-{sort_by} | head -{limit}"
  }'::jsonb )::json WHERE tool_name = 'top_processes';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- df -h {path}",
    "vcenter":    "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "aws_ssm":    "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "azure":      "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20"
  }'::jsonb )::json WHERE tool_name = 'check_disk_usage';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- free -h && cat /proc/meminfo | grep -E \"Mem|Swap|Cached\"",
    "vcenter":    "free -h && cat /proc/meminfo | grep -E \"Mem|Swap|Cached\"",
    "aws_ssm":    "free -h && cat /proc/meminfo | grep -E \"Mem|Swap|Cached\"",
    "azure":      "free -h && cat /proc/meminfo | grep -E \"Mem|Swap|Cached\""
  }'::jsonb )::json WHERE tool_name = 'memory_profiler';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "vcenter":    "df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "aws_ssm":    "df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
    "azure":      "df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20"
  }'::jsonb )::json WHERE tool_name = 'disk_usage_analysis';

-- ════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC — logs
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines}",
    "vcenter":    "journalctl -u {service} -n {lines} --no-pager 2>/dev/null || tail -n {lines} /var/log/syslog",
    "aws_ssm":    "journalctl -u {service} -n {lines} --no-pager 2>/dev/null || tail -n {lines} /var/log/syslog",
    "azure":      "journalctl -u {service} -n {lines} --no-pager 2>/dev/null || tail -n {lines} /var/log/syslog"
  }'::jsonb )::json WHERE tool_name = 'get_logs';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines}",
    "vcenter":    "journalctl -u {service} -n {lines} --no-pager 2>/dev/null",
    "aws_ssm":    "journalctl -u {service} -n {lines} --no-pager 2>/dev/null",
    "azure":      "journalctl -u {service} -n {lines} --no-pager 2>/dev/null"
  }'::jsonb )::json WHERE tool_name = 'log_analysis';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines} --timestamps",
    "vcenter":    "journalctl -n {lines} --no-pager 2>/dev/null",
    "aws_ssm":    "journalctl -n {lines} --no-pager 2>/dev/null",
    "azure":      "journalctl -n {lines} --no-pager 2>/dev/null"
  }'::jsonb )::json WHERE tool_name = 'pod_logs';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl logs {pod} -n {namespace} --since={window_min}m | grep -cE \"(ERROR|WARN|FATAL)\"",
    "vcenter":    "journalctl --since \"{window_min} min ago\" | grep -cE \"(ERROR|WARN|FATAL)\" 2>/dev/null",
    "aws_ssm":    "journalctl --since \"{window_min} min ago\" | grep -cE \"(ERROR|WARN|FATAL)\" 2>/dev/null",
    "azure":      "journalctl --since \"{window_min} min ago\" | grep -cE \"(ERROR|WARN|FATAL)\" 2>/dev/null"
  }'::jsonb )::json WHERE tool_name = 'error_analysis';

-- ════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC — process inspection
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- ps -fp $(pgrep {process_name}) 2>/dev/null",
    "vcenter":    "ps -fp $(pgrep {process_name}) 2>/dev/null",
    "aws_ssm":    "ps -fp $(pgrep {process_name}) 2>/dev/null",
    "azure":      "ps -fp $(pgrep {process_name}) 2>/dev/null"
  }'::jsonb )::json WHERE tool_name = 'process_info';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- pgrep {process_name} && echo RUNNING || echo STOPPED",
    "vcenter":    "pgrep {process_name} && echo RUNNING || echo STOPPED",
    "aws_ssm":    "pgrep {process_name} && echo RUNNING || echo STOPPED",
    "azure":      "pgrep {process_name} && echo RUNNING || echo STOPPED"
  }'::jsonb )::json WHERE tool_name = 'process_verify';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50",
    "vcenter":    "strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50",
    "aws_ssm":    "strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50",
    "azure":      "strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50"
  }'::jsonb )::json WHERE tool_name = 'syscall_profiler';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl top pod {pod} -n {namespace}",
    "vcenter":    "top -bn1 | head -15 && free -h",
    "aws_ssm":    "top -bn1 | head -15 && free -h",
    "azure":      "top -bn1 | head -15 && free -h"
  }'::jsonb )::json WHERE tool_name = 'container_monitor';

-- ════════════════════════════════════════════════════════════════════════════
-- REMEDIATION — process signals
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- kill -{signal} {process_name}",
    "vcenter":    "kill -{signal} {process_name}",
    "aws_ssm":    "kill -{signal} {process_name}",
    "azure":      "kill -{signal} {process_name}"
  }'::jsonb )::json WHERE tool_name = 'process_kill';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- kill -{signal} {process_name}",
    "vcenter":    "kill -{signal} {process_name}",
    "aws_ssm":    "kill -{signal} {process_name}",
    "azure":      "kill -{signal} {process_name}"
  }'::jsonb )::json WHERE tool_name = 'process_signal';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- kill -USR1 {process_name}",
    "vcenter":    "kill -USR1 {process_name}",
    "aws_ssm":    "kill -USR1 {process_name}",
    "azure":      "kill -USR1 {process_name}"
  }'::jsonb )::json WHERE tool_name = 'gc_trigger';

-- ════════════════════════════════════════════════════════════════════════════
-- REMEDIATION — service / container restart
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "vcenter":    "systemctl restart {target}",
    "aws_ssm":    "systemctl restart {target}",
    "azure":      "systemctl restart {target}"
  }'::jsonb )::json WHERE tool_name = 'restart_service';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "vcenter":    "systemctl restart {target}",
    "aws_ssm":    "systemctl restart {target}",
    "azure":      "systemctl restart {target}"
  }'::jsonb )::json WHERE tool_name = 'service_restart';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "vcenter":    "systemctl restart {target}",
    "aws_ssm":    "systemctl restart {target}",
    "azure":      "systemctl restart {target}"
  }'::jsonb )::json WHERE tool_name = 'pod_restart';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "vcenter":    "kill -9 {process_name} && systemctl restart {service}",
    "aws_ssm":    "kill -9 {process_name} && systemctl restart {service}",
    "azure":      "kill -9 {process_name} && systemctl restart {service}"
  }'::jsonb )::json WHERE tool_name = 'force_restart';

-- ════════════════════════════════════════════════════════════════════════════
-- REMEDIATION — file / disk cleanup
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- find {path} -name \"*.log\" -mtime +{days_to_retain} -delete",
    "vcenter":    "find {path} -name \"*.log\" -mtime +{days_to_retain} -delete",
    "aws_ssm":    "find {path} -name \"*.log\" -mtime +{days_to_retain} -delete",
    "azure":      "find {path} -name \"*.log\" -mtime +{days_to_retain} -delete"
  }'::jsonb )::json WHERE tool_name = 'cleanup_logs';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;",
    "vcenter":    "find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;",
    "aws_ssm":    "find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;",
    "azure":      "find {path} -name \"*.log\" -mtime +{days} -exec gzip -k {} \\;"
  }'::jsonb )::json WHERE tool_name = 'log_compression';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- find {path} -name \"*.log\" -mtime +{days} -delete",
    "vcenter":    "find {path} -name \"*.log\" -mtime +{days} -delete",
    "aws_ssm":    "find {path} -name \"*.log\" -mtime +{days} -delete",
    "azure":      "find {path} -name \"*.log\" -mtime +{days} -delete"
  }'::jsonb )::json WHERE tool_name = 'log_rotation';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete",
    "vcenter":    "find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete",
    "aws_ssm":    "find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete",
    "azure":      "find /tmp /var/tmp -type f -mmin +{older_than_minutes} -delete"
  }'::jsonb )::json WHERE tool_name = 'temp_cleanup';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- sh -c \"apt-get clean 2>/dev/null || yum clean all 2>/dev/null || apk cache clean 2>/dev/null\"",
    "vcenter":    "apt-get clean 2>/dev/null || yum clean all 2>/dev/null || apk cache clean 2>/dev/null",
    "aws_ssm":    "yum clean all 2>/dev/null || apt-get clean 2>/dev/null",
    "azure":      "apt-get clean 2>/dev/null || yum clean all 2>/dev/null"
  }'::jsonb )::json WHERE tool_name = 'package_cache_clean';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- find {path} -name \"{pattern}\" -exec gzip {} \\;",
    "vcenter":    "find {path} -name \"{pattern}\" -exec gzip {} \\;",
    "aws_ssm":    "find {path} -name \"{pattern}\" -exec gzip {} \\;",
    "azure":      "find {path} -name \"{pattern}\" -exec gzip {} \\;"
  }'::jsonb )::json WHERE tool_name = 'compression';

-- ════════════════════════════════════════════════════════════════════════════
-- REMEDIATION — cache / Redis
-- (vcenter/aws_ssm/azure assume Redis is running on the target VM)
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\"",
    "vcenter":    "redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\"",
    "aws_ssm":    "redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\"",
    "azure":      "redis-cli info stats | grep -E \"keyspace|hit|miss|used_memory\""
  }'::jsonb )::json WHERE tool_name = 'cache_stats';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- redis-cli FLUSHDB",
    "vcenter":    "redis-cli FLUSHDB",
    "aws_ssm":    "redis-cli FLUSHDB",
    "azure":      "redis-cli FLUSHDB"
  }'::jsonb )::json WHERE tool_name = 'cache_clear';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- redis-cli FLUSHALL",
    "vcenter":    "redis-cli FLUSHALL",
    "aws_ssm":    "redis-cli FLUSHALL",
    "azure":      "redis-cli FLUSHALL"
  }'::jsonb )::json WHERE tool_name = 'redis_flush';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- redis-cli LLEN {queue_name}",
    "vcenter":    "redis-cli LLEN {queue_name}",
    "aws_ssm":    "redis-cli LLEN {queue_name}",
    "azure":      "redis-cli LLEN {queue_name}"
  }'::jsonb )::json WHERE tool_name = 'queue_analysis';

-- ════════════════════════════════════════════════════════════════════════════
-- REMEDIATION — database (PostgreSQL)
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\"",
    "vcenter":    "psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\"",
    "aws_ssm":    "psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\"",
    "azure":      "psql -U {db_user} -d {db_name} -c \"SELECT state, count(*), max(now()-state_change) AS longest FROM pg_stat_activity GROUP BY state ORDER BY count DESC;\""
  }'::jsonb )::json WHERE tool_name = 'db_connection_analysis';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\"",
    "vcenter":    "psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\"",
    "aws_ssm":    "psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\"",
    "azure":      "psql -U {db_user} -d {db_name} -c \"SELECT pid, now()-query_start AS duration, state, left(query,120) FROM pg_stat_activity WHERE state != ''idle'' ORDER BY duration DESC LIMIT {limit};\""
  }'::jsonb )::json WHERE tool_name = 'query_analyzer';

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds';\"",
    "vcenter":    "psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds';\"",
    "aws_ssm":    "psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds';\"",
    "azure":      "psql -U {db_user} -d {db_name} -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds';\""
  }'::jsonb )::json WHERE tool_name = 'db_command';

-- ════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC — service discovery (platform-native listing)
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "vcenter":    "ps aux | grep {service} | grep -v grep || systemctl list-units --type=service --state=running | grep {service}",
    "aws_ssm":    "ps aux | grep {service} | grep -v grep || systemctl list-units --type=service --state=running | grep {service}",
    "azure":      "ps aux | grep {service} | grep -v grep || systemctl list-units --type=service --state=running | grep {service}"
  }'::jsonb )::json WHERE tool_name = 'service_discovery';

-- ════════════════════════════════════════════════════════════════════════════
-- WORKER METRICS — Celery (only meaningful on Docker/K8s, not in VMs)
-- ════════════════════════════════════════════════════════════════════════════

UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || '{
    "kubernetes": "kubectl exec {pod} -n {namespace} -- celery -A agentic_os.tasks inspect stats 2>/dev/null | head -40"
  }'::jsonb )::json WHERE tool_name = 'worker_metrics';

-- ════════════════════════════════════════════════════════════════════════════
-- SCALE / KUBERNETES control-plane — not applicable to vcenter/ssm/azure
-- (no variants added for these — the existing kubernetes/docker variants are correct)
-- ════════════════════════════════════════════════════════════════════════════
-- scale_up, scale_down, scale_service, kubectl_scale, worker_config — skip
