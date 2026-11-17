"""Audit how the assistant finds and calls actions on every Composio platform a user can connect.

    .venv/bin/python -m scripts.audit_composio_tools            # every connectable platform
    .venv/bin/python -m scripts.audit_composio_tools slack notion

Reads Composio's action metadata only (no connected account is used, nothing runs). For each platform:
its actions load and get a tier; no required argument is hidden from the model; and typical requests for
the platform's kind ("recent emails", "send message", "list tasks"...) rank a fitting action in the top 5.
Prints failures and a summary. Not a test: it calls Composio.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter

from freedombot.util import dotenv

for _fn in ("load", "load_dotenv", "load_env"):
    if hasattr(dotenv, _fn):
        getattr(dotenv, _fn)()
        break

from freedombot.connectors import composio_actions as acts  # noqa: E402
from freedombot.connectors import composio_catalog as catalog  # noqa: E402

R = {"LIST", "GET", "SEARCH", "FIND", "FETCH", "QUERY", "RETRIEVE", "READ"}
C = {"CREATE", "ADD", "INSERT", "NEW", "UPSERT", "POST"}
MAIL = {"MAIL", "MAILS", "MESSAGE", "MESSAGES", "EMAIL", "EMAILS", "THREAD", "THREADS", "INBOX"}
EVENT = {"EVENT", "EVENTS", "MEETING", "MEETINGS", "CALENDAR", "CALENDARS", "SCHEDULE"}
CONTACT = {"CONTACT", "CONTACTS", "PERSON", "PEOPLE", "PERSONS", "LEAD", "LEADS", "CUSTOMER", "CUSTOMERS"}
CHAT = {"MESSAGE", "MESSAGES", "CHAT", "POST", "TEXT"}
CHANNEL = {"CHANNEL", "CHANNELS", "CONVERSATION", "CONVERSATIONS", "ROOMS", "SPACES", "CHATS"}
TASK = {"TASK", "TASKS", "ISSUE", "ISSUES", "ITEM", "ITEMS", "TICKET", "TICKETS", "CARD", "CARDS", "TODO", "TODOS"}
DOC = {"DOCUMENT", "DOCUMENTS", "DOC", "DOCS", "PAGE", "PAGES", "FILE", "FILES", "NOTE", "NOTES", "DATABASE"}
FILE = {"FILE", "FILES", "ITEM", "ITEMS", "FOLDER", "FOLDERS", "DOCUMENT", "DOCUMENTS", "OBJECT", "OBJECTS"}
INVOICE = {"INVOICE", "INVOICES", "BILL", "BILLS"}
REPO = {"REPO", "REPOS", "REPOSITORY", "REPOSITORIES", "PROJECT", "PROJECTS"}
ISSUE = {"ISSUE", "ISSUES", "TICKET", "TICKETS", "BUG", "BUGS"}
POST = {"POST", "POSTS", "TWEET", "TWEETS", "SHARE", "UGC", "CONTENT", "VIDEO", "MEDIA", "STATUS"}

#: Category -> (request, verbs, nouns): a fitting action's name holds one of the verbs and one of the nouns.
PROBES = {
    "email": [("recent emails", R, MAIL), ("send an email", {"SEND", "REPLY"}, MAIL)],
    "calendar": [("upcoming meetings", R, EVENT), ("create a meeting", C | {"SCHEDULE", "QUICK"}, EVENT)],
    "crm": [("find contact", R, CONTACT), ("create contact", C, CONTACT)],
    "communication": [("send message", {"SEND", "POST", "CREATE"}, CHAT), ("list channels", R, CHANNEL)],
    "team chat": [("send message", {"SEND", "POST", "CREATE"}, CHAT)],
    "project management": [("list tasks", R, TASK), ("create task", C, TASK)],
    "documents": [("search documents", R, DOC), ("create document", C, DOC)],
    "file management & storage": [("list files", R, FILE), ("upload file", {"UPLOAD", "CREATE", "PUT", "ADD"}, FILE)],
    "accounting": [("list invoices", R, INVOICE)],
    "developer tools": [("list repositories", R, REPO), ("create issue", C | {"OPEN"}, ISSUE)],
    "social media": [("create post", C | {"PUBLISH", "SHARE", "SEND", "UPLOAD", "TWEET"}, POST)],
}
GENERIC: list = []


def rank(items, query: str) -> list[str]:
    words = acts._query_groups(query)
    scored = [(acts._score(item, words), item["action_id"]) for item, _ in items]
    return [slug for score, slug in sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], len(s[1])))]


def audit(slug: str) -> dict:
    out: dict = {"slug": slug, "problems": []}
    try:
        items = acts._toolkit_actions(slug)
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"actions did not load: {type(exc).__name__}: {str(exc)[:120]}")
        return out
    out["tools"] = len(items)
    out["tiers"] = Counter(item["tier"] for item, _ in items)
    if not items:
        out["problems"].append("no actions")
        return out
    for item, tool in items:
        params = getattr(tool, "input_parameters", None) or {}
        required = set(params.get("required") or [])
        missing = required - set(item["arguments"])
        if missing:
            out["problems"].append(f"{item['action_id']}: required args hidden {sorted(missing)[:5]}")
    platform = catalog.platform(slug) or {"categories": []}
    probes = [p for c in platform["categories"] for p in PROBES.get(c.lower(), [])] or GENERIC
    out["probes"] = len(probes)
    out["probe_fail"] = 0
    def fits(slug: str, verbs: set, nouns: set) -> bool:
        parts = set(slug.split("_")[1:])
        return bool(parts & verbs and parts & nouns)

    for query, verbs, nouns in probes:
        top = rank(items, query)[:5]
        if not any(fits(s, verbs, nouns) for s in top):
            available = [i["action_id"] for i, _ in items if fits(i["action_id"], verbs, nouns)]
            if available:  # only a failure when a fitting action exists but was not ranked
                out["probe_fail"] += 1
                out["problems"].append(f"'{query}' top5={top[:3]} but fitting exists e.g. {available[:2]}")
    return out


def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]]
    platforms = [p for p in catalog.catalog() if catalog.connectable(p) and (not wanted or p["slug"] in wanted)]
    print(f"auditing {len(platforms)} connectable platforms…", flush=True)
    t0, results = time.time(), []
    for p in platforms:
        r = audit(p["slug"])
        results.append(r)
        if r["problems"]:
            print(f"- {p['slug']} ({r.get('tools', 0)} tools): " + " | ".join(r["problems"][:4]), flush=True)
    probes = sum(r.get("probes", 0) for r in results)
    failed = sum(r.get("probe_fail", 0) for r in results)
    broken = [r["slug"] for r in results if any("did not load" in x or x == "no actions" for x in r["problems"])]
    hidden = sum(1 for r in results for x in r["problems"] if "required args hidden" in x)
    tiers = Counter()
    for r in results:
        tiers.update(r.get("tiers", {}))
    print(f"\n{len(results)} platforms, {sum(r.get('tools', 0) for r in results)} actions in {time.time() - t0:.0f}s")
    print(f"tiers: {dict(tiers)}")
    print(f"actions failing to load: {len(broken)} {broken[:10]}")
    print(f"actions with hidden required args: {hidden}")
    print(f"request probes: {probes - failed}/{probes} rank a fitting action in the top 5")


if __name__ == "__main__":
    main()
