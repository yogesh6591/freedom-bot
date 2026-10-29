"""Live check: bulk delete/remove/clear must refuse, not list. acquisition_test only."""

from __future__ import annotations

import os

import pytest

from tests.conftest import ACQUISITION_TEST_DB
from tests.test_unified_assistant import ask

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SHOW_PROSPECTS") != "1",
    reason="set RUN_SHOW_PROSPECTS=1 to talk to the real model against acquisition_test",
)

ETHAN = "ethanparker.us07@gmail.com"
HARBOR_TEST = "Harbor Light Test"
REFUSAL = "I can't delete everyone at once. Tell me which people to delete (up to 25 names) and I'll ask you to confirm."
LIST_TOOLS = {"query_records", "show_campaign_people", "show_contacts", "show_campaigns"}
DELETE_TOOLS = {"delete_prospects", "delete_campaign", "remove_from_campaign"}


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


def _seed():
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    _wipe()
    db = SessionLocal()
    alice = Prospect(company="Acme", executive_name="Alice Park", email="alice@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    ben = Prospect(company="Acme", executive_name="Ben Quinn", email="ben@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    ethan = Prospect(company="Acme", executive_name="Ethan Parker", email=ETHAN, approval_status=ApprovalStatus.APPROVED)
    evan = Prospect(company="Acme", executive_name="Evan Cole", email="evan@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    db.add_all([alice, ben, ethan, evan])
    db.flush()
    harbor = Campaign(name=HARBOR_TEST, sender_name="Me")
    db.add(harbor)
    db.flush()
    db.add(CampaignProspect(campaign_id=harbor.id, prospect_id=alice.id, outreach_status=OutreachStatus.PENDING))
    db.add(CampaignProspect(campaign_id=harbor.id, prospect_id=ben.id, outreach_status=OutreachStatus.ACTIVE))
    db.add(CampaignProspect(campaign_id=harbor.id, prospect_id=ethan.id, outreach_status=OutreachStatus.PENDING))
    db.commit()
    db.close()


def _snapshot():
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect
    from acquisition.models.prospect import Prospect

    db = SessionLocal()
    people = sorted((p.email, p.executive_name) for p in db.query(Prospect).all())
    campaigns = sorted(c.name for c in db.query(Campaign).all())
    links = sorted(
        (str(link.campaign_id), str(link.prospect_id), str(getattr(link.outreach_status, "value", link.outreach_status)))
        for link in db.query(CampaignProspect).all()
    )
    db.close()
    return people, campaigns, links


def _tools(out: dict) -> list[str]:
    return [c.get("tool") for c in ((out.get("trace") or {}).get("tool_log") or [])]


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
        lambda ctx, parsed: agent.FulfillResult(
            campaign_id="00000000-0000-0000-0000-000000000001", message="(test) not sent"
        ),
    )
    app_settings.llm_available.cache_clear()
    yield
    app_settings.llm_available.cache_clear()


def test_bulk_delete_refuses_and_lists_still_work(live_env, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    settings = client_scratch.settings
    rows = []

    def record(question, expected, out, extra_fail=""):
        reply = out.get("content") or ""
        tools = _tools(out)
        reasons = [extra_fail] if extra_fail else []
        status = "PASS" if not reasons else "FAIL"
        rows.append({"question": question, "expected": expected, "actual": reply, "tools": tools, "status": status, "reasons": reasons})
        print("\n---")
        print("Q:", question)
        print("EXPECTED:", expected)
        print("ACTUAL:", reply)
        print("TOOLS:", tools)
        print(status, reasons)

    _seed()
    before = _snapshot()

    bulk = [
        "Delete all prospects",
        "Remove everyone",
        "Delete all campaigns",
        "Clear the prospect list",
        "Wipe everything",
    ]
    for question in bulk:
        before = _snapshot()
        out = ask(ctx, settings, question)
        tools = set(_tools(out))
        extra_fail = ""
        if "refuse_bulk_change" not in tools:
            extra_fail = "did not call refuse_bulk_change"
        listed = tools & LIST_TOOLS
        deleted = tools & DELETE_TOOLS
        if listed:
            extra_fail = (extra_fail + "; " if extra_fail else "") + f"called list tool {sorted(listed)}"
        if deleted:
            extra_fail = (extra_fail + "; " if extra_fail else "") + f"called delete tool {sorted(deleted)}"
        if _snapshot() != before:
            extra_fail = (extra_fail + "; " if extra_fail else "") + "data changed"
        reply = (out.get("content") or "").strip()
        if REFUSAL not in reply:
            extra_fail = (extra_fail + "; " if extra_fail else "") + "reply is not the tool text"
        record(question, REFUSAL, out, extra_fail=extra_fail)

    before = _snapshot()
    out = ask(ctx, settings, f"Delete prospect {ETHAN}")
    tools = set(_tools(out))
    extra_fail = ""
    reply = out.get("content") or ""
    if "delete_prospects" in tools and any(
        str(c.get("result") or "").lower().startswith("deleted")
        for c in ((out.get("trace") or {}).get("tool_log") or [])
        if c.get("tool") == "delete_prospects"
    ):
        extra_fail = "deleted already"
    if "?" not in reply and "yes" not in reply.casefold():
        extra_fail = (extra_fail + "; " if extra_fail else "") + "did not ask yes/no"
    if ETHAN not in {email for email, _ in _snapshot()[0]}:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "prospect was deleted"
    if _snapshot() != before:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "data changed"
    record(
        f"Delete prospect {ETHAN}",
        "Ask one yes/no; do not delete yet",
        out,
        extra_fail=extra_fail,
    )

    before = _snapshot()
    out = ask(ctx, settings, f"Delete the campaign {HARBOR_TEST}")
    extra_fail = ""
    reply = out.get("content") or ""
    tools = set(_tools(out))
    if "delete_campaign" not in tools:
        extra_fail = "did not call delete_campaign"
    if "deleted" in reply.casefold() and "?" not in reply:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "deleted already"
    if "?" not in reply:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "did not ask yes/no"
    if HARBOR_TEST not in _snapshot()[1]:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "campaign was deleted"
    if _snapshot() != before:
        extra_fail = (extra_fail + "; " if extra_fail else "") + "data changed"
    record(
        f"Delete the campaign {HARBOR_TEST}",
        "Warning with counts and one yes/no; do not delete yet",
        out,
        extra_fail=extra_fail,
    )

    listings = [
        ("How many prospects do we have?", "workspace count from query_records"),
        ("List all prospects", "list from query_records"),
        (f"Who is in {HARBOR_TEST}?", "people in that campaign"),
        ("Which prospects are not in any campaign?", "only people in no campaign"),
    ]
    for question, expected in listings:
        before = _snapshot()
        out = ask(ctx, settings, question)
        tools = set(_tools(out))
        extra_fail = ""
        if not (tools & LIST_TOOLS):
            extra_fail = "did not call a listing tool"
        if tools & DELETE_TOOLS:
            extra_fail = (extra_fail + "; " if extra_fail else "") + "called a delete tool"
        if _snapshot() != before:
            extra_fail = (extra_fail + "; " if extra_fail else "") + "data changed"
        record(question, expected, out, extra_fail=extra_fail)

    print("\n\nQUESTION | TOOL | ACTUAL | PASS/FAIL")
    print("---|---|---|---")
    for row in rows:
        tag = row["status"] if row["status"] == "PASS" else f"FAIL ({'; '.join(row['reasons'])})"
        print(f"{row['question']} | {row['tools']} | {row['actual'].replace(chr(10), ' / ')} | {tag}")

    failed = [r for r in rows if r["status"] != "PASS"]
    assert not any("data changed" in "; ".join(r["reasons"]) for r in failed), failed
