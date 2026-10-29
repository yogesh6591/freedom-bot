"""Store Admin-uploaded demo videos under app/static/demo/."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

DEMO_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "demo"
ALLOWED_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".m4v"})
MAX_DEMO_BYTES = 250 * 1024 * 1024  # 250 MB

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class DemoVideoUploadError(ValueError):
    """Invalid or oversized demo upload."""


def packaged_executive_demo_url() -> str | None:
    """Return URL for the checked-in executive demo mp4 when present."""
    name = "FreedomBot_Executive_Demo_v1.mp4"
    if (DEMO_STATIC_DIR / name).is_file():
        return f"/static/demo/{name}"
    return None


_YOUTUBE = re.compile(
    r"^https://(?:www\.|m\.)?(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#/].*)?$",
    re.I,
)
_VIMEO = re.compile(r"^https://(?:www\.)?(?:vimeo\.com/|player\.vimeo\.com/video/)(\d+)(?:[?&#/].*)?$", re.I)
_DIRECT_FILE = re.compile(r"^https://[^\s?#]+\.(?:mp4|webm|mov|m4v)(?:\?[^\s#]*)?$", re.I)


def demo_player(url: str | None) -> tuple[str, str]:
    """(src, kind) for the demo page: YouTube and Vimeo links become embeds (kind "iframe"); an uploaded file
    or a direct video link plays in a <video> tag (kind "video"); a placeholder page stays an iframe."""
    raw = (url or "").strip()
    lower = raw.lower()
    if m := _YOUTUBE.match(raw):
        return f"https://www.youtube-nocookie.com/embed/{m.group(1)}", "iframe"
    if m := _VIMEO.match(raw):
        return f"https://player.vimeo.com/video/{m.group(1)}", "iframe"
    if lower.endswith((".html", ".htm")) or "placeholder" in lower:
        return raw, "iframe"
    return raw, "video"


def is_embed_link(url: str | None) -> bool:
    """A YouTube or Vimeo link: it plays, but the page cannot tell when it ends."""
    return bool(_YOUTUBE.match((url or "").strip()) or _VIMEO.match((url or "").strip()))


def is_valid_demo_link(url: str | None) -> bool:
    """What the chat may set: a YouTube or Vimeo link, or a direct https link to a video file."""
    raw = (url or "").strip()
    return bool(_YOUTUBE.match(raw) or _VIMEO.match(raw) or _DIRECT_FILE.match(raw))


def is_placeholder_demo_url(url: str | None) -> bool:
    raw = (url or "").strip().lower()
    return not raw or "placeholder" in raw or raw.endswith((".html", ".htm"))


def save_uploaded_demo_video(upload: UploadFile) -> str:
    """Write upload to static/demo and return the public /static/demo/… path."""
    original = (upload.filename or "").strip()
    if not original:
        raise DemoVideoUploadError("Choose a video file to upload.")

    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DemoVideoUploadError("Upload an MP4, WebM, or MOV video.")

    stem = _SAFE_NAME.sub("_", Path(original).stem).strip("._-") or "demo"
    filename = f"{stem}{ext}"
    DEMO_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEMO_STATIC_DIR / filename

    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DEMO_BYTES:
                    raise DemoVideoUploadError("Video is too large (max 250 MB).")
                out.write(chunk)
    except DemoVideoUploadError:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    if size == 0:
        dest.unlink(missing_ok=True)
        raise DemoVideoUploadError("Uploaded file was empty.")

    return f"/static/demo/{filename}"


ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_POSTER_BYTES = 5 * 1024 * 1024  # 5 MB


def save_uploaded_poster_image(upload: UploadFile) -> str:
    """Write a poster/thumbnail image to static/demo and return the public path."""
    original = (upload.filename or "").strip()
    if not original:
        raise DemoVideoUploadError("Choose an image file to upload.")

    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise DemoVideoUploadError("Upload a JPG, PNG, or WebP image.")

    stem = _SAFE_NAME.sub("_", Path(original).stem).strip("._-") or "poster"
    filename = f"poster_{stem}{ext}"
    DEMO_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEMO_STATIC_DIR / filename

    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_POSTER_BYTES:
                    raise DemoVideoUploadError("Image is too large (max 5 MB).")
                out.write(chunk)
    except DemoVideoUploadError:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    if size == 0:
        dest.unlink(missing_ok=True)
        raise DemoVideoUploadError("Uploaded file was empty.")

    return f"/static/demo/{filename}"
