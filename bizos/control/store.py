"""
Control-Plane Store
===================

CRUD for clients, users and role grants, plus the function that turns a
successful login into a :class:`~bizos.tenancy.context.TenantContext`.

Every read that returns customer-adjacent data takes an explicit ``client_id``
and filters on it. That is acceptable here because the control plane holds only
metadata (§1); customer content is reached exclusively through
:mod:`bizos.tenancy.registry`, which cannot see two clients at once.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import text

from bizos.control.db import control_connection
from bizos.control.models import Client, ClientSettings, User
from bizos.control.passwords import hash_password, verify_password
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import validate_identifier
from bizos.types import DeploymentType, ExecutionMode, Role
from bizos.util.ids import new_id, slugify
from bizos.util.timeutil import utcnow

#: Consecutive failed logins before an account is temporarily locked.
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 900


class ClientNotFound(LookupError):
    pass


class UserNotFound(LookupError):
    pass


class DuplicateClient(ValueError):
    pass


class DuplicateUser(ValueError):
    pass


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def _client_from_row(row: Any) -> Client:
    settings = ClientSettings.from_dict(row.settings)
    settings.client_id = row.id
    settings.company_name = row.company_name
    settings.deployment_type = row.deployment_type
    return Client(
        id=row.id,
        slug=row.slug,
        company_name=row.company_name,
        deployment_type=DeploymentType.parse(row.deployment_type, DeploymentType.DEDICATED_DB),  # type: ignore[arg-type]
        db_name=row.db_name,
        db_schema=row.db_schema,
        status=row.status,
        settings=settings,
        created_at=row.created_at,
    )


def create_client(
    *,
    company_name: str,
    slug: Optional[str] = None,
    deployment_type: Optional[DeploymentType] = None,
    settings: Optional[ClientSettings] = None,
    created_by: Optional[str] = None,
) -> Client:
    """Register a client and decide where its workspace lives.

    This only writes the registry row; :func:`bizos.tenancy.provisioning.provision_client`
    creates the actual database/schema. Splitting the two keeps registration
    transactional and provisioning re-runnable.
    """
    from bizos import settings as app_settings

    slug_value = validate_identifier(slugify(slug or company_name))
    deployment = deployment_type or app_settings.default_deployment_type()
    client_id = new_id("cli")

    if deployment == DeploymentType.DEDICATED_SCHEMA:
        db_name = app_settings.shared_workspace_database()
        db_schema = validate_identifier(f"ws_{slug_value}")
    else:
        db_name = validate_identifier(f"{app_settings.workspace_db_prefix()}{slug_value}")
        db_schema = "public"

    cfg = settings or ClientSettings()
    cfg.client_id = client_id
    cfg.company_name = company_name
    cfg.deployment_type = deployment.value
    if not cfg.enabled_domains:
        cfg.enabled_domains = list(DEFAULT_ENABLED_DOMAINS)
    cfg = ClientSettings.from_dict(cfg.to_dict())  # normalize + clamp

    with control_connection() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM clients WHERE slug = :s"), {"s": slug_value}
        ).first()
        if exists:
            raise DuplicateClient(f"A client with slug {slug_value!r} already exists")
        conn.execute(
            text(
                "INSERT INTO clients (id, slug, company_name, deployment_type, db_name, "
                "db_schema, status, settings, created_by) VALUES "
                "(:id, :slug, :name, :dep, :db, :schema, 'ACTIVE', CAST(:settings AS JSONB), :by)"
            ),
            {
                "id": client_id,
                "slug": slug_value,
                "name": company_name,
                "dep": deployment.value,
                "db": db_name,
                "schema": db_schema,
                "settings": _json(cfg.to_dict()),
                "by": created_by,
            },
        )
        for domain in DOMAIN_NAMES:
            conn.execute(
                text(
                    "INSERT INTO client_domains (client_id, domain, enabled) "
                    "VALUES (:c, :d, :e) ON CONFLICT DO NOTHING"
                ),
                {"c": client_id, "d": domain, "e": domain in cfg.enabled_domains},
            )
    return get_client(client_id)


def get_client(client_id: str) -> Client:
    with control_connection() as conn:
        row = conn.execute(text("SELECT * FROM clients WHERE id = :i"), {"i": client_id}).first()
    if row is None:
        raise ClientNotFound(client_id)
    return _client_from_row(row)


def get_client_by_slug(slug: str) -> Client:
    with control_connection() as conn:
        row = conn.execute(text("SELECT * FROM clients WHERE slug = :s"), {"s": slug}).first()
    if row is None:
        raise ClientNotFound(slug)
    return _client_from_row(row)


def list_clients() -> list[Client]:
    with control_connection() as conn:
        rows = conn.execute(text("SELECT * FROM clients ORDER BY created_at")).fetchall()
    return [_client_from_row(r) for r in rows]


def update_client_settings(client_id: str, settings: ClientSettings, *, updated_by: str) -> Client:
    """Replace a client's settings blob (normalized and clamped first)."""
    normalized = ClientSettings.from_dict(settings.to_dict())
    normalized.client_id = client_id
    with control_connection() as conn:
        result = conn.execute(
            text(
                "UPDATE clients SET settings = CAST(:s AS JSONB), updated_at = NOW() WHERE id = :i"
            ),
            {"s": _json(normalized.to_dict()), "i": client_id},
        )
        if result.rowcount == 0:
            raise ClientNotFound(client_id)
        for domain in DOMAIN_NAMES:
            conn.execute(
                text(
                    "INSERT INTO client_domains (client_id, domain, enabled, updated_by, updated_at) "
                    "VALUES (:c, :d, :e, :u, NOW()) "
                    "ON CONFLICT (client_id, domain) DO UPDATE SET enabled = EXCLUDED.enabled, "
                    "updated_by = EXCLUDED.updated_by, updated_at = NOW()"
                ),
                {
                    "c": client_id,
                    "d": domain,
                    "e": domain in normalized.enabled_domains,
                    "u": updated_by,
                },
            )
    return get_client(client_id)


def list_client_domains(client_id: str) -> list[dict[str, Any]]:
    """Domain enablement for a client, joined to the catalogue."""
    with control_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT p.name, p.title, p.description, p.phase, p.depth, "
                "COALESCE(cd.enabled, FALSE) AS enabled, COALESCE(cd.config, '{}'::jsonb) AS config "
                "FROM domain_packs p "
                "LEFT JOIN client_domains cd ON cd.domain = p.name AND cd.client_id = :c "
                "ORDER BY p.phase, p.name"
            ),
            {"c": client_id},
        ).fetchall()
    return [
        {
            "name": r.name,
            "title": r.title,
            "description": r.description,
            "phase": r.phase,
            "depth": r.depth,
            "enabled": r.enabled,
            "config": r.config,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------


def create_user(
    *,
    client_id: str,
    email: str,
    password: str,
    display_name: str = "",
    roles: Iterable[Role] = (Role.VIEWER,),
    created_by: Optional[str] = None,
) -> User:
    """Create a user inside one client and grant their roles."""
    email_norm = email.strip().casefold()
    if not email_norm:
        raise ValueError("email is required")
    role_set = frozenset(roles) or frozenset({Role.VIEWER})
    user_id = new_id("usr")
    with control_connection() as conn:
        if not conn.execute(text("SELECT 1 FROM clients WHERE id = :i"), {"i": client_id}).first():
            raise ClientNotFound(client_id)
        if conn.execute(
            text("SELECT 1 FROM users WHERE client_id = :c AND email = :e"),
            {"c": client_id, "e": email_norm},
        ).first():
            raise DuplicateUser(f"{email_norm} already exists for this client")
        conn.execute(
            text(
                "INSERT INTO users (id, client_id, email, display_name, password_hash, created_by) "
                "VALUES (:i, :c, :e, :n, :p, :b)"
            ),
            {
                "i": user_id,
                "c": client_id,
                "e": email_norm,
                "n": display_name or email_norm,
                "p": hash_password(password),
                "b": created_by,
            },
        )
        _replace_roles(conn, user_id, role_set, created_by)
    return get_user(user_id)


def _replace_roles(conn: Any, user_id: str, roles: frozenset[Role], granted_by: Optional[str]) -> None:
    conn.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user_id})
    for role in roles:
        conn.execute(
            text("INSERT INTO user_roles (user_id, role, granted_by) VALUES (:u, :r, :g)"),
            {"u": user_id, "r": str(role), "g": granted_by},
        )


def set_user_roles(user_id: str, roles: Iterable[Role], *, granted_by: str) -> User:
    """Replace a user's role grants wholesale."""
    role_set = frozenset(roles)
    if not role_set:
        raise ValueError("a user must hold at least one role")
    with control_connection() as conn:
        if not conn.execute(text("SELECT 1 FROM users WHERE id = :i"), {"i": user_id}).first():
            raise UserNotFound(user_id)
        _replace_roles(conn, user_id, role_set, granted_by)
    return get_user(user_id)


def set_user_status(user_id: str, status: str) -> User:
    """Activate or suspend a user."""
    with control_connection() as conn:
        result = conn.execute(
            text("UPDATE users SET status = :s, updated_at = NOW() WHERE id = :i"),
            {"s": status, "i": user_id},
        )
        if result.rowcount == 0:
            raise UserNotFound(user_id)
    return get_user(user_id)


def set_password(user_id: str, password: str) -> None:
    with control_connection() as conn:
        result = conn.execute(
            text("UPDATE users SET password_hash = :p, failed_logins = 0, locked_until = NULL, "
                 "updated_at = NOW() WHERE id = :i"),
            {"p": hash_password(password), "i": user_id},
        )
        if result.rowcount == 0:
            raise UserNotFound(user_id)


def _roles_for(conn: Any, user_id: str) -> frozenset[Role]:
    rows = conn.execute(
        text("SELECT role FROM user_roles WHERE user_id = :u"), {"u": user_id}
    ).fetchall()
    parsed = {Role.parse(r.role) for r in rows}
    return frozenset(r for r in parsed if r is not None)  # type: ignore[misc]


def _user_from_row(conn: Any, row: Any) -> User:
    return User(
        id=row.id,
        client_id=row.client_id,
        email=row.email,
        display_name=row.display_name,
        roles=_roles_for(conn, row.id),
        status=row.status,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


def get_user(user_id: str) -> User:
    with control_connection() as conn:
        row = conn.execute(text("SELECT * FROM users WHERE id = :i"), {"i": user_id}).first()
        if row is None:
            raise UserNotFound(user_id)
        return _user_from_row(conn, row)


def list_users(client_id: str) -> list[User]:
    """Every user belonging to one client. Never returns another client's users."""
    with control_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM users WHERE client_id = :c ORDER BY created_at"), {"c": client_id}
        ).fetchall()
        return [_user_from_row(conn, r) for r in rows]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class AuthenticationError(Exception):
    """Login failed. The message is deliberately non-specific to the caller."""


def authenticate(email: str, password: str, *, client_slug: Optional[str] = None) -> User:
    """Verify a password and return the user.

    ``client_slug`` disambiguates when the same email exists at more than one
    client. Without it, a unique match is required — never a "first match wins",
    which would silently sign someone in to the wrong workspace.

    Failure bookkeeping is deliberately committed in its **own** transaction
    before the error is raised. Doing the update in the same transaction as the
    raise would roll the increment back, so the lockout counter would never
    advance and brute-force protection would silently not exist.
    """
    email_norm = (email or "").strip().casefold()

    with control_connection() as conn:
        if client_slug:
            rows = conn.execute(
                text(
                    "SELECT u.* FROM users u JOIN clients c ON c.id = u.client_id "
                    "WHERE u.email = :e AND c.slug = :s"
                ),
                {"e": email_norm, "s": client_slug.strip().casefold()},
            ).fetchall()
        else:
            rows = conn.execute(
                text("SELECT * FROM users WHERE email = :e"), {"e": email_norm}
            ).fetchall()

    if len(rows) != 1:
        # Zero matches, or ambiguous across clients. Burn a hash comparison anyway
        # so response time does not reveal whether the account exists.
        verify_password(password, hash_password("timing-equalizer"))
        raise AuthenticationError("Invalid credentials")

    row = rows[0]
    now = utcnow()
    if row.locked_until is not None and row.locked_until > now:
        raise AuthenticationError("Account temporarily locked")
    if row.status != "ACTIVE":
        raise AuthenticationError("Invalid credentials")

    if not verify_password(password, row.password_hash):
        _record_failed_login(row.id, (row.failed_logins or 0) + 1)
        raise AuthenticationError("Invalid credentials")

    with control_connection() as conn:
        conn.execute(
            text(
                "UPDATE users SET last_login_at = NOW(), failed_logins = 0, locked_until = NULL "
                "WHERE id = :i"
            ),
            {"i": row.id},
        )
        return _user_from_row(conn, row)


def _record_failed_login(user_id: str, failed: int) -> None:
    """Commit the failure count, locking the account once the threshold is hit."""
    lock_clause = (
        f"NOW() + INTERVAL '{LOCKOUT_SECONDS} seconds'" if failed >= MAX_FAILED_LOGINS else "NULL"
    )
    with control_connection() as conn:
        conn.execute(
            text(f"UPDATE users SET failed_logins = :f, locked_until = {lock_clause} WHERE id = :i"),
            {"f": failed, "i": user_id},
        )


def build_context(user: User, *, attributes: Optional[dict] = None) -> TenantContext:
    """Turn an authenticated user into the tenant context for their workspace."""
    client = get_client(user.client_id)
    return TenantContext(
        client_id=client.id,
        client_slug=client.slug,
        user_id=user.id,
        roles=user.roles,
        deployment_type=client.deployment_type,
        db_name=client.db_name,
        db_schema=client.db_schema,
        default_execution_mode=client.settings.mode,
        email=user.email,
        display_name=user.display_name,
        attributes=attributes or {},
    )


def system_context(client_id: str, *, user_id: str = "__system__") -> TenantContext:
    """A full-authority context for server-side automation (scheduler, workflows).

    Still scoped to exactly one client — automation is not a way to see across
    workspaces. Callers must not build this from request data.
    """
    client = get_client(client_id)
    return TenantContext(
        client_id=client.id,
        client_slug=client.slug,
        user_id=user_id,
        roles=frozenset({Role.OPERATOR}),
        deployment_type=client.deployment_type,
        db_name=client.db_name,
        db_schema=client.db_schema,
        default_execution_mode=client.settings.mode,
        display_name="System automation",
    )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
