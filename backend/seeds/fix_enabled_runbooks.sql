-- ─────────────────────────────────────────────────────────────────────────────
-- Fix enabled runbook tool names + add missing catalog entries
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Add missing catalog entries for tools used in enabled runbooks ─────────

-- scale_service (alias for scale_up — runbooks use this name)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'scale_service',
  'Scale Service',
  'Scale a service up or down by adjusting replica count. Alias for scale_up, used in runbooks.',
  'remediation_safe', 2, false, true,
  'docker compose up -d --scale {target}={replicas}',
  '{
    "docker":     "docker compose up -d --scale {target}={replicas}",
    "kubernetes": "kubectl scale deployment/{target} --replicas={replicas} -n {namespace}"
  }'::json,
  '[
    {"name":"target",    "type":"string",  "required":true,  "description":"Service / deployment name"},
    {"name":"replicas",  "type":"integer", "required":true,  "default":"2", "description":"Target replica count"},
    {"name":"namespace", "type":"string",  "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- pod_restart (maps to restart_service semantics — used in Container Down and Service Unresponsive)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'pod_restart',
  'Restart Pod / Container',
  'Restart a container (Docker) or trigger a rolling pod restart (Kubernetes). Used in Container Down and Service Unresponsive runbooks.',
  'remediation_safe', 2, false, true,
  'docker restart --time {timeout_sec} {target}',
  '{
    "docker":     "docker restart --time {timeout_sec} {target}",
    "kubernetes": "kubectl rollout restart deployment/{target} -n {namespace}",
    "ssh":        "ssh {host} systemctl restart {target}"
  }'::json,
  '[
    {"name":"target",      "type":"string",  "required":true,  "description":"Container / service name"},
    {"name":"timeout_sec", "type":"integer", "required":false, "default":"10",      "description":"Graceful shutdown timeout (seconds)"},
    {"name":"namespace",   "type":"string",  "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- log_analysis (diagnostic — reads recent logs for errors, used in 3 enabled runbooks)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'log_analysis',
  'Log Analysis',
  'Fetch and filter recent logs from a container, pod, or host service for error diagnosis.',
  'diagnostic', 1, false, true,
  'docker logs {container} --tail {lines} 2>&1',
  '{
    "docker":     "docker logs {container} --tail {lines} 2>&1",
    "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines}",
    "ssh":        "ssh {host} journalctl -u {service} -n {lines} --no-pager"
  }'::json,
  '[
    {"name":"container", "type":"string",  "required":false, "default":"",   "description":"Container / pod name (Docker/K8s)"},
    {"name":"lines",     "type":"integer", "required":false, "default":"50", "description":"Number of recent lines to retrieve"},
    {"name":"namespace", "type":"string",  "required":false, "default":"default", "description":"K8s namespace"},
    {"name":"service",   "type":"string",  "required":false, "default":"",   "description":"systemd service name (SSH mode)"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- pod_logs (diagnostic — K8s pod logs, used in Container Down runbook)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'pod_logs',
  'Pod / Container Logs',
  'Retrieve recent log lines from a Kubernetes pod or Docker container.',
  'diagnostic', 1, false, true,
  'docker logs {container} --tail {lines} 2>&1',
  '{
    "docker":     "docker logs {container} --tail {lines} 2>&1",
    "kubernetes": "kubectl logs {pod} -n {namespace} --tail={lines} --timestamps"
  }'::json,
  '[
    {"name":"container", "type":"string",  "required":false, "default":"",      "description":"Container name (Docker)"},
    {"name":"pod",       "type":"string",  "required":false, "default":"",      "description":"Pod name (Kubernetes)"},
    {"name":"lines",     "type":"integer", "required":false, "default":"100",   "description":"Number of tail lines"},
    {"name":"namespace", "type":"string",  "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- health_check (diagnostic — HTTP/TCP probe, used in Container Down runbook)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'health_check',
  'Health Check',
  'HTTP health probe against a service endpoint. Returns HTTP status code.',
  'diagnostic', 1, false, true,
  'curl -Is --connect-timeout {timeout_sec} {protocol}://{host}:{port}/health',
  '{
    "any": "curl -Is --connect-timeout {timeout_sec} {protocol}://{host}:{port}/health"
  }'::json,
  '[
    {"name":"host",        "type":"string",  "required":true,  "description":"Target hostname or IP"},
    {"name":"port",        "type":"integer", "required":true,  "default":"8000", "description":"Target port"},
    {"name":"protocol",    "type":"string",  "required":false, "default":"http", "description":"http or https"},
    {"name":"timeout_sec", "type":"integer", "required":false, "default":"5",    "description":"Connect timeout seconds"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- process_signal (remediation — send a specific signal, used in Service Unresponsive runbook)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, process_rules, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'process_signal',
  'Send Process Signal',
  'Send a POSIX signal to a named process (SIGTERM, SIGHUP, SIGKILL, etc.). More targeted than process_kill — useful for graceful shutdown or config reload.',
  'remediation_intrusive', 2, false, true,
  'docker exec {container} kill -{signal} {process_name}',
  '{
    "docker":     "docker exec {container} kill -{signal} {process_name}",
    "kubernetes": "kubectl exec {pod} -n {namespace} -- kill -{signal} {process_name}",
    "ssh":        "ssh {host} kill -{signal} {process_name}"
  }'::json,
  '[
    {"name":"container",     "type":"string", "required":false, "default":"",        "description":"Target container"},
    {"name":"process_name",  "type":"string", "required":true,  "description":"Process name (exact match)"},
    {"name":"signal",        "type":"string", "required":false, "default":"SIGTERM", "description":"Signal: SIGTERM | SIGHUP | SIGKILL | SIGUSR1"},
    {"name":"namespace",     "type":"string", "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  '[
    {"priority":1,  "allow":false, "pattern":"^(dockerd|containerd|containerd-shim.*)$", "description":"Container runtime"},
    {"priority":2,  "allow":false, "pattern":"^(postgres|pg_.*)$",                       "description":"PostgreSQL"},
    {"priority":3,  "allow":false, "pattern":"^(redis-server|redis-.*)$",                "description":"Redis"},
    {"priority":4,  "allow":false, "pattern":"^(java)$",                                 "description":"JVM"},
    {"priority":5,  "allow":false, "pattern":"^(python3?|uvicorn|celery|gunicorn)$",     "description":"Platform backend"},
    {"priority":6,  "allow":false, "pattern":"^(sshd|systemd.*|init.*)$",               "description":"System processes"},
    {"priority":20, "allow":true,  "pattern":"^yes$",    "description":"CPU-bomb test"},
    {"priority":21, "allow":true,  "pattern":"^stress(-ng)?$", "description":"stress/stress-ng"},
    {"priority":22, "allow":true,  "pattern":"^nginx$",  "description":"NGINX worker reload"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- process_info (diagnostic — used in High Syscall Intensity runbook)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'process_info',
  'Process Info',
  'Get detailed information about a named process: PID, CPU, memory, open files, status.',
  'diagnostic', 1, false, true,
  'docker exec {container} ps -fp $(pgrep {process_name}) 2>/dev/null',
  '{
    "docker":     "docker exec {container} ps -fp $(pgrep {process_name}) 2>/dev/null",
    "kubernetes": "kubectl exec {pod} -n {namespace} -- ps -fp $(pgrep {process_name}) 2>/dev/null",
    "ssh":        "ssh {host} ps -fp $(pgrep {process_name}) 2>/dev/null"
  }'::json,
  '[
    {"name":"container",    "type":"string", "required":false, "default":"", "description":"Container / pod name"},
    {"name":"process_name", "type":"string", "required":true,  "description":"Process name to inspect"},
    {"name":"namespace",    "type":"string", "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;

-- process_verify (diagnostic — used in High Syscall Intensity runbook, verifies process was killed)
INSERT INTO approved_actions (id, tool_name, name, description, category, blast_radius, requires_approval, enabled, command, command_variants, parameters, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  'process_verify',
  'Verify Process State',
  'Check whether a process is still running. Used post-kill to confirm termination.',
  'diagnostic', 1, false, true,
  'docker exec {container} pgrep {process_name} && echo RUNNING || echo STOPPED',
  '{
    "docker":     "docker exec {container} pgrep {process_name} && echo RUNNING || echo STOPPED",
    "kubernetes": "kubectl exec {pod} -n {namespace} -- pgrep {process_name} && echo RUNNING || echo STOPPED",
    "ssh":        "ssh {host} pgrep {process_name} && echo RUNNING || echo STOPPED"
  }'::json,
  '[
    {"name":"container",    "type":"string", "required":false, "default":"", "description":"Container / pod name"},
    {"name":"process_name", "type":"string", "required":true,  "description":"Process name to check"},
    {"name":"namespace",    "type":"string", "required":false, "default":"default", "description":"K8s namespace"}
  ]'::json,
  NOW(), NOW()
) ON CONFLICT (tool_name) DO NOTHING;
