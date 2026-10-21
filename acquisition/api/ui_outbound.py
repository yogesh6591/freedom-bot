"""Admin UI for replies, AI settings, and campaign outbound actions."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.db.session import SessionLocal, get_db
from acquisition.models.replies import ReplyConversation
from acquisition.models.settings import AIReplySettings
from acquisition.services.campaign_activation_service import ActivationError, CampaignActivationService
from acquisition.services.campaign_service import CampaignNotFoundError
from acquisition.services.lead_sync_service import LeadSyncError, LeadSyncService
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request

templates = create_templates()

router = APIRouter(tags=["admin-ui-outbound"], dependencies=[Depends(require_admin_html_request)])


@router.get("/replies", response_model=None)
def replies_page() -> RedirectResponse:
    """Legacy inbox URL — replies live on Assessment detail now."""
    return RedirectResponse(
        url="/assessments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/replies/{conversation_id}", response_model=None)
def reply_detail(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Legacy thread URL — open the assessment Email replies tab."""
    conv = db.get(ReplyConversation, conversation_id)
    if conv is None:
        return RedirectResponse(
            url="/assessments?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/assessments/{conv.campaign_prospect_id}?tab=replies",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/settings/ai", response_class=HTMLResponse)
def ai_settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )
    from acquisition.services.icp_settings_service import IcpSettingsService
    from acquisition.services.openai_service import OpenAIService

    row = db.get(AIReplySettings, 1)
    if row is None:
        row = AIReplySettings(
            id=1,
            auto_reply_enabled=False,
            system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            assessment_system_prompt=DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
            qualification_system_prompt=DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    provider_ok = OpenAIService(db=db).get_summary().configured
    icp = IcpSettingsService(db).as_dict()
    from acquisition.config import get_settings
    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService

    settings_cfg = get_settings()
    orch = AcquisitionOrchestrationService(db, settings=settings_cfg)
    discovery_status = orch.discovery.provider_status()
    from acquisition.models.campaign import Campaign, CampaignStatus
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService
    from sqlalchemy import select

    campaigns = list(
        db.scalars(
            select(Campaign)
            .where(Campaign.status != CampaignStatus.ARCHIVED)
            .order_by(Campaign.name.asc())
        ).all()
    )
    automation_ops = AutonomousAutomationService(db, settings=settings_cfg).operational_status()
    from acquisition.services.acquisition_learning_service import AcquisitionLearningService

    learning_summary = AcquisitionLearningService(db).get_learning_summary(
        recompute_if_missing=False
    )
    preview_raw = request.query_params.get("preview")
    preview_text = None
    if preview_raw:
        from urllib.parse import unquote
        import json

        try:
            preview_text = json.dumps(json.loads(unquote(preview_raw)), indent=2)
        except Exception:  # noqa: BLE001
            preview_text = unquote(preview_raw)
    return templates.TemplateResponse(
        request,
        "settings/ai.html",
        {
            "settings": row,
            "provider_configured": provider_ok,
            "icp": icp,
            "discovery_status": discovery_status,
            "discovery_limit": icp.get("discovery_cycle_limit")
            or settings_cfg.discovery_cycle_limit,
            "discovery_enabled": bool(icp.get("discovery_enabled"))
            or bool(settings_cfg.discovery_enabled),
            "campaigns": campaigns,
            "activation_limit": icp.get("activation_batch_limit") or 5,
            "dry_run_default": settings_cfg.activation_dry_run_default,
            "automation_ops": automation_ops,
            "learning_summary": learning_summary,
            "flash": request.query_params.get("flash"),
            "discovery_summary": request.query_params.get("summary")
            if request.query_params.get("flash")
            in {"discovery_ran", "automation_discovery", "live_e2e", "live_e2e_error"}
            else None,
            "activation_summary": request.query_params.get("summary")
            if request.query_params.get("flash")
            in {
                "activation_ran",
                "activation_error",
                "automation_activation",
                "learning_recomputed",
            }
            else None,
            "preview_blob": preview_text,
            "discovery_sources": (
                orch.discovery.provider.source_status()
                if callable(getattr(orch.discovery.provider, "source_status", None))
                else []
            ),
            "discovery_provider_name": (settings_cfg.discovery_provider or "none"),
            "discovery_model": settings_cfg.openai_discovery_model,
            "discovery_reasoning": settings_cfg.openai_discovery_reasoning_effort,
            "discovery_price_input": getattr(settings_cfg, "discovery_price_input_per_m", None)
            or settings_cfg.openai_price_input_per_m,
            "discovery_price_output": getattr(settings_cfg, "discovery_price_output_per_m", None)
            or settings_cfg.openai_price_output_per_m,
            "discovery_price_web": getattr(settings_cfg, "discovery_price_web_search_per_call", 0.01),
            "discovery_pricing_updated": "2026-10-01",
        },
    )


@router.post("/settings/ai", response_model=None)
def ai_settings_save(
    auto_reply_enabled: Annotated[str | None, Form()] = None,
    system_prompt: Annotated[str, Form()] = "",
    assessment_system_prompt: Annotated[str, Form()] = "",
    qualification_system_prompt: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )

    row = db.get(AIReplySettings, 1)
    if row is None:
        row = AIReplySettings(
            id=1,
            system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            assessment_system_prompt=DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
            qualification_system_prompt=DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
        )
    row.auto_reply_enabled = auto_reply_enabled == "true"
    row.system_prompt = system_prompt.strip() or DEFAULT_AI_SYSTEM_PROMPT
    row.assessment_system_prompt = (
        assessment_system_prompt.strip() or DEFAULT_ASSESSMENT_SYSTEM_PROMPT
    )
    row.qualification_system_prompt = (
        qualification_system_prompt.strip() or DEFAULT_QUALIFICATION_SYSTEM_PROMPT
    )
    db.add(row)
    db.commit()
    return RedirectResponse(url="/settings/ai?flash=saved", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/ai/icp", response_model=None)
def icp_settings_save(
    geography_country: Annotated[str, Form()] = "",
    geography_nationwide: Annotated[str | None, Form()] = None,
    geography_states: Annotated[str, Form()] = "",
    geography_cities: Annotated[str, Form()] = "",
    revenue_primary_min: Annotated[str, Form()] = "5",
    revenue_primary_max: Annotated[str, Form()] = "50",
    revenue_sweet_min: Annotated[str, Form()] = "7",
    revenue_sweet_max: Annotated[str, Form()] = "30",
    revenue_allow_strong_fit: Annotated[str | None, Form()] = None,
    buyer_titles: Annotated[str, Form()] = "",
    fit_themes: Annotated[str, Form()] = "",
    priority_categories: Annotated[str, Form()] = "",
    excluded_categories: Annotated[str, Form()] = "",
    score_fit_percent: Annotated[str, Form()] = "45",
    score_timing_percent: Annotated[str, Form()] = "25",
    score_reachability_percent: Annotated[str, Form()] = "30",
    ready_priority_min: Annotated[str, Form()] = "55",
    ready_reachability_min: Annotated[str, Form()] = "40",
    notes: Annotated[str, Form()] = "",
    # Legacy JSON inputs are retained for backward compatibility with older clients/tests.
    geography_json: Annotated[str, Form()] = "",
    revenue_json: Annotated[str, Form()] = "",
    score_weights_json: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    import json

    from acquisition.services.icp_settings_service import IcpSettingsService

    def _float(raw: str, default: float) -> float:
        try:
            return float((raw or str(default)).strip() or default)
        except ValueError:
            return default

    def _lines(raw: str) -> list[str]:
        return [line.strip() for line in (raw or "").splitlines() if line.strip()]

    # Prefer the production-friendly fields rendered by the current UI.
    using_friendly_fields = bool((geography_country or "").strip())
    if using_friendly_fields:
        country = (geography_country or "United States").strip() or "United States"
        geography = {
            "countries": [country],
            "nationwide": geography_nationwide == "true",
            "states": _lines(geography_states),
            "cities": _lines(geography_cities),
        }

        p_min = _float(revenue_primary_min, 5)
        p_max = _float(revenue_primary_max, 50)
        s_min = _float(revenue_sweet_min, 7)
        s_max = _float(revenue_sweet_max, 30)
        if (
            p_min < 0
            or p_max < p_min
            or s_min < p_min
            or s_max < s_min
            or s_max > p_max
        ):
            return RedirectResponse(
                url="/settings/ai?flash=icp_validation_error",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        revenue = {
            "primary_min_millions": p_min,
            "primary_max_millions": p_max,
            "sweet_spot_min_millions": s_min,
            "sweet_spot_max_millions": s_max,
            "allow_strong_fit_outside_band": revenue_allow_strong_fit == "true",
        }

        fit_pct = _float(score_fit_percent, 45)
        timing_pct = _float(score_timing_percent, 25)
        reach_pct = _float(score_reachability_percent, 30)
        if any(x < 0 for x in (fit_pct, timing_pct, reach_pct)) or abs(
            (fit_pct + timing_pct + reach_pct) - 100.0
        ) > 0.5:
            return RedirectResponse(
                url="/settings/ai?flash=icp_validation_error",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        priority_min = _float(ready_priority_min, 55)
        reachability_min = _float(ready_reachability_min, 40)
        if not (0 <= priority_min <= 100 and 0 <= reachability_min <= 100):
            return RedirectResponse(
                url="/settings/ai?flash=icp_validation_error",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        score_weights = {
            "fit": fit_pct / 100.0,
            "timing": timing_pct / 100.0,
            "reachability": reach_pct / 100.0,
            "ready_for_outreach_min_priority": priority_min,
            "ready_for_outreach_min_reachability": reachability_min,
        }
    else:
        try:
            geography = json.loads(geography_json) if geography_json.strip() else None
            revenue = json.loads(revenue_json) if revenue_json.strip() else None
            score_weights = json.loads(score_weights_json) if score_weights_json.strip() else None
        except json.JSONDecodeError:
            return RedirectResponse(
                url="/settings/ai?flash=icp_json_error",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    fields: dict = {
        "buyer_titles": buyer_titles,
        "fit_themes": fit_themes,
        "priority_categories": priority_categories,
        "excluded_categories": excluded_categories,
        "notes": notes.strip() or None,
    }
    if geography is not None:
        fields["geography"] = geography
    if revenue is not None:
        fields["revenue"] = revenue
    if score_weights is not None:
        fields["score_weights"] = score_weights
    IcpSettingsService(db).update(**fields)
    return RedirectResponse(url="/settings/ai?flash=icp_saved", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/ai/discovery-cycle", response_model=None)
def run_discovery_cycle(
    limit: Annotated[str, Form()] = "5",
    research: Annotated[str | None, Form()] = "true",
    db: Session = Depends(get_db),
):
    """Admin/dev: bounded discovery (+ optional research/score). Never Instantly."""
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService

    settings_cfg = get_settings()
    if not settings_cfg.discovery_enabled:
        return RedirectResponse(
            url="/settings/ai?flash=discovery_disabled",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        limit_n = int((limit or "5").strip() or "5")
    except ValueError:
        limit_n = int(settings_cfg.discovery_cycle_limit or 5)

    do_research = (research or "").strip().lower() in {"1", "true", "yes", "on"}
    result = AcquisitionOrchestrationService(db, settings=settings_cfg).run_discovery_cycle(
        limit=limit_n,
        research=do_research,
        score=do_research,
    )
    discovery = result.get("discovery") or {}
    summary = (
        f"limit={discovery.get('limit')} candidates={discovery.get('candidates')} "
        f"created={discovery.get('created')} updated={discovery.get('updated')} "
        f"researched={len(result.get('research') or [])}"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=discovery_ran&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/activation-config", response_model=None)
def activation_config_save(
    autonomous_campaign_id: Annotated[str, Form()] = "",
    activation_batch_limit: Annotated[str, Form()] = "5",
    db: Session = Depends(get_db),
):
    from acquisition.services.icp_settings_service import IcpSettingsService

    try:
        limit_n = int((activation_batch_limit or "5").strip() or "5")
    except ValueError:
        limit_n = 5
    IcpSettingsService(db).update(
        autonomous_campaign_id=autonomous_campaign_id.strip() or None,
        activation_batch_limit=limit_n,
    )
    return RedirectResponse(
        url="/settings/ai?flash=activation_saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/activation-preview", response_model=None)
def activation_preview(
    prospect_id: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    import json
    from urllib.parse import quote
    import uuid as _uuid

    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService
    from acquisition.services.autonomous_activation_service import AutonomousActivationError

    try:
        pid = _uuid.UUID((prospect_id or "").strip())
        preview = AcquisitionOrchestrationService(db).preview_activation(pid)
        blob = quote(json.dumps(preview)[:3500])
        return RedirectResponse(
            url=f"/settings/ai?flash=activation_preview&preview={blob}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValueError, AutonomousActivationError) as exc:
        return RedirectResponse(
            url=f"/settings/ai?flash=activation_error&summary={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/settings/ai/activation-batch", response_model=None)
def activation_batch(
    limit: Annotated[str, Form()] = "5",
    dry_run: Annotated[str | None, Form()] = "true",
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService
    from acquisition.services.autonomous_activation_service import AutonomousActivationError

    settings_cfg = get_settings()
    try:
        limit_n = int((limit or "5").strip() or "5")
    except ValueError:
        limit_n = 5
    is_dry = (dry_run or "true").strip().lower() in {"1", "true", "yes", "on"}
    try:
        result = AcquisitionOrchestrationService(db, settings=settings_cfg).run_activation_batch(
            limit=limit_n, dry_run=is_dry
        )
    except AutonomousActivationError as exc:
        return RedirectResponse(
            url=f"/settings/ai?flash=activation_error&summary={quote(exc.message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    summary = (
        f"dry_run={is_dry} attempted={result.get('attempted')} "
        f"activated={result.get('activated_count')} "
        f"queue_selected={result.get('queue', {}).get('selected_count')}"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=activation_ran&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/automation-config", response_model=None)
def automation_config_save(
    discovery_enabled: Annotated[str | None, Form()] = None,
    discovery_cycle_limit: Annotated[str, Form()] = "5",
    discovery_interval_minutes: Annotated[str, Form()] = "1440",
    autonomous_outreach_enabled: Annotated[str | None, Form()] = None,
    autonomous_campaign_id: Annotated[str, Form()] = "",
    activation_batch_limit: Annotated[str, Form()] = "5",
    activation_interval_minutes: Annotated[str, Form()] = "1440",
    capacity_mode: Annotated[str, Form()] = "auto",
    capacity_upcoming_meetings_reduced_at: Annotated[str, Form()] = "5",
    capacity_upcoming_meetings_paused_at: Annotated[str, Form()] = "10",
    capacity_reduced_activation_limit: Annotated[str, Form()] = "2",
    # Legacy master switch remains accepted for old clients but is no longer shown.
    engine_enabled: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    import uuid as _uuid

    from acquisition.models.campaign import Campaign, CampaignStatus, InstantlySyncStatus
    from acquisition.services.icp_settings_service import IcpSettingsService

    def _int(raw: str, default: int) -> int:
        try:
            return int((raw or str(default)).strip() or default)
        except ValueError:
            return default

    discovery_on = discovery_enabled == "true"
    outreach_on = autonomous_outreach_enabled == "true"
    campaign_raw = (autonomous_campaign_id or "").strip()
    if outreach_on:
        if not campaign_raw:
            return RedirectResponse(
                url="/settings/ai?flash=automation_campaign_required",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        try:
            campaign_uuid = _uuid.UUID(campaign_raw)
        except ValueError:
            return RedirectResponse(
                url="/settings/ai?flash=automation_campaign_not_ready",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        campaign = db.get(Campaign, campaign_uuid)
        if (
            campaign is None
            or campaign.status != CampaignStatus.READY
            or not campaign.instantly_campaign_id
            or campaign.instantly_sync_status != InstantlySyncStatus.SYNCED
        ):
            return RedirectResponse(
                url="/settings/ai?flash=automation_campaign_not_ready",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    # Keep the legacy database flag synchronized for backward compatibility.
    # Runtime scheduling is controlled directly by discovery/outreach.
    scheduler_on = discovery_on or outreach_on

    IcpSettingsService(db).update(
        engine_enabled=scheduler_on,
        discovery_enabled=discovery_on,
        discovery_cycle_limit=_int(discovery_cycle_limit, 5),
        discovery_interval_minutes=_int(discovery_interval_minutes, 1440),
        autonomous_outreach_enabled=outreach_on,
        autonomous_campaign_id=campaign_raw or None,
        activation_batch_limit=_int(activation_batch_limit, 5),
        activation_interval_minutes=_int(activation_interval_minutes, 1440),
        capacity_mode=(capacity_mode or "auto").strip().lower(),
        capacity_upcoming_meetings_reduced_at=_int(capacity_upcoming_meetings_reduced_at, 5),
        capacity_upcoming_meetings_paused_at=_int(capacity_upcoming_meetings_paused_at, 10),
        capacity_reduced_activation_limit=_int(capacity_reduced_activation_limit, 2),
    )
    return RedirectResponse(
        url="/settings/ai?flash=automation_saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept or request.query_params.get("format") == "json"


@router.get("/settings/ai/automation/discovery-status", response_model=None)
def automation_discovery_status(request: Request, db: Session = Depends(get_db)):
    """Latest discovery run snapshot for the Admin on-page progress UI."""
    from acquisition.config import get_settings
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService

    ops = AutonomousAutomationService(db, settings=get_settings()).operational_status()
    last = ops.get("last_discovery")
    return JSONResponse(
        {
            "ok": True,
            "last_discovery": last,
            "running": bool(last and last.get("status") == "running"),
        }
    )


@router.get("/settings/ai/automation/discovery-preflight", response_model=None)
def automation_discovery_preflight(db: Session = Depends(get_db)):
    """Zero-OpenAI-call validate & estimate for the next discovery run."""
    from acquisition.config import get_settings
    from acquisition.services.acquisition_cost_estimator import AcquisitionCostEstimator
    from acquisition.services.discovery_context_builder import DiscoveryContextBuilder
    from acquisition.services.icp_settings_service import IcpSettingsService
    from acquisition.services.openai_service import OpenAIService

    settings_cfg = get_settings()
    admin = IcpSettingsService(db).as_dict()
    coverage = DiscoveryContextBuilder(db).build(admin)
    key_ok = bool(OpenAIService(db=db, settings=settings_cfg).get_summary().configured) or bool(
        (settings_cfg.openai_api_key or "").strip()
    )
    result = AcquisitionCostEstimator(settings_cfg).preflight(
        admin,
        requested_count=int(admin.get("discovery_cycle_limit") or 5),
        coverage=coverage,
        api_key_configured=key_ok,
    )
    # Never include full prompts in the Admin JSON (can be huge).
    result.pop("structured_outputs", None)
    return JSONResponse(result)


@router.post("/settings/ai/automation/discovery-now", response_model=None)
def automation_discovery_now(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start a discovery cycle without blocking the Admin browser tab.

    Work runs in a background task with its own DB session. Instantly/outreach
    are never called from this path. JSON clients stay on the page and poll
    /settings/ai/automation/discovery-status for progress.
    """
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.services.acquisition_cost_estimator import AcquisitionCostEstimator
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService
    from acquisition.services.discovery_context_builder import DiscoveryContextBuilder
    from acquisition.services.icp_settings_service import IcpSettingsService
    from acquisition.services.openai_service import OpenAIService

    settings_cfg = get_settings()
    admin = IcpSettingsService(db).as_dict()
    coverage = DiscoveryContextBuilder(db).build(admin)
    key_ok = bool(OpenAIService(db=db, settings=settings_cfg).get_summary().configured) or bool(
        (settings_cfg.openai_api_key or "").strip()
    )
    pre = AcquisitionCostEstimator(settings_cfg).preflight(
        admin,
        requested_count=int(admin.get("discovery_cycle_limit") or 5),
        coverage=coverage,
        api_key_configured=key_ok,
    )
    if not pre.get("safe_to_run"):
        problems = "; ".join(pre.get("problems") or ["preflight_failed"])[:500]
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "blocked": True, "problems": pre.get("problems"), "preflight": pre},
                status_code=400,
            )
        return RedirectResponse(
            url=f"/settings/ai?flash=discovery_preflight_blocked&summary={quote(problems)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    last = AutonomousAutomationService(db, settings=settings_cfg).operational_status().get(
        "last_discovery"
    )
    baseline_id = (last or {}).get("id") or ""

    def _run() -> None:
        from acquisition.config import get_settings
        from acquisition.services.autonomous_automation_service import AutonomousAutomationService

        with SessionLocal() as session:
            AutonomousAutomationService(session, settings=get_settings()).run_discovery_cycle(
                manual=True
            )

    background_tasks.add_task(_run)
    if _wants_json(request):
        return JSONResponse({"ok": True, "started": True, "baseline_id": baseline_id, "preflight": {
            "base_run_usd": (pre.get("estimate") or {}).get("base_run_usd"),
            "base_calls": (pre.get("plan") or {}).get("base_calls"),
        }})
    q = "flash=automation_discovery_started"
    if baseline_id:
        q += f"&baseline={quote(str(baseline_id))}"
    return RedirectResponse(
        url=f"/settings/ai?{q}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/automation/activation-dry-run", response_model=None)
def automation_activation_dry_run(db: Session = Depends(get_db)):
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService

    result = AutonomousAutomationService(db, settings=get_settings()).run_activation_cycle(
        dry_run=True, manual=True
    )
    run = result.get("run") or {}
    summary = (
        f"dry_run=true status={run.get('status') or result.get('reason') or result.get('error')} "
        f"queued={run.get('queued_count', 0)} activated={run.get('activated_count', 0)} "
        f"capacity={run.get('capacity_state')}"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=automation_activation&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/automation/activation-now", response_model=None)
def automation_activation_now(db: Session = Depends(get_db)):
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService

    result = AutonomousAutomationService(db, settings=get_settings()).run_activation_cycle(
        dry_run=False, manual=True
    )
    run = result.get("run") or {}
    summary = (
        f"dry_run=false status={run.get('status') or result.get('reason') or result.get('error')} "
        f"queued={run.get('queued_count', 0)} activated={run.get('activated_count', 0)} "
        f"failed={run.get('failed_count', 0)} capacity={run.get('capacity_state')}"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=automation_activation&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/live-e2e", response_model=None)
def live_e2e_preview(
    limit: Annotated[str, Form()] = "2",
    activate_one: Annotated[str | None, Form()] = None,
    dry_run: Annotated[str | None, Form()] = "true",
    db: Session = Depends(get_db),
):
    """Bounded live discovery → research → score → queue → outreach preview.

    Never mass-sends. Optional single activation stays dry-run unless
    LIVE_E2E_ACTIVATION_ENABLED=true AND dry_run unchecked.
    """
    from urllib.parse import quote

    from acquisition.config import get_settings
    from acquisition.integrations.discovery.factory import is_live_discovery_provider_name
    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService

    settings_cfg = get_settings()
    try:
        limit_n = int((limit or "2").strip() or "2")
    except ValueError:
        limit_n = int(settings_cfg.live_e2e_discovery_limit or 2)
    want_activate = (activate_one or "").strip().lower() in {"1", "true", "yes", "on"}
    is_dry = (dry_run or "true").strip().lower() in {"1", "true", "yes", "on", ""}
    provider_name = (settings_cfg.discovery_provider or "none").strip().lower()
    if not is_live_discovery_provider_name(provider_name):
        # Allow mock in non-prod for Admin dry path testing of the E2E wiring.
        from acquisition.config import is_non_production_environment

        if not (
            is_non_production_environment(settings_cfg.environment)
            and provider_name in {"mock", "fixture", "test"}
        ):
            return RedirectResponse(
                url=(
                    "/settings/ai?flash=live_e2e_error&summary="
                    + quote("Set DISCOVERY_PROVIDER=web_search|serper|brave with API key")
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )
    try:
        result = AcquisitionOrchestrationService(
            db, settings=settings_cfg
        ).run_live_e2e_preview(
            discovery_limit=limit_n,
            activate_one=want_activate,
            dry_run=is_dry,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url=f"/settings/ai?flash=live_e2e_error&summary={quote(str(exc)[:500])}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    discovery = (result.get("discovery") or {}).get("discovery") or {}
    summary = (
        f"limit={result.get('limit')} candidates={discovery.get('candidates')} "
        f"created={discovery.get('created')} researched={len((result.get('discovery') or {}).get('research') or [])} "
        f"queue={result.get('queue', {}).get('selected_count')} "
        f"previews={len(result.get('outreach_previews') or [])} "
        f"activation={bool(result.get('activation'))} mass_send=false"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=live_e2e&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/campaigns/{campaign_id}/instantly/leads/sync", response_model=None)
def ui_sync_leads(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    try:
        LeadSyncService(db).sync_campaign_leads(campaign_id)
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=customers&flash=leads_synced",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, LeadSyncError):
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=customers&flash=leads_sync_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post(
    "/campaigns/{campaign_id}/prospects/{prospect_id}/instantly/sync",
    response_model=None,
)
def ui_sync_one_lead(
    campaign_id: uuid.UUID,
    prospect_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    try:
        LeadSyncService(db).sync_one_lead(campaign_id, prospect_id)
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=customers&flash=lead_synced",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, LeadSyncError):
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=customers&flash=leads_sync_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/sending-accounts", response_model=None)
def ui_save_sending_accounts(
    campaign_id: uuid.UUID,
    selected_emails: Annotated[list[str] | None, Form()] = None,
    db: Session = Depends(get_db),
):
    emails = selected_emails or []
    try:
        CampaignActivationService(db).save_sending_accounts(campaign_id, emails)
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=accounts_saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, ActivationError):
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=accounts_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/activate", response_model=None)
def ui_activate(
    campaign_id: uuid.UUID,
    confirm: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    try:
        CampaignActivationService(db).activate(campaign_id, confirm=confirm == "true")
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=activated",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, ActivationError):
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=activate_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/pause", response_model=None)
def ui_pause(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        CampaignActivationService(db).pause(campaign_id)
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=paused",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, ActivationError):
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?tab=launch&flash=pause_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )
