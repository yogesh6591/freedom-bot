"""
CRM Tool Effects
================

The "what happens" half of every CRM capability. No permission logic lives here —
:func:`bizos.tools.base.guarded_call` has already decided that this call may
proceed. Keeping them separate means the approval executor can reuse the exact
same function when an approver releases a queued action.

Risk-input extractors query the workspace to establish how many records a call
would actually touch, **before** anything is written, so the bulk limit is
checked against reality rather than against what the model claimed.
"""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect, risk_inputs


def _crm(scope: RunScope):
    return get_connector("crm", ctx=scope.ctx)


# ------------------------------------------------------------------- reads


@effect("crm_search_contacts")
def crm_search_contacts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_contacts(
        args.get("query", ""), lifecycle=args.get("lifecycle"), limit=int(args.get("limit", 20))
    )


@effect("crm_get_contact")
def crm_get_contact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).get_contact(str(args.get("contact_id", "")))


@effect("crm_search_companies")
def crm_search_companies(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_companies(args.get("query", ""), limit=int(args.get("limit", 20)))


@effect("crm_search_deals")
def crm_search_deals(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_deals(
        stage=args.get("stage"), owner=args.get("owner"), limit=int(args.get("limit", 50))
    )


@effect("crm_pipeline_summary")
def crm_pipeline_summary(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).pipeline_summary()


# ------------------------------------------------------------------ writes


@effect("crm_create_note")
def crm_create_note(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_note(
        body=str(args.get("body", "")),
        contact_id=args.get("contact_id"),
        deal_id=args.get("deal_id"),
    )


@effect("crm_create_task")
def crm_create_task(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_task(
        title=str(args.get("title", "")),
        body=str(args.get("body", "")),
        contact_id=args.get("contact_id"),
        deal_id=args.get("deal_id"),
        assignee=args.get("assignee"),
        due_at=args.get("due_at"),
    )


@effect("crm_tag_lead")
def crm_tag_lead(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).tag_lead(
        str(args.get("contact_id", "")),
        add_tags=list(args.get("add_tags") or []),
        remove_tags=list(args.get("remove_tags") or []),
        lifecycle=args.get("lifecycle"),
    )


@risk_inputs("crm_tag_lead")
def _tag_lead_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Tagging touches ``tags`` and, when a lifecycle is given, ``lifecycle``."""
    fields = ["tags"]
    if args.get("lifecycle"):
        fields.append("lifecycle")
    return {"fields": tuple(fields), "record_count": 1}


@effect("crm_update_contact_field")
def crm_update_contact_field(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).update_contact_field(
        str(args.get("contact_id", "")), field=str(args.get("field", "")), value=args.get("value")
    )


@risk_inputs("crm_update_contact_field")
def _update_field_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"fields": (str(args.get("field", "")),), "record_count": 1}


@effect("crm_create_contact")
def crm_create_contact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_contact(
        first_name=str(args.get("first_name", "")),
        last_name=str(args.get("last_name", "")),
        email=args.get("email"),
        company=args.get("company"),
        title=args.get("title"),
        lifecycle=str(args.get("lifecycle", "lead")),
        tags=list(args.get("tags") or []),
        properties=dict(args.get("properties") or {}),
    )


@effect("crm_update_deal_stage")
def crm_update_deal_stage(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).update_deal_stage(str(args.get("deal_id", "")), stage=str(args.get("stage", "")))


@risk_inputs("crm_update_deal_stage")
def _deal_stage_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"fields": ("stage",), "record_count": 1}


@effect("crm_bulk_update_contacts")
def crm_bulk_update_contacts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).bulk_update_contacts(
        lifecycle_filter=args.get("lifecycle_filter"),
        field=str(args.get("field", "")),
        value=args.get("value"),
    )


@risk_inputs("crm_bulk_update_contacts")
def _bulk_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Count the rows the filter actually matches, before updating any of them.

    This is what makes the §30 bulk-action scenario real: the agent asking to
    change 1000 leads is measured against the workspace, not trusted.
    """
    count = _crm(scope).count_contacts(lifecycle=args.get("lifecycle_filter"))
    return {"record_count": count, "fields": (str(args.get("field", "")),)}


@effect("crm_delete_record")
def crm_delete_record(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).delete_record(
        record_type=str(args.get("record_type", "contact")), record_id=str(args.get("record_id", ""))
    )
