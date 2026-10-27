"""Seeded live check of show_prospects against the real assistant.

Uses acquisition_test only. Instantly send is stubbed. Nothing is emailed.
Run: RUN_SHOW_PROSPECTS=1 python -m pytest tests/test_show_prospects_seeded.py -s -p no:cacheprovider
"""

from __future__ import annotations

import json
import os
import re

import pytest

from app.api.deps import bound
from app.assistant.assistant import CampaignSession
from bizos import chat_history
from bizos.util.ids import new_id
from tests.conftest import ACQUISITION_TEST_DB
from tests.test_unified_assistant import ask

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SHOW_PROSPECTS") != "1",
    reason="set RUN_SHOW_PROSPECTS=1 to talk to the real model against acquisition_test",
)

PEOPLE = {
    "A": ("Alice Park", "alice@acme.example.com"),
    "B": ("Ben Quinn", "ben@acme.example.com"),
    "C": ("Cara Reid", "cara@acme.example.com"),
    "D": ("Dana Shah", "dana@acme.example.com"),
    "E": ("Evan Cole", "evan@acme.example.com"),
}
HARBOR_TEST = "Harbor Light Test"
HARBOR = "Harbor Light"
EAST = "East Coast"
WEST = "West Wind"
MISSING = "Moonbeam"
KNOWN_NAMES = {n for n, _ in PEOPLE.values()} | {"Fran Wu"}
KNOWN_EMAILS = {e for _, e in PEOPLE.values()} | {"fran@acme.example.com"}
KNOWN_CAMPAIGNS = {HARBOR_TEST, HARBOR, EAST, WEST, MISSING}
WHO_TO_EMAIL = re.compile(r"who to email|who should I email|which people", re.I)
MARKDOWN = re.compile(r"[|`*_#\[\]]|^\s*-{3,}\s*$", re.M)


def _wipe():
    from acquisition.db.session import SessionLocal, engine
    from acquisition.models.campaign import Campaign, CampaignMessage, CampaignProspect
    from acquisition.models.prospect import Prospect
    from acquisition.models.replies import ReplyConversation, ReplyMessage

    assert engine.url.database == ACQUISITION_TEST_DB, f"refusing to wipe {engine.url.database!r}"
    db = SessionLocal()
    for model in (ReplyMessage, ReplyConversation, CampaignProspect, CampaignMessage, Campaign, Prospect):
        db.query(model).delete()
    db.commit()
    db.close()


def _seed_five():
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    _wipe()
    db = SessionLocal()
    people = {}
    for key, (name, email) in PEOPLE.items():
        row = Prospect(company="Acme", executive_name=name, email=email, approval_status=ApprovalStatus.APPROVED)
        db.add(row)
        db.flush()
        people[key] = row
    harbor_test = Campaign(name=HARBOR_TEST, sender_name="Me")
    harbor = Campaign(name=HARBOR, sender_name="Me")
    east = Campaign(name=EAST, sender_name="Me")
    west = Campaign(name=WEST, sender_name="Me")
    db.add_all([harbor_test, harbor, east, west])
    db.flush()
    db.add(CampaignProspect(campaign_id=harbor_test.id, prospect_id=people["A"].id, outreach_status=OutreachStatus.PENDING))
    db.add(CampaignProspect(campaign_id=harbor_test.id, prospect_id=people["B"].id, outreach_status=OutreachStatus.ACTIVE))
    db.add(CampaignProspect(campaign_id=harbor_test.id, prospect_id=people["C"].id, outreach_status=OutreachStatus.REPLIED))
    db.add(CampaignProspect(campaign_id=harbor_test.id, prospect_id=people["D"].id, outreach_status=OutreachStatus.PENDING))
    db.add(CampaignProspect(campaign_id=east.id, prospect_id=people["D"].id, outreach_status=OutreachStatus.ACTIVE))
    db.commit()
    db.close()


def _tool(campaign="", only="all") -> dict:
    raw = CampaignSession(None, {}, {})._show_prospects(campaign=campaign, only=only)
    return json.loads(raw)


def _text(payload: dict) -> str:
    if payload.get("error"):
        return str(payload["error"])
    return str(payload.get("text") or "")


def _fresh(ctx, settings, message, session_id=None):
    return ask(ctx, settings, message, session_id=session_id)


def _form(reply: str) -> list[str]:
    problems = []
    if MARKDOWN.search(reply or ""):
        problems.append("markdown/table characters")
    return problems


def _invented(reply: str) -> list[str]:
    found = []
    for name in re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", reply or ""):
        if name not in KNOWN_NAMES and name not in KNOWN_CAMPAIGNS:
            found.append(name)
    return found


def _match_tool(reply: str, expected: str) -> str:
    got, want = (reply or "").strip(), (expected or "").strip()
    if got == want:
        return "exact"
    if want and want in got:
        extra = got.replace(want, "", 1).strip()
        return f"extra:{extra[:120]}" if extra else "contains"
    return "mismatch"


@pytest.fixture
def live_env(monkeypatch):
    if "127.0.0.1" in (os.environ.get("OPENAI_BASE_URL") or ""):
        os.environ.pop("OPENAI_BASE_URL", None)
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")
    from app.assistant import assistant as agent
    from bizos import settings as app_settings

    monkeypatch.setattr(
        agent,
        "fulfill_approved_email",
        lambda ctx, parsed: agent.FulfillResult(campaign_id="00000000-0000-0000-0000-000000000001", message="(test) not sent"),
    )
    app_settings.llm_available.cache_clear()
    yield
    app_settings.llm_available.cache_clear()


def test_show_prospects_against_seeded_data(live_env, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    settings = client_scratch.settings
    rows = []

    def record(question, expected, out, extra_fail=""):
        reply = (out.get("content") or "") if isinstance(out, dict) else str(out)
        log = ((out.get("trace") or {}).get("tool_log") or []) if isinstance(out, dict) else []
        tools = [c.get("tool") for c in log]
        form = _form(reply)
        invented = _invented(reply)
        match = _match_tool(reply, expected)
        reasons = []
        if extra_fail:
            reasons.append(extra_fail)
        reasons.extend(form)
        if invented:
            reasons.append("invented names: " + ", ".join(invented))
        if match == "exact":
            pass
        elif match.startswith("extra:"):
            reasons.append("model added extra sentences")
        elif match == "contains":
            pass
        else:
            reasons.append("reply does not match tool text")
        called = [t for t in tools if t in {"show_prospects", "show_campaign_people", "show_contacts"}]
        cause = ""
        if reasons:
            if not called and "does not match" in " ".join(reasons):
                cause = "model (did not call the listing tool)"
            elif match.startswith("extra") or "who to email" in " ".join(reasons).lower():
                cause = "model ignoring the tool"
            elif "does not match" in " ".join(reasons) and called:
                tool_blob = " ".join(str(c.get("result", "")) for c in log if c.get("tool") in called)
                if expected.strip() and expected.strip() in tool_blob:
                    cause = "model ignoring the tool"
                elif expected.strip() and expected.strip() not in tool_blob:
                    cause = "tool code"
                else:
                    cause = "model ignoring the tool"
            else:
                cause = "model ignoring the tool" if called else "model (did not call the listing tool)"
        status = "PASS" if not reasons else "FAIL"
        rows.append(
            {
                "question": question,
                "expected": expected,
                "actual": reply,
                "status": status,
                "cause": cause,
                "reasons": reasons,
                "tools": tools,
            }
        )
        print("\n---")
        print("Q:", question)
        print("EXPECTED:\n", expected)
        print("ACTUAL:\n", reply)
        print("TOOLS:", tools)
        print(status, cause, reasons)

    # --- 12. zero prospects, fresh chat
    _wipe()
    expected = _text(_tool())
    out = _fresh(ctx, settings, "How many prospects do we have?")
    record("12. How many prospects? (zero in workspace)", expected, out)

    # --- five-prospect world
    _seed_five()

    expected = _text(_tool())
    out = _fresh(ctx, settings, "How many prospects do we have?")
    record("1. How many prospects do we have?", expected, out)

    expected = _text(_tool())
    out = _fresh(ctx, settings, "List all prospects along with their campaigns.")
    record("2. List all prospects along with their campaigns.", expected, out)

    expected = _text(_tool(only="not_in_campaign"))
    out = _fresh(ctx, settings, "Which prospects are not in any campaign?")
    record("3. Which prospects are not in any campaign?", expected, out)

    expected = _text(_tool(only="in_campaign"))
    out = _fresh(ctx, settings, "Which prospects are in a campaign?")
    record("4. Which prospects are in a campaign?", expected, out)

    expected = _text(_tool(campaign=HARBOR_TEST))
    out = _fresh(ctx, settings, f"How many people are in {HARBOR_TEST}?")
    record(f"5. How many people are in {HARBOR_TEST}?", expected, out)

    expected = _text(_tool(campaign=HARBOR_TEST))
    out = _fresh(ctx, settings, f"Who is in {HARBOR_TEST}?")
    record(f"6. Who is in {HARBOR_TEST}?", expected, out)

    expected = _text(_tool(campaign=HARBOR_TEST))
    out = _fresh(ctx, settings, f"Did we send email to everyone in {HARBOR_TEST}?")
    record(f"7. Did we send email to everyone in {HARBOR_TEST}?", expected, out)

    sid = None
    first = _fresh(ctx, settings, f"Who is in {HARBOR_TEST}?")
    sid = first["session_id"]
    expected = _text(_tool())
    out = _fresh(ctx, settings, "How many prospects?", session_id=sid)
    extra = ""
    if str(HARBOR_TEST) in (out.get("content") or "") and "5 prospect" not in (out.get("content") or ""):
        extra = "answered the campaign count instead of the workspace total"
    record("8. How many prospects? (after a campaign was mentioned)", expected, out, extra)

    expected = _text(_tool(campaign=WEST))
    out = _fresh(ctx, settings, f"Who is in {WEST}?")
    record(f"9. Empty campaign ({WEST})", expected, out)

    expected = _text(_tool(campaign=MISSING))
    out = _fresh(ctx, settings, f"Who is in {MISSING}?")
    extra = ""
    if "could not find" not in (out.get("content") or "").lower():
        extra = "missing a clear could-not-find message"
    record(f"10. Campaign that does not exist ({MISSING})", expected, out, extra)

    expected = _text(_tool(campaign="HARBOR LIGHT TEST"))
    out = _fresh(ctx, settings, "Who is in HARBOR LIGHT TEST?")
    record("11. Different upper/lower case (HARBOR LIGHT TEST)", expected, out)

    # --- 13. pending email selection must stay untouched
    _seed_five()
    sid = new_id("ses")
    with bound(ctx):
        chat_history.record_turn(
            ctx,
            session_id=sid,
            user_message="email Alice Park, alice@acme.example.com",
            reply="Which campaign should I put Alice in, and what daily send window?",
            domain="acquisition",
            meta={"state": {"recipients": [{"email": "alice@acme.example.com", "name": "Alice Park"}]}},
        )
    expected = _text(_tool())
    out = _fresh(ctx, settings, "How many prospects do we have?", session_id=sid)
    extra = ""
    reply = out.get("content") or ""
    if WHO_TO_EMAIL.search(reply):
        extra = "asked who to email"
    saved = chat_history.get_session(ctx, sid)
    state = (saved["messages"][-1].get("meta") or {}).get("state") or {}
    recipients = state.get("recipients") or []
    emails = {str(r.get("email") or "").casefold() for r in recipients}
    if "alice@acme.example.com" not in emails:
        extra = (extra + "; " if extra else "") + "pending selection was changed"
    record("13. How many prospects? (while a half-finished email selection is pending)", expected, out, extra)

    # --- 14. twice in a row, once rephrased
    _seed_five()
    expected = _text(_tool())
    first = _fresh(ctx, settings, "How many prospects do we have?")
    record("14a. How many prospects do we have? (first of two)", expected, first)
    second = _fresh(ctx, settings, "How many prospects do we have?", session_id=first["session_id"])
    record("14b. How many prospects do we have? (same chat, again)", expected, second)
    third = _fresh(ctx, settings, "and in total?", session_id=first["session_id"])
    record("14c. and in total? (same chat, rephrased)", expected, third)

    # --- 15. after adding one prospect
    from acquisition.db.session import SessionLocal
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    db.add(Prospect(company="Acme", executive_name="Fran Wu", email="fran@acme.example.com", approval_status=ApprovalStatus.APPROVED))
    db.commit()
    db.close()
    expected = _text(_tool())
    out = _fresh(ctx, settings, "How many prospects do we have?", session_id=first["session_id"])
    extra = ""
    if "5 prospect" in (out.get("content") or "") and "6 prospect" not in (out.get("content") or ""):
        extra = "stale count (still 5 after adding Fran)"
    record("15. How many prospects? (after adding one)", expected, out, extra)

    print("\n\nQUESTION | EXPECTED | ACTUAL | PASS/FAIL")
    print("---|---|---|---")
    for row in rows:
        exp = row["expected"].replace("\n", " / ")
        act = row["actual"].replace("\n", " / ")
        tag = row["status"] if row["status"] == "PASS" else f"{row['status']} ({row['cause']}; {'; '.join(row['reasons'])})"
        print(f"{row['question']} | {exp} | {act} | {tag}")

    failed = [r for r in rows if r["status"] != "PASS"]
    # Keep pytest green on model-only misses so the table is the report; fail on tool-code bugs.
    tool_bugs = [r for r in failed if r["cause"] == "tool code"]
    assert not tool_bugs, tool_bugs
