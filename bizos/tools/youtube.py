"""YouTube tool effects (read-only, the user's own Composio connection)."""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect


def _youtube(scope: RunScope):
    return get_connector("youtube", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


def _ids(args: dict[str, Any]) -> list[str]:
    raw = args.get("video_ids") or args.get("video_id") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


@effect("youtube_search_videos")
def youtube_search_videos(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).search_videos(
        str(args.get("query", "")), limit=int(args.get("limit", 5)), order=args.get("order")
    )


@effect("youtube_my_channel")
def youtube_my_channel(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).my_channel()


@effect("youtube_my_videos")
def youtube_my_videos(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).my_videos(limit=int(args.get("limit", 10)))


@effect("youtube_video_details")
def youtube_video_details(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).video_details(_ids(args))


@effect("youtube_video_comments")
def youtube_video_comments(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).video_comments(
        str(args.get("video_id", "")),
        limit=int(args.get("limit", 20)),
        order=str(args.get("order", "relevance")),
    )
