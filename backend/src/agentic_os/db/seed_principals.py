"""
Idempotent seeder for the principals table.

Creates 4 default accounts on first startup if they do not already exist:
  - Mike Behar      (admin)      — email: admin@platform.local
  - Operator        (operator)   — email: operator@platform.local
  - Viewer          (viewer)     — email: viewer@platform.local
  - Watcher Bot     (automation) — no email; authenticated via API key

Credentials are read from environment variables with safe defaults.
Run this on every startup — it is a no-op when accounts already exist.
"""
import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from agentic_os.db.models import PrincipalModel

logger = logging.getLogger(__name__)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def seed_principals(db: Session) -> None:
    """Create default principals if they don't already exist."""

    # ── Human accounts ──────────────────────────────────────────────────────
    human_accounts = [
        {
            "name":     "Administrator",
            "email":    os.getenv("ADMIN_EMAIL", "admin@platform.local"),
            "role":     "admin",
            # Default meets the platform password policy (12+ chars, upper/lower/digit/special).
            # Override via ADMIN_INITIAL_PASSWORD env var — change it immediately after first login.
            "password": os.getenv("ADMIN_INITIAL_PASSWORD", "Admin@1234!"),
        },
        {
            "name":     "ITOM Admin",
            "email":    "itomadmin@platform.local",
            "role":     "itom_admin",
            "password": os.getenv("ITOM_ADMIN_INITIAL_PASSWORD", "ITOMAdmin@1234!"),
        },
        {
            "name":     "Operator",
            "email":    "operator@platform.local",
            "role":     "operator",
            "password": os.getenv("OPERATOR_INITIAL_PASSWORD", "Operator@1234!"),
        },
        {
            "name":     "Viewer",
            "email":    "viewer@platform.local",
            "role":     "viewer",
            "password": os.getenv("VIEWER_INITIAL_PASSWORD", "Viewer@1234!"),
        },
    ]

    for acct in human_accounts:
        existing = db.query(PrincipalModel).filter(
            PrincipalModel.email == acct["email"]
        ).first()
        if existing:
            logger.debug("Principal %s already exists — skipping", acct["email"])
            continue

        row = PrincipalModel(
            id=uuid.uuid4(),
            name=acct["name"],
            email=acct["email"],
            role=acct["role"],
            hashed_pw=pwd_ctx.hash(acct["password"]),
            enabled=True,
            created_at=datetime.utcnow(),
        )
        db.add(row)
        logger.info("Seeded principal: %s (%s)", acct["email"], acct["role"])

    # ── Automation account — Watcher Bot ────────────────────────────────────
    watcher_existing = db.query(PrincipalModel).filter(
        PrincipalModel.name == "Watcher Bot",
        PrincipalModel.role == "automation",
    ).first()

    if not watcher_existing:
        raw_key = os.getenv("WATCHER_API_KEY", "")
        if not raw_key:
            raw_key = secrets.token_urlsafe(32)
            print(
                f"\n*** WATCHER API KEY (capture this — shown once) ***\n"
                f"    {raw_key}\n"
                f"    Set WATCHER_API_KEY env var to avoid regeneration on restart.\n"
            )

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        prefix   = "ak_" + raw_key[:8]

        watcher_row = PrincipalModel(
            id=uuid.uuid4(),
            name="Watcher Bot",
            email=None,
            role="automation",
            hashed_pw=None,
            api_key_hash=key_hash,
            api_key_prefix=prefix,
            enabled=True,
            created_at=datetime.utcnow(),
        )
        db.add(watcher_row)
        logger.info("Seeded automation principal: Watcher Bot (prefix: %s)", prefix)
    else:
        logger.debug("Watcher Bot principal already exists — skipping")

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Principal seed commit failed (possible race condition): %s", e)
