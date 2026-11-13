"""
Create the platform owner account (run once)
============================================

    python -m scripts.create_platform_owner --email you@cepoch.com --name "Your Name"

Makes Cepoch's internal workspace (``cepoch``) if it is missing, adds an ADMIN
there with this email, and gives that account the platform owner view: every
client, every workspace and its members, and the New client form.

The password is typed at a hidden prompt, so it never lands in shell history.
For a non-interactive run, set ``PLATFORM_OWNER_PASSWORD`` instead.

Running it again for the same email keeps the account and only sets the new
password, so it doubles as a password reset for the platform owner.

Sign in afterwards with workspace identifier ``cepoch``, this email and password.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from bizos.util.dotenv import load_dotenv

load_dotenv()

from bizos import admin_ops  # noqa: E402
from bizos.bootstrap import OWNER_WORKSPACE_SLUG, ensure_internal_workspace  # noqa: E402
from bizos.control import store as control  # noqa: E402
from bizos.control.db import migrate_control_plane  # noqa: E402
from bizos.types import Role  # noqa: E402


def _password() -> str:
    from_env = os.environ.get("PLATFORM_OWNER_PASSWORD", "")
    if from_env:
        return from_env
    first = getpass.getpass("Password for the platform owner: ")
    second = getpass.getpass("Type it again: ")
    if first != second:
        raise SystemExit("The two passwords do not match. Nothing was changed.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Platform owner")
    args = parser.parse_args()

    email = args.email.strip().casefold()
    password = _password()
    if len(password) < admin_ops.MIN_PASSWORD_LENGTH:
        print(
            f"The password must be at least {admin_ops.MIN_PASSWORD_LENGTH} characters. Nothing was changed.",
            file=sys.stderr,
        )
        return 2

    migrate_control_plane()
    workspace = ensure_internal_workspace()
    user = next((u for u in control.list_users(workspace.id) if u.email == email), None)
    if user is None:
        user = control.create_user(
            client_id=workspace.id,
            email=email,
            password=password,
            display_name=args.name.strip() or "Platform owner",
            roles=(Role.ADMIN,),
            created_by="create_platform_owner",
        )
        action = "Created"
    else:
        control.set_password(user.id, password)
        if Role.ADMIN not in user.roles:
            control.set_user_roles(user.id, set(user.roles) | {Role.ADMIN}, granted_by="create_platform_owner")
        if user.status != "ACTIVE":
            control.set_user_status(user.id, "ACTIVE")
        action = "Updated the password for"
    control.set_platform_owner(user.id, True)

    print(f"{action} the platform owner {email}.")
    print(f"Sign in with workspace identifier '{OWNER_WORKSPACE_SLUG}', this email and the password you typed.")
    print("Then open Platform owner in the left menu to add new clients.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
