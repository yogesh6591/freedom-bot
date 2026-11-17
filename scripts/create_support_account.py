"""
Create a support account (run once per person)
==============================================

    python -m scripts.create_support_account --email yogesh@cepoch.com --name "Yogesh" --team CEPOCH
    python -m scripts.create_support_account --email jeanne@cepoch.com --team JEANNE

Adds (or updates) an account in Cepoch's internal workspace (``cepoch``) and puts it on a support team:

* ``CEPOCH``: technical tickets, the first support layer, and the triage queue.
* ``JEANNE``: business and operational tickets.

Only these accounts receive and work support tickets; a client workspace's admins never do.

The password is typed at a hidden prompt. For a non-interactive run set ``SUPPORT_ACCOUNT_PASSWORD``.
Running it again for an existing account keeps the account, sets the team, and changes the password
only if one is given. Sign in with workspace identifier ``cepoch``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from freedombot.util.dotenv import load_dotenv

load_dotenv()

from freedombot import admin_ops  # noqa: E402
from freedombot.bootstrap import OWNER_WORKSPACE_SLUG, ensure_internal_workspace  # noqa: E402
from freedombot.control import store as control  # noqa: E402
from freedombot.control.db import migrate_control_plane  # noqa: E402
from freedombot.types import Role  # noqa: E402


def _password(required: bool) -> str:
    from_env = os.environ.get("SUPPORT_ACCOUNT_PASSWORD", "")
    if from_env:
        return from_env
    prompt = "Password for the support account" + ("" if required else " (Enter to keep the current one)") + ": "
    first = getpass.getpass(prompt)
    if not first and not required:
        return ""
    if getpass.getpass("Type it again: ") != first:
        raise SystemExit("The two passwords do not match. Nothing was changed.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--team", required=True, choices=["CEPOCH", "JEANNE"])
    args = parser.parse_args()

    email = args.email.strip().casefold()
    migrate_control_plane()
    workspace = ensure_internal_workspace()
    user = next((u for u in control.list_users(workspace.id) if u.email == email), None)
    password = _password(required=user is None)
    if password and len(password) < admin_ops.MIN_PASSWORD_LENGTH:
        print(f"The password must be at least {admin_ops.MIN_PASSWORD_LENGTH} characters. Nothing was changed.",
              file=sys.stderr)
        return 2
    if user is None:
        user = control.create_user(
            client_id=workspace.id, email=email, password=password,
            display_name=args.name.strip() or email.split("@")[0].title(),
            roles=(Role.ADMIN,), created_by="create_support_account",
        )
        action = "Created"
    else:
        if password:
            control.set_password(user.id, password)
        if user.status != "ACTIVE":
            control.set_user_status(user.id, "ACTIVE")
        action = "Updated"
    control.set_support_team(user.id, args.team)
    print(f"{action} {email} on the {args.team} support team.")
    print(f"Sign in with workspace identifier '{OWNER_WORKSPACE_SLUG}' and open Support in the left menu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
