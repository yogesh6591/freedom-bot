"""Stripe return URLs must be the public site, not the Docker API hostname."""

from types import SimpleNamespace

import pytest

from acquisition.services.assessment_tokens import (
    PublicRedirectError,
    looks_like_browser_origin,
    public_redirect_base,
)


def test_docker_api_host_is_not_a_browser_origin():
    assert looks_like_browser_origin("http://api:8000") is False
    assert looks_like_browser_origin("http://api:8000/acquire") is False
    assert looks_like_browser_origin("https://freedombot-core.cloud.cepoch.com/acquire") is True
    assert looks_like_browser_origin("http://localhost:3001/acquire") is True


def test_public_redirect_ignores_docker_host(monkeypatch):
    monkeypatch.setenv("ACQUISITION_URL_PREFIX", "/acquire")
    settings = SimpleNamespace(public_base_url="https://freedombot-core.cloud.cepoch.com/acquire")
    base = public_redirect_base(request_base="http://api:8000", settings=settings)
    assert base == "https://freedombot-core.cloud.cepoch.com/acquire"
    assert "api:8000" not in base


def test_checkout_success_url_uses_public_site():
    settings = SimpleNamespace(public_base_url="https://example.test/acquire")
    base = public_redirect_base(request_base="http://api:8000/acquire", settings=settings)
    success = f"{base}/a/tok123/checkout/success?session_id={{CHECKOUT_SESSION_ID}}"
    assert success.startswith("https://example.test/acquire/a/tok123/checkout/success")
    assert "api:8000" not in success


def test_internal_only_urls_are_refused():
    settings = SimpleNamespace(public_base_url="http://api:8000/acquire")
    with pytest.raises(PublicRedirectError):
        public_redirect_base(request_base="http://api:8000", settings=settings)
