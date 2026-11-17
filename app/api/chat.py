"""
Chat Routes
===========

§26 ``/api/chat``.

The agent is constructed per request and bound to exactly one tenant (see
:mod:`freedombot.agents.agent` for why). The response carries the structured signals
the UI needs — citations, tool activity, whether something is awaiting approval —
without dumping raw traces at ordinary users (§17).
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
import os
import queue
import re
import threading
from typing import Any, Callable, Iterator, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, run_scope
from freedombot.actions import store as actions
from freedombot import chat_attachments, chat_history
from freedombot.agents import grounding
from freedombot.agents.agent import ModelNotConfigured, build_agent
from freedombot.agents.claims import enforce_claims
from freedombot.agents.instructions import assistant_name
from freedombot.agents.persona import AUTO, enforce_persona, route
from freedombot.rbac.registry import TOOL_SPECS
from freedombot.control.models import ClientSettings
from freedombot.policy.modes import describe as describe_mode
from freedombot.tenancy.context import TenantContext
from freedombot.types import ActionStatus
from freedombot.util.ids import new_id

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger(__name__)
#: Seconds of silence after which the chat stream sends a keep-alive comment.
KEEPALIVE_SEC = 15


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: Optional[str] = None
    #: "auto" (the default) routes each turn to the domain it is about, inside
    #: one continuous session. A named domain pins the turn to that domain.
    domain: str = AUTO
    #: The domain the previous turn was routed to, so short follow-ups stay put.
    previous_domain: Optional[str] = None
    #: May only narrow the workspace mode.
    execution_mode: Optional[str] = None
    #: Files uploaded with POST /api/chat/attachments to send with this message.
    attachment_ids: list[str] = Field(default_factory=list, max_length=chat_attachments.MAX_PER_MESSAGE)


class EditMessageRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=20000)
    #: Same as ChatRequest.domain.
    domain: str = AUTO


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.get("/context")
def chat_context(
    domain: str = AUTO,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """What the agent can do right now — drives the UI's mode banner and tool list.

    Lists every process the caller may *see* in their enabled domains, each
    flagged with whether they may *run* it (FB-037).
    """
    from freedombot.agents.surface import visible_specs
    from freedombot.domains.packs import enabled_packs, get_pack

    with bound(ctx):
        scope = run_scope(ctx, settings, domain="general" if domain == AUTO else domain)
        if domain == AUTO:
            packs = enabled_packs(settings.enabled_domains)
        else:
            packs = [p for p in (get_pack(domain), get_pack("general")) if p is not None]
        runnable = {s.name for s in visible_specs(scope, packs)}
        in_packs = {spec.name for p in packs for spec in p.tool_specs()}
        viewable = [
            s for s in TOOL_SPECS
            if s.name in in_packs and s.implemented and s.can_view(ctx)
        ]
        return {
            "mode": str(scope.mode),
            "mode_description": describe_mode(scope.mode),
            "domain": domain,
            "enabled_domains": settings.enabled_domains,
            "assistant_name": assistant_name(settings),
            "role": str(ctx.primary_role),
            "data_scopes": sorted(ctx.visible_areas),
            "tools": [
                {
                    "name": s.name,
                    "title": s.title,
                    "write": s.write,
                    "risk": str(s.risk),
                    "category": s.category,
                    "can_view": True,
                    "can_run": s.name in runnable,
                    "tool_mode": settings.tool_modes.get(s.name),
                }
                for s in viewable
            ],
            "sending_accounts": _sending_accounts(ctx),
            "pending_approvals": len(actions.pending_approvals(ctx=ctx)),
            "inefficiency_alerts": _open_alerts(settings, ctx),
        }


def _sending_accounts(ctx: TenantContext) -> list[str]:
    if not ctx.is_admin:
        return []
    return []  # outreach sends from the company Gmail, not from a list of sending accounts


def _open_alerts(settings: ClientSettings, ctx: TenantContext) -> list[dict[str, Any]]:
    try:
        from freedombot.insights import open_alerts, refresh_if_stale

        settings = refresh_if_stale(client_id=ctx.client_id, settings=settings, ctx=ctx)
        return open_alerts(settings, ctx)
    except Exception:
        return []


class EmailDraftUpdate(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    email_draft: str = Field(min_length=1, max_length=20000)
    email_subject: str = Field(default="", max_length=500)
    email_body: str = Field(default="", max_length=20000)
    email_from: str = Field(default="", max_length=200)
    email_address: str = Field(default="", max_length=500)


class EmailCardPersonPatch(BaseModel):
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    company: str = Field(default="", max_length=255)


class EmailCardUpdate(BaseModel):
    """What the user changed in the email card. Only what changed needs to be sent."""

    session_id: str = Field(min_length=1, max_length=80)
    subject: str | None = Field(default=None, max_length=1000, description="template subject, blanks visible")
    body: str | None = Field(default=None, max_length=20000, description="template body, blanks visible")
    people: dict[str, EmailCardPersonPatch] | None = None
    name_scope: str = Field(default="", max_length=20, description='"forever" or "this_email"')
    scope: str | None = Field(default=None, description='"everyone", "keep", or "" to clear')


@router.post("/email-draft")
def update_email_draft(
    body: EmailDraftUpdate, ctx: TenantContext = Depends(current_context)
) -> dict[str, str]:
    """Save an email the user edited in the chat. The next turn sends this version."""
    from app.outreach.card import edit_draft

    with bound(ctx):
        try:
            # The text is one person's rendered email: real names go back to blanks before it is saved.
            chat_history.edit_latest_assistant_meta(
                ctx,
                body.session_id,
                lambda meta: edit_draft(
                    ctx, meta, subject=body.email_subject, body=body.email_body, rendered=True
                ),
            )
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except ValueError:
            try:  # no template draft on that turn: keep the plain save
                chat_history.set_latest_email_draft(
                    ctx,
                    body.session_id,
                    body.email_draft.strip(),
                    {
                        "email_subject": body.email_subject.strip(),
                        "email_body": body.email_body.strip(),
                        "email_from": body.email_from.strip(),
                        "email_address": body.email_address.strip(),
                    },
                )
            except chat_history.SessionNotFound as exc:
                raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return {"email_draft": body.email_draft.strip()}


@router.post("/email-card")
def update_email_card(
    body: EmailCardUpdate, ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    """Apply a change made in the email card and return the refreshed view.

    Names typed for a person belong to that person's email only. With ``name_scope="forever"`` the
    stored name is also replaced, so every later campaign uses it.
    """
    from app.outreach.card import edit_draft
    from app.outreach.records import replace_person_name

    if body.subject is not None and not body.subject.strip():
        raise HTTPException(status_code=422, detail="Subject and body are required")
    if body.body is not None and not body.body.strip():
        raise HTTPException(status_code=422, detail="Subject and body are required")
    people = {
        email.strip(): patch.model_dump()
        for email, patch in (body.people or {}).items()
        if email.strip() and "@" in email
    }
    with bound(ctx):
        try:
            if body.name_scope.strip().lower() == "forever":
                for email, patch in people.items():
                    replace_person_name(
                        client_id=ctx.client_id,
                        email=email,
                        first_name=str(patch.get("first_name") or ""),
                        last_name=str(patch.get("last_name") or ""),
                        company=str(patch.get("company") or ""),
                    )
            view = chat_history.edit_latest_assistant_meta(
                ctx,
                body.session_id,
                lambda meta: edit_draft(
                    ctx, meta, subject=body.subject, body=body.body, people=people or None, scope=body.scope
                ),
            )
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"email_view": view}


def _engine() -> str:
    """``legacy`` restores the previous per-domain agent. Anything else uses the unified Agno assistant."""
    return os.environ.get("ASSISTANT_ENGINE", "unified").strip().lower()


def _edit_chat_request(body: EditMessageRequest, ctx: TenantContext, session_id: str) -> ChatRequest:
    """Same ChatRequest a new turn would send. Acquisition is part of the one assistant, so nothing is pinned."""
    domain = (body.domain or AUTO).strip().lower() or AUTO
    if domain == "acquisition":
        domain = AUTO
    return ChatRequest(message=body.message, session_id=session_id, domain=domain)


def _dispatch(body: ChatRequest, ctx: TenantContext, settings: ClientSettings,
              emit: Optional[Callable[[dict], None]] = None) -> dict[str, Any]:
    """Every chat turn goes through the routed pipeline (``app.assistant.v2``).

    ``ASSISTANT_ENGINE=legacy`` keeps the old per-domain agent as an emergency path.
    ``emit`` streams progress from the v2 pipeline.
    """
    from app.assistant import v2
    from freedombot import usage

    if _engine() == "legacy":
        return _legacy_chat(body, ctx, settings)
    started = time.monotonic()
    response: Optional[dict[str, Any]] = None
    with bound(ctx), usage.collect() as collected:
        try:
            response = v2.handle(body, ctx, settings, **({"emit": emit} if emit else {}))
            return response
        finally:
            # Audit > Token usage: one row per turn, saved even when the turn failed.
            usage.save(
                ctx,
                collected,
                query=body.message,
                session_id=(response or {}).get("session_id") or body.session_id,
                source="scheduled" if ctx.attributes.get("scheduled_task") else "chat",
                status="ok" if response is not None else "error",
                duration_ms=int((time.monotonic() - started) * 1000),
            )


@router.post("")
def chat(
    body: ChatRequest,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """One chat turn."""
    return _dispatch(body, ctx, settings)


async def _attach_video(file: UploadFile, filename: str, ctx: TenantContext) -> dict[str, Any]:
    """A demo or welcome video: saved on the server like an admin-panel upload; the assistant is given its link."""
    from starlette.concurrency import run_in_threadpool

    from acquisition.services.demo_video_storage import DemoVideoUploadError, save_uploaded_demo_video
    from freedombot.control import store as control

    if not control.build_context(control.get_user(ctx.user_id)).is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can attach a video.")
    try:
        url = await run_in_threadpool(save_uploaded_demo_video, file)
    except DemoVideoUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "The video could not be saved (mp4, webm, mov or m4v, up to 250 MB).") from exc
    with bound(ctx):
        return chat_attachments.save_video(ctx, filename=filename, size_bytes=int(getattr(file, "size", 0) or 0), url=url)


@router.post("/attachments", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Attach a PDF, Word (.docx), CSV, Excel (.xlsx) or text file to the next chat message.

    The text is read and checked now, with the same regulated-data rules as a typed message, so a file
    the Order/DPA does not allow is refused before it is stored or reaches a model.
    """
    if (file.filename or "").lower().endswith(chat_attachments.VIDEO_EXTENSIONS):
        return await _attach_video(file, file.filename, ctx)
    data = await file.read(chat_attachments.MAX_BYTES + 1)
    filename = file.filename or "file"
    try:
        extracted = chat_attachments.extract(filename, data)
    except chat_attachments.AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with bound(ctx):
        refusal = _regulated_data_refusal(extracted["text"], settings)
        if refusal:
            from app.assistant.assistant import _guardrail_audit

            _guardrail_audit(ctx, run_scope(ctx, settings, domain="general"), "", f"ATTACHMENT_{refusal[0]}")
            hint = ""
            if refusal[0] == "CHILD_DATA_BLOCKED":
                from freedombot.policy.classifier import _minor_match

                hit = _minor_match(extracted["text"])
                if hit:
                    hint = f' (The file contains "{hit}", which the child-data rule matched. Remove those lines and retry.)'
            raise HTTPException(status_code=422, detail=f"{filename} was not attached. {refusal[1]}{hint}")
        return chat_attachments.save(ctx, filename=filename, kind=extracted["kind"], size_bytes=len(data),
                                     body=extracted["text"], meta=extracted["meta"])


#: Chat-mic uploads stay small; whisper.cpp converts WebM/Opus via ffmpeg (--convert).
_TRANSCRIBE_MAX_BYTES = 10 * 1024 * 1024
_TRANSCRIBE_EXTENSIONS = (".webm", ".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".mpeg", ".mpga")
_TRANSCRIBE_TIMEOUT_SEC = 60.0
#: One in-flight transcription at a time (CPU whisper.cpp on a shared host).
_transcribe_lock = threading.Lock()


def _guess_audio_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".mp3") or lower.endswith(".mpga") or lower.endswith(".mpeg"):
        return "audio/mpeg"
    if lower.endswith(".mp4") or lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".ogg"):
        return "audio/ogg"
    return "audio/webm"


def _flatten_transcript(text: str) -> str:
    """Whisper often puts newlines between sentences; chat drafts stay one continuous line."""
    return " ".join(text.split())


def _text_from_whisper_payload(payload: Any) -> str:
    """Pull the transcript string out of whisper-server JSON (or plain text)."""
    if isinstance(payload, str):
        return _flatten_transcript(payload)
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return _flatten_transcript(text)
    # Some builds return segments only.
    segments = payload.get("transcription") or payload.get("segments") or []
    if isinstance(segments, list):
        parts = []
        for seg in segments:
            if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                parts.append(seg["text"].strip())
            elif isinstance(seg, str):
                parts.append(seg.strip())
        return _flatten_transcript(" ".join(p for p in parts if p))
    return ""


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, str]:
    """Turn a short voice recording into text with local whisper.cpp (OpenAI ``base.en``).

    The UI appends the result to whatever is already in the message box, so speaking mid-draft
    never replaces typed words. Auth is the same session cookie as every other chat call.
    """
    import httpx

    from freedombot import settings as freedombot_settings

    filename = (file.filename or "recording.webm").strip() or "recording.webm"
    lower = filename.lower()
    if not lower.endswith(_TRANSCRIBE_EXTENSIONS):
        # Browsers usually send webm/ogg; give ffmpeg/whisper a usable name if omitted.
        filename = "recording.webm"

    data = await file.read(_TRANSCRIBE_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Recording was empty.")
    if len(data) > _TRANSCRIBE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Recording is too large (max 10 MB).")

    if not _transcribe_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Another transcription is already running. Try again in a moment.",
        )

    url = f"{freedombot_settings.whisper_url()}/inference"
    try:
        try:
            async with httpx.AsyncClient(timeout=_TRANSCRIBE_TIMEOUT_SEC) as client:
                response = await client.post(
                    url,
                    files={"file": (filename, data, _guess_audio_content_type(filename))},
                    data={
                        "response_format": "json",
                        "temperature": "0.0",
                        "temperature_inc": "0.2",
                    },
                )
        except httpx.TimeoutException as exc:
            log.warning("chat.transcribe timeout user=%s url=%s", ctx.user_id, url)
            raise HTTPException(
                status_code=504,
                detail="Transcription timed out. Try a shorter recording.",
            ) from exc
        except httpx.HTTPError as exc:
            log.exception("chat.transcribe transport failed for user=%s", ctx.user_id)
            raise HTTPException(
                status_code=502,
                detail="Speech to text is unavailable right now. Try again in a moment.",
            ) from exc

        if response.status_code >= 400:
            log.error(
                "chat.transcribe upstream status=%s body=%s",
                response.status_code,
                (response.text or "")[:300],
            )
            raise HTTPException(
                status_code=502,
                detail="Could not transcribe the recording. Try again in a moment.",
            )

        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        text = _text_from_whisper_payload(payload)
        return {"text": text}
    finally:
        _transcribe_lock.release()


@router.post("/stream")
def chat_stream(
    body: ChatRequest,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> StreamingResponse:
    """One chat turn as Server-Sent Events, so the reply shows while it is written.

    Events (one JSON object per ``data:`` line): ``status`` (a tool is running), ``token`` (more of the
    reply, already passed through the claim gate), then ``done`` with exactly the response POST /api/chat
    returns, which replaces the streamed text, or ``error`` with an HTTP status and detail.
    The turn runs in its own thread with this request's context, so the tenant binding holds throughout.
    """
    events: queue.Queue = queue.Queue()
    context = contextvars.copy_context()

    def work() -> None:
        try:
            response = context.run(_dispatch, body, ctx, settings, events.put)
            events.put({"type": "done", "response": response})
        except HTTPException as exc:
            events.put({"type": "error", "status": exc.status_code, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - the stream must end with an event, never hang
            log.exception("chat stream failed")
            events.put({"type": "error", "status": 500, "detail": "Chat failed. Please try again."})
        finally:
            events.put(None)

    def stream() -> Iterator[str]:
        while True:
            try:
                event = events.get(timeout=KEEPALIVE_SEC)
            except queue.Empty:
                yield ": keep-alive\n\n"  # an SSE comment: keeps proxies from closing a long, quiet turn
                continue
            if event is None:
                return
            yield f"data: {json.dumps(jsonable_encoder(event))}\n\n"

    threading.Thread(target=work, name="chat-stream", daemon=True).start()
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _legacy_chat(
    body: ChatRequest,
    ctx: TenantContext,
    settings: ClientSettings,
) -> dict[str, Any]:
    session_id = body.session_id or new_id("ses")
    if body.session_id:
        try:
            chat_history.assert_can_continue(ctx, body.session_id)
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
    if body.domain == AUTO:
        domain = route(body.message, settings.enabled_domains, previous=body.previous_domain)
    else:
        domain = body.domain
    with bound(ctx):
        scope = run_scope(
            ctx,
            settings,
            domain=domain,
            requested_mode=body.execution_mode,
            session_id=session_id,
        )
        before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}
        grounded = grounding.analyze(ctx, body.message, settings.enabled_domains)
        match_ran = False
        keys = grounding.candidate_match_keys(grounded, body.message)
        if keys:
            from freedombot.tools.base import guarded_call

            outcome = guarded_call(
                scope, "hr_match_candidate", {"resume_key": keys[0], "opening_key": keys[1]}
            )
            if outcome.ok:
                match_ran = True
                grounded.text += (
                    "\n\n## Structured candidate comparison (computed by the platform)\n"
                    f"{outcome.message}\nResult: {outcome.data}\n"
                    "Present this comparison starting with the verdict in the computed words "
                    "('Likely fit' or 'Weak fit'), then must-haves met and what is missing. "
                    "State that it is assistive only and the hiring decision stays with a human."
                )

        refusal = _regulated_data_refusal(body.message, settings) or _licensed_advice_refusal(body.message, settings)
        output: Any = (refusal[1] if refusal else None) or grounding.restricted_candidate_reply(
            grounded, body.message
        )
        if refusal:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status=refusal[0],
                execution_mode=str(scope.mode),
            )
        if output is None:
            note = _licensed_advice_note(body.message, settings)
            if note:
                grounded.text += "\n\n" + note
            try:
                agent = build_agent(
                    scope, session_id=session_id, message=body.message, grounding_text=grounded.text
                )
            except ModelNotConfigured as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            try:
                output = agent.run(body.message)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"The agent could not complete: {exc}") from exc

        # Anything the turn created that a human now needs to look at.
        new_actions = [
            a.to_dict()
            for a in actions.list_actions(limit=200, ctx=ctx)
            if a.id not in before
        ]

        content, persona_fixed = enforce_persona(
            _separate_segments(str(getattr(output, "content", output) or "")),
            assistant_name(settings),
        )
        content, restriction_fixed = grounding.enforce_restrictions(
            content, grounded.hidden, answered_visible=bool(grounded.visible_keys)
        )
        content, conflict_fixed = grounding.enforce_conflicts(content, grounded.conflicts)
        content, package_fixed = grounding.enforce_package(
            content,
            grounded.unpurchased,
            company=getattr(settings, "company_name", "") or "",
            asked_for_action=grounded.unpurchased_action,
            answered_visible=bool(grounded.visible_keys),
        )
        activity = _tool_activity(output)
        content, claim_fixed = enforce_claims(
            content, message=body.message, activity=activity, new_actions=new_actions
        )
        if claim_fixed:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status="FALSE_ACTION_CLAIM_CORRECTED",
                execution_mode=str(scope.mode),
            )
        ran_match = match_ran or any(t["tool"] == "hr_match_candidate" and t["ok"] for t in activity)
        if ran_match and not re.search(
            r"\bhuman\b", content, re.I
        ):
            content = content.rstrip() + (
                "\n\nThis comparison is assistive only — the hiring decision stays with a human."
            )
        if restriction_fixed:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status="RESTRICTION_NOTED",
                execution_mode=str(scope.mode),
            )
        if conflict_fixed:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status="CONFLICT_ENFORCED",
                execution_mode=str(scope.mode),
            )
        if package_fixed:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                domain=",".join(sorted(grounded.unpurchased)),
                status="PACKAGE_SCOPE_NOTED",
                execution_mode=str(scope.mode),
            )
        if persona_fixed:
            from freedombot.audit.events import log
            from freedombot.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status="PERSONA_ENFORCED",
                execution_mode=str(scope.mode),
            )

        response = {
            "session_id": session_id,
            "content": content,
            "mode": str(scope.mode),
            "domain": domain,
            "routed": body.domain == AUTO,
            "tool_activity": activity,
            "actions": new_actions,
            "awaiting_approval": [
                a for a in new_actions if a["status"] == str(ActionStatus.PENDING_APPROVAL)
            ],
            "drafts": [a for a in new_actions if a["status"] == str(ActionStatus.DRAFT)],
        }
        _remember_turn(ctx, body.message, response)
        return response


#: Asking the assistant to keep, change, find or act on someone's details.
_HANDLE_INTENT = re.compile(
    r"\b(add|create|store|save|record|remember|register|enrol+|enter|log|put|upload|import|"
    r"update|look ?up|find|search|send|email|text|call|contact|schedule|book|onboard|sign up|"
    r"use|cover(?:ed|age)?|process)\b",
    re.I,
)

#: Concrete PHI identifiers in the message — refuse even without a "store/look up" verb,
#: so typed MRNs / labelled diagnoses are not answered from memory.
_PHI_PAYLOAD = re.compile(
    r"(?i)\b(?:mrn|medical record(?: number| no\.?)?)\s*[:#]?\s*[A-Z0-9]"
    r"|\bdiagnosis\s*:\s*\w+"
    r"|\b\d{3}-\d{2}-\d{4}\b"
)

#: Payment-card digit run (same shape the classifier uses).
_CARD_DIGITS = re.compile(r"\b(?:\d[ -]?){13,19}\b")

CHILD_DATA_REFUSAL = (
    "I can't process personal details about a child here. This workspace isn't authorized "
    "to handle children's personal data, so I won't add, store or look up this information "
    "— and please don't share more of it. If your contract covers a workflow for minors, an "
    "admin can arrange for it to be enabled."
)


def _child_data_refusal(message: str, settings: ClientSettings) -> Optional[str]:
    """M07-13: refuse a request to handle a child's personal data, before any model call.

    The policy engine also denies any tool call whose payload carries child data;
    this stops the assistant from asking for more of it in the first place.
    Child + personal markers (DOB, name, school) are enough — no "store" verb required.
    """
    if settings.order_dpa_allows_category("child") or getattr(settings, "allow_child_data", False):
        return None
    from freedombot.policy.classifier import classify_text

    if classify_text(message).contains_child_data:
        return CHILD_DATA_REFUSAL
    return None


PHI_REFUSAL = (
    "I can't process patient health information here. This workspace's Order/DPA doesn't "
    "allow PHI, so I won't add, store or look up these details — and please don't share "
    "more of them. An admin can update the Order/DPA setting if your contract covers PHI."
)

PHI_NO_BAA_REFUSAL = (
    "I can't process patient health information here. Your Order/DPA allows PHI, but the AI "
    "model this workspace uses ({vendor}) has no signed BAA, so PHI can't be sent to it — the "
    "same rule a dental practice follows with any vendor. Please don't share these details "
    "here; keep them in your practice system until a BAA-covered model is configured."
)

BIOMETRICS_REFUSAL = (
    "I can't process biometric identifiers (fingerprints, face or voice prints, iris scans) "
    "here. This workspace's Order/DPA doesn't allow biometric data, so I won't add, store or "
    "look it up. An admin can update the Order/DPA setting if your contract covers it."
)

CARD_DATA_REFUSAL = (
    "I can't process payment card data here. This workspace's Order/DPA doesn't allow card "
    "numbers or related payment details, so I won't add, store or look them up — and please "
    "don't share more of them. An admin can update the Order/DPA setting if your contract covers it."
)


SSN_REFUSAL = (
    "I can't process Social Security numbers here. This workspace's Order/DPA doesn't allow them, "
    "so I won't add, store or look them up. Please don't share more of them. An admin can update "
    "the Order/DPA setting if your contract covers it."
)

LICENSED_ADVICE_REFUSAL = (
    "I can't give {topic} advice here. That needs {expert}. I can still look up what your company "
    "has recorded or summarise a document for you."
)
_ADVICE_EXPERT = {
    "legal": "a lawyer",
    "tax": "a tax professional",
    "medical": "a doctor",
    "investment": "a licensed financial adviser",
}


def _licensed_advice_refusal(message: str, settings: ClientSettings) -> Optional[tuple[str, str]]:
    """Refuse a request for legal, tax, medical or investment advice, before any model call.

    Only for what the user asks in chat. A file or memory value may discuss these subjects freely.
    A message that also asks for other things is not refused whole: see :func:`_licensed_advice_note`.
    """
    from freedombot.policy.classifier import advice_is_one_part, licensed_advice_topic

    if settings.order_dpa_allows_category("licensed_advice"):
        return None
    topic = licensed_advice_topic(message)
    if topic is None or advice_is_one_part(message):
        return None
    return "LICENSED_ADVICE_BLOCKED", LICENSED_ADVICE_REFUSAL.format(topic=topic, expert=_ADVICE_EXPERT[topic])


def _licensed_advice_note(message: str, settings: ClientSettings) -> str:
    """When advice is one part of a bigger request: what to leave out, so the other parts still get answered."""
    from freedombot.policy.classifier import licensed_advice_topic

    topic = None if settings.order_dpa_allows_category("licensed_advice") else licensed_advice_topic(message)
    if topic is None:
        return ""
    return (f"## Licensed advice\nPart of this message asks for {topic} advice. Do not give that advice: say that "
            f"part needs {_ADVICE_EXPERT[topic]}. Answer every other part.")


def _regulated_data_refusal(
    message: str, settings: ClientSettings
) -> Optional[tuple[str, str]]:
    """FB-039: refuse regulated data the Order/DPA doesn't allow, before any model call.

    Returns ``(audit_status, reply)``. Dental BAA vendor pattern: PHI is never
    sent to a model vendor without a BAA, even when the Order/DPA allows PHI.
    The policy engine still denies tool calls carrying this data; this keeps it
    from reaching the model at all.
    """
    from freedombot.policy.classifier import classify_text, find_ssn, identifiable_phi

    child = _child_data_refusal(message, settings)
    if child:
        return "CHILD_DATA_BLOCKED", child

    found = classify_text(message)
    handling = bool(_HANDLE_INTENT.search(message or ""))
    phi_payload = bool(_PHI_PAYLOAD.search(message or ""))
    card_digits = bool(_CARD_DIGITS.search(message or ""))

    # SSN: the number itself in any sentence; the word alone only when someone wants it handled.
    if (find_ssn(message) or (handling and found.contains_ssn)) and not settings.order_dpa_allows_category("ssn"):
        return "SSN_BLOCKED", SSN_REFUSAL

    # PHI: handling intent, or a concrete identifier / labelled diagnosis in the message.
    if (
        (handling or phi_payload)
        and identifiable_phi(message)
        and not settings.phi_permitted()
    ):
        if not settings.order_dpa_allows_category("phi"):
            return "PHI_BLOCKED", PHI_REFUSAL
        vendor = "OpenAI" if settings.llm_vendor in {"openai", "open_ai"} else settings.llm_vendor
        return "PHI_NO_BAA_BLOCKED", PHI_NO_BAA_REFUSAL.format(vendor=vendor)

    # Card: digit PAN or classified card talk while handling / looking up.
    if (
        (handling or card_digits)
        and (found.contains_card_data or card_digits)
        and not settings.order_dpa_allows_category("card")
    ):
        return "CARD_DATA_BLOCKED", CARD_DATA_REFUSAL

    # Biometrics: strong biometric talk does not need a "store" verb ("use this fingerprint").
    if found.contains_biometrics and not settings.order_dpa_allows_category("biometrics"):
        return "BIOMETRICS_BLOCKED", BIOMETRICS_REFUSAL

    return None


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    """The fields the chat card shows — enough to redraw it from history."""
    return {
        k: action.get(k)
        for k in ("id", "title", "tool", "status", "risk_level", "policy_reason")
    }


def _remember_turn(ctx: TenantContext, message: str, response: dict[str, Any]) -> None:
    """Save the turn as shown. A storage failure must not lose the reply."""
    try:
        chat_history.record_turn(
            ctx,
            session_id=response["session_id"],
            user_message=message,
            reply=response["content"],
            domain=response["domain"],
            meta={
                "mode": response["mode"],
                "tool_activity": response["tool_activity"],
                "awaiting_approval": [_action_summary(a) for a in response["awaiting_approval"]],
                "drafts": [_action_summary(a) for a in response["drafts"]],
            },
        )
    except Exception as exc:  # pragma: no cover - logged, reply still returned
        from agno.utils.log import log_error

        log_error(f"chat history write failed: {type(exc).__name__}: {exc}")


@router.get("/sessions")
def list_sessions(
    limit: int = 50, ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    """The caller's own conversations in this workspace, newest first."""
    with bound(ctx):
        return {"items": chat_history.list_sessions(ctx, limit=limit)}


@router.delete("/sessions")
def delete_all_sessions(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Remove every conversation belonging to the caller."""
    with bound(ctx):
        deleted = chat_history.delete_all_sessions(ctx)
        chat_attachments.delete_session(ctx)
    return {"ok": True, "deleted": deleted}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Remove one of the caller's conversations."""
    with bound(ctx):
        try:
            chat_history.delete_session(ctx, session_id)
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        chat_attachments.delete_session(ctx, session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Rename one of the caller's conversations in the sidebar."""
    with bound(ctx):
        try:
            title = chat_history.rename_session(ctx, session_id, body.title)
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Title is required") from exc
    return {"ok": True, "session_id": session_id, "title": title}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """One of the caller's conversations, message by message."""
    with bound(ctx):
        try:
            return chat_history.get_session(ctx, session_id)
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post("/sessions/{session_id}/edit")
def edit_message(
    session_id: str,
    body: EditMessageRequest,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Replace a user message (and everything after it) and rerun from the new text.

    The deleted rows stay in the table until the new reply is stored, so a model
    failure (502) leaves the original conversation intact. Sent emails, approvals
    and tickets are not undone.
    """
    with bound(ctx):
        try:
            chat_history.assert_can_continue(ctx, session_id)
            ids = chat_history.tail_from_user_message(ctx, session_id, body.message_id)
        except chat_history.SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except chat_history.NotUserMessage as exc:
            raise HTTPException(status_code=400, detail="Only user messages can be edited") from exc
        request = _edit_chat_request(body, ctx, session_id)
        with chat_history.excluding_messages(ids):
            response = _dispatch(request, ctx, settings)
        chat_history.delete_messages(ctx, session_id, ids)
        return response


_GLUED = re.compile(r"(?<=[a-z0-9)][.!?])(?=[A-Z][a-z ])")
_SECTION_MARKER = re.compile(r"\[\[/?area(:\s*[a-z_]+\s*)?\]\]\n?", re.I)


def _separate_segments(text: str) -> str:
    """Text the model wrote before and after a tool call arrives concatenated
    ("…for review.I have prepared…"); put the break back. Section-access
    markers from memory are storage syntax, not something to show."""
    return _SECTION_MARKER.sub("", _GLUED.sub("\n\n", text))


def _tool_activity(output: Any) -> list[dict[str, Any]]:
    """Summarize tool calls for the UI without exposing raw arguments.

    Arguments can contain customer content; the activity strip only needs to say
    *what* ran and whether it succeeded. The full record, redacted, is in the
    audit log for those permitted to see it.
    """
    activity: list[dict[str, Any]] = []
    for call in getattr(output, "tools", None) or []:
        name = getattr(call, "tool_name", None) or (call.get("tool_name") if isinstance(call, dict) else None)
        if not name:
            continue
        error = getattr(call, "tool_call_error", None)
        if isinstance(call, dict):
            error = call.get("tool_call_error")
        activity.append({"tool": name, "ok": not error})
    return activity
