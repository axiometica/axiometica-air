"""
Slack ChatOps integration for the AI Ops Assistant.

POST /api/webhooks/slack/events   — Slack Events API (app_mention, DMs)
POST /api/webhooks/slack/actions  — Block Kit interactive buttons (approve/reject)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SLACK APP SETUP  (one-time, ~10 minutes)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Go to  https://api.slack.com/apps  → Create New App → From Scratch
2. OAuth & Permissions → Bot Token Scopes — add:
     chat:write            (post and update messages)
     app_mentions:read     (receive @bot mentions)
     im:read               (receive direct messages)
     im:write              (send direct messages)
     im:history            (read DM history — used for threading)
     users:read            (fetch user profile)
     users:read.email      (map Slack email → platform role)
3. Install App → Install to Workspace → copy Bot User OAuth Token
   → set env var  SLACK_BOT_TOKEN=xoxb-...
4. Basic Information → Signing Secret
   → set env var  SLACK_SIGNING_SECRET=...
5. Event Subscriptions → Enable Events
   Request URL: https://<your-host>/api/webhooks/slack/events
   Subscribe to Bot Events:
     app_mention
     message.im
6. Interactivity & Shortcuts → Enable
   Request URL: https://<your-host>/api/webhooks/slack/actions
7. Reinstall the app to the workspace after any scope change.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW IT WORKS
  • @mention the bot or send it a DM — same LLM context as the web UI.
  • Ask about incidents, approvals, MTTR, runbooks — anything the web UI supports.
  • If you request an approve/reject, the bot replies with interactive buttons.
  • Clicking Confirm executes the decision and updates the platform.
  • Role check: viewer-role users can query but cannot trigger actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_SLACK_HISTORY = 10   # conversation pairs kept per thread

# ── Redis-backed shared state (cross-worker, cross-restart) ──────────────────
#
# With multiple uvicorn workers each opening their own Socket Mode connection,
# any per-process in-memory state (sets, dicts) would be invisible across workers.
# Redis is already in the stack (used by Celery) — we use it for both:
#   • Active-thread tracking  → any worker can respond to thread replies without @mention
#   • Per-thread conversation history → context survives worker rebalancing
#
_REDIS_URL       = os.getenv("REDIS_URL", "redis://redis:6379")
_BOT_THREADS_KEY = "slack:bot_threads"   # Redis set  — active thread timestamps
_HIST_PREFIX     = "slack:hist:"          # Redis string key per thread (JSON list)
_STATE_TTL       = 86400 * 14            # 14-day expiry on all keys


def _redis():
    """Return a Redis client (cheap — connection pooled internally by redis-py)."""
    try:
        import redis as _r
        return _r.Redis.from_url(_REDIS_URL, decode_responses=True,
                                 socket_connect_timeout=1, socket_timeout=1)
    except Exception:
        return None


def _is_bot_thread(thread_ts: str) -> bool:
    """Return True if the bot has already posted/been mentioned in this thread."""
    try:
        r = _redis()
        return bool(r and r.sismember(_BOT_THREADS_KEY, thread_ts))
    except Exception:
        return False


def register_bot_thread(thread_ts: str) -> None:
    """Mark a thread as active so future replies need no @mention. Cross-worker safe."""
    try:
        r = _redis()
        if r:
            r.sadd(_BOT_THREADS_KEY, thread_ts)
            r.expire(_BOT_THREADS_KEY, _STATE_TTL)
    except Exception as exc:
        logger.debug("[SlackState] register_bot_thread failed: %s", exc)


def _get_thread_history(thread_key: str) -> list[dict]:
    """Fetch conversation history for a thread from Redis."""
    try:
        r = _redis()
        if r:
            data = r.get(f"{_HIST_PREFIX}{thread_key}")
            return json.loads(data) if data else []
    except Exception:
        pass
    return []


def _set_thread_history(thread_key: str, history: list[dict]) -> None:
    """Persist conversation history for a thread to Redis."""
    try:
        r = _redis()
        if r:
            r.set(f"{_HIST_PREFIX}{thread_key}", json.dumps(history), ex=_STATE_TTL)
    except Exception as exc:
        logger.debug("[SlackState] _set_thread_history failed: %s", exc)


def _store_thread_inc(thread_ts: str, inc_number: str) -> None:
    """Associate an INC number with a notification thread.

    Written when the notification is posted — before any conversation history
    exists — so the bot knows which incident the thread is about from the
    very first reply (no race condition with history writes).
    """
    try:
        r = _redis()
        if r:
            r.set(f"slack:thread_inc:{thread_ts}", inc_number, ex=_STATE_TTL)
    except Exception as exc:
        logger.debug("[SlackState] _store_thread_inc failed: %s", exc)


def _get_thread_inc(thread_ts: str) -> str | None:
    """Return the INC number associated with a notification thread (if any)."""
    try:
        r = _redis()
        return r.get(f"slack:thread_inc:{thread_ts}") if r else None
    except Exception:
        return None


def _claim_event(envelope_id: str) -> bool:
    """Return True if this worker should handle the event (first to claim it).

    Uses Redis NX to deduplicate across workers — Slack delivers Socket Mode
    events to every connected client, so without this both uvicorn workers
    would respond to the same message, producing duplicate replies.
    """
    try:
        r = _redis()
        return bool(r and r.set(f"slack:dedup:{envelope_id}", "1", nx=True, ex=300))
    except Exception:
        return True  # Redis down → process anyway (better than silent drop)

# ── Settings helpers ──────────────────────────────────────────────────────────

def _get_slack_setting(key: str, env_fallback: str = "") -> str:
    """
    Read a Slack setting value from platform_settings (DB), falling back to
    the corresponding environment variable when the DB row is missing or empty.

    Opens and closes its own short-lived DB session so it can be called from
    any context (signature verification, lazy client init, etc.).
    """
    try:
        from agentic_os.db.database import SessionLocal
        db = SessionLocal()
        try:
            from agentic_os.db.models import PlatformSettingModel
            from agentic_os.security.crypto import decrypt_if_encrypted
            row = db.get(PlatformSettingModel, key)
            db_value = decrypt_if_encrypted(row.value or "").strip() if row else ""
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Could not read Slack setting %s from DB: %s", key, exc)
        db_value = ""

    return db_value or os.getenv(env_fallback, "").strip()


# ── Slack SDK (optional dependency) ──────────────────────────────────────────

def _slack_client():
    """Return a Slack WebClient. Raises if token not configured."""
    token = _get_slack_setting("slack.bot_token", "SLACK_BOT_TOKEN")
    if not token:
        raise ValueError(
            "SLACK_BOT_TOKEN is not configured. "
            "Add it via Settings → Slack ChatOps or set the SLACK_BOT_TOKEN env var."
        )
    try:
        from slack_sdk import WebClient  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "slack-sdk is not installed. "
            "It is listed in requirements.txt — rebuild the Docker image."
        )
    return WebClient(token=token)


def _slack_configured() -> bool:
    """Return True if both Slack credentials are present (DB or env)."""
    return bool(
        _get_slack_setting("slack.bot_token", "SLACK_BOT_TOKEN")
        and _get_slack_setting("slack.signing_secret", "SLACK_SIGNING_SECRET")
    )


# ── Request signature verification ───────────────────────────────────────────

def _verify_signature(headers, raw_body: bytes) -> bool:
    """
    Verify Slack request using HMAC-SHA256.
    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    secret = _get_slack_setting("slack.signing_secret", "SLACK_SIGNING_SECRET")
    if not secret:
        logger.warning(
            "SLACK_SIGNING_SECRET not set — skipping signature verification (dev mode only)"
        )
        return True  # Allow in local dev; always set in production

    ts  = headers.get("x-slack-request-timestamp", "")
    sig = headers.get("x-slack-signature", "")

    if not ts or not sig:
        return False

    # Reject stale requests (replay-attack prevention — 5-minute window)
    try:
        if abs(time.time() - int(ts)) > 300:
            logger.warning("Slack request timestamp too old — possible replay attack")
            return False
    except ValueError:
        return False

    base = f"v0:{ts}:{raw_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ── Platform user lookup ──────────────────────────────────────────────────────

def _slack_user_email(slack_user_id: str) -> str | None:
    """Resolve a Slack user ID to their email address via the Users API."""
    try:
        info = _slack_client().users_info(user=slack_user_id)
        return info["user"]["profile"].get("email")
    except Exception as exc:
        logger.warning("Slack users.info failed for %s: %s", slack_user_id, exc)
        return None


def _platform_role(db: Session, email: str) -> str | None:
    """Look up the platform role for a user by email. Returns None if not found."""
    try:
        row = db.execute(
            text("SELECT role FROM principals WHERE email = :e LIMIT 1"),
            {"e": email},
        ).fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("Role lookup failed for %s: %s", email, exc)
        return None


def _can_query(role: str | None) -> bool:
    """Anyone with a platform account (any role, including viewer) can query the bot."""
    return role is not None


def _can_act(role: str | None) -> bool:
    """Only admin / itom_admin / operator roles can take approve/reject actions."""
    return role not in (None, "viewer")


# ── Message formatting ────────────────────────────────────────────────────────

def _to_mrkdwn(plain: str) -> str:
    """Light-touch conversion from LLM plain text to Slack mrkdwn."""
    # Bold INC numbers
    plain = re.sub(r"\b(INC-\d+)\b", r"*\1*", plain)
    # Convert markdown-style bullet lines to Slack bullets
    plain = re.sub(r"^[\-•]\s+", "• ", plain, flags=re.MULTILINE)
    return plain


def _action_blocks(response_text: str, action_spec: dict) -> list[dict]:
    """
    Build Slack Block Kit blocks: response text + Confirm / Cancel buttons.
    """
    is_approve = action_spec["type"] == "approve"
    verb       = "Approve" if is_approve else "Reject"
    style      = "primary" if is_approve else "danger"

    value = json.dumps({
        "workflow_id":     action_spec["workflow_id"],
        "action_type":     action_spec["type"],
        "incident_number": action_spec["incident_number"],
        "notes":           action_spec.get("notes", ""),
    })

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _to_mrkdwn(response_text)},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": f"✓ Confirm {verb}"},
                    "style":     style,
                    "action_id": "chatops_confirm",
                    "value":     value,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✗ Cancel"},
                    "action_id": "chatops_cancel",
                    "value":     "cancel",
                },
            ],
        },
    ]


# ── Background message processor ─────────────────────────────────────────────

async def _process_message(event: dict[str, Any], slack_user_id: str) -> None:
    """
    Handle a Slack message in the background (called via asyncio.create_task):
      1. Post a 'Thinking…' placeholder immediately
      2. Build context snapshot  (same logic as /api/chat)
      3. Call LLM (non-streaming)
      4. Update placeholder with response ± action buttons
    """
    # Imports are done here to avoid circular imports at module load time
    from agentic_os.db.database import SessionLocal
    from agentic_os.api.routes.chat import (
        _build_context,
        _detect_action_intent,
        _SYSTEM,
        ChatMessage,
        _extract_incident_numbers,
    )
    from agentic_os.services.summary_service import get_summary_service

    channel     = event.get("channel", "")
    is_dm       = event.get("channel_type", "") == "im"
    msg_ts      = event.get("ts", "")
    thread_ts   = event.get("thread_ts") or (None if is_dm else msg_ts)
    raw_text    = event.get("text", "")

    # Register this thread as active so future replies don't need @mention
    if thread_ts:
        register_bot_thread(thread_ts)

    # Strip the @bot mention so "approve INC-042" reaches the intent detector cleanly
    message = re.sub(r"<@[A-Z0-9]+>\s*", "", raw_text).strip()
    if not message:
        return

    # ── Thread history lookup ─────────────────────────────────────────────────
    # Use thread_ts for channel threads; channel ID for DMs (no thread_ts).
    thread_key    = thread_ts or channel
    history_dicts = _get_thread_history(thread_key)
    history_msgs  = [ChatMessage(role=h["role"], content=h["content"]) for h in history_dicts]

    client = _slack_client()

    # ── Post placeholder immediately (Slack 3-second timeout relief) ─────────
    try:
        resp     = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⏳ Thinking…",
        )
        msg_ts   = resp["ts"]
        msg_chan = resp["channel"]
    except Exception as exc:
        logger.error("Slack chat.postMessage failed: %s", exc)
        return

    db = SessionLocal()
    try:
        # ── Role / identity lookup ────────────────────────────────────────────
        email = _slack_user_email(slack_user_id)
        role  = _platform_role(db, email) if email else None

        # ── Access gate — platform account required ───────────────────────────
        if not email:
            client.chat_update(
                channel=msg_chan, ts=msg_ts,
                text=(
                    "⛔ I can't verify your identity. Your Slack email isn't visible to me — "
                    "check that your email is not hidden in your Slack profile, or contact your "
                    "ITOM Admin."
                ),
            )
            return

        if not _can_query(role):
            client.chat_update(
                channel=msg_chan, ts=msg_ts,
                text=(
                    f"⛔ *Access restricted.* _{email}_ does not have a platform account. "
                    "Only registered users can query the AI Ops Assistant. "
                    "Contact your ITOM Admin to request access."
                ),
            )
            return

        # role_note is appended to responses when the user can query but not act
        role_note = (
            "\n\n_⚠️ Your role is view-only — approval actions are disabled._"
            if role == "viewer" else ""
        )

        # ── LLM availability check ────────────────────────────────────────────
        provider = get_summary_service().provider
        if not provider.is_configured():
            client.chat_update(
                channel=msg_chan, ts=msg_ts,
                text="⚠️ LLM is not configured. Go to Settings → LLM to add an API key.",
            )
            return

        # ── Build context + detect action intent ──────────────────────────────

        # Resolve the INC in context.  Two sources, in priority order:
        #   1. Thread-associated INC stored in Redis when the notification was sent —
        #      available immediately with no race condition against history writes.
        #   2. Last INC found in conversation history — fallback for threads that
        #      weren't started by a platform notification.
        last_inc: str | None = _get_thread_inc(thread_ts) if thread_ts else None
        if not last_inc:
            for h in reversed(history_dicts):
                found = _extract_incident_numbers(h["content"])
                if found:
                    last_inc = found[0]
                    break

        # For action-intent detection: if the current message has an approve/reject
        # keyword but no INC number, inject the last-known INC so the detector can
        # resolve it (e.g. "I approve it" → "I approve it INC0017").
        _ACTION_WORDS = {"approve", "reject", "confirm", "decline"}
        current_incs = _extract_incident_numbers(message)

        # Always augment with last_inc for snapshot fetching when no INC is in the
        # current message — makes "who approved it?" / "was it successful?" pull
        # the full incident detail even without an explicit number.
        context_message = f"{message} {last_inc}" if (last_inc and not current_incs) else message

        # For action intent only inject INC when there's an explicit action keyword
        # to avoid false-positive approve/reject triggers on unrelated follow-ups.
        if last_inc and not current_incs and any(w in message.lower() for w in _ACTION_WORDS):
            action_message = context_message
        else:
            action_message = message

        snapshot    = _build_context(db, context_message, history=history_msgs)
        action_spec = _detect_action_intent(action_message, db)

        # Augment system prompt when an action is requested
        extra = ""
        if action_spec:
            verb   = "approving" if action_spec["type"] == "approve" else "rejecting"
            effect = (
                "remediation will begin immediately"
                if action_spec["type"] == "approve"
                else "the incident will be marked rejected — no automated remediation will run"
            )
            extra = (
                f"\n\nOPERATOR ACTION REQUEST:\n"
                f"  Action:   {action_spec['type'].upper()}\n"
                f"  Incident: {action_spec['incident_number']}\n\n"
                f"Confirm the action clearly. State that {verb} {action_spec['incident_number']} "
                f"means {effect}. Then ask the operator to confirm. "
                f"Do NOT perform any action yourself — only prepare the operator."
            )

        system_prompt = _SYSTEM.format(snapshot=snapshot) + extra

        # ── Build user_content with conversation history prefix ───────────────
        # This gives the LLM the same multi-turn context the web UI has.
        turns = "\n".join(
            f"{'Operator' if h['role'] == 'user' else 'Assistant'}: {h['content']}"
            for h in history_dicts[-MAX_SLACK_HISTORY:]
        )
        user_content = f"{turns}\nOperator: {message}" if turns else message

        # ── Call LLM (non-streaming — Slack doesn't support streaming) ────────
        response_text = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=1000,
            temperature=0.25,
        ) or "I couldn't generate a response. Please try again."

        response_text += role_note

        # ── Persist this exchange to thread history ───────────────────────────
        history_dicts.append({"role": "user",      "content": message})
        history_dicts.append({"role": "assistant",  "content": response_text})
        # Keep the last MAX_SLACK_HISTORY pairs (= MAX_SLACK_HISTORY × 2 messages)
        _set_thread_history(thread_key, history_dicts[-(MAX_SLACK_HISTORY * 2):])

        # ── Post response ± interactive buttons ───────────────────────────────
        if action_spec and _can_act(role):
            client.chat_update(
                channel=msg_chan,
                ts=msg_ts,
                text=response_text,             # plain-text fallback for notifications
                blocks=_action_blocks(response_text, action_spec),
            )
        else:
            client.chat_update(
                channel=msg_chan,
                ts=msg_ts,
                text=_to_mrkdwn(response_text),
            )

    except Exception as exc:
        logger.exception("Slack message processing error: %s", exc)
        try:
            client.chat_update(
                channel=msg_chan, ts=msg_ts,
                text="⚠️ An error occurred processing your request. Please try again.",
            )
        except Exception:
            pass
    finally:
        db.close()


# ── Events endpoint ───────────────────────────────────────────────────────────

@router.post("/webhooks/slack/events")
async def slack_events(request: Request):
    """
    Slack Events API handler.

    Responds with 200 immediately, then processes the message in the background
    (Slack requires a response within 3 seconds or it retries).

    Supported event types:
      app_mention  — @AIOpsAssistant <message> in any channel
      message      — direct message (im) to the bot
    """
    raw_body = await request.body()

    if not _verify_signature(request.headers, raw_body):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    payload = json.loads(raw_body)

    # ── URL verification (one-time, during app setup) ─────────────────────────
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    # ── Route events ──────────────────────────────────────────────────────────
    event        = payload.get("event", {})
    event_type   = event.get("type", "")
    channel_type = event.get("channel_type", "")
    bot_id       = event.get("bot_id")     # Ignore our own messages echoed back
    subtype      = event.get("subtype")    # Ignore message_changed, message_deleted, etc.

    is_mention = event_type == "app_mention"
    is_dm      = event_type == "message" and channel_type == "im"

    if (is_mention or is_dm) and not bot_id and not subtype:
        slack_user_id = event.get("user", "")
        asyncio.create_task(_process_message(event, slack_user_id))

    # Always return 200 immediately
    return {"ok": True}


# ── Shared action executor ────────────────────────────────────────────────────
# Called from both the HTTP endpoint (webhook mode) and the Socket Mode handler.

async def _execute_action_payload(payload: dict) -> None:
    """
    Process a Slack interactive payload (button click).

    Handles:
      chatops_confirm — write the approve/reject decision to the DB
      chatops_cancel  — dismiss the action bar

    Works identically whether the payload arrived via the HTTP actions endpoint
    (webhook mode) or the Socket Mode WebSocket (interactive envelope).
    """
    actions    = payload.get("actions", [])
    channel_id = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")
    slack_user = payload.get("user", {}).get("id", "")
    # Thread where the confirm button lives — used to store the approval result
    # in thread history so follow-up questions like "who approved it?" have context.
    button_thread_ts = (
        payload.get("container", {}).get("thread_ts")
        or payload.get("message",   {}).get("thread_ts")
        or channel_id   # DM fallback: channel IS the conversation
    )

    if not actions:
        return

    action    = actions[0]
    action_id = action.get("action_id", "")
    client    = _slack_client()

    # ── Cancel ────────────────────────────────────────────────────────────────
    if action_id == "chatops_cancel":
        try:
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text="Action cancelled.",
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "✗ Action cancelled."},
                }],
            )
        except Exception as exc:
            logger.warning("Failed to update cancelled Slack message: %s", exc)
        return

    # ── Confirm approve / reject ───────────────────────────────────────────────
    if action_id != "chatops_confirm":
        return

    value           = json.loads(action.get("value", "{}"))
    workflow_id     = value.get("workflow_id", "")
    action_type     = value.get("action_type", "")      # "approve" | "reject"
    incident_number = value.get("incident_number", "")
    notes           = value.get("notes", "")

    from agentic_os.db.database import SessionLocal
    from agentic_os.db.repositories import ApprovalRepository, WorkflowRepository
    from agentic_os.core.models import EventEnvelope, EventType, WorkflowType
    from agentic_os.bus.postgres_bus import PostgresEventBus
    from uuid import UUID as _UUID

    db = SessionLocal()
    try:
        # ── Role check ────────────────────────────────────────────────────────
        email = _slack_user_email(slack_user)
        role  = _platform_role(db, email) if email else None

        if not _can_act(role):
            denied_text = (
                "⛔ *Permission denied.*\n"
                "Your account does not have permission to approve or reject incidents from Slack. "
                "Contact your ITOM Admin, or use the Approvals queue in the web UI."
            )
            client.chat_update(
                channel=channel_id, ts=message_ts,
                text="Permission denied.",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": denied_text}}],
            )
            return

        # ── Look up the pending approval ──────────────────────────────────────
        decision   = "approved" if action_type == "approve" else "rejected"
        decided_by = email or f"slack:{slack_user}"
        auto_note  = notes or f"Decision via Slack ChatOps by {decided_by}"

        repo = ApprovalRepository(db)
        approval = (
            db.query(repo.model)
            .filter(
                repo.model.workflow_id == _UUID(workflow_id),
                repo.model.status == "pending",
            )
            .order_by(repo.model.requested_at.desc())
            .first()
        )

        if not approval:
            client.chat_update(
                channel=channel_id, ts=message_ts,
                text=(
                    f"⚠️ Could not find a pending approval for *{incident_number}*. "
                    f"It may have already been decided."
                ),
                blocks=[],
            )
            return

        # ── Write decision via repository (same path as the web UI) ──────────
        repo.decide(
            approval_id=approval.approval_id,
            decision=decision,
            decided_by=decided_by,
            decision_notes=auto_note,
        )

        # ── Publish event so the workflow engine learns about the decision ─────
        event_type = (
            EventType.APPROVAL_GRANTED if decision == "approved"
            else EventType.APPROVAL_REJECTED
        )
        event = EventEnvelope(
            workflow_id=approval.workflow_id,
            workflow_type=WorkflowType.INCIDENT,
            event_type=event_type,
            source_agent="slack_chatops",
            payload={
                "approval_id": str(approval.approval_id),
                "decision":    decision,
                "decided_by":  decided_by,
                "notes":       auto_note,
            },
        )
        try:
            event_bus = PostgresEventBus(
                "postgresql://postgres:agentic_os@postgres:5432/agentic_os"
            )
            await event_bus.publish(event)
        except Exception as pub_err:
            logger.warning("Slack ChatOps: failed to publish approval event: %s", pub_err)

        # ── Queue workflow resumption (approve) or update lifecycle (reject) ───
        if decision == "approved":
            try:
                from agentic_os.tasks.celery_app import resume_workflow_task
                wf_check = WorkflowRepository(db).get(str(approval.workflow_id))
                if wf_check and getattr(wf_check.lifecycle_state, "value",
                                        str(wf_check.lifecycle_state)) in ("resolved", "closed"):
                    logger.info(
                        "Slack ChatOps: workflow %s already resolved — approval noted, "
                        "remediation not queued", workflow_id
                    )
                else:
                    resume_workflow_task.delay(
                        workflow_id=str(approval.workflow_id),
                        approval_id=str(approval.approval_id),
                    )
            except Exception as resume_err:
                logger.warning("Slack ChatOps: failed to queue resumption task: %s", resume_err)
        else:
            try:
                WorkflowRepository(db).update_lifecycle_state(workflow_id, "rejected")
                db.commit()
            except Exception as state_err:
                logger.warning("Slack ChatOps: failed to set lifecycle to rejected: %s", state_err)

        # ── Reply to Slack ────────────────────────────────────────────────────
        icon   = "✅" if decision == "approved" else "❌"
        effect = (
            "Remediation has been queued and will begin shortly."
            if decision == "approved"
            else "Marked as rejected. No automated remediation will run."
        )
        result = f"{icon} *{incident_number}* {decision}.\n{effect}\n_Decided by {decided_by}_"

        client.chat_update(
            channel=channel_id, ts=message_ts,
            text=result,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": result}}],
        )

        # Store the approval outcome in thread history so the LLM can answer
        # follow-up questions like "who approved it?" or "was remediation started?"
        if button_thread_ts:
            hist = _get_thread_history(button_thread_ts)
            hist.append({"role": "assistant", "content": result})
            _set_thread_history(button_thread_ts, hist[-(MAX_SLACK_HISTORY * 2):])

    except Exception as exc:
        logger.exception("Slack action execution failed: %s", exc)
        try:
            client.chat_update(
                channel=channel_id, ts=message_ts,
                text=f"⚠️ Failed to {action_type} {incident_number}. Please retry from the Approvals queue.",
                blocks=[],
            )
        except Exception:
            pass
    finally:
        db.close()


# ── Interactive actions endpoint ──────────────────────────────────────────────

@router.post("/webhooks/slack/actions")
async def slack_actions(request: Request):
    """
    Slack Interactivity handler for webhook / Events API mode.

    In Socket Mode, interactive payloads arrive via the WebSocket and are handled
    by _execute_action_payload() called from start_socket_mode()._handle().
    This HTTP endpoint remains registered as a fallback for cloud deployments
    that use the Events API instead of Socket Mode.

    Button action IDs:
      chatops_confirm — execute the approve/reject decision
      chatops_cancel  — dismiss the action bar without executing
    """
    raw_body = await request.body()

    if not _verify_signature(request.headers, raw_body):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # Slack sends interactivity as form-encoded:  payload=<url-encoded JSON>
    form    = urllib.parse.parse_qs(raw_body.decode("utf-8"))
    payload = json.loads(form.get("payload", ["{}"])[0])

    await _execute_action_payload(payload)
    return {"ok": True}


# ── Socket Mode listener ─────────────────────────────────────────────────────

async def start_socket_mode() -> None:
    """
    Open an outbound WebSocket to Slack (Socket Mode) and listen for events.

    Called from FastAPI lifespan on startup.  Requires:
      slack.app_token   — xapp-... token  (Basic Information → App-Level Tokens,
                           scope: connections:write)
      slack.bot_token   — xoxb-... token  (already used for outbound notifications)

    No public URL or ngrok needed — the connection is outbound from this server.
    If either token is missing the function returns immediately (no-op).

    Socket Mode vs Events API
    ─────────────────────────
    Both use the same _process_message handler.  Socket Mode is preferred for
    local / self-hosted deployments because it does not require a public HTTPS
    endpoint.  The Events API webhook endpoints remain registered as a fallback
    for cloud-deployed instances that already have a public URL.
    """
    app_token = _get_slack_setting("slack.app_token", "SLACK_APP_TOKEN")
    bot_token = _get_slack_setting("slack.bot_token", "SLACK_BOT_TOKEN")

    if not app_token or not bot_token:
        logger.info(
            "[SlackSocket] Not starting — slack.app_token or slack.bot_token not configured. "
            "Add them in Settings → Slack ChatOps to enable inbound chat."
        )
        return

    # Validate token format before connecting — catches obviously wrong values
    # (e.g. empty strings that passed the None check, or placeholder values).
    if not app_token.startswith("xapp-"):
        logger.warning(
            "[SlackSocket] Not starting — slack.app_token does not look like a valid "
            "App-Level Token (expected prefix: xapp-). "
            "Go to api.slack.com → Your App → Basic Information → App-Level Tokens."
        )
        return
    if not bot_token.startswith("xoxb-"):
        logger.warning(
            "[SlackSocket] Not starting — slack.bot_token does not look like a valid "
            "Bot Token (expected prefix: xoxb-). "
            "Go to api.slack.com → Your App → OAuth & Permissions."
        )
        return

    # Quiet the slack_sdk internal logger — it logs at ERROR for auth failures
    # which are config issues, not application errors.
    import logging as _logging
    _logging.getLogger("slack_sdk.socket_mode.aiohttp").setLevel(_logging.CRITICAL)

    try:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient as _AsyncSMC  # type: ignore[import]
        from slack_sdk.web.async_client import AsyncWebClient as _AsyncWC         # type: ignore[import]
        from slack_sdk.socket_mode.request import SocketModeRequest               # type: ignore[import]
        from slack_sdk.socket_mode.response import SocketModeResponse             # type: ignore[import]
    except ImportError:
        logger.warning(
            "[SlackSocket] slack-sdk aiohttp transport not available. "
            "Install it with: pip install 'slack-sdk[optional]' aiohttp"
        )
        return

    # aiohttp transport calls `await self.web_client.apps_connections_open()` internally,
    # so we must pass AsyncWebClient (not the sync WebClient used elsewhere).
    sc = _AsyncSMC(app_token=app_token, web_client=_AsyncWC(token=bot_token))

    async def _handle(client: _AsyncSMC, req: SocketModeRequest) -> None:
        # Always acknowledge within 3 s — Slack retries if it doesn't get a response
        try:
            await client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
        except Exception as ack_exc:
            logger.warning("[SlackSocket] ack failed: %s", ack_exc)

        # Deduplicate — Slack delivers Socket Mode events to EVERY open connection.
        # With 2 uvicorn workers both connected, without this both would process the
        # same event and produce duplicate replies.
        if not _claim_event(req.envelope_id):
            return  # Another worker already claimed this event

        if req.type == "events_api":
            # ── Inbound chat messages (@mentions, DMs, and active threads) ────
            event        = req.payload.get("event", {})
            event_type   = event.get("type", "")
            channel_type = event.get("channel_type", "")
            bot_id       = event.get("bot_id")   # ignore our own echoed messages
            subtype      = event.get("subtype")  # ignore message_changed / deleted
            thread_ts    = event.get("thread_ts")
            msg_ts       = event.get("ts", "")

            is_mention = event_type == "app_mention"
            is_dm      = event_type == "message" and channel_type == "im"
            # Thread reply in a channel where the bot already participated —
            # no @mention needed.  Requires message.channels subscription.
            is_thread_reply = (
                event_type == "message"
                and thread_ts
                and thread_ts != msg_ts          # it's a reply, not the root
                and _is_bot_thread(thread_ts)
            )

            if (is_mention or is_dm or is_thread_reply) and not bot_id and not subtype:
                asyncio.create_task(_process_message(event, event.get("user", "")))

        elif req.type == "interactive":
            # ── Button clicks (Confirm Approve / Cancel) ───────────────────────
            # In Socket Mode, interactive payloads arrive here instead of the
            # HTTP /webhooks/slack/actions endpoint.
            asyncio.create_task(_execute_action_payload(req.payload))

    sc.socket_mode_request_listeners.append(_handle)

    try:
        print("[SlackSocket] Connecting to Slack via Socket Mode…", flush=True)
        # connect() establishes the WebSocket and spawns background receiver/monitor tasks,
        # then returns immediately.  We keep this coroutine alive via sleep so those background
        # tasks continue running until the FastAPI lifespan cancels us on shutdown.
        await sc.connect()
        print("[SlackSocket] ✓ Connected — listening for @mentions and DMs (no public URL needed)", flush=True)
        await asyncio.sleep(float("inf"))   # hold the task open; background tasks do the work
    except asyncio.CancelledError:
        print("[SlackSocket] Shutting down", flush=True)
    except Exception as exc:
        exc_str = str(exc).lower()
        if "invalid_auth" in exc_str or "not_authed" in exc_str or "account_inactive" in exc_str:
            # Auth failure = config issue, not an application error — warn, don't error
            logger.warning(
                "[SlackSocket] Slack auth failed (%s). "
                "Update your tokens in Settings → Slack ChatOps. "
                "Platform continues without Slack integration.", exc
            )
        else:
            logger.error("[SlackSocket] Fatal error: %s", exc, exc_info=True)
        print(f"[SlackSocket] Disconnected: {exc}", flush=True)
    finally:
        try:
            await sc.close()    # cleanly shuts down session + background tasks
        except Exception:
            pass
        print("[SlackSocket] Disconnected", flush=True)


# ── Health / config check ─────────────────────────────────────────────────────

@router.get("/webhooks/slack/status")
async def slack_status():
    """Quick check — returns whether Slack credentials are present (DB or env)."""
    token  = _get_slack_setting("slack.bot_token", "SLACK_BOT_TOKEN")
    secret = _get_slack_setting("slack.signing_secret", "SLACK_SIGNING_SECRET")
    return {
        "configured": bool(token and secret),
        "token_set":  bool(token),
        "secret_set": bool(secret),
    }


class SlackTestCredentials(BaseModel):
    bot_token: str
    channel: Optional[str] = None


@router.post("/webhooks/slack/test-credentials")
async def test_slack_credentials(creds: SlackTestCredentials):
    """
    Test Slack credentials WITHOUT saving them.
    Supports the Test-before-Save UX: validate the bot token is valid
    and can post to a channel before committing to the database.
    """
    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=creds.bot_token)

        # auth.test verifies the token is valid
        auth = await client.auth_test()
        if not auth["ok"]:
            raise HTTPException(status_code=400, detail=f"Slack auth failed: {auth.get('error', 'unknown')}")

        bot_name = auth.get("bot_id") or auth.get("user", "bot")
        team     = auth.get("team", "unknown workspace")

        # Optionally send a test message
        channel = creds.channel or _get_slack_setting("slack.default_channel", "SLACK_DEFAULT_CHANNEL") or "#general"
        try:
            await client.chat_postMessage(
                channel=channel,
                text=f"✅ Agentic Platform Slack test successful — bot `{bot_name}` connected to *{team}*.",
            )
            msg = f"Token valid and test message sent to {channel}"
        except Exception:
            msg = f"Token valid (auth.test passed) — could not post to {channel}"

        return {"status": "success", "message": msg, "team": team, "bot": bot_name}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Slack test failed: {str(e)}")
