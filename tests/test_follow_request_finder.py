"""Follow-request prospecting switch: offline behaviour in finder search/classify."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from acquisition.services.prospecting_rules_service import default_prospecting_rules, parse_and_validate
from app.outreach import finder_tools as ft
from app.outreach.toolkit import Call


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


def test_labelled_switch_on_empty_focus_uses_freeze_plain_query():
    plain = ft._labelled(_rules(follow_request=True), "", "", relax=True)
    assert plain == [("General", "real business or organisation, United States")]
    off = ft._labelled(_rules(follow_request=False), "", "", relax=True)
    assert plain != off


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
    assert ft._icp_category_for_save(_rules(follow_request=True), "", "Agencies") == "General"
    assert ft._icp_category_for_save(_rules(follow_request=False), "", "Agencies") == "Agencies"
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


def test_labelled_follow_omits_size_without_tool_employee_args():
    rules = _rules(follow_request=True)
    on = ft._labelled(rules, "clinics", "", relax=False)[0][1]
    off = ft._labelled(_rules(follow_request=False), "clinics", "", relax=False)[0][1]
    assert "employees" not in on
    assert "employees" in off


def test_labelled_follow_keeps_size_when_tool_passes_employee_args():
    q = ft._labelled(_rules(follow_request=True), "clinics", "", lo=10, hi=50, relax=False)[0][1]
    assert "10 to 50 employees" in q


@patch("freedombot.connectors.composio.execute")
def test_verified_email_accepts_unverified_in_follow_mode(mock_execute):
    mock_execute.return_value = {
        "person": {"email": {"email": "lead@acme.com", "status": "UNVERIFIED"}},
        "company": {},
    }
    email, _facts, verified = ft._verified_email(MagicMock(), "Jane Doe", "acme.com", accept_unverified=True)
    assert email == "lead@acme.com"
    assert verified is False
    args = mock_execute.call_args[0][3]
    assert args["only_verified_email"] is False


@patch("freedombot.connectors.composio.execute")
def test_verified_email_rejects_unverified_when_not_follow(mock_execute):
    mock_execute.return_value = {
        "person": {"email": {"email": "lead@acme.com", "status": "UNVERIFIED"}},
        "company": {},
    }
    email, _facts, verified = ft._verified_email(MagicMock(), "Jane Doe", "acme.com", accept_unverified=False)
    assert email is None
    assert verified is False
    args = mock_execute.call_args[0][3]
    assert args["only_verified_email"] is True


@patch.object(ft, "_exa")
def test_founder_wide_titles_accepts_practice_manager(mock_exa):
    mock_exa.return_value = [
        {
            "title": "Pat Lee",
            "summary": "Practice manager at Acme Clinic",
            "url": "https://linkedin.com/in/pat",
        }
    ]
    found = ft._founder("key", "Acme Clinic", wide_titles=True)
    assert found is not None
    assert found[0] == "Pat Lee"


@patch.object(ft, "_exa")
def test_founder_default_rejects_practice_manager(mock_exa):
    mock_exa.return_value = [
        {
            "title": "Pat Lee",
            "summary": "Practice manager at Acme Clinic",
            "url": "https://linkedin.com/in/pat",
        }
    ]
    assert ft._founder("key", "Acme Clinic", wide_titles=False) is None


def _find_call():
    db = MagicMock()
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = []
    return Call(db=db, state={}, latest="", user_id="user-1")


def test_labelled_freeze_plain_empty_focus_uses_general_query():
    pairs = ft._labelled(_rules(follow_request=True), "", "", relax=False)
    assert pairs == [("General", "real business or organisation, United States")]


def test_icp_category_freeze_plain_is_general():
    assert ft._icp_category_for_save(_rules(follow_request=True), "", "Other") == "General"


@patch.object(ft, "lookup")
@patch.object(ft, "_save")
@patch.object(ft, "_classify", return_value={0: ("Other", "clinic", True)})
@patch.object(ft, "_web_firms")
@patch("freedombot.connectors.composio._active_accounts", return_value={})
@patch("freedombot.control.store.build_context", return_value=MagicMock())
@patch("freedombot.control.store.get_user", return_value={})
@patch.object(ft, "get_prospecting_rules")
@patch.object(ft, "get_settings")
def test_find_new_prospects_freeze_on_empty_focus_runs_search_and_saves_general(
    mock_settings, mock_rules, _user, _ctx, _accounts, mock_web, _classify, mock_save, mock_lookup,
):
    mock_rules.return_value = _rules(follow_request=True)
    mock_settings.return_value.exa_api_key = ""
    mock_web.return_value = [
        {
            "title": "Acme Co",
            "url": "https://acme.co",
            "summary": "An operating business",
            "_category": "Other",
            "_query": "real business or organisation, United States",
            "_web": True,
            "_founder": ("Jane Smith", "Director", ""),
            "_email": "jane@acme.co",
        }
    ]
    mock_lookup.by_email.return_value = None
    saved = MagicMock()
    mock_save.return_value = saved

    ft.find_new_prospects(_find_call(), count=1, focus="")

    mock_web.assert_called()
    assert mock_web.call_args[0][1] == ""
    assert "real business or organisation" in mock_web.return_value[0]["_query"]
    assert mock_save.call_args[0][7] == "General"


@patch.object(ft, "_web_firms", return_value=[])
@patch("freedombot.connectors.composio._active_accounts", return_value={})
@patch("freedombot.control.store.build_context", return_value=MagicMock())
@patch("freedombot.control.store.get_user", return_value={})
@patch.object(ft, "get_prospecting_rules")
@patch.object(ft, "get_settings")
def test_find_new_prospects_freeze_off_empty_focus_still_searches(mock_settings, mock_rules, _user, _ctx, _accounts, mock_web):
    mock_rules.return_value = _rules(follow_request=False)
    mock_settings.return_value.exa_api_key = ""
    ft.find_new_prospects(_find_call(), count=1, focus="")
    mock_web.assert_called()
    args, kwargs = mock_web.call_args
    pairs = ft._labelled(mock_rules.return_value, "", args[2], relax=False)
    assert "real business or organisation" not in pairs[0][1]
    assert "founder-led" in pairs[0][1]


@patch.object(ft, "_web_firms", return_value=[])
@patch("freedombot.connectors.composio._active_accounts", return_value={})
@patch("freedombot.control.store.build_context", return_value=MagicMock())
@patch("freedombot.control.store.get_user", return_value={})
@patch.object(ft, "get_prospecting_rules")
@patch.object(ft, "get_settings")
def test_find_new_prospects_freeze_on_with_focus_unchanged(mock_settings, mock_rules, _user, _ctx, _accounts, mock_web):
    mock_rules.return_value = _rules(follow_request=True)
    mock_settings.return_value.exa_api_key = ""
    ft.find_new_prospects(_find_call(), count=1, focus="clinics")
    mock_web.assert_called()
    q = ft._labelled(mock_rules.return_value, "clinics", "", relax=False)[0][1]
    assert q.startswith("clinics firm or practice,")
    assert ft._icp_category_for_save(mock_rules.return_value, "clinics", "Other") == "Clinics"


@patch.object(ft, "_web_firms", return_value=[])
@patch("freedombot.connectors.composio._active_accounts", return_value={})
@patch("freedombot.control.store.build_context", return_value=MagicMock())
@patch("freedombot.control.store.get_user", return_value={})
@patch.object(ft, "get_prospecting_rules")
@patch.object(ft, "get_settings")
def test_find_new_prospects_reports_search_duration(mock_settings, _mock_rules, _user, _ctx, _accounts, _web):
    _mock_rules.return_value = _rules()
    mock_settings.return_value.exa_api_key = ""
    out = ft.find_new_prospects(_find_call(), count=1)
    assert "Search took" in out
    assert "seconds." in out