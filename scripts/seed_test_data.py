"""
Seed the Acme demo workspace for the FreedomBot test guide
==========================================================

    docker compose exec api python -m scripts.seed_test_data        # in Docker
    python -m scripts.seed_test_data                                # local venv

Puts realistic business data behind every query in docs/agent/FreedomBot_Test_Guide.pdf, so each
answer can be checked against what is really stored:

* first runs ``scripts.reset_dhanu_demo`` (users, packs, pending approvals, billables, test memory);
* puts the demo accounts back to their roles and ACTIVE status (admin tests change them);
* adds companies, contacts, deals, notes, tasks, invoices, company memory (SOPs, facts, decisions,
  a two-version policy and one conflict);
* recreates the test mailbox threads and calendar events relative to *now*, so "this week",
  "tomorrow" and "yesterday" always have data.

Safe to re-run: records are matched by name, email, invoice number or memory key, and only the
mailbox / calendar rows this script owns (``*_tp_*`` ids) are recreated. It only touches the
``acme`` demo workspace and refuses to run anywhere else.
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bizos.util.dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

WORKSPACE = "acme"
MAILBOX = "ops@acme.example.com"

# --------------------------------------------------------------------------------------------- data
# (name, domain, industry, size)
COMPANIES = [
    ("Globex Foods", "globexfoods.com", "Food distribution", "1,200 staff"),
    ("Initech", "initech.com", "Software", "300 staff"),
    ("Umbrella Health", "umbrellahealth.com", "Healthcare supplies", "2,500 staff"),
    ("Stark Freight", "starkfreight.com", "Freight carrier", "800 staff"),
    ("Wayne Retail", "wayneretail.com", "Retail", "5,000 staff"),
    ("Soylent Beverages", "soylentbev.com", "Beverages", "450 staff"),
    ("Hooli", "hooli.com", "Technology", "9,000 staff"),
    ("Vandelay Imports", "vandelayimports.com", "Import / export", "120 staff"),
    ("Massive Dynamic", "massivedynamic.com", "Manufacturing", "3,000 staff"),
]

# (first, last, email, company, title, lifecycle, tags, phone)
CONTACTS = [
    ("Priya", "Sharma", "priya.sharma@globexfoods.com", "Globex Foods", "Director of Supply Chain", "customer",
     ["enterprise", "renewal"], "+1 973 555 0142"),
    ("Daniel", "Okafor", "daniel.okafor@globexfoods.com", "Globex Foods", "Warehouse Manager", "customer",
     ["newark"], "+1 973 555 0187"),
    ("Rachel", "Kim", "rachel.kim@initech.com", "Initech", "Head of Operations", "lead",
     ["inbound", "demo-requested"], "+1 512 555 0110"),
    ("Kevin", "Patel", "kevin.patel@umbrellahealth.com", "Umbrella Health", "Procurement Manager", "lead",
     ["cold-chain"], "+1 617 555 0175"),
    ("Sofia", "Martinez", "sofia.martinez@starkfreight.com", "Stark Freight", "Partnerships Lead", "partner",
     ["carrier"], "+1 214 555 0133"),
    ("Liam", "O'Connor", "liam.oconnor@wayneretail.com", "Wayne Retail", "VP Logistics", "customer",
     ["peak-season"], "+1 312 555 0164"),
    ("Aisha", "Rahman", "aisha.rahman@soylentbev.com", "Soylent Beverages", "COO", "lead",
     ["referral"], "+1 415 555 0121"),
    ("Marcus", "Lee", "marcus.lee@hooli.com", "Hooli", "Facilities Director", "lead",
     ["webinar"], "+1 650 555 0190"),
    ("Elena", "Petrova", "elena.petrova@vandelayimports.com", "Vandelay Imports", "Customs Manager", "customer",
     ["import"], "+1 201 555 0158"),
    ("Ben", "Carter", "ben.carter@massivedynamic.com", "Massive Dynamic", "Plant Logistics Manager", "lead",
     ["inbound"], "+1 248 555 0102"),
    ("Grace", "Liu", "grace.liu@northwind.com", "Northwind", "Finance Controller", "customer",
     ["billing"], "+1 206 555 0147"),
]

# (deal name, contact email, company, stage, amount, close in days)
DEALS = [
    ("Globex Foods — Newark scanning rollout", "priya.sharma@globexfoods.com", "Globex Foods", "won", 18400, -33),
    ("Globex Foods — 2027 warehousing renewal", "priya.sharma@globexfoods.com", "Globex Foods", "negotiation",
     240000, 38),
    ("Initech — fulfilment pilot", "rachel.kim@initech.com", "Initech", "qualified", 36000, 45),
    ("Umbrella Health — cold-chain storage", "kevin.patel@umbrellahealth.com", "Umbrella Health", "proposal",
     96000, 30),
    ("Wayne Retail — peak season overflow", "liam.oconnor@wayneretail.com", "Wayne Retail", "negotiation",
     64000, 14),
    ("Wayne Retail — October overflow storage", "liam.oconnor@wayneretail.com", "Wayne Retail", "pending_invoice",
     7800, 0),
    ("Soylent Beverages — regional distribution", "aisha.rahman@soylentbev.com", "Soylent Beverages", "new",
     120000, 75),
    ("Hooli — office relocation logistics", "marcus.lee@hooli.com", "Hooli", "proposal", 28000, 21),
    ("Vandelay Imports — customs brokerage", "elena.petrova@vandelayimports.com", "Vandelay Imports", "won",
     45000, -12),
    ("Massive Dynamic — inbound parts consolidation", "ben.carter@massivedynamic.com", "Massive Dynamic", "lost",
     52000, -5),
]

# (contact email, body)
NOTES = [
    ("priya.sharma@globexfoods.com",
     "Renewal call prep: Globex forecasts 1,850 pallets/month in 2027 (up from 1,400). Wants a 12-month term "
     "and pricing for 2,000 pallet positions."),
    ("john@northwind.com", "John prefers calls before 11am ET. Renewal numbers go to Grace Liu (finance) first."),
    ("liam.oconnor@wayneretail.com", "Needs 600 extra pallet positions Nov 15 – Jan 10 for peak season."),
]

# (title, contact email, due in days, assignee)
TASKS = [
    ("Send Globex revised 2027 proposal", "priya.sharma@globexfoods.com", 3, "sales@acme"),
    ("Book Umbrella Health site visit", "kevin.patel@umbrellahealth.com", 5, "sales@acme"),
    ("Chase Contoso on overdue invoice INV-2026-0095", "maria@contoso.com", 1, "finance@acme"),
]

# (number, customer, email, status, total, balance, issued days ago, due days from issue)
INVOICES = [
    ("INV-2026-0087", "Acme Logistics", "billing@acmelogistics.example.com", "paid", 3900, 0, 84, 30),
    ("INV-2026-0095", "Contoso", "ap@contoso.com", "overdue", 6750, 6750, 42, 30),
    ("INV-2026-0101", "Northwind", "grace.liu@northwind.com", "paid", 12500, 0, 38, 30),
    ("INV-2026-0112", "Contoso", "ap@contoso.com", "overdue", 3400, 3400, 35, 30),
    ("INV-2026-0118", "Northwind", "grace.liu@northwind.com", "open", 8200, 8200, 20, 30),
    ("INV-2026-0120", "Vandelay Imports", "elena.petrova@vandelayimports.com", "paid", 15000, 0, 16, 30),
    ("INV-ATL-0920", "Globex Foods", "priya.sharma@globexfoods.com", "overdue", 18400, 18400, 33, 30),
]

# (category, key, title, content, tags, access area or None)
MEMORY = [
    ("FACT", "warehouse_locations", "Warehouse locations",
     "Newark, NJ: main site, 120,000 sq ft, 6,500 pallet positions, ambient and cold-chain. "
     "Dallas, TX: returns and overflow, 45,000 sq ft. Chicago, IL: headquarters and cross-dock.",
     ["operations", "ops", "warehouse"], None),
    ("FACT", "storage_rates_2026", "Storage and handling rates 2026",
     "Ambient storage $18 per pallet per month. Cold-chain (2-8°C) storage $32 per pallet per month. "
     "Handling in or out $6.50 per pallet. Minimum monthly billing $2,500.",
     ["finance", "pricing"], "finance"),
    ("FACT", "credit_terms_new_customers", "Credit terms for new customers",
     "New customers start on Net-15 with a $25,000 credit limit. Move to Net-30 after 6 months with no late "
     "payments.", ["finance", "payment"], "finance"),
    ("SOP", "carrier_damage_claims", "Pallet damage claims with carriers",
     "1) Photograph the damaged pallet and the trailer before unloading. 2) Write 'damaged' on the bill of lading "
     "before the driver signs. 3) Open a claim in the carrier portal within 5 business days with photos, BOL and "
     "invoice value. 4) Tell the customer the claim number the same day. 5) Follow up every 10 days until settled.",
     ["sop", "operations", "carrier", "claims"], None),
    ("SOP", "new_customer_onboarding", "New customer onboarding",
     "1) Countersigned MSA and credit check done. 2) Create the customer and contacts in the CRM. "
     "3) Collect SKU list, pallet forecast and labelling rules. 4) Book a kickoff call within 3 business days. "
     "5) First inbound receipt is supervised by the site lead.", ["sop", "onboarding", "sales"], None),
    ("SOP", "invoice_dispute_sop", "Invoice disputes",
     "Log the dispute within 1 business day, pause late fees on the disputed amount, check the billing lines "
     "against the warehouse system, and reply to the customer within 3 business days. Credits over $1,000 need "
     "finance approval.", ["sop", "finance", "billing"], "finance"),
    ("DECISION", "primary_ltl_carrier_2027", "Primary LTL carrier for 2027",
     "Stark Freight is our primary LTL carrier from January 1, 2027 (decided October 14, 2026 after the rate "
     "review). Backup carrier: Northwind Logistics.", ["decision", "operations", "carrier"], None),
    ("FACT", "goal_3", "Goal 3", "Sign $2.4M in 2027 renewals by December 31.", ["strategy", "goal"], None),
    ("FACT", "goal_4", "Goal 4", "Bring overdue receivables under $10,000 by the end of November.",
     ["strategy", "goal"], None),
    ("FACT", "liability_cap_standard", "Standard MSA liability cap",
     "Liability is capped at the fees paid in the 12 months before the claim. The cap does not apply to gross "
     "negligence, wilful misconduct or confidentiality breaches.", ["legal", "contract", "clause"], "legal"),
    ("FACT", "nda_term", "Mutual NDA confidentiality term",
     "Confidential information stays protected for 3 years after disclosure. Public information and "
     "independently developed work are excluded.", ["legal", "contract", "clause"], "legal"),
    ("FACT", "travel_per_diem", "Travel per diem", "Domestic travel per diem is $75 per day.",
     ["finance", "expenses"], None),
]

#: Two versions on one key, so memory_history has something to show (old value first).
HISTORY = ("FACT", "late_payment_fee", "Late payment fee",
           ["Late payment fee: 1.5% per month on balances more than 30 days overdue.",
            "Late payment fee: 2% per month on balances more than 30 days overdue, effective October 1, 2026."],
           ["finance", "payment"], "finance")

#: A second, different value on the same topic: lands PENDING as a conflict for memory_escalate_conflict.
CONFLICT = ("FACT", "travel_per_diem_2026", "Travel per diem", "Domestic travel per diem is $90 per day.",
            ["finance", "expenses"], None)

# (subject, sender, body, hours ago, extra messages [(sender, body, hours ago)])
THREADS = [
    ("Globex renewal: revised volumes for 2027", "priya.sharma@globexfoods.com",
     "Hi team,\n\nAhead of the renewal call, our 2027 forecast is 1,850 pallets a month (up from 1,400 this year). "
     "Please price 2,000 pallet positions on a 12-month term and send the revised proposal by Tuesday.\n\n"
     "Thanks,\nPriya", 2, []),
    ("Agenda for Monday's planning meeting", "john@northwind.com",
     "Hi,\n\nFor Monday's planning meeting I'd like to cover: 1) Q1 volumes, 2) the renewal numbers, "
     "3) dock scheduling at Newark. Can we move it to 11am ET?\n\nJohn", 3, []),
    ("Demo request: fulfilment pilot", "rachel.kim@initech.com",
     "Hello,\n\nWe ship about 500 orders a day across 3 product lines and want to test outsourced fulfilment. "
     "Could we see a demo next week? Wednesday or Thursday afternoon works.\n\nRachel Kim\nHead of Operations, Initech",
     5, []),
    ("Contoso rollout: security questionnaire", "maria@contoso.com",
     "Hi,\n\nOur IT team needs the attached security questionnaire completed before we sign the rollout. "
     "Can you return it by Wednesday?\n\nMaria", 6, []),
    ("Cold-chain storage questions", "kevin.patel@umbrellahealth.com",
     "Hi,\n\nWe store medical supplies at 2-8°C. Is your Newark cold room validated, and can you share the last "
     "temperature-mapping report and audit certificates?\n\nKevin Patel", 26, []),
    ("Peak season overflow: need an answer by Friday", "liam.oconnor@wayneretail.com",
     "Team,\n\nWe need 600 extra pallet positions from November 15 to January 10. Please confirm capacity and "
     "rate by Friday or we go to another provider.\n\nLiam", 30,
     [(MAILBOX, "Hi Liam, thanks. We are checking Newark capacity and will confirm by Thursday. — Acme Ops", 28)]),
    ("Invoice INV-2026-0118: PO number", "grace.liu@northwind.com",
     "Hi,\n\nPlease reissue invoice INV-2026-0118 with our PO number NW-4471, otherwise AP cannot release "
     "payment.\n\nGrace Liu\nFinance Controller, Northwind", 50, []),
    ("Carrier rate update for November", "sofia.martinez@starkfreight.com",
     "Hello,\n\nLTL rates go up 4.5% from November 1. Full-truckload rates stay the same. The new rate card is "
     "attached.\n\nSofia Martinez\nStark Freight", 52, []),
    ("Payment for INV-2026-0095", "ap@contoso.com",
     "Hello,\n\nPayment for INV-2026-0095 ($6,750) is scheduled for October 30. Apologies for the delay.\n\n"
     "Contoso Accounts Payable", 70, []),
    ("Intro: regional distribution", "aisha.rahman@soylentbev.com",
     "Hi,\n\nMarcus at Hooli suggested we talk. We are launching in the Midwest in Q1 and need a distribution "
     "partner for about 900 pallets a month. Are you free for a call next week?\n\nAisha Rahman, COO", 75, []),
]

# (title, day offset, start hour, start minute, minutes, attendees, location)
EVENTS = [
    ("Globex 2027 renewal call", 1, 11, 0, 45, ["priya.sharma@globexfoods.com", "daniel.okafor@globexfoods.com"],
     "Zoom"),
    ("Contoso rollout steering call", 1, 15, 0, 45, ["maria@contoso.com", "tom@contoso.com"], "Teams"),
    ("Weekly pipeline review", 2, 9, 30, 30, [], "Chicago HQ, room 4"),
    ("Initech fulfilment pilot demo", 2, 14, 0, 60, ["rachel.kim@initech.com"], "Zoom"),
    ("Call with John Baker: Q1 volumes", 3, 16, 0, 30, ["john@northwind.com"], "Phone"),
    ("Umbrella Health cold-chain site visit", 4, 10, 0, 90, ["kevin.patel@umbrellahealth.com"],
     "Newark warehouse"),
    ("Wayne Retail peak overflow planning", 4, 14, 0, 60, ["liam.oconnor@wayneretail.com"], "Teams"),
    ("Carrier rate review with Stark Freight", 5, 15, 0, 30, ["sofia.martinez@starkfreight.com"], "Zoom"),
    ("Monthly finance close review", 6, 11, 0, 60, [], "Chicago HQ, room 2"),
]

DEV_ROLES = None  # filled from bizos.bootstrap.DEV_USERS at run time


# --------------------------------------------------------------------------------------------- seed
def _tz(settings: Any) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(settings, "timezone", None) or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _reset_users(client: Any) -> int:
    from bizos.bootstrap import DEV_USERS
    from bizos.control import store as control

    domain = "acme.example.com"
    fixed = 0
    by_email = {u.email: u for u in control.list_users(client.id)}
    for template, _name, roles, _scopes in DEV_USERS:
        user = by_email.get(template.format(domain=domain))
        if user is None:
            continue
        if set(user.roles) != set(roles):
            control.set_user_roles(user.id, roles, granted_by="seed_test_data")
            fixed += 1
        if str(user.status) != "ACTIVE":
            control.set_user_status(user.id, "ACTIVE")
            fixed += 1
    return fixed


def _seed_crm(conn: Any, today: Any) -> dict[str, int]:
    from bizos.util.ids import new_id

    added = {"companies": 0, "contacts": 0, "deals": 0, "notes": 0, "tasks": 0}
    company_id: dict[str, str] = {
        r.name: r.id for r in conn.execute(text("SELECT id, name FROM crm_companies"))
    }
    for name, domain, industry, size in COMPANIES:
        if name not in company_id:
            company_id[name] = new_id("co")
            conn.execute(
                text("INSERT INTO crm_companies (id, name, domain, industry, size) VALUES (:i, :n, :d, :ind, :s)"),
                {"i": company_id[name], "n": name, "d": domain, "ind": industry, "s": size},
            )
            added["companies"] += 1

    contact_id: dict[str, str] = {
        r.email.lower(): r.id
        for r in conn.execute(text("SELECT id, email FROM crm_contacts WHERE email IS NOT NULL"))
    }
    for first, last, email, company, title, lifecycle, tags, phone in CONTACTS:
        if email.lower() in contact_id:
            # Bring a contact the tests changed or deleted back to its starting values.
            conn.execute(
                text(
                    "UPDATE crm_contacts SET first_name=:f, last_name=:l, company=:c, title=:t, lifecycle=:lc, "
                    "tags=:g, phone=:p, owner='sales@acme' WHERE lower(email)=lower(:e)"
                ),
                {"f": first, "l": last, "c": company, "t": title, "lc": lifecycle, "g": tags, "p": phone, "e": email},
            )
            continue
        contact_id[email.lower()] = new_id("con")
        conn.execute(
            text(
                "INSERT INTO crm_contacts (id, first_name, last_name, email, phone, company, title, lifecycle, "
                "tags, owner) VALUES (:i, :f, :l, :e, :p, :c, :t, :lc, :g, 'sales@acme')"
            ),
            {"i": contact_id[email.lower()], "f": first, "l": last, "e": email, "p": phone, "c": company,
             "t": title, "lc": lifecycle, "g": tags},
        )
        added["contacts"] += 1
    # The original demo contacts the guide edits: back to their bootstrap values.
    for email, title, lifecycle, tags in (
        ("maria@contoso.com", "VP Sales", "lead", ["inbound", "webinar"]),
        ("tom@contoso.com", "Analyst", "lead", ["inbound"]),
        ("john@northwind.com", "Ops Lead", "customer", ["renewal"]),
    ):
        conn.execute(
            text(
                "UPDATE crm_contacts SET title=:t, lifecycle=:lc, tags=:g, owner='sales@acme' "
                "WHERE lower(email)=:e"
            ),
            {"t": title, "lc": lifecycle, "g": tags, "e": email},
        )

    deals = {r.name: r.id for r in conn.execute(text("SELECT id, name FROM crm_deals"))}
    for name, email, company, stage, amount, close_in in DEALS:
        params = {"n": name, "c": contact_id.get(email.lower()), "co": company_id[company], "s": stage,
                  "a": amount, "cd": today + timedelta(days=close_in)}
        if name in deals:
            conn.execute(
                text("UPDATE crm_deals SET stage=:s, amount=:a, close_date=:cd, updated_at=NOW() "
                     "WHERE name=:n"), params)
            continue
        conn.execute(
            text(
                "INSERT INTO crm_deals (id, name, contact_id, company_id, stage, amount, currency, close_date, owner) "
                "VALUES (:i, :n, :c, :co, :s, :a, 'USD', :cd, 'sales')"
            ),
            {"i": new_id("deal"), **params},
        )
        added["deals"] += 1
    # The original demo deals back to their bootstrap stages.
    for name, stage in (("Northwind expansion", "proposal"), ("Contoso pilot", "new"),
                        ("Contoso rollout", "negotiation")):
        conn.execute(text("UPDATE crm_deals SET stage=:s WHERE name=:n"), {"s": stage, "n": name})

    for email, body in NOTES:
        cid = contact_id.get(email.lower())
        if cid and not conn.execute(text("SELECT 1 FROM crm_notes WHERE contact_id=:c AND body=:b"),
                                    {"c": cid, "b": body}).first():
            conn.execute(
                text("INSERT INTO crm_notes (id, contact_id, body, author) VALUES (:i, :c, :b, 'sales@acme')"),
                {"i": new_id("note"), "c": cid, "b": body},
            )
            added["notes"] += 1
    for title, email, due_in, assignee in TASKS:
        if not conn.execute(text("SELECT 1 FROM crm_tasks WHERE title=:t"), {"t": title}).first():
            conn.execute(
                text(
                    "INSERT INTO crm_tasks (id, title, contact_id, assignee, due_at, status, created_by) "
                    "VALUES (:i, :t, :c, :a, NOW() + make_interval(days => :d), 'open', 'seed_test_data')"
                ),
                {"i": new_id("task"), "t": title, "c": contact_id.get(email.lower()), "a": assignee, "d": due_in},
            )
            added["tasks"] += 1
    return added


def _seed_invoices(conn: Any, now: datetime) -> int:
    from bizos.util.ids import new_id

    added = 0
    for number, customer, email, status, total, balance, issued_ago, terms in INVOICES:
        cust = conn.execute(text("SELECT id FROM accounting_customers WHERE name=:n"), {"n": customer}).first()
        cust_id = cust.id if cust else new_id("acust")
        if not cust:
            conn.execute(text("INSERT INTO accounting_customers (id, name, email) VALUES (:i, :n, :e)"),
                         {"i": cust_id, "n": customer, "e": email})
        issued = now - timedelta(days=issued_ago)
        params = {"i": new_id("inv"), "num": number, "c": cust_id, "cn": customer, "ce": email, "s": status,
                  "t": total, "b": balance, "iss": issued, "due": issued + timedelta(days=terms)}
        exists = conn.execute(text("SELECT 1 FROM accounting_invoices WHERE number=:num"), params).first()
        if exists:
            conn.execute(text("UPDATE accounting_invoices SET status=:s, total=:t, balance_due=:b, issued_at=:iss, "
                              "due_at=:due, updated_at=NOW() WHERE number=:num"), params)
            continue
        conn.execute(
            text(
                "INSERT INTO accounting_invoices (id, number, customer_id, customer_name, customer_email, status, "
                "currency, total, balance_due, issued_at, due_at) VALUES (:i, :num, :c, :cn, :ce, :s, 'USD', :t, "
                ":b, :iss, :due)"
            ),
            params,
        )
        added += 1
    conn.execute(
        text("UPDATE accounting_customers c SET balance_due = COALESCE((SELECT SUM(balance_due) FROM "
             "accounting_invoices i WHERE i.customer_id = c.id), 0), updated_at = NOW()")
    )
    return added


def _seed_mailbox_and_calendar(conn: Any, now: datetime, tz: ZoneInfo) -> tuple[int, int]:
    from bizos.util.ids import new_id

    conn.execute(text("DELETE FROM email_messages WHERE id LIKE 'msg_tp_%'"))
    conn.execute(text("DELETE FROM calendar_events WHERE id LIKE 'evt_tp_%'"))
    # Past bootstrap meetings share titles with this week's ("Weekly pipeline review"): retire them.
    conn.execute(text("UPDATE calendar_events SET status = 'cancelled' WHERE starts_at < :n"), {"n": now})
    messages = 0
    for subject, sender, body, hours_ago, extra in THREADS:
        thread = new_id("thr_tp")
        for who, text_, ago in [(sender, body, hours_ago), *extra]:
            outbound = who == MAILBOX
            conn.execute(
                text(
                    "INSERT INTO email_messages (id, thread_id, direction, sender, recipients, subject, body, "
                    "sent_at) VALUES (:i, :t, :dir, :s, :r, :su, :b, :d)"
                ),
                {"i": new_id("msg_tp"), "t": thread, "dir": "outbound" if outbound else "inbound", "s": who,
                 "r": [sender] if outbound else [MAILBOX], "su": subject if not outbound else f"Re: {subject}",
                 "b": text_, "d": now - timedelta(hours=ago)},
            )
            messages += 1
    today = now.astimezone(tz).date()
    for title, offset, hour, minute, minutes, attendees, location in EVENTS:
        start = datetime.combine(today + timedelta(days=offset), time(hour, minute), tzinfo=tz)
        conn.execute(
            text(
                "INSERT INTO calendar_events (id, title, description, location, starts_at, ends_at, attendees, "
                "status, organizer, created_by) VALUES (:i, :t, :d, :l, :s, :e, :a, 'confirmed', :o, "
                "'seed_test_data')"
            ),
            {"i": new_id("evt_tp"), "t": title, "d": "", "l": location, "s": start,
             "e": start + timedelta(minutes=minutes), "a": attendees, "o": MAILBOX},
        )
    return messages, len(EVENTS)


def _seed_memory(ctx: Any, settings: Any) -> int:
    from bizos.memory import store as memory
    from bizos.types import ApprovalStatus, MemoryCategory, SourceType

    added = 0

    def put(category: str, key: str, title: str, content: str, tags: list[str], area: Any,
            approval: Any = ApprovalStatus.APPROVED) -> None:
        nonlocal added
        cat = MemoryCategory(category)
        current = memory.get_by_key(cat, key, ctx=ctx, include_archived=True)
        if current is not None and current.current and current.current.content == content:
            return
        memory.put(category=cat, memory_key=key, title=title, content=content, tags=tags, access_area=area,
                   source_type=SourceType.MANUAL, approval_status=approval, settings=settings, ctx=ctx)
        added += 1

    for category, key, title, content, tags, area in MEMORY:
        put(category, key, title, content, tags, area)
    category, key, title, versions, tags, area = HISTORY
    existing = memory.get_by_key(MemoryCategory(category), key, ctx=ctx, include_archived=True)
    if existing is None:
        for content in versions:
            put(category, key, title, content, tags, area)
    else:
        put(category, key, title, versions[-1], tags, area)
    category, key, title, content, tags, area = CONFLICT
    # No explicit approval: the store sees the clash with travel_per_diem and holds it as PENDING.
    put(category, key, title, content, tags, area, approval=None)
    return added


def main() -> int:
    from bizos.bootstrap import admin_context
    from bizos.control import store as control
    from bizos.tenancy.context import tenant_scope
    from bizos.tenancy.registry import workspace_connection
    from bizos.util.timeutil import utcnow
    from scripts.reset_dhanu_demo import main as reset_demo

    reset_demo()
    client = control.get_client_by_slug(WORKSPACE)
    if client is None or client.slug != WORKSPACE:
        print("The acme demo workspace does not exist; nothing seeded.")
        return 1
    ctx = admin_context(client)
    tz = _tz(client.settings)
    now = utcnow()
    users = _reset_users(client)
    with workspace_connection(ctx) as conn:
        crm = _seed_crm(conn, now.astimezone(tz).date())
        invoices = _seed_invoices(conn, now)
        messages, events = _seed_mailbox_and_calendar(conn, now, tz)
        retired = conn.execute(
            text(
                "UPDATE memory_versions SET status = 'SUPERSEDED', effective_until = NOW() WHERE status = 'ACTIVE' "
                "AND item_id IN (SELECT id FROM memory_items WHERE memory_key LIKE 'm04\\_%' "
                "OR memory_key LIKE 'conflict\\_%')"
            )
        ).rowcount
    with tenant_scope(ctx):
        memories = _seed_memory(ctx, client.settings)
    print(
        f"Test data ready ({tz.key}): {users} user role/status fix(es); added {crm['companies']} companies, "
        f"{crm['contacts']} contacts, {crm['deals']} deals, {crm['notes']} notes, {crm['tasks']} tasks, "
        f"{invoices} invoices, {memories} memory value(s); {messages} emails and {events} calendar events "
        f"recreated relative to now; {retired} leftover test memory value(s) retired."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
