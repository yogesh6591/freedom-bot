"""One email conversation: who the email goes to, the draft on the card, and the send.

Everything else the chat can do (records, assessments, bookings, payments, journey) lives in the tool modules
registered in ``toolkit.py``; this class is only the email card.

State lives in the last assistant message's ``meta.state`` (same place as before), so the email-card endpoints
and chat history keep working:

    {"recipients": [{"email", "name"}], "draft": {...}, "overrides": {email: {...}}, "pending": {...},
     "last_batch": {...}}

Every method returns the text the model sees. Nothing here sends mail: ``send_approved`` only queues it; the background sender (app/outreach/sender.py)
delivers it through the ops chatbot's Gmail, with spacing and a daily ceiling.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Optional

from app.outreach import card, chicago
from app.outreach.approval import CHANGE_VERB, WHOLE_EMAIL, approval_problem, asks_to_change_subject, user_said
from app.outreach.text import (
    _evidence_is_the_address,
    clean_field,
    keep_assessment_link,
    person_fields,
    proper_case,
    strip_assessment_link,
    tokenize,
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OutreachSession:
    def __init__(self, ctx: Any, state: dict, *, latest_message: str = "",
                 db_factory: Optional[Callable[[], Any]] = None) -> None:
        self.ctx = ctx
        self.state = state if isinstance(state, dict) else {}
        self.state.setdefault("recipients", [])
        self.latest = latest_message or ""
        self.card_shown = False
        self.sent_view: Optional[dict] = None  # the "who was emailed" table, set by send_from_template
        self.template_view: Optional[dict] = None  # the saved-template card, set by show/update_email_template
        if db_factory is None:
            from acquisition.db.session import SessionLocal

            db_factory = SessionLocal
        self.db_factory = db_factory

    # ------------------------------------------------------------------ helpers
    def _records(self, db) -> dict:
        from app.outreach.records import people_records

        return people_records(db, [r["email"] for r in self.state["recipients"]])

    def _fields(self, db) -> list[dict]:
        records = self._records(db)
        return [person_fields(records, self.state, r) for r in self.state["recipients"]]

    def _said(self, evidence: str) -> bool:
        return user_said(self.latest, evidence)

    def view(self) -> dict:
        """The email card for the saved draft ({} when there is none)."""
        if not self.state.get("draft"):
            return {}
        db = self.db_factory()
        try:
            return card.draft_view(self._records(db), self.state)
        finally:
            db.close()

    # ------------------------------------------------------------------ recipients
    def choose_recipients(self, people: list[dict] | None = None, mode: str = "replace") -> str:
        """Commit who the email goes to. Each item: {"email"?, "name"?, "company"?}."""
        from app.outreach.records import email_state, find_prospects

        if mode not in ("replace", "add", "remove"):
            return "error: mode must be replace, add or remove"
        db = self.db_factory()
        try:
            chosen: list[dict] = []
            notes: list[str] = []
            for item in people or []:
                email = str(item.get("email") or "").strip()
                name = str(item.get("name") or "").strip()
                company = clean_field(item.get("company")) if item.get("company") else ""
                if email and not _EMAIL.match(email):
                    return f"error: {email!r} is not an email address"
                if not email:
                    if not name:
                        return "error: give an email address or a name for each person"
                    matches = find_prospects(db, name)
                    if not matches:
                        return f"error: no saved prospect matches {name!r}. Ask the user for the email address."
                    if len(matches) > 1:
                        listing = "; ".join(f"{m.executive_name} <{m.email}> ({m.company or 'no company'})" for m in matches)
                        return f"error: {name!r} matches several people: {listing}. Ask which one."
                    email, name = matches[0].email, name or matches[0].executive_name
                known = find_prospects(db, email)
                known = next((k for k in known if k.email.lower() == email.lower()), None)
                if (known is None and email.lower() not in self.latest.lower()
                        and email.lower() not in {r["email"].lower() for r in self.state["recipients"]}):
                    return (f"error: {email} is not a saved prospect and the user did not type it in this message. "
                            "Ask the user for the address.")
                chosen.append({"email": email, "name": proper_case(name) if name else (known.executive_name if known else ""),
                               "company": company})
            current = list(self.state["recipients"])
            keys = {c["email"].lower() for c in chosen}
            if mode == "replace":
                merged = [{"email": c["email"], "name": c["name"]} for c in chosen]
            elif mode == "add":
                merged = current + [{"email": c["email"], "name": c["name"]} for c in chosen
                                    if c["email"].lower() not in {r["email"].lower() for r in current}]
            else:
                merged = [r for r in current if r["email"].lower() not in keys]
            lines, blocked = [], []
            for r in merged:
                known = next(iter(find_prospects(db, r["email"])), None)
                if known is None:
                    lines.append(f"{r['name'] or r['email']} <{r['email']}> - new address, not saved yet (saved when sent)")
                    continue
                st = email_state(db, known.id)
                if st["opted_out"]:
                    blocked.append(f"{known.executive_name} <{known.email}> ({st['opt_status']})")
                    continue
                if st["emails"]:
                    lines.append(
                        f"{known.executive_name} <{known.email}> - ALREADY EMAILED {chicago.fmt_day(st['last_at'])} "
                        f"(subject: {st['last_subject']!r}, {st['last_status']}) -> this will be a FOLLOW-UP"
                    )
                else:
                    lines.append(f"{known.executive_name} <{known.email}> - not emailed yet -> first email")
            if blocked:
                return ("error: cannot email " + ", ".join(blocked) + ". They opted out or bounced. "
                        "Remove them and tell the user.")
            same_people = {r["email"].lower() for r in merged} == {r["email"].lower() for r in current}
            self.state["recipients"] = merged
            for c in chosen:
                if c["company"]:
                    self.state.setdefault("overrides", {}).setdefault(c["email"].lower(), {})["company"] = c["company"]
            if same_people and self.state.get("draft") and merged:
                return ("recipients unchanged: " + ", ".join(r["email"] for r in merged) + ".\n"
                        "A draft is already on the card. If the user's latest message approves it, call send_approved now "
                        "with their own words as approval_evidence. Call propose_email only if the message asks for a "
                        "change. Never rewrite a draft the user just approved.")
            for r in merged:
                known = next(iter(find_prospects(db, r["email"])), None)
                local = r["email"].split("@")[0]
                shown = (r.get("name") or (known.executive_name if known else "") or "").strip()
                if not shown or shown.casefold() == local.casefold():
                    notes.append(f"No name on file for {r['email']} (the address starts with {local!r}). If that clearly "
                                 "contains a personal first name, save it with set_person_details (the address is the "
                                 "evidence) before propose_email. A role or shared mailbox, or any doubt: leave it blank.")
            if len(merged) > 1:
                notes.append("Several people: decide from the user's words whether they get the same wording or their "
                             "own. If the user wants different, separate or individual content for them, use per_person.")
            notes.append("Next: propose_email now, without asking the user anything. A person emailed before gets a "
                         "reply in their earlier thread; if the user asked for a new, fresh or separate email, set "
                         "new_thread to true.")
            return "recipients set:\n" + "\n".join(lines) + ("\n" + "\n".join(notes) if notes else "")
        finally:
            db.close()

    def set_person_details(self, email: str = "", first_name=None, last_name=None, company=None, evidence: str = "") -> str:
        key = str(email or "").strip().casefold()
        known = {str(r["email"]).casefold(): r for r in self.state["recipients"]}
        if key not in known:
            return "error: that address is not one of the chosen recipients"
        db = self.db_factory()
        try:
            records = self._records(db)
            current = person_fields(records, self.state, known[key])
            from_mailbox = _evidence_is_the_address(evidence, known[key]["email"])
            if not from_mailbox and not self._said(evidence):
                return "error: use only a name or company the user actually wrote, as evidence"
            if from_mailbox and current.get("first_name"):
                return "A name is already on file for this person; it was not replaced from the address."
            if from_mailbox:
                company = None
            typed = self.state.setdefault("overrides", {}).setdefault(key, {})
            for field, value in (("first_name", first_name), ("last_name", last_name), ("company", company)):
                if value is not None:
                    typed[field] = clean_field(value)
            if self.state.get("draft"):
                card.refresh_preview(records, self.state)
            f = person_fields(records, self.state, known[key])
            return f"saved: {f['first_name'] or '(no first name)'} {f['last_name']} / {f['company'] or '(no company)'}"
        finally:
            db.close()

    # ------------------------------------------------------------------ the draft
    def propose_email(self, subject: str = "", body: str = "", change_evidence: str = "",
                      per_person: Optional[list] = None, new_thread: Optional[bool] = None,
                      same_for_all: Optional[bool] = None) -> str:
        if not self.state["recipients"]:
            return "error: choose recipients first (choose_recipients)"
        per_person = [x for x in (per_person or []) if isinstance(x, dict)]
        shared = bool((subject or "").strip() and (body or "").strip())
        if not shared and not per_person:
            return "error: subject and body are required"
        db = self.db_factory()
        try:
            records = self._records(db)
            fields = [person_fields(records, self.state, r) for r in self.state["recipients"]]
            # Follow-ups reply in the person's earlier thread unless the user asked for a brand-new email.
            if new_thread is None:
                new_thread = bool((self.state.get("draft") or {}).get("new_thread"))
            self._new_thread = bool(new_thread)
            if per_person:
                return self._propose_per_person(db, records, fields, subject, body, per_person)
            if len(self.state["recipients"]) > 1:
                if same_for_all is None:
                    same_for_all = bool((self.state.get("draft") or {}).get("same_for_all"))
                if not same_for_all:
                    return ("error: there are several recipients. Decide from the user's words: if they want the "
                            "same text for everyone, call propose_email again with same_for_all=true. If they want "
                            "different, separate or individual content, call propose_email again with per_person "
                            "(one subject and body for each person). Nothing was shown yet.")
            subject, body = tokenize(subject.strip(), fields), tokenize(body.strip(), fields)
            subject, body = strip_assessment_link(subject), keep_assessment_link(body)
            prev = self.state.get("draft") or {}
            if prev and (prev.get("email_subject"), prev.get("email_body")) == (subject, body):
                return "ok: this is the draft already shown. Do not call propose_email again; answer in words."
            if prev and WHOLE_EMAIL.search(self.latest) and CHANGE_VERB.search(self.latest):
                s_changed = prev.get("email_subject") != subject
                b_changed = prev.get("email_body") != body
                if s_changed != b_changed:
                    return ("error: the user asked to change the whole email but only the "
                            f"{'subject' if s_changed else 'body'} changed. Call propose_email again with BOTH a new "
                            "subject and a new body.")
            if prev and not WHOLE_EMAIL.search(self.latest):
                # A small edit ("put the link in the middle", "make it shorter") must not rewrite the rest.
                if prev.get("email_subject") != subject and not asks_to_change_subject(self.latest):
                    return ("error: the user did not ask to change the subject. Call propose_email again with the subject "
                            f"exactly as it is: {prev.get('email_subject')!r}")
                old_breaks = len(re.findall(r"\n\s*\n", prev.get("email_body") or ""))
                new_breaks = len(re.findall(r"\n\s*\n", body))
                if old_breaks >= 2 and new_breaks < old_breaks:
                    return ("error: keep the email's paragraphs exactly as they are, with the blank lines between them. "
                            "Change only what the user asked for and copy every other line word for word. "
                            f"Current body:\n{prev.get('email_body')}")
            if prev.get("user_edited") and not (change_evidence and self._said(change_evidence)):
                return ("error: the user edited this draft in the card themselves. Keep their wording unless their latest "
                        f"message asks for a change (then pass change_evidence). Current subject: {prev.get('email_subject')!r}\n"
                        f"Current body:\n{prev.get('email_body')}")
            self.state["draft"] = {
                "id": uuid.uuid4().hex[:10], "email_subject": subject, "email_body": body,
                "email_template": True, "email_draft": "", "same_for_all": bool(same_for_all),
            }
            self._mark_threads(db)
            card.refresh_preview(records, self.state)
            self.card_shown = True
            n_people = len(self.state["recipients"])
            same_note = (f" Note: this is ONE shared email for {n_people} people. If the user asked for different, separate "
                         "or individual wording for each person, call propose_email again now with per_person "
                         "(one subject and body for each person)." if n_people > 1 else "")
            return "ok: the draft is shown to the user as a card. Ask one short question about it." + same_note
        finally:
            db.close()

    def _mark_threads(self, db: Any) -> None:
        """Remember which recipients get this as a reply in their earlier thread (and that thread's subject)."""
        from sqlalchemy import func, select

        from acquisition.models.prospect import Prospect
        from acquisition.services import outreach_service as svc
        from app.outreach import records as rec

        d = self.state["draft"]
        d["new_thread"] = bool(getattr(self, "_new_thread", False))
        d["threaded"], d["emailed_before"] = {}, []
        for r in self.state["recipients"]:
            key = str(r["email"]).strip().casefold()
            p = db.scalar(select(Prospect).where(func.lower(Prospect.email) == key))
            if p is not None and svc.last_email_to(db, p.id):
                d["emailed_before"].append(key)
                if not d["new_thread"]:
                    d["threaded"][key] = rec.email_state(db, p.id).get("last_subject") or ""

    # ------------------------------------------------------------------ send (queue)
    def _propose_per_person(self, db: Any, records: dict, fields: list, subject: str, body: str, per_person: list) -> str:
        """A different subject and body for some or all recipients. Code still fills names and the assessment link."""
        chosen = {str(r["email"]).casefold() for r in self.state["recipients"]}
        variants: dict[str, dict] = {}
        for item in per_person:
            key = str(item.get("email") or "").strip().casefold()
            subj, text = str(item.get("subject") or "").strip(), str(item.get("body") or "").strip()
            if key not in chosen:
                return f"error: {item.get('email')!r} is not one of the chosen recipients"
            if not subj or not text:
                return f"error: give a subject and a body for {item.get('email')}"
            variants[key] = {
                "subject": strip_assessment_link(tokenize(subj, fields)),
                "body": keep_assessment_link(tokenize(text, fields)),
            }
        shared_ok = bool((subject or "").strip() and (body or "").strip())
        missing = chosen - set(variants)
        if missing and not shared_ok:
            return ("error: give a subject and body for everyone, or a shared subject and body for the people "
                    "not listed. Missing: " + ", ".join(sorted(missing)))
        if shared_ok:
            base_subject = strip_assessment_link(tokenize(subject.strip(), fields))
            base_body = keep_assessment_link(tokenize(body.strip(), fields))
        else:
            first = variants[next(iter(variants))]
            base_subject, base_body = first["subject"], first["body"]
        self.state["draft"] = {
            "id": uuid.uuid4().hex[:10], "email_subject": base_subject, "email_body": base_body,
            "email_template": True, "email_draft": "", "variants": variants,
        }
        self._mark_threads(db)
        card.refresh_preview(records, self.state)
        self.card_shown = True
        return (f"ok: draft shown with a different email for {len(variants)} of {len(chosen)} people "
                "(the others get the shared text). Ask whether to send.")

    def send_approved(self, approval_evidence: str = "") -> str:
        recips, draft = self.state["recipients"], self.state.get("draft")
        if not recips:
            return "error: no recipients chosen"
        if not draft:
            return "error: no draft to send. Use propose_email first."
        problem = approval_problem(self.latest, approval_evidence)
        if problem:
            return f"error: not sent - {problem}. Nothing was queued."
        if draft.get("payment"):
            from app.outreach import payment_tools

            db = self.db_factory()
            try:
                why = payment_tools.payment_problem(db, recips[0]["email"], draft.get("email_body") or "")
            finally:
                db.close()
            if why:
                return f"error: not sent - {why}. Nothing was queued."
        from app.outreach import sender
        from app.outreach.records import ensure_prospect
        from acquisition.services import outreach_service as svc

        ready, why = sender.readiness()
        if not ready:
            return f"error: not sent - {why}. Nothing was queued."
        db = self.db_factory()
        try:
            ids = []
            for r in recips:
                p = ensure_prospect(db, r["email"], r.get("name", ""),
                                    (self.state.get("overrides", {}).get(r["email"].lower(), {}) or {}).get("company", ""))
                ids.append(p.id)
            new_thread = bool(draft.get("new_thread"))
            # Typed details (card / chat) are saved on the person so the merge uses them.
            for r, pid in zip(recips, ids):
                typed = (self.state.get("overrides") or {}).get(r["email"].lower(), {})
                if typed:
                    link = svc.ensure_link(db, db.get(svc.Prospect, pid))
                    if "first_name" in typed:
                        link.first_name_override = typed["first_name"] or None
                    if "last_name" in typed:
                        link.last_name_override = typed["last_name"] or None
                    if "company" in typed:
                        link.company_override = typed["company"] or None
            try:
                variants = draft.get("variants") or {}
                if variants:
                    import uuid as _uuid

                    batch_id, rows = _uuid.uuid4(), []
                    for r, pid in zip(recips, ids):
                        v = variants.get(str(r["email"]).casefold())
                        rows += svc.enqueue(
                            db, prospect_ids=[pid], batch_id=batch_id, requested_by=getattr(self.ctx, "user_id", None),
                            subject_template=(v or {}).get("subject") or draft["email_subject"],
                            body_template=(v or {}).get("body") or draft["email_body"],
                            new_thread=new_thread,
                        )
                else:
                    rows = svc.enqueue(
                        db, prospect_ids=ids, subject_template=draft["email_subject"], body_template=draft["email_body"],
                        requested_by=getattr(self.ctx, "user_id", None), new_thread=new_thread,
                    )
            except svc.OutreachError as exc:
                db.rollback()
                return f"error: not sent - {exc}. Nothing was queued."
            db.commit()
            batch = rows[0].batch_id if rows else None
            n, follow = len(rows), sum(1 for x in rows if x.kind == "follow_up")
            today = svc.sent_today(db)
            self.state["last_batch"] = {"id": str(batch), "count": n}
            self.state["recipients"], self.state["overrides"] = [], {}
            self.state.pop("draft", None)
            kind = "follow-up (same thread)" if follow and not new_thread else ("follow-up" if follow else "email")
            minutes = svc.eta_minutes(n, today)
            when = "under a minute" if minutes < 1 else f"about {minutes} min"
            return (f"queued {n} {kind}{'s' if n != 1 else ''} (batch {str(batch)[:8]}); sending one at a time, "
                    f"{when} to finish. Not delivered yet.")
        finally:
            db.close()

    # ------------------------------------------------------------------ send from the saved template
    #: Set to a number to ask for one yes above that many people. None: never ask (the sent card and "stop" are the net).
    TEMPLATE_CONFIRM_ABOVE: Optional[int] = None

    def find_people(self, stage: Optional[list] = None, order: str = "", count: int = 0, emailed: str = "any",
                    inactive_days: int = 0, text: str = "", place: str = "", include_recent: bool = False) -> str:
        """Who matches (stage, newest or oldest N, emailed or not, quiet for N days, a word, a place). Sends nothing."""
        from app.outreach import audience

        db = self.db_factory()
        try:
            res = audience.select_people(db, stage=stage, order=order, count=int(count or 0), emailed=emailed or "any",
                                         inactive_days=int(inactive_days or 0), text=text, place=place,
                                         include_recent=bool(include_recent))
            return audience.describe(res)
        finally:
            db.close()

    def send_from_template(self, people: Optional[list] = None, audience: str = "", stage: Optional[list] = None,
                           order: str = "", count: int = 0, emailed: str = "any", inactive_days: int = 0,
                           text: str = "", place: str = "", include_recent: bool = False,
                           subject: str = "", body: str = "", approval_evidence: str = "") -> str:
        """Queue emails with no card: the saved template, or the wording given in subject/body, for the named
        people or for everyone matching the filters."""
        from sqlalchemy import select

        from acquisition.models.prospect import Prospect
        from acquisition.services import outreach_service as svc
        from acquisition.services import outreach_settings
        from app.outreach import audience as aud
        from app.outreach import confirm, sender
        from app.outreach.records import ensure_prospect

        db = self.db_factory()
        try:
            cfg = outreach_settings.effective(db)
            if not cfg.auto_send_enabled:
                return ("error: sending from a saved template is switched off. Use the normal email flow "
                        "(choose_recipients, propose_email) instead.")
            custom = bool((subject or "").strip() or (body or "").strip())
            if not custom and not cfg.template_ready:
                return ("error: the saved first-email template is empty. Tell the user an admin must fill in the "
                        "first email subject and body on the Integrations page, Sending card.")
            if custom:
                if not (body or "").strip():
                    return "error: give the email body as well as the subject"
                unknown = (self._blanks_in(subject) | self._blanks_in(body)) - set(self.TEMPLATE_BLANKS)
                if unknown:
                    return ("error: unknown blank(s) " + ", ".join("{{" + b + "}}" for b in sorted(unknown))
                            + ". Allowed: " + ", ".join("{{" + b + "}}" for b in self.TEMPLATE_BLANKS))
                if "{{senderName}}" not in body:
                    return ("error: end the email with a sign-off line and then {{senderName}} on its own line "
                            "(the sender's real name is filled in by the system); never type a person's name there")
            if audience and audience not in ("all", "not_emailed_yet"):
                return "error: audience must be all or not_emailed_yet (or use the filters, or name the people)"
            filtered = bool(stage or order or count or inactive_days or text or place or emailed not in ("", "any"))
            if not (people or audience or filtered):
                return "error: name the people, or say who with audience or the filters (stage, order, count, ...)"
            ready, why = sender.readiness()
            if not ready:
                return f"error: not sent - {why}. Nothing was queued."

            # ---- who
            chosen: list[dict] = []
            skipped: list[dict] = []
            if people and not (audience or filtered):
                before = [dict(r) for r in self.state["recipients"]]
                found_text = self.choose_recipients(people, "replace")
                found = [dict(r) for r in self.state["recipients"]]
                self.state["recipients"] = before  # this tool never leaves a card-flow selection behind
                if found_text.startswith("error:"):
                    return found_text
                chosen = found
            else:
                res = aud.select_people(
                    db, stage=stage, order=order, count=int(count or 0),
                    emailed="no" if audience == "not_emailed_yet" else (emailed or "any"),
                    inactive_days=int(inactive_days or 0), text=text, place=place,
                    include_recent=bool(include_recent) or not (audience or filtered))
                if res["error"]:
                    return "error: " + res["error"] + " Nothing was queued."
                skipped = res["skipped"]
                chosen = [{"email": r["email"], "name": r["name"]} for r in res["people"]]
            if not chosen:
                why = ("; ".join(sorted({s["reason"] for s in skipped}))) if skipped else ""
                return "error: nobody matches" + (f" ({len(skipped)} skipped: {why})" if skipped else "") + ". Nothing was queued."

            # ---- no wording given: each person gets the saved email for where they are (code picks, nobody writes)
            plan: dict[str, str] = {}
            if not custom:
                stages = aud.stages_for(db, [r["email"] for r in chosen])
                fit = []
                for r in chosen:
                    st = stages.get(r["email"].lower(), "new")
                    key = "first" if st == "new" else self.STAGE_TEMPLATE.get(st)
                    if key is None:
                        skipped.append({"name": r.get("name") or r["email"], "email": r["email"],
                                        "reason": self.NO_EMAIL_REASON.get(st, f"nothing to send at {st}")})
                        continue
                    plan[r["email"].lower()] = key
                    fit.append(r)
                if not fit:
                    db.rollback()
                    why = "; ".join(sorted({s["reason"] for s in skipped}))
                    return (f"error: nobody here needs an email ({why}). Nothing was queued. Tell the user that; "
                            "do not write an email for them.")
                chosen = fit

            # ---- first email or follow-up for each
            ids: dict[str, Any] = {}
            follow: set[str] = set()
            for r in chosen:
                key = r["email"].lower()
                row = ensure_prospect(db, r["email"], r.get("name", ""))
                ids[key] = row
                if svc.last_email_to(db, row.id) is not None:
                    follow.add(key)
            n_follow = len(follow)
            n_first = len(chosen) - n_follow
            if custom and n_first and not (subject or "").strip():
                db.rollback()
                return "error: give a subject too, some of these people get a first email"

            # ---- one yes for a big send, only when a limit is set
            if self.TEMPLATE_CONFIRM_ABOVE is not None and len(chosen) > self.TEMPLATE_CONFIRM_ABOVE:
                key = confirm.action_key("send_template", sorted(r["email"].lower() for r in chosen))
                parts = [f"{n_first} first email{'s' if n_first != 1 else ''}"] if n_first else []
                if n_follow:
                    parts.append(f"{n_follow} follow-up{'s' if n_follow != 1 else ''}")
                question = f"Send the saved email to {len(chosen)} people ({' and '.join(parts)})?"
                held = confirm.gate(self.state, self.latest, key, approval_evidence, question)
                if held is not None:
                    db.rollback()
                    return held

            # ---- fill and queue
            signer = self._signer_name(db)
            # (subject, body) -> prospect ids; people who get the same text are queued together
            groups: dict[tuple, list] = {}
            if custom:
                first_subject = strip_assessment_link(subject or "")
                stages = aud.stages_for(db, [r["email"] for r in chosen])
                needs_link = any(stages.get(r["email"].lower(), "new") in ("new", "not_started") for r in chosen)
                own_body = self._tidy_email(self._fit_link(body.replace("{{senderName}}", signer), needs_link))
                for r in chosen:
                    groups.setdefault((first_subject, own_body), []).append(ids[r["email"].lower()].id)
            else:
                first_subject = strip_assessment_link(cfg.first_subject)
                first_text = self._wrap_email(keep_assessment_link(cfg.first_body.replace("{{senderName}}", signer)))
                missing = {r["email"].lower(): r.get("missing") for r in self._in_progress_rows(db, chosen, plan)}
                for r in chosen:
                    key_email = r["email"].lower()
                    which = plan[key_email]
                    if which == "first":
                        text_for = first_text
                    else:
                        text_for = cfg.stage_bodies[which].replace("{{senderName}}", signer)
                        if "{{missingTopics}}" in text_for:
                            text_for = text_for.replace("{{missingTopics}}", self._missing_phrase(missing.get(key_email)))
                        text_for = self._tidy_email(text_for)
                    groups.setdefault((first_subject, text_for), []).append(ids[key_email].id)
            import uuid as _uuid

            batch_id = _uuid.uuid4()
            requested_by = getattr(self.ctx, "user_id", None)
            rows = []
            try:
                for (subj, text_for), pids in groups.items():
                    rows += svc.enqueue(db, prospect_ids=pids, batch_id=batch_id, requested_by=requested_by,
                                        subject_template=subj, body_template=text_for)
            except svc.OutreachError as exc:
                db.rollback()
                return f"error: not sent - {exc}. Nothing was queued."
            db.commit()
            today = svc.sent_today(db)
            minutes = svc.eta_minutes(len(rows), today)
            names = {str(r["email"]).lower(): (r.get("name") or "") for r in chosen}
            table = [{"name": names.get(str(x.to_email).lower()) or str(x.to_email).split("@")[0],
                      "email": x.to_email, "kind": "follow_up" if x.kind == svc.KIND_FOLLOW_UP else "first"}
                     for x in rows]
            first_n = sum(1 for t in table if t["kind"] == "first")
            follow_n = sum(1 for t in table if t["kind"] == "follow_up")
            old = self.sent_view  # an earlier send call in this same turn: show one card for all of them
            if old:
                seen = {s["email"] for s in old["skipped"]}
                self.sent_view = {
                    "count": old["count"] + len(rows), "first": old["first"] + first_n,
                    "follow_up": old["follow_up"] + follow_n, "minutes": old["minutes"] + minutes,
                    "rows": old["rows"] + table,
                    "skipped": old["skipped"] + [s for s in skipped if s["email"] not in seen],
                }
            else:
                self.sent_view = {"count": len(rows), "first": first_n, "follow_up": follow_n,
                                  "minutes": minutes, "rows": table, "skipped": skipped}
            self.state["last_batch"] = {"id": str(batch_id), "count": len(rows)}
            when = "under a minute" if minutes < 1 else f"about {minutes} min"
            extra = f" {len(skipped)} were skipped ({'; '.join(sorted({s['reason'] for s in skipped}))})." if skipped else ""
            return (f"queued {len(rows)} email{'s' if len(rows) != 1 else ''} "
                    f"({'your wording' if custom else 'saved emails by stage'}; {first_n} first, "
                    f"{follow_n} follow-up); sending one at a time, "
                    f"{when} to finish. Not delivered yet.{extra} The user already sees a table of who was emailed, so "
                    "do NOT list the people. Reply with one short sentence, and say they are queued, not delivered.")
        finally:
            db.close()

    # ------------------------------------------------------------------ the saved template (see / change)
    TEMPLATE_BLANKS = ("firstName", "lastName", "companyName", "assessment_url", "senderName")
    #: Blanks the code fills itself while sending; allowed only inside the stage templates.
    STAGE_ONLY_BLANKS = ("missingTopics",)
    #: Where a person is -> which saved email they get. "new" gets the first email.
    STAGE_TEMPLATE = {"not_started": "not_started", "in_progress": "in_progress", "qualified": "qualified",
                      "exploring": "qualified", "unqualified": "unqualified"}
    NO_EMAIL_REASON = {"booked": "already booked", "purchased": "already a customer",
                       "completed": "finished, result not ready yet"}
    #: assessment answers -> plain words for {{missingTopics}}, in the order they are asked
    _TOPIC_WORDS = (
        (("company",), "your company"), (("goals",), "your goals"),
        (("revenue_range", "headcount"), "your company size"),
        (("functions", "tools", "workflows"), "your team, tools and workflows"),
        (("knowledge_locations",), "where your information lives"),
        (("pain_points", "time_cost_impact", "bottlenecks"), "where your time goes"),
        (("change_readiness", "ai_readiness"), "how ready you are for AI"), (("authority",), "your role"),
    )

    @classmethod
    def _missing_phrase(cls, missing: Optional[list]) -> str:
        """The unanswered assessment topics in plain words, at most three: 'your goals and your role'."""
        gone = set(missing or [])
        words = [w for keys, w in cls._TOPIC_WORDS if gone & set(keys)][:3]
        if not words:
            return "a few last answers"
        return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]

    @staticmethod
    def _in_progress_rows(db: Any, chosen: list, plan: dict) -> list:
        """The unanswered topics for each chosen person who is mid-assessment."""
        from app.outreach import audience as aud

        want = {r["email"].lower() for r in chosen if plan.get(r["email"].lower()) == "in_progress"}
        if not want:
            return []
        res = aud.select_people(db, stage=["in_progress"], include_recent=True)
        return [r for r in res["people"] if r["email"].lower() in want]

    @staticmethod
    def _blanks_in(text: str) -> set[str]:
        return {m.group(1) for m in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", text or "")}

    @staticmethod
    def _template_text(cfg: Any) -> str:
        from acquisition.services.outreach_settings import STAGE_KEYS, STAGE_LABELS

        text = f"First email subject:\n{cfg.first_subject or '(empty)'}\n\nFirst email body (people never emailed):\n{cfg.first_body or '(empty)'}"
        for k in STAGE_KEYS:
            text += f"\n\nStage '{k}' ({STAGE_LABELS[k]}):\n{cfg.stage_bodies[k]}"
        return text

    def _set_template_view(self, cfg: Any, saved: bool = False) -> None:
        from acquisition.services.outreach_settings import STAGE_KEYS, STAGE_LABELS

        self.template_view = {"subject": cfg.first_subject or "", "body": cfg.first_body or "",
                              "stages": [{"stage": k, "label": STAGE_LABELS[k], "body": cfg.stage_bodies[k]}
                                         for k in STAGE_KEYS],
                              "sending_on": bool(cfg.auto_send_enabled), "saved": saved}

    def show_email_template(self) -> str:
        from acquisition.services import outreach_settings

        db = self.db_factory()
        try:
            cfg = outreach_settings.effective(db)
            state = "ON (no card, the chat sends it)" if cfg.auto_send_enabled else "OFF (emails are shown on a card first)"
            self._set_template_view(cfg)
            return (f"Template sending is {state}.\n\n{self._template_text(cfg)}\n\n"
                    "The user already sees this as a card. Reply with one short sentence; do NOT repeat the template.")
        finally:
            db.close()

    def update_email_template(self, first_subject: Optional[str] = None, first_body: Optional[str] = None,
                              stage: Optional[str] = None, stage_body: Optional[str] = None,
                              change_evidence: str = "", remove_placeholders: Optional[list] = None) -> str:
        """Change the saved first email, or the saved email of one stage. Every {{blank}} stays unless the user asked to remove it."""
        from acquisition.services import outreach_settings

        if stage is not None and stage not in outreach_settings.STAGE_KEYS:
            return "error: stage must be one of " + ", ".join(outreach_settings.STAGE_KEYS)
        if (stage is None) != (stage_body is None):
            return "error: give stage and stage_body together"
        if first_subject is None and first_body is None and stage_body is None:
            return "error: give the new first_subject, first_body, or a stage with its stage_body"
        if not (change_evidence and self._said(change_evidence)):
            return "error: pass change_evidence, the user's own words asking for this change, copied from their latest message"
        allowed_gone = {str(x).strip().strip("{} ") for x in (remove_placeholders or []) if str(x).strip()}
        db = self.db_factory()
        try:
            cfg = outreach_settings.effective(db)
            changes = {"first_subject": (first_subject, cfg.first_subject), "first_body": (first_body, cfg.first_body),
                       f"stage {stage}": (stage_body, cfg.stage_bodies.get(stage or "", ""))}
            for field, (new, old) in changes.items():
                if new is None:
                    continue
                if not new.strip():
                    return f"error: {field} cannot be empty"
                ok_blanks = set(self.TEMPLATE_BLANKS) | (set(self.STAGE_ONLY_BLANKS) if field.startswith("stage ") else set())
                unknown = self._blanks_in(new) - ok_blanks
                if unknown:
                    return ("error: unknown blank(s) " + ", ".join("{{" + b + "}}" for b in sorted(unknown))
                            + ". Allowed: " + ", ".join("{{" + b + "}}" for b in sorted(ok_blanks)))
                dropped = (self._blanks_in(old) - self._blanks_in(new)) - allowed_gone
                if dropped:
                    return (f"error: the new {field} dropped " + ", ".join("{{" + b + "}}" for b in sorted(dropped))
                            + ". Keep every blank exactly as it was; only when the user clearly asked to remove one, "
                            "pass it in remove_placeholders. Current text:\n" + (old or ""))
            store = outreach_settings
            store.save(
                db, sender_user_id=cfg.sender_user_id, business_address=cfg.business_address,
                optout_line=cfg.optout_line, worker_enabled=cfg.worker_enabled, updated_by="chat",
                first_subject=first_subject, first_body=first_body,
                stage_bodies={stage: stage_body} if stage else None,
            )
            after = outreach_settings.effective(db)
            self._set_template_view(after, saved=True)
            return ("saved. This is the template now:\n\n" + self._template_text(after)
                    + "\n\nThe user already sees the new version as a card. Reply with one short sentence; "
                    "do NOT repeat the template.")
        finally:
            db.close()

    _GREETING = re.compile(r"^(hi|hello|dear|hey)\b.*[,:]$", re.I)
    _SIGN_OFF = re.compile(r"^(best|best regards|regards|kind regards|warm regards|thanks|thank you|sincerely|cheers)[,!.]?$", re.I)

    @classmethod
    def _tidy_email(cls, text: str) -> str:
        """Aligned layout: one blank line between paragraphs, the link alone on its line, the sign-off as its own block."""
        t = re.sub(r"[ \t]*(\{\{assessment_url\}\})[ \t]*", r"\n\n\1\n\n", (text or "").replace("\r\n", "\n"))
        out: list[str] = []
        for raw in t.split("\n"):
            line = raw.strip()
            if not line:
                if out and out[-1] != "":
                    out.append("")
                continue
            if cls._SIGN_OFF.match(line) and out and out[-1] != "":
                out.append("")  # the sign-off starts its own block
            elif out and out[-1] != "" and cls._GREETING.match(out[-1]) and len(out) == 1:
                out.append("")  # a blank line after the greeting
            out.append(line)
        while out and out[-1] == "":
            out.pop()
        return cls._wrap_email("\n".join(out))

    @staticmethod
    def _wrap_email(text: str, width: int = 72) -> str:
        """Plain-text emails: wrap long lines at about 72 characters. The link line and short lines stay as they are."""
        import textwrap

        lines = []
        for line in (text or "").split("\n"):
            if len(line) <= width or "{{assessment_url}}" in line or "://" in line:
                lines.append(line)
            else:
                lines.append(textwrap.fill(line, width=width, break_long_words=False, break_on_hyphens=False))
        return "\n".join(lines)

    @classmethod
    def _fit_link(cls, body: str, needed: bool) -> str:
        """Keep the wording as written. Add the assessment link (before the sign-off) only when it is needed and missing."""
        if re.search(r"\{\{\s*assessment_url\s*\}\}", body or "") or not needed:
            return body
        lines = (body or "").rstrip().split("\n")
        for i in range(len(lines) - 1, -1, -1):
            if cls._SIGN_OFF.match(lines[i].strip()):
                return "\n".join(lines[:i] + ["{{assessment_url}}", ""] + lines[i:])
        return "\n".join(lines + ["", "{{assessment_url}}"])

    def _signer_name(self, db: Any) -> str:
        """The signed-in user's display name (or the local part of their email), for the {{senderName}} blank."""
        from bizos.control import store as control
        from bizos.control.store import UserNotFound

        user_id = getattr(self.ctx, "user_id", None)
        if not user_id:
            return ""
        try:
            user = control.get_user(user_id)
        except UserNotFound:
            return ""
        name = (user.display_name or "").strip()
        if name:
            return clean_field(name)
        email = (user.email or "").strip()
        return clean_field(email.split("@", 1)[0]) if email else ""

    def cancel_sending(self) -> str:
        from acquisition.services import outreach_service as svc

        db = self.db_factory()
        try:
            n = svc.cancel_all_queued(db)
            db.commit()
            return f"stopped: {n} queued email(s) will not be sent. Emails already sent are not affected."
        finally:
            db.close()
