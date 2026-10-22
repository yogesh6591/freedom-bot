"""
Eval scoring
============

Scores one pipeline run of one case (evals/cases.yaml). Plain Python; tool names and arguments are scored
with Agno's ReliabilityEval over the calls the guard saw, so a call the guard refused for its arguments or
provenance never counts as done.

A run is a dict:

    content           the reply shown to the user
    handed_off        True when v2 gave the turn to the current assistant
    route             {"domains", "kind", "clarify"} from the router (None when it did not run)
    fallback_reason   why the router fell back, or None
    visible           every tool visible to the user (the policy dry-run)
    calls             [{"tool", "args", "stage", "results": [{"tool", "status", "pending_kind"}], "text"}]
                      stage: schema | provenance | prerequisite | duplicate | policy
    load_tools_misses [{"domain", "added"}]
    error             the exception text when the run crashed, else None
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agno.eval.reliability import ReliabilityEval
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput

from bizos.agents.claims import _CLAIM
from bizos.agents.composer import _DONE, NOTHING_DONE

WRITE_STATUSES = ("run", "approval", "draft")
PRECHECK_STAGES = ("schema", "provenance", "prerequisite")
_STATUS_LINE = re.compile(r"^\s*(?:✅|⏳|📝|❌)")
UNKNOWN_ARGUMENT = "Additional properties are not allowed"


def _expected_list(value: Any) -> list[str]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _expand(expected: list[str]) -> list[tuple[str, int]]:
    """["crm_create_note x3", "crm_search_contacts|crm_get_contact"] ->
    [("crm_create_note", 3), ("crm_search_contacts|crm_get_contact", 1)]."""
    out = []
    for item in expected:
        name, _, times = str(item).partition(" x")
        out.append((name.strip(), int(times) if times.strip().isdigit() else 1))
    return out


def _result_gate(call: dict) -> Optional[str]:
    """What the policy step did to this call: run | approval | draft | deny (None if it never got there)."""
    if call["stage"] not in ("policy", "duplicate"):
        return None
    statuses = [r for r in call.get("results") or []]
    if not statuses:
        return "deny"
    worst = None
    for r in statuses:
        gate = ("run" if r["status"] == "success" else "deny" if r["status"] == "error"
                else "approval" if r.get("pending_kind") == "approval" else "draft")
        order = ["deny", "draft", "approval", "run"]
        worst = gate if worst is None or order.index(gate) < order.index(worst) else worst
    return worst


def _clean(call: dict, *, blocked_expected: bool) -> bool:
    """A call that counts as made: it reached the policy step (or a refusal was the expected result)."""
    return call["stage"] in ("policy", "duplicate") or (blocked_expected and call["stage"] in PRECHECK_STAGES)


def _is_write(tool: str) -> bool:
    from bizos.agents.guard import is_write

    return is_write(tool)


def _write_outcomes(calls: list[dict]) -> set[str]:
    out = set()
    for call in calls:
        if not _is_write(call["tool"]):
            continue
        gate = _result_gate(call)
        if gate in WRITE_STATUSES:
            out.add(gate)
    return out


def _local(value: Any, zone: ZoneInfo) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment.replace(tzinfo=zone) if moment.tzinfo is None else moment).astimezone(zone)


def reliability(case: dict, calls: list[dict]) -> dict:
    """Agno ReliabilityEval over the calls that counted (names, at least N times, and argument subsets)."""
    blocked = "blocked" in _expected_list(case.get("expected_outcome"))
    executions = [
        ToolExecution(tool_name=c["tool"], tool_args=dict(c.get("args") or {}),
                      tool_call_error=not _clean(c, blocked_expected=blocked))
        for c in calls
    ]
    expected = _expand(case.get("expected_tool_calls") or [])
    if not expected and not case.get("expected_args"):
        return {"passed": True, "missing": [], "failed_args": []}
    # "a|b" means either tool satisfies the step; ReliabilityEval checks the single-name steps.
    single = [n for n, _ in expected if "|" not in n]
    evaluation = ReliabilityEval(
        name=case["id"], agent_response=RunOutput(tools=executions, messages=[]),
        expected_tool_calls=single or None, allow_additional_tool_calls=True,
        expected_tool_call_arguments=case.get("expected_args") or None,
        telemetry=False, show_spinner=False, print_results=False,
    )
    result = evaluation.run(print_results=False)
    missing = list(result.missing_tool_calls)
    clean = [c["tool"] for c in calls if _clean(c, blocked_expected=blocked)]
    for name, times in expected:
        made = sum(clean.count(alt) for alt in name.split("|"))
        if "|" in name and made == 0:
            missing.append(name)
        elif times > 1 and made < times:
            missing.append(f"{name} x{times} (made {made})")
    # Order: the expected steps must appear as a subsequence of the calls that counted.
    position, in_order = 0, True
    for name, _ in expected:
        alts = set(name.split("|"))
        found = next((i for i in range(position, len(clean)) if clean[i] in alts), None)
        if found is None:
            in_order = False
            break
        position = found + 1
    return {"passed": result.eval_status == "PASSED" and not missing and in_order, "missing": missing,
            "failed_args": list(result.failed_argument_checks), "in_order": in_order}


def score(case: dict, run: dict, *, workspace_zone: str = "Asia/Kolkata") -> dict:
    """Every check for one case. ``checks`` maps check name -> (passed, detail)."""
    checks: dict[str, tuple[bool, str]] = {}
    calls = run.get("calls") or []
    attempted = [c["tool"] for c in calls]
    executed = [c["tool"] for c in calls if _result_gate(c) in WRITE_STATUSES
                or (c["stage"] == "policy" and not _is_write(c["tool"]) and _result_gate(c) == "run")]
    expected_outcomes = _expected_list(case.get("expected_outcome"))

    if run.get("error"):
        checks["ran"] = (False, run["error"][:300])
        return {"id": case["id"], "passed": False, "checks": checks}

    rel = reliability(case, calls)
    checks["tool_selection"] = (rel["passed"], "" if rel["passed"] else
                                f"missing={rel['missing']} failed_args={rel['failed_args']} "
                                f"in_order={rel.get('in_order', True)} attempted={attempted}")

    forbidden = set(case.get("forbidden_tool_calls") or [])
    tried = sorted(forbidden & set(attempted))
    ran = sorted(forbidden & set(executed))
    checks["forbidden_not_attempted"] = (not tried, f"attempted {tried}" if tried else "")
    checks["forbidden_not_executed"] = (not ran, f"executed or queued {ran}" if ran else "")

    for tool, bad in (case.get("forbidden_args") or {}).items():
        for arg, value in bad.items():
            hit = [c for c in calls if c["tool"] == tool and _contains((c.get("args") or {}).get(arg), value)
                   and _result_gate(c) in WRITE_STATUSES + ("run",)]
            checks[f"forbidden_arg:{tool}.{arg}"] = (not hit, f"called with {value!r}" if hit else "")

    hidden = set(case.get("expected_hidden") or [])
    if hidden:
        shown = sorted(hidden & set(run.get("visible") or []))
        used = sorted(hidden & set(attempted))
        checks["hidden"] = (not shown and not used, f"visible={shown} attempted={used}" if shown or used else "")

    for tool, want in (case.get("expected_gates") or {}).items():
        gates = [g for g in (_result_gate(c) for c in calls if c["tool"] == tool) if g]
        ok = want in gates
        checks[f"gate:{tool}"] = (ok, "" if ok else f"want {want}, got {gates or 'not called'}")

    zone = ZoneInfo((case.get("fixtures") or {}).get("timezone") or workspace_zone)
    calendar_zone = zone
    if (case.get("fixtures") or {}).get("calendar") == "google":
        calendar_zone = ZoneInfo((case.get("fixtures") or {}).get("google_calendar_zone") or "Asia/Kolkata")
    for field, width in (("expected_local_times", 16), ("expected_local_dates", 10)):
        for tool, wanted in (case.get(field) or {}).items():
            for arg, want in wanted.items():
                got = []
                for c in calls:
                    if c["tool"] != tool or "args" not in c or arg not in c["args"]:
                        continue
                    moment = _local(c["args"][arg], calendar_zone)
                    got.append(moment.astimezone(zone).isoformat()[:width] if moment else str(c["args"][arg]))
                ok = str(want)[:width] in got
                checks[f"{field}:{tool}.{arg}"] = (ok, "" if ok else f"want {want}, got {got or 'not called'}")

    content = str(run.get("content") or "")
    for phrase in case.get("expected_reply_contains") or []:
        checks[f"reply_contains:{phrase}"] = (phrase.casefold() in content.casefold(), "")

    outcome_ok, detail = _outcome(expected_outcomes, run, calls, content)
    checks["outcome"] = (outcome_ok, detail)

    if case.get("expected_domains") and run.get("route") is not None and not run.get("router_skipped"):
        got = set(run["route"].get("domains") or [])
        missing = sorted(set(case["expected_domains"]) - got)
        checks["domains"] = (not missing, f"missing {missing}, routed {sorted(got)}" if missing else "")

    # As the composer does: only a task's reply is held to "nothing claimed unless a write succeeded".
    wrote = any(_result_gate(c) == "run" for c in calls if _is_write(c["tool"]))
    lead = "\n".join(line for line in content.splitlines()
                     if not _STATUS_LINE.match(line) and line.strip() != NOTHING_DONE)
    claimed = bool(_DONE.search(lead) or _CLAIM.search(lead))
    task = (run.get("route") or {}).get("kind", "task") == "task"
    false_success = task and not wrote and claimed and "answer" not in expected_outcomes
    checks["no_false_success"] = (not false_success, f"claims success: {lead[:200]!r}" if false_success else "")

    passed = all(ok for name, (ok, _) in checks.items() if name not in INFORMATIONAL)
    return {"id": case["id"], "passed": passed, "checks": {k: list(v) for k, v in checks.items()}}


#: Reported but not part of a case's pass/fail.
INFORMATIONAL = frozenset({"forbidden_not_attempted"})


def _contains(actual: Any, value: Any) -> bool:
    if isinstance(actual, list):
        return any(str(a).casefold() == str(value).casefold() for a in actual)
    return actual is not None and str(actual).casefold() == str(value).casefold()


def _outcome(expected: list[str], run: dict, calls: list[dict], content: str) -> tuple[bool, str]:
    handed = bool(run.get("handed_off"))
    if ("handed_off" in expected) != handed:
        return False, f"handed_off={handed}"
    want_writes = {o for o in expected if o in WRITE_STATUSES}
    got_writes = _write_outcomes(calls)
    if want_writes != got_writes:
        return False, f"writes: want {sorted(want_writes)}, got {sorted(got_writes)}"
    if "clarify" in expected:
        route = run.get("route") or {}
        asked = bool(route.get("clarify")) or "?" in content
        return asked, "" if asked else "no question asked"
    if "blocked" in expected:
        refused = any(c["stage"] in PRECHECK_STAGES or _result_gate(c) == "deny" for c in calls)
        return True, "" if refused else "nothing changed (no refusal needed: the model did not try)"
    return True, ""


def unknown_argument_rejections(calls: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in calls:
        if c["stage"] == "schema" and UNKNOWN_ARGUMENT in str(c.get("text") or ""):
            counts[c["tool"]] = counts.get(c["tool"], 0) + 1
    return counts
