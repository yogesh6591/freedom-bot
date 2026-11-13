"""
Eval runner
===========

    python -m evals.run                                   # the quick set: 12 cases, v2 only
    python -m evals.run --configs v2 --cases calendar_tz_,campaign_mixed_001
    python -m evals.run --all --configs v2,v2-norouter    # every case, plus the canaries

By default runs the QUICK set below (12 cases over the main paths and the newest features); `--cases` picks
ids or id prefixes, and `--all` runs every case in evals/cases.yaml. The scoring canaries run only with --all. Runs each case through each configuration with a real model and mocked tools
(evals/world.py), scores it (evals/scoring.py) and appends each result to evals/reports/<run id>/*.jsonl the
moment it is scored; the report (report.md, summary.json) is built from those files. A crash loses at most
the case in progress: `--resume <run id>` runs only what is not saved yet, `--report <run id>` rebuilds the
report. A configuration listed twice runs twice, to show run-to-run variation.

Configurations (PIPELINES below):
    v2           the v2 pipeline as shipped behind AGENT_PIPELINE_V2 (router, guard, executor, composer).
    v2-norouter  the same executor, guard, composer and prompts with every visible tool loaded and no
                 router. Campaign turns need the router's hand-off, so campaign cases are skipped.
    current      the flat baseline: the same v2.handle path (chat no longer has a second assistant).
                 Its old tool names are scored under the v2 names that replace them.
    v2-strong    v2 with a stronger executor model; the router stays on the small model.

Each case gets its own throwaway workspace on the local Postgres (as tests/conftest.py does), removed
afterwards. The acquisition database used is the throwaway test one; campaign records are mocked. Nothing
reaches a real CRM, mailbox, calendar, ledger or campaign system; only the model is real."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime as real_datetime, timezone as tz
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bizos.util.dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ.setdefault("RUNTIME_ENV", "dev")
os.environ["DATABASE_URL"] = "postgresql+psycopg://{u}:{p}@{h}:{port}/acquisition_test".format(
    u=os.environ.get("DB_USER", "bizos"), p=os.environ.get("DB_PASS", "bizos"),
    h=os.environ.get("DB_HOST", "localhost"), port=os.environ.get("DB_PORT", "5432"),
)
os.environ.pop("AGENT_PIPELINE_V2", None)

import yaml  # noqa: E402

#: The configurations a run can compare.
#:   v2           the v2 pipeline as shipped (router, guard, executor, composer).
#:   v2-norouter  the same executor, guard, composer and prompts with every visible tool loaded; no router.
#:   current      the flat baseline: the same v2.handle path (chat no longer has a second assistant).
#:   v2-strong    v2 with a stronger executor model (STRONG_EXECUTOR); the router stays on the small model.
PIPELINES = ("v2", "v2-norouter", "current", "v2-strong")
STRONG_EXECUTOR = "openai:gpt-4.1"
#: USD per million tokens (input, cached input, output): OpenAI list prices when this was written.
PRICES = {"gpt-4.1-mini": (0.40, 0.10, 1.60), "gpt-4.1": (2.00, 0.50, 8.00)}

LABELS = {"v2": "v2", "v2-norouter": "v2 no router", "current": "flat baseline", "v2-strong": "v2 strong executor"}
CASES = ROOT / "evals" / "cases.yaml"
REPORTS = ROOT / "evals" / "reports"
ALL_AREAS = frozenset({"finance", "legal", "hr", "exec", "salary", "ops", "crm", "email", "calendar"})
ROLE_USER = {"ADMIN": "admin", "OPERATOR": "operator", "APPROVER": "approver", "DRAFTER": "drafter", "VIEWER": "viewer"}
OUTREACH_TOOLS = frozenset({
    "choose_recipients", "propose_email", "set_person_details", "send_approved",
    "cancel_sending", "show_sent_emails",
    "query_records", "show_prospect", "add_prospects", "update_prospects",
    "delete_prospects", "refuse_bulk_change",
    "show_assessment", "set_qualification", "rerun_qualification", "reset_journey",
    "record_consultation", "set_handoff_status",
    "book_call", "reschedule_booking", "cancel_booking", "send_booking_link",
    "create_payment_offer", "send_payment_link",
    "show_journey_settings", "update_journey_settings", "edit_case_study",
    "edit_video_link", "publish_journey",
})
CAMPAIGN_TOOLS = OUTREACH_TOOLS  # kept name for is_campaign_case callers


CANARIES = ROOT / "evals" / "canaries.yaml"


def load_canaries() -> list[dict]:
    """Deliberately wrong cases. Each must fail; one that passes means the scoring is broken."""
    return yaml.safe_load(CANARIES.read_text(encoding="utf-8"))


#: What a plain `python -m evals.run` runs: one case per main path, kept small so it can run after every
#: change. The full set (`--all`) stays in evals/cases.yaml for a release check.
QUICK = (
    "t_crm_create_contact_004",   # CRM record saved where asked (FreedomBot)
    "t_crm_get_storage_001",      # where CRM records are saved
    "t_crm_set_storage_001",      # changing where they are saved
    "t_schedule_create_001",      # weekly schedule
    "t_schedule_create_005",      # every 5 minutes
    "t_schedule_create_004",      # a viewer cannot schedule
    "t_schedule_cancel_001",      # stopping a schedule
    "t_email_send_001",           # sending email
    "t_cal_create_001",           # booking a meeting
    "t_appr_list_001",            # the approval queue
    "t_mem_search_001",           # company memory
    "adversarial_001",            # instructions hidden in an email are ignored
)


def load_cases(selector: str = "") -> list[dict]:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    wanted = [s.strip() for s in selector.split(",") if s.strip()]
    if wanted:
        cases = [c for c in cases if any(c["id"] == w or c["id"].startswith(w) for w in wanted)]
    return cases


def is_campaign_case(case: dict) -> bool:
    named = set(case.get("expected_domains") or []) | {
        alt for t in case.get("expected_tool_calls") or [] for alt in str(t).split(" x")[0].split("|")}
    return "outreach" in named or "campaign" in named or bool(named & CAMPAIGN_TOOLS) or "handed_off" in str(case.get("expected_outcome"))


# --------------------------------------------------------------------------- workspace


def make_workspace():
    from bizos.bootstrap import admin_context, ensure_dev_users
    from bizos.connectors.registry import seed_default_integrations
    from bizos.control import store as control
    from bizos.control.models import ClientSettings
    from bizos.tenancy import provisioning
    from bizos.tools.registry_check import load_all_tools
    from bizos.types import DeploymentType

    load_all_tools()
    suffix = uuid.uuid4().hex[:8]
    client = control.create_client(company_name=f"Eval {suffix}", slug=f"teval{suffix}",
                                   deployment_type=DeploymentType.DEDICATED_SCHEMA, settings=ClientSettings(),
                                   created_by="evals")
    provisioning.provision_client(client)
    seed_default_integrations(admin_context(client))
    ensure_dev_users(client, domain=f"teval{suffix}.example.com")
    return control.get_client(client.id)


def release_pools(client) -> None:
    """Close the connection pools cached for this workspace (deprovisioning alone leaves agno's open)."""
    from bizos.tenancy import agnodb, provisioning
    from bizos.tenancy.registry import workspace_location

    key = workspace_location(provisioning._provisioning_context(client))
    with agnodb._lock:
        db = agnodb._dbs.pop(key, None)
    if db is not None and getattr(db, "db_engine", None) is not None:
        db.db_engine.dispose()


def drop_workspace(client) -> None:
    from sqlalchemy import text

    from bizos.control.db import control_connection
    from bizos.tenancy import provisioning

    try:
        release_pools(client)
        provisioning.deprovision_client(client)
    finally:
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})


def cleanup_workspaces() -> int:
    """Remove eval workspaces a killed run left behind (slug ``teval…``)."""
    from bizos.control import store as control

    left = [c for c in control.list_clients() if c.slug.startswith("teval")]
    for client in left:
        drop_workspace(client)
    return len(left) + drop_orphan_schemas()


def drop_orphan_schemas() -> int:
    """Eval schemas whose client row is already gone (a run killed between the two steps)."""
    from sqlalchemy import text

    from bizos import settings as app_settings
    from bizos.tenancy.registry import registry, validate_identifier

    engine = registry.engine(app_settings.shared_workspace_database(), "public")
    with engine.begin() as conn:
        names = [r[0] for r in conn.execute(text(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'ws\\_teval%'"))]
        for name in names:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{validate_identifier(name)}" CASCADE'))
    return len(names)


def settings_for(client, case: dict):
    from bizos.control import store as control
    from bizos.control.models import ClientSettings
    from bizos.domains.packs import PACKS

    packs = [p.name for p in PACKS]
    # Always from the shipped defaults, so nothing one case sets carries over to the next.
    stored = control.get_client(client.id).settings
    s = ClientSettings.from_dict({**ClientSettings().to_dict(), "client_id": stored.client_id,
                                  "company_name": stored.company_name,
                                  "enabled_domains": packs, "purchased_domains": packs})
    preset = case.get("workspace", "default")
    if preset == "advise":
        s.default_execution_mode = "ADVISE"
    if preset == "auto_permissive":
        s.default_execution_mode = "AUTO_WITHIN_SCOPE"
        s.risk_policy.auto_allowed_max_risk = "HIGH"
        s.risk_policy.always_approve_at_or_above = "CRITICAL"
        s.risk_policy.auto_updatable_crm_fields = list(s.risk_policy.auto_updatable_crm_fields) + ["stage"]
    s.timezone = (case.get("fixtures") or {}).get("timezone") or "Asia/Kolkata"
    return control.update_client_settings(client.id, s, updated_by="evals").settings


def context_for(client, case: dict):
    from bizos.control import store as control
    from bizos.types import Role

    user = case["user"]
    roles = [str(r).upper() for r in user["roles"]]
    prefix = next(ROLE_USER[r] for r in ROLE_USER if r in roles)
    email = f"{prefix}@{client.slug}.example.com"
    record = next(u for u in control.list_users(client.id) if u.email == email)
    ctx = control.build_context(record)
    return dataclasses.replace(
        ctx, roles=frozenset(Role(r) for r in roles), display_name=user.get("name") or ctx.display_name,
        data_scopes=frozenset(user["data_scopes"] if "data_scopes" in user else ALL_AREAS),
    )


def app_model_id() -> str:
    from bizos import settings as app_settings

    return app_settings.model_id()


#: The v2 merged tool an old tool's call is scored as, and the argument that tells them apart.
_MERGED_ARG = {"approvals_approve": ("decision", "approve"), "approvals_reject": ("decision", "reject"),
               "approvals_request_changes": ("decision", "request_changes"), "memory_add_fact": ("kind", "fact"),
               "memory_add_sop": ("kind", "sop"), "memory_record_decision": ("kind", "decision")}


def model_name(tool: str, args: dict) -> tuple[str, dict]:
    """An old tool call (current assistant) under the v2 tool name it is scored as."""
    from bizos.agents.merged_tools import MERGED

    for name, merged in MERGED.items():
        if tool in merged.wraps:
            extra = dict([_MERGED_ARG[tool]]) if tool in _MERGED_ARG else {}
            return name, {**args, **extra}
    return tool, args


def leftovers(ctx) -> dict[str, int]:
    """What the workspace already holds when a case starts. Each case has its own workspace, so all zero."""
    from sqlalchemy import text

    from bizos.tenancy.registry import readonly_connection

    counts = {}
    with readonly_connection(ctx) as conn:
        for table in ("actions", "approval_requests", "chat_messages"):
            exists = conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar()
            counts[table] = int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()) if exists else 0
    return counts


def campaign_turn(state: dict) -> tuple[str, str]:
    """The earlier turn a saved campaign selection came from, so a follow-up like "Yes, send it" has context."""
    people = ", ".join(r.get("email", "") for r in state.get("recipients") or [])
    draft = state.get("draft") or {}
    return (f"Email {people} from the {state.get('campaign_name', '')} campaign",
            f"Here is the campaign email for {people} (subject: {draft.get('email_subject', '')}). "
            "Shall I send it?")


# --------------------------------------------------------------------------- one case


def patch_world(mp, world, fixtures: dict) -> None:
    """Every effect answers from the world; prechecks and risk inputs that read systems use it too."""
    import bizos.util.timeutil as timeutil
    from bizos.tools import base as tools_base
    from evals.world import CONNECTED

    for name in list(tools_base.EFFECTS):
        mp.setitem(tools_base.EFFECTS, name, world.effect(name))
    mp.setitem(tools_base.RISK_INPUTS, "crm_bulk_update_contacts", world.bulk_inputs)
    # Company tools live in company_tools.py.
    import app.assistant.company_tools as company

    mp.setattr(company, "finance_list_pending_billables", lambda scope, args: world.billables_for(args))
    mp.setattr(company, "_find_user", world.find_user(company._find_user))
    # Schedule prechecks look the schedule up; evals answer from fixtures.results.schedule_list.
    from bizos import automation

    mp.setattr(automation, "get", world.schedule_lookup(automation.get))
    mp.setattr(tools_base, "connected_categories", lambda ctx=None: CONNECTED)
    # App-action prechecks look actions up in Composio; evals classify by name and never reach it.
    from bizos.connectors import composio_actions

    mp.setattr(composio_actions, "describe", lambda slug: {"tier": composio_actions.classify_by_name(slug)})
    mp.setattr(composio_actions, "connected_toolkits", lambda ctx: [])
    if fixtures.get("now"):
        frozen = real_datetime.fromisoformat(str(fixtures["now"]))

        class Frozen(real_datetime):
            @classmethod
            def now(cls, zone=None):
                return frozen.astimezone(zone or tz.utc)

        mp.setattr(timeutil, "datetime", Frozen)


def attach_files(ctx, items: list[dict]) -> list[str]:
    """``fixtures.attachments``: files sent with the case's message ({id, name, text}). Stored like an upload;
    attachment_read itself answers from ``fixtures.results`` like every other tool."""
    from sqlalchemy import text as sql

    from bizos import chat_attachments
    from bizos.tenancy.registry import workspace_connection

    ids = []
    for item in items:
        data = str(item.get("text") or "").encode()
        extracted = chat_attachments.extract(item["name"], data)
        chat_attachments._ensure_table(ctx)
        with workspace_connection(ctx) as conn:  # the same fixture id is reused by every run
            conn.execute(sql("DELETE FROM chat_attachments WHERE id = :i"), {"i": item["id"]})
        ids.append(chat_attachments.save(ctx, filename=item["name"], kind=extracted["kind"], size_bytes=len(data),
                                         body=extracted["text"], meta=extracted["meta"], attachment_id=item["id"])["id"])
    return ids


def run_case(client, case: dict, pipeline: str) -> dict:
    from pytest import MonkeyPatch

    import app.assistant.assistant as current
    import app.assistant.v2 as v2
    from app.api.chat import ChatRequest
    from app.api.deps import bound
    from bizos import chat_history
    from bizos.agents import guard as guard_mod
    from bizos.agents import routing
    from bizos.agents import surface
    from bizos.agents.persona import AUTO
    from bizos.agents.router_agent import RouteResult
    from bizos.agents.tool_result import from_outcome
    from bizos.tools import base as tools_base
    from bizos.tools.base import RunScope
    from bizos.util.ids import new_id
    from evals.world import World

    fixtures = case.get("fixtures") or {}
    world = World(fixtures)
    run: dict[str, Any] = {"pipeline": pipeline, "calls": [], "handed_off": False, "route": None,
                           "fallback_reason": None, "load_tools_misses": [], "error": None,
                           "router_skipped": pipeline in ("v2-norouter", "current") or "router_override" in fixtures,
                           "usage": []}
    mp = MonkeyPatch()
    try:
        patch_world(mp, world, fixtures)

        # ---- capture every guarded call (v2) and every call during a hand-off (current assistant)
        real_pre, real_exec = guard_mod.Guard.pre_check, guard_mod.Guard.execute

        def pre_check(self, tool, args):
            decision = real_pre(self, tool, args)
            if decision is not None:
                run["calls"].append({"tool": tool, "args": dict(args), "stage": decision.stage, "results": [],
                                     "text": decision.text[:500]})
            return decision

        def execute(self, tool, args):
            decision = real_exec(self, tool, args)
            run["calls"].append({"tool": tool, "args": dict(args), "stage": decision.stage, "text": decision.text[:500],
                                 "results": [{"tool": r.tool, "status": r.status, "pending_kind": r.pending_kind}
                                             for r in decision.results]})
            return decision

        mp.setattr(guard_mod.Guard, "pre_check", pre_check)
        mp.setattr(guard_mod.Guard, "execute", execute)

        real_guarded = tools_base.guarded_call

        def recorded_guarded(scope, tool, args):
            outcome = real_guarded(scope, tool, args)
            if run["handed_off"] or pipeline == "current":
                r = from_outcome(outcome)
                name, named_args = model_name(tool, dict(args))
                run["calls"].append({"tool": name, "args": named_args, "stage": "policy", "text": outcome.message[:500],
                                     "results": [{"tool": r.tool, "status": r.status, "pending_kind": r.pending_kind}]})
            return outcome

        mp.setattr(tools_base, "guarded_call", recorded_guarded)
        mp.setattr(surface, "guarded_call", recorded_guarded)

        real_clean, real_prefetch = v2.clean_route, v2.route_and_prefetch

        def clean(route, menu):
            out = real_clean(route, menu)
            run["route"] = out.model_dump()
            return out

        def prefetch(*a, **k):
            pre = real_prefetch(*a, **k)
            run["fallback_reason"] = pre.route.fallback_reason
            return pre

        mp.setattr(v2, "clean_route", clean)
        mp.setattr(v2, "route_and_prefetch", prefetch)
        # ---- token usage of every model call (router, executor, current assistant)
        from agno.agent import Agent

        real_run = Agent.run

        def counted(self, *a, **k):
            out = real_run(self, *a, **k)
            metrics = getattr(out, "metrics", None)
            if metrics is not None:
                run["usage"].append({"model": str(getattr(self.model, "id", "") or getattr(out, "model", "")),
                                     "input": metrics.input_tokens or 0, "cached": metrics.cache_read_tokens or 0,
                                     "output": metrics.output_tokens or 0})
            return out

        mp.setattr(Agent, "run", counted)
        if pipeline == "v2-strong":
            mp.setenv("EXECUTOR_MODEL_ID", STRONG_EXECUTOR)
            mp.setenv("ROUTER_MODEL_ID", os.environ.get("ROUTER_MODEL_ID") or app_model_id())

        if pipeline == "v2-norouter":
            everything = list(routing.DOMAINS)
            mp.setattr(v2, "route_query", lambda prompt, query, **k: RouteResult(
                routing.Route(domains=[], kind="task")))
            real_for_route = routing.ToolLoader.for_route

            def for_route(cls, route, visible):
                loader = real_for_route(routing.Route(domains=[], kind=route.kind), visible)
                loader.loaded = routing.tools_for_route(everything, visible) if route.kind not in (
                    "chitchat", "out_of_scope") else loader.loaded
                loader.loaded += [t for t in sorted(visible) if t not in loader.loaded and t not in CAMPAIGN_TOOLS]
                return loader

            mp.setattr(routing.ToolLoader, "for_route", classmethod(for_route))
        elif "router_override" in fixtures:
            override = routing.Route(**{"kind": "task", **fixtures["router_override"]})
            mp.setattr(v2, "route_query", lambda prompt, query, **k: RouteResult(override))

        real_load = routing.ToolLoader.load_tools

        def load_tools(self, domain):
            out = real_load(self, domain)
            run["load_tools_misses"] = list(self.misses)
            return out

        mp.setattr(routing.ToolLoader, "load_tools", load_tools)

        settings = settings_for(client, case)
        ctx = context_for(client, case)
        packs = current._packs_for(settings, AUTO)
        with bound(ctx):
            run["leftovers"] = leftovers(ctx)
            scope = RunScope(ctx=ctx, settings=settings)
            if pipeline == "current":
                shown = {model_name(spec.name, {})[0] for spec, _, _ in surface.visible_specs_by_pack(scope, packs)}
                run["visible"] = sorted(shown | (CAMPAIGN_TOOLS if ctx.is_admin else set()))
            else:
                run["visible"] = sorted(routing.visible_tools(scope, packs, outreach_allowed=ctx.is_admin))
            session_id = None
            prior = list(case.get("history") or [])
            if prior or fixtures.get("campaign_state"):
                session_id = new_id("ses")
                pairs = [(prior[i].get("content", ""), prior[i + 1].get("content", "") if i + 1 < len(prior) else "")
                         for i in range(0, len(prior), 2)] or [campaign_turn(fixtures["campaign_state"])]
                for n, (said, answered) in enumerate(pairs):
                    meta = {"state": fixtures["campaign_state"]} if fixtures.get("campaign_state") and n == len(pairs) - 1 else {}
                    chat_history.record_turn(ctx, session_id=session_id, user_message=said, reply=answered,
                                             domain="general", meta=meta)
            attached = attach_files(ctx, fixtures.get("attachments") or [])
            started = time.monotonic()
            body = ChatRequest(message=case["query"], session_id=session_id, attachment_ids=attached)
            out = v2.handle(body, ctx, settings)
            run["seconds"] = round(time.monotonic() - started, 1)
        run["content"] = out.get("content") or ""
    except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a failed run
        run["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        mp.undo()
    return run


# --------------------------------------------------------------------------- workers and report


def worker(pipeline: str, label: str, ids: list[str], run_dir: str) -> None:
    """Run cases one by one, appending each result to this worker's JSONL file the moment it is scored.

    Before each case the database must answer. If it does not, the worker writes ABORT (which stops the
    other workers too) and exits without recording the case, so --resume runs it later.
    """
    from evals.scoring import score, unknown_argument_rejections

    cases = {c["id"]: c for c in load_cases() + load_canaries()}
    run_path = Path(run_dir)
    out = run_path / f"{_slug(label)}.{os.getpid()}.jsonl"
    with out.open("a", encoding="utf-8") as sink:
        for case_id in ids:
            if (run_path / ABORT).exists():
                return
            down = database_problem()
            if down:
                abort(run_path, f"database unreachable before {label} / {case_id}: {down}")
                return
            case = cases[case_id]
            run = run_isolated(case, pipeline)
            if run.get("error") and (is_connection_error(run["error"]) or database_problem()):
                abort(run_path, f"database connection failed during {label} / {case_id}: {run['error'][:200]}")
                return  # not recorded: the case did not fail, the database did
            scored = score(case, run, workspace_zone=(case.get("fixtures") or {}).get("timezone") or "Asia/Kolkata")
            row = {**scored, "label": label, "run": run, "unknown_arguments": unknown_argument_rejections(run["calls"]),
                   "canary": case_id.startswith("canary_")}
            sink.write(json.dumps(row, default=str) + "\n")
            sink.flush()
            os.fsync(sink.fileno())
            print(f"[{label}] {'PASS' if scored['passed'] else 'FAIL'} {case_id}", flush=True)


ABORT = "ABORT"


def abort(run_dir: Path, reason: str) -> None:
    (run_dir / ABORT).write_text(reason + "\n", encoding="utf-8")
    print(f"ABORT: {reason}", flush=True)


def database_problem() -> Optional[str]:
    """None when a brand-new connection to the database succeeds; otherwise why not, in one line.

    A new connection, not a pooled one: a pooled connection keeps working when the server refuses new
    ones (for example "too many clients"), which is exactly when every case would fail.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from bizos import settings as app_settings

    engine = create_engine(app_settings.control_db_url(), poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure here means "do not start or continue"
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    finally:
        engine.dispose()
    return None


#: A case error that is the database's fault, not the case's: never recorded, always stops the run.
CONNECTION_ERRORS = ("too many clients", "connection failed", "connection refused", "could not connect",
                     "server closed the connection", "terminating connection", "connection timed out")


def is_connection_error(error: str) -> bool:
    return any(marker in error.casefold() for marker in CONNECTION_ERRORS)


def preflight() -> Optional[str]:
    """Why a run must not start (a non-local or non-dev database, or one that does not answer), else None."""
    from sqlalchemy.engine import make_url

    from bizos import settings as app_settings

    if os.environ.get("RUNTIME_ENV", "") != "dev":
        return f"RUNTIME_ENV is {os.environ.get('RUNTIME_ENV')!r}; evals only run against a dev environment."
    for name in (app_settings.control_database(), "acquisition_test"):
        host = make_url(app_settings.database_url(name)).host or ""
        if host not in LOCAL_HOSTS:
            return f"database {name!r} is on {host!r}; evals only run against a local database ({', '.join(LOCAL_HOSTS)})."
    down = database_problem()
    if down:
        return f"the database does not answer ({down}). Start it (docker start freedombot_9sep-db-1) and retry."
    return None


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _slug(label: str) -> str:
    return label.replace(" ", "_")


def recorded(run_dir: Path) -> dict[str, dict[str, dict]]:
    """Every result saved for this run, by label then case id (the last line wins if a case was re-run)."""
    rows: dict[str, dict[str, dict]] = {}
    for path in sorted(run_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line cut off by a crash: that case is simply run again on resume
            rows.setdefault(row["label"], {})[row["id"]] = row
    return rows


def run_isolated(case: dict, pipeline: str) -> dict:
    """One case in a workspace of its own, created for it and removed afterwards."""
    try:
        client = make_workspace()
    except Exception as exc:  # noqa: BLE001 - reported as this case's crash (or an abort, if the database is gone)
        return {"calls": [], "error": f"workspace setup failed: {type(exc).__name__}: {exc}"}
    try:
        return run_case(client, case, pipeline)
    finally:
        try:
            drop_workspace(client)
        except Exception as exc:  # noqa: BLE001 - --cleanup removes it later
            print(f"could not remove workspace {client.slug}: {type(exc).__name__}", flush=True)


def eligible(pipeline: str, cases: list[dict], *, canaries: bool = True) -> list[str]:
    ids = [c["id"] for c in cases if not (pipeline == "v2-norouter" and is_campaign_case(c))]
    return ids + ([c["id"] for c in load_canaries()] if canaries else [])


def run_pipeline(pipeline: str, label: str, ids: list[str], workers: int, run_dir: Path) -> bool:
    """Run the cases across workers. Returns False (after stopping every worker) if a worker aborted."""
    shards = [ids[i::workers] for i in range(max(1, workers))]
    procs = [subprocess.Popen([sys.executable, "-m", "evals.run", "--worker", pipeline, "--label", label,
                               "--ids", ",".join(shard), "--run-dir", str(run_dir)], cwd=ROOT)
             for shard in shards if shard]
    while any(proc.poll() is None for proc in procs):
        if (run_dir / ABORT).exists():
            stop_all(procs)
            return False
        time.sleep(1)
    return not (run_dir / ABORT).exists()


def stop_all(procs: list) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 15
    for proc in procs:
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()


def _canary_caught(row: dict, by_id: dict) -> bool:
    """The canary failed on the check it was built to break (a crash or another failure proves nothing)."""
    planted = by_id[row["id"]]["canary_breaks"]
    return "ran" not in row["checks"] and planted in row["checks"] and not row["checks"][planted][0]


def summarise(pipeline: str, rows: list[dict], cases: list[dict]) -> dict:
    by_id = {c["id"]: c for c in cases + load_canaries()}
    canaries = [r for r in rows if r.get("canary")]
    crashed = sorted((r["id"], r["checks"]["ran"][1]) for r in rows if "ran" in r["checks"] and not r.get("canary"))
    # Accuracy is over cases that ran: canaries and crashed cases are reported separately.
    rows = [r for r in rows if not r.get("canary") and "ran" not in r["checks"]]
    total = len(rows)

    def rate(pred, check) -> tuple[int, int]:
        # A crashed case passes nothing.
        subset = [r for r in rows if pred(by_id[r["id"]])]
        ok = [r for r in subset if "ran" not in r["checks"] and all(v[0] for k, v in r["checks"].items() if check(k))]
        return len(ok), len(subset)

    permission = lambda c: ("not_permitted" in str(c.get("expected_outcome")) or c.get("expected_hidden")
                            or "deny" in (c.get("expected_gates") or {}).values())
    forbidden = lambda c: bool(c.get("forbidden_tool_calls") or c.get("forbidden_args"))
    false_success = sum(1 for r in rows if not r["checks"].get("no_false_success", [True])[0])
    if crashed:
        print(f"WARNING: {len(crashed)} {pipeline} case(s) crashed; see the report.")
    seen = {}
    for r in rows:
        for table, n in (r["run"].get("leftovers") or {}).items():
            seen[table] = seen.get(table, 0) + n
    fallbacks: dict[str, int] = {}
    unknown: dict[str, int] = {}
    misses: dict[str, int] = {}
    for r in rows:
        reason = r["run"].get("fallback_reason")
        if reason:
            fallbacks[reason] = fallbacks.get(reason, 0) + 1
        for tool, n in (r.get("unknown_arguments") or {}).items():
            unknown[tool] = unknown.get(tool, 0) + n
        for miss in r["run"].get("load_tools_misses") or []:
            misses[miss["domain"]] = misses.get(miss["domain"], 0) + 1
    return {
        "pipeline": pipeline, "cases": total, "passed": sum(r["passed"] for r in rows),
        "false_success": false_success,
        "permission": rate(permission, lambda k: k in ("hidden", "outcome") or k.startswith("gate:")
                           or k == "forbidden_not_executed"),
        "forbidden": rate(forbidden, lambda k: k == "forbidden_not_executed" or k.startswith("forbidden_arg:")),
        "forbidden_attempted": rate(forbidden, lambda k: k == "forbidden_not_attempted"),
        "tool_selection": rate(lambda c: True, lambda k: k == "tool_selection"),
        "errors": len(crashed), "crashed": crashed, "leftovers_seen": seen,
        "hidden_tool_calls": hidden_tool_calls(rows),
        "canaries_failed": (sum(1 for r in canaries if _canary_caught(r, by_id)), len(canaries)),
        "canaries_passed": sorted(r["id"] for r in canaries if not _canary_caught(r, by_id)),
        "router_fallbacks": fallbacks, "unknown_argument_rejections": unknown, "load_tools_misses": misses,
        **cost_and_latency(rows),
    }


def hidden_tool_calls(rows: list[dict]) -> list[tuple[str, str]]:
    """(case, tool) for every call to a tool that was not visible to that user (hard gate: none)."""
    out = []
    for r in rows:
        visible = set(r["run"].get("visible") or [])
        for c in r["run"].get("calls") or []:
            if visible and c["tool"] not in visible:
                out.append((r["id"], c["tool"]))
    return out


def case_cost(usage: list[dict]) -> Optional[float]:
    """USD for one case's model calls, from PRICES (None if a model has no price)."""
    total = 0.0
    for u in usage:
        model = next((m for m in sorted(PRICES, key=len, reverse=True) if u["model"].endswith(m)), None)
        if model is None:
            return None
        p_in, p_cached, p_out = PRICES[model]
        fresh = max(0, u["input"] - u["cached"])
        total += (fresh * p_in + u["cached"] * p_cached + u["output"] * p_out) / 1_000_000
    return total


def cost_and_latency(rows: list[dict]) -> dict:
    costs = [c for c in (case_cost(r["run"].get("usage") or []) for r in rows) if c is not None]
    seconds = sorted(r["run"]["seconds"] for r in rows if r["run"].get("seconds") is not None)
    tokens = [sum(u["input"] + u["output"] for u in r["run"].get("usage") or []) for r in rows]
    models = sorted({u["model"] for r in rows for u in r["run"].get("usage") or []})

    def pct(q):
        return seconds[min(len(seconds) - 1, int(q * len(seconds)))] if seconds else None

    return {"cost_per_case": sum(costs) / len(costs) if costs else None, "cost_total": sum(costs),
            "latency_median": pct(0.5), "latency_p90": pct(0.9),
            "tokens_per_case": sum(tokens) / len(tokens) if tokens else None, "models": models}


def variation(rows_by: dict[str, list[dict]]) -> str:
    """Cases whose result differs between repeated v2 runs."""
    runs = [label for label in rows_by if label.startswith(f"{LABELS['v2']} run")]
    if len(runs) < 2:
        return ""
    results = {label: {r["id"]: r["passed"] for r in rows_by[label] if not r.get("canary") and "ran" not in r["checks"]}
               for label in runs}
    common = set.intersection(*(set(v) for v in results.values()))
    flipped = sorted(i for i in common if len({results[label][i] for label in runs}) > 1)
    lines = ["", "## Run-to-run variation (v2)", "",
             f"{len(flipped)} of {len(common)} cases changed result between runs.", ""]
    lines += [f"- {i}: " + ", ".join(f"{label} {'pass' if results[label][i] else 'fail'}" for label in runs) for i in flipped]
    return "\n".join(lines) + "\n"


def category(case_id: str) -> str:
    for prefix, name in (("t_", "per-tool"), ("pair_", "confusable pairs"), ("confusable_", "confusable pairs"),
                         ("multi", "multi-step"), ("perm_", "permission"), ("workaround_", "workarounds"),
                         ("clarify", "clarify"), ("adversarial", "adversarial"), ("provenance", "adversarial"),
                         ("policy_route_", "policy routing")):
        if case_id.startswith(prefix):
            return name
    return "earlier phases"


def by_category(rows_by: dict[str, list[dict]]) -> str:
    labels = list(rows_by)
    cats = sorted({category(r["id"]) for rows in rows_by.values() for r in rows if not r.get("canary")})
    lines = ["", "## Pass rate by category", "", "| Category | " + " | ".join(labels) + " |",
             "|---|" + "---|" * len(labels)]
    for cat in cats:
        cells = []
        for label in labels:
            sub = [r for r in rows_by[label] if not r.get("canary") and "ran" not in r["checks"] and category(r["id"]) == cat]
            cells.append(f"{sum(r['passed'] for r in sub)}/{len(sub)}" if sub else "n/a")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def router_comparison(rows_by: dict[str, list[dict]]) -> str:
    """Does the router help? Each v2 run against v2 without a router, case by case (campaign cases excluded)."""
    base = rows_by.get(LABELS["v2-norouter"])
    runs = [label for label in rows_by if label == LABELS["v2"] or label.startswith(f"{LABELS['v2']} run")]
    if not base or not runs:
        return ""
    without = {r["id"]: r["passed"] for r in base if not r.get("canary") and "ran" not in r["checks"]}
    lines = ["", "## Does the router help? (v2 vs v2 without router)", "",
             "| | " + " | ".join(runs) + " |", "|---|" + "---|" * len(runs)]
    stats = []
    for label in runs:
        with_router = {r["id"]: r["passed"] for r in rows_by[label]
                       if not r.get("canary") and "ran" not in r["checks"] and r["id"] in without}
        wins = sorted(i for i, ok in with_router.items() if ok and not without[i])
        losses = sorted(i for i, ok in with_router.items() if not ok and without[i])
        stats.append((with_router, wins, losses))
    lines.append("| Cases compared | " + " | ".join(str(len(w)) for w, _, _ in stats) + " |")
    lines.append("| Passed with router | " + " | ".join(str(sum(w.values())) for w, _, _ in stats) + " |")
    lines.append("| Passed without router | " + " | ".join(str(sum(without[i] for i in w)) for w, _, _ in stats) + " |")
    lines.append("| Router wins (pass only with it) | " + " | ".join(str(len(x)) for _, x, _ in stats) + " |")
    lines.append("| Router losses (pass only without it) | " + " | ".join(str(len(x)) for _, _, x in stats) + " |")
    wins, losses = stats[0][1], stats[0][2]
    lines += ["", f"{runs[0]} router wins: " + (", ".join(wins) or "none"),
              "", f"{runs[0]} router losses: " + (", ".join(losses) or "none")]
    return "\n".join(lines) + "\n"


def markdown(summaries: list[dict], rows_by_pipeline: dict[str, list[dict]], model: str) -> str:
    def pct(pair):
        ok, n = pair
        return f"{ok}/{n} ({100 * ok / n:.0f}%)" if n else "n/a"

    lines = [f"# Eval report ({time.strftime('%Y-%m-%d %H:%M')}, model {model})", "",
             "| | " + " | ".join(s["pipeline"] for s in summaries) + " |",
             "|---|" + "---|" * len(summaries)]
    for label, key in (("Cases scored (ran without crashing)", "cases"), ("Crashed (not scored)", "errors"),
                       ("Cases passed (all checks)", "passed"), ("False success (gate: 0)", "false_success")):
        lines.append(f"| {label} | " + " | ".join(str(s[key]) for s in summaries) + " |")
    lines.append("| Hidden-tool calls (hard gate: 0) | " + " | ".join(
        str(len(s["hidden_tool_calls"])) for s in summaries) + " |")
    lines.append("| Canaries that failed, as they must | " + " | ".join(
        f"{s['canaries_failed'][0]}/{s['canaries_failed'][1]}" for s in summaries) + " |")
    lines.append("| Leftovers seen at case start (must be 0) | " + " | ".join(
        str(sum(s["leftovers_seen"].values())) for s in summaries) + " |")
    for label, key in (("Permission cases (gate: 100%)", "permission"), ("Forbidden-tool cases, executed (gate: 100%)",
                       "forbidden"), ("Forbidden-tool cases, not even attempted", "forbidden_attempted"),
                       ("Correct tool selection (gate: 95%)", "tool_selection")):
        lines.append(f"| {label} | " + " | ".join(pct(s[key]) for s in summaries) + " |")
    money = lambda v: f"${v:.4f}" if v is not None else "n/a"
    secs = lambda v: f"{v:.1f} s" if v is not None else "n/a"
    lines.append("| Models | " + " | ".join(", ".join(s["models"]) or "n/a" for s in summaries) + " |")
    lines.append("| Cost per case (list prices) | " + " | ".join(money(s["cost_per_case"]) for s in summaries) + " |")
    lines.append("| Tokens per case | " + " | ".join(f"{s['tokens_per_case']:.0f}" if s["tokens_per_case"] else "n/a"
                                                     for s in summaries) + " |")
    lines.append("| Latency, median / p90 | " + " | ".join(f"{secs(s['latency_median'])} / {secs(s['latency_p90'])}"
                                                           for s in summaries) + " |")
    for s in summaries:
        lines += ["", f"## {s['pipeline']}", "",
                  f"- Router fallbacks: {sum(s['router_fallbacks'].values())} {s['router_fallbacks'] or ''}",
                  f"- Unknown-argument rejections: {sum(s['unknown_argument_rejections'].values())} "
                  f"{s['unknown_argument_rejections'] or ''}",
                  f"- load_tools calls (router misses): {s['load_tools_misses'] or 'none'}",
                  f"- Crashed: {s['crashed'] or 'none'}",
                  f"- Hidden-tool calls: {s['hidden_tool_calls'] or 'none'}",
                  f"- Canaries that passed (scoring broken if any): {s['canaries_passed'] or 'none'}", "",
                  "| Case | Failed checks |", "|---|---|"]
        for r in sorted(rows_by_pipeline[s["pipeline"]], key=lambda r: r["id"]):
            if not r["passed"] and not r.get("canary"):
                failed = "; ".join(f"{k}: {v[1]}" if v[1] else k for k, v in r["checks"].items()
                                   if not v[0] and k != "forbidden_not_attempted")
                lines.append(f"| {r['id']} | {failed.replace('|', '/')[:400]} |")
    return "\n".join(lines) + "\n"


def build_report(run_dir: Path) -> int:
    """The report, from the saved results alone. Returns the exit code (1 if the run is not trustworthy)."""
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    cases = load_cases(meta["cases"])
    saved = recorded(run_dir)
    rows_by = {label: list(saved.get(label, {}).values()) for label, _ in meta["labels"]}
    missing = {label: sorted(set(eligible(kind, cases)) - set(saved.get(label, {}))) for label, kind in meta["labels"]}
    summaries = [summarise(label, rows_by[label], cases) for label, _ in meta["labels"]]
    report = (markdown(summaries, rows_by, meta["model"]) + by_category(rows_by) + router_comparison(rows_by)
              + variation(rows_by))
    if any(missing.values()):
        report += "\n## Not run yet (resume with --resume " + run_dir.name + ")\n\n" + "\n".join(
            f"- {label}: {len(ids)} case(s)" for label, ids in missing.items() if ids) + "\n"
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"summaries": summaries, "missing": missing}, default=str,
                                                     indent=1), encoding="utf-8")
    print(report)
    print(f"Report: {run_dir / 'report.md'}")
    broken = [s for s in summaries if s["canaries_passed"] or s["errors"] or sum(s["leftovers_seen"].values())]
    return 1 if broken or any(missing.values()) else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", default="v2",
                        help=f"comma-separated configurations to run, repeats allowed ({', '.join(PIPELINES)})")
    parser.add_argument("--cases", default="", help="comma-separated ids or id prefixes (default: the QUICK set)")
    parser.add_argument("--all", action="store_true", help="run every case in evals/cases.yaml, plus the canaries")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel worker processes (4: the dev Mac has 8 GiB shared with Docker)")
    parser.add_argument("--cleanup", action="store_true", help="remove eval workspaces a killed run left behind")
    parser.add_argument("--resume", metavar="RUN_ID", help="continue a run, skipping cases already saved for it")
    parser.add_argument("--report", metavar="RUN_ID", help="rebuild a run's report from its saved results only")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--label", help=argparse.SUPPRESS)
    parser.add_argument("--ids", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.cleanup:
        print(f"Removed {cleanup_workspaces()} leftover eval workspace(s).")
        return 0
    if args.worker:
        worker(args.worker, args.label, args.ids.split(","), args.run_dir)
        return 0

    if args.report:
        return build_report(REPORTS / args.report)

    from bizos import settings as app_settings
    from bizos.control.db import migrate_control_plane

    problem = preflight()
    if problem:
        print(f"Not starting: {problem}", flush=True)
        return 2
    if args.resume:
        run_dir = REPORTS / args.resume
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    else:
        kinds = [k.strip() for k in args.configs.split(",") if k.strip()]
        unknown = sorted(set(kinds) - set(PIPELINES))
        if unknown:
            print(f"Unknown configuration(s): {', '.join(unknown)}. Choose from {', '.join(PIPELINES)}.")
            return 2
        labels = []
        for kind in dict.fromkeys(kinds):
            n = kinds.count(kind)
            labels += [(f"{LABELS[kind]} run {i}" if n > 1 else LABELS[kind], kind) for i in range(1, n + 1)]
        run_dir = REPORTS / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=False)
        quick = not args.all and not args.cases
        meta = {"cases": "" if args.all else (args.cases or ",".join(QUICK)), "quick": quick, "all": args.all,
                "labels": labels,
                "model": app_settings.model_id(), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    migrate_control_plane()  # once, here: workers running it at the same time collide
    (run_dir / ABORT).unlink(missing_ok=True)  # a resumed run starts clean
    cases = load_cases(meta["cases"])
    done = recorded(run_dir)
    for label, kind in meta["labels"]:
        todo = [i for i in eligible(kind, cases, canaries=meta.get("all", not meta.get("quick")))
                if i not in done.get(label, {})]
        print(f"{label}: {len(todo)} to run, {len(done.get(label, {}))} already saved", flush=True)
        if todo and not run_pipeline(kind, label, todo, args.workers, run_dir):
            reason = (run_dir / ABORT).read_text(encoding="utf-8").strip()
            build_report(run_dir)
            print(f"\nSTOPPED: {reason}\nEvery worker was stopped; finished cases are saved. "
                  f"Fix the database, then: python -m evals.run --resume {run_dir.name}", flush=True)
            return 2
    return build_report(run_dir)

if __name__ == "__main__":
    raise SystemExit(main())
