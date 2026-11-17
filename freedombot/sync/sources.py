"""One fetcher per source, built on the existing Composio connectors (freedombot/connectors/composio.py).

Each fetcher takes the user's connector and the source's checkpoint and returns ``(items, checkpoint)``.
Gmail is incremental (only messages newer than the checkpoint); Calendar, YouTube and HubSpot re-read a
recent window and rely on the content hash, so changed records are updated and unchanged ones skipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from freedombot.sync.store import SyncItem, _when

#: First Gmail sync looks this far back; later syncs only past the checkpoint.
GMAIL_BACKFILL = "newer_than:30d"
GMAIL_INCREMENTAL = "newer_than:2d"


def _ok(result: Any) -> list[dict[str, Any]]:
    if not getattr(result, "ok", False):
        raise RuntimeError(getattr(result, "message", None) or getattr(result, "summary", "") or "request failed")
    data = result.data
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def gmail(connector: Any, checkpoint: Optional[str]) -> tuple[list[SyncItem], Optional[str]]:
    rows = _ok(connector.search_threads(GMAIL_INCREMENTAL if checkpoint else GMAIL_BACKFILL, limit=25))
    floor = _when(checkpoint)
    items, newest = [], floor
    for row in rows:
        sent = _when(row.get("sent_at"))
        if floor is not None and sent is not None and sent <= floor:
            continue
        if sent is not None and (newest is None or sent > newest):
            newest = sent
        sender, to, subject = row.get("sender") or "", row.get("to") or "", row.get("subject") or "(no subject)"
        items.append(SyncItem(
            source="gmail", kind="email", source_id=str(row.get("message_id") or ""), title=subject,
            body=f"From: {sender}\nTo: {to}\nSubject: {subject}\n\n{row.get('body') or ''}",
            occurred_at=sent.isoformat() if sent else None,
            data={"thread_id": row.get("thread_id"), "from": sender, "to": to, "labels": row.get("labels") or []},
        ))
    return items, newest.isoformat() if newest else checkpoint


def calendar(connector: Any, checkpoint: Optional[str]) -> tuple[list[SyncItem], Optional[str]]:
    now = datetime.now(timezone.utc)
    rows = _ok(connector.list_events(start=(now - timedelta(days=1)).isoformat(),
                                     end=(now + timedelta(days=30)).isoformat(), limit=100))
    items = []
    for row in rows:
        attendees = ", ".join(row.get("attendees") or [])
        items.append(SyncItem(
            source="calendar", kind="event", source_id=str(row.get("id") or ""), title=row.get("title") or "",
            body=(f"{row.get('title') or ''}\nWhen: {row.get('starts_at')} to {row.get('ends_at')}\n"
                  f"Where: {row.get('location') or '-'}\nAttendees: {attendees or '-'}\n\n{row.get('description') or ''}"),
            occurred_at=row.get("starts_at"),
            data={k: row.get(k) for k in ("starts_at", "ends_at", "location", "attendees", "organizer", "meeting_link")
                  if row.get(k) is not None},
        ))
    return items, now.isoformat()


def youtube(connector: Any, checkpoint: Optional[str]) -> tuple[list[SyncItem], Optional[str]]:
    videos = _ok(connector.my_videos(limit=10))
    items = []
    for video in videos:
        stats = {k: video.get(k) for k in ("views", "likes", "comments") if video.get(k) is not None}
        items.append(SyncItem(
            source="youtube", kind="video", source_id=str(video.get("video_id") or ""), title=video.get("title") or "",
            body=f"{video.get('title') or ''}\nPublished: {video.get('published_at')}\n"
                 + ", ".join(f"{k}: {v}" for k, v in stats.items()),
            occurred_at=video.get("published_at"), data={"url": video.get("url"), **stats},
        ))
    for video in videos[:3]:
        result = connector.video_comments(str(video.get("video_id") or ""), limit=20, order="time")
        for comment in (result.data if getattr(result, "ok", False) and isinstance(result.data, list) else []):
            if not isinstance(comment, dict):
                continue
            items.append(SyncItem(
                source="youtube", kind="comment", source_id=str(comment.get("comment_id") or comment.get("id") or ""),
                title=f"Comment on {video.get('title') or 'a video'}",
                body=f"{comment.get('author') or 'Someone'}: {comment.get('text') or ''}",
                occurred_at=comment.get("published_at"),
                data={"video_id": video.get("video_id"), "likes": comment.get("likes")},
            ))
    return items, datetime.now(timezone.utc).isoformat()


def hubspot(connector: Any, checkpoint: Optional[str]) -> tuple[list[SyncItem], Optional[str]]:
    items = []
    for contact in _ok(connector.search_contacts("", limit=100)):
        name = " ".join(p for p in (contact.get("first_name"), contact.get("last_name")) if p) or contact.get("email") or ""
        items.append(SyncItem(
            source="hubspot", kind="contact", source_id=str(contact.get("id") or ""), title=name,
            body=(f"{name}\nEmail: {contact.get('email') or '-'}\nCompany: {contact.get('company') or '-'}\n"
                  f"Title: {contact.get('title') or '-'}\nLifecycle: {contact.get('lifecycle') or '-'}"),
            occurred_at=contact.get("updated_at"),
            data={k: contact.get(k) for k in ("email", "company", "title", "lifecycle", "phone") if contact.get(k)},
        ))
    for deal in _ok(connector.search_deals(limit=200)):
        items.append(SyncItem(
            source="hubspot", kind="deal", source_id=str(deal.get("id") or ""), title=deal.get("name") or "",
            body=(f"{deal.get('name') or ''}\nStage: {deal.get('stage') or '-'}\nAmount: {deal.get('amount')}\n"
                  f"Close date: {deal.get('close_date') or '-'}"),
            occurred_at=deal.get("close_date"),
            data={k: deal.get(k) for k in ("stage", "amount", "close_date", "pipeline") if deal.get(k) is not None},
        ))
    return items, datetime.now(timezone.utc).isoformat()


#: source -> (connector category, fetcher)
FETCHERS: dict[str, tuple[str, Callable[[Any, Optional[str]], tuple[list[SyncItem], Optional[str]]]]] = {
    "gmail": ("email", gmail),
    "calendar": ("calendar", calendar),
    "youtube": ("youtube", youtube),
    "hubspot": ("crm", hubspot),
}
