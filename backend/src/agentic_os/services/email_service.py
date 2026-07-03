"""
Email notification service.

Reads SMTP configuration from platform_settings (category='smtp') and sends
incident notifications via smtplib.  Called by the incident pipeline when
``general.email_notifications_enabled`` is true.

Usage::

    from agentic_os.services.email_service import EmailService

    svc = EmailService(db)
    if svc.is_configured() and svc.should_notify("P1"):
        svc.send_incident_notification(
            to_addresses=["oncall@example.com"],
            subject="[P1] High CPU on api-gateway",
            body="An automated incident has been raised ...",
        )
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmailService:
    """Thin SMTP sender backed by DB-stored SMTP configuration."""

    def __init__(self, db=None):
        self._db = db

    # ── Config loading ────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Read all smtp.* settings from the DB into a plain dict."""
        if self._db is None:
            return {}
        from agentic_os.db.models import PlatformSettingModel
        from agentic_os.security.crypto import decrypt_if_encrypted
        config: dict = {}
        for key in (
            "smtp.host", "smtp.port", "smtp.username", "smtp.password",
            "smtp.from_address", "smtp.use_tls",
            "smtp.notification_on_p1", "smtp.notification_on_p2",
            "smtp.to_addresses",
        ):
            row = self._db.get(PlatformSettingModel, key)
            if row is None:
                continue
            short = key.split(".", 1)[1]
            if row.value_type == "int":
                config[short] = int(row.value) if row.value else 0
            elif row.value_type == "bool":
                config[short] = (row.value or "").lower() in ("true", "1", "yes")
            elif short == "password":
                config[short] = decrypt_if_encrypted(row.value or "")
            else:
                config[short] = row.value or ""
        return config

    # ── Public API ────────────────────────────────────────────────────────────

    def get_recipients(self) -> List[str]:
        """Return the configured alert recipient list (from smtp.to_addresses)."""
        cfg = self._load_config()
        raw = cfg.get("to_addresses", "")
        return [r.strip() for r in raw.split(",") if r.strip()]

    def is_configured(self) -> bool:
        """Return True if the minimum SMTP fields (host + from_address) are set."""
        cfg = self._load_config()
        return bool(cfg.get("host") and cfg.get("from_address"))

    def should_notify(self, priority: str) -> bool:
        """Return True if email notifications are enabled for this priority level."""
        cfg = self._load_config()
        p = (priority or "").upper()
        if p == "P1":
            return bool(cfg.get("notification_on_p1", True))
        if p == "P2":
            return bool(cfg.get("notification_on_p2", False))
        return False

    def send_incident_notification(
        self,
        to_addresses: List[str],
        subject: str,
        body: str,
    ) -> bool:
        """
        Send a plain-text incident notification email.

        Non-fatal — logs the error and returns False on any failure so that
        the caller's pipeline is never interrupted by SMTP issues.
        """
        cfg       = self._load_config()
        host      = cfg.get("host", "")
        port      = int(cfg.get("port", 587))
        username  = cfg.get("username", "")
        password  = cfg.get("password", "")
        from_addr = cfg.get("from_address", "")
        use_tls   = bool(cfg.get("use_tls", True))

        if not host or not from_addr:
            logger.warning("[Email] SMTP not configured — skipping notification")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = from_addr
            msg["To"]      = ", ".join(to_addresses)
            msg.attach(MIMEText(body, "plain", "utf-8"))

            if use_tls:
                server = smtplib.SMTP(host, port, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(host, port, timeout=10)

            if username and password:
                server.login(username, password)

            server.sendmail(from_addr, to_addresses, msg.as_string())
            server.quit()
            logger.info("[Email] Notification sent to %s", to_addresses)
            return True

        except Exception as exc:
            logger.error("[Email] Failed to send notification: %s", exc)
            return False
