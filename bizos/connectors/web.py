"""
Web Research
============

``search`` finds pages on the public web; ``read`` fetches one page as plain text. Used for company,
prospect and news research.

* Search uses Tavily when ``TAVILY_API_KEY`` is set (built for agents: clean snippets), else
  DuckDuckGo's HTML results page (no key; best effort, and often refused for server traffic, in which
  case the tool says an admin must set the key).
* ``read`` only fetches public ``http``/``https`` addresses. Every address, including each redirect,
  must resolve to public IPs, so the tool can never reach the server's own network or cloud metadata.

Everything returned is external content and is neutralized before the model sees it.
"""

from __future__ import annotations

import ipaddress
import socket
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from bizos import settings
from bizos.connectors.base import ConnectorError
from bizos.connectors.untrusted import neutralize

TIMEOUT = 15.0
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 4
PAGE_TEXT_LIMIT = 15000
_UA = "Mozilla/5.0 (compatible; FreedomBot research; +https://cepoch.com)"


def _clean(text: Any, limit: int) -> str:
    return neutralize(" ".join(unescape(str(text or "")).split()))[0][:limit]


# --------------------------------------------------------------------------- search


def _tavily(query: str, limit: int, key: str) -> list[dict[str, str]]:
    response = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": limit, "search_depth": "basic"},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ConnectorError(f"Web search failed ({response.status_code}).")
    return [{"title": _clean(r.get("title"), 200), "url": str(r.get("url") or ""),
             "snippet": _clean(r.get("content"), 500)} for r in (response.json().get("results") or [])]


class _DuckResults(HTMLParser):
    """Titles, links and snippets from DuckDuckGo's HTML results page."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._field: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            href = a.get("href") or ""
            target = (parse_qs(urlparse(href).query).get("uddg") or [href])[0]
            self.rows.append({"title": "", "url": target if target.startswith("http") else "", "snippet": ""})
            self._field = "title"
        elif "result__snippet" in classes and self.rows:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "td", "div"):
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field and self.rows:
            self.rows[-1][self._field] += data


def _duckduckgo(query: str, limit: int) -> list[dict[str, str]]:
    response = httpx.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers={"User-Agent": _UA}, timeout=TIMEOUT, follow_redirects=True)
    if response.status_code >= 400:
        raise ConnectorError(f"Web search failed ({response.status_code}).")
    parser = _DuckResults()
    parser.feed(response.text)
    return [{"title": _clean(r["title"], 200), "url": r["url"], "snippet": _clean(r["snippet"], 500)}
            for r in parser.rows if r["url"]][:limit]


def search(query: str, *, limit: int = 6) -> dict[str, Any]:
    """Public web results for ``query``: title, url and snippet each."""
    query = " ".join(str(query or "").split())
    if not query:
        raise ConnectorError("query is required: say what to search for.")
    limit = max(1, min(int(limit or 6), 10))
    key = settings.tavily_api_key()
    try:
        rows = _tavily(query, limit, key) if key else _duckduckgo(query, limit)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Web search failed: {type(exc).__name__}.") from exc
    if not rows and not key:
        raise ConnectorError(
            "Web search returned nothing. This server has no search API key, and the free fallback is often "
            "refused for servers: an admin needs to set TAVILY_API_KEY. web_read still works for a known address."
        )
    return {"query": query, "provider": "tavily" if key else "duckduckgo", "results": rows,
            "_untrusted_source": "web_search"}


# --------------------------------------------------------------------------- read


def _public_host(host: str, port: int) -> None:
    """Raise unless every address ``host`` resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectorError(f"Could not find the site {host!r}.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise ConnectorError("That address is not a public website, so it cannot be read.")


def check_url(url: str) -> str:
    """The URL if it is a public http(s) address, else a ConnectorError explaining why not."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorError("url must be a full public web address starting with http:// or https://")
    if parsed.username or parsed.password:
        raise ConnectorError("url must not contain a user name or password.")
    _public_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    return parsed.geturl()


class _PageText(HTMLParser):
    """Visible text and title of an HTML page (scripts, styles and navigation skipped)."""

    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def read(url: str) -> dict[str, Any]:
    """One public page as plain text (title, text, final URL)."""
    current = check_url(url)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": _UA}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        current = check_url(urljoin(current, response.headers.get("location", "")))
                        continue
                    if response.status_code >= 400:
                        raise ConnectorError(f"The page returned an error ({response.status_code}).")
                    kind = response.headers.get("content-type", "").lower()
                    if not any(t in kind for t in ("text/html", "text/plain", "application/xhtml", "json", "xml")):
                        raise ConnectorError(f"That address is not a web page ({kind or 'unknown type'}).")
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    text = body.decode(response.encoding or "utf-8", errors="replace")
                    break
            except httpx.HTTPError as exc:
                raise ConnectorError(f"Could not read the page: {type(exc).__name__}.") from exc
        else:
            raise ConnectorError("The page redirected too many times.")
    title = ""
    if "html" in kind:
        parser = _PageText()
        parser.feed(text)
        title, text = parser.title, "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    plain = "\n".join(line for line in lines if line)
    cleaned, findings = neutralize(plain)
    return {
        "url": current,
        "title": _clean(title, 300),
        "characters": len(cleaned),
        "truncated": len(cleaned) > PAGE_TEXT_LIMIT,
        "text": cleaned[:PAGE_TEXT_LIMIT],
        **({"_untrusted_findings": [{"kind": k, "match": m} for k, m in findings]} if findings else {}),
        "_untrusted_source": "web_page",
    }
