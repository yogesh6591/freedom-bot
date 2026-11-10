"""Shape a show_assessment report into a reply that answers only what the user asked."""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("bizos.agents.assessment_reply")

ASSESSMENT_REPLY_INSTRUCTIONS = """
You write the chat reply after an assessment lookup tool has already run.

You receive the user's message and the tool's report.

Rules:
- Answer only what the user asked. Use only facts from the report. Drop everything else.
- When they ask for status or progress, keep the short status layout: the result line first, then at most four or five short one-line bullets. Do not collapse that into a paragraph.
- When they ask for the details or the full report, keep the full detail from the report.
- When they ask only for the pre-call brief and a brief link is in the report, reply with one short line naming the person and offering the brief, then the link alone on the next line. No brief points, no assessment status, no other sections.
- When they ask only for the pre-call brief and the report says the brief is not ready, reply with exactly: No pre-call brief yet: not booked.
- Do not invent facts that are not in the report. If the report does not contain what they asked for, say that briefly.
- No preamble. Reply with only the answer.
""".strip()


def shape_assessment_reply(user_message: str, report: str, *, model: Any = None) -> str:
    """One model pass: user message + report → reply that keeps only what was asked.

    On failure, returns the report unchanged so the user still sees the tool result.
    """
    report = (report or "").strip()
    if not report:
        return ""
    query = (
        f"User message:\n{(user_message or '').strip() or '(empty)'}\n\n"
        f"Assessment report:\n{report}"
    )
    try:
        from agno.agent import Agent

        from app.assistant.assistant import _model
        from bizos import settings as app_settings

        agent = Agent(
            name="bizos-assessment-reply",
            id="bizos-assessment-reply",
            model=model or _model(app_settings.assistant_reasoning_effort()),
            instructions=ASSESSMENT_REPLY_INSTRUCTIONS,
            add_history_to_context=False,
            enable_agentic_memory=False,
            telemetry=False,
            store_events=False,
        )
        output = agent.run(input=query)
    except Exception as exc:  # noqa: BLE001 - never fail the chat turn on a shaping miss
        log.warning("assessment reply shaping failed: %s", exc)
        return report
    content = getattr(output, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    text = str(content or "").strip()
    return text or report


def assessment_reports(results: list) -> list[str]:
    """Successful show_assessment report bodies from this turn's tool results."""
    out: list[str] = []
    for r in results:
        if getattr(r, "tool", None) != "show_assessment" or getattr(r, "status", None) != "success":
            continue
        body = r.data if isinstance(getattr(r, "data", None), str) else getattr(r, "summary", None)
        if (body or "").strip():
            out.append(body.strip())
    return out
