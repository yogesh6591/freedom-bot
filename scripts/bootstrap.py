"""
Bootstrap CLI
=============

    python -m scripts.bootstrap            # migrate the control plane only
    python -m scripts.bootstrap --demo     # …plus the Acme demo workspace and users
    python -m scripts.bootstrap --reset    # destroy the demo workspaces first

Idempotent, so it is safe as a container start step.
"""

from __future__ import annotations

import argparse
import json
import sys

from bizos.util.dotenv import load_dotenv

load_dotenv()

from bizos.bootstrap import DEV_PASSWORD, bootstrap  # noqa: E402
from bizos.control import store as control  # noqa: E402
from bizos.control.db import control_connection, migrate_control_plane  # noqa: E402
from bizos.tenancy import provisioning  # noqa: E402


def reset_demo() -> None:
    """Destroy the demo workspaces so a re-seed starts clean."""
    from sqlalchemy import text

    migrate_control_plane()
    # Include legacy "globex" so older demo installs are fully cleaned on --reset.
    for slug in ("acme", "globex"):
        try:
            client = control.get_client_by_slug(slug)
        except control.ClientNotFound:
            continue
        provisioning.deprovision_client(client)
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})
        print(f"removed demo client {slug}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the platform.")
    parser.add_argument("--demo", action="store_true", help="seed demo clients and users")
    parser.add_argument("--reset", action="store_true", help="destroy demo clients first")
    args = parser.parse_args()

    if args.reset:
        reset_demo()

    report = bootstrap(with_demo=args.demo)
    print(json.dumps(report, indent=2, default=str))
    if args.demo:
        print(f"\nDevelopment users share the password: {DEV_PASSWORD}")
        for entry in report["clients"]:
            print(f"  {entry['slug']} ({entry['mode']}, {entry['deployment_type']})")
            for user in entry["users"]:
                print(f"    {user['email']:40s} {', '.join(user['roles'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
