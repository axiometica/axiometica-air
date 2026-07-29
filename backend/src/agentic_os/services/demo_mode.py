"""
Demo-mode support.

Ships in every install but is inert unless BOTH:
  1. DEMO_MODE=true is set in the environment (controls provisioning), AND
  2. A principal with role='demo' actually exists in the DB.

Non-demo installs get identical behaviour to before this module existed:
  - No demo principal auto-created (guarded by DEMO_MODE env)
  - Middleware early-exits for every request whose principal isn't role='demo'
  - LLM cap wrapper is a no-op for every non-demo principal
  - Model override is a no-op for every non-demo principal

The demo principal itself is a normal DB row — its role is just another
string value, so removing DEMO_MODE from .env and deleting the demo user
returns the install to fully vanilla behaviour with no residue.

WHAT THE DEMO ROLE CAN DO
  - GET everything gated as _any or _itom_up (read-only browse)
  - POST /api/auth/login and /api/auth/logout (must be able to sign in/out)
  - POST /api/chat/... (capped at DEMO_CHAT_MESSAGES_PER_DAY)
  - Nothing else — every other write returns 403

WHAT IT CAN'T DO
  - Anything gated as _admin (returns 403 via existing require_role)
  - Any write outside the allow-list above (returns 403 via middleware)
  - Trigger LLM spend beyond DEMO_LLM_DAILY_LIMIT_USD (returns 429)
  - Trigger LLM spend beyond DEMO_LLM_SESSION_LIMIT_USD in one JWT lifetime

TUNING
  All limits and the demo credentials are constants at the top of this file.
  Changing them requires a backend restart. There is deliberately no UI to
  edit these — they aren't operator concerns, they're operator-of-demo
  concerns.
"""
from __future__ import annotations

import hashlib
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from jose import JWTError, jwt as _jwt

# Per-request context — set by demo_access_middleware for demo principals,
# read by the LLM provider layer to know whether to enforce caps / override
# the model. None for every non-demo request (the middleware early-returns
# without setting it). ContextVar isolates values per asyncio task so
# concurrent requests never see each other's demo state.
demo_request_ctx: ContextVar[Optional[dict]] = ContextVar("demo_request_ctx", default=None)

logger = logging.getLogger(__name__)

# ── Feature flag ─────────────────────────────────────────────────────────────
# When false (the default), this module is entirely inert — the startup
# provisioning task no-ops, and every helper below returns early for any
# principal whose role isn't 'demo' (which won't exist anyway). Non-demo
# installs pay only the cost of one env-var lookup on import and one
# `role != DEMO_ROLE` check per request.
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")

DEMO_ROLE: str = "demo"

# ── Demo credentials ─────────────────────────────────────────────────────────
# Published on the login page — reset to these values on every startup so a
# malicious visitor changing the password can't lock out subsequent demo
# visitors. Anyone with these creds gets a valid session; blast radius is
# capped by the middleware + LLM caps below.
DEMO_EMAIL: str = os.getenv("DEMO_EMAIL", "demo@axiometica.com")
DEMO_PASSWORD: str = os.getenv("DEMO_PASSWORD", "Demo@1234!")
DEMO_NAME: str = "Demo Visitor"

# ── Access control ───────────────────────────────────────────────────────────
# Writes are blocked for demo role EXCEPT to these path prefixes. Login/logout
# have to work; chat is the deliberately-allowed exception (capped separately).
# Anything not on this list gets a 403 with a demo-explanation message.
DEMO_WRITE_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/api/auth/login",
    "/api/auth/logout",
    "/api/chat",
    "/api/demo",
)

# Reads are permissive by default (demo needs to see the platform). If we ever
# want to hide specific read endpoints from demo (e.g. principals list, admin
# logs, LLM credentials), add prefixes here — demo GETs matching these get 403.
DEMO_READ_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/api/admin",              # backup/restore, system logs, danger zone
    "/api/admin-logs",         # audit trail
    "/api/auth/principals",    # user management — hides real user emails
    "/api/auth/api-keys",      # API keys — obvious secrets
    "/api/llm-settings",       # LLM API keys — obvious secrets
)

# ── LLM caps ─────────────────────────────────────────────────────────────────
# Per-principal daily budget. Resets at UTC midnight. Redis holds the live
# counter for fast increments; the llm_usage table holds durable audit rows
# so an operator can see who did what.
DEMO_LLM_DAILY_LIMIT_USD: float = float(os.getenv("DEMO_LLM_DAILY_LIMIT_USD", "1.0"))
DEMO_LLM_SESSION_LIMIT_USD: float = float(os.getenv("DEMO_LLM_SESSION_LIMIT_USD", "0.30"))
DEMO_CHAT_MESSAGES_PER_DAY: int = int(os.getenv("DEMO_CHAT_MESSAGES_PER_DAY", "20"))

# ── Model override ───────────────────────────────────────────────────────────
# Force demo requests onto the cheapest model regardless of the platform's
# configured default. Overrides are per-provider; unknown providers fall
# through to their configured model unchanged (no override).
_DEMO_MODEL_OVERRIDES = {
    "anthropic": "claude-3-haiku-20240307",
    "openai":    "gpt-4o-mini",
}

# ── Cost model ───────────────────────────────────────────────────────────────
# USD per 1M tokens. Sourced from provider pricing pages as of the module
# version. Overestimating is fine (users hit the cap sooner); underestimating
# is worse (real spend exceeds the cap). Update opportunistically when
# providers change pricing — an outdated table won't crash, just misprice.
_MODEL_COSTS_PER_1M = {
    # Anthropic
    "claude-3-haiku-20240307":      {"input": 0.25,  "output": 1.25},
    "claude-3-5-haiku-20241022":    {"input": 0.80,  "output": 4.00},
    "claude-3-5-sonnet-20241022":   {"input": 3.00,  "output": 15.00},
    "claude-3-opus-20240229":       {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-4o-mini":                  {"input": 0.15,  "output": 0.60},
    "gpt-4o":                       {"input": 2.50,  "output": 10.00},
    "gpt-4-turbo":                  {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo":                {"input": 0.50,  "output": 1.50},
}

# Fallback cost when the model isn't in the table — deliberately expensive so
# unknown models can't accidentally get unlimited spend under the cap.
_UNKNOWN_MODEL_COST = {"input": 5.00, "output": 20.00}


# ── Redis client ─────────────────────────────────────────────────────────────
# Fail-open (no Redis → no enforcement). Same pattern as the JWT blocklist.
def _get_redis():
    try:
        import redis as _redis
        client = _redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            socket_connect_timeout=1, socket_timeout=1,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.warning("[demo] Redis unavailable — LLM caps fail-open: %s", exc)
        return None


# ── Cost estimation ──────────────────────────────────────────────────────────

def estimate_call_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost of a single LLM call at the model's published prices.
    Unknown models fall back to the conservative _UNKNOWN_MODEL_COST."""
    prices = _MODEL_COSTS_PER_1M.get(model, _UNKNOWN_MODEL_COST)
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


# ── Cap enforcement ──────────────────────────────────────────────────────────
# Both counters live in Redis:
#   demo:llm:daily:<principal_id>:<YYYY-MM-DD>   → cost in millicents (int)
#   demo:llm:session:<jti>                       → cost in millicents (int)
#   demo:llm:chat:<principal_id>:<YYYY-MM-DD>    → chat message count
#
# Storing millicents (integer) rather than USD (float) avoids Redis's floating-
# point rounding drift over thousands of increments.

def _mc(usd: float) -> int:
    """Convert USD to integer millicents (1 USD = 100_000 millicents)."""
    return int(round(usd * 100_000))


def _from_mc(mc: int) -> float:
    return mc / 100_000


def _today_key() -> str:
    return date.today().isoformat()


def _seconds_until_utc_midnight() -> int:
    now = datetime.utcnow()
    tomorrow = datetime(now.year, now.month, now.day) + \
               (datetime(now.year, now.month, now.day + 1) - datetime(now.year, now.month, now.day)) \
               if False else None
    # Simpler:
    from datetime import timedelta
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((tomorrow_start - now).total_seconds()), 60)


@dataclass
class CapCheckResult:
    ok: bool
    reason: str = ""            # populated when ok=False, human-readable
    daily_used_usd: float = 0.0
    daily_limit_usd: float = DEMO_LLM_DAILY_LIMIT_USD
    session_used_usd: float = 0.0
    session_limit_usd: float = DEMO_LLM_SESSION_LIMIT_USD


def check_llm_cap(principal_id: str, jti: Optional[str], expected_cost_usd: float) -> CapCheckResult:
    """Return whether a demo principal can afford one more LLM call.
    Non-demo callers should never reach this — guard with is_demo(principal)
    at the call site. Fail-open when Redis is unavailable."""
    client = _get_redis()
    if client is None:
        return CapCheckResult(ok=True)

    try:
        daily_key   = f"demo:llm:daily:{principal_id}:{_today_key()}"
        session_key = f"demo:llm:session:{jti}" if jti else None

        daily_used_mc   = int(client.get(daily_key) or 0)
        session_used_mc = int(client.get(session_key) or 0) if session_key else 0
        cost_mc         = _mc(expected_cost_usd)

        if daily_used_mc + cost_mc > _mc(DEMO_LLM_DAILY_LIMIT_USD):
            return CapCheckResult(
                ok=False,
                reason=f"Demo LLM daily budget exhausted (${_from_mc(daily_used_mc):.3f} of "
                       f"${DEMO_LLM_DAILY_LIMIT_USD:.2f} used). Resets at UTC midnight.",
                daily_used_usd=_from_mc(daily_used_mc),
                session_used_usd=_from_mc(session_used_mc),
            )
        if session_key and session_used_mc + cost_mc > _mc(DEMO_LLM_SESSION_LIMIT_USD):
            return CapCheckResult(
                ok=False,
                reason=f"Demo session LLM budget exhausted (${_from_mc(session_used_mc):.3f} of "
                       f"${DEMO_LLM_SESSION_LIMIT_USD:.2f} per session). Log out and back in to reset.",
                daily_used_usd=_from_mc(daily_used_mc),
                session_used_usd=_from_mc(session_used_mc),
            )
        return CapCheckResult(
            ok=True,
            daily_used_usd=_from_mc(daily_used_mc),
            session_used_usd=_from_mc(session_used_mc),
        )
    except Exception as exc:
        logger.warning("[demo] LLM cap check failed (fail-open): %s", exc)
        return CapCheckResult(ok=True)


def record_llm_usage(principal_id: str, jti: Optional[str],
                     model: str, input_tokens: int, output_tokens: int) -> float:
    """Record actual usage after an LLM call completes. Returns USD cost.
    Also writes a durable audit row to llm_usage_daily via the caller —
    this function only updates the fast Redis counters."""
    cost_usd = estimate_call_cost_usd(model, input_tokens, output_tokens)
    client   = _get_redis()
    if client is None:
        return cost_usd

    try:
        cost_mc      = _mc(cost_usd)
        daily_key    = f"demo:llm:daily:{principal_id}:{_today_key()}"
        session_key  = f"demo:llm:session:{jti}" if jti else None
        ttl          = _seconds_until_utc_midnight()

        pipe = client.pipeline()
        pipe.incrby(daily_key, cost_mc)
        pipe.expire(daily_key, ttl)
        if session_key:
            pipe.incrby(session_key, cost_mc)
            # Session key TTL matches the JWT expiry — a day is plenty (JWT is 8h).
            pipe.expire(session_key, 24 * 3600)
        pipe.execute()
    except Exception as exc:
        logger.warning("[demo] Failed to record LLM usage (charge lost): %s", exc)

    return cost_usd


def check_chat_quota(principal_id: str) -> tuple[bool, int, int]:
    """Return (allowed, used_today, limit) for the demo chat message quota."""
    client = _get_redis()
    if client is None:
        return True, 0, DEMO_CHAT_MESSAGES_PER_DAY
    try:
        key  = f"demo:llm:chat:{principal_id}:{_today_key()}"
        used = int(client.get(key) or 0)
        return used < DEMO_CHAT_MESSAGES_PER_DAY, used, DEMO_CHAT_MESSAGES_PER_DAY
    except Exception:
        return True, 0, DEMO_CHAT_MESSAGES_PER_DAY


def increment_chat_count(principal_id: str) -> None:
    client = _get_redis()
    if client is None:
        return
    try:
        key = f"demo:llm:chat:{principal_id}:{_today_key()}"
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _seconds_until_utc_midnight())
        pipe.execute()
    except Exception:
        pass


# ── Model override ───────────────────────────────────────────────────────────

def override_model_for_demo(provider_type: str, current_model: str) -> str:
    """If we know a cheaper model for this provider, use it for demo calls.
    provider_type is 'anthropic' or 'openai' (lowercase); unknown providers
    keep their configured model. Non-demo call sites don't invoke this."""
    return _DEMO_MODEL_OVERRIDES.get(provider_type.lower(), current_model)


# ── Middleware ───────────────────────────────────────────────────────────────

def is_demo_principal_from_request(request) -> tuple[bool, Optional[str], Optional[str]]:
    """Peek at the Authorization header without triggering FastAPI's dependency
    injection. Returns (is_demo, principal_id, jti). All-None on any failure
    — a request with no/invalid auth just isn't a demo request as far as
    this middleware is concerned; the endpoint's own auth dep will reject it
    normally if auth is required.

    Same JWT decode logic as api/auth.py but tolerant — we're peeking, not
    authenticating."""
    from agentic_os.api.auth import JWT_SECRET, JWT_ALGORITHM
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return False, None, None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return False, None, None
    return (
        payload.get("role") == DEMO_ROLE,
        payload.get("sub"),
        payload.get("jti"),
    )


async def demo_access_middleware(request, call_next):
    """Enforce demo access rules. No-op for every non-demo request.
    Runs BEFORE FastAPI's route dependencies fire, so a 403 here short-
    circuits the whole request pipeline (no DB query, no LLM call).

    Also stashes principal_id/jti in demo_request_ctx so the LLM provider
    layer can cross-reference for cap enforcement and model override
    without needing to re-parse the JWT."""
    is_demo, principal_id, jti = is_demo_principal_from_request(request)
    if not is_demo:
        return await call_next(request)

    # Publish demo state for LLM provider hooks. Reset when the request ends
    # so the token doesn't leak across coroutines (ContextVar handles this
    # per-task, but explicit reset avoids surprises in shared executors).
    token = demo_request_ctx.set({"principal_id": principal_id, "jti": jti})
    try:
        return await _demo_dispatch(request, call_next)
    finally:
        demo_request_ctx.reset(token)


async def _demo_dispatch(request, call_next):
    """Actual access-control logic — split out so the middleware wrapper
    can own the contextvar lifecycle."""

    path   = request.url.path
    method = request.method.upper()

    # Safe-by-definition methods bypass write enforcement — GET/HEAD read
    # state without changing it, OPTIONS is CORS preflight which browsers
    # emit before any real request.
    if method in ("GET", "HEAD", "OPTIONS"):
        if method == "GET" and any(path.startswith(p) for p in DEMO_READ_BLOCKED_PREFIXES):
            from starlette.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={"detail": "Not available in demo mode. This section exposes "
                                   "credentials or user management and is restricted to real accounts."},
            )
        return await call_next(request)

    # Writes: allowlist-based (restrictive; only login/logout/chat permitted).
    if not any(path.startswith(p) for p in DEMO_WRITE_ALLOWED_PREFIXES):
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=403,
            content={"detail": "Demo account is read-only. Sign in with a real account "
                               "to make changes. Chat is available in demo mode."},
        )

    return await call_next(request)


# ── Startup provisioning ─────────────────────────────────────────────────────

def _password_hash(password: str) -> str:
    """Match the auth routes' bcrypt hash. Local import so passlib isn't
    pulled at module import time on installs that never enable demo mode."""
    from agentic_os.api.routes.auth import pwd_ctx
    return pwd_ctx.hash(password)


def ensure_demo_principal() -> None:
    """Idempotent: creates the demo principal if missing, or resets its
    password + role + enabled flag on every startup so a malicious visitor
    changing any of those can't lock out subsequent demo users. Safe to call
    on every startup regardless of DEMO_MODE — early-exits when the flag is off."""
    if not DEMO_MODE:
        return
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import PrincipalModel
    except Exception as exc:
        logger.error("[demo] Could not import DB models — provisioning skipped: %s", exc)
        return

    db = SessionLocal()
    try:
        existing = db.query(PrincipalModel).filter_by(email=DEMO_EMAIL).first()
        password_h = _password_hash(DEMO_PASSWORD)
        if existing is None:
            db.add(PrincipalModel(
                name=DEMO_NAME,
                email=DEMO_EMAIL,
                role=DEMO_ROLE,
                enabled=True,
                hashed_pw=password_h,
                created_at=datetime.utcnow(),
            ))
            db.commit()
            logger.info("[demo] Provisioned demo principal %s", DEMO_EMAIL)
        else:
            existing.role = DEMO_ROLE
            existing.enabled = True
            existing.hashed_pw = password_h
            db.commit()
            logger.info("[demo] Reset demo principal %s (role/enabled/password)", DEMO_EMAIL)
    except Exception as exc:
        logger.error("[demo] Failed to provision demo principal: %s", exc)
        db.rollback()
    finally:
        db.close()


# ── LLM provider integration ─────────────────────────────────────────────────
# The provider layer never sees the Principal object directly (it's a service,
# not a request handler). These helpers read the contextvar set by the demo
# middleware, so every call site looks the same regardless of whether it's a
# demo request or not — inert when the contextvar is empty.

def current_demo_ctx() -> Optional[dict]:
    """Return {'principal_id': str, 'jti': str} for the in-flight demo request,
    or None for every non-demo call site (which is 100% of traffic on non-demo
    installs)."""
    return demo_request_ctx.get()


def maybe_override_model(provider_type: str, current_model: str) -> str:
    """Called by each LLM provider before dispatching a completion. Returns
    the demo-preferred model when the current request is from a demo
    principal; otherwise returns current_model unchanged. Zero-cost no-op
    for non-demo requests."""
    if current_demo_ctx() is None:
        return current_model
    return override_model_for_demo(provider_type, current_model)


def maybe_check_cap(estimated_cost_usd: float = 0.01) -> CapCheckResult:
    """Pre-call cap check. Returns an ok=True result for non-demo requests
    (they're never capped). For demo, checks the daily + session budget.
    Callers should raise an HTTP-friendly exception when ok=False."""
    ctx = current_demo_ctx()
    if ctx is None:
        return CapCheckResult(ok=True)
    return check_llm_cap(ctx["principal_id"], ctx.get("jti"), estimated_cost_usd)


def maybe_record_usage(model: str, input_tokens: int, output_tokens: int) -> float:
    """Post-call usage recording. No-op for non-demo requests. Returns the
    computed USD cost (0.0 for non-demo)."""
    ctx = current_demo_ctx()
    if ctx is None:
        return 0.0
    cost = record_llm_usage(
        principal_id=ctx["principal_id"],
        jti=ctx.get("jti"),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    # Also write a durable audit row so operators can see spend history
    # even after the Redis daily counter has rolled over.
    try:
        _record_usage_row(ctx["principal_id"], model, input_tokens, output_tokens, cost)
    except Exception as exc:
        logger.warning("[demo] Failed to write llm_usage audit row: %s", exc)
    return cost


def _record_usage_row(principal_id: str, model: str,
                      input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    """Upsert one row per (principal_id, date) — cumulative counters. Rolls
    call_count / tokens / cost_usd into the row for the day. Called at most
    a few times per second per demo user; low contention risk."""
    from sqlalchemy import text
    from agentic_os.db.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO llm_usage (principal_id, usage_date, model, input_tokens,
                                       output_tokens, cost_usd, call_count)
                VALUES (:pid, :d, :model, :itok, :otok, :cost, 1)
                ON CONFLICT (principal_id, usage_date, model) DO UPDATE
                  SET input_tokens  = llm_usage.input_tokens  + EXCLUDED.input_tokens,
                      output_tokens = llm_usage.output_tokens + EXCLUDED.output_tokens,
                      cost_usd      = llm_usage.cost_usd      + EXCLUDED.cost_usd,
                      call_count    = llm_usage.call_count    + 1
            """),
            {"pid": principal_id, "d": date.today().isoformat(), "model": model,
             "itok": input_tokens, "otok": output_tokens, "cost": cost_usd},
        )
        db.commit()
    finally:
        db.close()


# ── Convenience predicate for use in call sites ──────────────────────────────

def is_demo(principal) -> bool:
    """True if the given Principal is the demo user. Cheap; safe to call
    from hot paths. Handles None principal (e.g. unauthenticated requests
    that somehow reach a demo-aware function) by returning False."""
    return principal is not None and getattr(principal, "role", None) == DEMO_ROLE
