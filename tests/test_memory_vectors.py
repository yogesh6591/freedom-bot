"""Meaning-based memory search adds hits; it never shows anything keyword search would hide.

The embedder is fake (no API call): a few concepts map to fixed directions, so "money back" lands next to
"refund" without sharing a word with it.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import text

from freedombot.memory import store as memory
from freedombot.memory import vectors
from freedombot.tenancy.context import tenant_scope
from freedombot.tenancy.registry import workspace_connection
from freedombot.types import MemoryCategory

CONCEPTS = [("refund", "money back", "returns"), ("salary", "pay band", "compensation"), ("dock", "warehouse")]


def fake_embed(texts):
    out = []
    for t in texts:
        low = t.casefold()
        v = [0.0] * vectors.DIMENSIONS
        for dim, words in enumerate(CONCEPTS):
            if any(w in low for w in words):
                v[dim] = 1.0
        v[len(CONCEPTS)] = 0.2  # a little shared "everything" direction
        n = math.sqrt(sum(x * x for x in v))
        out.append([x / n for x in v])
    return out


@pytest.fixture
def vector_search(monkeypatch, client_a, ctx_factory):
    monkeypatch.setattr(vectors, "enabled", lambda: True)
    monkeypatch.setattr(vectors, "embed", fake_embed)
    vectors.reset_cache()
    admin = ctx_factory(client_a, "admin")
    if not vectors.available(admin):
        pytest.skip("this test workspace cannot use pgvector")
    yield admin
    vectors.reset_cache()


def _put(ctx, client, key, title, content, area=None):
    return memory.put(category=MemoryCategory.FACT, memory_key=key, title=title, content=content,
                      access_area=area, settings=client.settings, ctx=ctx)


def test_a_question_with_no_shared_words_finds_the_fact(vector_search, client_a, monkeypatch):
    admin = vector_search
    with tenant_scope(admin):
        _put(admin, client_a, "vec_returns_window", "Returns window", "Refunds accepted within 30 days.")
        hits = memory.search("Can a client get their money back?", ctx=admin)
        assert "vec_returns_window" in [h.memory_key for h in hits]

        monkeypatch.setattr(vectors, "enabled", lambda: False)
        vectors.reset_cache()
        keyword_only = memory.search("Can a client get their money back?", ctx=admin)
        assert "vec_returns_window" not in [h.memory_key for h in keyword_only]


def test_restricted_items_are_never_embedded_or_shown(vector_search, client_a, ctx_factory):
    admin = vector_search
    viewer = ctx_factory(client_a, "viewer")
    with tenant_scope(admin):
        secret = _put(admin, client_a, "vec_pay_bands", "Pay bands", "Salary bands: Senior $150k.", area="salary")
        memory.search("what is the compensation for seniors", ctx=admin)
        with workspace_connection(admin) as conn:
            embedded = conn.execute(text("SELECT count(*) FROM memory_embeddings WHERE item_id = :i"),
                                    {"i": secret.id}).scalar()
        assert embedded == 0
    with tenant_scope(viewer):
        hits = memory.search("what is the compensation for seniors", ctx=viewer)
        assert "vec_pay_bands" not in [h.memory_key for h in hits]


def test_regulated_or_card_text_stays_keyword_only():
    assert vectors.embeddable_text("Patient", "Jordan Lee (DOB 1984-03-12) patient record",
                                   access_area=None, classification="INTERNAL") is None
    assert vectors.embeddable_text("Card", "Card on file 4111 1111 1111 1111",
                                   access_area=None, classification="INTERNAL") is None
    assert vectors.embeddable_text("Policy", "Board only [[area:exec]]hiring freeze[[/area]] all hands Friday",
                                   access_area=None, classification="INTERNAL") == "Policy\nBoard only   all hands Friday"
    assert vectors.embeddable_text("Doc", "Confidential terms", access_area=None, classification="CONFIDENTIAL") is None
