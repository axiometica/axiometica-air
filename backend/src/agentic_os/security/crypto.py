"""
Application-level encryption for secrets stored at rest — connector
passwords/tokens (ServiceNow, Splunk, webhooks), Slack/SMTP credentials, and
LLM provider API keys.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a master key from
SECRET_ENCRYPTION_KEY. Ciphertext carries a version prefix ("enc:v1:") so
decrypt_if_encrypted() can tell encrypted values apart from legacy plaintext
during migration, and so a future key-rotation scheme has a version to branch
on. This closes DB-only exposure (backup leak, SQL injection read) — it does
not protect against a full host/container compromise where the env var
itself is also readable. See SECURITY.md for key-backup guidance.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator
from sqlalchemy import String as _SAString

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"
_ENV_VAR = "SECRET_ENCRYPTION_KEY"

# Deterministic, publicly-known key used only when SECRET_ENCRYPTION_KEY is
# unset outside production (local dev/tests). Never use this in production —
# set the env var. Fixed (not random-per-process) so encrypted values survive
# dev process restarts.
_DEV_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"axiometica-air-insecure-dev-key-do-not-use-in-prod").digest()
).decode()

_fernet_cache: dict[str, Fernet] = {}


def _get_fernet() -> Fernet:
    key = os.getenv(_ENV_VAR, "").strip()
    environment = os.getenv("ENVIRONMENT", "production").lower()

    if not key:
        if environment == "production":
            raise RuntimeError(
                f"{_ENV_VAR} is not set. Generate one with: "
                f'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
                f"and add it to your .env — secrets cannot be stored or read without it in production. "
                f"Back this key up somewhere other than .env (e.g. a password manager) — if it's lost, "
                f"every encrypted secret becomes permanently unrecoverable."
            )
        logger.warning(
            "%s not set — using a fixed, publicly-known development key. "
            "This is fine for local dev but must NOT be used in production.",
            _ENV_VAR,
        )
        key = _DEV_KEY

    cached = _fernet_cache.get(key)
    if cached is None:
        try:
            cached = Fernet(key.encode())
        except Exception as exc:
            raise RuntimeError(f"{_ENV_VAR} is not a valid Fernet key: {exc}") from exc
        _fernet_cache[key] = cached
    return cached


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Empty/falsy input is returned unchanged.

    Idempotent: a value that's already encrypted (enc:v1: prefix) is returned
    as-is rather than double-encrypted. Callers routinely re-spread an
    existing config dict (keep-existing-value-if-not-replacing semantics)
    where that existing value may already be ciphertext from a prior save.
    """
    if not plaintext or is_encrypted(plaintext):
        return plaintext
    return _PREFIX + _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_if_encrypted(value: str) -> str:
    """Decrypt a value if it carries the enc:v1: prefix; otherwise return it unchanged.

    Safe to call on any stored setting/config value, not just known-secret
    fields — plaintext (including legacy un-migrated rows) passes through
    untouched, so callers don't need to track which keys are secrets.
    """
    if not value or not value.startswith(_PREFIX):
        return value
    token = value[len(_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error(
            "Failed to decrypt a stored secret — SECRET_ENCRYPTION_KEY may be incorrect "
            "or the value was encrypted under a different key."
        )
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)


def encrypt_fields(data: dict, fields: list[str]) -> dict:
    """Return a copy of data with the given keys encrypted (skips missing/empty values)."""
    out = dict(data)
    for f in fields:
        if out.get(f):
            out[f] = encrypt(out[f])
    return out


def decrypt_fields(data: dict, fields: list[str]) -> dict:
    """Return a copy of data with the given keys decrypted (no-op for plaintext/missing)."""
    out = dict(data)
    for f in fields:
        if out.get(f):
            out[f] = decrypt_if_encrypted(out[f])
    return out


class EncryptedString(TypeDecorator):
    """SQLAlchemy column type that transparently encrypts on write, decrypts on read.

    Use for dedicated single-purpose secret columns (e.g. LLMConfigModel.api_key).
    For JSON blobs that mix secret and non-secret fields, use encrypt_fields()/
    decrypt_fields() at the API boundary instead — a column type can't apply
    selectively within a JSON dict.
    """

    impl = _SAString
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_if_encrypted(value)
