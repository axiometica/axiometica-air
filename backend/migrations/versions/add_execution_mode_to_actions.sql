-- Add execution_mode column to approved_actions
-- Distinguishes between "host" (run on watcher) vs "target" (run inside container/VM)

ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(20) DEFAULT 'target';

-- Docker commands run inside target container
UPDATE approved_actions SET execution_mode = 'target' 
WHERE tool_name IN (
    'top_processes', 'list_containers', 'check_health_endpoint', 
    'get_process_info', 'get_logs', 'get_thread_dump', 'trace_syscalls'
);

-- Host-level commands run on watcher/jump host
UPDATE approved_actions SET execution_mode = 'host' 
WHERE tool_name IN (
    'restart_service', 'service_restart', 'force_restart', 
    'host_service_restart', 'host_service_status', 'host_service_stop',
    'host_process_kill', 'host_top_processes', 'host_reboot',
    'host_disk_usage', 'host_logs', 'host_log_cleanup', 'host_netstat', 'host_process_info',
    'k8s_rollout_restart', 'k8s_rollout_status', 'k8s_delete_pod', 'k8s_drain_node',
    'win_service_restart', 'win_service_stop', 'win_service_start', 'win_process_kill', 'win_reboot'
);
