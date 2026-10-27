"""
Short-term conversation memory (v2 pipeline)
=============================================

What the assistant did in the last few turns, so a follow-up such as "add another one", "same again" or
"cancel that" can be resolved without searching for it again. Plain Python: the pipeline saves one record
per turn in the chat history's meta and shows the recent ones to the router and the executor.

The record holds only what the guard logged: tool names, the arguments sent and each result's one-line
summary. Free-text arguments (email bodies, notes) are cut short. It is shown to the models as data about
earlier calls, never as instructions, and it changes no permission: every new call still goes through the
guard and the policy check.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

#: Turns kept in the record.
MAX_TURNS = 5
#: Calls kept per turn.
MAX_CALLS = 8
#: Earlier messages passed word for word to the executor; older turns are covered by the record.
HISTORY_MESSAGES = 24

_FREE_TEXT = frozenset({"body", "content", "text", "note", "notes", "description", "question", "message",
                        "comment", "summary", "html", "reason", "explanation"})


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short_args(args: dict[str, Any]) -> str:
    """``key=value`` pairs, free text cut to a few words, the whole line bounded."""
    parts = []
    for key, value in (args or {}).items():
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={_short(value, 40 if key in _FREE_TEXT else 80)}")
    return _short(", ".join(parts), 300)


def _status(decision: Any) -> str:
    results = list(getattr(decision, "results", None) or [])
    if not results:
        return "error"
    first = results[0]
    if first.status == "pending":
        return "waiting for approval" if first.pending_kind == "approval" else "saved as draft"
    return first.status


def turn_record(user_message: str, domains: Sequence[str], log: Iterable[Any]) -> dict[str, Any]:
    """One turn: what was asked, the tool areas used, and each call that reached the policy step."""
    calls = []
    for decision in log:
        if getattr(decision, "stage", "") not in ("policy", "duplicate"):
            continue
        results = list(getattr(decision, "results", None) or [])
        first = results[0] if results else None
        calls.append({
            "tool": decision.tool,
            "args": short_args(getattr(decision, "args", {}) or {}),
            "status": _status(decision),
            "summary": _short(first.summary if first else decision.text, 200),
            "ref": getattr(first, "ref", None) if first else None,
        })
    return {"asked": _short(user_message, 200), "domains": list(domains), "calls": calls[-MAX_CALLS:]}


def remember(turns: Sequence[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    return [*list(turns or []), record][-MAX_TURNS:]


def recent_work_text(turns: Sequence[dict[str, Any]]) -> str:
    """The record as prompt text, oldest first. Empty when nothing was done yet."""
    lines: list[str] = []
    for turn in turns or []:
        calls = turn.get("calls") or []
        if not calls:
            continue
        lines.append(f"- User asked: “{turn.get('asked', '')}”")
        for call in calls:
            ref = f" [{call['ref']}]" if call.get("ref") else ""
            lines.append(f"  - {call['tool']}({call.get('args', '')}) → {call.get('status')}: "
                         f"{call.get('summary', '')}{ref}")
    return "\n".join(lines)


def carry_domains(domains: Sequence[str], turns: Sequence[dict[str, Any]], menu: Sequence[str],
                  limit: int = 4) -> list[str]:
    """The router's domains plus the previous turn's, so a follow-up keeps the tools it was using."""
    out = list(domains)
    previous = (turns[-1].get("domains") if turns else None) or []
    for domain in previous:
        if len(out) >= limit:
            break
        if domain in menu and domain not in out:
            out.append(domain)
    return out


def window(history: Sequence[Any], size: int = HISTORY_MESSAGES) -> list[Any]:
    return list(history)[-size:]
