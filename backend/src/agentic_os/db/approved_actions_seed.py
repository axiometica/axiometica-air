"""
Seed data: 40 standard approved actions.

Categories
----------
diagnostic           – read-only observation, blast_radius 1
remediation_safe     – low-risk automated fix, blast_radius 1-2
remediation_intrusive – disruptive / potentially irreversible, blast_radius 2-3

Process rules (intrusive only)
-------------------------------
Evaluated in ascending priority order; first matching rule wins.
If NO rule matches → request is DENIED (whitelist-by-default).
  allow: true  → permit
  allow: false → explicitly block
"""

# ─────────────────────────────────────────────────────────────
# Default process allow-list rules shared across kill-type actions
# ─────────────────────────────────────────────────────────────
DEFAULT_PROCESS_RULES = [
    # ── Explicitly DENY critical system / infra processes ──────
    {"priority": 1,  "allow": False, "pattern": r"^(dockerd|containerd|containerd-shim.*)$",
     "description": "Container runtime — never kill"},
    {"priority": 2,  "allow": False, "pattern": r"^(postgres|pg_.*)$",
     "description": "PostgreSQL processes — never kill"},
    {"priority": 3,  "allow": False, "pattern": r"^(redis-server|redis-.*)$",
     "description": "Redis processes — never kill"},
    {"priority": 4,  "allow": False, "pattern": r"^(java)$",
     "description": "JVM (neo4j etc.) — never kill"},
    {"priority": 5,  "allow": False, "pattern": r"^(python3?|uvicorn|celery|gunicorn)$",
     "description": "Platform backend processes — never kill"},
    {"priority": 6,  "allow": False, "pattern": r"^(node|vite|npm|yarn)$",
     "description": "Frontend dev-server — never kill"},
    {"priority": 7,  "allow": False, "pattern": r"^(sshd|systemd.*|initd?|init\.d|upstart|kernel.*)$",
     "description": "System/init processes — never kill"},
    {"priority": 8,  "allow": False, "pattern": r"^(bpftrace|ebpf.*)$",
     "description": "Sentinel monitoring — never kill"},

    # ── ALLOW known safe test / benchmark processes ─────────────
    {"priority": 20, "allow": True,  "pattern": r"^yes$",
     "description": "Classic CPU-bomb test (yes > /dev/null)"},
    {"priority": 21, "allow": True,  "pattern": r"^stress(-ng)?$",
     "description": "stress / stress-ng load testers"},
    {"priority": 22, "allow": True,  "pattern": r"^fio$",
     "description": "I/O benchmark tool"},
    {"priority": 23, "allow": True,  "pattern": r"^iperf3?$",
     "description": "Network bandwidth test"},
    {"priority": 24, "allow": True,  "pattern": r"^ab$",
     "description": "Apache HTTP bench"},
    {"priority": 25, "allow": True,  "pattern": r"^wrk2?$",
     "description": "HTTP load tester"},
    {"priority": 26, "allow": True,  "pattern": r"^dd$",
     "description": "Disk write test"},
    {"priority": 27, "allow": True,  "pattern": r"^sysbench$",
     "description": "Sysbench benchmark"},
    {"priority": 28, "allow": True,  "pattern": r"^(cat|tail|head|grep|awk|sed)$",
     "description": "Common Unix utilities safe to kill"},
    {"priority": 29, "allow": True,  "pattern": r"^sleep$",
     "description": "Sleep process"},
    {"priority": 30, "allow": True,  "pattern": r"^(ping|nc|ncat|curl|wget)$",
     "description": "Network diagnostic tools"},

    # ── ALLOW app worker processes (regex covers common patterns) ──
    {"priority": 40, "allow": True,  "pattern": r"^worker[-_].*",
     "description": "App worker processes matching 'worker-*'"},
    {"priority": 41, "allow": True,  "pattern": r"^job[-_].*",
     "description": "App job processes matching 'job-*'"},
    {"priority": 42, "allow": True,  "pattern": r"^task[-_].*",
     "description": "App task processes matching 'task-*'"},
]

# ─────────────────────────────────────────────────────────────
# 40 Actions
# ─────────────────────────────────────────────────────────────
APPROVED_ACTIONS = [

    # ══════════════════════════════════════════════════════════
    # DIAGNOSTICS  (blast_radius=1, read-only)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "top_processes",
        "name": "Top Processes by Resource",
        "description": "List the top N processes ranked by CPU and memory usage inside a container.",
        # BusyBox ps does not support --sort; use top -bn1 instead (works on both GNU and BusyBox).
        # The | head is inside sh -c so it runs in the container, not on the host.
        "command": "docker exec {target} sh -c 'top -bn1 | tail -n +5 | head -{limit}'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'top -bn1 | tail -n +5 | head -{limit}'",
            "ssh":        "ssh {target} sh -c 'ps aux --sort=-{sort_by} 2>/dev/null | head -{limit} || top -bn1 | tail -n +5 | head -{limit}'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'top -bn1 | tail -n +5 | head -{limit}'",
            "vcenter":    "sh -c 'ps aux --sort=-{sort_by} | head -{limit}'",
            "aws_ssm":    "sh -c 'ps aux --sort=-{sort_by} | head -{limit}'",
            "azure":      "sh -c 'ps aux --sort=-{sort_by} | head -{limit}'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",  "type": "string",  "required": True,  "description": "Target container name"},
            {"name": "limit",      "type": "integer", "required": False, "default": 10,
             "description": "Number of processes to return"},
            {"name": "sort_by",    "type": "string",  "required": False, "default": "cpu",
             "description": "Sort field: cpu | memory"},
        ],
    },
    {
        "tool_name": "list_connections",
        "name": "List Network Connections",
        "description": "Show active TCP/UDP connections and listening ports for a container.",
        # ss is not available in Alpine/slim images; fall back to netstat then /proc/net/tcp.
        "command": "docker exec {target} sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop 2>/dev/null || (echo \"Local-Address           Remote-Address          State\" && awk \"NR>1{print \\$2, \\$3, \\$4}\" /proc/net/tcp /proc/net/tcp6 2>/dev/null | head -50)'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop 2>/dev/null || (echo \"Local-Address           Remote-Address          State\" && awk \"NR>1{print \\$2, \\$3, \\$4}\" /proc/net/tcp /proc/net/tcp6 2>/dev/null | head -50)'",
            "ssh":        "ssh {target} sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop 2>/dev/null'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop 2>/dev/null || awk \"NR>1{print \\$2, \\$3, \\$4}\" /proc/net/tcp /proc/net/tcp6 2>/dev/null | head -50'",
            "vcenter":    "sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop'",
            "aws_ssm":    "sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop'",
            "azure":      "sh -c 'ss -tunaop 2>/dev/null || netstat -tunaop'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "state",  "type": "string", "required": False, "default": "all",
             "description": "Connection state filter (informational — used in variants only): all | established | listening | time_wait"},
        ],
    },
    {
        "tool_name": "check_disk_usage",
        "name": "Check Disk Usage",
        "description": "Report disk utilisation by path/volume, highlighting directories over a threshold.",
        # All shell operators must be inside sh -c so they run in the container,
        # not on the Docker host (Docker exec has no shell of its own).
        "command": "docker exec {target} sh -c 'df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20'",
            "ssh":        "ssh {target} sh -c 'df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20'",
            "vcenter":    "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
            "aws_ssm":    "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
            "azure":      "df -h {path} && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "path",      "type": "string", "required": False, "default": "/",
             "description": "Filesystem path to inspect"},
            {"name": "threshold_pct", "type": "integer", "required": False, "default": 80},
        ],
    },
    {
        "tool_name": "check_memory",
        "name": "Memory Usage Breakdown",
        "description": "Detailed breakdown of memory consumption: RSS, virtual, shared, cached, swap.",
        # All shell operators inside sh -c.  free fallback reads /proc/meminfo directly
        # so the command works even on images without the procps package.
        "command": "docker exec {target} sh -c 'free -h 2>/dev/null || awk \"/MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree/{printf \\\"%-20s %s kB\\n\\\", \\$1, \\$2}\" /proc/meminfo; echo; grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'free -h 2>/dev/null || awk \"/MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree/{printf \\\"%-20s %s kB\\n\\\", \\$1, \\$2}\" /proc/meminfo; echo; grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
            "ssh":        "ssh {target} sh -c 'free -h 2>/dev/null || grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'free -h 2>/dev/null || grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
            "vcenter":    "sh -c 'free -h 2>/dev/null || grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
            "aws_ssm":    "sh -c 'free -h 2>/dev/null || grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
            "azure":      "sh -c 'free -h 2>/dev/null || grep -E \"Mem|Swap|Cached\" /proc/meminfo'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "check_cpu",
        "name": "CPU Usage Per Core",
        "description": "Per-core CPU utilisation snapshot and 1/5/15 minute load averages.",
        # | head must be inside sh -c so it executes in the container, not on the Docker host.
        "command": "docker exec {target} sh -c 'top -bn{interval_sec} | head -20'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'top -bn{interval_sec} | head -20'",
            "ssh":        "ssh {target} sh -c 'top -bn{interval_sec} | head -20'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'top -bn{interval_sec} | head -20'",
            "vcenter":    "sh -c 'top -bn{interval_sec} | head -20'",
            "aws_ssm":    "sh -c 'top -bn{interval_sec} | head -20'",
            "azure":      "sh -c 'top -bn{interval_sec} | head -20'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "interval_sec", "type": "integer", "required": False, "default": 2},
        ],
    },
    {
        "tool_name": "get_logs",
        "name": "Fetch Container Logs",
        "description": "Retrieve recent stdout/stderr from a container, optionally filtered by pattern.",
        "command": "docker logs {target} --tail {lines} 2>&1 | grep --color=never -E '{pattern}'",
        "command_variants": {
            "docker":     "docker logs {target} --tail {lines} 2>&1 | grep --color=never -E '{pattern}'",
            "ssh":        "ssh {target} journalctl -n {lines} --no-pager 2>&1 | grep --color=never -E '{pattern}'",
            "kubernetes": "kubectl logs {target} -n {namespace} --tail={lines} 2>&1 | grep --color=never -E '{pattern}'",
            "vcenter":    "journalctl -n {lines} --no-pager 2>&1 | grep --color=never -E '{pattern}'",
            "aws_ssm":    "journalctl -n {lines} --no-pager 2>&1 | grep --color=never -E '{pattern}'",
            "azure":      "journalctl -n {lines} --no-pager 2>&1 | grep --color=never -E '{pattern}'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string",  "required": True},
            {"name": "lines",     "type": "integer", "required": False, "default": 200},
            {"name": "since",     "type": "string",  "required": False, "description": "e.g. '10m', '1h'"},
            {"name": "pattern",   "type": "string",  "required": False, "default": ".",
             "description": "grep -E regex to filter; '.' matches all lines (default)"},
        ],
    },
    {
        "tool_name": "ping_service",
        "name": "Ping Service (TCP/HTTP)",
        "description": "Test connectivity to a host/port or HTTP endpoint and measure response time.",
        "command": "curl -Is --connect-timeout {timeout_sec} {protocol}://{host}:{port}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",     "type": "string",  "required": True},
            {"name": "port",     "type": "integer", "required": False},
            {"name": "protocol", "type": "string",  "required": False, "default": "tcp",
             "description": "tcp | http | https"},
            {"name": "timeout_sec", "type": "integer", "required": False, "default": 5},
        ],
        "output_fields": [
            {"field": "http_code", "kind": "regex", "pattern": r"HTTP/[\d.]+\s+(\d{3})", "type": "integer"},
            {"field": "reachable", "kind": "regex", "pattern": r"HTTP/[\d.]+\s+[1-4]\d\d", "type": "boolean"},
        ],
    },
    {
        "tool_name": "icmp_ping",
        "name": "ICMP Ping",
        "description": "Send raw ICMP echo requests to test network-layer reachability to a host — distinct from ping_service, which only checks TCP/HTTP connectivity.",
        # -c 3: send 3 probes, -W 2: 2s reply timeout per probe — bounded runtime even when the host is unreachable.
        "command": "ping -c 3 -W 2 {host}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
        ],
        "output_fields": [
            {"field": "packet_loss_percent", "kind": "regex", "pattern": r"(\d+)% packet loss", "type": "integer"},
            {"field": "packets_received",    "kind": "regex", "pattern": r"(\d+) (?:packets )?received", "type": "integer"},
            {"field": "rtt_avg_ms",          "kind": "regex", "pattern": r"=\s*[\d.]+/([\d.]+)/", "type": "float"},
            {"field": "reachable",           "kind": "regex", "pattern": r"bytes from", "type": "boolean"},
        ],
    },
    {
        "tool_name": "check_health_endpoint",
        "name": "HTTP Health Check",
        "description": "Call an HTTP endpoint and report the response code. Always succeeds as a diagnostic step — use the captured http_code output to determine service health. Pass url as http://{target}:PORT/path to resolve the resource name at runtime.",
        # Always exit 0 so the step is never marked 'failed' just because the service is down.
        # http_code=000 means unreachable; anything 2xx means healthy.
        "command": "rm -f /tmp/health_body.txt; code=$(curl -s -o /tmp/health_body.txt -w \"%{http_code}\" --max-time {timeout_sec} {url} 2>/dev/null); echo \"http_code=${code:-000}\"; body=$(cat /tmp/health_body.txt 2>/dev/null | head -c 200); [ -n \"$body\" ] && echo \"response_body=$body\"; true",
        # Deliberately no "docker" command_variant — this check is meant to measure
        # network-level reachability the same way watcher's own built-in external
        # checks do (curl runs from watcher itself, across the bridge network to the
        # target's published port). Wrapping it in `docker exec {target}` would make
        # the target curl its own published name from inside itself, which is a Docker
        # Desktop hairpin-NAT self-reference — confirmed empirically to be flaky
        # (2/3 failures in testing) — whereas a normal cross-container call from
        # watcher is reliable (5/5 in testing). See incident_agents.py's
        # _substitute_runbook_parameters for the localhost-rewrite safety net that
        # still protects any *other* tool that genuinely execs into a target and
        # references that same target's hostname.
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "url",             "type": "string",  "required": True,
             "description": "Full URL to check. Use http://{target}:PORT/path to resolve the resource name at runtime."},
            {"name": "expected_status", "type": "integer", "required": False, "default": 200},
            {"name": "timeout_sec",     "type": "integer", "required": False, "default": 10},
        ],
        "output_fields": [
            {"field": "http_code",      "kind": "regex", "pattern": r"http_code=(\d+)",    "type": "integer"},
            {"field": "response_body",  "kind": "regex", "pattern": r"response_body=(.+)", "type": "string"},
            {"field": "reachable",      "kind": "regex", "pattern": r"http_code=([1-4]\d\d)", "type": "boolean"},
        ],
    },
    {
        "tool_name": "list_open_files",
        "name": "List Open File Handles",
        "description": "Run lsof inside a container to show open files, sockets, and pipe handles.",
        "command": "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else echo \"[INFO] Process {process_name} not found — showing all open files\"; lsof 2>/dev/null | head -50; fi'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else echo \"[INFO] Process {process_name} not found — showing all open files\"; lsof 2>/dev/null | head -50; fi'",
            "ssh":        "ssh {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else echo \"[INFO] Process {process_name} not found — showing all open files\"; lsof 2>/dev/null | head -50; fi'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else echo \"[INFO] Process {process_name} not found — showing all open files\"; lsof 2>/dev/null | head -50; fi'",
            "vcenter":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else lsof 2>/dev/null | head -50; fi'",
            "aws_ssm":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else lsof 2>/dev/null | head -50; fi'",
            "azure":      "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then lsof -p \"$PID\" 2>/dev/null; else lsof 2>/dev/null | head -50; fi'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string",  "required": True},
            {"name": "process_name", "type": "string",  "required": False,
             "description": "Filter by process name"},
        ],
    },
    {
        "tool_name": "get_process_info",
        "name": "Process Detail",
        "description": "Show PID, parent, children, open FDs, start time, and cgroup for a named process.",
        "command": "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
            "ssh":        "ssh {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
            "vcenter":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
            "aws_ssm":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
            "azure":      "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null; else echo \"[INFO] Process {process_name} not found — may have already exited\"; fi'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
        ],
        "output_fields": [
            {"field": "process_found",  "kind": "regex", "pattern": r"State:\s*\S\s*\(",        "type": "boolean"},
            {"field": "pid",            "kind": "regex", "pattern": r"(?:^|\n)Pid:\s*(\d+)",     "type": "integer"},
            {"field": "process_state",  "kind": "regex", "pattern": r"State:\s*\S\s*\(([^)]+)\)","type": "string"},
            {"field": "mem_rss_kb",     "kind": "regex", "pattern": r"VmRSS:\s*(\d+)\s*kB",      "type": "integer"},
        ],
    },
    {
        "tool_name": "check_swap",
        "name": "Swap Usage and Pressure",
        "description": "Report swap usage, swap-in/out rates, and memory pressure events.",
        # vmstat is not in Alpine/slim images; fall back to /proc/vmstat key fields.
        # All shell operators inside sh -c so they run in the container.
        # vmstat is not in Alpine/slim images; fall back to /proc/vmstat key fields.
        # grep is used instead of awk to avoid quote-escaping issues inside sh -c.
        "command": "docker exec {target} sh -c 'cat /proc/swaps && echo --- && (vmstat 1 3 2>/dev/null || grep -E \"pswpin|pswpout|pgpgin|pgpgout|pgfault\" /proc/vmstat)'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'cat /proc/swaps && echo --- && (vmstat 1 3 2>/dev/null || grep -E \"pswpin|pswpout|pgpgin|pgpgout|pgfault\" /proc/vmstat)'",
            "ssh":        "ssh {target} sh -c 'cat /proc/swaps && (vmstat 1 3 2>/dev/null || grep -E \"pswpin|pswpout\" /proc/vmstat)'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'cat /proc/swaps && echo --- && (vmstat 1 3 2>/dev/null || grep -E \"pswpin|pswpout|pgpgin|pgpgout|pgfault\" /proc/vmstat)'",
            "vcenter":    "sh -c 'cat /proc/swaps && vmstat 1 3'",
            "aws_ssm":    "sh -c 'cat /proc/swaps && vmstat 1 3'",
            "azure":      "sh -c 'cat /proc/swaps && vmstat 1 3'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "check_dns",
        "name": "DNS Resolution Test",
        "description": "Resolve a hostname and measure lookup latency from inside a container.",
        "command": "docker exec {target} nslookup {hostname} {dns_server}",
        "command_variants": {
            "docker":     "docker exec {target} nslookup {hostname} {dns_server}",
            "ssh":        "ssh {target} nslookup {hostname} {dns_server}",
            "kubernetes": "kubectl exec {target} -n {namespace} -- nslookup {hostname} {dns_server}",
            "vcenter":    "nslookup {hostname} {dns_server}",
            "aws_ssm":    "nslookup {hostname} {dns_server}",
            "azure":      "nslookup {hostname} {dns_server}",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "hostname",  "type": "string", "required": True},
            {"name": "dns_server","type": "string", "required": False},
        ],
    },
    {
        "tool_name": "check_ports",
        "name": "Open Port Scan",
        "description": "Scan a range of ports on a target host to identify which are open/closed.",
        "command": "docker exec {target} nc -zv {host} {port_range} 2>&1",
        "command_variants": {
            "docker":     "docker exec {target} nc -zv {host} {port_range} 2>&1",
            "ssh":        "ssh {target} nc -zv {host} {port_range} 2>&1",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'nc -zv {host} {port_range} 2>&1'",
            "vcenter":    "nc -zv {host} {port_range} 2>&1",
            "aws_ssm":    "nc -zv {host} {port_range} 2>&1",
            "azure":      "nc -zv {host} {port_range} 2>&1",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",       "type": "string",  "required": True},
            {"name": "port_range", "type": "string",  "required": False, "default": "1-1024",
             "description": "e.g. '80,443' or '8000-9000'"},
        ],
    },
    {
        "tool_name": "get_error_rate",
        "name": "Error Rate from Logs",
        "description": "Count ERROR/WARN lines in container logs over a time window and compute error rate.",
        "command": "docker logs {target} --since {window_min}m 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
        "command_variants": {
            "docker":     "docker logs {target} --since {window_min}m 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
            "ssh":        "ssh {target} journalctl --since '{window_min} min ago' 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
            "kubernetes": "kubectl logs {target} -n {namespace} --since={window_min}m 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
            "vcenter":    "journalctl --since '{window_min} min ago' 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
            "aws_ssm":    "journalctl --since '{window_min} min ago' 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
            "azure":      "journalctl --since '{window_min} min ago' 2>&1 | grep -cE '(ERROR|WARN)' || echo 0",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",  "type": "string",  "required": True},
            {"name": "window_min", "type": "integer", "required": False, "default": 5},
        ],
    },
    {
        "tool_name": "check_queue_depth",
        "name": "Message Queue Depth",
        "description": "Query the length of a Redis list, AMQP queue, or Kafka topic.",
        "command": "docker exec {target} redis-cli LLEN {queue_name}",
        "command_variants": {
            "docker":     "docker exec {target} redis-cli LLEN {queue_name}",
            "ssh":        "ssh {target} redis-cli LLEN {queue_name}",
            "kubernetes": "kubectl exec {target} -n {namespace} -- redis-cli LLEN {queue_name}",
            "vcenter":    "redis-cli LLEN {queue_name}",
            "aws_ssm":    "redis-cli LLEN {queue_name}",
            "azure":      "redis-cli LLEN {queue_name}",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "queue_type", "type": "string", "required": True,
             "description": "redis | rabbitmq | kafka"},
            {"name": "queue_name", "type": "string", "required": True},
            {"name": "host",       "type": "string", "required": False, "default": "localhost"},
        ],
    },
    {
        "tool_name": "list_containers",
        "name": "Container Status Overview",
        "description": "List all Docker containers with status, uptime, restart count, and health state.",
        "command": "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.RunningFor}}\\t{{.Image}}'",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "filter_status", "type": "string", "required": False,
             "description": "all | running | exited | unhealthy"},
        ],
    },
    {
        "tool_name": "check_container_status",
        "name": "Container Status Detail",
        "description": "Inspect the affected container's runtime state, health, and restart count — scoped to the resource that triggered the incident.",
        "command": "docker inspect {target}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [],
    },
    {
        "tool_name": "trace_syscalls",
        "name": "Trace Syscalls (eBPF)",
        "description": "Run a 5-second strace window and return the top syscall-emitting processes.",
        # strace is not in Alpine/slim images; fall back to /proc/{pid}/syscall and
        # /proc/{pid}/status for basic tracing info without installing extra packages.
        #
        # process_name is REQUIRED — pgrep needs a pattern to resolve a PID; this tool
        # traces ONE already-identified process, it does not itself discover "the top
        # syscall-emitting process" system-wide. A runbook wanting that needs a separate
        # discovery step (e.g. top_processes) before calling this. Also: every variant
        # now echoes "PID=$PID" up front so the traced process's PID is actually
        # capturable downstream (e.g. for a subsequent process_kill) — previously the
        # PID was resolved internally via pgrep but never appeared anywhere in the
        # output, so no output_capture could ever retrieve it.
        "command": "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -z \"$PID\" ]; then echo \"[INFO] Process {process_name} not found — cannot trace (may have already exited)\"; else echo \"PID=$PID\"; if command -v strace >/dev/null 2>&1; then strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] strace not installed — reading /proc fallback\"; echo \"=== /proc/$PID/io ===\"; cat /proc/$PID/io 2>/dev/null || echo \"(not available)\"; echo \"=== /proc/$PID/status ===\"; cat /proc/$PID/status 2>/dev/null | head -20; fi; fi'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -z \"$PID\" ]; then echo \"[INFO] Process {process_name} not found — cannot trace (may have already exited)\"; else echo \"PID=$PID\"; if command -v strace >/dev/null 2>&1; then strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] strace not installed — reading /proc fallback\"; echo \"=== /proc/$PID/io ===\"; cat /proc/$PID/io 2>/dev/null || echo \"(not available)\"; echo \"=== /proc/$PID/status ===\"; cat /proc/$PID/status 2>/dev/null | head -20; fi; fi'",
            "ssh":        "ssh {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then echo \"PID=$PID\"; strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] Process {process_name} not found — cannot trace (may have already exited)\"; fi'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -z \"$PID\" ]; then echo \"[INFO] Process {process_name} not found\"; else echo \"PID=$PID\"; if command -v strace >/dev/null 2>&1; then strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"=== /proc/$PID/io ===\"; cat /proc/$PID/io 2>/dev/null; cat /proc/$PID/status 2>/dev/null | head -20; fi; fi'",
            "vcenter":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then echo \"PID=$PID\"; strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] Process {process_name} not found — cannot trace\"; fi'",
            "aws_ssm":    "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then echo \"PID=$PID\"; strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] Process {process_name} not found — cannot trace\"; fi'",
            "azure":      "sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then echo \"PID=$PID\"; strace -p \"$PID\" -c -e trace=all -T -f 2>&1 | head -50; else echo \"[INFO] Process {process_name} not found — cannot trace\"; fi'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string",  "required": True},
            {"name": "duration_sec", "type": "integer", "required": False, "default": 5},
            {"name": "top_n",        "type": "integer", "required": False, "default": 10},
        ],
    },
    {
        "tool_name": "check_env_vars",
        "name": "Inspect Environment Variables",
        "description": "List environment variables for a container, redacting values matching secret patterns.",
        # | sort must be inside sh -c so it runs in the container, not on the host.
        "command": "docker exec {target} sh -c 'env | sort'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'env | sort'",
            "ssh":        "ssh {target} sh -c 'env | sort'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'env | sort'",
            "vcenter":    "sh -c 'env | sort'",
            "aws_ssm":    "sh -c 'env | sort'",
            "azure":      "sh -c 'env | sort'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",       "type": "string", "required": True},
            {"name": "redact_secrets",  "type": "boolean","required": False, "default": True},
        ],
    },
    {
        "tool_name": "get_thread_dump",
        "name": "Thread / Goroutine Dump",
        "description": "Capture a thread dump from a JVM process (SIGQUIT) or Go runtime (SIGABRT).",
        # $() subshell MUST be inside sh -c — without it the subshell runs on the host
        # machine (pgrep not found there) and kill receives no PID.
        #
        # kill -3 only SIGNALS the process — the actual dump text is written to the
        # process's own stdout/log, not returned by this command at all. Without
        # tailing the container's logs afterward, this tool's raw_output was just a
        # confirmation message ("SIGQUIT sent...") with zero dump content, so no
        # deadlock signal could ever be parsed from it — a decision step checking
        # thread_deadlocks would always see a missing field, not a real answer.
        # docker/kubernetes variants now tail recent logs after a short settle delay
        # so _parse_tool_output has real dump text to search. ssh/vcenter/aws_ssm/azure
        # are left signal-only — there's no generic, safe way to guess where a given
        # SSH host or VM writes JVM/Go stdout (file path, journald, etc.) without
        # knowing the target's logging setup, so capture wasn't added in those.
        "command": "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then kill -3 \"$PID\" && sleep 1 && echo \"SIGQUIT sent to {process_name} (PID $PID)\"; else echo \"[WARN] No process matching {process_name} found\"; fi'; docker logs --tail 200 {target} 2>&1 | tail -200",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then kill -3 \"$PID\" && sleep 1 && echo \"SIGQUIT sent to {process_name} (PID $PID)\"; else echo \"[WARN] No process matching {process_name} found\"; fi'; docker logs --tail 200 {target} 2>&1 | tail -200",
            "ssh":        "ssh {target} sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1) && kill -3 \"$PID\"'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'PID=$(pgrep -f {process_name} 2>/dev/null | head -1); if [ -n \"$PID\" ]; then kill -3 \"$PID\" && sleep 1; else echo \"[WARN] No process matching {process_name} found\"; fi'; kubectl logs {target} -n {namespace} --tail=200 2>&1 | tail -200",
            "vcenter":    "sh -c 'kill -3 $(pgrep -f {process_name})'",
            "aws_ssm":    "sh -c 'kill -3 $(pgrep -f {process_name})'",
            "azure":      "sh -c 'kill -3 $(pgrep -f {process_name})'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
            {"name": "runtime",      "type": "string", "required": False, "default": "jvm",
             "description": "jvm | go | python"},
        ],
    },
    {
        "tool_name": "query_metrics",
        "name": "Query Metrics Endpoint",
        "description": "Fetch a named metric from a Prometheus-compatible /metrics endpoint.",
        "command": "curl -s {url}/metrics | grep '^{metric_name}'",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "url",          "type": "string", "required": True},
            {"name": "metric_name",  "type": "string", "required": True},
            {"name": "window",       "type": "string", "required": False, "default": "5m"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # REMEDIATION — SAFE  (blast_radius 1-2)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "cleanup_logs",
        "name": "Clean Up Old Log Files",
        "description": "Delete log files older than N days under a specified path inside the container.",
        # Wrapped in sh -c to prevent Windows/MINGW path conversion when running via Docker Desktop.
        "command": "docker exec {target} sh -c 'find {path} -type f -name \"*.log\" -mtime +{days_to_retain} -delete && echo \"Cleaned logs older than {days_to_retain} days from {path}\"'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'find {path} -type f -name \"*.log\" -mtime +{days_to_retain} -delete && echo \"Cleaned logs older than {days_to_retain} days from {path}\"'",
            "ssh":        "ssh {target} find {path} -type f -name '*.log' -mtime +{days_to_retain} -delete",
            "kubernetes": "kubectl exec {target} -n {namespace} -- find {path} -type f -name '*.log' -mtime +{days_to_retain} -delete",
            "vcenter":    "find {path} -type f -name '*.log' -mtime +{days_to_retain} -delete",
            "aws_ssm":    "find {path} -type f -name '*.log' -mtime +{days_to_retain} -delete",
            "azure":      "find {path} -type f -name '*.log' -mtime +{days_to_retain} -delete",
        },
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",      "type": "string",  "required": True},
            {"name": "path",           "type": "string",  "required": False, "default": "/var/log"},
            {"name": "days_to_retain", "type": "integer", "required": False, "default": 7},
        ],
    },
    {
        "tool_name": "clear_cache",
        "name": "Clear Application Cache",
        "description": "Flush an application's in-memory or filesystem cache via API or cache-clear command.",
        "command": "docker exec {target} redis-cli FLUSHDB",
        "command_variants": {
            "docker":     "docker exec {target} redis-cli FLUSHDB",
            "ssh":        "ssh {target} redis-cli FLUSHDB",
            "kubernetes": "kubectl exec {target} -n {namespace} -- redis-cli FLUSHDB",
            "vcenter":    "redis-cli FLUSHDB",
            "aws_ssm":    "redis-cli FLUSHDB",
            "azure":      "redis-cli FLUSHDB",
        },
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",   "type": "string", "required": True},
            {"name": "cache_type",  "type": "string", "required": False, "default": "all",
             "description": "all | redis | memcached | filesystem"},
        ],
    },
    {
        "tool_name": "rotate_logs",
        "name": "Rotate Log Files",
        "description": "Trigger logrotate inside a container to compress and cycle the current log files.",
        # logrotate is not in Alpine/slim images; fall back to manual rotation using find+mv.
        "command": "docker exec {target} sh -c 'if command -v logrotate >/dev/null 2>&1; then logrotate -f {config} && echo \"logrotate completed\"; else find /var/log -name \"*.log\" -size +1k -exec sh -c \\'mv \"$1\" \"$1.$(date +%Y%m%d%H%M%S)\" && touch \"$1\"\\' _ {} \\; && echo \"[FALLBACK] Manual rotation applied to /var/log (logrotate not installed)\"; fi'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'if command -v logrotate >/dev/null 2>&1; then logrotate -f {config} && echo \"logrotate completed\"; else find /var/log -name \"*.log\" -size +1k -exec sh -c \\'mv \"$1\" \"$1.$(date +%Y%m%d%H%M%S)\" && touch \"$1\"\\' _ {} \\; && echo \"[FALLBACK] Manual rotation applied to /var/log\"; fi'",
            "ssh":        "ssh {target} logrotate -f {config}",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'command -v logrotate >/dev/null 2>&1 && logrotate -f {config} || echo \"[WARN] logrotate not installed\"'",
            "vcenter":    "logrotate -f {config}",
            "aws_ssm":    "logrotate -f {config}",
            "azure":      "logrotate -f {config}",
        },
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "config",    "type": "string", "required": False, "default": "/etc/logrotate.conf",
             "description": "Path to logrotate config"},
        ],
    },
    {
        "tool_name": "free_temp_files",
        "name": "Clean Temporary Files",
        "description": "Remove stale files from /tmp and application temp directories inside a container.",
        # Wrapped in sh -c to prevent Windows/MINGW from converting /tmp to a host path.
        # Note: -mtime counts 24-hour periods; -mmin would be used for sub-day windows.
        "command": "docker exec {target} sh -c 'find /tmp -type f -mtime +{older_than_hours} -delete && echo \"Cleaned /tmp files older than {older_than_hours} days\"'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'find /tmp -type f -mtime +{older_than_hours} -delete && echo \"Cleaned /tmp files older than {older_than_hours} days\"'",
            "ssh":        "ssh {target} find /tmp -type f -mtime +{older_than_hours} -delete",
            "kubernetes": "kubectl exec {target} -n {namespace} -- find /tmp -type f -mtime +{older_than_hours} -delete",
            "vcenter":    "find /tmp -type f -mtime +{older_than_hours} -delete",
            "aws_ssm":    "find /tmp -type f -mtime +{older_than_hours} -delete",
            "azure":      "find /tmp -type f -mtime +{older_than_hours} -delete",
        },
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",         "type": "string",  "required": True},
            {"name": "older_than_hours",  "type": "integer", "required": False, "default": 24},
        ],
    },
    {
        "tool_name": "scale_up",
        "name": "Scale Up Replicas",
        "description": "Increase the replica count for a service or deployment.",
        "command": "docker compose up -d --scale {target}={replicas}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",   "type": "string",  "required": True,
             "description": "Service or deployment name"},
            {"name": "replicas", "type": "integer", "required": True},
            {"name": "max_cap",  "type": "integer", "required": False, "default": 20,
             "description": "Hard cap on replicas to prevent runaway scaling"},
        ],
    },
    {
        "tool_name": "scale_down",
        "name": "Scale Down Replicas",
        "description": "Decrease the replica count for a service or deployment.",
        "command": "docker compose up -d --scale {target}={replicas}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",   "type": "string",  "required": True},
            {"name": "replicas", "type": "integer", "required": True},
            {"name": "min_cap",  "type": "integer", "required": False, "default": 1},
        ],
    },
    {
        "tool_name": "update_config",
        "name": "Update Runtime Config",
        "description": "Set a runtime configuration key/value via environment variable override or config API.",
        "command": "docker exec {target} sh -c \"echo {key}={value} >> /etc/environment\"",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'echo {key}={value} >> /etc/environment'",
            "ssh":        "ssh {target} sh -c 'echo {key}={value} >> /etc/environment'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'echo {key}={value} >> /etc/environment'",
            "vcenter":    "sh -c 'echo {key}={value} >> /etc/environment'",
            "aws_ssm":    "sh -c 'echo {key}={value} >> /etc/environment'",
            "azure":      "sh -c 'echo {key}={value} >> /etc/environment'",
        },
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
            {"name": "key",       "type": "string", "required": True},
            {"name": "value",     "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "pause_cron",
        "name": "Pause Scheduled Jobs",
        "description": "Temporarily suspend cron / scheduled-task execution inside a container.",
        "command": "docker exec {target} sh -c \"crontab -l > /tmp/cron.bak && crontab -r\"",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
            "ssh":        "ssh {target} sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
            "vcenter":    "sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
            "aws_ssm":    "sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
            "azure":      "sh -c 'crontab -l > /tmp/cron.bak && crontab -r'",
        },
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string",  "required": True},
            {"name": "duration_min", "type": "integer", "required": False, "default": 30,
             "description": "How long to pause; 0 = indefinitely until manual resume"},
        ],
    },
    {
        "tool_name": "throttle_traffic",
        "name": "Apply Rate Limiting",
        "description": "Inject a tc (traffic control) rate limit on a container's network interface.",
        # tc (iproute2) is not in Alpine/slim images.  The command is attempted and a
        # clear error is returned so the operator knows to use an image with iproute2.
        "command": "docker exec {target} sh -c 'command -v tc >/dev/null 2>&1 && tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms && echo \"Rate limit applied: {rate_mbps}Mbit/s\" || echo \"[ERROR] tc not available in this container — install iproute2 (apk add iproute2) or apply rate limiting at the Docker network level\"'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'command -v tc >/dev/null 2>&1 && tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms && echo \"Rate limit applied: {rate_mbps}Mbit/s\" || echo \"[ERROR] tc not available — install iproute2\"'",
            "ssh":        "ssh {target} tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'command -v tc >/dev/null 2>&1 && tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms || echo \"[ERROR] tc not available\"'",
            "vcenter":    "tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms",
            "aws_ssm":    "tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms",
            "azure":      "tc qdisc add dev eth0 root tbf rate {rate_mbps}mbit burst 32kbit latency 400ms",
        },
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",  "type": "string", "required": True},
            {"name": "rate_mbps",  "type": "integer","required": True,
             "description": "Max outbound bandwidth in Mbit/s"},
        ],
    },
    {
        "tool_name": "flush_dns_cache",
        "name": "Flush DNS Cache",
        "description": "Clear the DNS resolver cache inside a container or on the host.",
        # || must be inside sh -c — without it the fallback runs on the Docker host.
        # nscd and systemd-resolved are not in Alpine/slim containers; the command
        # tries nscd first, then sends SIGHUP to PID 1 (triggers config reload in
        # some images), then reports honestly if no cache daemon is present.
        "command": "docker exec {target} sh -c 'nscd -i hosts 2>/dev/null && echo \"nscd host cache flushed\" || (kill -HUP 1 2>/dev/null && echo \"[INFO] SIGHUP sent to PID 1 — resolver may reload\") || echo \"[INFO] No DNS cache daemon found (nscd/dnsmasq not installed). musl/glibc resolvers have no persistent cache — restart the container to clear.\"'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'nscd -i hosts 2>/dev/null && echo \"nscd host cache flushed\" || (kill -HUP 1 2>/dev/null && echo \"SIGHUP sent\") || echo \"[INFO] No DNS cache daemon — restart container to flush resolver\"'",
            "ssh":        "ssh {target} sh -c 'nscd -i hosts 2>/dev/null || systemctl restart systemd-resolved 2>/dev/null || echo \"[INFO] No DNS cache daemon found\"'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'nscd -i hosts 2>/dev/null || echo \"[INFO] No DNS cache daemon — restart pod to flush resolver\"'",
            "vcenter":    "sh -c 'nscd -i hosts 2>/dev/null || systemctl restart systemd-resolved'",
            "aws_ssm":    "sh -c 'nscd -i hosts 2>/dev/null || systemctl restart systemd-resolved'",
            "azure":      "sh -c 'nscd -i hosts 2>/dev/null || systemctl restart systemd-resolved'",
        },
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target", "type": "string", "required": True},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # REMEDIATION — INTRUSIVE  (blast_radius 2-3)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "process_kill",
        "name": "Kill Process",
        "description": (
            "Send a POSIX signal (default SIGKILL) to a named process inside a container. "
            "Target process must match at least one allow rule — all critical system processes "
            "are denied by default."
        ),
        # CG-3 fix: use pkill (by name) not kill (requires PID). Works on all platforms.
        "command": "docker exec {target} pkill -{signal} {process_name}",
        "command_variants": {
            "docker":     "docker exec {target} pkill -{signal} {process_name}",
            "ssh":        "ssh {target} pkill -{signal} {process_name}",
            "kubernetes": "kubectl exec {target} -n {namespace} -- pkill -{signal} {process_name}",
            "vcenter":    "pkill -{signal} {process_name}",
            "aws_ssm":    "pkill -{signal} {process_name}",
            "azure":      "pkill -{signal} {process_name}",
        },
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": False,
        "parameters": [
            {"name": "target",    "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
            {"name": "signal",       "type": "string", "required": False, "default": "SIGKILL",
             "description": "POSIX signal: SIGKILL | SIGTERM | SIGINT | SIGQUIT | SIGHUP"},
        ],
        "process_rules": DEFAULT_PROCESS_RULES,
    },
    {
        "tool_name": "restart_service",
        "name": "Graceful Service Restart",
        "description": "Send SIGTERM to the main process and wait for it to exit cleanly before restarting.",
        "command": "docker restart --time {timeout_sec} {target}",
        "command_variants": {
            "docker":     "docker restart --time {timeout_sec} {target}",
            "ssh":        "ssh {target} systemctl restart {target}",
            "kubernetes": "kubectl rollout restart deployment/{target} -n {namespace}",
            "vcenter":    "systemctl restart {target}",
            "aws_ssm":    "systemctl restart {target}",
            "azure":      "systemctl restart {target}",
        },
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",       "type": "string",  "required": True},
            {"name": "timeout_sec",  "type": "integer", "required": False, "default": 30,
             "description": "Seconds to wait for clean exit before forcing SIGKILL"},
        ],
    },
    {
        "tool_name": "force_restart",
        "name": "Force Kill and Restart",
        "description": (
            "Immediately SIGKILL the main process then restart the container. "
            "Subject to the same process allow-list as process_kill."
        ),
        # Use ; not && — docker restart must run whether or not pkill found the process.
        # pkill -9 runs inside the container; docker restart runs on the host (intentional).
        "command": "docker exec {target} pkill -9 {process_name} 2>/dev/null; docker restart {target}",
        "command_variants": {
            "docker":     "docker exec {target} pkill -9 {process_name} 2>/dev/null; docker restart {target}",
            "ssh":        "ssh {target} pkill -9 {process_name} 2>/dev/null; ssh {target} systemctl restart {process_name}",
            "kubernetes": "kubectl exec {target} -n {namespace} -- pkill -9 {process_name}",
            "vcenter":    "pkill -9 {process_name}",
            "aws_ssm":    "pkill -9 {process_name}",
            "azure":      "pkill -9 {process_name}",
        },
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "target",    "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
        ],
        "process_rules": DEFAULT_PROCESS_RULES,
    },
    {
        "tool_name": "kill_connections",
        "name": "Terminate DB Connections",
        "description": "Run pg_terminate_backend() or equivalent to kill idle/stuck database connections.",
        "command": "docker exec {target} psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
        "command_variants": {
            "docker":     "docker exec {target} psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
            "ssh":        "ssh {target} psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
            "kubernetes": "kubectl exec {target} -n {namespace} -- psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
            "vcenter":    "psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
            "aws_ssm":    "psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
            "azure":      "psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < NOW() - INTERVAL '{max_idle_sec} seconds'\"",
        },
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "target",    "type": "string",  "required": True},
            {"name": "db_type",      "type": "string",  "required": True,
             "description": "postgres | mysql | mariadb"},
            {"name": "max_idle_sec", "type": "integer", "required": False, "default": 300,
             "description": "Only terminate connections idle longer than this"},
        ],
    },
    {
        "tool_name": "block_ip",
        "name": "Block IP via Firewall",
        "description": "Add an iptables DROP rule for a source IP to block inbound traffic.",
        "command": "iptables -I INPUT -s {ip} -j DROP",
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": True,
        "parameters": [
            {"name": "ip",          "type": "string",  "required": True,
             "description": "IPv4 or IPv6 address / CIDR block"},
            {"name": "duration_min","type": "integer", "required": False, "default": 60,
             "description": "Auto-expire rule after N minutes; 0 = permanent"},
        ],
    },
    {
        "tool_name": "rollback_deployment",
        "name": "Rollback Deployment",
        "description": "Revert a service to its previous image/version using docker compose or kubectl.",
        "command": "docker compose pull {service} && docker compose up -d {service}",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "service",        "type": "string", "required": True},
            {"name": "target_version", "type": "string", "required": False,
             "description": "Specific tag/SHA; omit to roll back one step"},
        ],
    },
    {
        "tool_name": "isolate_container",
        "name": "Network-Isolate Container",
        "description": "Disconnect a container from all Docker networks to quarantine a compromised workload.",
        "command": "for net in $(docker inspect {target} --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'); do docker network disconnect -f $net {target}; done",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "target",     "type": "string",  "required": True},
            {"name": "preserve_mgmt","type": "boolean", "required": False, "default": True,
             "description": "Keep management/loopback network attached for diagnostics"},
        ],
    },
    {
        "tool_name": "revoke_token",
        "name": "Revoke Auth Tokens",
        "description": "Invalidate all active JWT / session tokens for a user or service account.",
        "command": "docker exec {target} redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
        "command_variants": {
            "docker":     "docker exec {target} redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
            "ssh":        "ssh {target} redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
            "kubernetes": "kubectl exec {target} -n {namespace} -- redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
            "vcenter":    "redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
            "aws_ssm":    "redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
            "azure":      "redis-cli DEL \"session:{subject}\" \"token:{subject}\"",
        },
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": True,
        "parameters": [
            {"name": "target",   "type": "string", "required": True},
            {"name": "subject",     "type": "string", "required": True,
             "description": "User ID, service account name, or '*' for all"},
            {"name": "token_type",  "type": "string", "required": False, "default": "all",
             "description": "all | jwt | session | api_key"},
        ],
    },
    {
        "tool_name": "drain_node",
        "name": "Drain Node / Host",
        "description": "Cordon a node and gracefully evict all workloads to other nodes.",
        "command": "kubectl drain {node} --ignore-daemonsets={ignore_ds} --grace-period={grace_sec} --delete-emptydir-data",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "node",      "type": "string",  "required": True},
            {"name": "grace_sec", "type": "integer", "required": False, "default": 60},
            {"name": "ignore_ds", "type": "boolean", "required": False, "default": True,
             "description": "Ignore DaemonSet pods"},
        ],
    },
    {
        "tool_name": "evacuate_node",
        "name": "Emergency Evacuate Node",
        "description": "Immediately force-delete all pods on a node and mark it unschedulable.",
        "command": "kubectl drain {node} --force --ignore-daemonsets --delete-emptydir-data --grace-period=0",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "node",   "type": "string", "required": True},
            {"name": "reason", "type": "string", "required": True,
             "description": "Mandatory justification recorded in the audit log"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # HOST / SSH — DIAGNOSTICS  (blast_radius=1, read-only)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "host_service_status",
        "name": "Host Service Status",
        "description": "Check systemd service state on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl status {service}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True,  "description": "IP or hostname"},
            {"name": "service", "type": "string", "required": True,  "description": "systemd service name, e.g. nginx"},
        ],
    },
    {
        "tool_name": "host_logs",
        "name": "Host Journal Logs",
        "description": "Fetch systemd journal entries for a service via SSH.",
        "command": "ssh {host} journalctl -u {service} -n {lines} --no-pager",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string",  "required": True},
            {"name": "service", "type": "string",  "required": True,  "description": "systemd service name"},
            {"name": "lines",   "type": "integer", "required": False, "default": 100},
            {"name": "since",   "type": "string",  "required": False, "description": "e.g. '10m ago', '1h ago'"},
        ],
    },
    {
        "tool_name": "host_top_processes",
        "name": "Host Top Processes",
        "description": "List highest CPU/memory processes on a remote host via SSH.",
        # | head must be in the remote shell so it runs on the host, not locally.
        "command": "ssh {host} sh -c 'ps aux --sort=-{sort_by} 2>/dev/null | head -{limit} || top -bn1 | tail -n +5 | head -{limit}'",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string",  "required": True},
            {"name": "limit",   "type": "integer", "required": False, "default": 10},
            {"name": "sort_by", "type": "string",  "required": False, "default": "cpu",
             "description": "Sort field: cpu | rss"},
        ],
    },
    {
        "tool_name": "host_disk_usage",
        "name": "Host Disk Usage",
        "description": "Check disk space and largest directories on a remote host via SSH.",
        # && and | must be inside the remote sh -c, otherwise they run locally.
        "command": "ssh {host} sh -c 'df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20'",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
            {"name": "path", "type": "string", "required": False, "default": "/var",
             "description": "Directory to analyse"},
        ],
    },
    {
        "tool_name": "host_process_info",
        "name": "Host Process Info",
        "description": "Get PID, status, and resource usage for a named process on a remote host via SSH.",
        # $() and && must be in the remote shell — without sh -c they execute locally.
        "command": "ssh {host} sh -c 'PID=$(pgrep -f {process_name} | head -1) && ps -fp \"$PID\" && cat /proc/\"$PID\"/status 2>/dev/null'",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
        ],
        "output_fields": [
            {"field": "process_found",  "kind": "regex", "pattern": r"State:\s*\S\s*\(",        "type": "boolean"},
            {"field": "pid",            "kind": "regex", "pattern": r"(?:^|\n)Pid:\s*(\d+)",     "type": "integer"},
            {"field": "process_state",  "kind": "regex", "pattern": r"State:\s*\S\s*\(([^)]+)\)","type": "string"},
            {"field": "mem_rss_kb",     "kind": "regex", "pattern": r"VmRSS:\s*(\d+)\s*kB",      "type": "integer"},
        ],
    },
    {
        "tool_name": "host_netstat",
        "name": "Host Network Connections",
        "description": "List active TCP/UDP connections and listening ports on a remote host via SSH.",
        "command": "ssh {host} ss -tunaop",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",  "type": "string", "required": True},
            {"name": "state", "type": "string", "required": False, "default": "all",
             "description": "all | established | listening | time-wait"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # HOST / SSH — REMEDIATION SAFE  (blast_radius 1-2)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "host_service_restart",
        "name": "Host Service Restart",
        "description": "Restart a systemd service on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl restart {service}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True, "description": "systemd service name"},
        ],
    },
    {
        "tool_name": "host_service_stop",
        "name": "Host Service Stop",
        "description": "Stop a systemd service on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl stop {service}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "host_log_cleanup",
        "name": "Host Log Cleanup",
        "description": "Delete log files older than N days on a remote host via SSH.",
        "command": "ssh {host} find {path} -name '*.log' -mtime +{days_to_retain} -delete",
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",           "type": "string",  "required": True},
            {"name": "path",           "type": "string",  "required": False, "default": "/var/log"},
            {"name": "days_to_retain", "type": "integer", "required": False, "default": 7},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # HOST / SSH — REMEDIATION INTRUSIVE  (blast_radius 2-3)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "host_process_kill",
        "name": "Host Process Kill",
        "description": "Send a POSIX signal to a named process on a remote host via SSH.",
        # CG-3 alignment: also use pkill here for consistency with process_kill
        "command": "ssh {host} pkill -{signal} {process_name}",
        "command_variants": {
            "ssh":     "ssh {host} pkill -{signal} {process_name}",
            "vcenter": "pkill -{signal} {process_name}",
            "aws_ssm": "pkill -{signal} {process_name}",
            "azure":   "pkill -{signal} {process_name}",
        },
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
            {"name": "signal",       "type": "string", "required": False, "default": "SIGTERM",
             "description": "SIGTERM | SIGKILL | SIGHUP | SIGINT"},
        ],
        "process_rules": DEFAULT_PROCESS_RULES,
    },
    {
        "tool_name": "host_reboot",
        "name": "Host Reboot",
        "description": "Gracefully reboot a bare-metal or VM host via SSH. Requires manual approval.",
        "command": "ssh {host} systemctl reboot",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "host",          "type": "string",  "required": True},
            {"name": "delay_seconds", "type": "integer", "required": False, "default": 0,
             "description": "0 = immediate reboot"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # KUBERNETES — DIAGNOSTICS  (blast_radius=1, read-only)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "k8s_pod_logs",
        "name": "K8s Pod Logs",
        "description": "Fetch recent log lines from a Kubernetes pod.",
        "command": "kubectl logs {target} -n {namespace} --tail={lines} --timestamps",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",       "type": "string",  "required": True},
            {"name": "namespace", "type": "string",  "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "lines",     "type": "integer", "required": False, "default": 100},
            {"name": "container", "type": "string",  "required": False,
             "description": "Specific container in pod (multi-container pods only)"},
        ],
    },
    {
        "tool_name": "k8s_pod_describe",
        "name": "K8s Describe Pod",
        "description": "Full kubectl describe output: events, conditions, resource limits, and restart counts.",
        "command": "kubectl describe pod {target} -n {namespace}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",       "type": "string", "required": True},
            {"name": "namespace", "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
        ],
    },
    {
        "tool_name": "k8s_events",
        "name": "K8s Events",
        "description": "Get recent Kubernetes events sorted by timestamp for a namespace or label selector.",
        "command": "kubectl get events -n {namespace} --sort-by=.lastTimestamp",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "namespace",      "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "label_selector", "type": "string", "required": False,
             "description": "Optional label filter, e.g. app=myapp"},
        ],
    },
    {
        "tool_name": "k8s_top_pods",
        "name": "K8s Top Pods",
        "description": "CPU and memory resource usage for all pods in a namespace.",
        "command": "kubectl top pods -n {namespace} --sort-by={sort_by}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "namespace", "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "sort_by",   "type": "string", "required": False, "default": "cpu",
             "description": "cpu | memory"},
        ],
    },
    {
        "tool_name": "k8s_rollout_status",
        "name": "K8s Rollout Status",
        "description": "Check the progress and health of a Kubernetes deployment rollout.",
        "command": "kubectl rollout status deployment/{deployment} -n {namespace}",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string", "required": True},
            {"name": "namespace",  "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
        ],
    },
    {
        "tool_name": "k8s_pod_status",
        "name": "K8s Pod Status",
        "description": "List pods and their phase/status using a label selector.",
        "command": "kubectl get pods -n {namespace} -l {label_selector} -o wide",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "namespace",      "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "label_selector", "type": "string", "required": True,
             "description": "e.g. app=myapp"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # KUBERNETES — REMEDIATION SAFE  (blast_radius 1-2)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "k8s_rollout_restart",
        "name": "K8s Rollout Restart",
        "description": "Trigger a rolling restart of a Kubernetes deployment (zero-downtime).",
        "command": "kubectl rollout restart deployment/{deployment} -n {namespace}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string", "required": True},
            {"name": "namespace",  "type": "string", "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
        ],
    },
    {
        "tool_name": "k8s_scale",
        "name": "K8s Scale Deployment",
        "description": "Set replica count for a Kubernetes deployment.",
        "command": "kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string",  "required": True},
            {"name": "namespace",  "type": "string",  "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "replicas",   "type": "integer", "required": True},
            {"name": "min_cap",    "type": "integer", "required": False, "default": 1,
             "description": "Safety floor — refuses scale below this value"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # KUBERNETES — REMEDIATION INTRUSIVE  (blast_radius 2-3)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "k8s_delete_pod",
        "name": "K8s Delete Pod",
        "description": "Delete a pod so the ReplicaSet immediately recreates it. Use --force for stuck Terminating pods.",
        "command": "kubectl delete pod {target} -n {namespace} --grace-period={grace_seconds}",
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "target",           "type": "string",  "required": True},
            {"name": "namespace",     "type": "string",  "required": False, "default": "default",
             "description": "Watcher-injected from WATCHER_K8S_NAMESPACE — override only if targeting a non-default namespace"},
            {"name": "grace_seconds", "type": "integer", "required": False, "default": 30,
             "description": "0 = force delete immediately"},
        ],
    },
    {
        "tool_name": "k8s_cordon_node",
        "name": "K8s Cordon Node",
        "description": "Mark a Kubernetes node as unschedulable — no new pods will be placed on it.",
        "command": "kubectl cordon {node}",
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": True,
        "parameters": [
            {"name": "node", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "k8s_drain_node",
        "name": "K8s Drain Node",
        "description": "Cordon node and gracefully evict all pods. Requires manual approval.",
        "command": "kubectl drain {node} --ignore-daemonsets --delete-emptydir-data --grace-period={grace_sec}",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "node",      "type": "string",  "required": True},
            {"name": "grace_sec", "type": "integer", "required": False, "default": 60},
            {"name": "ignore_ds", "type": "boolean", "required": False, "default": True,
             "description": "Ignore DaemonSet pods"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # NETWORK — HTTP / TLS / FILE diagnostics (blast_radius=1)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "http_request",
        "name": "HTTP Request",
        "description": "Send an HTTP request to a URL and return the status code and response body. Use for health checks, API probes, or webhook calls.",
        # Always exits 0 so the step is never marked failed just because the service is unreachable.
        # http_code=000 means curl couldn't connect; anything 2xx means healthy.
        "command": "rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null || echo 000); echo \"http_code=$code\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null); echo \"http_code=${code:-000}\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true'",
            "ssh":        "ssh {target} sh -c 'rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null); echo \"http_code=${code:-000}\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null); echo \"http_code=${code:-000}\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true'",
            "vcenter":    "rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null || echo 000); echo \"http_code=$code\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true",
            "aws_ssm":    "rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null || echo 000); echo \"http_code=$code\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true",
            "azure":      "rm -f /tmp/http_resp.txt; code=$(curl -s -o /tmp/http_resp.txt -w \"%{http_code}\" --max-time 15 -X {method} {url} 2>/dev/null || echo 000); echo \"http_code=$code\"; body=$(cat /tmp/http_resp.txt 2>/dev/null | head -c 500); [ -n \"$body\" ] && echo \"response_body=$body\"; true",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "url",             "type": "string",  "required": True,  "description": "Target URL"},
            {"name": "method",          "type": "string",  "required": False, "default": "GET",
             "description": "HTTP method: GET | POST | PUT | DELETE | HEAD"},
            {"name": "headers",         "type": "string",  "required": False, "default": "",
             "description": "Extra headers as space-separated -H key:value pairs"},
            {"name": "body",            "type": "string",  "required": False, "default": "",
             "description": "Request body (for POST/PUT)"},
            {"name": "expected_status", "type": "string",  "required": False, "default": "200",
             "description": "Expected HTTP status code — used for success determination"},
        ],
        "output_fields": [
            {"field": "http_code",      "kind": "regex", "pattern": r"http_code=(\d+)",    "type": "integer"},
            {"field": "response_body",  "kind": "regex", "pattern": r"response_body=(.+)", "type": "string"},
            {"field": "reachable",      "kind": "regex", "pattern": r"http_code=([1-4]\d\d)", "type": "boolean"},
        ],
    },
    {
        "tool_name": "check_ssl_cert",
        "name": "Check SSL Certificate",
        "description": "Check the SSL/TLS certificate expiry and validity for a hostname. Returns days remaining and whether the cert is expiring soon.",
        "command": "echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates -subject 2>/dev/null",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates -subject 2>/dev/null'",
            "ssh":        "ssh {target} sh -c 'echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates -subject 2>/dev/null'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null'",
            "vcenter":    "echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null",
            "aws_ssm":    "echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null",
            "azure":      "echo | openssl s_client -connect {host}:{port} -servername {host} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",      "type": "string",  "required": True,  "description": "Hostname to check (e.g. api.example.com)"},
            {"name": "port",      "type": "string",  "required": False, "default": "443",
             "description": "TLS port (default 443)"},
            {"name": "warn_days", "type": "integer", "required": False, "default": 30,
             "description": "Warn if certificate expires within this many days"},
        ],
        "output_fields": [
            {"field": "days_remaining",  "description": "Days until certificate expires"},
            {"field": "expires_on",      "description": "Certificate expiry date string"},
            {"field": "cert_valid",      "description": "true if cert is valid and not expired"},
            {"field": "cert_expiring",   "description": "true if expiring within warn_days"},
        ],
    },
    {
        "tool_name": "check_file",
        "name": "Check File",
        "description": "Check whether a file exists on a host or container, its size in bytes, and optionally whether its content contains a pattern.",
        "command": "docker exec {target} sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path} 2>/dev/null || stat -f%z {path} 2>/dev/null)\" || echo \"exists=false size=0\"'",
        "command_variants": {
            "docker":     "docker exec {target} sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path} 2>/dev/null || stat -f%z {path} 2>/dev/null)\" || echo \"exists=false size=0\"'",
            "ssh":        "ssh {target} sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path})\" || echo \"exists=false size=0\"'",
            "kubernetes": "kubectl exec {target} -n {namespace} -- sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path} 2>/dev/null || stat -f%z {path} 2>/dev/null)\" || echo \"exists=false size=0\"'",
            "vcenter":    "sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path})\" || echo \"exists=false size=0\"'",
            "aws_ssm":    "sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path})\" || echo \"exists=false size=0\"'",
            "azure":      "sh -c 'test -f {path} && echo \"exists=true size=$(stat -c%s {path})\" || echo \"exists=false size=0\"'",
        },
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "target",   "type": "string", "required": True,  "description": "Container name or host"},
            {"name": "path",     "type": "string", "required": True,  "description": "Absolute file path to check"},
            {"name": "pattern",  "type": "string", "required": False, "default": "",
             "description": "grep pattern — check if file content contains this string"},
            {"name": "min_size", "type": "integer","required": False, "default": 0,
             "description": "Minimum expected file size in bytes (0 = any)"},
        ],
        "output_fields": [
            {"field": "file_exists",     "description": "true if the file exists"},
            {"field": "file_size_bytes", "description": "File size in bytes"},
            {"field": "pattern_found",   "description": "true if pattern was found in file content"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # WINDOWS / WinRM — Default process rules
    # ══════════════════════════════════════════════════════════
]

DEFAULT_WINDOWS_PROCESS_RULES = [
    {"priority": 1,  "allow": False, "pattern": "^(System|smss|csrss|wininit|services|lsass|svchost)$",
     "description": "Core Windows OS processes — never kill"},
    {"priority": 2,  "allow": False, "pattern": "^(sqlservr|mysqld|postgres)$",
     "description": "Database engines — never kill"},
    {"priority": 3,  "allow": False, "pattern": "^(redis-server)$",
     "description": "Redis — never kill"},
    {"priority": 4,  "allow": False, "pattern": "^(python|uvicorn|celery|node|npm)$",
     "description": "Platform backend processes — never kill"},
    {"priority": 20, "allow": True,  "pattern": "^(notepad|calc|mspaint|wordpad)$",
     "description": "Safe GUI test apps"},
    {"priority": 21, "allow": True,  "pattern": "^stress.*$",
     "description": "Stress test tools"},
    {"priority": 30, "allow": True,  "pattern": "^w3wp$",
     "description": "IIS worker process"},
]

WINDOWS_ACTIONS = [
    # ══════════════════════════════════════════════════════════
    # WINDOWS / WinRM — DIAGNOSTICS  (blast_radius=1, read-only)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "win_service_status",
        "name": "Win Service Status",
        "description": "Get Windows service state (Running/Stopped/StartType) via WinRM Invoke-Command.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-Service -Name {service} | Select Name,Status,StartType }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True,  "description": "IP or hostname (WinRM must be enabled)"},
            {"name": "service", "type": "string", "required": True,  "description": "Windows service name e.g. W3SVC, wuauserv"},
        ],
    },
    {
        "tool_name": "win_event_log",
        "name": "Win Event Log",
        "description": "Fetch recent Windows Event Log entries filtered by log name and entry type.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-EventLog -LogName {log_name} -Newest {lines} -EntryType {entry_type} | Select TimeGenerated,Source,EventID,Message }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",       "type": "string",  "required": True},
            {"name": "log_name",   "type": "string",  "required": False, "default": "Application",
             "description": "Application | System | Security"},
            {"name": "lines",      "type": "integer", "required": False, "default": 50},
            {"name": "entry_type", "type": "string",  "required": False, "default": "Error",
             "description": "Error | Warning | Information"},
        ],
    },
    {
        "tool_name": "win_top_processes",
        "name": "Win Top Processes",
        "description": "List highest CPU or memory consuming processes on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-Process | Sort-Object {sort_by} -Descending | Select-Object -First {limit} Name,Id,CPU,WorkingSet,Handles }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string",  "required": True},
            {"name": "limit",   "type": "integer", "required": False, "default": 10},
            {"name": "sort_by", "type": "string",  "required": False, "default": "CPU",
             "description": "CPU | WorkingSet | Handles"},
        ],
    },
    {
        "tool_name": "win_disk_usage",
        "name": "Win Disk Usage",
        "description": "Check drive free/used space on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-PSDrive -PSProvider FileSystem | Select Name,@{n='Used(GB)';e={[math]::Round($_.Used/1GB,2)}},@{n='Free(GB)';e={[math]::Round($_.Free/1GB,2)}} }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",  "type": "string", "required": True},
            {"name": "drive", "type": "string", "required": False,
             "description": "Drive letter e.g. C — leave blank for all drives"},
        ],
    },
    {
        "tool_name": "win_process_info",
        "name": "Win Process Info",
        "description": "Get detailed info (PID, CPU, memory, handles, start time) for a named process on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-Process -Name {process_name} | Select Name,Id,CPU,WorkingSet,Handles,StartTime,Path }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True,
             "description": "Process name without .exe"},
        ],
    },
    {
        "tool_name": "win_netstat",
        "name": "Win Network Connections",
        "description": "List active TCP connections and listening ports on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { netstat -ano | Select-String {state} }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",  "type": "string", "required": True},
            {"name": "state", "type": "string", "required": False, "default": "ESTABLISHED",
             "description": "ESTABLISHED | LISTENING | TIME_WAIT | (blank = all)"},
        ],
    },
    {
        "tool_name": "win_memory",
        "name": "Win Memory Usage",
        "description": "Get physical and virtual memory stats from a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-CimInstance Win32_OperatingSystem | Select @{n='TotalRAM_GB';e={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}},@{n='FreeRAM_GB';e={[math]::Round($_.FreePhysicalMemory/1MB,2)}},@{n='FreeVirt_GB';e={[math]::Round($_.FreeVirtualMemory/1MB,2)}} }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_iis_status",
        "name": "Win IIS App Pool Status",
        "description": "List IIS Application Pool states on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Get-ChildItem IIS:\\AppPools | Select Name,State,@{n='PipelineMode';e={$_.managedPipelineMode}} }",
        "category": "diagnostic",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",     "type": "string", "required": True},
            {"name": "app_pool", "type": "string", "required": False,
             "description": "Filter by pool name — leave blank for all"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # WINDOWS / WinRM — REMEDIATION SAFE  (blast_radius 1-2)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "win_service_restart",
        "name": "Win Service Restart",
        "description": "Restart a Windows service via WinRM Invoke-Command.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Restart-Service -Name {service} -Force }",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True,
             "description": "Windows service name e.g. W3SVC"},
        ],
    },
    {
        "tool_name": "win_service_stop",
        "name": "Win Service Stop",
        "description": "Stop a Windows service via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Stop-Service -Name {service} -Force }",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_service_start",
        "name": "Win Service Start",
        "description": "Start a stopped Windows service via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Start-Service -Name {service} }",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_clear_temp",
        "name": "Win Clear Temp Files",
        "description": "Delete stale files from Windows temp directories via WinRM to free disk space.",
        "command": r'Invoke-Command -ComputerName {host} -ScriptBlock { Remove-Item "$env:TEMP\*" -Recurse -Force -EA 0; Remove-Item "C:\Windows\Temp\*" -Recurse -Force -EA 0 }',
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host",             "type": "string",  "required": True},
            {"name": "include_win_temp", "type": "boolean", "required": False, "default": True,
             "description": r"Also clear C:\Windows\Temp"},
        ],
    },
    {
        "tool_name": "win_flush_dns",
        "name": "Win Flush DNS",
        "description": "Flush the DNS resolver cache on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { ipconfig /flushdns }",
        "category": "remediation_safe",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_iis_recycle",
        "name": "Win IIS Recycle App Pool",
        "description": "Recycle an IIS Application Pool via WinRM — drains active connections and starts a fresh worker process.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Restart-WebAppPool -Name {app_pool} }",
        "category": "remediation_safe",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host",     "type": "string", "required": True},
            {"name": "app_pool", "type": "string", "required": True,
             "description": "IIS app pool name e.g. DefaultAppPool"},
        ],
    },

    # ══════════════════════════════════════════════════════════
    # WINDOWS / WinRM — REMEDIATION INTRUSIVE  (blast_radius 2-3)
    # ══════════════════════════════════════════════════════════
    {
        "tool_name": "win_iis_stop_start",
        "name": "Win IIS Stop/Start Website",
        "description": "Stop then start an IIS website via WinRM. Briefly interrupts traffic.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Stop-Website -Name {site}; Start-Sleep 2; Start-Website -Name {site} }",
        "category": "remediation_intrusive",
        "blast_radius": 2,
        "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
            {"name": "site", "type": "string", "required": True,
             "description": "IIS website name e.g. Default Web Site"},
        ],
    },
    {
        "tool_name": "win_process_kill",
        "name": "Win Process Kill",
        "description": "Forcibly terminate a named process on a Windows host via WinRM (Stop-Process -Force).",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Stop-Process -Name {process_name} -Force }",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True,
             "description": "Process name without .exe e.g. notepad, w3wp"},
        ],
        "process_rules": DEFAULT_WINDOWS_PROCESS_RULES,
    },
    {
        "tool_name": "win_reboot",
        "name": "Win Reboot",
        "description": "Reboot a Windows host via WinRM. Requires manual approval.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Restart-Computer -Force -Delay {delay_seconds} }",
        "category": "remediation_intrusive",
        "blast_radius": 3,
        "requires_approval": True,
        "parameters": [
            {"name": "host",          "type": "string",  "required": True},
            {"name": "delay_seconds", "type": "integer", "required": False, "default": 0,
             "description": "Seconds before reboot; 0 = immediate"},
        ],
    },
]

# ── Notification / alerting tools ─────────────────────────────────────────────
# These are handled natively by the runbook executor (incident_agents.py,
# ToolRegistryAgent._execute_notify_action) and never execute a shell command —
# command is intentionally None; _execute_tool_impl special-cases these tool
# names before the catalog command-lookup path is ever reached.
NOTIFICATION_ACTIONS = [
    {
        "tool_name": "notify",
        "name": "Notify",
        "description": (
            "Send a notification via whichever channel(s) are configured: a named "
            "notification team (Settings → Notification Teams) if `team` is given and "
            "found, otherwise the default PagerDuty/Slack/SMTP connectors. "
            "action=escalate opens/triggers an incident on every configured channel; "
            "acknowledge/resolve only affect PagerDuty; message posts to Slack/email/"
            "webhook (not PagerDuty)."
        ),
        "command": None,
        "command_variants": None,
        "category": "notify",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "action",    "type": "string", "required": True,
             "description": "escalate | acknowledge | resolve | message"},
            {"name": "message",   "type": "string", "required": True,
             "description": "Notification message body. Supports {{variable}} placeholders."},
            {"name": "severity",  "type": "string", "required": False, "default": "warning",
             "description": "Severity level: info | warning | high | critical"},
            {"name": "team",      "type": "string", "required": False,
             "description": "Notification team name to route to. Omit to use the default channels."},
            {"name": "dedup_key", "type": "string", "required": False,
             "description": "PagerDuty dedup_key — required for acknowledge/resolve; auto-generated for escalate if omitted."},
        ],
        "output_fields": [],
    },
    {
        "tool_name": "send_alert",
        "name": "Send Alert Notification",
        "description": (
            "Send a notification message when a runbook step completes. "
            "Delivers to a notification team's Slack/email/webhook (if `team` resolves "
            "to one) or the default Slack/SMTP connectors otherwise. "
            "Use as the final step of a runbook to confirm remediation outcome."
        ),
        "command": None,
        "command_variants": None,
        "category": "notify",
        "blast_radius": 1,
        "requires_approval": False,
        "parameters": [
            {"name": "message",  "type": "string", "required": True,
             "description": "Notification message body. Supports {{variable}} placeholders."},
            {"name": "severity", "type": "string", "required": False, "default": "info",
             "description": "Severity level: info | warning | critical"},
            {"name": "team",     "type": "string", "required": False,
             "description": "Notification team name to route to. Omit to use the default channels."},
        ],
        "output_fields": [],
    },
]

# Combined master list exported for use by seed_defaults()
APPROVED_ACTIONS = APPROVED_ACTIONS + WINDOWS_ACTIONS + NOTIFICATION_ACTIONS
