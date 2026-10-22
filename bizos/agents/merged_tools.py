"""
Merged Tools (v2 pipeline)
==========================

Thin wrappers the v2 executor shows in place of the old tools they wrap
(docs/agent/ARCHITECTURE.md §6). The old tools and their functions stay registered and unchanged.

Gating: every merged call becomes one or more calls to the specific old tools its arguments map to,
and each goes through :func:`~bizos.tools.base.guarded_call` under that old tool's own name and
pack scope. So per-tool permission overrides, tool modes and the audit log all keep working, and a
merged call is gated exactly like the old tool it maps to.

Visibility: a merged tool is shown only if at least one old tool it maps to is visible to the user,
and its enums and optional fields are narrowed to what that user may use (:func:`schema_for`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from bizos.tools.base import RunScope, ToolOutcome, assess, guarded_call
from bizos.types import PolicyEffect


class MergeArgumentError(ValueError):
    """The merged call's arguments cannot be mapped. The message tells the model how to fix the call."""


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]
    #: Restores the previous state if a later call in the same all-or-nothing request fails.
    undo: Optional["Call"] = None


@dataclass(frozen=True)
class MergedTool:
    name: str
    domain: str
    #: Old tools this one may call.
    wraps: tuple[str, ...]
    schema: dict[str, Any]
    #: Maps arguments to old-tool calls. ``scope_for(tool)`` gives the scope that tool runs in.
    plan: Callable[[Callable[[str], RunScope], dict[str, Any]], list[Call]]
    #: Narrows the schema to the allowed old tools; ``None`` hides the tool.
    narrow: Callable[[dict[str, Any], set[str]], Optional[dict[str, Any]]]
    #: All calls must be able to run before any does, and a later failure undoes earlier ones.
    all_or_nothing: bool = False


def _s(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _arr(description: str) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": {"type": "string"}}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _narrow_enum(prop: str, choices: dict[str, str]) -> Callable[[dict[str, Any], set[str]], Optional[dict[str, Any]]]:
    """Keep only the enum values whose old tool is allowed; hide the tool if none is."""

    def narrow(schema: dict[str, Any], allowed: set[str]) -> Optional[dict[str, Any]]:
        values = [v for v, tool in choices.items() if tool in allowed]
        if not values:
            return None
        out = copy.deepcopy(schema)
        out["properties"][prop]["enum"] = values
        return out

    return narrow


def _drop(schema: dict[str, Any], props: tuple[str, ...]) -> dict[str, Any]:
    out = copy.deepcopy(schema)
    for prop in props:
        out["properties"].pop(prop, None)
    return out


# --------------------------------------------------------------------------- approvals_decide

_DECISIONS = {
    "approve": "approvals_approve",
    "reject": "approvals_reject",
    "request_changes": "approvals_request_changes",
}


def _plan_decide(scope_for, args):
    decision = str(args.get("decision") or "").strip().casefold()
    if decision not in _DECISIONS:
        raise MergeArgumentError("decision must be one of approve, reject, request_changes.")
    if decision == "request_changes" and not str(args.get("comment") or "").strip():
        raise MergeArgumentError("request_changes needs a comment saying what must change.")
    if not args.get("action_id"):
        raise MergeArgumentError(
            "Give the request's action_id from approvals_list or approvals_get. Words from its title can match "
            "the wrong request, so they are not accepted for a decision."
        )
    passed = {k: args[k] for k in ("action_id", "comment") if args.get(k) not in (None, "")}
    return [Call(_DECISIONS[decision], passed)]


APPROVALS_DECIDE = MergedTool(
    name="approvals_decide",
    domain="approvals",
    wraps=tuple(_DECISIONS.values()),
    schema=_schema(
        {
            "action_id": _s("The request's action_id from approvals_list or approvals_get, e.g. act_…"),
            "decision": {"type": "string", "enum": list(_DECISIONS), "description": "What the approver decided"},
            "comment": _s("Why; required for request_changes, which the requester sees"),
        },
        ["action_id", "decision"],
    ),
    plan=_plan_decide,
    narrow=_narrow_enum("decision", _DECISIONS),
)


# --------------------------------------------------------------------------- memory_add

_KINDS = {"fact": "memory_add_fact", "sop": "memory_add_sop", "decision": "memory_record_decision"}
_MEMORY_FIELDS = ("title", "content", "key", "tags", "source_type", "confidence", "topic",
                  "decided_on", "approved_by", "context", "reason", "status")


def _plan_memory_add(scope_for, args):
    kind = str(args.get("kind") or "").strip().casefold()
    if kind not in _KINDS:
        raise MergeArgumentError("kind must be one of fact, sop, decision.")
    if not str(args.get("title") or "").strip() or not str(args.get("content") or "").strip():
        raise MergeArgumentError("title and content are required.")
    passed = {k: args[k] for k in _MEMORY_FIELDS if args.get(k) not in (None, "")}
    if kind == "decision":
        passed["decision"] = passed["content"]
    return [Call(_KINDS[kind], passed)]


MEMORY_ADD = MergedTool(
    name="memory_add",
    domain="memory",
    wraps=tuple(_KINDS.values()),
    schema=_schema(
        {
            "kind": {"type": "string", "enum": list(_KINDS),
                     "description": "decision for decisions, sop for procedures, fact for everything else"},
            "title": _s("Short title"),
            "content": _s("The fact, the SOP steps, or what was decided"),
            "key": _s("Optional stable key, e.g. acme_renewal_month"),
            "tags": _arr("Optional tags"),
            "decided_on": _s("Decisions: date decided"),
            "approved_by": _s("Decisions: who approved it"),
            "context": _s("Decisions: background"),
            "reason": _s("Decisions: why"),
        },
        ["kind", "title", "content"],
    ),
    plan=_plan_memory_add,
    narrow=_narrow_enum("kind", _KINDS),
)


# --------------------------------------------------------------------------- admin_update_user

_ROLE_FIELDS = ("roles_add", "roles_remove")
_ACCESS_FIELDS = ("access_grant", "access_revoke")


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def _plan_update_user(scope_for, args):
    from app.assistant.company_tools import _find_user
    from bizos import admin_ops as ops

    wants_roles = any(_names(args.get(k)) for k in _ROLE_FIELDS)
    wants_access = any(_names(args.get(k)) for k in _ACCESS_FIELDS)
    if not (wants_roles or wants_access):
        raise MergeArgumentError("Say what changes: roles_add, roles_remove, access_grant or access_revoke.")
    try:
        user = _find_user(scope_for("admin_set_user_roles"), args.get("user"))
    except ops.AdminError as exc:
        raise MergeArgumentError(exc.message) from exc

    calls: list[Call] = []
    if wants_roles:
        current = sorted(str(r) for r in user.roles)
        wanted = (set(current) | {r.upper() for r in _names(args.get("roles_add"))}) - {
            r.upper() for r in _names(args.get("roles_remove"))
        }
        if not wanted:
            raise MergeArgumentError("A user must keep at least one role; add another before removing the last.")
        calls.append(Call(
            "admin_set_user_roles",
            {"user": user.id, "roles": sorted(wanted)},
            undo=Call("admin_set_user_roles", {"user": user.id, "roles": current}),
        ))
    if wants_access:
        calls.append(Call(
            "admin_set_user_access",
            {"user": user.id, "add": _names(args.get("access_grant")), "remove": _names(args.get("access_revoke"))},
        ))
    return calls


def _narrow_update_user(schema, allowed):
    roles, access = "admin_set_user_roles" in allowed, "admin_set_user_access" in allowed
    if not (roles or access):
        return None
    dropped: tuple[str, ...] = (() if roles else _ROLE_FIELDS) + (() if access else _ACCESS_FIELDS)
    return _drop(schema, dropped)


ADMIN_UPDATE_USER = MergedTool(
    name="admin_update_user",
    domain="admin_users",
    wraps=("admin_set_user_roles", "admin_set_user_access"),
    schema=_schema(
        {
            "user": _s("The user's user_id from admin_list_users"),
            "roles_add": _arr("Roles to add: VIEWER, DRAFTER, APPROVER, OPERATOR, ADMIN"),
            "roles_remove": _arr("Roles to remove"),
            "access_grant": _arr("Restricted data areas to grant, e.g. finance, hr, legal"),
            "access_revoke": _arr("Restricted data areas to revoke"),
        },
        ["user"],
    ),
    plan=_plan_update_user,
    narrow=_narrow_update_user,
    all_or_nothing=True,
)


# --------------------------------------------------------------------------- youtube


def _plan_channel(scope_for, args):
    calls = [Call("youtube_my_channel", {})]
    if args.get("include_videos"):
        calls.append(Call("youtube_my_videos", {k: args[k] for k in ("limit",) if args.get(k)}))
    return calls


def _plan_video(scope_for, args):
    video_id = str(args.get("video_id") or "").strip()
    if not video_id:
        raise MergeArgumentError("video_id is required: take it from youtube_search_videos or youtube_my_channel.")
    calls = [Call("youtube_video_details", {"video_ids": [video_id]})]
    if args.get("include_comments"):
        calls.append(Call("youtube_video_comments",
                          {"video_id": video_id, **{k: args[k] for k in ("limit", "order") if args.get(k)}}))
    return calls


def _narrow_optional(base: str, extra: str, props: tuple[str, ...]):
    def narrow(schema, allowed):
        if base not in allowed:
            return None
        return schema if extra in allowed else _drop(schema, props)

    return narrow


YOUTUBE_MY_CHANNEL = MergedTool(
    name="youtube_my_channel",
    domain="youtube",
    wraps=("youtube_my_channel", "youtube_my_videos"),
    schema=_schema(
        {
            "include_videos": {"type": "boolean", "description": "Also list the channel's recent videos"},
            "limit": {"type": "integer", "description": "How many videos, when include_videos is true"},
        },
        [],
    ),
    plan=_plan_channel,
    narrow=_narrow_optional("youtube_my_channel", "youtube_my_videos", ("include_videos", "limit")),
)

YOUTUBE_VIDEO = MergedTool(
    name="youtube_video",
    domain="youtube",
    wraps=("youtube_video_details", "youtube_video_comments"),
    schema=_schema(
        {
            "video_id": _s("Video id from youtube_search_videos or youtube_my_channel"),
            "include_comments": {"type": "boolean", "description": "Also read the video's comments"},
            "limit": {"type": "integer", "description": "How many comments"},
            "order": {"type": "string", "enum": ["relevance", "time"], "description": "Comment order"},
        },
        ["video_id"],
    ),
    plan=_plan_video,
    narrow=_narrow_optional("youtube_video_details", "youtube_video_comments", ("include_comments", "limit", "order")),
)


MERGED: dict[str, MergedTool] = {
    t.name: t for t in (APPROVALS_DECIDE, MEMORY_ADD, ADMIN_UPDATE_USER, YOUTUBE_MY_CHANNEL, YOUTUBE_VIDEO)
}

#: Old tools the v2 model no longer sees directly (they are reached through a merged tool).
WRAPPED: frozenset[str] = frozenset(t for m in MERGED.values() for t in m.wraps) - frozenset(MERGED)


def schema_for(name: str, allowed: set[str]) -> Optional[dict[str, Any]]:
    """The merged tool's schema narrowed to the old tools ``allowed`` for this user, or None to hide it."""
    tool = MERGED[name]
    return tool.narrow(tool.schema, allowed)


# --------------------------------------------------------------------------- running


@dataclass
class MergedResult:
    tool: str
    #: One outcome per old-tool call, in order (including any undo).
    outcomes: list[ToolOutcome] = field(default_factory=list)
    #: Set when the request stopped before running anything.
    refused: Optional[str] = None


def run(name: str, args: dict[str, Any], scope_for: Callable[[str], RunScope]) -> MergedResult:
    """Run one merged call. Raises :class:`MergeArgumentError` for arguments that cannot be mapped."""
    tool = MERGED[name]
    calls = tool.plan(scope_for, dict(args or {}))
    result = MergedResult(name)

    if tool.all_or_nothing and len(calls) > 1:
        checks = [assess(scope_for(c.tool), c.tool, c.args, record=False) for c in calls]
        refused = next((c for c in checks if c.refused), None)
        if refused is not None:
            result.refused = f"Nothing was changed: {refused.message}"
            return result
        if len({c.effect for c in checks}) > 1:
            result.refused = (
                "Nothing was changed: these changes would not be handled the same way "
                f"({', '.join(f'{c.tool}: {c.effect}' for c in checks)}). "
                "Make the two changes as separate requests."
            )
            return result

    done: list[Call] = []
    for call in calls:
        outcome = guarded_call(scope_for(call.tool), call.tool, call.args)
        result.outcomes.append(outcome)
        if outcome.ok:  # ran, or was queued for approval or as a draft
            if outcome.effect == PolicyEffect.ALLOW:
                done.append(call)
            continue
        if tool.all_or_nothing:
            for previous in reversed(done):
                if previous.undo is not None:
                    result.outcomes.append(
                        guarded_call(scope_for(previous.undo.tool), previous.undo.tool, previous.undo.args)
                    )
            break
    return result
