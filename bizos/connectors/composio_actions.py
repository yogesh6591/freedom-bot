"""
Composio Actions (any action of a connected app)
================================================

Lets the assistant find and run any Composio action of the apps a user connected (Gmail, Google Calendar,
Google Drive, Google Sheets, Google Docs, YouTube, HubSpot, Facebook and Apollo: ``composio.TOOLKITS``), not only the ones a
dedicated tool wraps. Actions of any other platform are refused.
Tools the user switched off in Integrations are neither offered nor run. Plain Python; the tools in
``app/assistant/company_tools.py`` call it after the guard and the policy check.

Every action has a tier, and each tier has its own tool, so a destructive action is never run by a
tool that also runs harmless ones (CLAUDE.md):

* ``read``         - only looks things up                       -> ``integration_read_action``
* ``write``        - creates or changes something               -> ``integration_write_action``
* ``destructive``  - deletes, trashes, sends or posts publicly   -> ``integration_destructive_action``

The tier is the strictest of two signals: the words in the action's name (``DELETE``, ``SEND``,
``TRASH``...) and Composio's own hints (``readOnlyHint``, ``destructiveHint``). Composio's hints
alone are not enough: it marks ``GMAIL_MOVE_TO_TRASH`` as an update and ``GMAIL_SEND_EMAIL`` as a
create. An action counts as a read only when both signals agree; anything unclear is a write.

Results are external content: every string is neutralized (:mod:`bizos.connectors.untrusted`)
before the model sees it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from bizos.connectors import composio
from bizos.connectors.base import ConnectorError, ConnectorNotConfigured
from bizos.connectors.untrusted import neutralize
from bizos.tenancy.context import TenantContext

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"
TIERS = (READ, WRITE, DESTRUCTIVE)

#: The tool that runs each tier.
TOOL_FOR_TIER = {
    READ: "integration_read_action",
    WRITE: "integration_write_action",
    DESTRUCTIVE: "integration_destructive_action",
}

#: Action-name prefix -> Composio toolkit slug (only toolkits users can connect, composio.TOOLKITS).
_PREFIX_TOOLKIT = {"GMAIL": "gmail", "GOOGLECALENDAR": "googlecalendar", "YOUTUBE": "youtube",
                   "GOOGLEDRIVE": "googledrive", "GOOGLESHEETS": "googlesheets", "GOOGLEDOCS": "googledocs",
                   "HUBSPOT": "hubspot", "FACEBOOK": "facebook", "APOLLO": "apollo"}

#: Name words that make an action destructive anywhere in its name: it removes data, or it sends or
#: publishes something that cannot be taken back (CLAUDE.md lists send with delete, deactivate, reset).
_DESTRUCTIVE_WORDS = frozenset({
    "DELETE", "TRASH", "PURGE", "ERASE", "DESTROY", "GDPR", "SEND", "FORWARD", "PUBLISH", "UPLOAD",
    "DEACTIVATE", "RESET", "REVOKE",
})
#: Words that make an action destructive when they are its verb (the first word after the prefix).
_DESTRUCTIVE_VERBS = _DESTRUCTIVE_WORDS | frozenset({
    "REMOVE", "EMPTY", "CLEAR", "ARCHIVE", "REPLY", "POST", "BROADCAST", "INVITE", "DISABLE",
    "UNSUBSCRIBE", "CANCEL", "MERGE", "REPORT",
})
#: Words that start a read-only action's name (after the toolkit prefix).
_READ_VERBS = frozenset({
    "GET", "LIST", "SEARCH", "FETCH", "FIND", "READ", "QUERY", "LOAD", "CHECK", "COUNT", "RETRIEVE",
    "DOWNLOAD", "VIEW", "LOOKUP", "DESCRIBE", "EXPORT",
})
#: Apollo sequences email people on their own once a contact is in an active sequence, so starting one
#: (or putting contacts into one, or switching their status in one) is a send: destructive, whatever the verb.
_SENDS_EMAIL = frozenset({
    "APOLLO_ACTIVATE_SEQUENCE", "APOLLO_ADD_CONTACTS_TO_SEQUENCE", "APOLLO_UPDATE_CONTACT_STATUS_IN_SEQUENCE",
})
#: YouTube comment replies are posts on the public channel, whatever the verb says.
_PUBLIC_POSTS = frozenset({"YOUTUBE_CREATE_COMMENT_REPLY", "YOUTUBE_POST_COMMENT", "YOUTUBE_UPDATE_COMMENT"})
#: Creating or editing a post or comment publishes it (FACEBOOK_CREATE_POST, FACEBOOK_UPDATE_POST,
#: LINKEDIN_CREATE_LINKED_IN_POST...), even though Composio calls it a create or an update.
_PUBLISHING_VERBS = frozenset({"CREATE", "UPDATE"})
_PUBLISHED_THINGS = frozenset({"POST", "COMMENT"})

_RESULT_LIMIT = 12000  # characters of result JSON handed back to the model
_SCHEMA_CACHE_TTL = 600.0
_schema_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def is_new_public_post(slug: str) -> bool:
    """Whether this action publishes new content (a Page post, a comment): checked for duplicates first.

    Editing an existing post (``FACEBOOK_UPDATE_POST``) is not a new post.
    """
    name = str(slug or "").upper()
    words = name.split("_")[1:]
    verb = words[0] if words else ""
    if name in {"YOUTUBE_CREATE_COMMENT_REPLY", "YOUTUBE_POST_COMMENT"}:
        return True
    return verb in {"CREATE", "POST", "PUBLISH"} and bool(set(words[1:]) & _PUBLISHED_THINGS)


def toolkit_of(slug: str) -> Optional[str]:
    """The built-in app an action belongs to, from its name (``YOUTUBE_LIST_COMMENTS`` -> ``youtube``)."""
    return _PREFIX_TOOLKIT.get(str(slug or "").split("_", 1)[0].upper())


def classify(slug: str, tags: Optional[list[str]] = None) -> str:
    """The tier of one action: the strictest of its name and Composio's hints.

    A read needs a read verb *and* Composio's read-only hint (so ``GMAIL_LIST_SEND_AS`` is a read);
    a read verb later in the name counts only when no destructive word is present.
    """
    name = str(slug or "").upper()
    words = name.split("_")[1:]
    verb = words[0] if words else ""
    hints = {str(t) for t in (tags or [])}
    if name in _PUBLIC_POSTS or name in _SENDS_EMAIL or "destructiveHint" in hints:
        return DESTRUCTIVE
    if verb in _PUBLISHING_VERBS and set(words[1:]) & _PUBLISHED_THINGS:
        return DESTRUCTIVE
    read_only = "readOnlyHint" in hints and not hints & {"createHint", "updateHint"}
    if read_only and verb in _READ_VERBS:
        return READ
    if set(words) & _DESTRUCTIVE_WORDS or verb in _DESTRUCTIVE_VERBS:
        return DESTRUCTIVE
    if read_only and set(words[:3]) & _READ_VERBS:  # GMAIL_SETTINGS_GET_IMAP
        return READ
    return WRITE


def classify_by_name(slug: str) -> str:
    """The tier from the name alone (a read verb counts as the read-only hint). Used only when Composio
    is not configured, where nothing can run anyway, and by the evals, which never reach Composio."""
    verb = (str(slug or "").upper().split("_") + ["", ""])[1]
    return classify(slug, ["readOnlyHint"] if verb in _READ_VERBS else [])


def connected_toolkits(ctx: TenantContext) -> list[str]:
    """Toolkits this user has an active connection for."""
    if not composio.configured() or not ctx.user_id:
        return []
    return [slug for slug, account in composio._active_accounts(ctx).items() if account]


#: Argument names most requests fill, listed before the rest when an action has many arguments.
_COMMON_ARGS = ("id", "email", "name", "title", "subject", "body", "text", "message", "content", "query", "q",
                "channel", "to", "recipient", "user_id", "limit", "max_results", "start", "end", "date", "description")
#: Arguments shown per action in a list; one action asked for by its action_id shows all of them.
LIST_ARGS = 25


def _type_of(spec: dict[str, Any]) -> str:
    if spec.get("type"):
        return str(spec["type"])
    options = spec.get("anyOf") or spec.get("oneOf") or []
    kinds = sorted({str(o.get("type")) for o in options if isinstance(o, dict) and o.get("type") and o.get("type") != "null"})
    return " or ".join(kinds) or "any"


def _arg(spec: Any, *, depth: int = 0) -> dict[str, Any]:
    """One argument: type, short description, enum, default, and one level of nested fields or list items."""
    spec = spec if isinstance(spec, dict) else {}
    entry: dict[str, Any] = {"type": _type_of(spec),
                             "description": " ".join(str(spec.get("description") or "").split())[:160]}
    if isinstance(spec.get("enum"), list):
        entry["enum"] = spec["enum"][:12]
    if spec.get("default") not in (None, "", [], {}):
        entry["default"] = spec["default"]
    if depth == 0:
        inner = spec.get("properties")
        if isinstance(inner, dict) and inner:
            entry["fields"] = {k: _arg(v, depth=1) for k, v in list(inner.items())[:12]}
            if spec.get("required"):
                entry["required_fields"] = list(spec["required"])[:12]
        items = spec.get("items")
        if isinstance(items, dict):
            entry["items"] = ({k: _arg(v, depth=1) for k, v in list(items["properties"].items())[:12]}
                              if isinstance(items.get("properties"), dict) else _type_of(items))
    return entry


def _compact_schema(params: Any, *, limit: Optional[int] = LIST_ARGS, prefer: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The action's arguments for the model: required ones first, then ones the request mentions, then the
    usual ones, then the rest. ``limit`` None shows every argument; otherwise ``_more`` counts the hidden ones."""
    params = params if isinstance(params, dict) else {}
    props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
    required = set(params.get("required") or [])

    def rank(key: str) -> tuple[int, int]:
        words = set(re.findall(r"[a-z0-9]+", key.lower()))
        if key in required:
            return (0, 0)
        if words & prefer:
            return (1, 0)
        if key.lower() in _COMMON_ARGS or words & set(_COMMON_ARGS):
            return (2, 0)
        return (3, 0)

    keys = sorted(props, key=rank)  # stable: Composio's order within each group
    shown = keys if limit is None else keys[:max(limit, len(required))]
    out: dict[str, Any] = {}
    for key in shown:
        entry = _arg(props[key])
        if key in required:
            entry["required"] = True
        out[key] = entry
    if len(shown) < len(keys):
        out["_more"] = (f"{len(keys) - len(shown)} more optional arguments; call integration_find_action with this "
                        "action_id as the query to see all of them")
    return out


def _describe(tool: Any, *, full: bool = False, prefer: frozenset[str] = frozenset()) -> dict[str, Any]:
    slug = str(getattr(tool, "slug", "") or "")
    tier = classify(slug, list(getattr(tool, "tags", None) or []))
    return {
        "action_id": slug,  # an *_id key, so the guard counts it as a known value for the run tools
        "app": str(getattr(getattr(tool, "toolkit", None), "slug", "") or toolkit_of(slug) or "").lower(),
        "title": str(getattr(tool, "name", "") or ""),
        "description": " ".join(str(getattr(tool, "description", "") or "").split())[:300],
        "tier": tier,
        "run_with": TOOL_FOR_TIER[tier],
        "arguments": _compact_schema(getattr(tool, "input_parameters", None), limit=None if full else LIST_ARGS,
                                     prefer=prefer),
    }


def find(ctx: TenantContext, query: str, *, toolkit: Optional[str] = None, limit: int = 8) -> dict[str, Any]:
    """Actions of the user's connected apps that match ``query``, each with its tier and arguments."""
    connected = connected_toolkits(ctx)
    if not connected:
        raise ConnectorNotConfigured(
            "No apps are connected for your account. Connect one on the Integrations page."
        )
    wanted = [toolkit.strip().lower()] if toolkit else connected
    missing = [t for t in wanted if t not in connected]
    if missing:
        names = ", ".join(composio.app_name(t) for t in missing)
        raise ConnectorNotConfigured(f"{names} is not connected for your account. Connect it on the Integrations page.")
    from bizos.connectors import composio_catalog

    limit = max(1, min(int(limit or 8), 15))
    exact = str(query or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)+", exact):
        # One action by its action_id: every argument, nested fields included.
        try:
            tool = composio.get_client().tools.get_raw_composio_tool_by_slug(exact)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Unknown app action {exact!r}. Search with plain words instead.") from exc
        item = _describe(tool, full=True)
        if item["app"] not in connected:
            raise ConnectorNotConfigured(f"{composio.app_name(item['app'])} is not connected for your account. "
                                         "Connect it on the Integrations page.")
        if not composio_catalog.allowed(ctx, item["app"], item["action_id"]):
            raise ConnectorError(f"{exact} is switched off for this connection in Integrations.")
        with _cache_lock:
            _schema_cache[exact] = (time.monotonic(), item)
        return {"connected_apps": connected, "actions": [item]}
    words = _query_groups(query)
    # Named apps narrow the search ("recent emails on outlook" -> outlook).
    named = [t for t in wanted if t in words["apps"]]
    targets = named or wanted
    # Composio's own search is a keyword match that misses plain phrasings ("recent emails"), so every
    # action of the target apps is also ranked here, and both are merged.
    try:
        hits = composio.get_client().tools.get_raw_composio_tools(
            search=str(query or "").strip() or None, toolkits=targets, limit=limit)
    except Exception:  # noqa: BLE001 - the local ranking below still answers
        hits = []
    boosted = {str(getattr(t, "slug", "")) for t in hits}
    candidates: dict[str, tuple[dict[str, Any], Any]] = {}
    for slug in targets:
        try:
            for item, tool in _toolkit_actions(slug):
                candidates[item["action_id"]] = (item, tool)
        except Exception as exc:  # noqa: BLE001
            if not hits:
                raise ConnectorError(f"Could not search the app actions: {exc}") from exc
    for t in hits:
        if not getattr(t, "is_deprecated", False):
            candidates.setdefault(str(t.slug), (_describe(t), t))

    def relevance(item: dict[str, Any]) -> int:
        return _score(item, words) + (2 if item["action_id"] in boosted else 0)

    ranked = sorted((pair for pair in candidates.values() if relevance(pair[0]) > 0),
                    key=lambda pair: (-relevance(pair[0]), len(pair[0]["action_id"])))
    if not ranked:  # nothing matched the words: offer the platform's read actions to start from
        ranked = sorted((p for p in candidates.values() if p[0]["tier"] == READ), key=lambda p: len(p[0]["action_id"]))
    prefer = frozenset(words["apps"])
    found = [_describe(tool, prefer=prefer) for item, tool in ranked
             if composio_catalog.allowed(ctx, item["app"], item["action_id"])][:limit]
    for item in found:
        with _cache_lock:
            _schema_cache[item["action_id"]] = (time.monotonic(), item)
    result: dict[str, Any] = {"connected_apps": connected, "actions": found}
    try:  # an app the user named but has not connected ("send a slack message" with no Slack)
        unconnected = sorted(w for w in words["apps"] - set(connected) if composio_catalog.platform(w))
    except Exception:  # noqa: BLE001 - only a hint
        unconnected = []
    if unconnected:
        result["not_connected"] = [composio.app_name(w) for w in unconnected]
    return result


#: Words that mean the same thing in an action's name or description (query word -> group).
_SYNONYMS = {
    **dict.fromkeys(["email", "emails", "mail", "mails", "message", "messages", "inbox", "thread", "threads"], "mail"),
    **dict.fromkeys(["recent", "latest", "new", "list", "show", "get", "fetch", "read", "find", "search", "query",
                     "any", "check", "view"], "read"),
    **dict.fromkeys(["send", "reply", "respond", "forward", "write"], "send"),
    **dict.fromkeys(["delete", "remove", "trash", "archive"], "delete"),
    **dict.fromkeys(["meeting", "meetings", "event", "events", "calendar", "schedule"], "event"),
    **dict.fromkeys(["create", "add", "new", "insert", "make", "upsert"], "create"),
    **dict.fromkeys(["update", "edit", "change", "modify", "patch", "set", "rename"], "update"),
    **dict.fromkeys(["contact", "contacts", "people", "person", "persons", "customer", "customers", "client",
                     "clients", "lead", "leads"], "contact"),
    **dict.fromkeys(["file", "files", "document", "documents", "doc", "docs", "attachment", "attachments"], "file"),
    **dict.fromkeys(["task", "tasks", "card", "cards", "issue", "issues", "ticket", "tickets", "todo", "todos"], "task"),
    **dict.fromkeys(["repository", "repositories", "repo", "repos", "project", "projects"], "project"),
    **dict.fromkeys(["invoice", "invoices", "bill", "bills"], "invoice"),
    **dict.fromkeys(["post", "posts", "tweet", "tweets", "status"], "post"),
    **dict.fromkeys(["channel", "channels", "conversation", "conversations"], "channel"),
    **dict.fromkeys(["comment", "comments"], "comment"),
    **dict.fromkeys(["page", "pages", "note", "notes"], "page"),
    **dict.fromkeys(["deal", "deals", "opportunity", "opportunities"], "deal"),
    **dict.fromkeys(["user", "users", "member", "members"], "user"),
}
#: Groups that are verbs; a request's nouns decide more than its verb ("list tasks" is mostly about tasks).
_VERB_GROUPS = frozenset({"read", "send", "delete", "create", "update"})
_STOPWORDS = frozenset({"the", "a", "an", "on", "in", "of", "to", "for", "from", "my", "me", "i", "is", "are", "any",
                        "and", "or", "with", "about", "please", "can", "you", "what", "do", "have", "got"})
_TOOLKIT_ACTIONS: dict[str, tuple[float, list[tuple[dict[str, Any], Any]]]] = {}


def _groups(text: str) -> set[str]:
    return {_SYNONYMS.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def _query_groups(query: str) -> dict[str, Any]:
    raw = {w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if w not in _STOPWORDS}
    return {"groups": {_SYNONYMS.get(w, w) for w in raw}, "apps": raw}


def _score(item: dict[str, Any], words: dict[str, Any]) -> int:
    """How well an action matches the query: its name counts most, then its title, then its description."""
    name = _groups(item["action_id"].split("_", 1)[-1].replace("_", " "))
    title, about = _groups(item.get("title", "")), _groups(item.get("description", ""))
    score = 0
    for group in words["groups"]:
        weight = 1 if group in _VERB_GROUPS else 2
        score += weight * (3 if group in name else 2 if group in title else 1 if group in about else 0)
    return score


def _toolkit_actions(slug: str) -> list[tuple[dict[str, Any], Any]]:
    """Every current action of one platform: (description, Composio tool), cached for an hour."""
    cached = _TOOLKIT_ACTIONS.get(slug)
    if cached and time.monotonic() - cached[0] < _SCHEMA_CACHE_TTL * 6:
        return cached[1]
    raw = composio.get_client().tools.get_raw_composio_tools(toolkits=[slug], limit=1000)
    items = [(_describe(t), t) for t in raw if not getattr(t, "is_deprecated", False)]
    _TOOLKIT_ACTIONS[slug] = (time.monotonic(), items)
    return items


def describe(slug: str) -> dict[str, Any]:
    """One action's tier and arguments (cached from :func:`find`, else looked up)."""
    with _cache_lock:
        cached = _schema_cache.get(slug)
    if cached and time.monotonic() - cached[0] < _SCHEMA_CACHE_TTL:
        return cached[1]
    client = composio.get_client()  # raises ConnectorNotConfigured when Composio is not set up
    try:
        tool = client.tools.get_raw_composio_tool_by_slug(slug)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Unknown app action {slug!r}. Use integration_find_action to find the right one.") from exc
    item = _describe(tool)
    with _cache_lock:
        _schema_cache[slug] = (time.monotonic(), item)
    return item


def check(slug: str, tier: str) -> tuple[Optional[str], Optional[str]]:
    """(toolkit, problem) for running ``slug`` with the ``tier`` tool. ``problem`` is None when it may run."""
    name = str(slug or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{3,120}", name):
        return None, "action must be an action name from integration_find_action, e.g. YOUTUBE_LIST_COMMENTS"
    try:
        described = describe(name)
        actual, toolkit = described["tier"], described.get("app") or toolkit_of(name)
    except ConnectorNotConfigured:
        actual, toolkit = classify_by_name(name), toolkit_of(name)
    if not toolkit:
        return None, f"{name} is not an action of a connectable app. Use integration_find_action to find one."
    if not composio.allowed_app(toolkit):
        return None, (f"{name} belongs to an app FreedomBot does not use. It works with "
                      f"{composio.allowed_apps_text()} only. Nothing was run.")
    if actual != tier:
        return toolkit, (f"{name} is a {actual} action; run it with {TOOL_FOR_TIER[actual]}, not "
                         f"{TOOL_FOR_TIER[tier]}. Nothing was run.")
    return toolkit, None


def _scrub(value: Any, depth: int = 0) -> Any:
    """Neutralize every string in a result (external content)."""
    if depth > 12:
        return None
    if isinstance(value, str):
        return neutralize(value)[0]
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value[:200]]
    if isinstance(value, dict):
        return {str(k): _scrub(v, depth + 1) for k, v in value.items()}
    return value


def run(ctx: TenantContext, slug: str, arguments: Any, *, tier: str) -> tuple[Any, bool]:
    """Run one action as ``ctx``'s user. Returns (scrubbed result, truncated)."""
    name = str(slug or "").strip().upper()
    toolkit, problem = check(name, tier)
    if problem:
        raise ConnectorError(problem)
    args = arguments if isinstance(arguments, dict) else {}
    data = composio.execute(ctx, toolkit, name, args)
    cleaned = _scrub(data)
    text = json.dumps(cleaned, default=str)
    if len(text) <= _RESULT_LIMIT:
        return cleaned, False
    return {"partial_json": text[:_RESULT_LIMIT], "note": "Result was long and was cut; narrow the request "
                                                          "(fewer results, a filter, or a page size)."}, True
