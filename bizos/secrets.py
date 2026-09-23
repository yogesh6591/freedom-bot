"""
Workspace Secret Store
======================

§14 and §27: OAuth tokens, API keys and connector credentials are encrypted at
rest inside the client's own workspace, and are never exposed to the model.

Design notes:

* **AES-256-GCM** with a random 96-bit nonce per write. GCM is authenticated, so
  a tampered ciphertext fails to decrypt rather than returning garbage.
* The key-encryption key comes from ``SECRET_ENCRYPTION_KEY`` and is derived with
  HKDF **per client**, so two workspaces never share an encryption key even
  though they share the master secret. A stolen ciphertext from one tenant is
  useless against another.
* Nothing in this module is reachable from a tool, an agent prompt or an API
  response. The only consumer is the connector layer, which uses a credential to
  make a call and never returns it.
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text

from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import workspace_connection

_NONCE_BYTES = 12
_KEY_BYTES = 32


class SecretsNotConfigured(RuntimeError):
    """No master key is configured, so secrets cannot be stored or read."""


class SecretNotFound(LookupError):
    pass


def _master_key() -> bytes:
    raw = os.getenv("SECRET_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise SecretsNotConfigured(
            "SECRET_ENCRYPTION_KEY is not set, so connector credentials cannot be stored. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if len(raw.encode()) < 32:
        raise SecretsNotConfigured("SECRET_ENCRYPTION_KEY must be at least 32 bytes.")
    return raw.encode("utf-8")


def _client_key(client_id: str) -> bytes:
    """Derive this client's encryption key from the master key.

    Per-tenant derivation means the blast radius of one leaked ciphertext is one
    tenant, and it makes "was this row copied from another workspace?" a
    decryption failure rather than a silent success.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=b"bizos-workspace-secrets-v1",
        info=client_id.encode("utf-8"),
    )
    return hkdf.derive(_master_key())


def put_secret(name: str, value: str, *, ctx: Optional[TenantContext] = None) -> None:
    """Encrypt and store one secret in the current workspace."""
    tenant = ctx or require_context()
    nonce = os.urandom(_NONCE_BYTES)
    # The client id is authenticated additional data, so a ciphertext moved to
    # another workspace's table fails to decrypt instead of being readable there.
    ciphertext = AESGCM(_client_key(tenant.client_id)).encrypt(
        nonce, value.encode("utf-8"), tenant.client_id.encode("utf-8")
    )
    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO secrets (name, ciphertext, nonce, created_by) "
                "VALUES (:n, :c, :o, :u) "
                "ON CONFLICT (name) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, "
                "nonce = EXCLUDED.nonce, updated_at = NOW()"
            ),
            {"n": name, "c": ciphertext, "o": nonce, "u": tenant.user_id},
        )


def get_secret(name: str, *, ctx: Optional[TenantContext] = None) -> str:
    """Decrypt one secret. Raises rather than returning a partial value."""
    tenant = ctx or require_context()
    with workspace_connection(tenant, readonly=True) as conn:
        row = conn.execute(
            text("SELECT ciphertext, nonce FROM secrets WHERE name = :n"), {"n": name}
        ).first()
    if row is None:
        raise SecretNotFound(name)
    plaintext = AESGCM(_client_key(tenant.client_id)).decrypt(
        bytes(row.nonce), bytes(row.ciphertext), tenant.client_id.encode("utf-8")
    )
    return plaintext.decode("utf-8")


def delete_secret(name: str, *, ctx: Optional[TenantContext] = None) -> bool:
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        result = conn.execute(text("DELETE FROM secrets WHERE name = :n"), {"n": name})
    return result.rowcount > 0


def list_secret_names(ctx: Optional[TenantContext] = None) -> list[str]:
    """Names only — the values never leave this module."""
    with workspace_connection(ctx, readonly=True) as conn:
        return [r.name for r in conn.execute(text("SELECT name FROM secrets ORDER BY name")).fetchall()]


def secrets_available() -> bool:
    try:
        _master_key()
        return True
    except SecretsNotConfigured:
        return False
