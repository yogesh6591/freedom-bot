"""§14 — the workspace secret store."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from bizos import secrets as secret_store
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection

TOKEN = "1//0-a-real-looking-refresh-token-value"


def test_round_trip(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        secret_store.put_secret("gmail_refresh_token", TOKEN, ctx=ctx)
        assert secret_store.get_secret("gmail_refresh_token", ctx=ctx) == TOKEN
        assert "gmail_refresh_token" in secret_store.list_secret_names(ctx)


def test_the_stored_value_is_encrypted(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        secret_store.put_secret("probe", TOKEN, ctx=ctx)
        with workspace_connection(ctx, readonly=True) as conn:
            row = conn.execute(
                text("SELECT ciphertext FROM secrets WHERE name='probe'")
            ).first()
    assert TOKEN.encode() not in bytes(row.ciphertext)


def test_each_write_uses_a_fresh_nonce(client_a, ctx_factory):
    """Identical plaintext must not produce identical ciphertext."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        secret_store.put_secret("nonce_probe", TOKEN, ctx=ctx)
        with workspace_connection(ctx, readonly=True) as conn:
            first = bytes(
                conn.execute(text("SELECT ciphertext FROM secrets WHERE name='nonce_probe'")).first().ciphertext
            )
        secret_store.put_secret("nonce_probe", TOKEN, ctx=ctx)
        with workspace_connection(ctx, readonly=True) as conn:
            second = bytes(
                conn.execute(text("SELECT ciphertext FROM secrets WHERE name='nonce_probe'")).first().ciphertext
            )
    assert first != second


def test_a_ciphertext_from_another_workspace_cannot_be_decrypted(client_a, client_b, ctx_factory):
    """Per-tenant key derivation: moving a row between workspaces makes it useless."""
    ctx_a = ctx_factory(client_a, "admin")
    ctx_b = ctx_factory(client_b, "admin")
    with tenant_scope(ctx_a):
        secret_store.put_secret("cross_tenant", TOKEN, ctx=ctx_a)
        with workspace_connection(ctx_a, readonly=True) as conn:
            row = conn.execute(
                text("SELECT ciphertext, nonce FROM secrets WHERE name='cross_tenant'")
            ).first()
    with tenant_scope(ctx_b):
        with workspace_connection(ctx_b) as conn:
            conn.execute(
                text("INSERT INTO secrets (name, ciphertext, nonce) VALUES ('stolen', :c, :n)"),
                {"c": bytes(row.ciphertext), "n": bytes(row.nonce)},
            )
        with pytest.raises(Exception):
            secret_store.get_secret("stolen", ctx=ctx_b)


def test_tampering_is_detected(client_a, ctx_factory):
    """AES-GCM is authenticated: a flipped byte fails rather than decrypting."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        secret_store.put_secret("tamper", TOKEN, ctx=ctx)
        with workspace_connection(ctx) as conn:
            row = conn.execute(text("SELECT ciphertext FROM secrets WHERE name='tamper'")).first()
            corrupted = bytearray(bytes(row.ciphertext))
            corrupted[0] ^= 0xFF
            conn.execute(
                text("UPDATE secrets SET ciphertext = :c WHERE name='tamper'"),
                {"c": bytes(corrupted)},
            )
        with pytest.raises(Exception):
            secret_store.get_secret("tamper", ctx=ctx)


def test_missing_secret_raises(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        with pytest.raises(secret_store.SecretNotFound):
            secret_store.get_secret("never_stored", ctx=ctx)
