"""Seed Windows / WinRM approved actions into the live DB."""
import requests

DEFAULT_PROCESS_RULES = [
    {"priority": 1,  "allow": False, "pattern": "^(System|smss|csrss|wininit|services|lsass|svchost)$", "description": "Core Windows processes - never kill"},
    {"priority": 2,  "allow": False, "pattern": "^(sqlservr|mysqld|postgres)$",                          "description": "Database engines - never kill"},
    {"priority": 3,  "allow": False, "pattern": "^(redis-server)$",                                       "description": "Redis - never kill"},
    {"priority": 4,  "allow": False, "pattern": "^(python|uvicorn|celery|node|npm)$",                    "description": "Platform backend processes - never kill"},
    {"priority": 20, "allow": True,  "pattern": "^(notepad|calc|mspaint|wordpad)$",                      "description": "Safe test apps"},
    {"priority": 21, "allow": True,  "pattern": "^stress.*$",                                             "description": "Stress test tools"},
    {"priority": 30, "allow": True,  "pattern": "^w3wp$",                                                 "description": "IIS worker process (individual pool)"},
]

WINDOWS_ACTIONS = [
    # ══ WINDOWS — DIAGNOSTICS ═════════════════════════════════════════════════
    {
        "tool_name": "win_service_status",
        "name": "Win Service Status",
        "description": "Get Windows service state (Running/Stopped/StartType) via WinRM Invoke-Command.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-Service -Name {service} | Select Name,Status,StartType }",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
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
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
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
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
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
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",  "type": "string", "required": True},
            {"name": "drive", "type": "string", "required": False, "description": "Drive letter e.g. C — leave blank for all drives"},
        ],
    },
    {
        "tool_name": "win_process_info",
        "name": "Win Process Info",
        "description": "Get detailed info (PID, CPU, memory, handles, start time) for a named process on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Get-Process -Name {process_name} | Select Name,Id,CPU,WorkingSet,Handles,StartTime,Path }",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True, "description": "Process name without .exe"},
        ],
    },
    {
        "tool_name": "win_netstat",
        "name": "Win Network Connections",
        "description": "List active TCP connections and listening ports on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { netstat -ano | Select-String {state} }",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
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
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_iis_status",
        "name": "Win IIS App Pool Status",
        "description": "List IIS Application Pool states on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Get-ChildItem IIS:\\AppPools | Select Name,State,@{n='PipelineMode';e={$_.managedPipelineMode}} }",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",     "type": "string", "required": True},
            {"name": "app_pool", "type": "string", "required": False, "description": "Filter by pool name — leave blank for all"},
        ],
    },
    # ══ WINDOWS — REMEDIATION SAFE ════════════════════════════════════════════
    {
        "tool_name": "win_service_restart",
        "name": "Win Service Restart",
        "description": "Restart a Windows service via WinRM Invoke-Command.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Restart-Service -Name {service} -Force }",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True, "description": "Windows service name e.g. W3SVC"},
        ],
    },
    {
        "tool_name": "win_service_stop",
        "name": "Win Service Stop",
        "description": "Stop a Windows service via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Stop-Service -Name {service} -Force }",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
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
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_clear_temp",
        "name": "Win Clear Temp Files",
        "description": "Delete stale files from Windows temp directories via WinRM to free disk space.",
        "command": 'Invoke-Command -ComputerName {host} -ScriptBlock { Remove-Item "$env:TEMP\\*" -Recurse -Force -EA 0; Remove-Item "C:\\Windows\\Temp\\*" -Recurse -Force -EA 0 }',
        "category": "remediation_safe", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",             "type": "string",  "required": True},
            {"name": "include_win_temp", "type": "boolean", "required": False, "default": True,
             "description": "Also clear C:\\Windows\\Temp"},
        ],
    },
    {
        "tool_name": "win_flush_dns",
        "name": "Win Flush DNS",
        "description": "Flush the DNS resolver cache on a Windows host via WinRM.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { ipconfig /flushdns }",
        "category": "remediation_safe", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "win_iis_recycle",
        "name": "Win IIS Recycle App Pool",
        "description": "Recycle an IIS Application Pool via WinRM — drains active connections and starts fresh worker process.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Restart-WebAppPool -Name {app_pool} }",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host",     "type": "string", "required": True},
            {"name": "app_pool", "type": "string", "required": True, "description": "IIS app pool name e.g. DefaultAppPool"},
        ],
    },
    # ══ WINDOWS — REMEDIATION INTRUSIVE ══════════════════════════════════════
    {
        "tool_name": "win_iis_stop_start",
        "name": "Win IIS Stop/Start Website",
        "description": "Stop then start an IIS website via WinRM. Briefly interrupts traffic.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Import-Module WebAdministration; Stop-Website -Name {site}; Start-Sleep 2; Start-Website -Name {site} }",
        "category": "remediation_intrusive", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
            {"name": "site", "type": "string", "required": True, "description": "IIS website name e.g. Default Web Site"},
        ],
    },
    {
        "tool_name": "win_process_kill",
        "name": "Win Process Kill",
        "description": "Forcibly terminate a named process on a Windows host via WinRM (Stop-Process -Force).",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Stop-Process -Name {process_name} -Force }",
        "category": "remediation_intrusive", "blast_radius": 3, "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True,
             "description": "Process name without .exe e.g. notepad, stress"},
        ],
        "process_rules": DEFAULT_PROCESS_RULES,
    },
    {
        "tool_name": "win_reboot",
        "name": "Win Reboot",
        "description": "Reboot a Windows host via WinRM. Requires manual approval.",
        "command": "Invoke-Command -ComputerName {host} -ScriptBlock { Restart-Computer -Force -Delay {delay_seconds} }",
        "category": "remediation_intrusive", "blast_radius": 3, "requires_approval": True,
        "parameters": [
            {"name": "host",          "type": "string",  "required": True},
            {"name": "delay_seconds", "type": "integer", "required": False, "default": 0,
             "description": "Seconds before reboot; 0 = immediate"},
        ],
    },
]

ok = skip = fail = 0
for action in WINDOWS_ACTIONS:
    r = requests.post("http://localhost:8000/api/approved-actions", json=action)
    if r.status_code == 201:
        ok += 1
        print(f"  OK  {action['tool_name']}")
    elif r.status_code == 409:
        skip += 1
        print(f"  --  {action['tool_name']} (already exists)")
    else:
        fail += 1
        print(f"  XX  {action['tool_name']} -> {r.status_code}: {r.text[:120]}")

print(f"\nDone: {ok} created, {skip} skipped, {fail} failed")
