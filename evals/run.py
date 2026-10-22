"""
Eval runner
===========

    python -m evals.run --pipeline both --workers 6
    python -m evals.run --pipeline v2 --cases calendar_tz_,campaign_mixed_001

Runs every case in evals/cases.yaml through a pipeline with a real model and mocked tools (evals/world.py),
scores it (evals/scoring.py) and writes a JSON and a Markdown report to evals/reports/.

Pipelines:
    v2    the v2 pipeline as shipped behind AGENT_PIPELINE_V2 (router, guard, executor, composer).
    flat  the baseline: the same executor, guard and composer with every visible tool loaded and no router.
          Campaign turns cannot run without the router's hand-off, so campaign cases are skipped.

Each worker process gets its own throwaway workspace on the local Postgres (as tests/conftest.py does) and
removes it afterwards. The acquisition database used is the throwaway test one; campaign records are
mocked. Nothing reaches a real CRM, mailbox, calendar, ledger or campaign system; only the model is real.
"""

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

CASES = ROOT / "evals" / "cases.yaml"
REPORTS = ROOT / "evals" / "reports"
ALL_AREAS = frozenset({"finance", "legal", "hr", "exec", "salary", "ops"})
ROLE_USER = {"ADMIN": "admin", "OPERATOR": "operator", "APPROVER": "approver", "DRAFTER": "drafter", "VIEWER": "viewer"}
CAMPAIGN_TOOLS = frozenset({"update_selection", "show_contacts", "show_campaigns", "propose_email", "approve_move",
                            "send_approved"})


def load_cases(selector: str = "") -> list[dict]:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    wanted = [s.strip() for s in selector.split(",") if s.strip()]
    if wanted:
        cases = [c for c in cases if any(c["id"] == w or c["id"].startswith(w) for w in wanted)]
    return cases


def is_campaign_case(case: dict) -> bool:
    named = set(case.get("expected_domains") or []) | {str(t).split(" x")[0] for t in case.get("expected_tool_calls") or []}
    return "campaign" in named or bool(named & CAMPAIGN_TOOLS) or "handed_off" in str(case.get("expected_outcome"))


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


def drop_workspace(client) -> None:
    from sqlalchemy import text

    from bizos.control.db import control_connection
    from bizos.tenancy import provisioning

    try:
        provisioning.deprovision_client(client)
    finally:
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})


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
        data_scopes=frozenset(user.get("data_scopes") or ALL_AREAS),
    )


def campaign_turn(state: dict) -> tuple[str, str]:
    """The earlier turn a saved campaign selection came from, so a follow-up like "Yes, send it" has context."""
    people = ", ".join(r.get("email", "") for r in state.get("recipients") or [])
    draft = state.get("draft") or {}
    return (f"Email {people} from the {state.get('campaign_name', '')} campaign",
            f"Here is the campaign email for {people} (subject: {draft.get('email_subject', '')}). "
            "Shall I send it?")


# --------------------------------------------------------------------------- one case


def run_case(client, case: dict, pipeline: str) -> dict:
    from pytest import MonkeyPatch

    import app.assistant.assistant as current
    import app.assistant.v2 as v2
    import bizos.util.timeutil as timeutil
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
    from evals.world import CONNECTED, World

    fixtures = case.get("fixtures") or {}
    world = World(fixtures)
    run: dict[str, Any] = {"pipeline": pipeline, "calls": [], "handed_off": False, "route": None,
                           "fallback_reason": None, "load_tools_misses": [], "error": None,
                           "router_skipped": pipeline == "flat" or "router_override" in fixtures}
    mp = MonkeyPatch()
    try:
        for name in list(tools_base.EFFECTS):
            mp.setitem(tools_base.EFFECTS, name, world.effect(name))
        mp.setitem(tools_base.RISK_INPUTS, "crm_bulk_update_contacts", world.bulk_inputs)
        mp.setattr(current, "finance_list_pending_billables", lambda scope, args: world.billables_for(args))
        mp.setattr(current, "known_records", lambda ctx=None: world.campaign_records())
        mp.setattr(current, "_find_user", world.find_user(current._find_user))
        mp.setattr(tools_base, "connected_categories", lambda ctx=None: CONNECTED)

        if fixtures.get("now"):
            frozen = real_datetime.fromisoformat(str(fixtures["now"]))

            class Frozen(real_datetime):
                @classmethod
                def now(cls, zone=None):
                    return frozen.astimezone(zone or tz.utc)

            mp.setattr(timeutil, "datetime", Frozen)

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
            if run["handed_off"]:
                r = from_outcome(outcome)
                run["calls"].append({"tool": tool, "args": dict(args), "stage": "policy", "text": outcome.message[:500],
                                     "results": [{"tool": r.tool, "status": r.status, "pending_kind": r.pending_kind}]})
            return outcome

        mp.setattr(tools_base, "guarded_call", recorded_guarded)
        mp.setattr(surface, "guarded_call", recorded_guarded)

        real_reply = current.reply

        def handed(body, ctx, settings):
            run["handed_off"] = True
            return real_reply(body, ctx, settings)

        mp.setattr(current, "reply", handed)

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
        if pipeline == "flat":
            everything = [d for d in routing.DOMAINS if d != "campaign"]
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
            run["visible"] = sorted(routing.visible_tools(RunScope(ctx=ctx, settings=settings), packs,
                                                          campaign_allowed=ctx.is_admin))
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
            started = time.monotonic()
            out = v2.handle(ChatRequest(message=case["query"], session_id=session_id), ctx, settings)
            run["seconds"] = round(time.monotonic() - started, 1)
        run["content"] = out.get("content") or ""
    except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a failed run
        run["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        mp.undo()
    return run


# --------------------------------------------------------------------------- workers and report


def worker(pipeline: str, ids: list[str], out_path: str) -> None:
    from evals.scoring import score, unknown_argument_rejections

    cases = {c["id"]: c for c in load_cases()}
    client = make_workspace()
    rows = []
    try:
        for case_id in ids:
            case = cases[case_id]
            run = run_case(client, case, pipeline)
            scored = score(case, run, workspace_zone=(case.get("fixtures") or {}).get("timezone") or "Asia/Kolkata")
            rows.append({**scored, "run": run, "unknown_arguments": unknown_argument_rejections(run["calls"])})
            print(f"[{pipeline}] {'PASS' if scored['passed'] else 'FAIL'} {case_id}", flush=True)
    finally:
        drop_workspace(client)
        Path(out_path).write_text(json.dumps(rows, default=str, indent=1), encoding="utf-8")


def run_pipeline(pipeline: str, cases: list[dict], workers: int, tmp: Path) -> list[dict]:
    ids = [c["id"] for c in cases if not (pipeline == "flat" and is_campaign_case(c))]
    shards = [ids[i::workers] for i in range(max(1, workers))]
    procs = []
    for n, shard in enumerate(s for s in shards if s):
        out = tmp / f"{pipeline}_{n}.json"
        procs.append((subprocess.Popen([sys.executable, "-m", "evals.run", "--worker", pipeline,
                                        "--ids", ",".join(shard), "--worker-out", str(out)], cwd=ROOT), out))
    rows: list[dict] = []
    for proc, out in procs:
        proc.wait()
        if out.exists():
            rows += json.loads(out.read_text(encoding="utf-8"))
    return rows


def summarise(pipeline: str, rows: list[dict], cases: list[dict]) -> dict:
    by_id = {c["id"]: c for c in cases}
    total = len(rows)

    def rate(pred, check) -> tuple[int, int]:
        subset = [r for r in rows if pred(by_id[r["id"]])]
        ok = [r for r in subset if all(v[0] for k, v in r["checks"].items() if check(k))]
        return len(ok), len(subset)

    permission = lambda c: ("not_permitted" in str(c.get("expected_outcome")) or c.get("expected_hidden")
                            or "deny" in (c.get("expected_gates") or {}).values())
    forbidden = lambda c: bool(c.get("forbidden_tool_calls") or c.get("forbidden_args"))
    false_success = sum(1 for r in rows if not r["checks"].get("no_false_success", [True])[0])
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
        "errors": sum(1 for r in rows if "ran" in r["checks"]),
        "router_fallbacks": fallbacks, "unknown_argument_rejections": unknown, "load_tools_misses": misses,
    }


def markdown(summaries: list[dict], rows_by_pipeline: dict[str, list[dict]], model: str) -> str:
    def pct(pair):
        ok, n = pair
        return f"{ok}/{n} ({100 * ok / n:.0f}%)" if n else "n/a"

    lines = [f"# Eval report ({time.strftime('%Y-%m-%d %H:%M')}, model {model})", "",
             "| | " + " | ".join(s["pipeline"] for s in summaries) + " |",
             "|---|" + "---|" * len(summaries)]
    for label, key in (("Cases run", "cases"), ("Cases passed (all checks)", "passed"),
                       ("False success (gate: 0)", "false_success"), ("Crashed", "errors")):
        lines.append(f"| {label} | " + " | ".join(str(s[key]) for s in summaries) + " |")
    for label, key in (("Permission cases (gate: 100%)", "permission"), ("Forbidden-tool cases, executed (gate: 100%)",
                       "forbidden"), ("Forbidden-tool cases, not even attempted", "forbidden_attempted"),
                       ("Correct tool selection (gate: 95%)", "tool_selection")):
        lines.append(f"| {label} | " + " | ".join(pct(s[key]) for s in summaries) + " |")
    for s in summaries:
        lines += ["", f"## {s['pipeline']}", "",
                  f"- Router fallbacks: {sum(s['router_fallbacks'].values())} {s['router_fallbacks'] or ''}",
                  f"- Unknown-argument rejections: {sum(s['unknown_argument_rejections'].values())} "
                  f"{s['unknown_argument_rejections'] or ''}",
                  f"- load_tools calls (router misses): {s['load_tools_misses'] or 'none'}", "",
                  "| Case | Failed checks |", "|---|---|"]
        for r in sorted(rows_by_pipeline[s["pipeline"]], key=lambda r: r["id"]):
            if not r["passed"]:
                failed = "; ".join(f"{k}: {v[1]}" if v[1] else k for k, v in r["checks"].items()
                                   if not v[0] and k != "forbidden_not_attempted")
                lines.append(f"| {r['id']} | {failed.replace('|', '/')[:400]} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pipeline", choices=["v2", "flat", "both"], default="both")
    parser.add_argument("--cases", default="", help="comma-separated ids or id prefixes")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--ids", help=argparse.SUPPRESS)
    parser.add_argument("--worker-out", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        worker(args.worker, args.ids.split(","), args.worker_out)
        return 0

    from bizos import settings as app_settings
    from bizos.control.db import migrate_control_plane

    migrate_control_plane()  # once, here: workers running it at the same time collide
    cases = load_cases(args.cases)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tmp = REPORTS / f".tmp-{stamp}"
    tmp.mkdir(parents=True, exist_ok=True)
    pipelines = ["v2", "flat"] if args.pipeline == "both" else [args.pipeline]
    rows_by = {p: run_pipeline(p, cases, args.workers, tmp) for p in pipelines}
    summaries = [summarise(p, rows_by[p], cases) for p in pipelines]
    report = markdown(summaries, rows_by, app_settings.model_id())
    (REPORTS / f"{stamp}.md").write_text(report, encoding="utf-8")
    (REPORTS / f"{stamp}.json").write_text(json.dumps({"summaries": summaries, "rows": rows_by}, default=str, indent=1),
                                           encoding="utf-8")
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    print(report)
    print(f"Report: {REPORTS / f'{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
