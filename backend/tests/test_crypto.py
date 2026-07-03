"""
Unit tests for agentic_os.security.crypto — secrets-at-rest encryption.

No database required: these test the encrypt/decrypt primitives and the
EncryptedString SQLAlchemy type in isolation.
"""
import base64
import os

import pytest


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    """Use a fixed, valid Fernet key for every test so results are deterministic."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ENVIRONMENT", "test")
    # Clear the module-level Fernet cache so each test's monkeypatched key takes effect
    from agentic_os.security import crypto
    crypto._fernet_cache.clear()
    yield
    crypto._fernet_cache.clear()


class TestEncryptDecrypt:
    def test_round_trip(self):
        from agentic_os.security.crypto import encrypt, decrypt_if_encrypted
        plaintext = "xoxb-super-secret-token-123"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext
        assert ciphertext.startswith("enc:v1:")
        assert decrypt_if_encrypted(ciphertext) == plaintext

    def test_empty_string_passes_through(self):
        from agentic_os.security.crypto import encrypt, decrypt_if_encrypted
        assert encrypt("") == ""
        assert encrypt(None) is None
        assert decrypt_if_encrypted("") == ""
        assert decrypt_if_encrypted(None) is None

    def test_plaintext_passthrough_on_decrypt(self):
        """Legacy/un-migrated plaintext values decrypt as a no-op."""
        from agentic_os.security.crypto import decrypt_if_encrypted
        assert decrypt_if_encrypted("plain-old-password") == "plain-old-password"

    def test_encrypt_is_idempotent(self):
        """Re-encrypting an already-encrypted value is a no-op, not double encryption."""
        from agentic_os.security.crypto import encrypt, decrypt_if_encrypted
        once = encrypt("my-secret")
        twice = encrypt(once)
        assert once == twice
        assert decrypt_if_encrypted(twice) == "my-secret"

    def test_is_encrypted(self):
        from agentic_os.security.crypto import encrypt, is_encrypted
        assert is_encrypted(encrypt("x")) is True
        assert is_encrypted("plaintext") is False
        assert is_encrypted("") is False
        assert is_encrypted(None) is False

    def test_decrypt_with_wrong_key_returns_empty_not_raise(self, monkeypatch):
        """A value encrypted under a different key fails closed (empty string), not an exception."""
        from cryptography.fernet import Fernet
        from agentic_os.security import crypto

        monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
        crypto._fernet_cache.clear()
        ciphertext = crypto.encrypt("secret-under-key-a")

        monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
        crypto._fernet_cache.clear()
        assert crypto.decrypt_if_encrypted(ciphertext) == ""

    def test_production_requires_key(self, monkeypatch):
        from agentic_os.security import crypto
        monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        crypto._fernet_cache.clear()
        with pytest.raises(RuntimeError, match="SECRET_ENCRYPTION_KEY"):
            crypto.encrypt("anything")

    def test_dev_fallback_key_used_outside_production(self, monkeypatch):
        from agentic_os.security import crypto
        monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        crypto._fernet_cache.clear()
        ciphertext = crypto.encrypt("dev-secret")
        assert crypto.decrypt_if_encrypted(ciphertext) == "dev-secret"


class TestFieldHelpers:
    def test_encrypt_fields_only_touches_listed_keys(self):
        from agentic_os.security.crypto import encrypt_fields, is_encrypted
        data = {"base_url": "https://x.test", "username": "bob", "password": "hunter2"}
        out = encrypt_fields(data, ["password", "token"])
        assert out["base_url"] == "https://x.test"
        assert out["username"] == "bob"
        assert is_encrypted(out["password"])
        assert "token" not in out  # missing field is skipped, not added

    def test_decrypt_fields_round_trip(self):
        from agentic_os.security.crypto import encrypt_fields, decrypt_fields
        data = {"password": "hunter2", "token": "tok-abc"}
        encrypted = encrypt_fields(data, ["password", "token"])
        decrypted = decrypt_fields(encrypted, ["password", "token"])
        assert decrypted == data

    def test_encrypt_fields_skips_empty_values(self):
        from agentic_os.security.crypto import encrypt_fields
        data = {"password": "", "token": None}
        out = encrypt_fields(data, ["password", "token"])
        assert out["password"] == ""
        assert out["token"] is None


class TestEncryptedStringType:
    def test_bind_and_result_round_trip(self):
        from agentic_os.security.crypto import EncryptedString, decrypt_if_encrypted
        col = EncryptedString(500)
        bound = col.process_bind_param("sk-real-api-key", dialect=None)
        assert bound != "sk-real-api-key"
        assert bound.startswith("enc:v1:")
        result = col.process_result_value(bound, dialect=None)
        assert result == "sk-real-api-key"

    def test_none_passes_through(self):
        from agentic_os.security.crypto import EncryptedString
        col = EncryptedString(500)
        assert col.process_bind_param(None, dialect=None) is None
        assert col.process_result_value(None, dialect=None) is None

    def test_reading_legacy_plaintext_column_value(self):
        """A column written before encryption existed reads back unchanged."""
        from agentic_os.security.crypto import EncryptedString
        col = EncryptedString(500)
        assert col.process_result_value("sk-legacy-plaintext-key", dialect=None) == "sk-legacy-plaintext-key"
