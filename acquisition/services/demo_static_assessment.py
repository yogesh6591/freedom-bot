"""Developer demo: static assessment scripts captured from live Fintech runs.

Modes (client gesture):
  - off / green: normal AI assessment
  - unqualified / red: canned answers → unqualified
  - qualified / blue: canned answers → qualified

Questions are fixed so each prefilled answer matches the ask. Result + qualification
payloads still come from app/data/demo_static_assessment/{outcome}.json (live capture).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm.attributes import flag_modified

from acquisition.models.assessment import (
    AssessmentConversation,
    AssessmentMessageRole,
    AssessmentProfile,
)
from acquisition.models.campaign import AssessmentStatus, CampaignProspect
from acquisition.models.journey import QualificationStatus

logger = logging.getLogger(__name__)

DemoStaticOutcome = Literal["unqualified", "qualified"]
VALID_OUTCOMES: frozenset[str] = frozenset({"unqualified", "qualified"})

# Fixed Q→A pairs so the demo reads coherently (not the free-form AI questions
# from capture, which asked about handoffs while answers covered headcount, etc.).
STATIC_SCRIPTS: dict[str, list[dict[str, str]]] = {
    "unqualified": [
        {
            "question": (
                "Tell me about your business and what you're trying to accomplish right now."
            ),
            "answer": (
                "We are Fintech, a growing financial technology company serving mid-sized businesses. "
                "We want to grow without adding too much overhead, but our sales-to-delivery process "
                "is creating some problems."
            ),
        },
        {
            "question": (
                "Roughly how large is the company today, and which teams are mainly involved "
                "in this sales-to-delivery work?"
            ),
            "answer": (
                "We have around 70 employees and our annual revenue is between $10M and $25M. "
                "Sales, operations and delivery teams are mainly involved."
            ),
        },
        {
            "question": (
                "Where does the friction show up — for example across tools, handoffs, or "
                "senior decisions — and what delay or rework does that create?"
            ),
            "answer": (
                "Information is spread across HubSpot, Slack and email, and some important decisions "
                "still depend on senior people. This can cause around 2–3 days of delay and 10–15 hours "
                "of waiting or rework on affected projects."
            ),
        },
        {
            "question": (
                "Who can approve changes here, and how open is the team to changing the process "
                "or using AI day to day?"
            ),
            "answer": (
                "I can approve changes, but right now the team is not comfortable using AI in day-to-day "
                "work and we are not ready to make a major change to our current process. We would prefer "
                "to continue with the current way of working for now."
            ),
        },
    ],
    "qualified": [
        {
            "question": (
                "Tell me about your business and what you're trying to accomplish right now."
            ),
            "answer": (
                "We are Fintech, a growing financial technology company serving mid-sized businesses. "
                "We want to grow without adding too much overhead, but our sales-to-delivery process "
                "is becoming harder to manage."
            ),
        },
        {
            "question": (
                "Roughly how large is the company today, and which teams are mainly involved "
                "in this sales-to-delivery work?"
            ),
            "answer": (
                "We have around 70 employees and our annual revenue is between $10M and $25M. "
                "Sales, operations and delivery teams are mainly involved in this process."
            ),
        },
        {
            "question": (
                "Where does the friction show up after a deal closes — tools, handoffs, or "
                "senior guidance — and what delay or rework does that create?"
            ),
            "answer": (
                "The main problem starts after sales closes a deal. Information is spread across HubSpot, "
                "Slack and email, and some important guidance is still with senior people, so affected "
                "projects can face around 2–3 days of delay and 10–15 hours of waiting or rework."
            ),
        },
        {
            "question": (
                "Who can approve changes here, and how open is the team to standardizing the "
                "process or using AI with human review?"
            ),
            "answer": (
                "I can approve changes to this process, and our Head of Operations can lead the rollout "
                "with sales and delivery. The team is open to standardizing the process and using AI for "
                "guidance, as long as important client and pricing decisions still have human review."
            ),
        },
    ],
}

# Back-compat for tests / callers that still import STATIC_ANSWERS.
STATIC_ANSWERS: dict[str, list[str]] = {
    key: [step["answer"] for step in steps] for key, steps in STATIC_SCRIPTS.items()
}


def normalize_demo_static(value: str | None) -> DemoStaticOutcome | None:
    raw = (value or "").strip().lower()
    if raw in VALID_OUTCOMES:
        return raw  # type: ignore[return-value]
    return None


def public_answer_bank() -> dict[str, list[str]]:
    return {k: list(v) for k, v in STATIC_ANSWERS.items()}


def _script_steps(outcome: DemoStaticOutcome) -> list[dict[str, str]]:
    return list(STATIC_SCRIPTS[outcome])


def _fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "demo_static_assessment"


def _verify_fixture_grounded(outcome: str, data: dict[str, Any]) -> None:
    """Fail fast if any public evidence quote is not in the scripted answers."""
    from acquisition.services.assessment_extraction import evidence_quote_in_user_message

    answers = [step["answer"] for step in _script_steps(outcome)]  # type: ignore[arg-type]
    # Prefer fixture answers when present (must match script).
    fixture_answers = data.get("answers") or []
    if isinstance(fixture_answers, list) and fixture_answers:
        if [str(a) for a in fixture_answers] != answers:
            raise ValueError(
                f"Demo static fixture answers out of sync with STATIC_SCRIPTS ({outcome})"
            )

    def _ok(quote: str) -> bool:
        return any(evidence_quote_in_user_message(quote, a) for a in answers)

    for area in (data.get("result_payload") or {}).get("opportunity_areas") or []:
        if not isinstance(area, dict):
            continue
        for quote in area.get("evidence_quotes") or []:
            if quote and not _ok(str(quote)):
                raise ValueError(
                    f"Demo static result quote not grounded ({outcome}): {quote!r}"
                )
    for item in (data.get("qualification") or {}).get("disqualifiers") or []:
        if not isinstance(item, dict):
            continue
        quote = item.get("evidence_quote")
        if quote and not _ok(str(quote)):
            raise ValueError(
                f"Demo static disqualifier quote not grounded ({outcome}): {quote!r}"
            )
    state = data.get("structured_state") or {}
    if isinstance(state, dict):
        for key, field in state.items():
            if not isinstance(field, dict):
                continue
            quote = field.get("evidence_quote")
            if quote and field.get("status") == "known" and not _ok(str(quote)):
                raise ValueError(
                    f"Demo static structured_state.{key} quote not grounded ({outcome}): {quote!r}"
                )


@lru_cache(maxsize=4)
def load_fixture(outcome: str) -> dict[str, Any]:
    path = _fixtures_dir() / f"{outcome}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing demo static fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid demo static fixture: {path}")
    _verify_fixture_grounded(outcome, data)
    return data


def opening_question(outcome: DemoStaticOutcome) -> str:
    steps = _script_steps(outcome)
    return steps[0]["question"]


def next_question(outcome: DemoStaticOutcome, *, answered_count: int) -> str | None:
    """Question to show after `answered_count` user answers."""
    steps = _script_steps(outcome)
    if answered_count < 0:
        return opening_question(outcome)
    if answered_count >= len(steps):
        return None
    return steps[answered_count]["question"]


def structured_state_after(outcome: DemoStaticOutcome, *, answered_count: int) -> dict[str, Any]:
    fixture = load_fixture(outcome)
    turns = fixture.get("turns") or []
    if answered_count <= 0:
        return {}
    idx = min(answered_count, len(turns)) - 1
    state = turns[idx].get("structured_state_after") or {}
    return state if isinstance(state, dict) else {}


def apply_static_turn(
    *,
    chat: Any,
    token: str,
    link: CampaignProspect,
    conversation: AssessmentConversation,
    profile: AssessmentProfile,
    content: str,
    outcome: DemoStaticOutcome,
) -> dict[str, Any]:
    """Persist one static user answer and advance using the aligned Q→A script."""
    fixture = load_fixture(outcome)
    steps = _script_steps(outcome)
    if not steps:
        raise RuntimeError(f"Demo static script has no steps: {outcome}")

    messages = chat._ordered_messages(conversation.id)
    retrying = False
    if (
        messages
        and messages[-1].role == AssessmentMessageRole.USER
        and messages[-1].content.strip() == content.strip()
    ):
        retrying = True
    else:
        user_count_before = sum(1 for m in messages if m.role == AssessmentMessageRole.USER)
        chat._save_message(conversation, AssessmentMessageRole.USER, content)
        chat.db.commit()
        if user_count_before == 0:
            chat.events.assessment_started(token)
            chat.db.refresh(link)

    messages = chat._ordered_messages(conversation.id)
    answered = sum(1 for m in messages if m.role == AssessmentMessageRole.USER)
    state = structured_state_after(outcome, answered_count=answered)
    if state:
        profile.structured_state = state
        flag_modified(profile, "structured_state")
        chat.db.add(profile)
        chat.db.commit()
        chat.db.refresh(profile)

    is_last = answered >= len(steps)
    if is_last:
        from acquisition.services.assessment_extraction import ASSESSMENT_CLOSING_MESSAGE

        messages = chat._ordered_messages(conversation.id)
        if not (
            messages
            and messages[-1].role == AssessmentMessageRole.ASSISTANT
            and retrying
        ):
            chat._save_message(
                conversation,
                AssessmentMessageRole.ASSISTANT,
                ASSESSMENT_CLOSING_MESSAGE,
            )
            chat.db.commit()

        # Apply final captured state before freezing result/qualification.
        final_state = fixture.get("structured_state")
        if isinstance(final_state, dict) and final_state:
            profile.structured_state = final_state
            flag_modified(profile, "structured_state")

        result_payload = fixture.get("result_payload") or {}
        qualification = fixture.get("qualification") or {}
        # Ensure complete() will apply qualification path.
        profile.result_payload = None
        profile.qualification_status = QualificationStatus.PENDING
        chat.db.add(profile)
        chat.db.commit()
        chat.db.refresh(profile)

        chat.completion.complete(
            token,
            link,
            conversation,
            profile,
            generate_result_fn=lambda *_a, **_k: result_payload,
            generate_qualification_fn=lambda *_a, **_k: qualification,
        )
        chat.db.refresh(profile)
        # Force exact captured outcome (guards against validator drift).
        status_raw = str(
            fixture.get("qualification_status")
            or qualification.get("assessment_status")
            or qualification.get("status")
            or outcome
        ).lower()
        if status_raw in {
            QualificationStatus.QUALIFIED.value,
            QualificationStatus.UNQUALIFIED.value,
            QualificationStatus.PENDING.value,
        }:
            profile.qualification_status = QualificationStatus(status_raw)
            if isinstance(profile.qualification, dict):
                profile.qualification["status"] = status_raw
                profile.qualification["assessment_status"] = status_raw
                flag_modified(profile, "qualification")
            from acquisition.models.campaign import JourneyStage

            if status_raw == QualificationStatus.QUALIFIED.value:
                link.journey_stage = JourneyStage.QUALIFIED
            elif status_raw == QualificationStatus.UNQUALIFIED.value:
                link.journey_stage = JourneyStage.UNQUALIFIED_EXIT
            chat.db.add(profile)
            chat.db.add(link)
            chat.db.commit()

        try:
            from acquisition.services.personalized_value_service import PersonalizedValueService

            PersonalizedValueService(
                chat.db, openai_call=chat._call_responses
            ).ensure_for_profile(link, profile, conversation)
        except Exception:  # noqa: BLE001
            logger.warning("Demo static: personalized value skipped/failed")
        return chat.get_session(token)

    nxt = next_question(outcome, answered_count=answered)
    if not nxt:
        # Should not happen before the last answer; keep flow moving without
        # flashing the closing line as a fake question with a prefilled answer.
        raise RuntimeError(
            f"Demo static script missing question after {answered} answers ({outcome})"
        )
    messages = chat._ordered_messages(conversation.id)
    if not (
        messages
        and messages[-1].role == AssessmentMessageRole.ASSISTANT
        and retrying
    ):
        chat._save_message(conversation, AssessmentMessageRole.ASSISTANT, nxt)
        chat.db.commit()
    return chat.get_session(token)


def ensure_static_greeting(
    *,
    chat: Any,
    token: str,
    link: CampaignProspect,
    conversation: AssessmentConversation,
    outcome: DemoStaticOutcome,
) -> dict[str, Any]:
    """Ensure opening question matches the static script (before any user answers)."""
    if link.assessment_status == AssessmentStatus.COMPLETED:
        return chat.get_session(token)
    messages = chat._ordered_messages(conversation.id)
    user_count = sum(1 for m in messages if m.role == AssessmentMessageRole.USER)
    if user_count:
        return chat.get_session(token)

    opening = opening_question(outcome)
    if not messages:
        chat._save_message(conversation, AssessmentMessageRole.ASSISTANT, opening)
        chat.db.commit()
        return chat.get_session(token)

    # Replace sole assistant greeting so demo mode can switch before first answer.
    if len(messages) == 1 and messages[0].role == AssessmentMessageRole.ASSISTANT:
        if (messages[0].content or "").strip() != opening:
            messages[0].content = opening
            chat.db.add(messages[0])
            chat.db.commit()
    return chat.get_session(token)
