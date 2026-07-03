"""
Authentication and principal management routes.

POST /api/auth/login                              — email+password → JWT
GET  /api/auth/me                                 — current principal info
POST /api/auth/logout                             — server-side no-op; client drops token
POST /api/auth/change-password                    — self-service password change
GET  /api/auth/principals                         — list all principals  [admin]
POST /api/auth/principals                         — create user or automation account  [admin]
PUT  /api/auth/principals/{id}                    — update name/role/enabled  [admin]
DELETE /api/auth/principals/{id}                  — soft-disable  [admin]
POST /api/auth/principals/{id}/reset-password     — admin resets any user's password  [admin]
POST /api/auth/principals/{id}/api-key            — generate API key  [admin]
DELETE /api/auth/principals/{id}/api-key          — revoke API key  [admin]
GET  /api/auth/audit-log                          — recent audit events  [admin]
"""
import hashlib
import logging
import secrets
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

import re

from agentic_os.api.rate_limit import RateLimit

# 10 login attempts per minute per IP — brute-force protection
_login_rate_limit         = RateLimit(times=10,  seconds=60)
# 5 password-change attempts per 15 minutes per IP
_change_pw_rate_limit     = RateLimit(times=5,   seconds=900)
# 10 password-reset attempts per minute (admin-only route, still worth limiting)
_reset_pw_rate_limit      = RateLimit(times=10,  seconds=60)

from agentic_os.api.auth import (
    Principal,
    blocklist_token,
    create_access_token,
    get_current_principal,
    require_role,
    JWT_EXPIRY_HOURS,
    JWT_SECRET,
    JWT_ALGORITHM,
)
from agentic_os.db.database import get_session
from agentic_os.db.models import PrincipalModel, PrincipalAuditLogModel

logger = logging.getLogger(__name__)
router = APIRouter()

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Password policy ──────────────────────────────────────────────────────────
# NIST SP 800-63B + enterprise baseline:
#   • Minimum 12 characters
#   • At least one uppercase, one lowercase, one digit, one special character
# Applied at account creation, self-service change, and admin reset.
MIN_PASSWORD_LENGTH = 12
_SPECIAL = r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>/?\\|`~]'


def _validate_password(password: str) -> None:
    """Raise HTTP 422 if the password doesn't meet the platform policy."""
    errors = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r'[A-Z]', password):
        errors.append("at least one uppercase letter (A-Z)")
    if not re.search(r'[a-z]', password):
        errors.append("at least one lowercase letter (a-z)")
    if not re.search(r'[0-9]', password):
        errors.append("at least one digit (0-9)")
    if not re.search(_SPECIAL, password):
        errors.append("at least one special character (!@#$%^&*…)")
    if errors:
        raise HTTPException(
            status_code=422,
            detail="Password does not meet policy requirements: " + "; ".join(errors) + ".",
        )


# ── Audit helper ─────────────────────────────────────────────────────────────

def _audit(
    db: Session,
    actor: Optional[Principal],
    action: str,
    target: Optional[PrincipalModel] = None,
    detail: str = "",
) -> None:
    """Write an immutable audit entry; silently swallows failures."""
    try:
        entry = PrincipalAuditLogModel(
            actor_id=uuid.UUID(actor.id) if actor else None,
            actor_name=actor.name if actor else None,
            action=action,
            target_id=target.id if target else None,
            target_name=target.name if target else None,
            detail=detail or None,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


# ── Pydantic models ──────────────────────────────────────────────────────────

class PrincipalResponse(BaseModel):
    id: str
    name: str
    email: Optional[str]
    role: str
    enabled: bool
    created_at: str
    last_seen_at: Optional[str] = None
    api_key_prefix: Optional[str] = None  # shown for automation accounts

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int   # seconds
    principal: PrincipalResponse


class CreatePrincipalRequest(BaseModel):
    name: str
    email: Optional[str] = None
    role: str             # admin | operator | viewer | automation
    password: Optional[str] = None   # required for human accounts


class UpdatePrincipalRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    enabled: Optional[bool] = None


class CreateApiKeyResponse(BaseModel):
    api_key: str          # raw key — show ONCE only
    api_key_prefix: str   # prefix for display
    principal: PrincipalResponse


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class AuditLogEntry(BaseModel):
    id: int
    ts: str
    actor_name: Optional[str]
    action: str
    target_name: Optional[str]
    detail: Optional[str]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_response(p: PrincipalModel) -> PrincipalResponse:
    return PrincipalResponse(
        id=str(p.id),
        name=p.name,
        email=p.email,
        role=p.role,
        enabled=p.enabled,
        created_at=p.created_at.isoformat() if p.created_at else "",
        last_seen_at=p.last_seen_at.isoformat() if p.last_seen_at else None,
        api_key_prefix=p.api_key_prefix,
    )


def _require_admin():
    return Depends(require_role("admin"))


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/auth/login", response_model=LoginResponse)
def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_session),
    _rl: None = Depends(_login_rate_limit),      # Fix 7 — 10 attempts/min per IP
):
    """Email + password → JWT."""
    principal = db.query(PrincipalModel).filter(
        PrincipalModel.email == body.email,
        PrincipalModel.enabled == True,
    ).first()

    if not principal or not principal.hashed_pw:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not pwd_ctx.verify(body.password, principal.hashed_pw):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Update last_seen
    try:
        principal.last_seen_at = datetime.utcnow()
        db.commit()
    except Exception:
        pass

    # Resolve session timeout from general settings (falls back to JWT_EXPIRY_HOURS)
    _expiry_hours: Optional[float] = None
    try:
        from agentic_os.db.models import PlatformSettingModel as _PSM
        _timeout_row = db.get(_PSM, "general.session_timeout_minutes")
        if _timeout_row and _timeout_row.value:
            _expiry_hours = int(_timeout_row.value) / 60.0
    except Exception:
        pass

    token = create_access_token(
        principal_id=str(principal.id),
        name=principal.name,
        email=principal.email,
        role=principal.role,
        expiry_hours=_expiry_hours,
    )
    _effective_expiry = _expiry_hours if _expiry_hours is not None else float(JWT_EXPIRY_HOURS)
    _audit(db, None, "login", principal, f"from role={principal.role}")
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=int(_effective_expiry * 3600),
        principal=_to_response(principal),
    )


@router.get("/auth/me", response_model=PrincipalResponse)
async def get_me(principal: Principal = Depends(get_current_principal)):
    """Return current principal info."""
    return PrincipalResponse(
        id=principal.id,
        name=principal.name,
        email=principal.email,
        role=principal.role,
        enabled=True,
        created_at="",
    )


@router.post("/auth/logout")
async def logout(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_session),
):
    """Invalidate the current JWT by adding its jti to the Redis blocklist."""
    # Decode the raw token to extract jti + exp (we already verified it via Depends)
    try:
        from jose import jwt as _jwt
        raw = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if raw:
            payload = _jwt.decode(raw, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            jti = payload.get("jti")
            exp_ts = payload.get("exp")
            if jti and exp_ts:
                from datetime import datetime as _dt
                blocklist_token(jti, _dt.utcfromtimestamp(exp_ts))
    except Exception as exc:
        logger.warning("Logout: could not blocklist token: %s", exc)

    _audit(db, principal, "logout", detail="token blocklisted")
    return {"detail": "Logged out"}


@router.get("/auth/principals", response_model=List[PrincipalResponse])
def list_principals(
    db: Session = Depends(get_session),
    _admin: Principal = Depends(require_role("admin")),
):
    """List all principals [admin only]."""
    rows = db.query(PrincipalModel).order_by(PrincipalModel.created_at).all()
    return [_to_response(r) for r in rows]


@router.post("/auth/principals", response_model=PrincipalResponse, status_code=201)
def create_principal(
    body: CreatePrincipalRequest,
    db: Session = Depends(get_session),
    _admin: Principal = Depends(require_role("admin")),
):
    """Create a user or automation account [admin only]."""
    valid_roles = {"admin", "itom_admin", "operator", "viewer", "automation"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}")

    if body.role != "automation" and not body.email:
        raise HTTPException(status_code=422, detail="email is required for human accounts")

    if body.role != "automation" and not body.password:
        raise HTTPException(status_code=422, detail="password is required for human accounts")

    # Enforce password policy for human accounts
    if body.password:
        _validate_password(body.password)

    # Check email uniqueness
    if body.email:
        existing = db.query(PrincipalModel).filter(PrincipalModel.email == body.email).first()
        if existing:
            raise HTTPException(status_code=409, detail="A principal with that email already exists")

    hashed_pw = pwd_ctx.hash(body.password) if body.password else None

    row = PrincipalModel(
        id=uuid.uuid4(),
        name=body.name,
        email=body.email,
        role=body.role,
        hashed_pw=hashed_pw,
        enabled=True,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.put("/auth/principals/{principal_id}", response_model=PrincipalResponse)
def update_principal(
    principal_id: str,
    body: UpdatePrincipalRequest,
    db: Session = Depends(get_session),
    actor: Principal = Depends(require_role("admin")),
):
    """Update name, role, or enabled status [admin only]."""
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found")

    details = []
    if body.name is not None:
        details.append(f"name: {row.name!r} → {body.name!r}")
        row.name = body.name
    if body.role is not None:
        valid_roles = {"admin", "itom_admin", "operator", "viewer", "automation"}
        if body.role not in valid_roles:
            raise HTTPException(status_code=422, detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}")
        details.append(f"role: {row.role!r} → {body.role!r}")
        row.role = body.role
    if body.enabled is not None:
        details.append("enabled" if body.enabled else "disabled")
        row.enabled = body.enabled

    db.commit()
    db.refresh(row)
    if details:
        action = "enabled" if (body.enabled is True and len(details) == 1) else \
                 "disabled" if (body.enabled is False and len(details) == 1) else "updated"
        _audit(db, actor, action, row, "; ".join(details))
    return _to_response(row)


@router.delete("/auth/principals/{principal_id}", response_model=PrincipalResponse)
def disable_principal(
    principal_id: str,
    db: Session = Depends(get_session),
    _admin: Principal = Depends(require_role("admin")),
):
    """Soft-disable a principal (never hard-delete) [admin only]."""
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found")

    row.enabled = False
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.post("/auth/principals/{principal_id}/api-key", response_model=CreateApiKeyResponse)
def generate_api_key(
    principal_id: str,
    db: Session = Depends(get_session),
    _admin: Principal = Depends(require_role("admin")),
):
    """Generate a new API key for an automation account [admin only].
    The raw key is returned ONCE — it is never stored or retrievable again.
    """
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found")
    if row.role != "automation":
        raise HTTPException(status_code=422, detail="API keys can only be generated for automation accounts")

    raw_key     = secrets.token_urlsafe(32)
    prefix      = "ak_" + raw_key[:8]
    key_hash    = hashlib.sha256(raw_key.encode()).hexdigest()

    row.api_key_hash   = key_hash
    row.api_key_prefix = prefix
    db.commit()
    db.refresh(row)

    return CreateApiKeyResponse(
        api_key=raw_key,
        api_key_prefix=prefix,
        principal=_to_response(row),
    )


@router.delete("/auth/principals/{principal_id}/api-key", response_model=PrincipalResponse)
def revoke_api_key(
    principal_id: str,
    db: Session = Depends(get_session),
    actor: Principal = Depends(require_role("admin")),
):
    """Revoke the API key for an automation account [admin only]."""
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found")

    row.api_key_hash   = None
    row.api_key_prefix = None
    db.commit()
    db.refresh(row)
    _audit(db, actor, "api_key_revoked", row)
    return _to_response(row)


# ── Password management ───────────────────────────────────────────────────────

@router.post("/auth/change-password")
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_session),
    _rl: None = Depends(_change_pw_rate_limit),   # 5 per 15 min per IP
):
    """Self-service password change — requires the current password to proceed."""
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal.id).first()
    if not row or not row.hashed_pw:
        raise HTTPException(status_code=400,
            detail="Password change is not available for this account type.")
    if not pwd_ctx.verify(body.current_password, row.hashed_pw):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422,
            detail="New password must differ from the current password.")
    _validate_password(body.new_password)

    row.hashed_pw = pwd_ctx.hash(body.new_password)
    db.commit()
    _audit(db, principal, "password_changed", row, "self-service")
    return {"detail": "Password changed successfully."}


@router.post("/auth/principals/{principal_id}/reset-password")
def reset_password(
    principal_id: str,
    request: Request,
    body: ResetPasswordRequest,
    actor: Principal = Depends(require_role("admin")),
    db: Session = Depends(get_session),
    _rl: None = Depends(_reset_pw_rate_limit),    # 10 per min per IP
):
    """Admin resets any human principal's password [admin only]."""
    row = db.query(PrincipalModel).filter(PrincipalModel.id == principal_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found.")
    if row.role == "automation":
        raise HTTPException(status_code=422,
            detail="Automation accounts authenticate via API key — use Gen Key instead.")
    _validate_password(body.new_password)

    row.hashed_pw = pwd_ctx.hash(body.new_password)
    db.commit()
    _audit(db, actor, "password_reset", row, f"reset by admin {actor.name}")
    return {"detail": f"Password for {row.name} has been reset."}


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/auth/audit-log", response_model=List[AuditLogEntry])
def get_audit_log(
    limit: int = 100,
    db: Session = Depends(get_session),
    _admin: Principal = Depends(require_role("admin")),
):
    """Return the most recent audit log entries [admin only]."""
    rows = (
        db.query(PrincipalAuditLogModel)
        .order_by(PrincipalAuditLogModel.ts.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        AuditLogEntry(
            id=r.id,
            ts=r.ts.isoformat(),
            actor_name=r.actor_name,
            action=r.action,
            target_name=r.target_name,
            detail=r.detail,
        )
        for r in rows
    ]
