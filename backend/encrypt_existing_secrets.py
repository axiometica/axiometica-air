#!/usr/bin/env python3
"""
One-time migration: encrypt plaintext secrets already sitting in the database
(connector passwords/tokens, Slack/SMTP credentials, LLM provider API keys).

Idempotent — safe to re-run. Each value is checked for the "enc:v1:" prefix
before being touched, so rows already encrypted by a previous run (or by the
app itself, going forward) are left alone.

Run from backend/ with the same environment the app uses (DATABASE_URL,
SECRET_ENCRYPTION_KEY):
    python encrypt_existing_secrets.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sqlalchemy import create_engine, text

from agentic_os.db.database import DATABASE_URL
from agentic_os.security.crypto import encrypt, is_encrypted

_CONNECTOR_SECRET_FIELDS = ["password", "token", "webhook_secret"]
_SLACK_SECRET_KEYS = {"slack.bot_token", "slack.signing_secret", "slack.app_token"}
_SMTP_SECRET_KEYS = {"smtp.password"}

engine = create_engine(DATABASE_URL)


def encrypt_connector_configs(conn) -> int:
    rows = conn.execute(text("SELECT id, config_json FROM connector_configs")).fetchall()
    changed = 0
    for connector_id, config_json in rows:
        config_json = config_json or {}
        updated = dict(config_json)
        touched = False
        for field in _CONNECTOR_SECRET_FIELDS:
            value = updated.get(field)
            if value and not is_encrypted(value):
                updated[field] = encrypt(value)
                touched = True
        if touched:
            conn.execute(
                text("UPDATE connector_configs SET config_json = CAST(:cfg AS JSON) WHERE id = :id"),
                {"cfg": json.dumps(updated), "id": connector_id},
            )
            changed += 1
            print(f"  connector_configs[{connector_id}]: encrypted {[f for f in _CONNECTOR_SECRET_FIELDS if f in updated]}")
    return changed


def encrypt_llm_configs(conn) -> int:
    rows = conn.execute(text("SELECT config_id, api_key FROM llm_configs")).fetchall()
    changed = 0
    for config_id, api_key in rows:
        if api_key and not is_encrypted(api_key):
            conn.execute(
                text("UPDATE llm_configs SET api_key = :key WHERE config_id = :id"),
                {"key": encrypt(api_key), "id": config_id},
            )
            changed += 1
            print(f"  llm_configs[{config_id}]: encrypted api_key")
    return changed


def encrypt_platform_settings(conn) -> int:
    keys = tuple(_SLACK_SECRET_KEYS | _SMTP_SECRET_KEYS)
    rows = conn.execute(
        text("SELECT key, value FROM platform_settings WHERE key IN :keys").bindparams(
            __import__("sqlalchemy").bindparam("keys", expanding=True)
        ),
        {"keys": keys},
    ).fetchall()
    changed = 0
    for key, value in rows:
        if value and not is_encrypted(value):
            conn.execute(
                text("UPDATE platform_settings SET value = :val WHERE key = :key"),
                {"val": encrypt(value), "key": key},
            )
            changed += 1
            print(f"  platform_settings[{key}]: encrypted")
    return changed


def main():
    with engine.connect() as conn:
        print("Encrypting connector_configs (ServiceNow/Splunk/webhook secrets)...")
        n1 = encrypt_connector_configs(conn)
        print("Encrypting llm_configs (OpenAI/Anthropic API keys)...")
        n2 = encrypt_llm_configs(conn)
        print("Encrypting platform_settings (Slack/SMTP credentials)...")
        n3 = encrypt_platform_settings(conn)
        conn.commit()
        print(f"\nDone. Rows encrypted — connectors: {n1}, llm_configs: {n2}, platform_settings: {n3}.")
        if n1 == n2 == n3 == 0:
            print("Nothing to do — either already migrated or no secrets configured yet.")


if __name__ == "__main__":
    main()
