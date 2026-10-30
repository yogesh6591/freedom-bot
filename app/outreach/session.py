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
from app.outreach.approval import CHANGE_VERB, WHOLE_EMAIL, approval_problem, user_said
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
            firsts = sum(1 for ln in lines if "first email" in ln or "new address" in ln)
            follow = sum(1 for ln in lines if "FOLLOW-UP" in ln)
            if firsts and follow:
                notes.append("These are mixed: some were emailed before, some not. One email cannot be both. "
                             "Tell the user to send the first emails and the follow-ups separately, and keep one group.")
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
                if not re.search(r"\b(subject|title|headline)\b", self.latest, re.I) and prev.get("email_subject") != subject:
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
            ids, kinds = [], set()
            for r in recips:
                p = ensure_prospect(db, r["email"], r.get("name", ""),
                                    (self.state.get("overrides", {}).get(r["email"].lower(), {}) or {}).get("company", ""))
                ids.append(p.id)
                kinds.add(bool(svc.last_email_to(db, p.id)))
            new_thread = bool(draft.get("new_thread"))
            if len(kinds) > 1 and not new_thread:
                db.rollback()
                return ("error: some of these people were emailed before and some not. Send first emails and "
                        "follow-ups separately. Nothing was queued.")
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

    def cancel_sending(self) -> str:
        from acquisition.services import outreach_service as svc

        db = self.db_factory()
        try:
            n = svc.cancel_all_queued(db)
            db.commit()
            return f"stopped: {n} queued email(s) will not be sent. Emails already sent are not affected."
        finally:
            db.close()
