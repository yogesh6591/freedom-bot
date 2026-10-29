"""Dump the exact merged system prompt a real admin chat turn would send.

Run from the project root:

    .venv/bin/python scripts/dump_prompt.py

Writes prompt_full.txt. Not imported by the app.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bizos.util.dotenv import load_dotenv

load_dotenv()

from app.api.deps import bound, run_scope  # noqa: E402
from app.assistant.assistant import _packs_for  # noqa: E402
from app.assistant.v2 import _executor_prompt  # noqa: E402
from bizos.agents import grounding  # noqa: E402
from bizos.agents.persona import AUTO  # noqa: E402
from bizos.agents.routing import (  # noqa: E402
    Route,
    ToolLoader,
    render_playbooks,
    visible_tools,
)
from bizos.agents.tool_meta import TOOL_META  # noqa: E402
from bizos.control import store as control  # noqa: E402
from bizos import settings as app_settings  # noqa: E402
from bizos.types import Role  # noqa: E402
from bizos.util.timeutil import workspace_zone  # noqa: E402

OUT = ROOT / "prompt_full.txt"
MESSAGE = "hello"


def _fail(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 1


def _workspace():
    slug = app_settings.pinned_client_slug() or "acme"
    try:
        return control.get_client_by_slug(slug)
    except control.ClientNotFound:
        names = ", ".join(c.slug for c in control.list_clients()) or "(none)"
        raise SystemExit(
            _fail(
                f"No workspace {slug!r}. Set CLIENT_SLUG or bootstrap the demo client. "
                f"Known slugs: {names}"
            )
        )


def _admin(client):
    users = control.list_users(client.id)
    admins = [u for u in users if Role.ADMIN in u.roles]
    if not admins:
        raise SystemExit(_fail(f"No admin user in workspace {client.slug}."))
    return next((u for u in admins if (u.email or "").startswith("admin@")), admins[0])


def main() -> int:
    client = _workspace()
    user = _admin(client)
    ctx = control.build_context(user)
    settings = control.get_client(client.id).settings

    with bound(ctx):
        scope = run_scope(ctx, settings, domain="general", session_id="ses_dump_prompt")
        packs = _packs_for(settings, AUTO)
        grounded = grounding.analyze(ctx, MESSAGE, settings.enabled_domains)
        visible = visible_tools(scope, packs, outreach_allowed=True)
        loader = ToolLoader.for_route(Route(domains=["outreach"], kind="task"), visible)
        playbooks = render_playbooks(
            sorted({TOOL_META[t].domain for t in loader.loaded if t in TOOL_META}),
            visible,
            timezone=workspace_zone(settings).key,
        )
        prompt = _executor_prompt(
            ctx,
            settings,
            memory_context="(nothing relevant recorded)",
            playbooks=playbooks,
            grounded=grounded.text,
        )

    names = list(loader.loaded)
    role = ", ".join(sorted(str(r) for r in ctx.roles))
    header = (
        f"time: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"user: {user.email}\n"
        f"role: {role}\n"
        f"workspace: {client.slug}\n"
        f"tools: {', '.join(names)}\n"
        f"---\n"
    )
    OUT.write_text(header + prompt, encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
