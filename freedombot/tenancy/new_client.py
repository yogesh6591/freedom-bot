"""
New Client
==========

A platform owner can add a client from the owner page instead of running
``scripts/provision_client.py`` on the server. Both use :func:`set_up_workspace`,
so a client made either way starts the same way: the template's settings, its own
database (or schema), the client wall on every table, the default integrations,
the template's starter memory and a first admin.

The screen differs from the script in three ways:

- It never reuses an existing client. A workspace identifier that is already
  taken is refused, so nobody can add an admin to another company from here.
- It writes no deployment files. A client made here signs in on this deployment;
  a separate per-client deployment still comes from the script.
- If any step fails, the half-made workspace and its registry row are removed,
  so a failed attempt can simply be tried again.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from agno.utils.log import log_warning
from sqlalchemy import text

from freedombot import admin_ops, template
from freedombot import settings as app_settings
from freedombot.connectors.registry import seed_default_integrations
from freedombot.control import store as control
from freedombot.control.db import control_connection
from freedombot.control.models import Client
from freedombot.domains.catalog import DOMAIN_CATALOG, DOMAIN_NAMES
from freedombot.tenancy import provisioning
from freedombot.tenancy.context import TenantContext
from freedombot.tenancy.registry import registry
from freedombot.types import AuditEventType, DeploymentType, Role
from freedombot.util.ids import new_id, slugify

#: Always part of what a client has (the catalog says "Always enabled").
ALWAYS_ON = "general"
MAX_NAME_LENGTH = 120


class SetupFailed(admin_ops.AdminError):
    """The server could not finish the workspace. Nothing was kept."""

    status = 500


class WorkspaceNotWalled(RuntimeError):
    """A new workspace has tables without the per-client wall. It must not be used."""


def set_up_workspace(client: Client, config: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Create and fill one client's workspace. Shared by the screen and the script.

    Idempotent, like the steps it runs. Raises :class:`WorkspaceNotWalled` when a
    table is missing the client wall.
    """
    from freedombot.bootstrap import admin_context

    report = provisioning.provision_client(client)
    ctx = admin_context(client, user_id=actor)
    seed_default_integrations(ctx)
    seeded = template.seed_memory(ctx, config, client.settings)
    unwalled = provisioning.unwalled_tables(ctx)
    if unwalled:
        raise WorkspaceNotWalled(f"client wall missing on: {', '.join(unwalled)}")
    return {"provisioning": report, "seeded_memory": seeded}


def form_defaults() -> dict[str, Any]:
    """What the new-client form offers: every domain, and what a client gets by default."""
    defaults = _purchased(None)
    return {
        "domains": [
            {"name": d.name, "label": d.title, "description": d.description, "always_on": d.name == ALWAYS_ON}
            for d in DOMAIN_CATALOG
        ],
        "default_domains": defaults,
        "min_password_length": admin_ops.MIN_PASSWORD_LENGTH,
        "signs_in_here": app_settings.pinned_client_slug() is None,
    }


def create_client(
    *,
    company_name: str,
    admin_email: str,
    admin_password: str,
    admin_name: str = "",
    slug: Optional[str] = None,
    purchased_domains: Optional[Iterable[str]] = None,
    actor: TenantContext,
) -> dict[str, Any]:
    """Make a new client with its first admin. ``actor`` is the platform owner doing it.

    Raises :class:`freedombot.admin_ops.AdminError` (400) for bad input and
    :class:`freedombot.admin_ops.AdminConflict` (409) when the identifier is taken.
    Nothing is left behind when it raises.
    """
    name = " ".join((company_name or "").split())
    if not name:
        raise admin_ops.AdminError("Enter the company name")
    if len(name) > MAX_NAME_LENGTH:
        raise admin_ops.AdminError(f"The company name can be at most {MAX_NAME_LENGTH} characters")
    workspace = slugify(slug or name)
    email = (admin_email or "").strip()
    if not admin_ops._EMAIL.match(email):
        raise admin_ops.AdminError(f"{email!r} is not a valid email address")
    if len(admin_password or "") < admin_ops.MIN_PASSWORD_LENGTH:
        raise admin_ops.AdminError(f"Password must be at least {admin_ops.MIN_PASSWORD_LENGTH} characters")
    domains = _purchased(purchased_domains)
    if _slug_taken(workspace):
        raise admin_ops.AdminConflict(f"The workspace identifier {workspace!r} is already taken. Choose another.")
    if _leftover_workspace(workspace):
        # Most likely a removed client's data. Reusing it could show their data to the new client,
        # and undoing a failed setup would delete it, so it is never touched from here.
        raise admin_ops.AdminConflict(
            f"A database for {workspace!r} is still on the server from an earlier client. "
            "Choose another identifier, or ask whoever runs the server to remove it."
        )

    config = template.merge(template.load_template(), {"purchased_domains": domains, "enabled_domains": domains})
    try:
        client = control.create_client(
            company_name=name,
            slug=workspace,
            # This server's own setting decides where workspaces live (DEFAULT_DEPLOYMENT_TYPE).
            deployment_type=None,
            settings=template.settings_from(config, company_name=name),
            created_by=actor.user_id,
        )
    except control.DuplicateClient as exc:  # someone took it a moment ago
        raise admin_ops.AdminConflict(f"The workspace identifier {workspace!r} is already taken. Choose another.") from exc
    except ValueError as exc:
        raise admin_ops.AdminError(str(exc)) from exc

    try:
        report = set_up_workspace(client, config, actor=actor.user_id)
        from freedombot.bootstrap import admin_context

        admin = admin_ops.create_user(
            admin_context(client, user_id=actor.user_id),
            email=email,
            password=admin_password,
            display_name=" ".join((admin_name or "").split()) or "Client admin",
            roles=(Role.ADMIN.value,),
        )
    except BaseException as exc:
        _discard(client)
        try:
            _control_audit(actor, None, "CLIENT_CREATE_FAILED", {"slug": workspace, "company_name": name},
                           # Only our own messages: a database error can quote the values it was given.
                           error=exc.message if isinstance(exc, admin_ops.AdminError) else type(exc).__name__)
        except Exception as audit_exc:  # the original failure is the one to report
            log_warning(f"could not audit the failed client {workspace}: {audit_exc}")
        if isinstance(exc, admin_ops.AdminError):
            raise
        if isinstance(exc, Exception):
            log_warning(f"new client {workspace} could not be set up: {exc!r}")
            raise SetupFailed(
                "The new workspace could not be set up, so nothing was kept. Try again, or ask "
                "whoever runs the server to check its logs."
            ) from exc
        raise

    client = control.get_client(client.id)
    try:
        _control_audit(actor, client.id, "CLIENT_CREATED",
                       {"slug": client.slug, "company_name": name, "admin_email": email, "purchased_domains": domains})
    except Exception as exc:  # the client is made; a missing audit row must not report it as failed
        log_warning(f"could not audit the new client {client.slug}: {exc}")
    return {
        "client": {
            "id": client.id,
            "slug": client.slug,
            "company_name": client.company_name,
            "deployment_type": str(client.deployment_type),
            "purchased_domains": client.settings.purchased_domains,
        },
        "admin": {"email": admin.email, "display_name": admin.display_name},
        "seeded_memory": report["seeded_memory"],
        "signs_in_here": app_settings.pinned_client_slug() in (None, client.slug),
    }


def _purchased(chosen: Optional[Iterable[str]]) -> list[str]:
    """The chosen domains in catalog order, with General always included."""
    if chosen is None:
        picked = set(template.load_template().get("purchased_domains") or [])
    else:
        picked = {str(d).strip() for d in chosen if str(d).strip()}
        unknown = sorted(picked - set(DOMAIN_NAMES))
        if unknown:
            raise admin_ops.AdminError(f"Unknown domain: {', '.join(unknown)}")
    picked.add(ALWAYS_ON)
    return [d for d in DOMAIN_NAMES if d in picked]


def _slug_taken(slug: str) -> bool:
    try:
        control.get_client_by_slug(slug)
    except control.ClientNotFound:
        return False
    return True


def _leftover_workspace(slug: str) -> bool:
    """Whether the database (or schema) a new client with this identifier would get already exists."""
    if app_settings.default_deployment_type() != DeploymentType.DEDICATED_SCHEMA:
        return provisioning.database_exists(f"{app_settings.workspace_db_prefix()}{slug}")
    shared = app_settings.shared_workspace_database()
    if not provisioning.database_exists(shared):
        return False
    with registry.engine(shared, "public").connect() as conn:
        found = conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"), {"s": f"ws_{slug}"}
        ).first()
    return found is not None


def _discard(client: Client) -> None:
    """Undo a client whose setup failed: its workspace, then its registry row (users go with it).

    Logs rather than raises, so the failure that started the undo is the one reported.
    """
    try:
        provisioning.deprovision_client(client)
    except Exception as exc:
        log_warning(f"could not remove the workspace of failed client {client.slug}: {exc}")
    try:
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})
    except Exception as exc:
        log_warning(f"could not remove the registry row of failed client {client.slug}: {exc}")


def _control_audit(actor: TenantContext, client_id: Optional[str], status: str, request: dict[str, Any],
                   *, error: Optional[str] = None) -> None:
    """Who made which client, in the control-plane audit. The password is never recorded."""
    with control_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO control_audit_events (id, event_type, client_id, user_id, actor_role, status, request, "
                "error) VALUES (:i, :e, :c, :u, 'PLATFORM_OWNER', :s, CAST(:r AS JSONB), :err)"
            ),
            {
                "i": new_id("caud"),
                "e": str(AuditEventType.CONFIG_CHANGED),
                "c": client_id,
                "u": actor.user_id,
                "s": status,
                "r": json.dumps({**request, "by_client": actor.client_id}, default=str),
                "err": error,
            },
        )
