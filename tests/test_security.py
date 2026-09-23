"""§27 security properties that are not covered by the scenario suites."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from bizos.control import passwords, store as control
from bizos.control.tokens import CLAIM_CLIENT, CLAIM_ROLES, TokenError, issue_token, verify_token
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import (
    InvalidWorkspaceIdentifier,
    validate_identifier,
    workspace_connection,
)
from bizos.types import Role
from bizos.util.redaction import redact


# --------------------------------------------------------------- credentials


def test_passwords_are_hashed_not_stored(client_a):
    with control.control_connection() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE client_id = :c LIMIT 1"),
            {"c": client_a.id},
        ).first()
    assert row.password_hash.startswith("pbkdf2_sha256$")
    assert "bizos-dev-password" not in row.password_hash


def test_password_verification_is_correct():
    encoded = passwords.hash_password("correct horse battery staple")
    assert passwords.verify_password("correct horse battery staple", encoded)
    assert not passwords.verify_password("wrong", encoded)
    assert not passwords.verify_password("", encoded)
    assert not passwords.verify_password("x", "not-a-valid-hash")


def test_two_hashes_of_the_same_password_differ():
    """A per-password salt, so identical passwords do not share a hash."""
    assert passwords.hash_password("same") != passwords.hash_password("same")


def test_repeated_failures_lock_the_account(client_b):
    """Brute force is slowed by a lockout, not just by hashing cost."""
    email = f"viewer@{client_b.slug}.example.com"
    for _ in range(control.MAX_FAILED_LOGINS):
        with pytest.raises(control.AuthenticationError):
            control.authenticate(email, "wrong", client_slug=client_b.slug)
    with pytest.raises(control.AuthenticationError) as excinfo:
        control.authenticate(email, "bizos-dev-password", client_slug=client_b.slug)
    assert "locked" in str(excinfo.value).lower()
    # Reset so the rest of the suite is unaffected.
    user = next(u for u in control.list_users(client_b.id) if u.email == email)
    control.set_password(user.id, "bizos-dev-password")
    assert control.authenticate(email, "bizos-dev-password", client_slug=client_b.slug)


# -------------------------------------------------------------------- tokens


def test_a_forged_token_is_rejected():
    import jwt

    forged = jwt.encode(
        {"sub": "attacker", "exp": 9999999999, CLAIM_CLIENT: "cli_x", CLAIM_ROLES: ["ADMIN"]},
        "some-other-key-entirely-that-is-long-enough-x",
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        verify_token(forged)


def test_an_alg_none_token_is_rejected():
    """The verifier pins the algorithm, so `alg: none` cannot slip through."""
    import jwt

    unsigned = jwt.encode(
        {"sub": "attacker", "exp": 9999999999, CLAIM_CLIENT: "cli_x"}, None, algorithm="none"
    )
    with pytest.raises(TokenError):
        verify_token(unsigned)


def test_an_expired_token_is_rejected():
    token, _ = issue_token(user_id="u", client_id="c", roles={Role.VIEWER}, ttl_seconds=-120)
    with pytest.raises(TokenError):
        verify_token(token)


def test_roles_are_intersected_with_the_user_record(client_a):
    """A stale token claiming ADMIN does not grant ADMIN after a demotion."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.api.deps import current_context

    viewer = next(
        u for u in control.list_users(client_a.id) if u.email.startswith("viewer@")
    )
    inflated, _ = issue_token(
        user_id=viewer.id, client_id=client_a.id, roles={Role.ADMIN, Role.OPERATOR}
    )
    scope = {
        "type": "http", "method": "GET", "path": "/api/auth/me",
        "headers": [(b"authorization", f"Bearer {inflated}".encode())],
        "query_string": b"", "client": ("127.0.0.1", 1234),
    }
    from fastapi.security import HTTPAuthorizationCredentials

    ctx = current_context(
        Request(scope),
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=inflated),
    )
    # The user record says VIEWER, so the intersection is VIEWER.
    assert ctx.roles == frozenset({Role.VIEWER})
    assert not ctx.is_admin


def test_a_token_for_another_clients_user_is_refused(client_a, client_b):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.requests import Request

    from app.api.deps import current_context

    user_b = control.list_users(client_b.id)[0]
    mismatched, _ = issue_token(
        user_id=user_b.id, client_id=client_a.id, roles={Role.ADMIN}
    )
    scope = {
        "type": "http", "method": "GET", "path": "/api/auth/me",
        "headers": [], "query_string": b"", "client": ("127.0.0.1", 1234),
    }
    with pytest.raises(HTTPException) as excinfo:
        current_context(
            Request(scope),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=mismatched),
        )
    assert excinfo.value.status_code == 403


# ------------------------------------------------------------------- secrets


def test_redaction_covers_every_secret_shape():
    payload = {
        "password": "hunter2",
        "clientSecret": "abc",
        "oauth_refresh_token": "1//0abcdefghijklmnop",
        "nested": {"api_key": "sk-abcdefghijklmnopqrst", "ok": 1},
        "text": (
            "Bearer eyJhbGciOi.eyJzdWIi.SflKxwRJ and ya29.abcdefghijkl and "
            "xoxb-1234567890-abcdef and AKIAIOSFODNN7EXAMPLE and "
            "postgresql://u:p@h/db and 4111 1111 1111 1111"
        ),
        "token_usage": 512,
        "list": ["ghp_abcdefghijklmnopqrst"],
    }
    result = redact(payload)
    blob = str(result)
    for secret in ("hunter2", "1//0abcdefghijklmnop", "sk-abcdefghijklmnopqrst",
                   "eyJhbGciOi.eyJzdWIi.SflKxwRJ", "ya29.abcdefghijkl",
                   "xoxb-1234567890-abcdef", "AKIAIOSFODNN7EXAMPLE", "u:p@h",
                   "4111 1111 1111 1111", "ghp_abcdefghijklmnopqrst", "abc"):
        assert secret not in blob, secret
    # Useful non-secret fields survive.
    assert result["token_usage"] == 512
    assert result["nested"]["ok"] == 1


def test_integration_config_never_stores_a_credential(client_a, ctx_factory):
    """§14: a caller passing a token in `config` has it stripped, not persisted."""
    from bizos.connectors.registry import set_integration

    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        set_integration(
            category="email", provider="gmail", display_name="Gmail",
            status="DISCONNECTED", enabled=False,
            config={"mailbox": "ops@acme.example.com", "refresh_token": "1//0secretvalue",
                    "client_secret": "shhh"},
            ctx=ctx,
        )
        with workspace_connection(ctx) as conn:
            row = conn.execute(
                text("SELECT config::text AS body FROM integrations WHERE provider='gmail'")
            ).first()
    assert "1//0secretvalue" not in row.body
    assert "shhh" not in row.body
    assert "ops@acme.example.com" in row.body


# ------------------------------------------------------------- input safety


def test_workspace_identifiers_are_validated():
    """Identifiers reach DDL, so they are validated rather than escaped."""
    validate_identifier("bizos_ws_acme")
    for hostile in ('pg"; DROP DATABASE x; --', "Public", "1abc", "a" * 70, "", "ws-acme"):
        with pytest.raises(InvalidWorkspaceIdentifier):
            validate_identifier(hostile)


def test_crm_field_names_are_allowlisted(client_a, ctx_factory):
    """A field name reaches an UPDATE clause, so only known columns are accepted."""
    from bizos.connectors.registry import get_connector

    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        crm = get_connector("crm", ctx=ctx)
        result = crm.update_contact_field("anyone", field="email = 'x'; DROP TABLE crm_contacts; --", value="y")
    assert not result.ok
    assert "not an updatable contact field" in (result.error or "")
    # And the table is intact.
    with tenant_scope(ctx), workspace_connection(ctx) as conn:
        assert conn.execute(text("SELECT count(*) FROM crm_contacts")).scalar() >= 0


def test_workspace_connection_rejects_confinement_escapes(client_a, ctx_factory):
    """A statement that would repoint search_path or grant rights is refused."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        for statement in ("SET search_path TO public", "SET ROLE postgres",
                          "GRANT ALL ON SCHEMA public TO PUBLIC"):
            with pytest.raises(Exception) as excinfo:
                with workspace_connection(ctx) as conn:
                    conn.execute(text(statement))
            assert "rejected" in str(excinfo.value).lower(), statement


def test_a_jwt_secret_shorter_than_the_hash_is_refused(monkeypatch):
    from bizos import settings as app_settings

    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError):
        app_settings.jwt_secret()


def test_production_refuses_a_default_signing_key(monkeypatch):
    from bizos import settings as app_settings

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("RUNTIME_ENV", "prd")
    with pytest.raises(RuntimeError):
        app_settings.jwt_secret()
