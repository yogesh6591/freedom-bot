"""v2 tool metadata follows docs/agent/TOOL_DESCRIPTIONS.md and matches the code and the dry-run."""

from __future__ import annotations

import re

import pytest

from app.assistant.assistant import _packs_for
from bizos.agents.merged_tools import MERGED, WRAPPED
from bizos.agents.tool_args import schema_for_tool
from bizos.agents.tool_meta import DOMAINS, GATE_TEXT, TOOL_META, render
from bizos.control import store as control
from bizos.rbac.registry import TOOL_SPECS, get_spec
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope
from bizos.tools.dry_run import DENY, dry_run_for_user

REGISTERED = [s.name for s in TOOL_SPECS]
NAMES = sorted(set(REGISTERED) | set(MERGED))
#: What the v2 model sees: merged tools replace the old tools they wrap.
VISIBLE = sorted((set(REGISTERED) - WRAPPED) | set(MERGED))

#: Hard prerequisites the guard enforces (Phase 6). Anything else is a suggestion only.
HARD_PRIOR = {
    "admin_launch_sign_off": ("admin_launch_checklist",),
    "admin_apply_setup": ("admin_get_setup",),
    "admin_update_settings": ("admin_get_setup",),
    "admin_set_tool_permission": ("admin_tool_permissions",),
    "admin_set_tool_mode": ("admin_tool_permissions",),
    "finance_propose_adjustment": ("finance_lookup_policy",),
    "integration_read_action": ("integration_find_action",),
    "integration_write_action": ("integration_find_action",),
    "integration_destructive_action": ("integration_find_action",),
}

#: Confusable groups from TOOL_DESCRIPTIONS.md, in the final (model-visible) names.
CONFUSABLE = [
    ("email_send_message", "propose_email", "send_approved"),
    ("email_create_draft", "email_send_message"),
    ("crm_update_contact_field", "crm_bulk_update_contacts"),
    ("crm_update_contact_field", "crm_tag_lead"),
    ("crm_search_contacts", "crm_search_companies", "crm_search_deals"),
    ("crm_search_contacts", "query_records"),
    ("crm_create_task", "support_create_ticket"),
    ("admin_list_users", "crm_search_contacts"),
    ("admin_update_user", "admin_set_user_status"),
    ("admin_update_user", "admin_set_tool_permission"),
    ("admin_set_tool_permission", "admin_set_tool_mode"),
    ("admin_update_user", "admin_toggle_domain"),
    ("admin_list_alerts", "admin_scan_alerts", "admin_dismiss_alert", "admin_set_baselines"),
    ("admin_pricing", "admin_consult_quote", "admin_record_change_order"),
    ("memory_add", "memory_correct", "memory_escalate_conflict"),
    ("memory_get_fact", "memory_history", "memory_search"),
    ("calendar_list_events", "calendar_check_availability"),
    ("approvals_decide", "approvals_cancel"),
    ("youtube_my_channel", "youtube_video", "youtube_search_videos"),
    ("finance_draft_invoice", "finance_propose_adjustment"),
    ("query_records", "show_assessment", "show_sent_emails"),
    ("query_records", "choose_recipients"),
    ("query_records", "show_prospect"),
    ("propose_email", "choose_recipients"),
    ("delete_prospects", "refuse_bulk_change"),
    ("update_prospects", "delete_prospects", "refuse_bulk_change"),
    ("show_assessment", "set_qualification"),
    ("set_qualification", "rerun_qualification"),
    ("rerun_qualification", "reset_journey"),
    ("reset_journey", "cancel_booking"),
    ("record_consultation", "set_handoff_status"),
    ("reschedule_booking", "cancel_booking"),
    ("create_payment_offer", "send_payment_link"),
    ("update_journey_settings", "edit_case_study", "publish_journey"),
    ("update_journey_settings", "edit_video_link"),
    ("edit_case_study", "edit_video_link"),
]

HINGLISH = re.compile(r"\b(ko|kar do|kya|kab|hai|hain|bhej|bana|dhundo|daal|kaun|hata|mein|kis|bhejo|haan|karo)\b")


def _lines(name: str) -> list[str]:
    return [line.split(":", 1)[0] for line in TOOL_META[name].description.splitlines() if ":" in line]


def _renders(name: str) -> list[str]:
    text = TOOL_META[name].description
    return [render(name, g) for g in GATE_TEXT] if "{gate}" in text else [text]


def test_every_registered_and_merged_tool_has_metadata_and_nothing_else_does():
    assert set(TOOL_META) == set(NAMES)
    assert all(TOOL_META[n].domain in DOMAINS for n in NAMES)
    assert all(TOOL_META[n].domain == MERGED[n].domain for n in MERGED)
    assert len(VISIBLE) == 116  # outreach 31; youtube_transcript, integration_* (4), web_search, web_read, attachment_read
    assert {n for n in NAMES if TOOL_META[n].domain == "outreach"} == {
        "choose_recipients", "propose_email", "set_person_details", "send_approved",
        "send_from_template", "cancel_sending", "show_sent_emails",
        "show_email_template", "update_email_template", "find_people",
        "query_records", "show_prospect", "add_prospects", "update_prospects",
        "delete_prospects", "refuse_bulk_change",
        "show_assessment", "set_qualification", "rerun_qualification", "reset_journey",
        "record_consultation", "set_handoff_status",
        "reschedule_booking", "cancel_booking",
        "create_payment_offer", "send_payment_link",
        "show_journey_settings", "update_journey_settings", "edit_case_study",
        "edit_video_link", "publish_journey",
    }


@pytest.mark.parametrize("name", NAMES)
def test_description_format(name):
    meta, text = TOOL_META[name], TOOL_META[name].description
    lines = text.splitlines()
    assert any(line.startswith("Use when:") for line in lines), name
    assert any(line.startswith("Don't use:") for line in lines), name
    assert not lines[0].startswith(("Use when", "Don't use", "Needs", "Effect")), "starts with a plain sentence"
    if meta.id_fields:
        assert any(line.startswith("Needs:") for line in lines), "a tool that takes IDs says where they come from"
    spec = get_spec(name)
    if (spec and spec.write and name not in MERGED) or (name in MERGED and any(get_spec(t).write for t in MERGED[name].wraps)):
        assert "{gate}" in text, "a write states its gate from the dry-run"
    if "{gate}" in text:
        effect = next(line for line in lines if line.startswith("Effect:"))
        assert effect is lines[-1] or text.rstrip().endswith("{gate}")
    for rendered in _renders(name):
        assert 40 <= len(rendered.split()) <= 90, (name, len(rendered.split()))


@pytest.mark.parametrize("name", NAMES)
def test_gate_is_stated_only_through_the_dry_run(name):
    """The gate sentence comes from the policy dry-run; static text never claims one."""
    static = TOOL_META[name].description.replace("{gate}", "").casefold()
    for phrase in ("needs approval", "requires approval", "runs straight away", "saved as a draft for review"):
        assert phrase not in static, (name, phrase)


@pytest.mark.parametrize("group", CONFUSABLE, ids=lambda g: "/".join(g))
def test_confusable_tools_name_their_siblings(group):
    for tool in group:
        dont_use = " ".join(
            line for line in TOOL_META[tool].description.splitlines()
            if not line.startswith(("Use when:", "Effect:"))
        )
        missing = [s for s in group if s != tool and s not in dont_use]
        assert not missing, (tool, missing)


@pytest.mark.parametrize("name", NAMES)
def test_id_fields_and_prerequisites_are_real(name):
    meta = TOOL_META[name]
    props = set((schema_for_tool(name) or {}).get("properties") or {})
    if name in MERGED:
        props = set(MERGED[name].schema["properties"])
    assert set(meta.id_fields) <= props, (name, set(meta.id_fields) - props)
    for prior in meta.requires_prior + meta.suggested_prior:
        assert prior in VISIBLE and prior != name, (name, prior)


def test_hard_prerequisites_are_exactly_the_agreed_set():
    assert {n: TOOL_META[n].requires_prior for n in NAMES if TOOL_META[n].requires_prior} == HARD_PRIOR


@pytest.mark.parametrize("name", VISIBLE)
def test_visible_descriptions_never_name_a_hidden_tool(name):
    named = {w for w in WRAPPED if re.search(rf"\b{w}\b", TOOL_META[name].description)}
    assert not named, (name, named)


def test_at_least_fifteen_tools_have_a_hindi_or_hinglish_phrasing():
    assert sum(1 for n in VISIBLE if HINGLISH.search(TOOL_META[n].description)) >= 15


@pytest.mark.parametrize("who", ["viewer", "drafter", "approver", "operator", "admin"])
def test_effect_line_matches_the_dry_run_for_every_role(client_scratch, ctx_factory, who):
    ctx = ctx_factory(client_scratch, who)
    settings = control.get_client(client_scratch.id).settings
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    packs = _packs_for(settings, "")
    with tenant_scope(ctx):
        for name in REGISTERED:
            if "{gate}" not in TOOL_META[name].description:
                continue
            gate = dry_run_for_user(scope, name, {}, packs=packs).gate
            if gate == DENY:
                continue  # never shown to this user
            text = render(name, gate)
            assert GATE_TEXT[gate] in text and all(GATE_TEXT[g] not in text for g in GATE_TEXT if g != gate)


def test_the_current_assistant_does_not_read_v2_metadata():
    import inspect

    from app.assistant import assistant
    from bizos.agents import surface

    for module in (assistant, surface):
        assert "tool_meta" not in inspect.getsource(module)
