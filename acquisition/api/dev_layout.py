"""Temporary layout tuner — drag-resize journey shell, persist for the agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from acquisition.auth.admin import require_admin_api

router = APIRouter(
    prefix="/api/dev",
    tags=["dev-layout"],
    dependencies=[Depends(require_admin_api)],
)

LAYOUT_FILE = Path(__file__).resolve().parents[1] / ".layout-tune.json"


class LayoutTunePayload(BaseModel):
    width_px: float = Field(..., ge=320, le=2400)
    card_min_height_px: float = Field(..., ge=200, le=1600)
    page: str | None = None
    viewport_width: float | None = None
    viewport_height: float | None = None


@router.get("/layout-tune")
def get_layout_tune() -> dict[str, Any]:
    if not LAYOUT_FILE.exists():
        return {"ok": False, "saved": None, "path": str(LAYOUT_FILE)}
    try:
        import json

        data = json.loads(LAYOUT_FILE.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": str(LAYOUT_FILE)}
    return {"ok": True, "saved": data, "path": str(LAYOUT_FILE)}


@router.post("/layout-tune")
def save_layout_tune(payload: LayoutTunePayload) -> JSONResponse:
    import json
    from datetime import datetime, timezone

    data = {
        "width_px": round(payload.width_px),
        "card_min_height_px": round(payload.card_min_height_px),
        "page": payload.page,
        "viewport_width": payload.viewport_width,
        "viewport_height": payload.viewport_height,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    LAYOUT_FILE.write_text(json.dumps(data, indent=2) + "\n")
    return JSONResponse({"ok": True, "saved": data, "path": str(LAYOUT_FILE)})
