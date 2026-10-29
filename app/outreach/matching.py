"""Typo-tolerant matching for names, emails and companies ("shubhum" finds "Shubham").

Pure Python on top of ordinary SQL, so it needs no database extension. Two parts:

* ``score`` rates how well a query matches one candidate text (0 to 1).
* ``find_ids`` asks the database for just the id and the few text columns, ranks them here, and returns the
  best matches. Up to ``SCAN_LIMIT`` rows are ranked in full; beyond that the database first narrows the rows
  to those sharing a three-letter fragment with the query, so the cost stays bounded as the list grows.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

#: below this a candidate is not a match
CUTOFF = 0.78
#: rows ranked in full before the database narrows them first
SCAN_LIMIT = 50_000

_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: object) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(_WORD.findall(raw.casefold()))


def _words(text: str) -> list[str]:
    return text.split()


def _best_word(token: str, words: Sequence[str]) -> float:
    best = 0.0
    for w in words:
        if token == w:
            return 1.0
        if len(token) >= 3 and w.startswith(token):
            best = max(best, 0.95)
        elif len(token) >= 4:
            best = max(best, SequenceMatcher(None, token, w).ratio())
    return best


def score(query: str, candidate: str) -> float:
    """0..1: 1 is an exact match, about 0.8 and up survives a typo or two."""
    q, c = normalize(query), normalize(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if len(q) >= 3 and q in c:
        return 0.96
    q_words, c_words = _words(q), _words(c)
    per_word = [_best_word(t, c_words) for t in q_words]
    word_score = sum(per_word) / len(per_word)
    if len(q) < 4:
        return word_score
    return max(word_score, SequenceMatcher(None, q, c).ratio())


def best_score(query: str, texts: Iterable[object]) -> float:
    return max((score(query, str(t)) for t in texts if t), default=0.0)


def find_ids(
    db: Session,
    query: str,
    id_column: Any,
    text_columns: Sequence[Any],
    *,
    where: Sequence[Any] = (),
    limit: int = 50,
    cutoff: float = CUTOFF,
) -> list[tuple[Any, float]]:
    """The ids whose text columns best match ``query``, best first.

    ``where`` holds conditions that must hold for a row to be considered (for example a filter on status).
    """
    q = normalize(query)
    if not q:
        return []
    base = select(id_column, *text_columns)
    sized = select(id_column)
    if where:
        base, sized = base.where(*where), sized.where(*where)
    total = int(db.scalar(select(func.count()).select_from(sized.subquery())) or 0)
    if total > SCAN_LIMIT:
        probes = {t[:3] for t in _words(q) if len(t) >= 3} or {q[:3]}
        likes = [func.lower(col).like(f"%{p}%") for p in probes for col in text_columns]
        base = base.where(or_(*likes))
    ranked: list[tuple[Any, float]] = []
    for row in db.execute(base.execution_options(yield_per=2000)):
        s = best_score(q, row[1:])
        if s >= cutoff:
            ranked.append((row[0], s))
    ranked.sort(key=lambda item: -item[1])
    return ranked[:limit]
