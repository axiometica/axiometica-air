"""
Admin Logs API — in-memory circular log buffer + download endpoint.

The LogBufferHandler is attached to the root logger at startup so it
captures log lines from all modules. The buffer holds the most recent
MAX_LINES entries; older lines are automatically discarded.
"""

import logging
import threading
from collections import deque
from datetime import datetime
from fastapi import APIRouter, Response

router = APIRouter(prefix="/api/admin", tags=["admin-logs"])

MAX_LINES = 5000   # Keep last 5 000 log lines in memory

_buffer: deque = deque(maxlen=MAX_LINES)
_lock = threading.Lock()


class LogBufferHandler(logging.Handler):
    """Thread-safe logging handler that writes to the in-memory deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with _lock:
                _buffer.append(line)
        except Exception:
            pass   # Never let the log handler crash the application


# ── Module-level setup: attach handler once when this module is imported ──────

_handler = LogBufferHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))

def init_log_buffer() -> None:
    """Attach the buffer handler to the root logger.  Call once at startup."""
    root = logging.getLogger()
    # Guard: don't add a second handler if already attached
    if not any(isinstance(h, LogBufferHandler) for h in root.handlers):
        root.addHandler(_handler)
        logging.getLogger(__name__).info("Log buffer handler initialised (max %d lines)", MAX_LINES)


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/logs")
def get_logs(last: int = 200):
    """
    Return the most recent *last* log lines (default 200, max 5 000).
    Each item is a plain string: the formatted log record.
    """
    last = min(last, MAX_LINES)
    with _lock:
        lines = list(_buffer)[-last:]
    return {"count": len(lines), "lines": lines}


@router.get("/logs/download")
def download_logs(last: int = MAX_LINES):
    """
    Download all buffered log lines as a plain-text file.
    Suitable for attaching to bug reports / support tickets.
    """
    last = min(last, MAX_LINES)
    with _lock:
        lines = list(_buffer)[-last:]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename  = f"agentic_platform_logs_{timestamp}.txt"
    content   = "\n".join(lines)

    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
