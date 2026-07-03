-- ============================================================
-- Migration: populate command field for all 40 approved actions
-- Run once against existing databases that have NULL command values.
-- ============================================================

-- Ensure column exists (idempotent)
ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS command TEXT;

-- ── DIAGNOSTICS ──────────────────────────────────────────────────────────────

UPDATE approved_actions
SET command = 'docker exec {container} ps aux --sort=-{sort_by} | head -{limit}'
WHERE tool_name = 'top_processes' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} ss -tunaop | grep {state}'
WHERE tool_name = 'list_connections' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} df -h {path} && docker exec {container} du -sh {path}/* 2>/dev/null | sort -rh | head -20'
WHERE tool_name = 'check_disk_usage' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} free -h && docker exec {container} cat /proc/meminfo | grep -E "Mem|Swap|Cached"'
WHERE tool_name = 'check_memory' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} top -bn{interval_sec} | head -20'
WHERE tool_name = 'check_cpu' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker logs {container} --tail {lines} 2>&1 | grep -E "{pattern}"'
WHERE tool_name = 'get_logs' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'curl -Is --connect-timeout {timeout_sec} {protocol}://{host}:{port}'
WHERE tool_name = 'ping_service' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'curl -s -o /dev/null -w "%{http_code}" --max-time {timeout_sec} {url}'
WHERE tool_name = 'check_health_endpoint' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} lsof -p $(pgrep {process_name}) 2>/dev/null || docker exec {container} lsof'
WHERE tool_name = 'list_open_files' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} ps -fp $(pgrep {process_name}) && docker exec {container} cat /proc/$(pgrep {process_name})/status'
WHERE tool_name = 'get_process_info' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} cat /proc/swaps && docker exec {container} vmstat 1 3'
WHERE tool_name = 'check_swap' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} nslookup {hostname} {dns_server}'
WHERE tool_name = 'check_dns' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} nc -zv {host} {port_range} 2>&1 || nmap -p {port_range} {host}'
WHERE tool_name = 'check_ports' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker logs {container} --since {window_min}m 2>&1 | grep -cE "(ERROR|WARN)"'
WHERE tool_name = 'get_error_rate' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} redis-cli LLEN {queue_name}'
WHERE tool_name = 'check_queue_depth' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}\t{{.Image}}" --filter "status={filter_status}"'
WHERE tool_name = 'list_containers' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} strace -p $(pgrep {process_name}) -c -e trace=all -T -f 2>&1 | head -50'
WHERE tool_name = 'trace_syscalls' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} env | sort'
WHERE tool_name = 'check_env_vars' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} kill -3 $(pgrep {process_name})'
WHERE tool_name = 'get_thread_dump' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'curl -s {url}/metrics | grep "^{metric_name}"'
WHERE tool_name = 'query_metrics' AND (command IS NULL OR command = '');

-- ── REMEDIATION — SAFE ───────────────────────────────────────────────────────

UPDATE approved_actions
SET command = 'docker exec {container} find {path} -type f -name "*.log" -mtime +{days_to_retain} -delete'
WHERE tool_name = 'cleanup_logs' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} redis-cli FLUSHDB'
WHERE tool_name = 'clear_cache' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} logrotate -f {config}'
WHERE tool_name = 'rotate_logs' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} find /tmp -type f -mmin +{older_than_hours}0 -delete'
WHERE tool_name = 'free_temp_files' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker compose up -d --scale {target}={replicas}'
WHERE tool_name = 'scale_up' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker compose up -d --scale {target}={replicas}'
WHERE tool_name = 'scale_down' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} sh -c "echo {key}={value} >> /etc/environment"'
WHERE tool_name = 'update_config' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} sh -c "crontab -l > /tmp/cron.bak && crontab -r && echo Cron paused for {duration_min}m"'
WHERE tool_name = 'pause_cron' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms'
WHERE tool_name = 'throttle_traffic' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} nscd -i hosts 2>/dev/null || docker exec {container} systemctl restart systemd-resolved'
WHERE tool_name = 'flush_dns_cache' AND (command IS NULL OR command = '');

-- ── REMEDIATION — INTRUSIVE ──────────────────────────────────────────────────

UPDATE approved_actions
SET command = 'docker exec {container} kill -{signal} $(pgrep {process_name})'
WHERE tool_name = 'process_kill' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker restart --time {timeout_sec} {target}'
WHERE tool_name = 'restart_service' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} kill -9 $(pgrep {process_name}) && docker restart {container}'
WHERE tool_name = 'force_restart' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} psql -U postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{max_idle_sec} seconds''"'
WHERE tool_name = 'kill_connections' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'iptables -I INPUT -s {ip} -j DROP'
WHERE tool_name = 'block_ip' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker compose pull {service} && docker compose up -d {service}'
WHERE tool_name = 'rollback_deployment' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'for net in $(docker inspect {container} -f "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"); do docker network disconnect -f $net {container}; done'
WHERE tool_name = 'isolate_container' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'docker exec {container} redis-cli DEL "session:{subject}" "token:{subject}"'
WHERE tool_name = 'revoke_token' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'kubectl drain {node} --ignore-daemonsets={ignore_ds} --grace-period={grace_sec} --delete-emptydir-data'
WHERE tool_name = 'drain_node' AND (command IS NULL OR command = '');

UPDATE approved_actions
SET command = 'kubectl drain {node} --force --ignore-daemonsets --delete-emptydir-data --grace-period=0'
WHERE tool_name = 'evacuate_node' AND (command IS NULL OR command = '');
