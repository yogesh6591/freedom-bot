"""Rewrite acquisition links so the feature lives under one FreedomBot origin."""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


def url_prefix() -> str:
    return (os.environ.get("ACQUISITION_URL_PREFIX") or "").rstrip("/")


class AcquisitionPrefixMiddleware(BaseHTTPMiddleware):
    """Prefix absolute URLs when this app is mounted inside FreedomBot core."""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        prefix = url_prefix()
        response = await call_next(request)
        if not prefix:
            return response

        location = response.headers.get("location")
        if location and location.startswith("/") and not location.startswith(prefix + "/"):
            response.headers["location"] = prefix + location

        content_type = response.headers.get("content-type", "")
        if not any(part in content_type for part in ("text/html", "javascript", "text/css")):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="ignore")
        for needle in (
            'href="/',
            'action="/',
            'src="/',
            'hx-post="/',
            'hx-get="/',
            'hx-put="/',
            'hx-patch="/',
            'hx-delete="/',
            'fetch("/',
            "fetch('/",
            "url('/",
            'url("/',
            'location.href = "/',
            "location.href = '/",
            'data-fb-booking-status-url="/',
            'data-fb-booked-url="/',
        ):
            text = text.replace(needle, needle[:-1] + prefix + "/")
        text = text.replace(prefix + prefix, prefix)
        headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
        return Response(content=text, status_code=response.status_code, headers=headers)
