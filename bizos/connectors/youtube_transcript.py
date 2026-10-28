"""
YouTube Transcripts
===================

The spoken words of a video, so the assistant can summarize it, answer questions about it or compare
videos. Two sources, tried in order:

1. **The user's own channel** through their Composio YouTube connection (caption tracks they own).
   YouTube only lets the channel owner download caption tracks this way.
2. **Any public video** with captions, through ``youtube-transcript-api``. That library reads the
   captions YouTube shows on the watch page; it is unofficial and YouTube may block server IPs.

The transcript is external content and is neutralized before the model sees it.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from bizos.connectors import composio
from bizos.connectors.base import ConnectorError
from bizos.connectors.untrusted import neutralize
from bizos.tenancy.context import TenantContext

TEXT_LIMIT = 30000  # characters handed to the model
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(value: str) -> Optional[str]:
    """The 11-character id from an id or any YouTube URL (watch, youtu.be, shorts, embed, live)."""
    text = str(value or "").strip()
    if _VIDEO_ID.match(text):
        return text
    try:
        url = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return None
    host = (url.hostname or "").lower()
    if host.endswith("youtu.be"):
        candidate = url.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        candidate = (parse_qs(url.query).get("v") or [""])[0]
        if not candidate:
            parts = [p for p in url.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]
    else:
        return None
    return candidate if _VIDEO_ID.match(candidate or "") else None


def _srt_to_text(raw: str) -> str:
    """Caption-file text without numbering, timestamps or WebVTT headers."""
    lines = []
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s or s.isdigit() or "-->" in s or s.upper().startswith(("WEBVTT", "KIND:", "LANGUAGE:")):
            continue
        lines.append(re.sub(r"<[^>]+>", "", s))
    return " ".join(lines)


def _items(data: Any) -> list[dict[str, Any]]:
    inner = data.get("response_data") if isinstance(data, dict) and isinstance(data.get("response_data"), dict) else data
    if isinstance(inner, dict) and isinstance(inner.get("items"), list):
        return [i for i in inner["items"] if isinstance(i, dict)]
    return []


def _longest_text(data: Any) -> str:
    """The caption text from a download result, whatever key it comes back under."""
    if isinstance(data, str):
        return data
    best = ""
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, str) and len(value) > len(best):
            best = value
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return best


def _from_own_channel(owner: TenantContext, vid: str, language: Optional[str]) -> Optional[tuple[str, str]]:
    """(text, language) from the user's own caption tracks, or None when there are none for this video."""
    if not composio.connected_toolkit(owner, "youtube"):
        return None
    tracks = _items(composio.execute(owner, "youtube", "YOUTUBE_LIST_CAPTION_TRACK",
                                     {"video_id": vid, "part": "snippet"}))
    if not tracks:
        return None

    def rank(track: dict[str, Any]) -> tuple[int, int]:
        snippet = track.get("snippet") or {}
        lang = str(snippet.get("language") or "")
        return (0 if language and lang.lower().startswith(language.lower()) else 1,
                0 if str(snippet.get("trackKind") or "").lower() != "asr" else 1)

    track = sorted(tracks, key=rank)[0]
    data = composio.execute(owner, "youtube", "YOUTUBE_LOAD_CAPTIONS", {"id": str(track.get("id")), "tfmt": "srt"})
    text = _srt_to_text(_longest_text(data))
    return (text, str((track.get("snippet") or {}).get("language") or "")) if text else None


def _from_public_captions(vid: str, language: Optional[str]) -> tuple[str, str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover - pinned in requirements.txt
        raise ConnectorError("Public video transcripts are not installed on this server.") from exc
    languages = [language, "en"] if language and language != "en" else ["en"]
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(vid, languages=languages)
    except Exception:  # noqa: BLE001 - fall back to whatever language the video has
        try:
            listing = api.list(vid)
            transcript = next(iter(listing))
            fetched = transcript.fetch()
        except Exception as exc:  # noqa: BLE001 - the library raises many specific types
            raise ConnectorError(
                f"No transcript is available for video {vid}: captions may be turned off, the video may be "
                f"private, or YouTube blocked the request ({type(exc).__name__})."
            ) from exc
    text = " ".join(str(getattr(s, "text", "") or "").replace("\n", " ") for s in fetched)
    return text, str(getattr(fetched, "language_code", "") or "")


def fetch(owner: TenantContext, video: str, *, language: Optional[str] = None) -> dict[str, Any]:
    """The transcript of one video as ``owner`` (their own captions first, then public captions)."""
    vid = video_id(video)
    if not vid:
        raise ConnectorError("video must be a YouTube video id (11 characters) or a YouTube link.")
    found: Optional[tuple[str, str]] = None
    source = ""
    try:
        found = _from_own_channel(owner, vid, language)
        source = "your channel's captions"
    except ConnectorError:
        found = None  # not the user's video, or captions not downloadable: try public captions
    if not found:
        found = _from_public_captions(vid, language)
        source = "public captions"
    text, lang = found
    text, findings = neutralize(" ".join(text.split()))
    return {
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "source": source,
        "language": lang or language or "",
        "characters": len(text),
        "truncated": len(text) > TEXT_LIMIT,
        "transcript": text[:TEXT_LIMIT],
        **({"_untrusted_findings": [{"kind": k, "match": m} for k, m in findings]} if findings else {}),
        "_untrusted_source": "youtube_transcript",
    }
