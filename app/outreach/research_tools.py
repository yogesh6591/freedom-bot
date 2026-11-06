"""Web research on saved prospects, started from the chat and run in the background, one person at a time.

* ``research_prospects`` queues the named (or filtered) people. Nothing researches on its own after a search.
* A worker thread takes one queued person at a time, asks GPT with web search, saves the notes, the source links
  and the three scores (fit, timing, reachability) and marks the person suitable or not suitable with the reason.
* People who are queued or being researched are on hold: they cannot be emailed until their research is done.
* ``show_research`` and ``progress`` say how far the latest batch is (the chat answer and the bell card).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from acquisition.config import get_settings
from acquisition.models.prospect import AcquisitionStatus, Prospect, ProspectResearchSource
from app.outreach import prospect_tools as people_tools
from app.outreach.toolkit import Call, ToolError, tool

log = logging.getLogger(__name__)

MAX_RESEARCH = 20  # people per request
MAX_SOURCES = 10  # source links kept per person
MIN_FIT = 50  # a researched person below this fit is "not suitable"
SIGNAL_WORDS = ("hiring, growth, expansion, acquisition, leadership change, new offering, launch, scaling, "
                "funding, technology, operational complexity")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ asking GPT
def _prompt(p: Prospect, rules: Any) -> str:
    kinds = "; ".join(c.name for c in sorted(rules.categories, key=lambda c: c.priority)) or "consulting and advisory firms"
    skip = ", ".join(rules.exclusions) or "none"
    facts = [f"Company: {p.company or 'unknown'}", f"Website: {p.website or 'unknown'}",
             f"Contact: {p.executive_name or 'unknown'}" + (f", {p.executive_title}" if p.executive_title else ""),
             f"Headcount: {p.headcount_band or 'unknown'}", f"Revenue: {p.revenue_band or 'unknown'}"]
    return (
        "Research this company on the public web to judge whether it matches the good-fit description below. "
        "Judge only the match with that description; whether the firm already uses AI is not a concern.\n" + "\n".join(facts) + "\n\n"
        f"Good-fit firms: {kinds}. About {rules.employees_min} to {rules.employees_max} employees and "
        f"${rules.revenue_min // 1_000_000}M to ${rules.revenue_max // 1_000_000}M revenue (revenue is a soft signal). "
        f"Not wanted: {skip}.\n\n"
        "Search the company's website, news, job postings and public posts. Use only what you actually find; "
        "never invent a fact; use null when unknown. Reply with ONE JSON object and nothing else:\n"
        '{"summary": "2 plain sentences on what the firm does",'
        ' "business_type": "short label", "industry": "short label",'
        ' "headcount_estimate": "e.g. 40-60 or null", "revenue_estimate": "e.g. $5M-$10M or null",'
        ' "city": "or null", "state": "US state name or null",'
        ' "signals": ["only things that CHANGED in roughly the last 12 months, each a short plain sentence with its '
        f'date, such as {SIGNAL_WORDS}. Plain facts about the firm (when it was founded, what it offers, how big it '
        'is) are NOT signals. Empty list if you found no recent change"],'
        ' "fit_reasons": ["why they match the good-fit description"],'
        ' "concerns": ["only ways they do NOT match the good-fit description (wrong kind of firm, too big or too small, excluded kind); empty if none"],'
        ' "icp_category": "exactly one of: ' + " | ".join([c.name for c in rules.categories] + ["Other"]) + ', judged from what the firm really does",'
        ' "excluded_kind": "the excluded kind of business this is, or null",'
        ' "sources": ["the full web addresses of the pages you actually used"]}'
    )


def _json_in(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON in the answer")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("the answer was not an object")
    return data


def _clean(url: str) -> str:
    """The address without the tracking parameters the web search adds (utm_source=openai and the like)."""
    url = re.sub(r"([?&])utm_[a-z_]+=[^&#\s)]*&?", r"\1", url)
    return re.sub(r"[?&]+(?=[)\s]|$)", "", url)


def _pct(value: Any) -> int:
    """A score as a whole number, .5 rounding up, so every place shows the same number."""
    return int(float(value or 0) + 0.5)


def _citations(response: Any) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            for note in getattr(part, "annotations", None) or []:
                url = _clean(str(getattr(note, "url", "") or ""))
                if getattr(note, "type", "") == "url_citation" and url and url not in seen:
                    seen.add(url)
                    out.append({"url": url[:1024], "title": str(getattr(note, "title", "") or "")[:300]})
    return out[:MAX_SOURCES]


def _searched(response: Any) -> list[dict]:
    """Every page the web search looked at (the search call's own list), whether or not the answer cites it."""
    out: list[dict] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "web_search_call":
            continue
        action = getattr(item, "action", None)
        for src in (getattr(action, "sources", None) or []) if action is not None else []:
            url = str(getattr(src, "url", "") or (src.get("url") if isinstance(src, dict) else "") or "")
            if url:
                out.append({"url": url[:1024], "title": ""})
    return out


def _merge_sources(*groups: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for group in groups:
        for src in group:
            url = _clean(str(src.get("url") or "").strip())
            if url.lower().startswith("http") and url not in seen:
                seen.add(url)
                out.append({"url": url[:1024], "title": str(src.get("title") or "")[:300]})
    return out[:MAX_SOURCES]


def ask_web(prompt: str) -> tuple[dict, list[dict]]:
    """(the JSON GPT answered, the pages it cited). Raises when there is no key or the answer is unusable."""
    from openai import OpenAI

    settings = get_settings()
    key = (settings.openai_api_key or "").strip()  # the key alone; this does not depend on AI_REPLY_PROVIDER
    if not key:
        raise RuntimeError("no OpenAI key is set up")
    model = (settings.openai_web_model or settings.openai_model or "").strip() or "gpt-4.1"
    client = OpenAI(api_key=key, timeout=240)
    context = (os.environ.get("WEB_SEARCH_CONTEXT") or "high").strip().lower()
    effort = (os.environ.get("WEB_SEARCH_EFFORT") or "medium").strip().lower()
    tool_deep = {"type": "web_search", "search_context_size": context if context in ("low", "medium", "high") else "high"}
    tries = [  # the deepest first; a model or API that refuses an option falls back to the next one
        {"tools": [tool_deep], "reasoning": {"effort": effort}, "include": ["web_search_call.action.sources"]},
        {"tools": [tool_deep], "include": ["web_search_call.action.sources"]},
        {"tools": [{"type": "web_search"}]},
    ]
    if effort in ("", "none", "off"):
        tries = tries[1:]
    response = None
    for i, options in enumerate(tries):
        try:
            response = client.responses.create(model=model, input=prompt, **options)
            break
        except Exception as exc:  # noqa: BLE001
            # only a refused option falls back to a simpler request; a rate limit or timeout is not retried here
            if i == len(tries) - 1 or type(exc).__name__ not in ("BadRequestError", "UnprocessableEntityError"):
                raise
    data = _json_in(getattr(response, "output_text", "") or "")
    listed = [{"url": str(u), "title": ""} for u in (data.get("sources") or []) if isinstance(u, str)] \
        if isinstance(data.get("sources"), list) else []
    return data, _merge_sources(_citations(response), listed, _searched(response))


# ------------------------------------------------------------------------------------------------ saving it
def _lines(label: str, items: Any) -> str:
    rows = [str(x).strip() for x in (items or []) if str(x).strip()] if isinstance(items, list) else []
    return f"{label}: " + " | ".join(rows) if rows else ""


def _verdict(p: Prospect, concerns: list, reasons: list) -> str:
    status, fit = p.acquisition_status, float(p.fit_score or 0)
    if status == AcquisitionStatus.EXCLUDED:
        why = "an excluded kind of business"
    elif status == AcquisitionStatus.UNREACHABLE:
        why = "no usable business email"
    elif status == AcquisitionStatus.NEEDS_MORE_RESEARCH:
        why = "not enough information found"
    elif fit < MIN_FIT:
        why = "weak fit" + (f" ({concerns[0]})" if concerns else "")
    else:
        return "suitable" + (": " + "; ".join(reasons[:2]) if reasons else "")
    return "not suitable: " + why


def apply_result(db: Any, p: Prospect, data: dict, sources: list[dict]) -> None:
    """Save what the web research found on the person, then score them."""
    from acquisition.services.prospect_scoring_service import ProspectScoringService

    def text(key: str) -> Optional[str]:
        value = str(data.get(key) or "").strip()
        return value if value and value.lower() != "null" else None

    signals = [str(s).strip() for s in (data.get("signals") or []) if str(s).strip()] if isinstance(data.get("signals"), list) else []
    concerns = [str(s).strip() for s in (data.get("concerns") or []) if str(s).strip()] if isinstance(data.get("concerns"), list) else []
    if text("summary"):
        p.company_description = text("summary")[:4000]
    p.industry = (text("industry") or p.industry or "")[:255] or None
    p.business_type = (text("business_type") or p.business_type or "")[:128] or None
    p.public_signals = signals
    p.city = p.city or (text("city") or "")[:128] or None
    p.state = p.state or (text("state") or "")[:128] or None
    p.headcount_band = p.headcount_band or (text("headcount_estimate") or "")[:100] or None
    p.revenue_band = p.revenue_band or (text("revenue_estimate") or "")[:100] or None
    excluded = text("excluded_kind")
    if excluded:
        concerns.append(f"looks like {excluded}")
    from acquisition.services.prospecting_rules_service import get_prospecting_rules

    said = text("icp_category")
    if said:
        names = [c.name for c in get_prospecting_rules().categories]
        p.icp_category = "Other" if excluded else next((n for n in names if n.casefold() == said.casefold()), "Other")
    if (p.source or "") == "Exa + Prospeo" and not p.email_confidence:
        p.email_confidence = "verified"  # Prospeo only returns verified emails
    body = "\n".join(x for x in (
        text("summary") and f"Summary: {text('summary')}", _lines("Signals", signals),
        _lines("Why it fits", data.get("fit_reasons")), _lines("Concerns", concerns)) if x)
    p.research_notes = _clean(body)[:8000] or None
    p.last_researched_at = _now()
    p.acquisition_status = AcquisitionStatus.RESEARCHED
    for old in db.scalars(select(ProspectResearchSource).where(ProspectResearchSource.prospect_id == p.id,
                                                              ProspectResearchSource.category == "web research")):
        db.delete(old)
    for src in sources:
        db.add(ProspectResearchSource(prospect_id=p.id, source_url=src["url"], source_type="web",
                                      category="web research", note=src["title"] or None, confidence=0.7))
    db.add(p)
    db.commit()
    ProspectScoringService(db).score_prospect(p)
    scores = (f"Fit {_pct(p.fit_score)}, timing {_pct(p.timing_score)}, "
              f"reach {_pct(p.reachability_score)}, priority {_pct(p.priority_score)}")
    why = [str(x).strip() for x in data["fit_reasons"] if str(x).strip()] if isinstance(data.get("fit_reasons"), list) else []
    p.research_notes = f"Verdict: {_verdict(p, concerns, why)}. {scores}.\n" + (p.research_notes or "")
    db.add(p)
    db.commit()


# ------------------------------------------------------------------------------------------------ the worker
def tick(ask: Optional[Callable[[str], tuple[dict, list[dict]]]] = None, db_factory: Optional[Callable] = None) -> bool:
    """Research the next queued person. True when there was someone."""
    from acquisition.db.session import SessionLocal
    from acquisition.services.prospecting_rules_service import get_prospecting_rules

    db = (db_factory or SessionLocal)()
    try:
        p = db.scalars(select(Prospect).where(Prospect.research_status == "queued")
                       .order_by(Prospect.research_updated_at, Prospect.created_at)
                       .with_for_update(skip_locked=True).limit(1)).first()
        if p is None:
            return False
        p.research_status, p.research_updated_at = "running", _now()
        db.commit()
        try:
            data, sources = (ask or ask_web)(_prompt(p, get_prospecting_rules()))
            apply_result(db, p, data, sources)
            p.research_status = "done"
        except Exception as exc:  # noqa: BLE001 - one person failing must not stop the others
            db.rollback()
            log.warning("research failed for %s: %s", p.email, exc)
            p = db.get(Prospect, p.id)
            p.research_status = "failed"
        p.research_updated_at = _now()
        db.commit()
        return True
    finally:
        db.close()


def start() -> Optional[Callable[[], None]]:
    """Start the research loop in a daemon thread; returns a stop function. Call from the app lifespan."""
    from acquisition.db.session import SessionLocal

    db = SessionLocal()
    try:  # a restart in the middle of a research puts that person back in the queue
        for p in db.scalars(select(Prospect).where(Prospect.research_status == "running")):
            p.research_status = "queued"
        db.commit()
    finally:
        db.close()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                busy = tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("research: tick failed: %s", exc)
                busy = False
            stop.wait(0.5 if busy else 3.0)

    threading.Thread(target=loop, name="prospect-research", daemon=True).start()
    log.info("research: worker started")
    return stop.set


# ------------------------------------------------------------------------------------------------ progress
def progress(db: Any) -> Optional[dict]:
    """How far the latest research batch is, or None when nothing was ever researched from the chat."""
    batch = db.scalar(select(Prospect.research_batch).where(Prospect.research_batch.is_not(None))
                      .order_by(Prospect.research_updated_at.desc()).limit(1))
    if not batch:
        return None
    rows = list(db.scalars(select(Prospect).where(Prospect.research_batch == batch)
                           .order_by(Prospect.research_updated_at, Prospect.executive_name)))
    name = lambda p: f"{p.executive_name} ({p.company})" if (p.company or "").strip() else p.executive_name  # noqa: E731
    finished = []
    for p in rows:
        if p.research_status == "done":
            first = (p.research_notes or "").split("\n", 1)[0]
            verdict = first[len("Verdict: "):].split(". Fit ", 1)[0] if first.startswith("Verdict: ") else ""
            finished.append({"name": name(p), "verdict": verdict,
                             "priority": float(p.priority_score) if p.priority_score is not None else None})
    return {
        "batch": batch, "total": len(rows), "done": len(finished), "finished": finished,
        "failed": [name(p) for p in rows if p.research_status == "failed"],
        "running": [name(p) for p in rows if p.research_status == "running"],
        "waiting": [name(p) for p in rows if p.research_status == "queued"],
    }


def describe(info: Optional[dict]) -> str:
    if info is None:
        return "No research has been started yet."
    parts = [f"Research: {info['done']} of {info['total']} done."]
    if info["running"]:
        parts.append("Researching now: " + ", ".join(info["running"]) + ".")
    if info["waiting"]:
        parts.append("Waiting: " + ", ".join(info["waiting"]) + ".")
    if info["failed"]:
        parts.append("Failed (can be asked again): " + ", ".join(info["failed"]) + ".")
    if info["finished"]:
        ranked = sorted(info["finished"], key=lambda r: -(r["priority"] if r["priority"] is not None else -1))
        parts.append("Done, best first: " + "; ".join(
            f"{r['name']} - {r['verdict'] or 'done'}" + (f", priority {_pct(r['priority'])}" if r["priority"] is not None else "")
            for r in ranked) + ".")
    return " ".join(parts)


# ------------------------------------------------------------------------------------------------ chat tools
@tool("research_prospects", "people", "where")
def research_prospects(call: Call, people: Optional[list] = None, where: Optional[list] = None) -> str:
    """Queue web research for the named (or filtered) people. They are researched one at a time in the background."""
    targets = people_tools._targets(call, people, where)
    if len(targets) > MAX_RESEARCH:
        raise ToolError(f"{len(targets)} people is too many for one request (max {MAX_RESEARCH}). Ask the user to narrow it.")
    batch = str(uuid.uuid4())
    queued: list[Prospect] = []
    busy: list[str] = []
    done: list[str] = []
    for p in targets:
        if p.research_status in ("queued", "running"):
            busy.append(p.executive_name)
            continue
        if not people and p.research_status == "done":
            done.append(p.executive_name)  # a group or "all": only the ones not researched yet; a named person is redone
            continue
        p.research_status, p.research_batch, p.research_updated_at = "queued", batch, _now()
        queued.append(p)
    call.db.commit()
    skipped = f" Already researched, skipped (name them to research again): {', '.join(done)}." if done else ""
    if not queued:
        return "Nothing queued." + (f" {', '.join(busy)} already in research." if busy else "") + skipped
    names = ", ".join(p.executive_name for p in queued[:8]) + (f" and {len(queued) - 8} more" if len(queued) > 8 else "")
    extra = f" Already in research, left as they were: {', '.join(busy)}." if busy else ""
    extra += skipped
    return (f"Queued {len(queued)} for web research: {names}. They are researched one at a time in the background "
            f"and cannot be emailed until their research is done.{extra} Tell the user the bell shows progress, "
            "and that show_research gives the details.")


@tool("show_research")
def show_research(call: Call) -> str:
    """How the latest research batch is going: who is done (with the verdict), who is running, who is waiting."""
    return describe(progress(call.db))
