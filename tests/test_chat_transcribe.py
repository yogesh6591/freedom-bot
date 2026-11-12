"""POST /api/chat/transcribe — local whisper.cpp (OpenAI base.en)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, client) -> str:
    response = app_client.post(
        "/api/auth/login",
        json={
            "email": f"admin@{client.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client.slug,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response | Exception, **_kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, files=None, data=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_transcribe_requires_auth(app_client):
    from app.main import app

    with TestClient(app) as anonymous:
        response = anonymous.post(
            "/api/chat/transcribe",
            files={"file": ("recording.webm", b"fake-audio", "audio/webm")},
        )
    assert response.status_code == 401


def test_transcribe_rejects_empty(app_client, client_a):
    token = _login(app_client, client_a)
    response = app_client.post(
        "/api/chat/transcribe",
        headers=_auth(token),
        files={"file": ("recording.webm", b"", "audio/webm")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_transcribe_calls_local_whisper(app_client, client_a, monkeypatch):
    monkeypatch.setattr("bizos.settings.whisper_url", lambda: "http://whisper:8080")
    token = _login(app_client, client_a)
    upstream = httpx.Response(200, json={"text": "  show me Ethan Parker  "})

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _FakeAsyncClient(upstream, **kw)):
        response = app_client.post(
            "/api/chat/transcribe",
            headers=_auth(token),
            files={"file": ("recording.webm", b"not-empty-audio-bytes", "audio/webm")},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "show me Ethan Parker"}


def test_transcribe_busy_returns_429(app_client, client_a):
    from app.api import chat as chat_api

    token = _login(app_client, client_a)
    assert chat_api._transcribe_lock.acquire(blocking=False)
    try:
        response = app_client.post(
            "/api/chat/transcribe",
            headers=_auth(token),
            files={"file": ("recording.webm", b"not-empty-audio-bytes", "audio/webm")},
        )
    finally:
        chat_api._transcribe_lock.release()

    assert response.status_code == 429


def test_transcribe_timeout_maps_to_504(app_client, client_a, monkeypatch):
    monkeypatch.setattr("bizos.settings.whisper_url", lambda: "http://whisper:8080")
    token = _login(app_client, client_a)

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kw: _FakeAsyncClient(httpx.TimeoutException("slow"), **kw),
    ):
        response = app_client.post(
            "/api/chat/transcribe",
            headers=_auth(token),
            files={"file": ("recording.webm", b"not-empty-audio-bytes", "audio/webm")},
        )

    assert response.status_code == 504
