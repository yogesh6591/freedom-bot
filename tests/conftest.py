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

from freedombot.util.dotenv import load_dotenv

load_dotenv()
# Tests must never be able to mint a token accepted by a real deployment.
os.environ.setdefault("RUNTIME_ENV", "dev")
# Tests wipe and rebuild acquisition data, so they ALWAYS use a throwaway database,
# whatever DATABASE_URL says in .env or the shell. The real acquisition database is never touched.
_DB = dict(
    u=os.environ.get("DB_USER", "freedombot"),
    p=os.environ.get("DB_PASS", "freedombot"),
    h=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
)
ACQUISITION_TEST_DB = "acquisition_test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://{u}:{p}@{h}:{port}/".format(**_DB) + ACQUISITION_TEST_DB
os.environ["JWT_SECRET"] = "test-only-signing-key-please-do-not-reuse-anywhere-0001"
# Memory vector search calls the embedding API; tests that need it switch it on with a fake embedder.
os.environ["MEMORY_VECTOR_SEARCH"] = "off"
# The background integration sync must not start inside tests.
os.environ["INTEGRATION_SYNC_ENABLED"] = "off"
os.environ["SCHEDULED_TASKS_ENABLED"] = "off"
# Tests never reach Composio (CLAUDE.md: mocked tools only); tests that need it patch it in.
os.environ["COMPOSIO_API_KEY"] = ""

from freedombot.bootstrap import admin_context, ensure_dev_users, seed_business_data  # noqa: E402
from freedombot.connectors.registry import seed_default_integrations  # noqa: E402
from freedombot.control import store as control  # noqa: E402
from freedombot.control.db import control_connection, migrate_control_plane  # noqa: E402
from freedombot.control.models import ClientSettings  # noqa: E402
from freedombot.tenancy import provisioning  # noqa: E402
from freedombot.tenancy.context import TenantContext  # noqa: E402
from freedombot.tools.base import RunScope  # noqa: E402
from freedombot.tools.registry_check import load_all_tools  # noqa: E402
from freedombot.types import DeploymentType, ExecutionMode, Role  # noqa: E402

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


@pytest.fixture(scope="session", autouse=True)
def _acquisition_test_db() -> None:
    """Create and migrate the throwaway acquisition database (once per run)."""
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import sql

    control = os.environ.get("CONTROL_DB_NAME", "bizos_control")
    with psycopg.connect(
        host=_DB["h"], port=_DB["port"], user=_DB["u"], password=_DB["p"], dbname=control, autocommit=True
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (ACQUISITION_TEST_DB,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(ACQUISITION_TEST_DB)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    command.upgrade(Config(os.path.join(root, "acquisition_alembic.ini")), "head")


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


@pytest.fixture(scope="module")
def client_scratch():
    """A WAIT_FOR_APPROVAL client a test module may reconfigure freely."""
    suffix = uuid.uuid4().hex[:8]
    client = _make_client(
        f"Test Scratch {suffix}", f"tscratch{suffix}", DeploymentType.DEDICATED_SCHEMA,
        ExecutionMode.WAIT_FOR_APPROVAL,
    )
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
