"""Removing a workspace, or failing to provision one, closes every connection pool opened for it."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from bizos import settings
from bizos.control import store as control
from bizos.control.db import control_connection
from bizos.control.models import ClientSettings
from bizos.tenancy import agnodb, provisioning
from bizos.tenancy.registry import registry, workspace_location
from bizos.types import DeploymentType


def _client(deployment):
    suffix = uuid.uuid4().hex[:8]
    return control.create_client(company_name=f"Pool {suffix}", slug=f"tpool{suffix}", deployment_type=deployment,
                                 settings=ClientSettings(), created_by="pytest")


def _forget(client):
    with control_connection() as conn:
        conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})


def _key(client):
    return workspace_location(provisioning._provisioning_context(client))


def _connections() -> int:
    engine = create_engine(settings.control_db_url(), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname IS NOT NULL")).scalar())
    finally:
        engine.dispose()


def _spy_dispose(monkeypatch, db):
    closed = []
    real = db.db_engine.dispose
    monkeypatch.setattr(db.db_engine, "dispose", lambda *a, **k: (closed.append(True), real(*a, **k))[1])
    return closed


@pytest.mark.parametrize("deployment", [DeploymentType.DEDICATED_SCHEMA, DeploymentType.DEDICATED_DB])
def test_deprovisioning_closes_the_agno_pool_and_the_registry_engines(monkeypatch, deployment):
    client = _client(deployment)
    try:
        provisioning.provision_client(client)
        key = _key(client)
        db = agnodb._dbs[key]
        closed = _spy_dispose(monkeypatch, db)
        provisioning.deprovision_client(client)
        assert key not in agnodb._dbs and closed == [True]
        assert not [k for k in registry._engines if k[:2] == key]
    finally:
        _forget(client)


def test_a_failed_provisioning_closes_what_it_opened(monkeypatch):
    client = _client(DeploymentType.DEDICATED_SCHEMA)
    key = _key(client)
    opened = {}
    real_agno = provisioning.provision_agno_tables

    def agno_then_remember(ctx):
        real_agno(ctx)
        opened["db"] = agnodb._dbs[key]
        opened["closed"] = _spy_dispose(monkeypatch, opened["db"])

    monkeypatch.setattr(provisioning, "provision_agno_tables", agno_then_remember)
    monkeypatch.setattr(provisioning, "verify_workspace", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        with pytest.raises(RuntimeError, match="boom"):
            provisioning.provision_client(client)
        assert key not in agnodb._dbs and opened["closed"] == [True]
        assert not [k for k in registry._engines if k[:2] == key]
    finally:
        monkeypatch.undo()
        provisioning.deprovision_client(client)
        _forget(client)


def test_clear_cache_closes_every_pool(monkeypatch):
    client = _client(DeploymentType.DEDICATED_SCHEMA)
    try:
        provisioning.provision_client(client)
        closed = _spy_dispose(monkeypatch, agnodb._dbs[_key(client)])
        agnodb.clear_cache()
        assert closed == [True] and agnodb._dbs == {}
    finally:
        provisioning.deprovision_client(client)
        _forget(client)


def test_repeated_workspaces_do_not_accumulate_connections():
    before = _connections()
    for _ in range(8):
        client = _client(DeploymentType.DEDICATED_SCHEMA)
        try:
            provisioning.provision_client(client)
        finally:
            provisioning.deprovision_client(client)
            _forget(client)
    assert _connections() <= before + 2
