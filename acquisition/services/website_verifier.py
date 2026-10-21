"""Live website check for AI/index-suggested domains.

Search models can invent or mistype domains. Before a suggested company is
saved, its homepage must respond and — where the page title is readable —
share a meaningful token with the company name. Redirects to a different root
domain (rebrands, parked domains) are reported so dedupe uses the final domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from acquisition.config import Settings, get_settings
from acquisition.services.domain_tools import is_non_company_domain, root_domain

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_PARKED = re.compile(
    r"(domain (is )?for sale|buy this domain|parked free|this domain may be for sale|"
    r"godaddy\.com/park|sedoparking|hugedomains|dan\.com)",
    re.I,
)
_STOP = {
    "the", "and", "of", "inc", "llc", "llp", "pllc", "group", "company", "co", "firm",
    "partners", "associates", "services", "consulting", "agency", "health", "center",
}


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    domain: str | None
    final_domain: str | None = None
    title: str | None = None
    status_code: int | None = None
    reason: str = ""
    name_match: bool | None = None


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOP}


class WebsiteVerifier:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "discovery_verify_websites", True))

    def verify(self, domain: str | None, company: str | None) -> VerifyResult:
        root = root_domain(domain)
        if not root:
            return VerifyResult(ok=False, domain=domain, reason="no domain")
        if is_non_company_domain(root):
            return VerifyResult(ok=False, domain=root, reason="directory/social/news domain")
        timeout = float(getattr(self.settings, "discovery_verify_timeout_seconds", 8) or 8)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FreedomBotVerifier/1.0)"}
        last_error = ""
        for url in (f"https://{root}", f"https://www.{root}", f"http://{root}"):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    transport=self._transport,
                    headers=headers,
                ) as client:
                    resp = client.get(url)
            except Exception as exc:  # noqa: BLE001
                last_error = exc.__class__.__name__
                continue
            final = root_domain(str(resp.url)) or root
            if resp.status_code >= 400 and resp.status_code not in (401, 403):
                last_error = f"http_{resp.status_code}"
                continue
            body = resp.text[:200_000] if resp.text else ""
            m = _TITLE.search(body)
            title = " ".join(m.group(1).split())[:200] if m else None
            if _PARKED.search(body[:20_000]):
                return VerifyResult(
                    ok=False, domain=root, final_domain=final, title=title,
                    status_code=resp.status_code, reason="parked or for-sale domain",
                )
            if final != root and is_non_company_domain(final):
                return VerifyResult(
                    ok=False, domain=root, final_domain=final, title=title,
                    status_code=resp.status_code, reason=f"redirects to directory {final}",
                )
            name_match = None
            if company and (title or body):
                ct = _tokens(company)
                page = _tokens(title or "") | _tokens(body[:5000]) | _tokens(final.replace(".", " "))
                name_match = bool(ct & page) if ct else None
            return VerifyResult(
                ok=True,
                domain=root,
                final_domain=final,
                title=title,
                status_code=resp.status_code,
                reason="reachable" + ("" if name_match in (None, True) else " (name not found on page)"),
                name_match=name_match,
            )
        return VerifyResult(ok=False, domain=root, reason=f"unreachable ({last_error or 'error'})")
