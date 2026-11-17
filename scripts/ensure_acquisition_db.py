"""Create the acquisition database on the FreedomBot Postgres server."""

from __future__ import annotations

import os

import psycopg
from psycopg import sql


def main() -> None:
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ.get("DB_USER", "freedombot")
    password = os.environ.get("DB_PASS", "freedombot")
    control = os.environ.get("CONTROL_DB_NAME", "bizos_control")
    name = os.environ.get("ACQUISITION_DB_NAME", "acquisition")
    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=control,
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"database {name} is ready")


if __name__ == "__main__":
    main()