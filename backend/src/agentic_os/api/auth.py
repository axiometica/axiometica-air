"""
Core authentication and authorisation module.

Supports two credential types:
  - Bearer JWT  (human users — issued at login, 8h TTL)
  - X-API-Key   (automation accounts — watcher_brain, external tools)

Both resolve to a Principal dataclass. All permission checks are identical
regardless of credential type. Use require_role() as a FastAPI dependency.

JWT logout blocklist
--------------------
Each JWT carries a `jti` (JWT ID) claim — a random UUID minted at issuance.
When a principal logs out, the jti is written to Redis with a TTL equal to the
token's remaining lifetime.  verify_jwt() rejects any token whose jti is found
in the blocklist.

Fail-open: if Redis is unavailable the blocklist check is skipped and the
token is accepted.  This preserves availability at the cost of not honouring
logouts during a Redis outage — an acceptable trade-off for most deployments.
"""
import hashlib
import logging
import os
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session

logger = logging.getLogger(__name__)

_JWT_SECRET_DEFAULT = "dev-secret-change-in-production-openssl-rand-hex-32"
JWT_SECRET = os.getenv("JWT_SECRET", _JWT_SECRET_DEFAULT)
if JWT_SECRET == _JWT_SECRET_DEFAULT and os.getenv("ENVIRONMENT", "development") == "production":
    raise RuntimeError(
        "JWT_SECRET must be set to a strong random value in production. "
        "Generate one with: openssl rand -hex 32"
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "8"))

# ── Redis blocklist helpers ──────────────────────────────────────────────────

_BLOCKLIST_PREFIX = "jwt:blocklist:"


def _get_redis():
    """Lazy-acquire a Redis client. Returns None if Redis is unreachable."""
    try:
        import redis as _redis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        client = _redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("[auth] Redis unavailable for blocklist — fail-open: %s", exc)
        return None


def blocklist_token(jti: str, exp: datetime) -> None:
    """Add a JWT's jti to the Redis blocklist until the token's expiry time."""
    client = _get_redis()
    if client is None:
        return
    try:
        ttl = max(int((exp - datetime.utcnow()).total_seconds()), 1)
        client.setex(f"{_BLOCKLIST_PREFIX}{jti}", ttl, "1")
        logger.info("[auth] Token jti=%s blocklisted (TTL %ds)", jti, ttl)
    except Exception as exc:
        logger.warning("[auth] Could not blocklist token jti=%s: %s", jti, exc)


def _is_blocklisted(jti: str) -> bool:
    """Return True if the jti appears in the Redis blocklist."""
    client = _get_redis()
    if client is None:
        return False  # fail-open
    try:
        return client.exists(f"{_BLOCKLIST_PREFIX}{jti}") > 0
    except Exception as exc:
        logger.warning("[auth] Blocklist lookup failed (fail-open): %s", exc)
        return False

bearer_scheme  = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass
class Principal:
    id:    str
    name:  str
    email: Optional[str]
    role:  str   # admin | operator | viewer | automation


def create_access_token(
    principal_id: str,
    name: str,
    email: Optional[str],
    role: str,
    expiry_hours: Optional[float] = None,
) -> str:
    hours   = expiry_hours if expiry_hours is not None else float(JWT_EXPIRY_HOURS)
    expire  = datetime.utcnow() + timedelta(hours=hours)
    jti     = str(_uuid.uuid4())   # unique token ID — used for blocklist on logout
    payload = {
        "sub": principal_id,
        "name": name,
        "email": email,
        "role": role,
        "exp": expire,
        "jti": jti,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> Principal:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {e}")

    # Blocklist check — rejects tokens that were explicitly logged out
    jti = payload.get("jti")
    if jti and _is_blocklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked. Please log in again.")

    return Principal(
        id=payload["sub"],
        name=payload["name"],
        email=payload.get("email"),
        role=payload["role"],
    )


def verify_api_key(key: str, db: Session) -> Principal:
    from agentic_os.db.models import PrincipalModel
    key_hash  = hashlib.sha256(key.encode()).hexdigest()
    principal = db.query(PrincipalModel).filter(
        PrincipalModel.api_key_hash == key_hash,
        PrincipalModel.enabled      == True,
    ).first()
    if not principal:
        raise HTTPException(status_code=401, detail="Invalid API key")
    # update last_seen
    try:
        principal.last_seen_at = datetime.utcnow()
        db.commit()
    except Exception:
        pass
    return Principal(id=str(principal.id), name=principal.name,
                     email=principal.email, role=principal.role)


async def get_current_principal(
    bearer:  Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    api_key: Optional[str]                          = Security(api_key_header),
    db:      Session                                = Depends(get_session),
) -> Principal:
    if bearer:
        return verify_jwt(bearer.credentials)
    if api_key:
        return verify_api_key(api_key, db)
    raise HTTPException(status_code=401,
        detail="Authentication required. Provide a Bearer token or X-API-Key header.")


def require_role(*roles: str):
    """FastAPI dependency that checks the caller has one of the given roles."""
    async def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=403,
                detail=f"Role '{principal.role}' not authorised. Required: {', '.join(roles)}")
        return principal
    return _check


# ── Shorthand dependencies ──────────────────────────────────────────────────
# Role hierarchy (highest → lowest privilege):
#   admin > itom_admin > operator > viewer > automation
def AdminOnly():           return require_role("admin")
def AdminOrITOMAdmin():    return require_role("admin", "itom_admin")
def AdminOrOperator():     return require_role("admin", "itom_admin", "operator")
def AnyHuman():            return require_role("admin", "itom_admin", "operator", "viewer")
def AnyPrincipal():        return require_role("admin", "itom_admin", "operator", "viewer", "automation")
