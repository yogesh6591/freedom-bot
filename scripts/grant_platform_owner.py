"""
Grant or revoke the cross-client owner view (M15-20)
====================================================

    python -m scripts.grant_platform_owner --email jeanne@cepoch.example.com --client cepoch
    python -m scripts.grant_platform_owner --email someone@x.com --client cepoch --revoke

Only for Cepoch staff. A client's own ADMIN must never be granted this: the
owner view shows every client's tickets, revenue and risk.
"""

from __future__ import annotations

import argparse
import sys

from bizos.util.dotenv import load_dotenv

load_dotenv()

from bizos.control import store as control  # noqa: E402
from bizos.control.db import migrate_control_plane  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--email", required=True)
    parser.add_argument("--client", required=True, help="workspace slug the user signs in to")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    migrate_control_plane()
    client = control.get_client_by_slug(args.client)
    if not getattr(client.settings, "internal_workspace", False) and not args.revoke:
        print(
            f"Refusing: {client.slug} is a client workspace. Platform owners must sign in "
            "through Cepoch's internal workspace.",
            file=sys.stderr,
        )
        return 2
    user = next((u for u in control.list_users(client.id) if u.email == args.email.casefold()), None)
    if user is None:
        print(f"No user {args.email} in {client.slug}", file=sys.stderr)
        return 1
    control.set_platform_owner(user.id, not args.revoke)
    print(f"{'Revoked' if args.revoke else 'Granted'} owner view for {user.email} ({client.slug})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
