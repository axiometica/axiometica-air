#!/usr/bin/env python3
"""
testapp — simple HTTP backend for agentic_nginx_prod_test.

Endpoints:
  GET /           → 200 HTML index
  GET /health     → 200 JSON status
  GET /metrics    → 200 Prometheus-style text metrics
  GET /slow?ms=N  → 200 after sleeping N ms (default 2000)
  GET /error      → 500 (simulates app error for error-rate runbook)
  GET /log-error  → 200 but writes an ERROR line to the app log

nginx proxies all of these from :80/:443 → :8080.
"""

import json
import os
import sys
import time
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

START_TIME = time.time()
REQUEST_COUNT = {"total": 0, "errors": 0, "slow": 0}
_lock = threading.Lock()

LOG_FILE = "/var/log/app/testapp.log"


def log(level, msg):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] [{level}] testapp: {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        with _lock:
            REQUEST_COUNT["total"] += 1

        if path == "/health":
            self._json(200, {
                "status": "ok",
                "service": "testapp",
                "uptime_seconds": round(time.time() - START_TIME, 1),
                "pid": os.getpid(),
                "requests": REQUEST_COUNT["total"],
            })

        elif path == "/metrics":
            uptime = time.time() - START_TIME
            body = "\n".join([
                "# HELP http_requests_total Total HTTP requests handled",
                "# TYPE http_requests_total counter",
                f'http_requests_total{{status="200"}} {REQUEST_COUNT["total"] - REQUEST_COUNT["errors"]}',
                f'http_requests_total{{status="500"}} {REQUEST_COUNT["errors"]}',
                "# HELP process_uptime_seconds Seconds since process start",
                "# TYPE process_uptime_seconds gauge",
                f"process_uptime_seconds {uptime:.1f}",
                "# HELP process_resident_memory_bytes RSS memory",
                "# TYPE process_resident_memory_bytes gauge",
                f"process_resident_memory_bytes {self._rss()}",
                "# HELP process_cpu_seconds_total CPU seconds consumed",
                "# TYPE process_cpu_seconds_total counter",
                f"process_cpu_seconds_total {self._cpu_time():.2f}",
            ])
            self._text(200, body)

        elif path == "/slow":
            ms = int(qs.get("ms", ["2000"])[0])
            ms = min(ms, 30000)
            with _lock:
                REQUEST_COUNT["slow"] += 1
            time.sleep(ms / 1000)
            self._json(200, {"status": "ok", "delayed_ms": ms})

        elif path == "/error":
            with _lock:
                REQUEST_COUNT["errors"] += 1
            log("ERROR", f"simulated 500 from {self.client_address[0]}")
            self._text(500, "Internal Server Error\n")

        elif path == "/log-error":
            log("ERROR", "application exception: NullPointerException in RequestHandler.process()")
            log("WARN",  "retrying failed downstream call (attempt 3/3)")
            self._json(200, {"logged": "error"})

        elif path == "/":
            html = (
                "<html><body>"
                "<h1>AgenticOS Test Target</h1>"
                "<p>This container is a runbook test target.</p>"
                "<ul>"
                "<li><a href='/health'>/health</a></li>"
                "<li><a href='/metrics'>/metrics</a></li>"
                "<li><a href='/slow?ms=1000'>/slow?ms=1000</a></li>"
                "<li><a href='/error'>/error</a></li>"
                "<li><a href='/log-error'>/log-error</a></li>"
                "</ul>"
                "</body></html>"
            )
            self._html(200, html)

        else:
            self._text(404, "Not Found\n")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text):
        body = text.encode() if isinstance(text, str) else text
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rss(self):
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def _cpu_time(self):
        try:
            with open("/proc/self/stat") as f:
                fields = f.read().split()
            utime = int(fields[13])
            stime = int(fields[14])
            hz = os.sysconf("SC_CLK_TCK")
            return (utime + stime) / hz
        except (OSError, ValueError, AttributeError):
            return 0.0

    def log_message(self, fmt, *args):
        log("INFO", f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 8080
    log("INFO", f"testapp starting on {host}:{port} (pid={os.getpid()})")
    server = HTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("INFO", "testapp shutting down")
