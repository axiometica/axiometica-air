"""
Canonical runbook seed data — all runbooks defined as Python dicts.

Rules:
  • Every tool reference MUST exist in approved_actions_seed.py (catalog tools only).
  • platform must be explicitly set ('any', 'docker', 'linux', 'kubernetes', 'windows').
  • enabled=True for all entries in this file.
  • service=None means catch-all (any service triggers this runbook).
  • Steps use args_json (not args) — the legacy key caused silent failures.
  • process_name_from_context: "anomaly_process" is resolved at runtime from
    the watcher alert payload — no hardcoded process name needed.

Catalog tool reference (approved_actions_seed.py):
  Diagnostics: check_cpu, top_processes, get_process_info, check_memory,
               check_disk_usage, get_logs, get_error_rate, check_health_endpoint,
               ping_service, check_swap, check_dns, check_ports, check_env_vars,
               check_queue_depth, trace_syscalls, list_open_files, get_thread_dump,
               list_connections, query_metrics, check_queue_depth,
               k8s_pod_logs, k8s_pod_describe, k8s_events, k8s_top_pods,
               k8s_rollout_status, k8s_pod_status
  Remediation: process_kill, restart_service, cleanup_logs, free_temp_files,
               rotate_logs, clear_cache, kill_connections, throttle_traffic,
               flush_dns_cache, update_config, pause_cron, scale_up, scale_down,
               block_ip, force_restart, isolate_container, revoke_token,
               k8s_rollout_restart, k8s_scale, k8s_delete_pod,
               host_service_restart, host_process_kill
"""

# ── Step type helpers ─────────────────────────────────────────────────────────

def _diag(order, name, description, tool, args=None):
    return {
        "order": order, "type": "diagnostic",
        "name": name, "description": description,
        "tool": tool, "args_json": args or {},
    }

def _action(order, name, description, tool, args=None):
    return {
        "order": order, "type": "remediation",
        "name": name, "description": description,
        "tool": tool, "args_json": args or {},
    }

def _verify(order, name, description, metric, check="less_than", value=None):
    step = {"order": order, "name": name, "description": description,
            "metric": metric, "check": check}
    if value is not None:
        step["value"] = value
    return step


# ─────────────────────────────────────────────────────────────────────────────
# RUNBOOK DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

RUNBOOKS = [

    # ══════════════════════════════════════════════════════════════════════════
    # 1. HIGH CPU — catch-all
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440101",
        "name": "High CPU — Kill Runaway or Scale Up",
        "description": (
            "Handles elevated CPU. If a rogue subprocess is the cause, kills it and restarts "
            "the service. If the main service process is legitimately overloaded, scales up replicas."
        ),
        "event_type": "infrastructure.compute.cpu_high",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.82,
        "blast_radius": 2,
        # Flat arrays derived from the visual-editor graph (source_steps).
        # Graph: Check CPU → Get Top Process → DECISION(top_process_cpu_pct>60)
        #        → true: Kill Runaway | false: Scale Up → Verify CPU → Notify
        "diagnostics": [
            _diag(1, "Check CPU Usage",
                  "Measure current CPU utilisation across all cores",
                  "check_cpu", {}),
            _diag(2, "Get Top Process Info",
                  "Identify the top CPU-consuming process",
                  "top_processes", {"sort": "cpu", "limit": "5"}),
        ],
        "actions": [
            _action(1, "Kill Runaway Process",
                    "Send SIGTERM to the top CPU process when it exceeds 60%",
                    "process_kill",
                    {"pid": "{{top_process_pid}}", "signal": "SIGTERM",
                     "process_name": "{{top_process_name}}"}),
            _action(2, "Scale Up Service",
                    "Add replicas to absorb load when no single runaway process is found",
                    "scale_up", {"replicas": "2"}),
            _action(3, "Notify Resolution",
                    "Send notification that CPU remediation is complete",
                    "send_alert",
                    {"message": "CPU remediation complete. CPU now: {{cpu_after}}%",
                     "severity": "info"}),
        ],
        "verification_steps": [
            _verify(1, "Verify CPU Normal",
                    "CPU should drop below 80% after remediation",
                    "cpu_after", "less_than", 80),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_cpu", "name": "Check CPU Usage", "tool": "check_cpu", "type": "diagnostic", "args": {}, "output_capture": {"cpu_pct": "$.cpu_percent"}},
                {"id": "diag_top_proc", "name": "Get Top Process Info", "tool": "top_processes", "type": "diagnostic", "args": {"sort": "cpu", "limit": "5"}, "output_capture": {"top_process_pid": "$.top_process_pid", "top_process_name": "$.top_process", "top_process_cpu_pct": "$.top_cpu_percent"}},
                {"id": "dec_runaway", "name": "Runaway Process Causing It?", "type": "decision", "condition": "top_process_cpu_pct > 60", "on_true": "action_kill", "on_false": "action_scale"},
                {"id": "action_kill", "name": "Kill Runaway Process", "tool": "process_kill", "type": "action", "args": {"pid": "{{top_process_pid}}", "signal": "SIGTERM", "process_name": "{{top_process_name}}"}},
                {"id": "wait_after_kill", "name": "Wait for CPU Recovery", "type": "wait", "duration_seconds": 15},
                {"id": "action_scale", "name": "Scale Up Service", "tool": "scale_up", "type": "action", "args": {"replicas": "2"}},
                {"id": "wait_after_scale", "name": "Wait for Instances to Start", "type": "wait", "duration_seconds": 30},
                {"id": "verify_cpu", "name": "Verify CPU Normal", "tool": "check_cpu", "type": "verification", "args": {}, "check": "less_than", "value": "80", "metric": "cpu_after", "output_capture": {"cpu_after": "$.cpu_percent"}},
                {"id": "notify_done", "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "args": {"message": "CPU remediation complete. CPU now: {{cpu_after}}%", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",          "target": "diag_cpu",        "sourceHandle": None},
                {"source": "diag_cpu",        "target": "diag_top_proc",   "sourceHandle": None},
                {"source": "diag_top_proc",   "target": "dec_runaway",     "sourceHandle": None},
                {"source": "dec_runaway",     "target": "action_kill",     "sourceHandle": "true"},
                {"source": "dec_runaway",     "target": "action_scale",    "sourceHandle": "false"},
                {"source": "action_kill",     "target": "wait_after_kill", "sourceHandle": None},
                {"source": "wait_after_kill", "target": "verify_cpu",      "sourceHandle": None},
                {"source": "action_scale",    "target": "wait_after_scale","sourceHandle": None},
                {"source": "wait_after_scale","target": "verify_cpu",      "sourceHandle": None},
                {"source": "verify_cpu",      "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done",     "sourceHandle": None},
                {"source": "notify_done",     "target": "end",             "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 2. HIGH MEMORY — catch-all
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440102",
        "name": "High Memory — Cache Clear and Restart",
        "description": (
            "Frees memory by flushing caches and restarting the container. "
            "Swap check helps determine whether OOM risk is immediate."
        ),
        "event_type": "infrastructure.compute.memory_high",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.80,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Memory breakdown",
                  "Show free/used/swap memory and cached pages",
                  "check_memory"),
            _diag(2, "Swap pressure",
                  "Check swap usage and swap-in/out rates",
                  "check_swap"),
            _diag(3, "Top memory processes",
                  "Identify the top 10 processes by RSS",
                  "top_processes", {"limit": 10, "sort_by": "memory"}),
        ],
        "actions": [
            _action(1, "Flush application cache",
                    "Flush the Redis / application cache to free in-memory objects.",
                    "clear_cache", {"cache_type": "all"}),
            _action(2, "Graceful service restart",
                    "Restart the container to reclaim leaked memory.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "Memory normalised",
                    "Memory usage should drop below 80% after cache flush and restart",
                    "memory_after", "less_than", 80),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_memory", "name": "Check Memory Usage", "type": "diagnostic", "tool": "check_memory", "on_failure": "abort", "output_capture": {"memory_pct": "$.mem_percent"}},
                {"id": "diag_process", "name": "Get Process Info", "type": "diagnostic", "tool": "get_process_info", "args": {"name": "cache"}, "on_failure": "abort", "output_capture": {"process_status": "$.status"}},
                {"id": "dec_memory", "type": "decision", "on_failure": "abort", "condition": "diag_memory.mem_percent > 80", "on_true": "action_clear_cache", "on_false": "notify_no_action"},
                {"id": "action_clear_cache", "name": "Clear Cache", "type": "action", "tool": "clear_cache", "on_failure": "continue"},
                {"id": "action_restart_service", "name": "Restart Service", "type": "action", "tool": "restart_service", "args": {"name": "cache"}, "run_if": "process_status == 'running'", "on_failure": "abort"},
                {"id": "wait_for_restart", "name": "Wait for Service Restart", "type": "wait", "duration_seconds": 30},
                {"id": "verify_memory", "name": "Verify Memory Normal", "type": "verification", "tool": "check_memory", "on_failure": "abort", "metric": "memory_after", "check": "less_than", "value": "80", "output_capture": {"memory_after": "$.mem_percent"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "Memory remediation complete. Memory now: {{memory_after}}%", "severity": "info"}, "on_failure": "abort"},
                {"id": "notify_no_action", "name": "Notify: Memory Already Normal", "type": "notify", "tool": "send_alert", "args": {"message": "Memory usage is within normal range ({{memory_pct}}%). No action required.", "severity": "info"}, "on_failure": "abort"},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "on_failure": "abort", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_memory", "sourceHandle": None},
                {"source": "diag_memory", "target": "diag_process", "sourceHandle": None},
                {"source": "diag_process", "target": "dec_memory", "sourceHandle": None},
                {"source": "dec_memory", "target": "action_clear_cache", "sourceHandle": "true"},
                {"source": "action_clear_cache", "target": "action_restart_service", "sourceHandle": None},
                {"source": "action_restart_service", "target": "wait_for_restart", "sourceHandle": None},
                {"source": "wait_for_restart", "target": "verify_memory", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "dec_memory", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "verify_memory", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 3. DISK FULL — catch-all
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440103",
        "name": "Disk Full — Clean Logs and Temp Files",
        "description": (
            "Reclaims disk space by cleaning logs, rotating old files, and removing "
            "temporary data. Safe to run without service interruption."
        ),
        "event_type": "infrastructure.storage.disk_full",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.88,
        "blast_radius": 1,
        "diagnostics": [
            _diag(1, "Disk usage by directory",
                  "Find which directories are consuming the most space",
                  "check_disk_usage", {"path": "/"}),
            _diag(2, "Recent error logs",
                  "Check whether disk pressure is causing write errors",
                  "get_logs", {"lines": 50}),
        ],
        "actions": [
            _action(1, "Delete old log files",
                    "Remove log files older than 7 days under /var/log.",
                    "cleanup_logs", {"path": "/var/log", "days_to_retain": 7}),
            _action(2, "Clean temp files",
                    "Remove stale files from /tmp older than 24 hours.",
                    "free_temp_files", {"older_than_hours": 24}),
            _action(3, "Rotate active logs",
                    "Trigger logrotate to compress and cycle current log files.",
                    "rotate_logs", {"config": "/etc/logrotate.conf"}),
        ],
        "verification_steps": [
            _verify(1, "Disk usage reduced",
                    "Disk usage should drop below 85% after cleanup",
                    "disk_after", "less_than", 85),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_disk_usage", "name": "Check Disk Usage", "type": "diagnostic", "tool": "check_disk_usage", "output_capture": {"disk_used_pct": "$.disk_percent"}},
                {"id": "diag_large_dirs", "name": "Identify Large Directories", "type": "diagnostic", "tool": "host_disk_usage", "output_capture": {"large_dirs": "$.large_dirs"}},
                {"id": "dec_disk_full", "type": "decision", "condition": "disk_used_pct > 90", "on_true": "action_cleanup_logs", "on_false": "notify_no_action"},
                {"id": "action_cleanup_logs", "name": "Clean Up Logs", "type": "action", "tool": "cleanup_logs", "args": {"days": "7", "path": "/var/log"}},
                {"id": "action_free_temp_files", "name": "Free Temp Files", "type": "action", "tool": "free_temp_files"},
                {"id": "verify_disk", "name": "Verify Disk Space", "type": "verification", "tool": "check_disk_usage", "metric": "disk_after", "check": "less_than", "value": "85", "output_capture": {"disk_after": "$.disk_percent"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "Disk cleanup complete. Disk usage now: {{disk_after}}%", "severity": "info"}},
                {"id": "notify_no_action", "name": "Notify: Disk Usage Acceptable", "type": "notify", "tool": "send_alert", "args": {"message": "Disk usage is within acceptable limits ({{disk_used_pct}}%). No cleanup required.", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_disk_usage", "sourceHandle": None},
                {"source": "diag_disk_usage", "target": "diag_large_dirs", "sourceHandle": None},
                {"source": "diag_large_dirs", "target": "dec_disk_full", "sourceHandle": None},
                {"source": "dec_disk_full", "target": "action_cleanup_logs", "sourceHandle": "true"},
                {"source": "action_cleanup_logs", "target": "action_free_temp_files", "sourceHandle": None},
                {"source": "action_free_temp_files", "target": "verify_disk", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "dec_disk_full", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "verify_disk", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 4. SERVICE DOWN — catch-all (NEW)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440111",
        "name": "Service Down — Health Check and Restart",
        "description": (
            "Verifies the service is truly down via health check, reviews recent logs "
            "for the root cause, then performs a graceful restart."
        ),
        "event_type": "application.availability.service_down",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.85,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "HTTP health check",
                  "Probe the service health endpoint to confirm it is unresponsive",
                  "check_health_endpoint",
                  {"url": "http://{target}:8080/health", "timeout_sec": 5}),
            _diag(2, "Recent logs",
                  "Review the last 100 log lines for crash or error context",
                  "get_logs", {"lines": 100}),
            _diag(3, "Check network connections",
                  "Verify whether the service is listening on its port",
                  "list_connections"),
        ],
        "actions": [
            _action(1, "Graceful service restart",
                    "Restart the container — the fastest path back to healthy.",
                    "restart_service", {"timeout_sec": 60}),
        ],
        "verification_steps": [
            _verify(1, "Service responds",
                    "Health endpoint should return below HTTP 400",
                    "service_http_code", "less_than", 400),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_health_check", "name": "Health Check", "type": "diagnostic", "tool": "check_health_endpoint", "output_capture": {"service_status": "$.http_code"}},
                {"id": "diag_error_rate", "name": "Error Rate Check", "type": "diagnostic", "tool": "get_error_rate", "args": {"window": "5m"}, "output_capture": {"error_rate": "$.error_count"}},
                {"id": "dec_service_status", "type": "decision", "condition": "service_status >= 400", "on_true": "action_restart_service", "on_false": "notify_no_action"},
                {"id": "action_restart_service", "name": "Restart Service", "type": "action", "tool": "restart_service"},
                {"id": "wait_for_startup", "name": "Wait for Service Startup", "type": "wait", "duration_seconds": 30},
                {"id": "verify_service_up", "name": "Verify Service is Up", "type": "verification", "tool": "check_health_endpoint", "metric": "service_http_code", "check": "less_than", "value": "400", "output_capture": {"service_http_code": "$.http_code"}},
                {"id": "notify_restart", "name": "Notify Service Restart", "type": "notify", "tool": "send_alert", "args": {"message": "Service was down. Restarted successfully. Status now: {{service_status_after_restart}}", "severity": "info"}},
                {"id": "notify_no_action", "name": "Notify No Action Needed", "type": "notify", "tool": "send_alert", "args": {"message": "Service is up. No action needed.", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_health_check", "sourceHandle": None},
                {"source": "diag_health_check", "target": "diag_error_rate", "sourceHandle": None},
                {"source": "diag_error_rate", "target": "dec_service_status", "sourceHandle": None},
                {"source": "dec_service_status", "target": "action_restart_service", "sourceHandle": "true"},
                {"source": "dec_service_status", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "action_restart_service", "target": "wait_for_startup", "sourceHandle": None},
                {"source": "wait_for_startup", "target": "verify_service_up", "sourceHandle": None},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "notify_restart", "target": "end", "sourceHandle": None},
                {"source": "verify_service_up", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_restart", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 5. SERVICE UNRESPONSIVE — signal-then-restart (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440105",
        "name": "Service Unresponsive — Signal and Restart",
        "description": (
            "Smart remediation: reads the alert context to identify the failing process "
            "and port. Sends SIGTERM first (graceful shutdown, Docker restart policy revives "
            "it), then SIGKILL if still running, then a full container restart as last resort."
        ),
        "event_type": "application.availability.service_unresponsive",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.90,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Review recent logs",
                  "Check log output for the failure reason (hung vs crashed)",
                  "get_logs", {"lines": 100}),
            _diag(2, "Process detail",
                  "Inspect PID, resource usage, and open FDs for the flagged process",
                  "get_process_info",
                  {"process_name_from_context": "anomaly_process"}),
        ],
        "actions": [
            _action(1, "Graceful SIGTERM",
                    "Send SIGTERM to the process — graceful shutdown. "
                    "Docker restart policy will revive it automatically.",
                    "process_kill",
                    {"signal": "SIGTERM",
                     "process_name_from_context": "anomaly_process"}),
            _action(2, "Force SIGKILL",
                    "Force-kill the process if SIGTERM was insufficient.",
                    "process_kill",
                    {"signal": "SIGKILL",
                     "process_name_from_context": "anomaly_process"}),
            _action(3, "Container restart (last resort)",
                    "Full container restart if process signals failed.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "Service responding",
                    "HTTP health probe should return below HTTP 400",
                    "health_http_code", "less_than", 400),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_health", "name": "Check Health Endpoint", "type": "diagnostic", "tool": "check_health_endpoint", "args": {}, "output_capture": {"health_status": "$.http_code"}},
                {"id": "diag_error_rate", "name": "Get Error Rate", "type": "diagnostic", "tool": "get_error_rate", "args": {"window": "5m"}, "output_capture": {"error_rate": "$.error_count"}},
                {"id": "dec_service_down", "type": "decision", "condition": "health_status != 200 || error_rate > 0.1", "on_true": "action_restart", "on_false": "end"},
                {"id": "action_restart", "name": "Restart Service", "type": "action", "tool": "restart_service", "args": {}},
                {"id": "wait_for_startup", "name": "Wait for Service Startup", "type": "wait", "duration_seconds": 30},
                {"id": "verify_health", "name": "Verify Health Endpoint", "type": "verification", "tool": "check_health_endpoint", "args": {}, "metric": "health_http_code", "check": "less_than", "value": "400", "output_capture": {"health_http_code": "$.http_code"}},
                {"id": "dec_service_still_down", "type": "decision", "condition": "verify_health.health_http_code != 200", "on_true": "notify_failure", "on_false": "notify_success"},
                {"id": "notify_failure", "name": "Notify Failure", "type": "notify", "tool": "send_alert", "args": {"message": "Service is still down after restart attempt.", "severity": "critical"}},
                {"id": "notify_success", "name": "Notify Success", "type": "notify", "tool": "send_alert", "args": {"message": "Service is responsive after restart.", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
                {"id": "incident_update_failed", "name": "Mark Failed", "type": "incident_update", "state": "failed"},
            ],
            "edges": [
                {"source": "start", "target": "diag_health", "sourceHandle": None},
                {"source": "diag_health", "target": "diag_error_rate", "sourceHandle": None},
                {"source": "diag_error_rate", "target": "dec_service_down", "sourceHandle": None},
                {"source": "dec_service_down", "target": "action_restart", "sourceHandle": "true"},
                {"source": "dec_service_down", "target": "end", "sourceHandle": "false"},
                {"source": "action_restart", "target": "wait_for_startup", "sourceHandle": None},
                {"source": "wait_for_startup", "target": "verify_health", "sourceHandle": None},
                {"source": "verify_health", "target": "dec_service_still_down", "sourceHandle": None},
                {"source": "dec_service_still_down", "target": "incident_update_failed", "sourceHandle": "true"},
                {"source": "incident_update_failed", "target": "notify_failure", "sourceHandle": None},
                {"source": "notify_failure", "target": "end", "sourceHandle": None},
                {"source": "notify_success", "target": "end", "sourceHandle": None},
                {"source": "dec_service_still_down", "target": "incident_update_resolve", "sourceHandle": "false"},
                {"source": "incident_update_resolve", "target": "notify_success", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 6. POD CRASH — Kubernetes specific (NEW)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440112",
        "name": "Pod Crash — Inspect and Recreate",
        "description": (
            "Diagnoses a Kubernetes pod crash (CrashLoopBackOff, OOMKilled, etc.) by "
            "reviewing logs, events, and resource limits, then deletes the pod so its "
            "ReplicaSet immediately recreates it with a clean state."
        ),
        "event_type": "container.pod.crash_looping",
        "service": None,
        "environment": None,
        "platform": "kubernetes",
        "enabled": True,
        "confidence": 0.87,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Pod logs",
                  "Fetch the last 200 lines from the crashed pod",
                  "k8s_pod_logs", {"lines": 200}),
            _diag(2, "Describe pod",
                  "Full kubectl describe: events, restart count, resource limits",
                  "k8s_pod_describe"),
            _diag(3, "Namespace events",
                  "Recent K8s events for root cause (OOM, image pull, probe failure)",
                  "k8s_events"),
        ],
        "actions": [
            _action(1, "Delete pod (force recreate)",
                    "Delete the crashed pod — the ReplicaSet immediately creates a replacement.",
                    "k8s_delete_pod", {"grace_seconds": 0}),
            _action(2, "Rolling restart deployment",
                    "If crash is systematic, trigger a full rolling restart of the deployment.",
                    "k8s_rollout_restart"),
        ],
        "verification_steps": [
            _verify(1, "Pod healthy",
                    "New pod should reach Running state",
                    "new_pod_status", "equals", "Running"),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_pod_status", "name": "Check Pod Status", "type": "diagnostic", "tool": "k8s_pod_status", "args": {}, "output_capture": {"pod_status": "$.status"}},
                {"id": "diag_pod_logs", "name": "Fetch Pod Logs", "type": "diagnostic", "tool": "k8s_pod_logs", "args": {}, "output_capture": {"pod_logs": "$.logs"}},
                {"id": "dec_pod_status", "type": "decision", "condition": "pod_status == 'CrashLoopBackOff'", "on_true": "action_delete_pod", "on_false": "notify_no_action"},
                {"id": "action_delete_pod", "name": "Delete Pod", "type": "action", "tool": "k8s_delete_pod", "args": {}},
                {"id": "wait_for_pod_ready", "name": "Wait for Pod to Start", "type": "wait", "duration_seconds": 60},
                {"id": "verify_pod_recreation", "name": "Verify Pod Recreation", "type": "verification", "tool": "k8s_pod_status", "args": {}, "metric": "new_pod_status", "check": "equals", "value": "Running", "output_capture": {"new_pod_status": "$.status"}},
                {"id": "notify_no_action", "name": "Notify No Action Needed", "type": "notify", "tool": "send_alert", "args": {"message": "Pod not in CrashLoopBackOff status. No action needed.", "severity": "info"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "Pod remediation complete. New pod status: {{new_pod_status}}", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_pod_status", "sourceHandle": None},
                {"source": "diag_pod_status", "target": "diag_pod_logs", "sourceHandle": None},
                {"source": "diag_pod_logs", "target": "dec_pod_status", "sourceHandle": None},
                {"source": "dec_pod_status", "target": "action_delete_pod", "sourceHandle": "true"},
                {"source": "dec_pod_status", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "action_delete_pod", "target": "wait_for_pod_ready", "sourceHandle": None},
                {"source": "wait_for_pod_ready", "target": "verify_pod_recreation", "sourceHandle": None},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "verify_pod_recreation", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 7. HIGH LATENCY — cache + restart (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440106",
        "name": "High Latency — Diagnose and Reduce Load",
        "description": (
            "Identifies the source of latency (CPU, memory, cache, connection count), "
            "flushes caches, and restarts the service if needed."
        ),
        "event_type": "application.performance.latency_high",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.75,
        "blast_radius": 2,
        # Flat arrays derived from the visual-editor graph (source_steps).
        # Graph: Check Latency → Check CPU → DECISION(cpu>80) → scale_up|clear_cache
        #        → Verify Latency → Notify
        "diagnostics": [
            _diag(1, "Check Latency",
                  "Ping the service to measure current response latency",
                  "ping_service", {"host": "target_service"}),
            _diag(2, "Check CPU Usage",
                  "High CPU is the most common latency root cause",
                  "check_cpu", {}),
        ],
        "actions": [
            _action(1, "Scale Up Service",
                    "Add one replica to absorb load when CPU is the bottleneck",
                    "scale_up", {"replicas": "+1"}),
            _action(2, "Clear Cache",
                    "Flush stale cache entries when CPU is normal but latency is high",
                    "clear_cache", {}),
            _action(3, "Notify Resolution",
                    "Send notification that latency remediation is complete",
                    "send_alert", {"message": "Latency remediation complete. Service health check HTTP status: {{latency_http_code}}", "severity": "info"}),
        ],
        "verification_steps": [
            _verify(1, "Verify Latency Reduction",
                    "Response latency should drop below 500ms after remediation",
                    "latency_http_code", "less_than", 500),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_latency", "name": "Check Latency", "tool": "ping_service", "type": "diagnostic", "args": {"host": "target_service"}, "output_capture": {"latency": "$.http_code"}},
                {"id": "diag_cpu", "name": "Check CPU Usage", "tool": "check_cpu", "type": "diagnostic", "args": {}, "output_capture": {"cpu_usage": "$.cpu_percent"}},
                {"id": "dec_cpu_high", "type": "decision", "condition": "cpu_usage > 80", "on_true": "action_scale_up", "on_false": "action_clear_cache"},
                {"id": "action_scale_up", "name": "Scale Up Service", "tool": "scale_up", "type": "action", "args": {"replicas": "+1"}},
                {"id": "wait_after_scale", "name": "Wait for Instances to Start", "type": "wait", "duration_seconds": 30},
                {"id": "action_clear_cache", "name": "Clear Cache", "tool": "clear_cache", "type": "action", "args": {}},
                {"id": "wait_after_cache_clear", "name": "Wait for Cache to Stabilise", "type": "wait", "duration_seconds": 15},
                {"id": "verify_latency", "name": "Verify Latency Reduction", "tool": "ping_service", "type": "verification", "args": {"host": "target_service"}, "check": "less_than", "value": "500", "metric": "latency_http_code", "output_capture": {"latency_http_code": "$.http_code"}},
                {"id": "notify_done", "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "args": {"message": "Latency remediation complete. Service health check HTTP status: {{latency_http_code}}", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",               "target": "diag_latency",        "sourceHandle": None},
                {"source": "diag_latency",         "target": "diag_cpu",            "sourceHandle": None},
                {"source": "diag_cpu",             "target": "dec_cpu_high",        "sourceHandle": None},
                {"source": "dec_cpu_high",         "target": "action_scale_up",     "sourceHandle": "true"},
                {"source": "dec_cpu_high",         "target": "action_clear_cache",  "sourceHandle": "false"},
                {"source": "action_scale_up",      "target": "wait_after_scale",    "sourceHandle": None},
                {"source": "wait_after_scale",     "target": "verify_latency",      "sourceHandle": None},
                {"source": "action_clear_cache",   "target": "wait_after_cache_clear", "sourceHandle": None},
                {"source": "wait_after_cache_clear","target": "verify_latency",     "sourceHandle": None},
                {"source": "verify_latency",       "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done",      "sourceHandle": None},
                {"source": "notify_done",          "target": "end",                 "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 8. HIGH ERROR RATE — log triage and restart (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440107",
        "name": "High Error Rate — Triage and Restart",
        "description": (
            "Counts and inspects recent errors to distinguish transient spikes from "
            "persistent failures, then restarts the service if errors are systemic."
        ),
        "event_type": "application.performance.error_rate_high",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.78,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Error count (5 min window)",
                  "Count ERROR/WARN lines in the last 5 minutes",
                  "get_error_rate", {"window_min": 5}),
            _diag(2, "Recent error logs",
                  "Review the last 100 log lines to identify the error pattern",
                  "get_logs", {"lines": 100}),
            _diag(3, "Upstream health check",
                  "Verify dependencies are reachable (errors may be cascading failures)",
                  "check_health_endpoint",
                  {"url": "http://{target}:8080/health", "timeout_sec": 5}),
        ],
        "actions": [
            _action(1, "Graceful service restart",
                    "If errors are systemic and not upstream, restart to clear bad state.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "Error rate reduced",
                    "Error count over 5 min window should be below 10",
                    "error_count_after", "less_than", 10),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_error_rate", "name": "Get Error Rate", "type": "diagnostic", "tool": "get_error_rate", "args": {"window": "5m"}, "output_capture": {"error_rate": "$.error_count"}},
                {"id": "diag_logs", "name": "Get Logs", "type": "diagnostic", "tool": "get_logs", "args": {"limit": "10", "pattern": "ERROR"}, "output_capture": {"error_logs": "$.logs"}},
                {"id": "dec_error_cause", "type": "decision", "condition": "'OutOfMemoryError' in error_logs", "on_true": "action_restart", "on_false": "action_scale"},
                {"id": "action_restart", "name": "Restart Service", "type": "action", "tool": "restart_service", "args": {}},
                {"id": "wait_after_restart", "name": "Wait for Service Restart", "type": "wait", "duration_seconds": 30},
                {"id": "action_scale", "name": "Scale Up Replicas", "type": "action", "tool": "scale_up", "args": {"replicas": "+1"}},
                {"id": "wait_after_scale", "name": "Wait for Instances to Start", "type": "wait", "duration_seconds": 30},
                {"id": "verify_error_rate", "name": "Verify Error Rate Normal", "type": "verification", "tool": "get_error_rate", "args": {"window": "5m"}, "metric": "error_count_after", "check": "less_than", "value": "10", "output_capture": {"error_count_after": "$.error_count"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "Error rate remediation complete. Error rate now: {{error_rate_after}}%", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_error_rate", "sourceHandle": None},
                {"source": "diag_error_rate", "target": "diag_logs", "sourceHandle": None},
                {"source": "diag_logs", "target": "dec_error_cause", "sourceHandle": None},
                {"source": "dec_error_cause", "target": "action_restart", "sourceHandle": "true"},
                {"source": "dec_error_cause", "target": "action_scale", "sourceHandle": "false"},
                {"source": "action_restart", "target": "wait_after_restart", "sourceHandle": None},
                {"source": "wait_after_restart", "target": "verify_error_rate", "sourceHandle": None},
                {"source": "action_scale", "target": "wait_after_scale", "sourceHandle": None},
                {"source": "wait_after_scale", "target": "verify_error_rate", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "verify_error_rate", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 9. CERTIFICATE EXPIRY — inspect and reload (NEW)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440113",
        "name": "Certificate Expiry — Inspect and Reload",
        "description": (
            "Confirms the TLS certificate is expiring via health check, then restarts "
            "the service so it picks up a recently renewed certificate from disk. "
            "Certificate renewal itself must be triggered externally (ACME, vault, etc.)."
        ),
        "event_type": "network.tls.certificate_expiring",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.70,
        "blast_radius": 1,
        "diagnostics": [
            _diag(1, "HTTPS health check",
                  "Confirm the HTTPS endpoint is reachable and the certificate is present",
                  "check_health_endpoint",
                  {"url": "https://{target}/health", "timeout_sec": 10}),
            _diag(2, "Check DNS resolution",
                  "Ensure the certificate domain resolves correctly",
                  "check_dns",
                  {"hostname_from_context": "resource_name"}),
        ],
        "actions": [
            _action(1, "Flush DNS cache",
                    "Clear the DNS cache in case of stale entries after cert renewal.",
                    "flush_dns_cache"),
            _action(2, "Reload service",
                    "Restart the service so it picks up the renewed certificate from disk. "
                    "Run this AFTER the certificate file has been updated externally.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "Certificate renewed",
                    "Days remaining until expiry should be back above 7",
                    "new_cert_expiry", "greater_than", 7),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_cert_status", "name": "Check Certificate Status", "type": "diagnostic", "tool": "check_env_vars", "args": {"filter": "CERT"}, "output_capture": {"cert_expiry": "$.CERT_EXPIRY"}},
                {"id": "dec_cert_expiry", "type": "decision", "condition": "cert_expiry <= 7", "on_true": "action_reload_cert", "on_false": "notify_no_action"},
                {"id": "action_reload_cert", "name": "Reload Certificate", "type": "action", "tool": "host_service_restart", "args": {"service": "nginx"}},
                {"id": "wait_for_service_reload", "name": "Wait for Service Reload", "type": "wait", "duration_seconds": 10},
                {"id": "verify_cert_reload", "name": "Verify Certificate Reload", "type": "verification", "tool": "check_env_vars", "args": {"filter": "CERT"}, "metric": "new_cert_expiry", "check": "greater_than", "value": 7, "output_capture": {"new_cert_expiry": "$.CERT_EXPIRY"}},
                {"id": "notify_no_action", "name": "Notify No Action Needed", "type": "notify", "tool": "send_alert", "args": {"message": "Certificate expiry is more than 7 days. No action needed.", "severity": "info"}},
                {"id": "notify_done", "name": "Notify Certificate Reloaded", "type": "notify", "tool": "send_alert", "args": {"message": "Certificate reloaded. New expiry: {{new_cert_expiry}} days.", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_cert_status", "sourceHandle": None},
                {"source": "diag_cert_status", "target": "dec_cert_expiry", "sourceHandle": None},
                {"source": "dec_cert_expiry", "target": "action_reload_cert", "sourceHandle": "true"},
                {"source": "dec_cert_expiry", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "action_reload_cert", "target": "wait_for_service_reload", "sourceHandle": None},
                {"source": "wait_for_service_reload", "target": "verify_cert_reload", "sourceHandle": None},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "verify_cert_reload", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 10. DB CONNECTION POOL EXHAUSTED (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440104",
        "name": "DB Connection Pool Exhausted — Kill Idle and Restart",
        "description": (
            "Kills idle/stuck database connections to free the pool, then restarts "
            "the application if the pool remains exhausted."
        ),
        "event_type": "database.connections.pool_exhausted",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.85,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "DB error logs",
                  "Check recent logs for connection pool error messages",
                  "get_logs", {"lines": 100}),
            _diag(2, "Network connections",
                  "Count active TCP connections to confirm pool exhaustion",
                  "list_connections"),
        ],
        "actions": [
            _action(1, "Kill idle DB connections",
                    "Terminate idle PostgreSQL connections older than 300 seconds.",
                    "kill_connections",
                    {"db_type": "postgres", "max_idle_sec": 300}),
            _action(2, "Restart application",
                    "Restart the application to reset its connection pool.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "DB health restored",
                    "DB health endpoint should return below HTTP 400",
                    "db_http_code", "less_than", 400),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_db_connections", "name": "List DB Connections", "type": "diagnostic", "tool": "list_connections", "args": {}, "output_capture": {"idle_connections": "$.idle_connections"}},
                {"id": "diag_db_health", "name": "Check DB Health", "type": "diagnostic", "tool": "check_health_endpoint", "args": {}, "output_capture": {"db_health": "$.status"}},
                {"id": "dec_idle_connections", "type": "decision", "condition": "idle_connections > 10", "on_true": "action_kill_idle", "on_false": "action_restart_service"},
                {"id": "action_kill_idle", "name": "Kill Idle Connections", "type": "action", "tool": "kill_connections", "args": {}},
                {"id": "action_restart_service", "name": "Restart Service", "type": "action", "tool": "restart_service", "args": {}, "run_if": "db_health != 'healthy'"},
                {"id": "wait_for_db_restart", "name": "Wait for DB Restart", "type": "wait", "duration_seconds": 30},
                {"id": "verify_db_health", "name": "Verify DB Health", "type": "verification", "tool": "check_health_endpoint", "args": {}, "metric": "db_http_code", "check": "less_than", "value": "400", "output_capture": {"db_http_code": "$.http_code"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "DB remediation complete. DB health now: {{db_health_after}}", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_db_connections", "sourceHandle": None},
                {"source": "diag_db_connections", "target": "diag_db_health", "sourceHandle": None},
                {"source": "diag_db_health", "target": "dec_idle_connections", "sourceHandle": None},
                {"source": "dec_idle_connections", "target": "action_kill_idle", "sourceHandle": "true"},
                {"source": "dec_idle_connections", "target": "action_restart_service", "sourceHandle": "false"},
                {"source": "action_kill_idle", "target": "verify_db_health", "sourceHandle": None},
                {"source": "action_restart_service", "target": "wait_for_db_restart", "sourceHandle": None},
                {"source": "wait_for_db_restart", "target": "verify_db_health", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "verify_db_health", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 11. HIGH SYSCALL INTENSITY — profile and kill (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440201",
        "name": "High Syscall Intensity — Process Termination",
        "description": (
            "Handles excessive syscall activity by profiling the source process, "
            "gathering diagnostic data, and safely terminating it. Uses anomaly_process "
            "from watcher context to identify the specific process."
        ),
        "event_type": "infrastructure.compute.syscall_intensity_high",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.94,
        "blast_radius": 1,
        # Flat arrays derived from the visual-editor graph (source_steps).
        # Graph: Top Syscall Processes → DECISION(syscall_count>10000) → Kill|end
        #        → Verify Syscall Normal → Notify
        "diagnostics": [
            _diag(1, "Top Syscall Processes",
                  "Trace syscalls to identify the process with highest syscall count",
                  "trace_syscalls", {"process_name": ""}),
        ],
        "actions": [
            _action(1, "Kill High Syscall Process",
                    "Terminate the process identified as causing excessive syscalls",
                    "process_kill", {"pid": "{{top_syscall_pid}}"}),
            _action(2, "Notify Resolution",
                    "Send notification that syscall remediation is complete",
                    "send_alert", {"message": "Syscall remediation complete. Syscall count now: {{syscall_after}}", "severity": "info"}),
        ],
        "verification_steps": [
            _verify(1, "Verify Syscall Normal",
                    "Confirm syscall count has returned to normal levels",
                    "syscall_after", "less_than", 10000),
        ],
        "source_steps": {
            # trace_syscalls traces ONE already-identified process — it cannot itself
            # discover "the top syscall-emitting process" (process_name is required
            # for pgrep to resolve a PID at all). Added a top_processes discovery step
            # first, matching the same proven pattern the "High CPU" runbook already
            # uses, instead of calling trace_syscalls with process_name="" (which gave
            # pgrep an empty pattern — undefined behavior, never reliably worked).
            #
            # Also: top_syscall_pid/top_syscall_count/syscall_after previously captured
            # via a nested "$.top_process.pid" / "$.top_process.syscall_count" JSONPath
            # that trace_syscalls's parser never actually produced (it returns flat
            # fields) — action_kill's pid: "{{top_syscall_pid}}" was therefore always
            # unresolved literal text in a real run. trace_syscalls's command and parser
            # were fixed to echo and capture a real "pid" field; paths corrected below.
            #
            # action_kill now passes process_name instead of pid: the process_kill tool's
            # Kill-API handler (ToolRegistryAgent._execute_tool_impl) only ever reads
            # args["process_name"] — a raw pid was silently rejected with "process_kill
            # action missing 'process_name'", confirmed live on GCP once the upstream
            # top_process_name/top_syscall_pid capture chain above was actually working.
            #
            # verify_syscall now also carries metric/check/value. Without them, the editor's
            # Test Run path treats a metric-less verification as an automatic pass ("metric
            # not captured"), while the real incident pipeline treats the exact same case as
            # a hard failure ("Verification step missing metric/check fields") — so the step
            # silently diverged between preview and live execution. metric=syscall_after
            # mirrors the decision's own threshold (top_syscall_count > 10000) so "normal"
            # means the same thing on the way in as on the way out.
            "steps": [
                {"id": "diag_top_proc", "name": "Identify Top Process", "tool": "top_processes", "type": "diagnostic", "args": {"sort": "cpu", "limit": "5"}, "output_capture": {"top_process_name": "$.top_process"}},
                {"id": "diag_syscall", "name": "Top Syscall Processes", "tool": "trace_syscalls", "type": "diagnostic", "args": {"process_name": "{{top_process_name}}"}, "output_capture": {"top_syscall_pid": "$.pid", "top_syscall_count": "$.top_syscall_count"}},
                {"id": "dec_high_syscall", "type": "decision", "condition": "top_syscall_count > 10000", "on_true": "action_kill", "on_false": "end"},
                {"id": "action_kill", "name": "Kill High Syscall Process", "tool": "process_kill", "type": "action", "args": {"process_name": "{{top_process_name}}"}},
                {"id": "wait_after_kill", "name": "Wait for Process Termination", "type": "wait", "duration_seconds": 10},
                {"id": "verify_syscall", "name": "Verify Syscall Normal", "tool": "trace_syscalls", "type": "verification", "args": {"process_name": "{{top_process_name}}"}, "output_capture": {"syscall_after": "$.top_syscall_count"}, "metric": "syscall_after", "check": "less_than", "value": 10000},
                {"id": "notify_done", "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "args": {"message": "Syscall remediation complete. Syscall count now: {{syscall_after}}", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",          "target": "diag_top_proc",   "sourceHandle": None},
                {"source": "diag_top_proc",  "target": "diag_syscall",    "sourceHandle": None},
                {"source": "diag_syscall",   "target": "dec_high_syscall","sourceHandle": None},
                {"source": "dec_high_syscall","target": "action_kill",    "sourceHandle": "true"},
                {"source": "dec_high_syscall","target": "end",            "sourceHandle": "false"},
                {"source": "action_kill",    "target": "wait_after_kill", "sourceHandle": None},
                {"source": "wait_after_kill","target": "verify_syscall",  "sourceHandle": None},
                {"source": "verify_syscall", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
                {"source": "notify_done",    "target": "end",             "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 12. QUEUE DEPTH CRITICAL — drain backlog (replaces existing SQL row)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-446655440108",
        "name": "Queue Depth Critical — Inspect and Scale Workers",
        "description": (
            "Identifies queue backlog root cause, then scales worker replicas and "
            "restarts the worker service to clear the backlog."
        ),
        "event_type": "application.messaging.queue_depth_critical",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.80,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Queue depth",
                  "Check the current depth of the affected message queue",
                  "check_queue_depth",
                  {"queue_type": "redis", "queue_name": "default"}),
            _diag(2, "Worker processes",
                  "List the top worker processes to see if they are stuck",
                  "top_processes", {"limit": 10, "sort_by": "cpu"}),
            _diag(3, "Worker logs",
                  "Review recent worker log output for error patterns",
                  "get_logs", {"lines": 100}),
        ],
        "actions": [
            _action(1, "Scale up workers (Docker)",
                    "Double the worker replica count to drain the backlog faster.",
                    "scale_up", {"replicas": 2}),
            _action(2, "Restart worker service",
                    "Restart the worker to clear any stuck connections or state.",
                    "restart_service", {"timeout_sec": 30}),
        ],
        "verification_steps": [
            _verify(1, "Queue draining",
                    "Queue depth should decrease below 1000 after scaling workers",
                    "queue_depth_after", "less_than", 1000),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_queue_depth", "name": "Check Queue Depth", "type": "diagnostic", "tool": "check_queue_depth", "args": {}, "output_capture": {"queue_depth": "$.queue_depth"}},
                {"id": "diag_cpu", "name": "Check CPU Utilization", "type": "diagnostic", "tool": "check_cpu", "args": {}, "output_capture": {"cpu_utilization": "$.cpu_percent"}},
                {"id": "dec_scale_workers", "type": "decision", "condition": "queue_depth > 1000 && cpu_utilization < 80", "on_true": "action_scale_up", "on_false": "notify_no_action"},
                {"id": "action_scale_up", "name": "Scale Up Workers", "type": "action", "tool": "scale_up", "args": {"replicas": "+1"}},
                {"id": "wait_after_scale", "name": "Wait for Workers to Start", "type": "wait", "duration_seconds": 30},
                {"id": "verify_queue_depth", "name": "Verify Queue Depth", "type": "verification", "tool": "check_queue_depth", "args": {}, "metric": "queue_depth_after", "check": "less_than", "value": "1000", "output_capture": {"queue_depth_after": "$.queue_depth"}},
                {"id": "notify_done", "name": "Notify Resolution", "type": "notify", "tool": "send_alert", "args": {"message": "Queue depth remediation complete. Queue depth now: {{queue_depth_after}}", "severity": "info"}},
                {"id": "notify_no_action", "name": "Notify: No Scaling Needed", "type": "notify", "tool": "send_alert", "args": {"message": "Queue depth or CPU conditions do not warrant scaling at this time.", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start", "target": "diag_queue_depth", "sourceHandle": None},
                {"source": "diag_queue_depth", "target": "diag_cpu", "sourceHandle": None},
                {"source": "diag_cpu", "target": "dec_scale_workers", "sourceHandle": None},
                {"source": "dec_scale_workers", "target": "action_scale_up", "sourceHandle": "true"},
                {"source": "action_scale_up", "target": "wait_after_scale", "sourceHandle": None},
                {"source": "wait_after_scale", "target": "verify_queue_depth", "sourceHandle": None},
                {"source": "notify_done", "target": "end", "sourceHandle": None},
                {"source": "dec_scale_workers", "target": "notify_no_action", "sourceHandle": "false"},
                {"source": "notify_no_action", "target": "end", "sourceHandle": None},
                {"source": "verify_queue_depth", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 13. LOG ERROR DETECTED — Custom event from log file monitoring (NEW)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-000000000501",
        "name": "Log Error Detected — Diagnose and Recover",
        "description": (
            "Handles errors detected in log files via regex pattern matching. "
            "The watcher's log file monitor tail-watches logs and emits this event "
            "when a pattern (e.g., ERROR, CRITICAL, panic) is found. This runbook "
            "fetches context, checks health, and restarts the service if needed."
        ),
        "event_type": "log.error.spike",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.75,
        "blast_radius": 2,
        # Flat arrays derived from the visual-editor graph (source_steps).
        # Graph: Get Error Rate → Get Logs → DECISION(error_rate>50)
        #        → true: Restart Service → Verify → Notify
        #        → false: Notify Low Error Rate → end
        "diagnostics": [
            _diag(1, "Get Error Rate",
                  "Fetch current error count from logs over a 5-minute window",
                  "get_error_rate", {"window": "5m"}),
            _diag(2, "Get Logs",
                  "Retrieve recent logs matching ERROR pattern for context",
                  "get_logs", {"limit": "100", "pattern": "ERROR"}),
        ],
        "actions": [
            _action(1, "Restart Service",
                    "Restart the service when error rate exceeds threshold",
                    "restart_service", {}),
            _action(2, "Notify Low Error Rate",
                    "Notify that error rate is low and no restart is needed",
                    "send_alert", {"message": "Error rate is low: {{error_rate}}%", "severity": "info"}),
            _action(3, "Notify Resolution",
                    "Send notification that service recovery is complete",
                    "send_alert", {"message": "Service recovery complete. Service status: {{service_http_code}}", "severity": "info"}),
        ],
        "verification_steps": [
            _verify(1, "Verify Service",
                    "Health endpoint should return a successful HTTP code after restart",
                    "service_http_code", "less_than", 400),
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_error_rate", "name": "Get Error Rate", "tool": "get_error_rate", "type": "diagnostic", "args": {"window": "5m"}, "output_capture": {"error_rate": "$.error_count"}},
                {"id": "diag_logs", "name": "Get Logs", "tool": "get_logs", "type": "diagnostic", "args": {"limit": "100", "pattern": "ERROR"}, "output_capture": {"logs": "$.logs"}},
                {"id": "dec_high_error_rate", "type": "decision", "condition": "error_rate > 50", "on_true": "action_restart_service", "on_false": "action_notify_low_error_rate"},
                {"id": "action_restart_service", "name": "Restart Service", "tool": "restart_service", "type": "action", "args": {}},
                {"id": "wait_for_restart", "name": "Wait for Service Restart", "type": "wait", "duration_seconds": 30},
                {"id": "action_notify_low_error_rate", "name": "Notify Low Error Rate", "tool": "send_alert", "type": "notify", "args": {"message": "Error rate is low: {{error_rate}}%", "severity": "info"}},
                {"id": "verify_service", "name": "Verify Service", "tool": "check_health_endpoint", "type": "verification", "args": {}, "check": "less_than", "value": "400", "metric": "service_http_code", "output_capture": {"service_http_code": "$.http_code"}},
                {"id": "notify_done", "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "args": {"message": "Service recovery complete. Service status: {{service_http_code}}", "severity": "info"}},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",                       "target": "diag_error_rate",              "sourceHandle": None},
                {"source": "diag_error_rate",             "target": "diag_logs",                    "sourceHandle": None},
                {"source": "diag_logs",                   "target": "dec_high_error_rate",          "sourceHandle": None},
                {"source": "dec_high_error_rate",         "target": "action_restart_service",       "sourceHandle": "true"},
                {"source": "dec_high_error_rate",         "target": "action_notify_low_error_rate", "sourceHandle": "false"},
                {"source": "action_restart_service",      "target": "wait_for_restart",             "sourceHandle": None},
                {"source": "wait_for_restart",            "target": "verify_service",               "sourceHandle": None},
                {"source": "action_notify_low_error_rate","target": "end",                          "sourceHandle": None},
                {"source": "verify_service",              "target": "incident_update_resolve",      "sourceHandle": None},
                {"source": "incident_update_resolve",     "target": "notify_done",                  "sourceHandle": None},
                {"source": "notify_done",                 "target": "end",                          "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # Web Service Health Alert — HTTP check, optional restart (graph-guided)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "361ad4f9-ada2-4e6d-9825-30fe076fd9e2",
        "name": "Web Service Health Check and Remediation",
        "description": (
            "Checks HTTP reachability of a web service. If unreachable, inspects memory, "
            "CPU, and error rate to decide whether a container restart is warranted. "
            "Restarts only when diagnostics confirm high resource pressure or error rate."
        ),
        "event_type": "infrastructure.service.health_alert",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.92,
        "blast_radius": 3,
        "diagnostics": [
            {"id": "diag_health_endpoint", "name": "Check HTTP Health Endpoint", "tool": "check_health_endpoint", "order": 1, "run_if": "", "args_json": {"url": "http://service-url/health", "timeout_sec": "10", "expected_status": "200"}, "on_failure": "abort", "description": "", "output_capture": {"reachable": "$.reachable"}},
            {"id": "diag_memory",          "name": "Check Memory Usage",          "tool": "check_memory",         "order": 3, "run_if": "", "args_json": {},                                                                                                                               "on_failure": "continue", "description": "", "output_capture": {"mem_pct": "$.mem_percent"}},
            {"id": "diag_cpu",             "name": "Check CPU Usage",             "tool": "check_cpu",            "order": 4, "run_if": "", "args_json": {"interval_sec": "2"},                                                                                                           "on_failure": "continue", "description": "", "output_capture": {"cpu_pct": "$.cpu_percent"}},
            {"id": "diag_top_proc",        "name": "Top CPU Processes",           "tool": "top_processes",        "order": 7, "run_if": "", "args_json": {"limit": "1", "sort_by": "cpu"},                                                                                                "on_failure": "continue", "description": "", "output_capture": {"top_pid": "$.top_process_pid", "top_cpu_pct": "$.top_cpu_percent", "top_proc_name": "$.top_process"}},
            {"id": "diag_error_rate",      "name": "Check Error Rate",            "tool": "get_error_rate",       "order": 9, "run_if": "", "args_json": {"window_min": "5"},                                                                                                             "on_failure": "continue", "description": "", "output_capture": {"has_errors": "$.has_errors"}},
        ],
        "actions": [
            {"id": "action_restart", "name": "Restart Container", "tool": "restart_service", "order": 8,  "run_if": "", "args_json": {"timeout_sec": "30"}, "on_failure": "abort", "description": ""},
            {"id": "notify_done",    "name": "Notify Resolution",  "tool": "send_alert",      "order": 12, "run_if": "", "args_json": {"severity": "info", "message": "Web service health check and remediation complete. Service is now healthy: {{reachable_after}}"}, "on_failure": "abort", "description": ""},
        ],
        "verification_steps": [
            {"order": 1, "type": "verification", "name": "Health endpoint reachable", "description": "Verify the service responds with a 2xx status after restart", "tool": "check_health_endpoint", "metric": "reachable_after", "check": "equals", "value": True, "args_json": {"url": "{service_url}", "timeout_sec": "10", "expected_status": "200"}, "on_failure": "abort", "output_capture": {"reachable_after": "$.reachable"}},
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_health_endpoint",  "args": {"url": "{service_url}", "timeout_sec": "10", "expected_status": "200"}, "name": "Check HTTP Health Endpoint",       "tool": "check_health_endpoint", "type": "diagnostic",    "on_failure": "abort",    "output_capture": {"reachable": "$.reachable"}},
                {"id": "dec_health_endpoint",   "type": "decision",   "on_true": "diag_memory",    "on_false": "end",            "condition": "reachable == false",            "on_failure": "abort"},
                {"id": "diag_memory",           "name": "Check Memory Usage",                                                    "tool": "check_memory",                      "type": "diagnostic",    "on_failure": "continue",  "output_capture": {"mem_pct": "$.mem_percent"}},
                {"id": "diag_cpu",              "args": {"interval_sec": "2"}, "name": "Check CPU Usage",                       "tool": "check_cpu",                         "type": "diagnostic",    "on_failure": "continue",  "output_capture": {"cpu_pct": "$.cpu_percent"}},
                {"id": "dec_memory",            "type": "decision",   "on_true": "action_restart",  "on_false": "dec_cpu",       "condition": "mem_pct > 85",                  "on_failure": "abort"},
                {"id": "dec_cpu",               "type": "decision",   "on_true": "diag_top_proc",   "on_false": "diag_error_rate", "condition": "cpu_pct > 90",               "on_failure": "abort"},
                {"id": "diag_top_proc",         "args": {"limit": "1", "sort_by": "cpu"}, "name": "Top CPU Processes",          "tool": "top_processes",                     "type": "diagnostic",    "on_failure": "continue",  "output_capture": {"top_pid": "$.top_process_pid", "top_cpu_pct": "$.top_cpu_percent", "top_proc_name": "$.top_process"}},
                {"id": "action_restart",        "args": {"timeout_sec": "30"}, "name": "Restart Container",                     "tool": "restart_service",                   "type": "action",        "on_failure": "abort"},
                {"id": "wait_for_startup",      "name": "Wait for Service Startup",                                              "type": "wait",   "duration_seconds": 30},
                {"id": "diag_error_rate",       "args": {"window_min": "5"}, "name": "Check Error Rate",                        "tool": "get_error_rate",                    "type": "diagnostic",    "on_failure": "continue",  "output_capture": {"has_errors": "$.has_errors"}},
                {"id": "dec_error_rate",        "type": "decision",   "on_true": "action_restart",  "on_false": "end",           "condition": "has_errors == true",            "on_failure": "abort"},
                {"id": "verify_health_endpoint","args": {"url": "{service_url}", "timeout_sec": "10", "expected_status": "200"}, "name": "Verify Health Endpoint Recovery",  "tool": "check_health_endpoint", "type": "verification", "check": "equals", "value": True, "metric": "reachable_after", "on_failure": "abort", "output_capture": {"reachable_after": "$.reachable"}},
                {"id": "notify_done",           "args": {"message": "Web service health check and remediation complete. Service is now healthy: {{reachable_after}}", "severity": "info"}, "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "on_failure": "abort"},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "on_failure": "abort", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",                  "target": "diag_health_endpoint",   "sourceHandle": None},
                {"source": "diag_health_endpoint",   "target": "dec_health_endpoint",    "sourceHandle": None},
                {"source": "dec_health_endpoint",    "target": "diag_memory",            "sourceHandle": "true"},
                {"source": "dec_health_endpoint",    "target": "end",                    "sourceHandle": "false"},
                {"source": "diag_memory",            "target": "diag_cpu",               "sourceHandle": None},
                {"source": "diag_cpu",               "target": "dec_memory",             "sourceHandle": None},
                {"source": "dec_memory",             "target": "action_restart",         "sourceHandle": "true"},
                {"source": "dec_memory",             "target": "dec_cpu",                "sourceHandle": "false"},
                {"source": "dec_cpu",                "target": "diag_top_proc",          "sourceHandle": "true"},
                {"source": "dec_cpu",                "target": "diag_error_rate",        "sourceHandle": "false"},
                {"source": "diag_top_proc",          "target": "action_restart",         "sourceHandle": None},
                {"source": "action_restart",         "target": "wait_for_startup",       "sourceHandle": None},
                {"source": "wait_for_startup",       "target": "verify_health_endpoint", "sourceHandle": None},
                {"source": "diag_error_rate",        "target": "dec_error_rate",         "sourceHandle": None},
                {"source": "dec_error_rate",         "target": "action_restart",         "sourceHandle": "true"},
                {"source": "dec_error_rate",         "target": "end",                    "sourceHandle": "false"},
                {"source": "verify_health_endpoint", "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve", "target": "notify_done",           "sourceHandle": None},
                {"source": "notify_done",            "target": "end",                    "sourceHandle": None},
            ],
            "positions": {},
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # Service Unresponsive — Container Level (Check → Restart → Validate)
    # ══════════════════════════════════════════════════════════════════════════
    {
        "id": "550e8400-e29b-41d4-a716-000000000502",
        "name": "Service Unresponsive — Check Status, Restart, Validate",
        "description": (
            "Simple container-level remediation for unresponsive services. "
            "Checks current container status, restarts the container, "
            "and validates it has started successfully."
        ),
        "event_type": "application.availability.service_unresponsive",
        "service": None,
        "environment": None,
        "platform": "any",
        "enabled": True,
        "confidence": 0.92,
        "blast_radius": 2,
        "diagnostics": [
            _diag(1, "Check container status",
                  "Check the status of the container/resource across the platform",
                  "check_container_status",
                  {}),
        ],
        "actions": [
            _action(1, "Restart container",
                    "Gracefully restart the unresponsive container with 10s timeout",
                    "restart_service",
                    {"target": "{target}", "timeout_sec": 10}),
        ],
        "verification_steps": [
            {
                "order": 1,
                "type": "verification",
                "name": "Container is running",
                "description": "Verify the container has started and is in 'running' state",
                "metric": "container_status",
                "check": "equals",
                "value": "running"
            },
        ],
        "source_steps": {
            "steps": [
                {"id": "diag_service_status", "name": "Check Container Service Status", "tool": "check_container_status", "type": "diagnostic", "on_failure": "abort", "output_capture": {"container_status": "$.container_status", "container_running": "$.container_running"}},
                {"id": "dec_service_down", "type": "decision", "on_true": "action_restart_service", "on_false": "end", "condition": "diag_service_status.container_status != 'running'", "on_failure": "abort"},
                {"id": "action_restart_service", "name": "Restart Service", "tool": "restart_service", "type": "action", "on_failure": "abort", "args": {"timeout_sec": "30"}},
                {"id": "wait_for_startup", "name": "Wait for Service Startup", "type": "wait", "duration_seconds": 30},
                {"id": "verify_service", "name": "Verify Service Status", "tool": "check_container_status", "type": "verification", "check": "equals", "value": "True", "metric": "container_running", "on_failure": "abort", "output_capture": {"is_running": "$.container_running", "container_status_after": "$.container_status"}},
                {"id": "notify_done", "args": {"message": "Service remediation complete. Service status now: {{container_status_after}}", "severity": "info"}, "name": "Notify Resolution", "tool": "send_alert", "type": "notify", "on_failure": "abort"},
                {"id": "incident_update_resolve", "name": "Mark Resolved", "type": "incident_update", "on_failure": "abort", "state": "resolved"},
            ],
            "edges": [
                {"source": "start",                  "target": "diag_service_status",    "sourceHandle": None},
                {"source": "diag_service_status",    "target": "dec_service_down",        "sourceHandle": None},
                {"source": "dec_service_down",       "target": "action_restart_service",  "sourceHandle": "true"},
                {"source": "dec_service_down",       "target": "verify_service",          "sourceHandle": "false"},
                {"source": "action_restart_service", "target": "wait_for_startup",        "sourceHandle": None},
                {"source": "wait_for_startup",       "target": "verify_service",          "sourceHandle": None},
                {"source": "verify_service",         "target": "incident_update_resolve", "sourceHandle": None},
                {"source": "incident_update_resolve","target": "notify_done",             "sourceHandle": None},
                {"source": "notify_done",            "target": "end",                     "sourceHandle": None},
            ],
            "positions": {},
        },
    },
]
