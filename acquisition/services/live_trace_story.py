"""Assemble an operator-friendly Live Trace story from existing trace events.

Presentation-only: does not change discovery behavior. Secrets are scrubbed
before any request payload is exposed to the Admin UI.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|secret|password|token|credential|bearer)",
    re.I,
)
_SOURCE_ORDER = ("openai_web",)
_SOURCE_LABELS = {
    "openai_web": "OpenAI Web",
    "nppes": "NPPES",
    "exa": "Exa",
}


_LIST_LIMIT_DEFAULT = 80
_LIST_LIMIT_BY_KEY = {
    "surviving_pool": 300,
    "accepted_companies": 300,
    "returned_companies": 300,
    "produced_companies": 300,
    "selected": 120,
    "not_selected": 300,
    "candidates": 300,
    "companies": 120,
}


def scrub_payload(value: Any, *, depth: int = 0, list_limit: int = _LIST_LIMIT_DEFAULT) -> Any:
    """Remove secrets from nested payloads for safe Admin display."""
    if depth > 5:
        return str(value)[:200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _SECRET_KEY_RE.search(key):
                out[key] = "[redacted]"
                continue
            child_limit = _LIST_LIMIT_BY_KEY.get(key, _LIST_LIMIT_DEFAULT)
            out[key] = scrub_payload(v, depth=depth + 1, list_limit=child_limit)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_payload(v, depth=depth + 1) for v in list(value)[: max(1, int(list_limit))]]
    text = str(value)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text if len(text) <= 4000 else text[:3997] + "…"


def _source_key(label_or_name: str | None) -> str | None:
    text = (label_or_name or "").strip().lower()
    if not text:
        return None
    if text in _SOURCE_LABELS:
        return text
    if "nppes" in text:
        return "nppes"
    if "exa" in text:
        return "exa"
    if "openai" in text:
        return "openai_web"
    return None


def _ensure_source(story: dict[str, Any], key: str) -> dict[str, Any]:
    sources = story.setdefault("sources", {})
    if key not in sources:
        sources[key] = {
            "key": key,
            "label": _SOURCE_LABELS.get(key, key),
            "status": "pending",
            "fetch_limit": None,
            "slices": [],
            "requests": [],
            "raw_received": 0,
            "candidates_produced": 0,
            "survived": 0,
            "accepted_companies": [],
            "returned_companies": [],
            "errors": [],
            "_raw_locked": False,
            "_cand_locked": False,
        }
    return sources[key]


def _classify_reject(title: str) -> str:
    t = title.lower()
    if "duplicate" in t or "already known after redirect" in t:
        return "Duplicates removed"
    if "already known" in t or "researched" in t:
        return "Known skipped"
    if "excluded" in t:
        return "Excluded"
    if "website" in t or "verify" in t or "unreachable" in t:
        return "Website failures"
    if "list" in t or "directory" in t or "review" in t or "social" in t or "news" in t:
        return "Excluded"
    return "Excluded"


def assemble_live_trace_story(
    events: list[dict[str, Any]],
    run: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build grouped operator sections from raw Live Trace events."""
    run = run or {}
    is_preview = (run.get("cycle_type") or "") == "discovery_preview" or bool(
        run.get("stop_reason") == "preview_complete"
    )
    story: dict[str, Any] = {
        "title": "Discovery Preview" if is_preview else "Discovery run",
        "is_preview": is_preview,
        "status": run.get("status") or "unknown",
        "requested": None,
        "mode": "Real providers",
        "started_at": run.get("started_at"),
        "duration_s": run.get("duration_s"),
        "plan": {
            "status": "pending",
            "requested": None,
            "categories": [],
            "geography": None,
            "slices": [],
            "fetch_limits": {},
        },
        "sources": {},
        "filtering": {
            "status": "pending",
            "entering": 0,
            "removed": {},
            "remaining": 0,
            "details": [],
        },
        "triage": {
            "status": "pending",
            "compared": 0,
            "pool_available": None,
            "sent_to_ai": None,
            "considered_count": None,
            "requested": None,
            "model": None,
            "selected": [],
            "not_selected": [],
            "fallback": False,
            "selection_summary": None,
        },
        "downstream": {"save": 0, "research": 0, "score": 0, "decision": 0},
        "result": {
            "status": "pending",
            "discovered": 0,
            "filtered": 0,
            "compared": 0,
            "selected": 0,
            "preview_stopped": is_preview,
            "selected_names": [],
            "new": run.get("new"),
            "usable": run.get("usable"),
            "researched": run.get("researched"),
            "scored": run.get("scored"),
            "ready": 0,
            "unreachable": 0,
            "excluded": 0,
            "scores": [],
        },
        "errors": [],
        "event_count": len(events),
    }

    accepted: list[dict[str, Any]] = []
    reject_details: list[dict[str, Any]] = []
    removed_counts: dict[str, int] = {
        "Duplicates removed": 0,
        "Known skipped": 0,
        "Excluded": 0,
        "Website failures": 0,
    }
    scores_by_company: dict[str, dict[str, Any]] = {}
    last_fit: float | str | None = None
    last_timing: float | str | None = None
    last_reach: float | str | None = None
    last_priority: float | str | None = None

    for ev in events:
        stage = ev.get("stage") or ""
        title = ev.get("title") or ""
        detail = scrub_payload(ev.get("detail") or {})
        if not isinstance(detail, dict):
            detail = {}
        company = ev.get("company")
        level = ev.get("level") or "info"

        if stage == "run" and "preview" in title.lower():
            story["is_preview"] = True
            story["title"] = "Discovery Preview"
            story["result"]["preview_stopped"] = True
        if stage == "run" and detail.get("requested") is not None and story["requested"] is None:
            story["requested"] = detail.get("requested")
            story["plan"]["requested"] = detail.get("requested")
            story["triage"]["requested"] = detail.get("requested")
            story["plan"]["status"] = "active"

        if stage == "icp":
            geo = detail.get("geography")
            if isinstance(geo, dict):
                if geo.get("nationwide"):
                    story["plan"]["geography"] = "United States (nationwide)"
                else:
                    states = geo.get("states") or []
                    story["plan"]["geography"] = (
                        ", ".join(str(s) for s in states) if states else "United States"
                    )

        if stage == "plan":
            story["plan"]["status"] = "active"
            if detail.get("requested") is not None:
                story["plan"]["requested"] = detail.get("requested")
                story["requested"] = detail.get("requested")
                story["triage"]["requested"] = detail.get("requested")
            if detail.get("categories"):
                story["plan"]["categories"] = list(detail["categories"])
            if detail.get("priority_states") and not story["plan"]["geography"]:
                states = detail.get("priority_states") or []
                story["plan"]["geography"] = (
                    "United States · priority states: " + ", ".join(str(s) for s in states)
                    if states
                    else "United States"
                )
            fl = detail.get("fetch_limits") or {}
            if isinstance(fl, dict) and fl:
                story["plan"]["fetch_limits"] = {str(k): fl[k] for k in fl}
                for k, v in fl.items():
                    sk = _source_key(str(k))
                    if sk:
                        src = _ensure_source(story, sk)
                        src["fetch_limit"] = v
                        src["status"] = "planned"
            sources_meta = detail.get("sources") or {}
            if isinstance(sources_meta, dict):
                for k, meta in sources_meta.items():
                    sk = _source_key(str(k))
                    if not sk:
                        continue
                    src = _ensure_source(story, sk)
                    if isinstance(meta, dict) and meta.get("fetch_limit") is not None:
                        src["fetch_limit"] = meta.get("fetch_limit")
                        story["plan"]["fetch_limits"][sk] = meta.get("fetch_limit")
                    src["status"] = "planned"
            if "Slice →" in title:
                slice_key = detail.get("slice")
                m = re.search(r"Slice → ([^:]+):", title)
                label = m.group(1).strip() if m else ""
                sk = _source_key(label) or _source_key(str(slice_key or ""))
                place = title.split(":", 1)[-1].split("· fetch remaining")[0].strip() if m else title
                if sk:
                    src = _ensure_source(story, sk)
                    src["status"] = "requesting"
                    entry = {
                        "slice": slice_key,
                        "summary": place,
                        "fetch_remaining": detail.get("fetch_remaining"),
                    }
                    if place and not any(s.get("slice") == slice_key for s in src["slices"]):
                        src["slices"].append(entry)
                    story["plan"]["slices"].append(
                        {
                            "source": sk,
                            "label": _SOURCE_LABELS.get(sk, sk),
                            "summary": place,
                            "slice": slice_key,
                        }
                    )
            if "Candidate pool" in title or "Combined surviving candidate pool" in title:
                story["plan"]["status"] = "done"
                story["filtering"]["status"] = "done"
                if detail.get("pool_before_cap") is not None:
                    story["triage"]["pool_available"] = int(detail["pool_before_cap"])
                    story["filtering"]["remaining"] = int(detail["pool_before_cap"])
                    story["result"]["filtered"] = int(detail["pool_before_cap"])
                elif detail.get("surviving") is not None:
                    story["triage"]["pool_available"] = int(detail["surviving"])
                    story["filtering"]["remaining"] = int(detail["surviving"])
                    story["result"]["filtered"] = int(detail["surviving"])
                elif detail.get("surviving_count") is not None:
                    story["triage"]["pool_available"] = int(detail["surviving_count"])
                    story["filtering"]["remaining"] = int(detail["surviving_count"])
                    story["result"]["filtered"] = int(detail["surviving_count"])
                elif detail.get("pool_available") is not None:
                    post_cap = int(detail["pool_available"])
                    if story["triage"].get("pool_available") is None:
                        story["triage"]["pool_available"] = post_cap
                        story["filtering"]["remaining"] = post_cap
                    story["result"]["filtered"] = int(story["filtering"]["remaining"] or post_cap)
                if detail.get("sent_to_global_ai_comparison") is not None:
                    n = int(detail["sent_to_global_ai_comparison"])
                    story["triage"]["sent_to_ai"] = n
                    story["triage"]["compared"] = n
                    story["triage"]["considered_count"] = n
                elif detail.get("sent_to_ai") is not None:
                    story["triage"]["sent_to_ai"] = int(detail["sent_to_ai"])
                    story["triage"]["compared"] = int(detail["sent_to_ai"])
                    story["triage"]["considered_count"] = int(detail["sent_to_ai"])
                elif detail.get("triage_cap") is not None and story["triage"].get("sent_to_ai") is None:
                    story["triage"]["sent_to_ai"] = int(detail["triage_cap"])
                by_source = detail.get("by_source") or {}
                if isinstance(by_source, dict):
                    for k, meta in by_source.items():
                        sk = _source_key(str(k))
                        if not sk or not isinstance(meta, dict):
                            continue
                        src = _ensure_source(story, sk)
                        if meta.get("accepted") is not None:
                            src["survived"] = int(meta.get("accepted") or 0)
                        if meta.get("raw_rows") is not None:
                            src["raw_received"] = int(meta.get("raw_rows") or 0)
                            src["_raw_locked"] = True
                        if meta.get("harvested") is not None:
                            src["candidates_produced"] = int(meta.get("harvested") or 0)
                            src["_cand_locked"] = True
                        if meta.get("fetch_limit") is not None:
                            src["fetch_limit"] = meta.get("fetch_limit")
                        src["status"] = "done" if src["raw_received"] or src["survived"] else src["status"]
                slices = detail.get("slices") or []
                if isinstance(slices, list):
                    for sl in slices[:40]:
                        if not isinstance(sl, dict):
                            continue
                        sk = _source_key(str(sl.get("source") or ""))
                        if not sk:
                            continue
                        summary = sl.get("key") or ""
                        story["plan"]["slices"].append(
                            {
                                "source": sk,
                                "label": _SOURCE_LABELS.get(sk, sk),
                                "summary": summary,
                                "slice": sl.get("key"),
                            }
                        )
            if detail.get("geography") and not story["plan"]["geography"]:
                story["plan"]["geography"] = detail.get("geography")
            if "exceeds triage cap" in title.lower():
                if detail.get("pool_available") is not None:
                    story["triage"]["pool_available"] = int(detail["pool_available"])
                    story["filtering"]["remaining"] = int(detail["pool_available"])
                    story["result"]["filtered"] = int(detail["pool_available"])
                if detail.get("triage_cap") is not None:
                    story["triage"]["sent_to_ai"] = int(detail["triage_cap"])
                story["filtering"]["status"] = "done"
        if stage == "harvest":
            sk = _source_key(str(detail.get("source") or ""))
            if not sk:
                for key in _SOURCE_ORDER:
                    if _SOURCE_LABELS[key].lower() in title.lower() or key in title.lower():
                        sk = key
                        break
            if "REQUEST" in title and sk:
                src = _ensure_source(story, sk)
                src["status"] = "requesting"
                if detail.get("fetch_limit") is not None:
                    src["fetch_limit"] = detail.get("fetch_limit")
                    story["plan"]["fetch_limits"][sk] = detail.get("fetch_limit")
                if detail.get("prompt") or detail.get("model"):
                    req = {
                        "prompt": detail.get("prompt"),
                        "model": detail.get("model"),
                    }
                    scrubbed = scrub_payload(req)
                    if scrubbed not in src["requests"]:
                        src["requests"].append(scrubbed)
                queries = detail.get("queries") or detail.get("slices") or []
                if isinstance(queries, list):
                    for q in queries:
                        if not isinstance(q, dict):
                            continue
                        place = " · ".join(
                            str(p)
                            for p in [q.get("category"), q.get("place"), q.get("angle")]
                            if p
                        )
                        if place and not any(s.get("slice") == q.get("slice") for s in src["slices"]):
                            src["slices"].append(
                                {
                                    "slice": q.get("slice"),
                                    "summary": place,
                                    "fetch_remaining": q.get("requested"),
                                }
                            )
                            story["plan"]["slices"].append(
                                {
                                    "source": sk,
                                    "label": _SOURCE_LABELS.get(sk, sk),
                                    "summary": place,
                                    "slice": q.get("slice"),
                                }
                            )
                        req = q.get("request")
                        if isinstance(req, dict):
                            scrubbed = scrub_payload(req)
                            if scrubbed not in src["requests"]:
                                src["requests"].append(scrubbed)
            elif (
                "RESULT" in title
                or "Requested/fetch limit" in title
                or "returned" in title.lower()
            ) and sk:
                src = _ensure_source(story, sk)
                src["status"] = "error" if level == "error" else "done"
                if detail.get("fetch_limit") is not None:
                    src["fetch_limit"] = detail.get("fetch_limit")
                if detail.get("raw_received") is not None:
                    src["raw_received"] = int(detail["raw_received"])
                    src["_raw_locked"] = True
                if detail.get("candidates_produced") is not None:
                    src["candidates_produced"] = int(detail["candidates_produced"])
                    src["_cand_locked"] = True
                if detail.get("returned") is not None and not src.get("_raw_locked"):
                    src["raw_received"] = int(detail["returned"])
                    src["_raw_locked"] = True
                if detail.get("survived") is not None:
                    src["survived"] = int(detail["survived"])
                req = detail.get("request")
                if isinstance(req, dict):
                    scrubbed = scrub_payload(req)
                    if scrubbed not in src["requests"]:
                        src["requests"].append(scrubbed)
                # Prefer API returns (pre-filter) for Source results table.
                for key in ("returned_companies", "produced_companies"):
                    for row in detail.get(key) or []:
                        if isinstance(row, dict) and len(src["returned_companies"]) < 300:
                            src["returned_companies"].append(row)
                for row in detail.get("accepted_companies") or []:
                    if isinstance(row, dict) and len(src["accepted_companies"]) < 300:
                        src["accepted_companies"].append(row)
                if level == "error":
                    src["errors"].append(title)
            elif sk:
                src = _ensure_source(story, sk)
                src["status"] = "error" if level == "error" else "harvested"
                req = detail.get("request")
                if isinstance(req, dict):
                    scrubbed = scrub_payload(req)
                    if scrubbed not in src["requests"]:
                        src["requests"].append(scrubbed)
                if detail.get("raw_received") is not None and not src["_raw_locked"]:
                    src["raw_received"] = int(src.get("raw_received") or 0) + int(detail["raw_received"])
                if detail.get("candidates_produced") is not None and not src["_cand_locked"]:
                    src["candidates_produced"] = int(src.get("candidates_produced") or 0) + int(
                        detail["candidates_produced"]
                    )
                if level == "error":
                    src["errors"].append(title)
                    src["status"] = "error"

        if stage in {"filter", "known", "verify", "dedupe"}:
            story["filtering"]["status"] = "active"
            if "Filtering summary" in title:
                story["filtering"]["status"] = "done"
                if detail.get("evaluated") is not None:
                    story["filtering"]["entering"] = int(detail["evaluated"])
                if detail.get("remaining") is not None:
                    story["filtering"]["remaining"] = int(detail["remaining"])
                    story["result"]["filtered"] = int(detail["remaining"])
                removed = detail.get("removed") or {}
                if isinstance(removed, dict):
                    for k, v in removed.items():
                        try:
                            removed_counts[str(k)] = int(v or 0)
                        except (TypeError, ValueError):
                            continue
                for row in detail.get("accepted_companies") or []:
                    if isinstance(row, dict):
                        accepted.append(row)
                        sk = _source_key(str(row.get("source") or ""))
                        if sk:
                            src = _ensure_source(story, sk)
                            if len(src["accepted_companies"]) < 40:
                                src["accepted_companies"].append(row)
                for row in detail.get("failed_websites") or []:
                    if isinstance(row, dict):
                        reject_details.append(
                            {
                                "company": row.get("company"),
                                "reason": row.get("reason") or "website failed",
                                "stage": "verify",
                                "source": row.get("source"),
                            }
                        )
            elif title.startswith("Accepted into pool:"):
                name = company or title.split(":", 1)[-1].strip()
                row = {
                    "company": name,
                    "source": detail.get("source"),
                    "state": detail.get("state"),
                    "city": detail.get("city"),
                    "domain": detail.get("domain"),
                }
                accepted.append(row)
                sk = _source_key(str(detail.get("source") or ""))
                if sk:
                    src = _ensure_source(story, sk)
                    if len(src["accepted_companies"]) < 40:
                        src["accepted_companies"].append({**row, "source": sk})
            elif title.startswith("Rejected ") or title.startswith("Already known"):
                reason = title.split(":", 1)[-1].strip() if ":" in title else title
                bucket = _classify_reject(title)
                removed_counts[bucket] = removed_counts.get(bucket, 0) + 1
                reject_details.append(
                    {
                        "company": company
                        or title.split(":", 1)[0].replace("Rejected ", "").replace("Already known", "").strip(),
                        "reason": reason,
                        "stage": stage,
                        "source": detail.get("source"),
                    }
                )

        if stage == "triage":
            story["triage"]["status"] = "active"
            if "AI triage unavailable" in title:
                story["triage"]["fallback"] = True
            if detail.get("model"):
                story["triage"]["model"] = detail.get("model")
            if detail.get("requested") is not None:
                story["triage"]["requested"] = detail.get("requested")
                story["requested"] = detail.get("requested")
            if detail.get("pool_available") is not None:
                pool_available = int(detail["pool_available"])
                prev = story["triage"].get("pool_available")
                if prev is None or pool_available > int(prev):
                    story["triage"]["pool_available"] = pool_available
                if detail.get("considered_count") is not None:
                    considered = int(detail["considered_count"])
                    story["triage"]["considered_count"] = considered
                    story["triage"]["compared"] = considered
                    story["triage"]["sent_to_ai"] = considered
                elif detail.get("sent_to_global_ai_comparison") is not None:
                    considered = int(detail["sent_to_global_ai_comparison"])
                    story["triage"]["considered_count"] = considered
                    story["triage"]["compared"] = considered
                    story["triage"]["sent_to_ai"] = considered
                else:
                    story["triage"]["compared"] = int(
                        story["triage"].get("sent_to_ai") or story["triage"].get("considered_count") or pool_available
                    )
                story["result"]["compared"] = story["triage"]["compared"]
                prev_rem = story["filtering"].get("remaining") or 0
                if prev_rem == 0 or pool_available > int(prev_rem):
                    story["filtering"]["remaining"] = int(story["triage"]["pool_available"] or pool_available)
            if detail.get("considered_count") is not None:
                story["triage"]["considered_count"] = int(detail["considered_count"])
                story["triage"]["compared"] = int(detail["considered_count"])
                story["triage"]["sent_to_ai"] = int(detail["considered_count"])
            if detail.get("selection_summary"):
                story["triage"]["selection_summary"] = detail.get("selection_summary")
            if detail.get("pool_target") is not None:
                # pool_target used to mean triage cap; now equals surviving pool size.
                if story["triage"].get("sent_to_ai") is None:
                    story["triage"]["sent_to_ai"] = int(detail["pool_target"])
            if detail.get("sent_to_ai") is not None:
                story["triage"]["sent_to_ai"] = int(detail["sent_to_ai"])
                story["triage"]["compared"] = int(detail["sent_to_ai"])
                story["triage"]["considered_count"] = int(detail["sent_to_ai"])
            if detail.get("triage_cap") is not None and story["triage"].get("sent_to_ai") is None:
                story["triage"]["sent_to_ai"] = int(detail["triage_cap"])
            # Aggregate AI triage result: selected/not_selected arrays on ONE event.
            if ("AI triage result" in title or "Preview complete" in title) and (
                isinstance(detail.get("selected"), list) or isinstance(detail.get("not_selected"), list)
            ):
                story["triage"]["status"] = "done"
                if isinstance(detail.get("selected"), list) and not story["triage"]["selected"]:
                    for item in detail.get("selected") or []:
                        if not isinstance(item, dict):
                            continue
                        evidence = item.get("evidence") or item.get("positive_signals") or []
                        story["triage"]["selected"].append(
                            {
                                "rank": item.get("rank"),
                                "company": item.get("company"),
                                "score": item.get("selection_score")
                                or item.get("triage_score")
                                or item.get("score"),
                                "reason": item.get("reason"),
                                "source": item.get("source"),
                                "state": item.get("state"),
                                "domain": item.get("domain"),
                                "category": item.get("category"),
                                "positive_signals": evidence,
                                "evidence": evidence,
                                "uncertainties": item.get("uncertainties") or [],
                            }
                        )
                if isinstance(detail.get("not_selected"), list) and not story["triage"]["not_selected"]:
                    for item in detail.get("not_selected") or []:
                        if not isinstance(item, dict):
                            continue
                        story["triage"]["not_selected"].append(
                            {
                                "rank": item.get("rank"),
                                "company": item.get("company"),
                                "score": item.get("triage_score") or item.get("score"),
                                "reason": item.get("reason"),
                                "source": item.get("source"),
                                "state": item.get("state"),
                                "domain": item.get("domain"),
                            }
                        )
                story["result"]["selected"] = len(story["triage"]["selected"])
                story["result"]["selected_names"] = [
                    s["company"] for s in story["triage"]["selected"] if s.get("company")
                ]
            elif detail.get("selected") is True or detail.get("selected") is False:
                item = {
                    "rank": detail.get("rank"),
                    "company": company or detail.get("company"),
                    "score": detail.get("triage_score"),
                    "reason": detail.get("reason"),
                    "source": detail.get("source"),
                    "state": detail.get("state"),
                    "domain": detail.get("domain"),
                    "positive_signals": detail.get("positive_signals") or [],
                    "uncertainties": detail.get("uncertainties") or [],
                }
                if detail.get("selected") is True:
                    story["triage"]["selected"].append(item)
                else:
                    story["triage"]["not_selected"].append(item)
                story["result"]["selected"] = len(story["triage"]["selected"])
                story["result"]["selected_names"] = [
                    s["company"] for s in story["triage"]["selected"] if s.get("company")
                ]
            if "Candidate selection:" in title or "AI candidate triage:" in title:
                story["triage"]["status"] = "active"
            if "AI triage result" in title:
                story["triage"]["status"] = "done"
            if "Preview complete" in title:
                story["is_preview"] = True
                story["result"]["preview_stopped"] = True
                story["result"]["status"] = "done"
                story["triage"]["status"] = "done"
                if isinstance(detail.get("selected"), list) and not story["triage"]["selected"]:
                    for item in detail["selected"]:
                        if isinstance(item, dict):
                            story["triage"]["selected"].append(
                                {
                                    "rank": item.get("rank"),
                                    "company": item.get("company"),
                                    "score": item.get("triage_score") or item.get("score"),
                                    "reason": item.get("reason"),
                                    "source": item.get("source"),
                                    "state": item.get("state"),
                                }
                            )
                    story["result"]["selected"] = len(story["triage"]["selected"])
                    story["result"]["selected_names"] = [
                        s["company"] for s in story["triage"]["selected"] if s.get("company")
                    ]

        if stage == "score":
            story["downstream"]["score"] += 1
            # Aggregate scoring summary stores companies[] on one event.
            if isinstance(detail.get("companies"), list):
                for crow in detail["companies"]:
                    if not isinstance(crow, dict):
                        continue
                    name = crow.get("company") or "unknown"
                    scores_by_company[name] = {
                        "company": name,
                        "fit": crow.get("fit"),
                        "timing": crow.get("timing"),
                        "reachability": crow.get("reachability"),
                        "priority": crow.get("priority"),
                        "decision": crow.get("status"),
                    }
                    st = str(crow.get("status") or "").lower()
                    if "ready" in st:
                        story["result"]["ready"] += 1
                    elif "unreachable" in st:
                        story["result"]["unreachable"] += 1
                    elif "exclud" in st:
                        story["result"]["excluded"] += 1
            else:
                score_val = detail.get("Score") or detail.get("score")
                if title.lower().startswith("fit score"):
                    last_fit = score_val or title
                elif title.lower().startswith("timing score"):
                    last_timing = score_val or title
                elif title.lower().startswith("reachability score"):
                    last_reach = score_val or title
                elif title.lower().startswith("priority score"):
                    last_priority = (
                        detail.get("BasePriority")
                        or detail.get("EffectivePriority")
                        or score_val
                        or title
                    )

        if stage == "decision":
            story["downstream"]["decision"] += 1
            if isinstance(detail.get("status_counts"), dict):
                sc = detail["status_counts"]
                story["result"]["ready"] = int(sc.get("ready_for_outreach") or story["result"]["ready"] or 0)
                story["result"]["unreachable"] = int(sc.get("unreachable") or story["result"]["unreachable"] or 0)
                story["result"]["excluded"] = int(sc.get("excluded") or story["result"]["excluded"] or 0)
                for crow in detail.get("companies") or []:
                    if isinstance(crow, dict) and crow.get("company"):
                        scores_by_company[crow["company"]] = {
                            "company": crow["company"],
                            "priority": crow.get("priority"),
                            "decision": crow.get("status"),
                            "fit": (scores_by_company.get(crow["company"]) or {}).get("fit"),
                            "timing": (scores_by_company.get(crow["company"]) or {}).get("timing"),
                            "reachability": (scores_by_company.get(crow["company"]) or {}).get("reachability"),
                        }
            else:
                name = company or detail.get("Company") or title.split("→", 1)[0].strip()
                status_val = detail.get("Status") or ""
                if not status_val and "→" in title:
                    status_val = title.split("→", 1)[-1].strip()
                row = {
                    "company": name,
                    "fit": detail.get("Fit") if detail.get("Fit") is not None else last_fit,
                    "timing": detail.get("Timing") if detail.get("Timing") is not None else last_timing,
                    "reachability": detail.get("Reachability")
                    if detail.get("Reachability") is not None
                    else last_reach,
                    "priority": detail.get("EffectivePriority")
                    or detail.get("BasePriority")
                    or last_priority,
                    "decision": status_val,
                    "prospect_id": detail.get("ProspectID"),
                }
                scores_by_company[name] = row
                st = str(status_val).lower()
                if "ready" in st:
                    story["result"]["ready"] += 1
                elif "unreachable" in st:
                    story["result"]["unreachable"] += 1
                elif "exclud" in st:
                    story["result"]["excluded"] += 1
                last_fit = last_timing = last_reach = last_priority = None

        if stage == "save":
            story["downstream"]["save"] += 1
        if stage == "research":
            story["downstream"]["research"] += 1
            if "Research summary" in title and detail.get("research_completed") is not None:
                story["downstream"]["research"] = int(detail.get("research_completed") or 0)
        if stage == "error":
            story["errors"].append(title)

    # Prefer aggregate filtering summary counts when present; else derive from accepted+removed.
    if story["filtering"].get("entering") in (None, 0) and (accepted or removed_counts):
        story["filtering"]["entering"] = len(accepted) + sum(removed_counts.values())
    # Drop zero buckets so the UI can still show a fixed set from counts.
    story["filtering"]["removed"] = {k: v for k, v in removed_counts.items() if v}
    story["filtering"]["counts"] = {
        "produced": story["filtering"]["entering"],
        "duplicates": removed_counts.get("Duplicates removed", 0) + removed_counts.get("Duplicates (same run)", 0),
        "known": removed_counts.get("Known skipped", 0),
        "excluded": removed_counts.get("Excluded", 0),
        "website_failures": removed_counts.get("Website failures", 0),
        "remaining": story["filtering"]["remaining"],
    }
    story["filtering"]["details"] = reject_details[:200]
    story["result"]["scores"] = list(scores_by_company.values())
    if story["filtering"]["remaining"] == 0 and story["triage"]["compared"]:
        story["filtering"]["remaining"] = story["triage"]["compared"]
        story["filtering"]["counts"]["remaining"] = story["triage"]["compared"]
    if removed_counts or accepted:
        if story["filtering"]["status"] == "active" and story["triage"]["status"] in {"done", "active"}:
            story["filtering"]["status"] = "done"
        elif story["plan"]["status"] == "done":
            story["filtering"]["status"] = "done"

    if story["triage"]["selected"] and not story["result"]["selected_names"]:
        story["result"]["selected_names"] = [s["company"] for s in story["triage"]["selected"] if s.get("company")]
        story["result"]["selected"] = len(story["triage"]["selected"])
    if story["triage"]["compared"]:
        story["result"]["compared"] = story["triage"]["compared"]
    if story["triage"]["sent_to_ai"] is None and story["triage"]["compared"]:
        story["triage"]["sent_to_ai"] = story["triage"]["compared"]
    if story["triage"]["considered_count"] is None and story["triage"]["sent_to_ai"] is not None:
        story["triage"]["considered_count"] = story["triage"]["sent_to_ai"]
    story["result"]["discovered"] = story["filtering"]["entering"] or sum(
        int((story["sources"].get(k) or {}).get("candidates_produced") or 0) for k in story["sources"]
    )
    story["result"]["filtered"] = story["filtering"]["remaining"] or story["triage"]["compared"] or 0
    story["event_count"] = len(events)

    if run.get("status") == "completed":
        story["status"] = "completed"
        # Force every in-flight section off pending/active once the run row is
        # terminal — agentic runs skip classic triage pool events.
        story["plan"]["status"] = "done"
        story["filtering"]["status"] = "done"
        story["triage"]["status"] = "done"
        story["result"]["status"] = "done"
        for src in story["sources"].values():
            if src["status"] in {"planned", "requesting", "harvested", "active", "pending"}:
                src["status"] = "done"
    elif run.get("status") == "running":
        story["result"]["status"] = "pending"
    elif run.get("status") in {"failed", "skipped"}:
        story["status"] = run.get("status")
        story["result"]["status"] = run.get("status")
        for src in story["sources"].values():
            if src["status"] in {"planned", "requesting", "harvested", "active"}:
                src["status"] = "error" if run.get("status") == "failed" else "done"

    # Prefer API-returned lists for Source results. Fall back to survivors only for
    # older runs that never stored returned_companies.
    surviving_pool_rows: list[dict[str, Any]] = []
    for ev in events:
        detail = scrub_payload(ev.get("detail") or {})
        rows = detail.get("surviving_pool")
        if isinstance(rows, list) and rows:
            surviving_pool_rows = [r for r in rows if isinstance(r, dict)]
    if surviving_pool_rows:
        for row in surviving_pool_rows:
            sk = _source_key(str(row.get("discovery_source") or row.get("source") or ""))
            if not sk:
                continue
            src = _ensure_source(story, sk)
            company = (row.get("company") or "").strip().lower()
            domain = (row.get("domain") or "").strip().lower()
            if not company:
                continue
            key = (company, domain)
            already_acc = {
                (
                    (c.get("company") or "").strip().lower(),
                    (c.get("domain") or "").strip().lower(),
                )
                for c in src["accepted_companies"]
            }
            if key not in already_acc:
                src["accepted_companies"].append(
                    {
                        "company": row.get("company"),
                        "domain": row.get("domain"),
                        "source": sk,
                        "state": row.get("state"),
                        "city": row.get("city"),
                    }
                )
    for src in story["sources"].values():
        if not src.get("returned_companies") and src.get("accepted_companies"):
            src["returned_companies"] = list(src["accepted_companies"])

    ordered = []
    seen: set[str] = set()
    for k in list(story["plan"]["fetch_limits"].keys()) + list(story["sources"].keys()) + list(_SOURCE_ORDER):
        sk = _source_key(str(k)) or str(k)
        if sk in seen or sk not in story["sources"]:
            continue
        row = dict(story["sources"][sk])
        row.pop("_raw_locked", None)
        row.pop("_cand_locked", None)
        ordered.append(row)
        seen.add(sk)
    story["sources_ordered"] = ordered
    story["summary"] = {
        "requested": story["requested"] or story["plan"]["requested"],
        "discovered": story["result"]["discovered"],
        "filtered": story["result"]["filtered"],
        "selected": story["result"]["selected"] or len(story["triage"]["selected"]),
        "duration_s": story["duration_s"],
        "status": story["status"],
        "by_source": [
            {
                "key": s["key"],
                "label": s["label"],
                "raw": s.get("raw_received") or 0,
                "survived": s.get("survived") or 0,
                "status": s.get("status"),
            }
            for s in ordered
        ],
    }
    if _is_agentic_run(events, run):
        _apply_agentic_story(story, events, run)
    return story


def _is_agentic_run(events: list[dict[str, Any]], run: dict[str, Any]) -> bool:
    if run.get("agentic"):
        return True
    for ev in events:
        title = (ev.get("title") or "").lower()
        detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
        if "agentic" in title:
            return True
        provider = str(detail.get("provider") or "").lower()
        if "agentic" in provider:
            return True
    return False


def _apply_agentic_story(
    story: dict[str, Any],
    events: list[dict[str, Any]],
    run: dict[str, Any],
) -> None:
    """Rewrite operator story for openai_agentic (no legacy pool/triage UI)."""
    story["mode"] = "agentic"
    story["title"] = "OpenAI agentic discovery"
    story["provider"] = "openai_agentic"

    agentic: dict[str, Any] = {
        "plan": {
            "status": "pending",
            "requested": story.get("requested") or story.get("plan", {}).get("requested") or run.get("requested"),
            "geography": story.get("plan", {}).get("geography"),
            "categories": list(story.get("plan", {}).get("categories") or []),
            "icp_loaded": False,
            "context_loaded": False,
            "total_discovered": None,
        },
        "research": {
            "status": "pending",
            "provider": "openai_agentic",
            "model": None,
            "search_calls": None,
            "cost_usd": None,
            "started": False,
            "completed": False,
        },
        "ai_usage": None,
        "results": {
            "status": "pending",
            "returned": 0,
            "valid": 0,
            "invalid": 0,
            "prospects": [],
            "attempts": [],
            "shortfall_count": 0,
            "shortfall_reason": None,
            "search_attempts": 0,
            "companies_considered": None,
            "rejected_before_selection": None,
            "rejection_reason_counts": {},
            "strong_seeds_returned": 0,
        },
        "enrichment": {
            "status": "pending",
            "companies": [],
            "started": 0,
            "completed": 0,
            "failed": 0,
            "enriched": 0,
            "dm_found": 0,
            "dm_unknown": 0,
            "contact_found": 0,
            "revenue_supported": 0,
            "headcount_supported": 0,
            "sources_consulted": 0,
            "ready": 0,
            "needs_research": 0,
            "excluded": 0,
            "parallel_started": False,
            "system_error": None,
            "system_error_fingerprint": None,
            "technical_failure": 0,
        },
        "database": {
            "status": "pending",
            "new": 0,
            "updated": 0,
            "duplicates": 0,
            "excluded": 0,
        },
        "scoring": {
            "status": "pending",
            "prospects_scored": 0,
            "ready": 0,
            "needs_more_research": 0,
            "not_ready": 0,
            "unreachable": 0,
            "excluded": 0,
            "scores": [],
        },
        "result": {
            "status": "pending",
            "created": 0,
            "updated": 0,
            "usable": run.get("usable"),
            "stop_reason": None,
        },
    }

    for ev in events:
        stage = ev.get("stage") or ""
        title = ev.get("title") or ""
        detail = scrub_payload(ev.get("detail") or {})
        if not isinstance(detail, dict):
            detail = {}
        title_l = title.lower()

        if stage == "run" and detail.get("requested") is not None:
            agentic["plan"]["requested"] = detail.get("requested")
            agentic["plan"]["status"] = "active"
        if stage == "icp":
            agentic["plan"]["icp_loaded"] = True
            agentic["plan"]["status"] = "active"
            if detail.get("priority_categories"):
                agentic["plan"]["categories"] = list(detail["priority_categories"])
            geo = detail.get("geography")
            if isinstance(geo, dict):
                if geo.get("nationwide"):
                    agentic["plan"]["geography"] = "United States (nationwide)"
                else:
                    states = geo.get("states") or []
                    agentic["plan"]["geography"] = (
                        ", ".join(str(s) for s in states) if states else "United States"
                    )
        if stage == "plan" and "context" in title_l:
            agentic["plan"]["context_loaded"] = True
            agentic["plan"]["status"] = "done"
            if detail.get("total_discovered") is not None:
                agentic["plan"]["total_discovered"] = detail.get("total_discovered")

        if stage == "research" or (stage == "harvest" and "request" in title_l):
            if detail.get("model"):
                agentic["research"]["model"] = detail.get("model")
            if "started" in title_l or "REQUEST" in title:
                agentic["research"]["started"] = True
                agentic["research"]["status"] = "active"
            if "completed" in title_l or detail.get("search_calls") is not None:
                agentic["research"]["completed"] = True
                agentic["research"]["status"] = "done"
                if detail.get("search_calls") is not None:
                    agentic["research"]["search_calls"] = int(detail["search_calls"])
                if detail.get("cost_usd") is not None:
                    agentic["research"]["cost_usd"] = detail.get("cost_usd")

        if stage == "plan" and (
            "discovery complete" in title_l
            or "master discovery" in title_l
            or detail.get("attempts") is not None
        ):
            if isinstance(detail.get("attempts"), list):
                agentic["results"]["attempts"] = [a for a in detail["attempts"] if isinstance(a, dict)]
            if detail.get("found_count") is not None:
                agentic["results"]["returned"] = int(detail["found_count"])
                agentic["results"]["valid"] = int(detail["found_count"])
                agentic["results"]["strong_seeds_returned"] = int(
                    detail.get("strong_seeds_returned") or detail["found_count"]
                )
            if detail.get("shortfall_count") is not None:
                agentic["results"]["shortfall_count"] = int(detail["shortfall_count"])
            if detail.get("shortfall_reason"):
                agentic["results"]["shortfall_reason"] = detail.get("shortfall_reason")
            if detail.get("search_attempts") is not None:
                agentic["results"]["search_attempts"] = int(detail["search_attempts"])
            if detail.get("companies_considered") is not None:
                agentic["results"]["companies_considered"] = int(detail["companies_considered"])
            if detail.get("rejected_before_selection") is not None:
                agentic["results"]["rejected_before_selection"] = int(
                    detail["rejected_before_selection"]
                )
            if isinstance(detail.get("rejection_reason_counts"), dict):
                agentic["results"]["rejection_reason_counts"] = {
                    str(k): int(v)
                    for k, v in detail["rejection_reason_counts"].items()
                    if str(k)
                }
            agentic["results"]["status"] = "done"

        if (
            stage == "harvest"
            and "request" not in title_l
            and ("returned" in title_l or "strong seeds" in title_l or "master discovery" in title_l or detail.get("returned") is not None)
        ):
            returned = detail.get("returned")
            if returned is None and detail.get("raw_received") is not None:
                returned = detail.get("raw_received")
            if returned is None:
                m = re.search(r"returned:\s*(\d+)", title_l)
                if m:
                    returned = int(m.group(1))
            returned_n = int(returned or 0)
            # Keep cumulative found pool when present.
            if detail.get("pool_size") is not None:
                agentic["results"]["returned"] = int(detail["pool_size"])
                agentic["results"]["valid"] = int(detail["pool_size"])
                agentic["results"]["strong_seeds_returned"] = int(detail["pool_size"])
            else:
                agentic["results"]["returned"] = max(agentic["results"]["returned"], returned_n)
                agentic["results"]["valid"] = int(
                    detail["valid"] if detail.get("valid") is not None else agentic["results"]["returned"]
                )
            if detail.get("companies_considered") is not None:
                prev = agentic["results"].get("companies_considered")
                agentic["results"]["companies_considered"] = (
                    int(prev or 0) + int(detail["companies_considered"])
                    if prev is not None
                    else int(detail["companies_considered"])
                )
            if detail.get("rejected_before_selection") is not None:
                prev_r = agentic["results"].get("rejected_before_selection")
                agentic["results"]["rejected_before_selection"] = (
                    int(prev_r or 0) + int(detail["rejected_before_selection"])
                    if prev_r is not None
                    else int(detail["rejected_before_selection"])
                )
            if isinstance(detail.get("rejection_reason_counts"), dict):
                counts = agentic["results"].setdefault("rejection_reason_counts", {})
                for k, v in detail["rejection_reason_counts"].items():
                    try:
                        counts[str(k)] = int(counts.get(str(k)) or 0) + int(v)
                    except (TypeError, ValueError):
                        continue
            agentic["results"]["invalid"] = int(detail.get("invalid") or 0)
            agentic["results"]["status"] = "done" if agentic["results"]["returned"] or agentic["research"]["completed"] else "active"
            if detail.get("attempt") is not None:
                agentic["results"]["attempts"].append(
                    {
                        "attempt": detail.get("attempt"),
                        "returned": returned_n,
                        "accepted": detail.get("accepted"),
                        "remaining_after": detail.get("remaining_after"),
                        "companies_considered": detail.get("companies_considered"),
                        "rejected_before_selection": detail.get("rejected_before_selection"),
                    }
                )
            for row in detail.get("returned_companies") or []:
                if isinstance(row, dict) and len(agentic["results"]["prospects"]) < 80:
                    agentic["results"]["prospects"].append(row)
            if not agentic["research"]["completed"] and agentic["research"]["started"]:
                agentic["research"]["status"] = "done"
                agentic["research"]["completed"] = True

        if stage == "research" and "enrichment completed" in title_l:
            agentic["enrichment"]["status"] = "active"
            agentic["enrichment"]["companies"].append(
                {
                    "company": detail.get("company") or (title.split(":", 1)[-1].strip() if ":" in title else title),
                    "decision_maker": detail.get("decision_maker"),
                    "acquisition_status": detail.get("acquisition_status"),
                    "readiness_reason": detail.get("readiness_reason"),
                }
            )
        if stage == "research" and (
            "stage b enrichment starting" in title_l or "parallel work started" in title_l
        ):
            agentic["enrichment"]["status"] = "active"
            agentic["enrichment"]["parallel_started"] = True
            if detail.get("enrichment_jobs") is not None:
                agentic["enrichment"]["started"] = max(
                    int(agentic["enrichment"]["started"] or 0),
                    int(detail.get("started") or 0),
                ) or int(detail["enrichment_jobs"])
        if stage == "research" and "enrichment summary" in title_l:
            for key in ("started", "completed", "failed"):
                if detail.get(key) is not None:
                    agentic["enrichment"][key] = int(detail[key])
            agentic["enrichment"]["status"] = "active"

        if stage == "dedupe" or (stage == "save" and "records saved" in title_l):
            if detail.get("created") is not None or detail.get("duplicates") is not None:
                agentic["database"]["new"] = int(detail.get("created") or agentic["database"]["new"] or 0)
                agentic["database"]["updated"] = int(detail.get("updated") or agentic["database"]["updated"] or 0)
                agentic["database"]["duplicates"] = int(
                    detail.get("duplicates") or agentic["database"]["duplicates"] or 0
                )
                agentic["database"]["excluded"] = int(
                    detail.get("excluded") or agentic["database"]["excluded"] or 0
                )
                agentic["database"]["status"] = "done"
            if stage == "save":
                agentic["result"]["created"] = int(detail.get("created") or agentic["result"]["created"] or 0)
                agentic["result"]["updated"] = int(detail.get("updated") or agentic["result"]["updated"] or 0)

        if stage == "error" and ("slot system error" in title_l or detail.get("stop_reason") == "slot_system_error"):
            agentic["enrichment"]["system_error"] = detail.get("sample_error") or detail.get("system_error") or title
            agentic["enrichment"]["system_error_fingerprint"] = detail.get("fingerprint")
            agentic["result"]["stop_reason"] = "slot_system_error"
            agentic["result"]["status"] = "done"
            agentic["enrichment"]["status"] = "done"

        if stage == "decision" and "slot system error" in title_l:
            agentic["enrichment"]["system_error"] = detail.get("system_error") or title
            agentic["enrichment"]["system_error_fingerprint"] = detail.get("system_error_fingerprint")
            agentic["result"]["stop_reason"] = "slot_system_error"

        if stage == "filter" and (
            "enrichment completeness" in title_l
            or detail.get("dm_found") is not None
            or detail.get("enriched") is not None
        ):
            for key in (
                "started",
                "completed",
                "failed",
                "enriched",
                "dm_found",
                "dm_unknown",
                "contact_found",
                "revenue_supported",
                "headcount_supported",
                "sources_consulted",
                "ready",
                "needs_research",
                "excluded",
                "technical_failure",
            ):
                if detail.get(key) is not None:
                    agentic["enrichment"][key] = int(detail[key])
            if detail.get("system_error"):
                agentic["enrichment"]["system_error"] = detail.get("system_error")
            if detail.get("system_error_fingerprint"):
                agentic["enrichment"]["system_error_fingerprint"] = detail.get(
                    "system_error_fingerprint"
                )
            agentic["enrichment"]["status"] = "done"

        if stage == "score" and ("scoring" in title_l or detail.get("scores") is not None):
            scores = detail.get("scores") or []
            if isinstance(scores, list):
                agentic["scoring"]["scores"] = [s for s in scores if isinstance(s, dict)][:40]
                agentic["scoring"]["prospects_scored"] = len(scores)
            agentic["scoring"]["status"] = "active"

        if stage == "decision" and ("priority" in title_l or "readiness" in title_l):
            ready = int(detail.get("ready") or 0)
            needs = int(detail.get("needs_more_research") or 0)
            unreachable = int(detail.get("unreachable") or 0)
            excluded = int(detail.get("excluded") or 0)
            scored = int(detail.get("prospects_scored") or detail.get("scored") or 0)
            agentic["scoring"]["ready"] = ready
            agentic["scoring"]["needs_more_research"] = needs
            agentic["scoring"]["unreachable"] = unreachable
            agentic["scoring"]["excluded"] = excluded
            agentic["scoring"]["not_ready"] = unreachable + excluded + needs
            agentic["enrichment"]["ready"] = ready
            agentic["enrichment"]["needs_research"] = needs
            agentic["enrichment"]["dm_found"] = int(
                detail.get("dm_found") or agentic["enrichment"]["dm_found"] or 0
            )
            agentic["enrichment"]["dm_unknown"] = int(
                detail.get("dm_unknown") or agentic["enrichment"]["dm_unknown"] or 0
            )
            if scored:
                agentic["scoring"]["prospects_scored"] = scored
            elif agentic["scoring"]["prospects_scored"] == 0 and agentic["scoring"]["scores"]:
                agentic["scoring"]["prospects_scored"] = len(agentic["scoring"]["scores"])
            agentic["scoring"]["status"] = "done"
            agentic["enrichment"]["status"] = "done"

        if stage == "save" and detail.get("usable") is not None:
            agentic["result"]["usable"] = int(detail["usable"])
            if detail.get("shortfall_count") is not None:
                agentic["results"]["shortfall_count"] = int(detail["shortfall_count"])
            if detail.get("shortfall_reason"):
                agentic["results"]["shortfall_reason"] = detail.get("shortfall_reason")
            if detail.get("search_attempts") is not None:
                agentic["results"]["search_attempts"] = int(detail["search_attempts"])
            if detail.get("found_count") is not None:
                agentic["results"]["returned"] = int(detail["found_count"])
            if isinstance(detail.get("enrichment"), dict):
                for k, v in detail["enrichment"].items():
                    if k in agentic["enrichment"] and v is not None and k != "companies":
                        agentic["enrichment"][k] = v
            if isinstance(detail.get("ai_usage"), dict):
                agentic["ai_usage"] = detail["ai_usage"]
        if stage == "score" and "actual openai usage" in title_l:
            # Rollup emitted at end of run — prefer this over partials.
            usage = {
                k: detail.get(k)
                for k in (
                    "api_requests",
                    "successful_calls",
                    "failed_calls",
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "web_search_calls",
                    "input_cost_usd",
                    "output_cost_usd",
                    "web_search_cost_usd",
                    "total_cost_usd",
                    "cost_per_ready_usd",
                    "by_call_type",
                    "per_call",
                )
                if detail.get(k) is not None
            }
            if usage:
                agentic["ai_usage"] = {**(agentic.get("ai_usage") or {}), **usage}
        if stage == "run" and "completed" in title_l:
            if detail.get("usable") is not None:
                agentic["result"]["usable"] = int(detail["usable"])

    if agentic["enrichment"]["enriched"] == 0 and agentic["enrichment"]["companies"]:
        agentic["enrichment"]["enriched"] = len(agentic["enrichment"]["companies"])

    # Fallbacks from run row when events are sparse (older agentic traces).
    if agentic["plan"]["requested"] is None and run.get("requested") is not None:
        agentic["plan"]["requested"] = run.get("requested")
    if agentic["database"]["new"] == 0 and run.get("new") is not None:
        agentic["database"]["new"] = int(run.get("new") or 0)
        agentic["database"]["status"] = "done"
    if agentic["result"]["created"] == 0 and agentic["database"]["new"]:
        agentic["result"]["created"] = agentic["database"]["new"]
    if agentic["result"]["updated"] == 0 and agentic["database"]["updated"]:
        agentic["result"]["updated"] = agentic["database"]["updated"]
    if agentic["result"]["usable"] is None:
        agentic["result"]["usable"] = run.get("usable")

    saved = int(agentic["result"]["created"] or 0) + int(agentic["result"]["updated"] or 0)
    if saved == 0:
        saved = int(agentic["database"]["new"] or 0) + int(agentic["database"]["updated"] or 0)

    stop = run.get("stop_reason")
    if (saved > 0 or (agentic["result"]["usable"] or 0) > 0) and stop in {
        None,
        "pool_exhausted",
        "quota_met",
    }:
        stop = "completed"
    elif story.get("status") == "completed" and saved > 0 and stop not in {
        "completed_with_shortfall",
        "failed",
        "skipped",
    }:
        stop = "completed"
    agentic["result"]["stop_reason"] = stop

    if story.get("status") == "completed":
        agentic["plan"]["status"] = "done"
        if agentic["research"]["started"] or agentic["research"]["completed"]:
            agentic["research"]["status"] = "done"
        if agentic["results"]["returned"] or agentic["research"]["completed"]:
            agentic["results"]["status"] = "done"
        if (
            saved
            or agentic["database"]["duplicates"]
            or agentic["database"]["excluded"]
            or agentic["database"]["status"] == "done"
        ):
            agentic["database"]["status"] = "done"
        if agentic["scoring"]["prospects_scored"] or agentic["scoring"]["status"] != "pending":
            agentic["scoring"]["status"] = "done"
        if agentic["enrichment"]["enriched"] or agentic["enrichment"]["companies"]:
            agentic["enrichment"]["status"] = "done"
        agentic["result"]["status"] = "done"

    story["agentic"] = agentic
    story["requested"] = agentic["plan"]["requested"]
    story["plan"]["requested"] = agentic["plan"]["requested"]
    story["plan"]["geography"] = agentic["plan"]["geography"] or story["plan"].get("geography")
    story["plan"]["categories"] = agentic["plan"]["categories"] or story["plan"].get("categories") or []
    story["stop_reason"] = stop
    usage = agentic.get("ai_usage") if isinstance(agentic.get("ai_usage"), dict) else {}
    ready_n = int(agentic["scoring"].get("ready") or agentic["enrichment"].get("ready") or 0)
    story["summary"] = {
        "status": story.get("status"),
        "provider": "openai_agentic",
        "requested": agentic["plan"]["requested"],
        "returned": agentic["results"]["returned"],
        "ready": ready_n,
        "new": agentic["database"]["new"],
        "duplicates": agentic["database"]["duplicates"],
        "excluded": agentic["database"]["excluded"],
        "saved": saved,
        "usable": agentic["result"]["usable"],
        "duration_s": story.get("duration_s"),
        "stop_reason": stop,
        "shortfall_count": agentic["results"]["shortfall_count"],
        "shortfall_reason": agentic["results"]["shortfall_reason"],
        "search_attempts": agentic["results"]["search_attempts"],
        "mode": "agentic",
        "api_requests": usage.get("api_requests"),
        "web_search_calls": usage.get("web_search_calls"),
        "actual_cost_usd": usage.get("total_cost_usd"),
        "cost_complete": usage.get("cost_complete", True) if usage else True,
        "cost_per_ready_usd": usage.get("cost_per_ready_usd"),
        "cost_message": (
            "Actual cost unavailable — long-context pricing is not configured."
            if (usage and not usage.get("cost_complete", True))
            or stop == "pricing_unknown"
            else None
        ),
        "ai_usage": usage or None,
    }
