"""
Outbound notification service — Slack and email.

Called by the global WebSocket poll loop (ws.py) when badge-relevant events
are detected.  All functions are synchronous, non-fatal, and gated by the
corresponding platform settings:

  Slack
  -----
  slack.enabled                  — master Slack switch
  slack.default_channel          — target channel for all alerts
  slack.notify_on_new_incident   — critical/high incident alerts
  slack.notify_on_storm_detected — storm-detected alerts
  slack.notify_on_approval_required — approval-pending alerts

  Email (SMTP)
  -----
  smtp.notification_on_p1        — critical incident email alerts
  smtp.notification_on_p2        — high incident email alerts
  smtp.to_addresses              — comma-separated recipient list

Design notes:
  • Every public function returns bool (True = sent at least one notification).
  • Exceptions are caught and logged — callers are never interrupted.
  • DB sessions are opened / closed internally so the functions can be called
    from asyncio.to_thread() without sharing the poll loop's session.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ── Settings helpers ──────────────────────────────────────────────────────────

def _get_setting(key: str, env_fallback: str = "") -> str:
    """Read a platform setting from DB, falling back to env var."""
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import PlatformSettingModel
        from agentic_os.security.crypto import decrypt_if_encrypted
        db = SessionLocal()
        try:
            row = db.get(PlatformSettingModel, key)
            val = decrypt_if_encrypted(row.value or "").strip() if row else ""
        finally:
            db.close()
        return val or os.getenv(env_fallback, "").strip()
    except Exception as exc:
        logger.debug("Could not read setting %s: %s", key, exc)
        return os.getenv(env_fallback, "").strip()


def _bool_setting(key: str, env_fallback: str = "", default: bool = False) -> bool:
    raw = _get_setting(key, env_fallback)
    if not raw:
        return default
    return raw.lower() in ("true", "1", "yes")


# ── Slack helpers ─────────────────────────────────────────────────────────────

def _slack_client():
    """Return a Slack WebClient, or None if not configured."""
    if not _bool_setting("slack.enabled"):
        return None
    token = _get_setting("slack.bot_token", "SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        from slack_sdk import WebClient  # type: ignore[import]
        return WebClient(token=token)
    except ImportError:
        logger.warning("[Notify] slack-sdk not installed — Slack alerts disabled")
        return None


def _post_slack(
    text: str,
    inc_number: str | None = None,
    channel: str | None = None,
    error_out: list | None = None,
) -> bool:
    """Post a plain-text message to a Slack channel.

    Defaults to slack.default_channel; pass `channel` to post somewhere else
    (e.g. a notification team's own channel) using the same configured bot token.

    If inc_number is provided the posted message's ts is stored in Redis as
    the INC associated with that thread.  This lets the bot know which incident
    the thread is about from the very first reply — before any conversation
    history has been written (eliminates the history-write race condition).

    On any failure, appends a short human-readable reason to `error_out` (if
    given) so callers that report back to a user (e.g. a runbook step result)
    can distinguish "Slack isn't configured at all" from "Slack is configured
    but this specific channel rejected the post" (bot not invited, channel
    archived/renamed, etc.) — both previously collapsed into a bare False.
    """
    client = _slack_client()
    if client is None:
        if error_out is not None:
            error_out.append("Slack is not enabled or has no bot token configured")
        return False
    channel = channel or _get_setting("slack.default_channel")
    if not channel:
        logger.debug("[Notify] Slack skipped — no default_channel configured")
        if error_out is not None:
            error_out.append("no Slack channel configured")
        return False
    try:
        resp = client.chat_postMessage(channel=channel, text=text)
        logger.info("[Notify] Slack message posted to %s", channel)
        posted_ts = (resp.get("ts") if isinstance(resp, dict) else getattr(resp, "data", {}).get("ts"))
        if posted_ts:
            try:
                from agentic_os.api.routes.slack_webhook import register_bot_thread, _store_thread_inc
                register_bot_thread(posted_ts)
                if inc_number:
                    _store_thread_inc(posted_ts, inc_number)
                logger.debug("[Notify] Registered notification thread %s (inc=%s) in Redis",
                             posted_ts, inc_number or "none")
            except Exception as reg_exc:
                logger.debug("[Notify] Could not register thread ts: %s", reg_exc)
        return True
    except Exception as exc:
        logger.warning("[Notify] Slack post failed: %s", exc)
        if error_out is not None:
            # slack_sdk's SlackApiError carries a short machine code (e.g.
            # "channel_not_found", "not_in_channel") in response["error"] —
            # much more actionable than the verbose default str(exc).
            detail = None
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    detail = response.get("error") or response["error"]
                except Exception:
                    detail = None
            error_out.append(f"channel '{channel}': {detail or exc}")
        return False


# ── Email helper ──────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> bool:
    """Send a plain-text email using the configured SMTP + to_addresses settings."""
    try:
        from agentic_os.db.database import SessionLocal
        db = SessionLocal()
        try:
            from agentic_os.services.email_service import EmailService
            svc = EmailService(db)
            recipients = svc.get_recipients()
            if not recipients:
                logger.debug("[Notify] Email skipped — no smtp.to_addresses configured")
                return False
            if not svc.is_configured():
                logger.debug("[Notify] Email skipped — SMTP not configured")
                return False
            return svc.send_incident_notification(recipients, subject, body)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[Notify] Email send failed: %s", exc)
        return False


# ── Public notification functions ─────────────────────────────────────────────

def notify_incident_created(
    incident_number: str,
    title: str,
    severity: str,
    lifecycle_state: str,
    risk_score: Optional[float] = None,
) -> bool:
    """
    Notify operators when a new critical or high severity incident is created.

    Sends Slack + email based on configured toggles.
    The severity threshold for email is controlled by smtp.notification_on_p1
    (critical) and smtp.notification_on_p2 (high).
    """
    sev_upper = (severity or "unknown").upper()
    sent = False

    # ── Slack ─────────────────────────────────────────────────────────────────
    if _bool_setting("slack.notify_on_new_incident"):
        emoji = "🔴" if sev_upper == "CRITICAL" else "🟠"
        lines = [
            f"{emoji} *New {sev_upper} Incident: {incident_number}*",
            title,
            f"State: `{lifecycle_state}`",
        ]
        if risk_score is not None:
            lines.append(f"Risk Score: *{int(round(risk_score))}/100*")
        slack_text = "\n".join(lines)
        sent = _post_slack(slack_text, inc_number=incident_number) or sent

    # ── Email ─────────────────────────────────────────────────────────────────
    priority = "P1" if sev_upper == "CRITICAL" else "P2"
    try:
        from agentic_os.db.database import SessionLocal
        db = SessionLocal()
        try:
            from agentic_os.services.email_service import EmailService
            svc = EmailService(db)
            if svc.should_notify(priority) and svc.is_configured():
                recipients = svc.get_recipients()
                if recipients:
                    subject = f"[{sev_upper}] New Incident: {incident_number}"
                    body = (
                        f"A new {sev_upper} incident has been opened.\n\n"
                        f"Incident: {incident_number}\n"
                        f"Title:    {title}\n"
                        f"State:    {lifecycle_state}\n\n"
                        f"Log in to the Agentic Platform to review and take action."
                    )
                    sent = svc.send_incident_notification(recipients, subject, body) or sent
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[Notify] incident email check failed: %s", exc)

    return sent


def notify_incident_resolved(
    incident_number: str,
    title: str,
    severity: str,
    lifecycle_state: str,
    risk_score: Optional[float] = None,
    remediation_outcome: Optional[str] = None,
) -> bool:
    """
    Notify operators when an incident reaches a terminal state
    (resolved, deployed, rolled_back, rejected, failed).
    Slack only — email is not sent for closures.
    """
    if not _bool_setting("slack.notify_on_incident_resolved", default=True):
        return False

    sev_upper = (severity or "unknown").upper()

    _STATE_EMOJI = {
        "resolved":    "✅",
        "deployed":    "🚀",
        "rolled_back": "↩️",
        "rejected":    "🚫",
        "failed":      "❌",
    }
    emoji = _STATE_EMOJI.get(lifecycle_state.lower(), "✅")
    state_label = lifecycle_state.replace("_", " ").title()

    lines = [
        f"{emoji} *Incident {state_label}: {incident_number}*",
        f"[{sev_upper}] {title}",
    ]
    if risk_score is not None:
        lines.append(f"Risk Score: *{int(round(risk_score))}/100*")
    if remediation_outcome:
        lines.append(f"Outcome: `{remediation_outcome}`")

    return _post_slack("\n".join(lines))


def notify_storm_detected(
    incident_number: str,
    title: str,
    child_count: int,
) -> bool:
    """
    Notify operators when the Storm Agent creates a new event storm.
    Slack only (storms are a platform-specific concept, not a P1/P2 email trigger).
    """
    if not _bool_setting("slack.notify_on_storm_detected", default=True):
        return False

    text = (
        f"⚡ *Event Storm Detected: {incident_number}*\n"
        f"{title}\n"
        f"{child_count} correlated incident(s) grouped — "
        f"review on the Event Storms page."
    )
    return _post_slack(text)


def notify_approval_required(
    incident_number: str,
    title: str,
    severity: str,
    proposed_action: Optional[dict] = None,
    risk_score: Optional[float] = None,
) -> bool:
    """
    Notify operators when a remediation is waiting for approval.
    Sends Slack + email.
    """
    sev_upper = (severity or "unknown").upper()
    sent = False

    # ── Build shared action summary ───────────────────────────────────────────
    action_lines: list[str] = []
    if proposed_action:
        tool   = proposed_action.get("tool", "")
        target = proposed_action.get("target", "")
        blast  = proposed_action.get("blast_radius")
        mttr   = proposed_action.get("estimated_mttr")
        if tool:
            action_line = f"Action: `{tool}`"
            if target:
                action_line += f" on `{target}`"
            action_lines.append(action_line)
        details: list[str] = []
        if blast is not None:
            details.append(f"Blast Radius: {blast}")
        if mttr:
            total_secs = int(mttr)
            mins, secs = divmod(total_secs, 60)
            details.append(f"Est. Recovery: {mins}m {secs}s" if secs else f"Est. Recovery: {mins}m")
        if details:
            action_lines.append(" | ".join(details))

    # ── Slack ─────────────────────────────────────────────────────────────────
    if _bool_setting("slack.notify_on_approval_required", default=True):
        lines = [
            f"⏳ *Approval Required: {incident_number}*",
            f"[{sev_upper}] {title}",
        ]
        if risk_score is not None:
            lines.append(f"Risk Score: *{int(round(risk_score))}/100*")
        lines.extend(action_lines)
        lines.append("A remediation action is waiting for your approval.")
        sent = _post_slack("\n".join(lines), inc_number=incident_number) or sent

    # ── Email ─────────────────────────────────────────────────────────────────
    try:
        from agentic_os.db.database import SessionLocal
        db = SessionLocal()
        try:
            from agentic_os.services.email_service import EmailService
            svc = EmailService(db)
            if svc.is_configured():
                recipients = svc.get_recipients()
                if recipients:
                    subject = f"Action Required: Approval needed for {incident_number}"
                    body_lines = [
                        "A remediation action requires your approval.",
                        "",
                        f"Incident: {incident_number}",
                        f"Title:    {title}",
                        f"Severity: {sev_upper}",
                    ]
                    if risk_score is not None:
                        body_lines.append(f"Risk Score: {int(round(risk_score))}/100")
                    if action_lines:
                        body_lines.append("")
                        body_lines.extend(action_lines)
                    body_lines += [
                        "",
                        "Log in to the Agentic Platform → Approvals to review and decide.",
                    ]
                    body = "\n".join(body_lines)
                    sent = svc.send_incident_notification(recipients, subject, body) or sent
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[Notify] approval email check failed: %s", exc)

    return sent
