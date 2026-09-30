"""
Department people + Dhanu demo scenarios (buckets, payroll SOP, HR match, invoices).
"""

from __future__ import annotations

from bizos.bootstrap import DEV_PASSWORD, DEV_USERS
from bizos.control import store as control
from bizos.memory import store as memory
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.rbac.registry import get_spec
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.types import ExecutionMode, MemoryCategory, PolicyEffect, Role


def test_department_people_seeded_with_distinct_scopes(client_a):
    by_email = {u.email.split("@")[0]: u for u in control.list_users(client_a.id)}
    assert by_email["finance"].data_scopes == frozenset({"finance"})
    assert by_email["ops"].data_scopes == frozenset({"ops"})
    assert by_email["hr"].data_scopes == frozenset({"hr", "salary"})
    assert by_email["employee"].data_scopes == frozenset()
    assert by_email["lead"].data_scopes == frozenset({"ops"})
    assert "exec" in by_email["exec"].data_scopes
    assert Role.OPERATOR in by_email["finance"].roles
    assert Role.VIEWER in by_email["employee"].roles
    assert Role.APPROVER in by_email["exec"].roles
    # Platform demos still exist for existing tests.
    assert "operator" in by_email and "admin" in by_email
    assert len(DEV_USERS) >= 11
    assert DEV_PASSWORD


def test_finance_vs_ops_vs_hr_memory_buckets(client_a, ctx_factory):
    finance = ctx_factory(client_a, "finance")
    ops = ctx_factory(client_a, "ops")
    hr = ctx_factory(client_a, "hr")
    employee = ctx_factory(client_a, "employee")

    with tenant_scope(finance):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=200, ctx=finance)}
    assert "discount_policy" in keys
    assert "operating_hours" not in keys
    assert "job_opening_ops_coordinator" not in keys

    with tenant_scope(ops):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=200, ctx=ops)}
    assert "operating_hours" in keys
    assert "monthly_payroll_from_attendance" in keys
    assert "discount_policy" not in keys
    assert "salary_bands" not in keys

    with tenant_scope(hr):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=200, ctx=hr)}
    assert "job_opening_ops_coordinator" in keys
    assert "candidate_resume_jordan_lee" in keys
    assert "salary_bands" in keys
    assert "discount_policy" not in keys

    with tenant_scope(employee):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=200, ctx=employee)}
    assert "my_scope_of_work" in keys
    assert "client_agreement_acme_logistics" not in keys
    assert "discount_policy" not in keys
    assert "board_q3_plan" not in keys


def test_employee_lead_exec_see_different_info(client_a, ctx_factory):
    employee = ctx_factory(client_a, "employee")
    lead = ctx_factory(client_a, "lead")
    exec_user = ctx_factory(client_a, "exec")

    with tenant_scope(employee):
        e_keys = {i.memory_key for i in memory.list_items(ctx=employee)}
    with tenant_scope(lead):
        l_keys = {i.memory_key for i in memory.list_items(ctx=lead)}
    with tenant_scope(exec_user):
        x_keys = {i.memory_key for i in memory.list_items(ctx=exec_user)}

    assert "my_scope_of_work" in e_keys
    assert "monthly_payroll_from_attendance" not in e_keys
    assert "monthly_payroll_from_attendance" in l_keys  # ops scope
    assert "board_q3_plan" in x_keys
    assert "client_agreement_acme_logistics" in x_keys
    assert "board_q3_plan" not in l_keys


def test_payroll_process_sop_retrievable(client_a, ctx_factory):
    ops = ctx_factory(client_a, "ops")
    with tenant_scope(ops):
        hits = memory.search("payroll attendance payslips", authoritative_only=True, limit=10, ctx=ops)
        keys = {i.memory_key for i in hits}
        item = memory.get_by_key(MemoryCategory.SOP, "monthly_payroll_from_attendance", ctx=ops)
    assert "monthly_payroll_from_attendance" in keys
    assert item is not None and "Upload employee attendance" in (item.current.content or "")


def test_hr_match_candidate_tool(client_a, ctx_factory):
    hr = ctx_factory(client_a, "hr")
    employee = ctx_factory(client_a, "employee")
    with tenant_scope(hr):
        scope = RunScope(ctx=hr, settings=client_a.settings)
        out = guarded_call(scope, "hr_match_candidate", {})
    assert out.ok and out.data["applicable"] is True
    assert out.data["match_score"] >= 40

    # No HR scope → tool denied by data_area.
    with tenant_scope(employee):
        scope = RunScope(ctx=employee, settings=client_a.settings)
        denied = guarded_call(scope, "hr_match_candidate", {})
    assert not denied.ok


def test_invoice_list_and_block_without_finance(client_a, ctx_factory):
    finance = ctx_factory(client_a, "finance")
    ops = ctx_factory(client_a, "ops")

    with tenant_scope(finance):
        scope = RunScope(ctx=finance, settings=client_a.settings)
        listed = guarded_call(scope, "finance_list_pending_billables", {"customer": "Acme Logistics"})
    assert listed.ok
    assert listed.data["total"] >= 5700
    assert len(listed.data["items"]) >= 2

    with tenant_scope(ops):
        scope = RunScope(ctx=ops, settings=client_a.settings)
        blocked = guarded_call(scope, "finance_list_pending_billables", {"customer": "Acme Logistics"})
    assert not blocked.ok

    spec = get_spec("finance_draft_invoice")
    assert spec is not None and spec.data_area == "finance"


def _all_domains(client):
    import copy

    from bizos.domains.catalog import DOMAIN_NAMES

    settings = copy.deepcopy(client.settings)
    settings.enabled_domains = list(DOMAIN_NAMES)
    return settings


def test_finance_draft_invoice_requires_approval(client_a, ctx_factory):
    finance = ctx_factory(client_a, "finance")
    settings = _all_domains(client_a)
    with tenant_scope(finance):
        scope = RunScope(ctx=finance, settings=settings)
        req = PolicyRequest(
            ctx=finance,
            settings=settings,
            tool="finance_draft_invoice",
            arguments={"customer": "Acme Logistics"},
            domain="finance",
            requested_mode=ExecutionMode.WAIT_FOR_APPROVAL,
            connected_integrations=scope.integrations,
        )
        decision = evaluate(req)
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL, decision.reason


def test_grounding_names_restricted_area_without_leaking(client_a, ctx_factory):
    from bizos.agents import grounding

    finance = ctx_factory(client_a, "finance")
    ops = ctx_factory(client_a, "ops")
    employee = ctx_factory(client_a, "employee")
    domains = client_a.settings.enabled_domains
    with tenant_scope(finance):
        own = grounding.build(finance, "What is our discount / pricing policy?", domains)
        hours = grounding.build(finance, "What are our operating hours?", domains)
        salary = grounding.build(finance, "What salary band is Engineer II?", domains)
    with tenant_scope(ops):
        discount = grounding.build(ops, "What is our discount policy?", domains)
    with tenant_scope(employee):
        payroll = grounding.build(employee, "How do we run monthly payroll from attendance?", domains)

    assert "15%" in own
    assert "RESTRICTED" in hours and "Operations" in hours and "09:00" not in hours
    assert "Salary" in salary and "$120k" not in salary
    assert "RESTRICTED" in discount and "Finance" in discount and "15%" not in discount
    assert "RESTRICTED" in payroll and "Upload employee attendance" not in payroll


def test_get_fact_falls_back_and_reports_restriction(client_a, ctx_factory):
    hr = ctx_factory(client_a, "hr")
    finance = ctx_factory(client_a, "finance")
    with tenant_scope(hr):
        found = guarded_call(RunScope(ctx=hr, settings=client_a.settings), "memory_get_fact", {"key": "salary_band_engineer_ii"})
    with tenant_scope(finance):
        hidden = guarded_call(RunScope(ctx=finance, settings=client_a.settings), "memory_get_fact", {"key": "salary_band_engineer_ii"})
    assert found.ok and "$120k" in str(found.data)
    assert hidden.data is None
    assert "restricted" in hidden.agent_text().lower()


def test_finance_lookup_finds_untagged_finance_fact(client_a, ctx_factory):
    finance = ctx_factory(client_a, "finance")
    with tenant_scope(finance):
        out = guarded_call(
            RunScope(ctx=finance, settings=_all_domains(client_a), domain="finance"),
            "finance_lookup_policy",
            {"query": "discount"},
        )
    assert out.ok and any(h["key"] == "discount_policy" for h in out.data)


def test_hr_match_by_candidate_name(client_a, ctx_factory):
    hr = ctx_factory(client_a, "hr")
    with tenant_scope(hr):
        out = guarded_call(
            RunScope(ctx=hr, settings=client_a.settings),
            "hr_match_candidate",
            {"candidate_name": "Jordan Lee", "opening": "Operations Coordinator"},
        )
    assert out.ok and out.data["applicable"] is True
    assert out.data["must_haves_total"] == len(out.data["must_haves_met"])
    assert any("SQL" in m for m in out.data["missing"])


def test_draft_invoice_refused_when_nothing_to_bill(client_a, ctx_factory):
    from bizos.actions import store as actions

    finance = ctx_factory(client_a, "finance")
    with tenant_scope(finance):
        scope = RunScope(ctx=finance, settings=_all_domains(client_a), domain="finance")
        before = len(actions.list_actions(limit=500, ctx=finance))
        out = guarded_call(scope, "finance_draft_invoice", {"customer": "Nobody Such Customer"})
        after = len(actions.list_actions(limit=500, ctx=finance))
    assert not out.ok and "nothing to invoice" in out.message
    assert after == before


def test_search_single_word_query(client_a, ctx_factory):
    finance = ctx_factory(client_a, "finance")
    with tenant_scope(finance):
        hits = memory.search("discount", ctx=finance)
    assert any(i.memory_key == "discount_policy" for i in hits)


def test_restriction_names_the_right_area(client_a, ctx_factory):
    from bizos.agents.grounding import hidden_areas

    ops = ctx_factory(client_a, "ops")
    lead = ctx_factory(client_a, "lead")
    with tenant_scope(ops):
        billables = hidden_areas(ops, "Fiona told me the Acme Logistics pending total is $5,700. Confirm it.")
    with tenant_scope(lead):
        board = hidden_areas(lead, "What do I need to know about the payroll process and the Q3 staffing direction?")
    # A customer name alone must not blame Legal for a billing question.
    assert set(billables) == {"finance"}
    assert set(board) == {"exec"}


def test_enforce_restrictions_adds_area_and_strips_speculation():
    from bizos.agents.grounding import enforce_restrictions

    fixed, changed = enforce_restrictions("There is no record of that.", {"hr": ["resume"]})
    assert changed and "HR area" in fixed

    guess = "That is restricted to Legal.\n\nTypically such clauses allow 30 days notice.\n\n- 30 days"
    fixed, _ = enforce_restrictions(guess, {"legal": ["termination"]})
    assert "30 days" not in fixed and "Legal" in fixed


def test_memory_written_from_restricted_sop_stays_restricted(client_a, ctx_factory):
    lead = ctx_factory(client_a, "lead")
    employee = ctx_factory(client_a, "employee")
    sop = memory.get_by_key(MemoryCategory.SOP, "monthly_payroll_from_attendance", ctx=lead)
    with tenant_scope(lead):
        out = guarded_call(
            RunScope(ctx=lead, settings=client_a.settings),
            "memory_add_sop",
            {"key": "test_team_payroll_checklist", "title": "Team payroll checklist", "content": sop.current.content},
        )
    assert out.effect in (PolicyEffect.ALLOW, PolicyEffect.DRAFT_ONLY, PolicyEffect.REQUIRE_APPROVAL)
    with tenant_scope(employee):
        leaked = memory.get_by_key(MemoryCategory.SOP, "test_team_payroll_checklist", ctx=employee)
    assert leaked is None


def test_ops_bucket_in_data_areas():
    from bizos.types import DATA_AREAS

    assert "ops" in DATA_AREAS


def test_one_record_mixes_open_project_work_and_finance_only_invoice(client_a, ctx_factory):
    from bizos.agents import grounding

    employee = ctx_factory(client_a, "employee")
    ops = ctx_factory(client_a, "ops")
    finance = ctx_factory(client_a, "finance")
    admin = ctx_factory(client_a, "admin")

    for ctx in (employee, ops):
        with tenant_scope(ctx):
            item = memory.get_by_key(MemoryCategory.FACT, "project_atlas_globex", ctx=ctx)
            assert item is not None
            content = item.current.content
            assert "go-live Oct 3" in content
            assert "18,400" not in content and "INV-ATL" not in content
            assert "restricted to the Finance area" in content
            hits = {i.memory_key: i.current.content for i in memory.search("Globex invoice amount", ctx=ctx)}
            assert all("18,400" not in c for c in hits.values())
            assert "finance" in memory.restricted_area_hits("What is the Globex Foods invoice amount and due date?", ctx=ctx)
            g = grounding.analyze(ctx, "What is the invoice amount for Project Atlas?", client_a.settings.enabled_domains)
            assert "18,400" not in g.text and "finance" in g.hidden
            try:
                memory.correct(item_id=item.id, new_content="Project work only.", reason="test", ctx=ctx)
                raise AssertionError("dropping a hidden section must be refused")
            except PermissionError:
                pass

    for ctx in (finance, admin):
        with tenant_scope(ctx):
            item = memory.get_by_key(MemoryCategory.FACT, "project_atlas_globex", ctx=ctx)
            assert "$18,400" in item.current.content and "go-live Oct 3" in item.current.content
            assert "finance" not in memory.restricted_area_hits("What is the Globex Foods invoice amount?", ctx=ctx)
