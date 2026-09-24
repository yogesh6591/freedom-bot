"""
Test fixtures.
==============

Every test runs against **real Postgres**, with real provisioned workspaces. The
platform's central claim is about isolation and enforcement at the database and
policy layer; mocking that away would test nothing.

Two throwaway clients are provisioned per session (one ``DEDICATED_DB``, one
``DEDICATED_SCHEMA``, so both isolation models are exercised) and destroyed
afterwards.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from bizos.util.dotenv import load_dotenv

load_dotenv()
# Tests must never be able to mint a token accepted by a real deployment.
os.environ.setdefault("RUNTIME_ENV", "dev")
os.environ["JWT_SECRET"] = "test-only-signing-key-please-do-not-reuse-anywhere-0001"

from bizos.bootstrap import admin_context, ensure_dev_users, seed_business_data  # noqa: E402
from bizos.connectors.registry import seed_default_integrations  # noqa: E402
from bizos.control import store as control  # noqa: E402
from bizos.control.db import control_connection, migrate_control_plane  # noqa: E402
from bizos.control.models import ClientSettings  # noqa: E402
from bizos.tenancy import provisioning  # noqa: E402
from bizos.tenancy.context import TenantContext  # noqa: E402
from bizos.tools.base import RunScope  # noqa: E402
from bizos.tools.registry_check import load_all_tools  # noqa: E402
from bizos.types import DeploymentType, ExecutionMode, Role  # noqa: E402

load_all_tools()

#: Unique per run so parallel or repeated runs never collide.
SUFFIX = uuid.uuid4().hex[:8]


def _make_client(name: str, slug: str, deployment: DeploymentType, mode: ExecutionMode):
    settings = ClientSettings()
    settings.default_execution_mode = mode.value
    client = control.create_client(
        company_name=name, slug=slug, deployment_type=deployment, settings=settings,
        created_by="pytest",
    )
    provisioning.provision_client(client)
    ctx = admin_context(client)
    seed_default_integrations(ctx)
    # `.example.com` rather than `.test`: the API validates addresses and
    # rejects reserved special-use TLDs, which is the correct behaviour.
    ensure_dev_users(client, domain=f"{slug}.example.com")
    return control.get_client(client.id)


def _destroy(client) -> None:
    try:
        provisioning.deprovision_client(client)
    finally:
        with control_connection() as conn:
            from sqlalchemy import text

            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})


@pytest.fixture(scope="session", autouse=True)
def _control_plane() -> None:
    migrate_control_plane()


@pytest.fixture(scope="session")
def client_a():
    """Client A — dedicated database, WAIT_FOR_APPROVAL."""
    client = _make_client(
        f"Test Alpha {SUFFIX}", f"talpha{SUFFIX}", DeploymentType.DEDICATED_DB,
        ExecutionMode.WAIT_FOR_APPROVAL,
    )
    seed_business_data(client, flavor="default")
    yield client
    _destroy(client)


@pytest.fixture(scope="session")
def client_b():
    """Client B — dedicated schema, ADVISE. Different data, deliberately."""
    client = _make_client(
        f"Test Beta {SUFFIX}", f"tbeta{SUFFIX}", DeploymentType.DEDICATED_SCHEMA,
        ExecutionMode.ADVISE,
    )
    seed_business_data(client, flavor="alt")
    yield client
    _destroy(client)


def _user(client, prefix: str):
    email = f"{prefix}@{client.slug}.example.com"
    return next(u for u in control.list_users(client.id) if u.email == email)


@pytest.fixture
def ctx_factory():
    """Build a verified tenant context for a named demo user."""

    def make(client, prefix: str) -> TenantContext:
        return control.build_context(_user(client, prefix))

    return make


@pytest.fixture
def scope_factory(ctx_factory):
    """Build a RunScope, optionally overriding the workspace settings."""

    def make(
        client,
        prefix: str,
        *,
        domain: str = "sales",
        mode: ExecutionMode | None = None,
        settings: ClientSettings | None = None,
        requested_mode: ExecutionMode | None = None,
    ) -> RunScope:
        resolved = settings or client.settings
        if mode is not None:
            resolved = ClientSettings.from_dict(
                {**resolved.to_dict(), "default_execution_mode": mode.value}
            )
        return RunScope(
            ctx=ctx_factory(client, prefix),
            settings=resolved,
            domain=domain,
            requested_mode=requested_mode,
        )

    return make


@pytest.fixture
def settings_with():
    """A ClientSettings with specific overrides applied and clamped."""

    def make(base: ClientSettings, **overrides) -> ClientSettings:
        payload = base.to_dict()
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                payload[key] = {**payload[key], **value}
            else:
                payload[key] = value
        return ClientSettings.from_dict(payload)

    return make
