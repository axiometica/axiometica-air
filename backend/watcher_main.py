"""
Watcher Brain - Standalone entrypoint
Monitors Sentinel (eBPF) telemetry and orchestrates incident response.

Also runs a lightweight HTTP server on port 8080 that the workflow engine
can call to execute real process kills inside the sentinel container.
"""

import asyncio
import logging
import sys
import os
import json
import subprocess
import threading

# Add src directory to path
sys.path.insert(0, "/app/src")

from agentic_os.services.watcher_service import WatcherService

# Configure logging - DEBUG level to see detailed diagnostics
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Module-level watcher reference so Kill-API handlers can access it
_watcher_instance: "WatcherService | None" = None  # type: ignore[name-defined]


def _adapter():
    """Return the active ExecutionAdapter (falls back to DockerAdapter if watcher not ready)."""
    if _watcher_instance is not None and hasattr(_watcher_instance, "adapter"):
        return _watcher_instance.adapter
    from agentic_os.services.adapters.docker_adapter import DockerAdapter
    return DockerAdapter()

# ─────────────────────────────────────────────
# Kill-API  (tiny aiohttp server, port 8080)
# ─────────────────────────────────────────────

async def handle_kill(request):
    """
    POST /kill
    Body: { "process_name": "yes", "container": "sentinel_senses", "signal": "SIGKILL" }
    Routes through the active ExecutionAdapter (Docker / SSH / vCenter).
    """
    from aiohttp import web

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    process_name = body.get("process_name", "").strip()
    target       = body.get("container", "sentinel_senses").strip()
    signal       = body.get("signal", "SIGKILL").strip()

    if not process_name:
        return web.json_response({"success": False, "error": "process_name is required"}, status=400)

    adapter = _adapter()
    logger.info(f"[KILL-API] kill '{process_name}' on '{target}' via {adapter.adapter_name}")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: adapter.kill_process(target, process_name, signal),
        )
        raw_output = "\n".join(filter(None, [result.stdout, result.stderr])) or None
        if result.success:
            msg = result.stdout or f"Sent {signal} to '{process_name}' on '{target}'"
            logger.info(f"[KILL-API] ✓ {msg}")
            return web.json_response({
                "success": True, "message": msg,
                "command": result.command, "raw_output": raw_output,
            })
        else:
            msg = result.stderr or result.stdout or f"Kill failed (rc={result.returncode})"
            logger.error(f"[KILL-API] ✗ {msg}")
            return web.json_response({
                "success": False, "error": msg,
                "command": result.command, "raw_output": raw_output,
            })
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def handle_check_process(request):
    """
    POST /check-process
    Body: { "process_name": "yes", "container": "sentinel_senses" }
    Returns: { "running": bool }
    Routes through the active ExecutionAdapter.
    """
    from aiohttp import web
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    process_name = body.get("process_name", "").strip()
    target       = body.get("container", "sentinel_senses").strip()

    if not process_name:
        return web.json_response({"running": False, "error": "process_name is required"}, status=400)

    adapter = _adapter()
    logger.info(f"[CHECK-API] check '{process_name}' on '{target}' via {adapter.adapter_name}")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: adapter.check_process(target, process_name)
        )
        running = result.get("running", False)
        logger.info(f"[CHECK-API] '{process_name}' running={running}")
        return web.json_response({"running": running, "process_name": process_name})
    except Exception as exc:
        logger.error(f"[CHECK-API] error: {exc}")
        return web.json_response({"running": False, "error": str(exc)})


async def handle_detect_port_process(request):
    """
    POST /detect-port-process
    Body: { "container": "agentic_os_flower", "port": 5555 }
    Returns: { "found": bool, "process": str, "pid": int }
    On-demand port→process lookup for ToolRegistryAgent.
    Tries ss → netstat → /proc/net/tcp inode walk for maximum portability.
    """
    from aiohttp import web
    import re as _re

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"found": False, "error": "Invalid JSON body"}, status=400)

    container = body.get("container", "").strip()
    port      = int(body.get("port", 0))

    if not container or not port:
        return web.json_response({"found": False, "error": "container and port required"}, status=400)

    logger.info(f"[PORT-API] detect process on port {port} in '{container}'")
    loop = asyncio.get_event_loop()

    def _run(cmd, timeout=5):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    proc_name, pid, found = "", 0, False

    # ── Method 1: ss ─────────────────────────────────────────────────────────
    try:
        r = await loop.run_in_executor(None, lambda: _run(
            ["docker", "exec", container, "ss", "-tlnp", f"sport = :{port}"]
        ))
        for line in r.stdout.splitlines():
            if f":{port}" in line:
                m = _re.search(r'\("([^"]+)",pid=(\d+)', line)
                if m:
                    proc_name, pid = m.group(1), int(m.group(2))
                found = True
                break
    except Exception:
        pass

    # ── Method 2: netstat ────────────────────────────────────────────────────
    if not found:
        try:
            r = await loop.run_in_executor(None, lambda: _run(
                ["docker", "exec", container, "netstat", "-tlnp"]
            ))
            for line in r.stdout.splitlines():
                if "LISTEN" in line and f":{port} " in line:
                    parts = line.split()
                    pid_prog = parts[-1] if parts else "-"
                    if "/" in pid_prog:
                        pid_str, proc_name = pid_prog.split("/", 1)
                        try:
                            pid = int(pid_str)
                        except ValueError:
                            pass
                    found = True
                    break
        except Exception:
            pass

    # ── Method 3: /proc/net/tcp inode walk (universal fallback) ──────────────
    if not found:
        try:
            proc_sh = (
                f"awk 'NR>1 && $4==\"0A\"{{split($2,a,\":\"); print a[2],$10}}' "
                f"/proc/net/tcp /proc/net/tcp6 2>/dev/null "
                f"| while read hexport inode; do "
                f"  p=$((0x$hexport)); "
                f"  [ \"$p\" = \"{port}\" ] || continue; "
                f"  for fd_path in /proc/[0-9]*/fd/*; do "
                f"    pid=$(echo $fd_path | cut -d/ -f3); "
                f"    link=$(readlink $fd_path 2>/dev/null); "
                f"    if [ \"$link\" = \"socket:[$inode]\" ]; then "
                f"      comm=$(cat /proc/$pid/comm 2>/dev/null); "
                f"      echo \"$comm $pid\"; break 2; "
                f"    fi; "
                f"  done; "
                f"done"
            )
            r = await loop.run_in_executor(None, lambda: _run(
                ["docker", "exec", container, "sh", "-c", proc_sh], timeout=10
            ))
            if r.stdout.strip():
                parts = r.stdout.strip().split()
                if parts:
                    proc_name = parts[0]
                    pid = int(parts[1]) if len(parts) > 1 else 0
                    found = True
        except Exception:
            pass

    logger.info(f"[PORT-API] port {port} on {container}: found={found} process='{proc_name}' pid={pid}")
    return web.json_response({"found": found, "process": proc_name, "pid": pid, "port": port})


async def handle_restart(request):
    """
    POST /restart
    Body: { "container": "agentic_os_flower", "force": false }
    Routes through the active ExecutionAdapter (docker restart / VM reboot / kubectl rollout).
    """
    from aiohttp import web
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    target = body.get("container", "").strip()
    force  = bool(body.get("force", False))
    if not target:
        return web.json_response({"success": False, "error": "container is required"}, status=400)

    adapter = _adapter()
    logger.info(f"[RESTART-API] restart '{target}' force={force} via {adapter.adapter_name}")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: adapter.restart_target(target, force=force)
        )
        logger.info(f"[RESTART-API] rc={result.returncode} msg={result.stdout}")
        return web.json_response({
            "success":   result.success,
            "message":   result.stdout or result.stderr,
            "container": target,
            "action":    "restart",
        })
    except Exception as exc:
        logger.error(f"[RESTART-API] Failed to restart '{target}': {exc}")
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def handle_exec(request):
    """
    POST /exec
    General-purpose command execution — routes through the active adapter.

    Body:
      {
        "container": "agentic_os_flower",   # target name
        "command":   "df -h /",
        "mode":      "container",           # "container" (default) | "host"
        "timeout":   10
      }
    """
    from aiohttp import web
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    command = body.get("command", "").strip()
    mode    = body.get("mode", "container").strip()
    target  = body.get("container", "").strip()
    timeout = int(body.get("timeout", 10))

    if not command:
        return web.json_response({"success": False, "error": "command is required"}, status=400)
    if mode == "container" and not target:
        return web.json_response({"success": False, "error": "container is required for mode=container"}, status=400)

    adapter = _adapter()
    logger.info(f"[EXEC-API] mode={mode} target={target} via {adapter.adapter_name}: {command[:80]}")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: adapter.exec(target, command, timeout=timeout, mode=mode)
        )
        logger.info(f"[EXEC-API] rc={result.returncode} stdout_len={len(result.stdout)}")
        return web.json_response(result.to_dict())
    except Exception as exc:
        logger.error(f"[EXEC-API] error: {exc}")
        return web.json_response({
            "success": False, "error": str(exc),
            "command": command, "stdout": "", "stderr": "", "returncode": -1,
        }, status=500)


async def handle_health(request):
    from aiohttp import web
    return web.json_response({"status": "ok", "service": "watcher-kill-api"})


async def handle_reset(request):
    """
    POST /reset
    Clears all in-memory watcher state (active_conditions, active_workflow_ids,
    cooldowns, consecutive poll counters) so the watcher fires fresh events.
    Called by the admin delete-all endpoint after incidents are wiped from the DB.
    """
    from aiohttp import web
    import pathlib, json as _json
    global _watcher_instance

    if _watcher_instance is None:
        return web.json_response({"success": False, "error": "Watcher not initialised yet"}, status=503)

    # Clear all in-memory tracking state.
    # IMPORTANT: attribute names must exactly match watcher_service.py definitions.
    _watcher_instance.active_conditions.clear()

    # _active_workflow_ids (underscore-prefixed) — tracks open incident IDs per resource.
    # Previously incorrectly referenced as 'active_workflow_ids' (no underscore) so
    # hasattr() always returned False and this dict was never cleared, causing
    # "Incident already open" after a reset/delete-all.
    _watcher_instance._active_workflow_ids.clear()

    # consecutive_anomaly_counts — consecutive poll counters per "container:event_type".
    # Previously incorrectly referenced as '_anomaly_counts'.
    _watcher_instance.consecutive_anomaly_counts.clear()

    # cooldown_until — single Optional[datetime], not a dict.  Set to None to clear.
    _watcher_instance.cooldown_until = None

    # per_resource_cooldown — per-resource cooldown dict.
    _watcher_instance.per_resource_cooldown.clear()

    if hasattr(_watcher_instance, '_active_process_by_resource'):
        _watcher_instance._active_process_by_resource.clear()

    # Also reset the status file so state doesn't restore on next restart
    _status_path = pathlib.Path("/app/.state/watcher_status.json")
    if _status_path.exists():
        try:
            _status = _json.loads(_status_path.read_text())
            _status["active_conditions"] = {}
            _status["active_workflow_ids"] = {}
            _status["active_incident_id"] = ""
            _status["cooldown_until"] = None
            _status_path.write_text(_json.dumps(_status, indent=2))
        except Exception as e:
            logger.warning(f"[RESET-API] Could not clear status file: {e}")

    logger.info("[RESET-API] Watcher state cleared — ready for fresh incident detection")
    return web.json_response({
        "success": True,
        "message": "Watcher state cleared — active_conditions, cooldowns, and consecutive counters reset",
    })


async def handle_config(request):
    """
    PUT /config
    Body: { "cpu_threshold": 75.0, "memory_threshold": 85.0, ... }
    Applies new thresholds to the running watcher immediately (no restart needed).
    """
    from aiohttp import web
    global _watcher_instance

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if _watcher_instance is None:
        return web.json_response({"success": False, "error": "Watcher not initialised yet"}, status=503)

    applied: dict = {}

    def _set(attr: str, key: str, cast):
        if key in body:
            setattr(_watcher_instance, attr, cast(body[key]))
            applied[key] = getattr(_watcher_instance, attr)

    _set("poll_interval",          "poll_interval",          int)
    _set("anomaly_threshold",      "syscall_threshold",      int)
    _set("connection_threshold",   "connection_threshold",   int)
    _set("cooldown_seconds",       "cooldown_seconds",       int)
    _set("min_consecutive_polls",  "min_consecutive_polls",  int)
    _set("discovery_interval_polls", "discovery_interval_polls", int)
    _set("discovery_enabled",      "discovery_enabled",      lambda v: str(v).lower() in ("true", "1", "yes"))

    # CPU — also recompute hysteresis clear threshold
    if "cpu_threshold" in body:
        _watcher_instance.cpu_threshold = float(body["cpu_threshold"])
        _watcher_instance.cpu_clear_threshold = _watcher_instance.cpu_threshold * 0.80
        applied["cpu_threshold"] = _watcher_instance.cpu_threshold

    # Memory — also recompute hysteresis clear threshold
    if "memory_threshold" in body:
        _watcher_instance.memory_threshold = float(body["memory_threshold"])
        _watcher_instance.memory_clear_threshold = _watcher_instance.memory_threshold * 0.80
        applied["memory_threshold"] = _watcher_instance.memory_threshold

    # Disk
    if "disk_threshold" in body:
        _watcher_instance.disk_threshold = float(body["disk_threshold"])
        applied["disk_threshold"] = _watcher_instance.disk_threshold

    logger.info(f"[CONFIG-API] Applied live config update: {applied}")
    return web.json_response({"success": True, "applied": applied})


async def handle_log_monitors_reload(request):
    """
    POST /log-monitors/reload
    Body: [{ "name": "...", "file": "...", "pattern": "...", "event_type": "...", "interval_sec": 5, "enabled": true }, ...]
    Reloads log monitor configuration without restarting the watcher.
    Preserves file position tracking for unchanged monitors.
    """
    from aiohttp import web
    global _watcher_instance

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if _watcher_instance is None:
        return web.json_response({"success": False, "error": "Watcher not initialised yet"}, status=503)

    if not hasattr(_watcher_instance, 'log_monitor'):
        return web.json_response({"success": False, "error": "Log monitor not initialised"}, status=503)

    if not isinstance(body, list):
        return web.json_response({"success": False, "error": "Body must be a list of log monitor configs"}, status=400)

    try:
        # Reload log monitors
        _watcher_instance.log_monitor.reload_configs(body)
        logger.info(f"[LOG-MONITORS-API] Reloaded {len(body)} log monitor configurations")
        return web.json_response({
            "success": True,
            "message": f"Reloaded {len(body)} log monitor configurations",
            "count": len(body),
        })
    except Exception as exc:
        logger.error(f"[LOG-MONITORS-API] Failed to reload log monitors: {exc}")
        return web.json_response({"success": False, "error": str(exc)}, status=400)


async def handle_test_check(request):
    """
    POST /test-check
    Body: { "check_type": "http", "target": "http://...", "port": null,
            "expected_status": 200, "timeout_ms": 5000, ... }
    Runs a single external check from this watcher's network context and returns
    the raw result — status, HTTP code, response time, and body snippet.
    """
    from aiohttp import web
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    target = body.get("target", "").strip()
    if not target:
        return web.json_response({"success": False, "error": "target is required"}, status=400)

    try:
        from agentic_os.services.advanced_monitoring_service import (
            AdvancedMonitoringService, ExternalCheckConfig,
        )
        svc = AdvancedMonitoringService()
        cfg = ExternalCheckConfig(
            check_type=body.get("check_type", "http"),
            target=target,
            port=body.get("port") or 0,
            expected_status=body.get("expected_status", 200),
            timeout_ms=body.get("timeout_ms", 5000),
            latency_threshold_ms=body.get("latency_threshold_ms", 0),
            tls_expiry_warning_days=body.get("tls_expiry_warning_days", 30),
        )
        result = svc.run_external_check(cfg)
        return web.json_response({
            "success": True,
            "status": result.status,
            "status_code": result.status_code,
            "response_time_ms": round(result.response_time_ms, 1),
            "response_body": result.response_body,
            "tls_days_remaining": result.tls_days_remaining,
            "error": result.error,
        })
    except Exception as exc:
        logger.error(f"[TEST-CHECK] Unexpected error: {exc}", exc_info=True)
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def _run_kill_server_async():
    """aiohttp kill-API server — runs inside the dedicated kill-server thread."""
    from aiohttp import web
    app = web.Application()
    app.router.add_post("/kill", handle_kill)
    app.router.add_post("/restart", handle_restart)
    app.router.add_post("/check-process", handle_check_process)
    app.router.add_post("/detect-port-process", handle_detect_port_process)
    app.router.add_post("/exec", handle_exec)
    app.router.add_get("/health", handle_health)
    app.router.add_put("/config", handle_config)
    app.router.add_post("/reset", handle_reset)
    app.router.add_post("/log-monitors/reload", handle_log_monitors_reload)
    app.router.add_post("/test-check", handle_test_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("🔫 [KILL-API] Listening on http://0.0.0.0:8080")

    while True:
        await asyncio.sleep(3600)


def start_kill_server_thread() -> threading.Thread:
    """
    Start the aiohttp kill-API in a dedicated daemon thread with its own
    event loop.

    This isolation is critical: watcher.run() makes blocking subprocess calls
    (docker exec for process-hunt, docker stats, etc.) that can stall the
    asyncio event loop for 10-20 s.  Running the HTTP server in its own loop
    ensures kill/check-process requests are always processed promptly —
    regardless of what the monitoring loop is doing.
    """
    def _thread_main():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_kill_server_async())
        except Exception as exc:
            logger.error(f"[KILL-API] Thread crashed: {exc}", exc_info=True)
        finally:
            loop.close()

    t = threading.Thread(target=_thread_main, daemon=True, name="kill-api-server")
    t.start()
    return t


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

async def main():
    """Main entry point for Watcher Brain."""
    logger.info("=" * 60)
    logger.info("🧠 WATCHER BRAIN - STARTING")
    logger.info("=" * 60)

    # Initialize Watcher service with all threshold parameters
    global _watcher_instance

    # Security: disable discovery and external checks by default.
    # Enable only after watcher is approved via WATCHER_DISCOVERY_ENABLED env var.
    discovery_enabled = os.getenv("WATCHER_DISCOVERY_ENABLED", "false").lower() == "true"

    watcher = WatcherService(
        sentinel_container=os.getenv("SENTINEL_CONTAINER", "sentinel_senses"),
        api_base_url=os.getenv("WATCHER_API_URL", "http://backend:8000"),
        watcher_name=os.getenv("WATCHER_NAME", "watcher_brain"),
        poll_interval=int(os.getenv("WATCHER_POLL_INTERVAL", "10")),
        anomaly_threshold=int(os.getenv("WATCHER_ANOMALY_THRESHOLD", "20000")),
        cpu_threshold=float(os.getenv("WATCHER_CPU_THRESHOLD", "80.0")),
        memory_threshold=float(os.getenv("WATCHER_MEMORY_THRESHOLD", "90.0")),
        disk_threshold=float(os.getenv("WATCHER_DISK_THRESHOLD", "90.0")),
        connection_threshold=int(os.getenv("WATCHER_CONNECTION_THRESHOLD", "1000")),
        cooldown_seconds=int(os.getenv("WATCHER_COOLDOWN_SECONDS", "60")),
        min_consecutive_polls=int(os.getenv("WATCHER_MIN_CONSECUTIVE_POLLS", "3")),
        discovery_interval_polls=int(os.getenv("WATCHER_DISCOVERY_INTERVAL_POLLS", "15")),
        discovery_enabled=discovery_enabled,
    )

    if discovery_enabled:
        logger.info("⚠️  [STARTUP] Discovery and external checks are ENABLED")
    else:
        logger.info("ℹ️  [STARTUP] Discovery and external checks are DISABLED by default")

    _watcher_instance = watcher

    # Prime config from platform API before the run loop starts.
    # Retry a few times since the backend may still be warming up.
    for attempt in range(1, 4):
        loaded = await watcher.load_config_from_api()
        if loaded is not False:  # True = changes applied, False = no change but reachable
            logger.info(f"✓ [STARTUP] Watcher config loaded from platform API")
            break
        if attempt < 3:
            logger.info(f"⏳ [STARTUP] Platform API not ready, retrying in 5s (attempt {attempt}/3)…")
            await asyncio.sleep(5)
    else:
        logger.warning("⚠️  [STARTUP] Could not load config from API — using env var / file defaults")

    # Start the kill-API in its own thread/event-loop so blocking subprocess
    # calls in watcher.run() cannot starve the HTTP server.
    start_kill_server_thread()

    # Register this watcher with the platform so the UI can discover it
    await watcher.register_with_api()

    # ── APPROVAL GATE ────────────────────────────────────────────────────────
    # Do NOT start monitoring until the watcher is approved by an admin.
    # This ensures no metrics are collected or incidents created until
    # the watcher has been explicitly authorized.
    logger.info("⏸️  [STARTUP] Waiting for watcher approval before starting monitoring...")
    approval_check_interval = 30  # Check every 30 seconds
    approval_wait_attempts = 0
    while True:
        approval_wait_attempts += 1
        try:
            import httpx
            logger.debug(f"[STARTUP] Approval check attempt {approval_wait_attempts}...")
            async with httpx.AsyncClient(timeout=5.0, headers=watcher._api_headers) as client:
                resp = await client.get(
                    f"{watcher.api_base_url}/api/monitoring/watchers"
                )
                logger.debug(f"[STARTUP] Approval check response: {resp.status_code}")
                if resp.status_code == 200:
                    watchers = resp.json()
                    logger.debug(f"[STARTUP] Got {len(watchers)} watchers from API")
                    # Find this watcher in the list
                    my_registration = next(
                        (w for w in watchers if w.get("watcher_name") == watcher.watcher_name),
                        None
                    )
                    if my_registration:
                        status = my_registration.get("registration_status", "unknown")
                        if status == "approved":
                            logger.info("✅ [STARTUP] Watcher approved — starting monitoring")
                            break
                        else:
                            if approval_wait_attempts % 2 == 1:  # Log every other attempt to avoid noise
                                logger.info(f"⏳ [STARTUP] Watcher status: {status} — waiting for approval…")
                    else:
                        logger.info(
                            f"ℹ️  [STARTUP] Watcher '{watcher.watcher_name}' not found in registration "
                            f"list yet — retrying registration (attempt {approval_wait_attempts})…"
                        )
                        await watcher.register_with_api()
                else:
                    logger.warning(f"[STARTUP] Approval check returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"[STARTUP] Could not check approval status (attempt {approval_wait_attempts}): {e}")

        logger.debug(f"[STARTUP] Sleeping for {approval_check_interval}s before next approval check")
        await asyncio.sleep(approval_check_interval)
    # ─────────────────────────────────────────────────────────────────────────

    # Run the monitoring loop in the main event loop
    try:
        await watcher.run()
    except Exception as e:
        logger.error(f"Fatal error in Watcher: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
