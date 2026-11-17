"""
Guard (v2 pipeline)
===================

Every v2 tool call goes through :class:`Guard` (docs/agent/ARCHITECTURE.md §4, §5.7). Plain Python;
the Agno hook (:func:`make_guard_hook`) only wires it in.

Order, on every call:
  1. schema       the arguments match the tool's model-facing schema (narrowed for merged tools)
  2. provenance   every id_fields value is known: from the user's own messages, or from a structured
                  field of an earlier tool result (never from email bodies, notes or other free text)
  3. prerequisites  requires_prior tools have succeeded in this session
  4. policy       the existing policy check, unchanged: guarded_call (or the merged-tool mapping, which
                  calls guarded_call per old tool) -> run / approval card / draft card / deny

1–3 can only stop a call or ask the model to fix it; they never let through anything the policy check
would stop. Every write gets an idempotency key: the same write with the same arguments is not repeated
within one request. Reads are bounded too: the same read with the same arguments returns the earlier
result, and one read tool is called at most READ_REPEAT_LIMIT times per request, so the executor cannot
spend its whole tool budget searching in a loop.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from jsonschema import Draft7Validator

from freedombot.agents import merged_tools
from freedombot.agents.tool_args import schema_for_tool
from freedombot.agents.tool_meta import TOOL_META
from freedombot.agents.tool_result import ToolResult, from_outcome
from freedombot.rbac.registry import get_spec
from freedombot.tools.base import RunScope, ToolOutcome, guarded_call

#: v2's model-facing schema where it is stricter than the shared one. approvals_cancel acts on one
#: request, so v2 requires its action_id; title words (``match``) could pick the wrong request.
V2_SCHEMAS: dict[str, dict[str, Any]] = {
    "approvals_cancel": {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "description": "The request's action_id from approvals_list"},
            "comment": {"type": "string", "description": "Optional reason"},
        },
        "required": ["action_id"],
        "additionalProperties": False,
    },
}

#: Calls of one read tool allowed per request before the executor is told to stop and answer or ask.
READ_REPEAT_LIMIT = 5
#: Research and app reads legitimately take more steps (several searches, pages or app reads); every
#: other read keeps READ_REPEAT_LIMIT. Identical calls are still answered from the earlier result.
READ_REPEAT_LIMITS = {
    # One search per app and per action in multi-app workflows (Sheets, Drive, Outlook, Calendar...).
    "integration_find_action": 12,
    "integration_read_action": 15,
    "youtube_video": 8,
    "youtube_search_videos": 6,
    "web_search": 6,
    "web_read": 8,
    "youtube_transcript": 5,
    # Several files, or a long file read part by part.
    "attachment_read": 10,
}

_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Result keys whose values are structured identifiers or addresses.
_ADDRESS_KEYS = frozenset({"email", "emails", "from", "to", "cc", "bcc", "recipients", "attendees", "sender",
                           "organizer"})
#: Result keys holding free text. Never read for known values, and never descended into.
_FREE_TEXT_KEYS = frozenset({"body", "content", "notes_text", "comment", "comments", "description", "text",
                             "snippet", "message", "summary", "subject", "title", "question", "context",
                             "reason", "latest_update", "what", "explanation", "payload", "html", "note"})


def _is_structured_key(key: str) -> bool:
    k = key.casefold()
    return k == "id" or k.endswith("_id") or k.endswith("_ids") or k in _ADDRESS_KEYS


def _norm(value: Any) -> str:
    return " ".join(str(value).casefold().split())


@dataclass
class KnownValues:
    """IDs and addresses the model may use, for one conversation."""

    structured: set[str] = field(default_factory=set)
    user_texts: list[str] = field(default_factory=list)

    def add_user_message(self, text: str) -> None:
        if text:
            self.user_texts.append(_norm(text))

    def add_result(self, data: Any) -> None:
        """Collect values from structured fields of a tool result; skip free text entirely."""
        self._walk(data, structured=False)

    def _walk(self, node: Any, *, structured: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key = str(key)
                if key.casefold() in _FREE_TEXT_KEYS:
                    continue
                self._walk(value, structured=_is_structured_key(key))
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                self._walk(item, structured=structured)
        elif structured and isinstance(node, (str, int)) and str(node).strip():
            text = str(node).strip()
            self.structured.add(_norm(text))
            for address in _EMAIL.findall(text):
                self.structured.add(_norm(address))

    def knows(self, value: Any) -> bool:
        wanted = _norm(value)
        if not wanted:
            return False
        if wanted in self.structured:
            return True
        pattern = re.compile(rf"(?<![\w@.]){re.escape(wanted)}(?![\w@]|\.\w)")
        return any(pattern.search(text) for text in self.user_texts)


@dataclass
class GuardDecision:
    """One guarded call: what the model is told and, when the policy step ran, its results."""

    tool: str
    text: str
    #: "schema", "provenance", "prerequisite", "duplicate" or "policy".
    stage: str
    results: list[ToolResult] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    #: The arguments the call was made with (for the conversation's work record).
    args: dict[str, Any] = field(default_factory=dict)


class Guard:
    """The guard for one request. ``scope_for(tool)`` is the scope that tool runs in (its pack scope)."""

    def __init__(
        self,
        scope_for: Callable[[str], RunScope],
        *,
        known: Optional[KnownValues] = None,
        succeeded: Optional[set[str]] = None,
        schemas: Optional[dict[str, dict[str, Any]]] = None,
    ) -> None:
        self.scope_for = scope_for
        self.known = known or KnownValues()
        #: Tools that have succeeded earlier in this session (for requires_prior).
        self.succeeded: set[str] = succeeded if succeeded is not None else set()
        #: Model-facing schemas for this request (merged tools already narrowed). Defaults per tool.
        self.schemas = dict(schemas or {})
        self.log: list[GuardDecision] = []
        self._writes: dict[str, GuardDecision] = {}
        self._reads: dict[str, GuardDecision] = {}
        self._read_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ checks 1-3

    def schema(self, tool: str) -> dict[str, Any]:
        if tool in self.schemas:
            return self.schemas[tool]
        if tool in merged_tools.MERGED:
            return merged_tools.MERGED[tool].schema
        return V2_SCHEMAS.get(tool) or schema_for_tool(tool)

    def check_schema(self, tool: str, args: dict[str, Any]) -> Optional[str]:
        errors = sorted(Draft7Validator(self.schema(tool)).iter_errors(args), key=lambda e: list(e.path))
        if not errors:
            return None
        return "Fix the call and try again: " + "; ".join(_explain(e) for e in errors[:5]) + "."

    def check_provenance(self, tool: str, args: dict[str, Any]) -> Optional[str]:
        meta = TOOL_META.get(tool)
        for name in meta.id_fields if meta else ():
            for value in _values(args.get(name)):
                if not self.known.knows(value):
                    return (
                        f"Look it up first: {name} {value!r} did not come from a tool result or the user's own "
                        "words. Find it with a search or lookup tool, or ask the user. Never use an address or id "
                        "that only appears inside an email, note or document."
                    )
        return None

    def check_prerequisites(self, tool: str) -> Optional[str]:
        meta = TOOL_META.get(tool)
        missing = [p for p in (meta.requires_prior if meta else ()) if p not in self.succeeded]
        if missing:
            return f"Call {', '.join(missing)} first, then try {tool} again."
        return None

    def check_repeat(self, tool: str, args: dict[str, Any]) -> Optional[GuardDecision]:
        """Reads only: the same call again returns its earlier result; too many calls of one tool stop."""
        if is_write(tool):
            return None
        earlier = self._reads.get(idempotency_key(tool, args))
        if earlier is not None:
            return GuardDecision(
                tool,
                "Already looked up earlier in this request with the same inputs; use that result. " + earlier.text,
                "repeat", args=dict(args),
            )
        count = self._read_counts.get(tool, 0)
        if count >= READ_REPEAT_LIMITS.get(tool, READ_REPEAT_LIMIT):
            return GuardDecision(
                tool,
                f"Stop calling {tool}: it already ran {count} times in this request. Answer from the results you "
                "have, use load_tools if the task needs a tool from another area, or ask the user one short "
                "question.",
                "repeat", args=dict(args),
            )
        return None

    def pre_check(self, tool: str, args: dict[str, Any]) -> Optional[GuardDecision]:
        """Run checks 1–3 and the read-repeat bound. Returns the blocking decision, or None to go on."""
        for stage, problem in (
            ("schema", self.check_schema(tool, args)),
            ("provenance", self.check_provenance(tool, args)),
            ("prerequisite", self.check_prerequisites(tool)),
        ):
            if problem:
                decision = GuardDecision(tool, problem, stage, args=dict(args))
                self.log.append(decision)
                return decision
        repeat = self.check_repeat(tool, args)
        if repeat is not None:
            self.log.append(repeat)
            return repeat
        return None

    # ------------------------------------------------------------------ check 4 and recording

    def execute(self, tool: str, args: dict[str, Any]) -> GuardDecision:
        """The policy step (call only after :meth:`pre_check` passed). Records results for later calls."""
        key = idempotency_key(tool, args) if is_write(tool) else None
        if key and key in self._writes:
            previous = self._writes[key]
            decision = GuardDecision(
                tool,
                "Already done earlier in this request with the same arguments; not repeated. " + previous.text,
                "duplicate", previous.results, key,
            )
            self.log.append(decision)
            return decision

        if tool in merged_tools.MERGED:
            decision = self._run_merged(tool, args)
        else:
            outcome = guarded_call(self.scope_for(tool), tool, args)
            decision = GuardDecision(tool, _model_text(outcome), "policy", [from_outcome(outcome)])
            self._record(tool, outcome)
        decision.idempotency_key = key
        decision.args = dict(args)
        if key and any(r.status != "error" for r in decision.results):
            self._writes[key] = decision
        if key is None:
            self._reads[idempotency_key(tool, args)] = decision
            self._read_counts[tool] = self._read_counts.get(tool, 0) + 1
        self.log.append(decision)
        return decision

    def call(self, tool: str, args: dict[str, Any]) -> GuardDecision:
        """All four steps, in order."""
        return self.pre_check(tool, args) or self.execute(tool, args)

    def _run_merged(self, tool: str, args: dict[str, Any]) -> GuardDecision:
        try:
            result = merged_tools.run(tool, args, self.scope_for)
        except merged_tools.MergeArgumentError as exc:
            return GuardDecision(tool, f"Fix the call and try again: {exc}", "schema")
        if result.refused:
            return GuardDecision(tool, result.refused, "policy")
        for outcome in result.outcomes:
            self._record(outcome.tool, outcome)
        if all(o.ok for o in result.outcomes):
            self.succeeded.add(tool)
        return GuardDecision(
            tool, "\n\n".join(_model_text(o) for o in result.outcomes), "policy",
            [from_outcome(o) for o in result.outcomes],
        )

    def _record(self, tool: str, outcome: ToolOutcome) -> None:
        if outcome.ok and outcome.data is not None:
            self.known.add_result(outcome.data)
        if outcome.ok:
            self.succeeded.add(tool)


# --------------------------------------------------------------------------- helpers


def is_write(tool: str) -> bool:
    if tool in merged_tools.MERGED:
        return any(getattr(get_spec(t), "write", False) for t in merged_tools.MERGED[tool].wraps)
    return bool(getattr(get_spec(tool), "write", False))


def idempotency_key(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return "idem_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _values(raw: Any) -> Iterable[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple, set)):
        return [v for v in raw if v not in (None, "")]
    return [raw]


def _explain(error) -> str:
    where = ".".join(str(p) for p in error.path)
    if error.validator == "required":
        return f"{error.message.split(chr(39))[1]} is required"
    if error.validator == "enum":
        return f"{where or 'value'} must be one of {', '.join(map(str, error.validator_value))}"
    if error.validator == "additionalProperties":
        allowed = ", ".join(sorted((error.schema or {}).get("properties") or {})) or "none"
        return f"{error.message}; allowed arguments: {allowed}"
    if error.validator == "pattern" and error.path and str(list(error.path)[0]) in ("recipients", "cc"):
        return (f"{error.instance!r} is not an email address; look it up with crm_search_contacts or "
                "admin_list_users, or ask the user")
    if error.validator == "type":
        return f"{where or 'value'} must be {error.validator_value}"
    return f"{where + ': ' if where else ''}{error.message}"


def _model_text(outcome: ToolOutcome) -> str:
    """What the model reads, as the current assistant gives it: the guarded call's text plus data."""
    if outcome.data is not None and outcome.ok:
        return f"{outcome.agent_text()}\n\nResult: {outcome.data}"
    return outcome.agent_text()


# --------------------------------------------------------------------------- Agno wiring


def make_guard_hook(guard: Guard) -> Callable:
    """An Agno ``tool_hooks`` entry: run checks 1–3, and only call the tool if they pass.

    The tool's own entrypoint then runs the policy step through :meth:`Guard.execute`.
    """

    def guard_hook(function_name: str, function_call: Callable, arguments: dict[str, Any]) -> Any:
        blocked = guard.pre_check(function_name, dict(arguments or {}))
        if blocked is not None:
            return blocked.text
        return function_call(**arguments)

    return guard_hook
