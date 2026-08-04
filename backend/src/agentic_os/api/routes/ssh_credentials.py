"""
SSH Credential Management API.

CRUD for named SSH credentials stored with AES-encrypted private keys.
The SSH adapter resolves credentials at connection time by matching the
target hostname against host_pattern (fnmatch glob).

GET    /api/settings/ssh-credentials          → list all (key masked)
POST   /api/settings/ssh-credentials          → create
PUT    /api/settings/ssh-credentials/{id}     → update
DELETE /api/settings/ssh-credentials/{id}     → delete
POST   /api/settings/ssh-credentials/{id}/test → test connection
"""

from __future__ import annotations

import fnmatch
import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.models import SSHCredentialModel

logger = logging.getLogger(__name__)

router = APIRouter()
automation_router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class SSHCredentialCreate(BaseModel):
    name: str = Field(..., max_length=100)
    host_pattern: str = Field(..., max_length=255)
    username: str = Field("root", max_length=100)
    private_key: str = Field(..., description="PEM-encoded private key")
    port: int = Field(22, ge=1, le=65535)
    description: Optional[str] = None


class SSHCredentialUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    host_pattern: Optional[str] = Field(None, max_length=255)
    username: Optional[str] = Field(None, max_length=100)
    private_key: Optional[str] = Field(None, description="PEM-encoded private key (omit to keep existing)")
    port: Optional[int] = Field(None, ge=1, le=65535)
    description: Optional[str] = None
    enabled: Optional[bool] = None


class SSHCredentialResponse(BaseModel):
    id: str
    name: str
    host_pattern: str
    username: str
    has_key: bool
    port: int
    description: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str


class SSHCredentialTestRequest(BaseModel):
    hostname: str = Field(..., description="Target hostname or IP to test against")
    port: Optional[int] = Field(None, ge=1, le=65535, description="Override port for test")


def _to_response(row: SSHCredentialModel) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "host_pattern": row.host_pattern,
        "username": row.username,
        "has_key": bool(row.private_key),
        "port": row.port,
        "description": row.description,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/settings/ssh-credentials")
def list_ssh_credentials(db: Session = Depends(get_session)):
    rows = db.query(SSHCredentialModel).order_by(SSHCredentialModel.name).all()
    return {"credentials": [_to_response(r) for r in rows]}


@router.post("/settings/ssh-credentials", status_code=201)
def create_ssh_credential(body: SSHCredentialCreate, db: Session = Depends(get_session)):
    existing = db.query(SSHCredentialModel).filter_by(name=body.name).first()
    if existing:
        raise HTTPException(409, f"Credential named '{body.name}' already exists")

    row = SSHCredentialModel(
        id=_uuid.uuid4(),
        name=body.name,
        host_pattern=body.host_pattern,
        username=body.username,
        private_key=body.private_key,
        port=body.port,
        description=body.description,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"[SSH CRED] Created credential '{body.name}' for pattern '{body.host_pattern}'")
    return _to_response(row)


@router.put("/settings/ssh-credentials/{cred_id}")
def update_ssh_credential(cred_id: str, body: SSHCredentialUpdate, db: Session = Depends(get_session)):
    row = db.query(SSHCredentialModel).filter_by(id=cred_id).first()
    if not row:
        raise HTTPException(404, "Credential not found")

    if body.name is not None:
        dup = db.query(SSHCredentialModel).filter(
            SSHCredentialModel.name == body.name,
            SSHCredentialModel.id != row.id,
        ).first()
        if dup:
            raise HTTPException(409, f"Credential named '{body.name}' already exists")
        row.name = body.name
    if body.host_pattern is not None:
        row.host_pattern = body.host_pattern
    if body.username is not None:
        row.username = body.username
    if body.private_key is not None:
        row.private_key = body.private_key
    if body.port is not None:
        row.port = body.port
    if body.description is not None:
        row.description = body.description
    if body.enabled is not None:
        row.enabled = body.enabled

    db.commit()
    db.refresh(row)
    logger.info(f"[SSH CRED] Updated credential '{row.name}'")
    return _to_response(row)


@router.delete("/settings/ssh-credentials/{cred_id}")
def delete_ssh_credential(cred_id: str, db: Session = Depends(get_session)):
    row = db.query(SSHCredentialModel).filter_by(id=cred_id).first()
    if not row:
        raise HTTPException(404, "Credential not found")
    name = row.name
    db.delete(row)
    db.commit()
    logger.info(f"[SSH CRED] Deleted credential '{name}'")
    return {"deleted": True, "name": name}


@router.post("/settings/ssh-credentials/{cred_id}/test")
def test_ssh_credential(cred_id: str, body: SSHCredentialTestRequest, db: Session = Depends(get_session)):
    """Test an SSH credential by connecting to a hostname and running 'echo ok'."""
    row = db.query(SSHCredentialModel).filter_by(id=cred_id).first()
    if not row:
        raise HTTPException(404, "Credential not found")

    if not fnmatch.fnmatch(body.hostname, row.host_pattern):
        return {
            "success": False,
            "error": f"Hostname '{body.hostname}' does not match pattern '{row.host_pattern}'",
        }

    try:
        import paramiko
        import io

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pkey = paramiko.RSAKey.from_private_key(io.StringIO(row.private_key))
        port = body.port or row.port

        client.connect(
            hostname=body.hostname,
            port=port,
            username=row.username,
            pkey=pkey,
            timeout=10,
            banner_timeout=10,
        )
        _, stdout, _ = client.exec_command("echo ok", timeout=5)
        output = stdout.read().decode().strip()
        client.close()

        return {"success": output == "ok", "output": output}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── Resolve endpoint (used by remote watchers via API) ────────────────────────

class SSHCredentialResolveRequest(BaseModel):
    hostname: str = Field("", description="Target hostname to match against host_pattern")
    credential_name: Optional[str] = Field(None, description="Exact credential name (takes priority over hostname)")


@automation_router.post("/settings/ssh-credentials/resolve")
def resolve_ssh_credential_api(body: SSHCredentialResolveRequest, db: Session = Depends(get_session)):
    """Resolve an SSH credential by name or hostname pattern match.
    Returns the full credential including decrypted private key.
    Requires API key auth (used by remote watchers)."""
    cred = None
    if body.credential_name:
        cred = db.query(SSHCredentialModel).filter_by(
            name=body.credential_name, enabled=True,
        ).first()
    if not cred and body.hostname:
        cred = resolve_credential(body.hostname, db)
    if not cred:
        return {"found": False}
    return {
        "found": True,
        "username": cred.username,
        "port": cred.port,
        "private_key": cred.private_key,
        "name": cred.name,
        "host_pattern": cred.host_pattern,
    }


# ── Lookup helper (used by SSH adapter direct DB path) ────────────────────────

def resolve_credential(hostname: str, db: Session) -> Optional[SSHCredentialModel]:
    """Find the first enabled credential whose host_pattern matches hostname."""
    rows = db.query(SSHCredentialModel).filter_by(enabled=True).all()
    for row in rows:
        if fnmatch.fnmatch(hostname, row.host_pattern):
            return row
    return None
