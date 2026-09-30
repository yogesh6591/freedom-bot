"""
Per-turn Grounding
==================

Before the model answers, the platform itself looks up organizational memory for
the user's message — with the caller's access areas applied in SQL — and places
what it found in the prompt. The model no longer has to guess a memory key or
decide whether to search, so answers are grounded in the recorded value.

It also reports *which restricted areas* hold matching records the caller cannot
see (area names only, never content), and which capabilities are unavailable to
them for lack of an area. That lets the assistant say "that is in the finance
area, which your account can't access" instead of "no record exists".

Nothing here grants access: everything shown was already readable by this user,
and the tools still re-check every call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bizos.memory import store as memory
from bizos.rbac.registry import TOOL_SPECS
from bizos.tenancy.context import TenantContext

#: How restricted areas are named to users.
AREA_LABELS: dict[str, str] = {
    "finance": "Finance",
    "ops": "Operations",
    "hr": "HR",
    "salary": "Salary / compensation",
    "legal": "Legal",
    "exec": "Executive / board",
}

#: Words a reply must contain to count as naming the area.
_AREA_MENTIONS: dict[str, tuple[str, ...]] = {
    "finance": (r"\bfinance\b",),
    "ops": (r"\boperations\b",),
    "hr": (r"\bhr\b", r"\bhuman resources\b"),
    "salary": (r"\bsalary\b", r"\bcompensation\b"),
    "legal": (r"\blegal\b",),
    "exec": (r"\bexecutive\b", r"\bboard\b"),
}

#: Data that lives outside memory (CRM, ledger, HR files) but belongs to an
#: area. Detected from the user's own words.
_TOPICS: dict[str, tuple[tuple[str, str], ...]] = {
    "finance": (
        (r"\bbillables?\b", "billables"),
        (r"\binvoic", "invoices"),
        (r"\$\s?\d", "amounts owed"),
        (r"\bpending (total|amount|payment)", "pending totals"),
        (r"\bamounts? (owed|due|pending)", "amounts owed"),
        (r"\bpayments? (due|pending)", "payments due"),
    ),
    "hr": (
        (r"\bresumes?\b", "resumes"),
        (r"\bcandidates?\b", "candidates"),
        (r"\bapplicants?\b", "applicants"),
        (r"\bjob opening", "job openings"),
    ),
    "salary": (
        (r"\bsalar(y|ies)\b", "salary"),
        (r"\bcompensation\b", "compensation"),
        (r"\bpay bands?\b", "pay bands"),
    ),
    "ops": (
        (r"\bpayroll\b", "payroll"),
        (r"\bpayslips?\b", "payslips"),
        (r"\boperating hours\b", "operating hours"),
    ),
    "legal": (
        (r"\bmsa\b", "the MSA"),
        (r"\bcontracts?\b", "contracts"),
        (r"\bclauses?\b", "clauses"),
        (r"\bmaster agreement\b", "the master agreement"),
    ),
    "exec": ((r"\bboard\b", "board decisions"),),
}

#: M03-09: words that put a request inside a domain pack. Used only for packs the
#: client has NOT bought, so the assistant says so instead of offering the work.
_PACKAGE_TOPICS: dict[str, tuple[tuple[str, str], ...]] = {
    "finance": (
        (r"\binvoic", "invoices"),
        (r"\bbillables?\b", "billables"),
        (r"\bbilling\b", "billing"),
        (r"\brefunds?\b", "refunds"),
        (r"\bpayment terms\b", "payment terms"),
        (r"\bcredit (adjustment|note|memo|line)s?\b", "credit adjustments"),
        (r"\baccounts? (receivable|payable)\b", "receivables and payables"),
        (r"\bfinanc(e|ial)\b", "finance"),
    ),
    "legal": (
        (r"\blegal\b", "legal review"),
        (r"\bcontracts?\b", "contracts"),
        (r"\bclauses?\b", "contract clauses"),
        (r"\bmsa\b", "the MSA"),
        (r"\bmaster agreement\b", "the master agreement"),
        (r"\bcounsel\b", "counsel escalation"),
        (r"\bnda\b", "NDAs"),
    ),
    "brand": (
        (r"\bbrand\b", "brand"),
        (r"\btaglines?\b", "taglines"),
        (r"\bslogans?\b", "slogans"),
        (r"\bhomepage copy\b", "homepage copy"),
        (r"\bpress release", "press releases"),
    ),
    "strategy": (
        (r"\bstrateg(y|ic|ies)\b", "strategy"),
        (r"\boptions? memo", "options memos"),
    ),
}

#: A reply sentence that offers to do or prepare something.
_OFFER = re.compile(
    r"\b(i can(?!['’]t|not)|i could(?!n['’]t)|i'll|i will(?! not)|i would(?!n['’]t)|shall i|should i|would you like me to|do you want me to|"
    r"let me|i'd be happy to|i am able to|i'm able to|i'm happy to|happy to help|"
    r"ready to|i'm ready|proceed with)\b",
    re.I,
)

#: A request to *do* something, as opposed to asking what is recorded.
_ACTION = re.compile(
    r"\b(draft|create|prepare|send|submit|issue|generate|raise|write|make|run|approve|review|"
    r"analy[sz]e|enable|turn on|set up|propose|build|flag)\b",
    re.I,
)

_MAX_ITEMS = 6
_MAX_VALUE_CHARS = 900
_OPEN_SECTION = re.compile(r"\[\[area:\s*([a-z_]+)\s*\]\]", re.I)
_CLOSE_SECTION = re.compile(r"\[\[/area\]\]", re.I)


def area_label(area: str) -> str:
    return AREA_LABELS.get(area, area)


@dataclass
class Grounding:
    text: str
    #: Restricted areas the question touches, with the user's words that point there.
    hidden: dict[str, list[str]] = field(default_factory=dict)
    #: Memory keys of the visible records placed in the prompt.
    visible_keys: list[str] = field(default_factory=list)
    #: Domain packs this client has not bought that the question touches (M03-09),
    #: with the user's words that point there.
    unpurchased: dict[str, list[str]] = field(default_factory=dict)
    #: Whether the question asks for work in one of those packs.
    unpurchased_action: bool = False


def _render_item(item: Any, hits: list[str]) -> str:
    version = item.current
    value = (version.content if version else "") or ""
    value = _OPEN_SECTION.sub(
        lambda m: f"({area_label(m.group(1).casefold())}-only section, you may show it:)", value
    )
    value = _CLOSE_SECTION.sub("(end of section)", value)
    if len(value) > _MAX_VALUE_CHARS:
        value = value[:_MAX_VALUE_CHARS] + "…"
    updated = version.updated_at.date().isoformat() if version and version.updated_at else "unknown"
    return (
        f"- [{item.label}] {item.category} `{item.memory_key}` — {item.title} "
        f"(last updated {updated}; answers the user's words: {', '.join(hits)})\n  {value}"
    )


def _item_text(item: Any) -> str:
    version = item.current
    return " ".join(
        [item.title or "", item.memory_key or "", (version.content if version else "") or "", *(item.tags or [])]
    )


def domain_title(domain: str) -> str:
    from bizos.domains.catalog import get_entry

    entry = get_entry(domain)
    return entry.title if entry else domain.title()


def unpurchased_hits(message: str, enabled_domains: list[str]) -> dict[str, list[str]]:
    """Domain packs outside this client's package that the message is about."""
    from bizos.domains.catalog import DOMAIN_NAMES

    enabled = {d.casefold() for d in (enabled_domains or [])}
    lowered = (message or "").casefold()
    out: dict[str, list[str]] = {}
    for domain, patterns in _PACKAGE_TOPICS.items():
        if domain in enabled or domain not in DOMAIN_NAMES:
            continue
        found: list[str] = []
        for pattern, label in patterns:
            if re.search(pattern, lowered) and label not in found:
                found.append(label)
        if found:
            out[domain] = found
    return out


def package_message(unpurchased: dict[str, list[str]], company: str) -> str:
    who = f"{company}'s" if company else "this workspace's"
    return "\n".join(
        f"- {domain_title(d)} ({', '.join(words)}) isn't part of {who} current FreedomBot "
        "package, so I can't do, draft or prepare that here. An admin can add it with a "
        "change order."
        for d, words in unpurchased.items()
    )


def _mentions_package(reply: str, domain: str, words: list[str] | None = None) -> bool:
    """Whether the reply already says this pack (by name or by the user's words)
    is outside the package."""
    text = (reply or "").casefold()
    if not re.search(r"\b(package|not purchased|change order|not part of)\b", text):
        return False
    names = [domain_title(domain).casefold(), *[w.casefold() for w in words or []]]
    return any(n in text for n in names)


#: Offering to record a fact in organizational memory is allowed in any package.
_MEMORY_OFFER = re.compile(r"\b(record|save|note down|remember|document)\b", re.I)


def _offers_unpurchased(sentence: str, unpurchased: dict[str, list[str]]) -> bool:
    if not _OFFER.search(sentence) or _MEMORY_OFFER.search(sentence):
        return False
    lowered = sentence.casefold()
    return any(
        re.search(pattern, lowered)
        for domain in unpurchased
        for pattern, _ in _PACKAGE_TOPICS.get(domain, ())
    )


def enforce_package(
    reply: str,
    unpurchased: dict[str, list[str]],
    *,
    company: str = "",
    asked_for_action: bool = False,
    answered_visible: bool = False,
) -> tuple[str, bool]:
    """M03-09: never offer work from a domain pack the client has not bought.

    The tools for an unbought pack are already absent, so nothing can run — but
    the model may still *offer* the work ("I can draft that invoice…"). Sentences
    that offer it are removed, and the reply names the pack as outside the
    package whenever the user asked for that work, an offer was removed, or no
    recorded fact answered the question.
    """
    if not unpurchased:
        return reply, False
    changed = False
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", reply or ""):
        sentences = re.split(r"(?<=[.!?])\s+", para)
        kept: list[str] = []
        dropped_previous = False
        for sentence in sentences:
            # "…? Or …" and "Shall I proceed?" continue the offer just removed.
            follows_removed = dropped_previous and (
                re.match(r"\s*or\b", sentence, re.I)
                or (_OFFER.search(sentence) and not _MEMORY_OFFER.search(sentence))
            )
            if _offers_unpurchased(sentence, unpurchased) or follows_removed:
                dropped_previous = True
                continue
            dropped_previous = False
            kept.append(sentence)
        if len(kept) != len(sentences):
            changed = True
        text_ = " ".join(kept).strip()
        if text_:
            paragraphs.append(text_)
    reply = "\n\n".join(paragraphs).strip()
    if not (changed or asked_for_action or not answered_visible):
        return reply, changed
    missing = {d: w for d, w in unpurchased.items() if not _mentions_package(reply, d, w)}
    if not missing:
        return reply, changed
    note = package_message(missing, company)
    if not reply:
        return note.replace("- ", "", 1) if len(missing) == 1 else note, True
    return reply.rstrip() + "\n\n**Not in your package:**\n" + note, True


def blocked_capabilities(ctx: TenantContext, enabled_domains: list[str]) -> list[str]:
    """Capabilities in this workspace the caller lacks the data area for."""
    enabled = set(enabled_domains or [])
    out: list[str] = []
    for spec in TOOL_SPECS:
        if not spec.implemented or not spec.data_area or ctx.can_see_area(spec.data_area):
            continue
        if spec.domains and not (set(spec.domains) & enabled):
            continue
        out.append(f"- {spec.title} — needs {area_label(spec.data_area)} access")
    return out


def hidden_areas(
    ctx: TenantContext, message: str, *, common: set[str] | None = None
) -> dict[str, list[str]]:
    """Restricted areas this message touches that the caller cannot see."""
    query = (message or "").strip()
    if not query:
        return {}
    if common is None:
        try:
            visible = memory.search(query, authoritative_only=True, limit=20, ctx=ctx)
        except Exception:
            visible = []
        common = {w for i in visible for w in memory.word_hits(_item_text(i), query)}
    try:
        hidden = memory.restricted_area_hits(query, ctx=ctx, common=common)
    except Exception:
        hidden = {}
    lowered = query.casefold()
    for area, patterns in _TOPICS.items():
        if ctx.can_see_area(area):
            continue
        found = [label for pattern, label in patterns if re.search(pattern, lowered)]
        if found:
            bucket = hidden.setdefault(area, [])
            bucket.extend(f for f in found if f not in bucket)
            label_text = " ".join(found)
            hidden[area] = [w for w in bucket if w in found or w not in label_text]
    return hidden


def analyze(ctx: TenantContext, message: str, enabled_domains: list[str]) -> Grounding:
    """The grounding section for one turn's system prompt, plus what it flagged."""
    query = (message or "").strip()
    try:
        items = memory.search(query, authoritative_only=True, limit=_MAX_ITEMS * 2, ctx=ctx) if query else []
    except Exception:
        items = []
    # Same bar as restricted matching: one stray shared word ("run", "attendance")
    # is not a record that answers the question.
    words = memory._search_words(query)
    scored = [(i, memory.word_hits(_item_text(i), query)) for i in items]
    common = {w for _, h in scored for w in h}
    # A long multi-part question names each topic with one word ("attendance"),
    # so a single-word hit counts there; a short question needs two.
    minimum = 1 if len(words) >= 6 else 2
    if len(words) > 1:
        scored = [(i, h) for i, h in scored if len(h) >= minimum]
    scored = sorted(scored, key=lambda s: -len(s[1]))[:_MAX_ITEMS]
    hidden = hidden_areas(ctx, query, common=common)
    covered = {w for _, h in scored for w in h}
    hidden = {a: ([w for w in hits if w not in covered] or hits) for a, hits in hidden.items()}
    blocked = blocked_capabilities(ctx, enabled_domains)

    lines = ["## Recorded memory for this question (retrieved for this user's access)"]
    if hidden:
        lines.append(
            "RESTRICTED — this user does NOT have access to these areas, and the question "
            "touches them (the user's own words that point to each area are shown):"
        )
        for area, hits in hidden.items():
            lines.append(f"- {area_label(area)} area ← {', '.join(hits)}")
        lines.append(
            "For every part of the question that belongs to one of these areas, reply that "
            "it is restricted to that named area and an admin can grant access. Assign each "
            "part to the area whose words match it. Do not reveal, guess, confirm figures the "
            "user supplies, or paraphrase restricted content; do not say it does not exist or "
            "is 'not recorded'; do not offer to create it or request access for them. Still "
            "answer the parts that a visible record below does answer."
        )
        lines.append("")
    if scored:
        lines.append(
            "These are the approved records this user may see that match the question. "
            "Answer from them. Quote the value exactly and prefix it with Fact: (or "
            "Estimate: when labelled estimate). Only use the ones that actually answer "
            "the question."
        )
        lines.extend(_render_item(i, h) for i, h in scored)
    else:
        lines.append("No approved record visible to this user matches the question.")

    if blocked:
        lines.append("")
        lines.append(
            "Capabilities this user cannot use for lack of a data area (if asked, say which "
            "access is needed — do not look for the data another way):"
        )
        lines.extend(blocked)

    unpurchased = unpurchased_hits(query, enabled_domains)
    if unpurchased:
        lines.append("")
        lines.append(
            "NOT IN THIS CLIENT'S PACKAGE — the question touches domain packs this client "
            "has not bought (the user's words that point to each are shown):"
        )
        for domain, words in unpurchased.items():
            lines.append(f"- {domain_title(domain)} ← {', '.join(words)}")
        lines.append(
            "For those parts, say plainly that the pack is not part of the client's current "
            "package and an admin can add it with a change order. Do not offer to draft, "
            "prepare, submit, queue or simulate that work, and do not describe a workaround. "
            "This is a package limit, not a data-access restriction — do not say the user "
            "lacks access. Still answer every other part, and any part a recorded fact above "
            "answers."
        )
    return Grounding(
        text="\n".join(lines),
        hidden=hidden,
        visible_keys=[i.memory_key for i, _ in scored if i.memory_key],
        unpurchased=unpurchased,
        unpurchased_action=bool(unpurchased and _ACTION.search(query)),
    )


_MATCH_INTENT = re.compile(r"\b(compare|fit|fits|match|matches|suitable|qualif|hire|hired|hiring)\b", re.I)


def candidate_match_keys(grounded: Grounding, message: str) -> tuple[str, str] | None:
    """(resume_key, opening_key) when the question compares a visible candidate to
    a visible opening — the platform then computes the structured match itself."""
    if not _MATCH_INTENT.search(message or ""):
        return None
    resume = next((k for k in grounded.visible_keys if k.startswith("candidate_resume")), None)
    opening = next((k for k in grounded.visible_keys if k.startswith("job_opening")), None)
    return (resume, opening) if resume and opening else None


def restricted_candidate_reply(grounded: Grounding, message: str) -> str | None:
    """A fixed reply when someone without HR access asks to assess a candidate —
    there is nothing they may be told, so the model is not asked."""
    if "hr" not in grounded.hidden or not _MATCH_INTENT.search(message or ""):
        return None
    return (
        "Candidate, resume and job-opening details are restricted to the HR area, "
        "which your account doesn't have access to — an admin can grant it."
    )


def build(ctx: TenantContext, message: str, enabled_domains: list[str]) -> str:
    return analyze(ctx, message, enabled_domains).text


def names_area(reply: str, area: str) -> bool:
    text = (reply or "").casefold()
    return any(re.search(p, text) for p in _AREA_MENTIONS.get(area, (rf"\b{area}\b",)))


_SPECULATION = re.compile(
    r"\b(typically|usually|generally|in general|general idea|commonly|common practice|"
    r"standard practice|reasonable guess|best guess|most companies|companies often|"
    r"general description)\b",
    re.I,
)


def _strip_speculation(reply: str) -> str:
    """Drop paragraphs that speculate ("typically…") and the lists that follow them."""
    kept: list[str] = []
    dropping = False
    for para in re.split(r"\n\s*\n", reply or ""):
        is_list = all(re.match(r"\s*([-*•]|\d+[.)])\s", line) for line in para.splitlines() if line.strip())
        # A paragraph quoting a recorded Fact is grounded, whatever words it uses.
        if _SPECULATION.search(para) and "fact:" not in para.casefold():
            dropping = True
            continue
        if dropping and is_list:
            continue
        dropping = False
        kept.append(para)
    return "\n\n".join(kept).strip()


def restriction_message(hidden: dict[str, list[str]]) -> str:
    return "\n".join(
        f"That information ({', '.join(hidden[a])}) is restricted to the {area_label(a)} area, "
        "which your account doesn't have access to — an admin can grant it."
        for a in hidden
    )


def enforce_restrictions(
    reply: str, hidden: dict[str, list[str]], *, answered_visible: bool = True
) -> tuple[str, bool]:
    """Make sure every restricted area the question touched is named in the reply.

    The model sometimes answers a blocked part with "no record" or folds it into
    another area. The access facts are deterministic, so the platform states
    them itself rather than relying on phrasing. "What is typical" paragraphs
    are removed — generic content about a restricted record can reveal it — and
    if nothing grounded is left the reply becomes the restriction notice.
    """
    changed = False
    if hidden and _SPECULATION.search(reply or ""):
        stripped = _strip_speculation(reply)
        reply = stripped if (stripped and answered_visible) else restriction_message(hidden)
        changed = True
    missing = [a for a in hidden if not names_area(reply, a)]
    if not missing:
        return reply, changed
    notes = [
        f"- Anything about {', '.join(hidden[a])} is restricted to the {area_label(a)} area, "
        "which your account doesn't have access to — an admin can grant it."
        for a in missing
    ]
    return (reply.rstrip() + "\n\n**Access note:**\n" + "\n".join(notes)), True
