"""Value-step budget reason is stored, and stale assessment copy is rewritten for display."""

from types import SimpleNamespace

from acquisition.models.campaign import JourneyStage
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.assessment_completion_service import build_qualification_transparency


def test_complete_value_stores_budget_signal_reason(monkeypatch):
    profile = SimpleNamespace(
        qualification={
            "budget_readiness": "not_assessed",
            "signal_reasons": {
                "budget_readiness": "Budget readiness has not been assessed.",
            },
        }
    )
    link = SimpleNamespace(
        value_context_completed_at=None,
        last_activity_at=None,
        journey_stage=JourneyStage.VALUE,
    )
    flagged = {}

    class FakeDb:
        def add(self, _obj):
            return None

        def commit(self):
            return None

        def refresh(self, _obj):
            return None

    monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda obj, key: flagged.update(key=key))
    monkeypatch.setattr(
        "acquisition.services.acquisition_journey_service.is_calendar_eligible",
        lambda **_kw: False,
    )
    svc = AcquisitionJourneyService(FakeDb())
    monkeypatch.setattr(svc, "resolve", lambda token: (link, profile))
    monkeypatch.setattr(svc, "can_access_value", lambda link, profile: True)

    out = svc.complete_value("tok_value", budget_readiness="ready")
    assert out["budget_readiness"] == "ready"
    assert profile.qualification["budget_readiness"] == "ready"
    assert profile.qualification["signal_reasons"]["budget_readiness"] == (
        "Prospect said they are ready to discuss next steps on the value step."
    )
    assert flagged["key"] == "qualification"


def test_stale_budget_reason_not_been_assessed():
    out = build_qualification_transparency(
        {
            "budget_readiness": "ready",
            "signal_reasons": {
                "budget_readiness": (
                    "Budget readiness has not been assessed. "
                    "No budget or ability-to-pay commitment was provided."
                )
            },
        }
    )
    row = next(item for item in out["breakdown"] if item["key"] == "budget_readiness")
    assert row["reason"] == "Marked ready during value / price conditioning."
