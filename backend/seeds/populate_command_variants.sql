-- Populate command_variants for the 11 core cross-environment tools.
-- Variants use mode="host" (command runs on the watcher container).
-- Transport prefix is included in each variant command.
-- Resolution order: command_variants[adapter_mode] → command_variants["any"] → command

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} ps aux --sort=-{sort_by} | head -{limit}",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- ps aux --sort=-{sort_by} | head -{limit}",
  "ssh":        "ssh {host} ps aux --sort=-{sort_by} | head -{limit}"
}'::json WHERE tool_name = 'top_processes';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- df -h {path}",
  "ssh":        "ssh {host} df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20"
}'::json WHERE tool_name = 'check_disk_usage';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} free -h",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- free -h",
  "ssh":        "ssh {host} free -h && vmstat 1 3"
}'::json WHERE tool_name = 'check_memory';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} top -bn{interval_sec} | head -20",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- top -bn{interval_sec} | head -20",
  "ssh":        "ssh {host} top -bn{interval_sec} | head -20"
}'::json WHERE tool_name = 'check_cpu';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker logs {container} --tail {lines} 2>&1",
  "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines}",
  "ssh":        "ssh {host} journalctl -u {service} -n {lines} --no-pager"
}'::json WHERE tool_name = 'get_logs';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} find {path} -type f -name \"*.log\" -mtime +{days_to_retain} -delete",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- find {path} -type f -name \"*.log\" -mtime +{days_to_retain} -delete",
  "ssh":        "ssh {host} find {path} -type f -name \"*.log\" -mtime +{days_to_retain} -delete"
}'::json WHERE tool_name = 'cleanup_logs';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker restart --time {timeout_sec} {target}",
  "kubernetes": "kubectl rollout restart deployment/{target} -n {namespace}",
  "ssh":        "ssh {host} systemctl restart {target}"
}'::json WHERE tool_name = 'restart_service';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} kill -{signal} {process_name}",
  "kubernetes": "kubectl exec {pod} -n {namespace} -- kill -{signal} {process_name}",
  "ssh":        "ssh {host} kill -{signal} {process_name}"
}'::json WHERE tool_name = 'process_kill';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker exec {container} kill -9 {process_name} && docker restart {container}",
  "kubernetes": "kubectl delete pod {pod} -n {namespace} --grace-period=0",
  "ssh":        "ssh {host} kill -9 {process_name} && systemctl restart {service}"
}'::json WHERE tool_name = 'force_restart';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker compose up -d --scale {target}={replicas}",
  "kubernetes": "kubectl scale deployment/{target} --replicas={replicas} -n {namespace}"
}'::json WHERE tool_name = 'scale_up';

UPDATE approved_actions SET command_variants = '{
  "docker":     "docker compose up -d --scale {target}={replicas}",
  "kubernetes": "kubectl scale deployment/{target} --replicas={replicas} -n {namespace}"
}'::json WHERE tool_name = 'scale_down';
