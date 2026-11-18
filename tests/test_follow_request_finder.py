"""Follow-request prospecting switch: offline behaviour in finder search/classify."""

from __future__ import annotations

from unittest.mock import patch

from acquisition.services.prospecting_rules_service import default_prospecting_rules, parse_and_validate
from app.outreach import finder_tools as ft


def _rules(**kwargs):
    rules = default_prospecting_rules()
    for key, value in kwargs.items():
        setattr(rules, key, value)
    return rules


def test_parse_missing_follow_request_defaults_false():
    payload = default_prospecting_rules().to_payload()
    payload.pop("follow_request", None)
    rules = parse_and_validate(payload)
    assert rules.follow_request is False


def test_labelled_switch_off_uses_founder_led_query():
    pairs = ft._labelled(_rules(follow_request=False), "neurosurgery", "", relax=True)
    assert len(pairs) == 1
    assert "founder-led neurosurgery firm" in pairs[0][1]


def test_labelled_switch_on_drops_founder_led():
    pairs = ft._labelled(_rules(follow_request=True), "neurosurgery", "", relax=True)
    assert len(pairs) == 1
    assert "founder-led" not in pairs[0][1]
    assert pairs[0][1].startswith("neurosurgery firm or practice,")


def test_labelled_switch_on_empty_focus_unchanged():
    off = ft._labelled(_rules(follow_request=False), "", "", relax=True)
    on = ft._labelled(_rules(follow_request=True), "", "", relax=True)
    assert off == on


def test_classify_switch_on_prompt_skips_founder_led_and_exclusions(monkeypatch):
    prompts: list[str] = []

    def _fake(prompt: str) -> str:
        prompts.append(prompt)
        return '[{"i": 0, "category": "Other", "business_type": "clinic", "matches": true}]'

    monkeypatch.setattr(ft, "_small_model", _fake)
    ft._classify(_rules(follow_request=True), "law firms", [{"title": "Acme Legal", "summary": "a midsize law firm"}])
    assert len(prompts) == 1
    body = prompts[0].casefold()
    assert "founder-led" not in body
    assert "excluded" not in body
    assert "organisation the user asked for: law firms" in prompts[0]


def test_classify_switch_off_keeps_founder_led(monkeypatch):
    prompts: list[str] = []

    def _fake(prompt: str) -> str:
        prompts.append(prompt)
        return "[]"

    monkeypatch.setattr(ft, "_small_model", _fake)
    ft._classify(_rules(follow_request=False), "law firms", [{"title": "Acme", "summary": "law"}])
    assert "founder-led" in prompts[0]


@patch.object(ft, "_exa")
def test_companies_switch_on_skips_exclusion_filter(mock_exa):
    mock_exa.return_value = [
        {"url": "https://example.com", "title": "Joe's Restaurant", "summary": "restaurants in town"}
    ]
    out = ft._companies("key", _rules(follow_request=True), "restaurants", relax=True)
    assert len(out) == 1


@patch.object(ft, "_exa")
def test_companies_switch_off_filters_excluded_kind(mock_exa):
    mock_exa.return_value = [
        {"url": "https://example.com", "title": "Joe's Restaurant", "summary": "restaurants in town"}
    ]
    out = ft._companies("key", _rules(follow_request=False), "restaurants", relax=True)
    assert len(out) == 0


def test_follow_request_to_payload_roundtrip():
    rules = parse_and_validate({**default_prospecting_rules().to_payload(), "follow_request": True})
    assert rules.to_payload().get("follow_request") is True
    assert rules.to_dict().get("follow_request") is True


def test_icp_category_for_save_uses_focus_title_case():
    rules = _rules(follow_request=True)
    assert ft._icp_category_for_save(rules, "famous neurosurgeons", "Other") == "Famous Neurosurgeons"
    assert ft._icp_category_for_save(rules, "neurosurgeons", "Consultants and advisory firms") == "Neurosurgeons"


def test_icp_category_for_save_off_or_empty_focus_unchanged():
    rules = _rules(follow_request=True)
    assert ft._icp_category_for_save(rules, "", "Agencies") == "Agencies"
    assert ft._icp_category_for_save(_rules(follow_request=False), "neurosurgeons", "Other") == "Other"


@patch.object(ft, "_json_in", return_value=[])
@patch.object(ft, "_web_ask")
def test_web_firms_follow_request_prompt(mock_ask, _mock_json):
    mock_ask.return_value = "[]"
    ft._web_firms(_rules(follow_request=True), "neurosurgeons", "", None, None, set(), relax=True)
    prompt = mock_ask.call_args[0][0]
    assert "real businesses or organisations" in prompt
    assert "owner-led" not in prompt
    assert "practice manager" in prompt
    assert "best contact person" in prompt


@patch.object(ft, "_json_in", return_value=[])
@patch.object(ft, "_web_ask")
def test_web_firms_default_prompt_unchanged(mock_ask, _mock_json):
    mock_ask.return_value = "[]"
    ft._web_firms(_rules(follow_request=False), "neurosurgeons", "", None, None, set(), relax=True)
    prompt = mock_ask.call_args[0][0]
    assert "real, owner-led firms" in prompt
    assert "its current founder, owner or CEO" in prompt
    assert "Never include these kinds of business" in prompt