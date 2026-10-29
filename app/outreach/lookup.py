"""Find the person a user means, tolerating typos ("shubhum" finds "Shubham").

``find`` returns the best matches; ``one`` and ``many`` turn a typed name or address into exactly the people
meant, or raise a ``ToolError`` that says what to ask the user (no match, or which of several).
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acquisition.models.prospect import Prospect
from app.outreach import matching
from app.outreach.toolkit import Call, ToolError

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: the best match is used without asking when it is the only one, or beats the runner-up by this much
MARGIN = 0.06
MAX_CHOICES = 6


def label(p: Prospect) -> str:
    """How a person is written in replies: ``Name <email> (Company)``."""
    company = f" ({p.company})" if (p.company or "").strip() else ""
    return f"{p.executive_name} <{p.email}>{company}"


def by_email(db: Session, email: str) -> Optional[Prospect]:
    return db.scalar(select(Prospect).where(func.lower(Prospect.email) == (email or "").strip().lower()))


def find(db: Session, text: str, limit: int = MAX_CHOICES) -> list[tuple[Prospect, float]]:
    """People matching ``text`` by name, email or company, best first, each with its score."""
    query = (text or "").strip()
    if not query:
        return []
    if _EMAIL.match(query):
        exact = by_email(db, query)
        if exact is not None:
            return [(exact, 1.0)]
    ranked = matching.find_ids(db, query, Prospect.id, [Prospect.executive_name, Prospect.email, Prospect.company],
                               limit=limit)
    if not ranked:
        return []
    people = {p.id: p for p in db.scalars(select(Prospect).where(Prospect.id.in_([pid for pid, _ in ranked])))}
    return [(people[pid], score) for pid, score in ranked if pid in people]


def one(db: Session, text: str) -> Prospect:
    """Exactly one person, or a ToolError saying who to ask about."""
    found = find(db, text)
    if not found:
        raise ToolError(f"no saved prospect matches {text!r}. Ask the user to check the spelling or give the email.")
    top_p, top = found[0]
    if len(found) == 1 or top - found[1][1] >= MARGIN:
        return top_p
    options = "; ".join(label(p) for p, _ in found)
    raise ToolError(f"several people match {text!r}: {options}. Ask the user which one (or give the email).")


def many(db: Session, names: list[str], limit: int) -> list[Prospect]:
    """One person per name. Stops with all the problems listed, so the user can fix them in one go."""
    names = [str(n).strip() for n in names or [] if str(n).strip()]
    if not names:
        raise ToolError("no names given.")
    if len(names) > limit:
        raise ToolError(f"{len(names)} people is too many for one step (max {limit}). Ask the user to split the list.")
    people: dict = {}
    problems: list[str] = []
    for text in names:
        try:
            p = one(db, text)
        except ToolError as exc:
            problems.append(str(exc))
            continue
        people[p.id] = p
    if problems:
        raise ToolError("nothing was changed. " + " | ".join(problems))
    return list(people.values())


def person(call: Call, who: str = "") -> Prospect:
    """The person a tool call is about: the one named, else the single person chosen for the email in this chat."""
    if (who or "").strip():
        return one(call.db, who)
    chosen = call.state.get("recipients") or []
    if len(chosen) == 1:
        found = by_email(call.db, str(chosen[0]["email"]))
        if found is not None:
            return found
        raise ToolError(f"{chosen[0]['email']} is not saved yet.")
    if len(chosen) > 1:
        names = "; ".join(str(r.get("name") or r["email"]) for r in chosen)
        raise ToolError(f"several people are chosen ({names}). Ask the user which one.")
    raise ToolError("nobody is named or chosen yet. Ask the user who.")
