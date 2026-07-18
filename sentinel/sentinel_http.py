"""
Sentinel HTTP wrapper for Kubernetes mode.

Runs bpftrace continuously in a background thread and exposes two endpoints:

  GET  /metrics  — latest 5-second syscall counts as JSON {process: count}
  GET  /health   — liveness/readiness probe (200 ok once bpftrace has data)
  POST /kill?process=<name>  — pkill -9 on the host (hostPID:true makes this work)
"""

import http.server
import json
import logging
import os
import signal
import subprocess
import sys
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sentinel] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

PORT = int(os.getenv("SENTINEL_PORT", "9090"))

BPFTRACE_EXPR = (
    "tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } "
    "interval:s:5 { print(@); clear(@); }"
)

_lock = threading.Lock()
_latest: dict = {}
_ready = threading.Event()  # set once the first snapshot arrives


# ── bpftrace reader ──────────────────────────────────────────────────────────

def _read_bpftrace():
    while True:
        try:
            proc = subprocess.Popen(
                ["bpftrace", "-f", "json", "-e", BPFTRACE_EXPR],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            log.info("bpftrace started (pid %d)", proc.pid)
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "map":
                        counts = obj.get("data", {}).get("@", {})
                        with _lock:
                            _latest.clear()
                            _latest.update(counts)
                        _ready.set()
                except json.JSONDecodeError:
                    pass
            proc.wait()
            log.warning("bpftrace exited (code %d), restarting in 2 s", proc.returncode)
        except FileNotFoundError:
            log.error("bpftrace not found — is the image built correctly?")
        except Exception as exc:
            log.error("bpftrace reader error: %s", exc)

        import time
        time.sleep(2)


# ── HTTP handler ─────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            with _lock:
                snapshot = dict(_latest)
            body = json.dumps(snapshot).encode()
            self._respond(200, "application/json", body)

        elif self.path == "/health":
            if _ready.is_set():
                self._respond(200, "text/plain", b"ok")
            else:
                self._respond(503, "text/plain", b"waiting for first bpftrace snapshot")

        else:
            self._respond(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path.startswith("/kill"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            process = (qs.get("process") or [""])[0].strip()
            if not process:
                self._respond(400, "text/plain", b"missing ?process=")
                return
            try:
                result = subprocess.run(
                    ["pkill", "-9", process],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    log.info("pkill -9 %s succeeded", process)
                    self._respond(200, "text/plain", b"killed")
                else:
                    # returncode 1 means no matching process
                    self._respond(404, "text/plain", b"process not found")
            except Exception as exc:
                log.error("pkill error: %s", exc)
                self._respond(500, "text/plain", str(exc).encode())
        else:
            self._respond(404, "text/plain", b"not found")

    def _respond(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # suppress per-request noise; errors still go to stderr
        pass


# ── entrypoint ───────────────────────────────────────────────────────────────

def main():
    t = threading.Thread(target=_read_bpftrace, daemon=True, name="bpftrace-reader")
    t.start()

    server = http.server.HTTPServer(("", PORT), _Handler)
    log.info("Sentinel HTTP server listening on :%d", PORT)

    # Graceful shutdown on SIGTERM (K8s sends this before SIGKILL)
    def _shutdown(sig, frame):
        log.info("SIGTERM received — shutting down")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
