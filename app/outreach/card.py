"""The email card: what the UI shows for a draft, and how the user's edits in the card are saved.

The view has the same shape as before (see ui/src/lib/types.ts EmailView) so the card UI does not change.
There is no campaign any more, so the ``audience`` block is always "nothing special": no scope question.
"""

from __future__ import annotations

from typing import Any

from app.outreach.text import (
    TOKEN_COMPANY,
    TOKEN_FIRST,
    clean_field,
    literal_greeting_names,
    normalize_blanks,
    person_fields,
    render,
    tokenize,
)


def _link_for(records: dict, email: str) -> str | None:
    key = (email or "").casefold()
    for p in records.get("people") or []:
        if str(p.get("email") or "").casefold() == key and p.get("assessment_url"):
            return p["assessment_url"]
    return None


def draft_view(records: dict, state: dict) -> dict:
    """Everything the card needs. Built from saved state, never from the model."""
    d = state.get("draft") or {}
    if not d:
        return {}
    fields = [person_fields(records, state, r) for r in state.get("recipients") or []]
    subject, body = d.get("email_subject") or "", d.get("email_body") or ""
    variants = d.get("variants") or {}

    def _text(f: dict) -> tuple[str, str]:
        v = variants.get(str(f["email"]).casefold())
        return (v["subject"], v["body"]) if v else (subject, body)

    threaded = d.get("threaded") or {}

    def _subject(f: dict) -> str:
        earlier = threaded.get(str(f["email"]).casefold())
        if earlier:  # a reply in the earlier thread carries that thread's subject
            return earlier if earlier.lower().startswith("re:") else "Re: " + earlier
        return render(_text(f)[0], f, _link_for(records, f["email"]))

    before = set(d.get("emailed_before") or [])

    def _mode(f: dict) -> str:
        key = str(f["email"]).casefold()
        if key not in before:
            return "First email"
        return "New email (new thread)" if d.get("new_thread") else "Follow-up (same thread)"

    previews = [
        {
            "email": f["email"],
            "mode": _mode(f),
            "subject": _subject(f),
            "body": render(_text(f)[1], f, _link_for(records, f["email"])),
        }
        for f in fields
    ]
    warnings: list[str] = []
    all_text = subject + body + "".join(v["subject"] + v["body"] for v in variants.values())
    for f in fields:
        if not f["first_name"] and TOKEN_FIRST in all_text:
            warnings.append(
                f'{f["email"]}: no first name on file. The email will say "Hi there,". Type a name in Details if you know it.'
            )
    for name in literal_greeting_names(body if not variants else ""):
        warnings.append(
            f'The greeting says "{name}" as plain text, so everyone who gets this email sees exactly that. '
            "Put a person's name in Details instead."
        )
    if TOKEN_COMPANY in all_text:
        for f in fields:
            if not f["company"]:
                warnings.append(f'{f["email"]}: no company on file, so the company part of the email will be empty.')
    label = lambda f: " ".join(x for x in (f["first_name"], f["last_name"]) if x) or f["email"]  # noqa: E731
    return {
        "draft_id": d.get("id") or "",
        "template": {"subject": subject, "body": body},
        "assessment_url": (records.get("assessment_url") or "").strip(),
        "people": fields,
        "previews": previews,
        "warnings": warnings,
        "audience": {
            "campaign": "",
            "campaign_is_new": False,
            "will_send_now": [label(f) for f in fields],
            "also_waiting": [],
            "template_changed": False,
            "changed_parts": [],
            "first_difference": "",
            "scope": "",
            "scope_needed": False,
        },
        # Extra (the card ignores it): "first" or "follow_up" emails, so the chat can say which.
        "kind": state.get("kind") or "first",
    }


def refresh_preview(records: dict, state: dict) -> None:
    """Keep the plain-text preview (the first recipient's view) in step with the template."""
    d = state.get("draft") or {}
    fields = [person_fields(records, state, r) for r in state.get("recipients") or []]
    if not d or not fields:
        return
    link = _link_for(records, fields[0]["email"])
    v = (d.get("variants") or {}).get(str(fields[0]["email"]).casefold())
    subj, text = (v["subject"], v["body"]) if v else (d["email_subject"], d["email_body"])
    d["email_draft"] = f"Subject: {render(subj, fields[0], link)}\n\n{render(text, fields[0], link)}"


def edit_draft(
    ctx: Any,
    meta: dict,
    *,
    subject: str | None = None,
    body: str | None = None,
    people: dict | None = None,
    scope: str | None = None,  # kept so /email-card keeps working; there is no scope any more
    rendered: bool = False,
    records: dict | None = None,
) -> dict:
    """Apply what the user changed in the email card to the saved state of the latest turn.

    ``subject``/``body`` are the TEMPLATE (blanks visible). ``people`` maps an email to the details the user
    typed for that person only. Returns the new email view.
    """
    state = meta.get("state")
    if not isinstance(state, dict) or not state.get("draft"):
        raise ValueError("There is no email draft to edit.")
    if records is None:
        from acquisition.db.session import SessionLocal
        from app.outreach.records import people_records

        db = SessionLocal()
        try:
            records = people_records(db, [r["email"] for r in state.get("recipients") or []])
        finally:
            db.close()
    d = state["draft"]
    if (subject is not None or body is not None) and d.get("variants"):
        raise ValueError(
            "This draft has a different email for each person, so it cannot be edited as one. "
            "Ask in the chat to change it (for one person or for everyone)."
        )
    if subject is not None or body is not None:
        new_subject = (subject if subject is not None else d.get("email_subject", "")).strip()
        new_body = (body if body is not None else d.get("email_body", "")).strip()
        if not new_subject or not new_body:
            raise ValueError("The subject and the body cannot be empty.")
        new_subject, new_body = normalize_blanks(new_subject), normalize_blanks(new_body)
        if rendered:
            fields = [person_fields(records, state, r) for r in state.get("recipients") or []]
            new_subject, new_body = tokenize(new_subject, fields), tokenize(new_body, fields)
        if (new_subject, new_body) != (d.get("email_subject"), d.get("email_body")) or not d.get("email_template"):
            d["email_subject"], d["email_body"], d["email_template"] = new_subject, new_body, True
            d["user_edited"] = True  # the model must not overwrite this by regenerating
    recipients = {str(r["email"]).casefold(): r for r in state.get("recipients") or []}
    for email, values in (people or {}).items():
        key = str(email).casefold()
        if key not in recipients:
            raise ValueError(f"{email} is not one of the recipients.")
        base_state = {**state, "overrides": {k: v for k, v in (state.get("overrides") or {}).items() if k != key}}
        base = person_fields(records, base_state, recipients[key])
        typed = (state.setdefault("overrides", {})).setdefault(key, {})
        for field in ("first_name", "last_name", "company"):
            if field not in (values or {}):
                continue
            value = clean_field(values[field])
            if value == base[field]:
                typed.pop(field, None)
            else:
                typed[field] = value
        if not typed:
            state["overrides"].pop(key, None)
    refresh_preview(records, state)
    view = draft_view(records, state)
    payload = meta.get("json") if isinstance(meta.get("json"), dict) else {}
    payload.update(
        {
            "email_subject": d["email_subject"],
            "email_body": d["email_body"],
            "email_draft": d.get("email_draft") or "",
            "email_view": view,
        }
    )
    meta["json"], meta["state"] = payload, state
    return view
