"""
Web Research
============

``search`` finds pages on the public web; ``read`` fetches one page as plain text. Used for company,
prospect and news research.

* Search uses OpenAI's built-in web search (Responses API, ``web_search`` tool) with
  ``WEB_SEARCH_MODEL_ID``: a cited answer plus its source links, on the OpenAI key the chat already uses.
  ``WEB_SEARCH_PROVIDER=tavily`` (with ``TAVILY_API_KEY``) uses Tavily's result list instead.
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
from urllib.parse import urljoin, urlparse

import httpx

from freedombot import settings
from freedombot.connectors.base import ConnectorError
from freedombot.connectors.untrusted import neutralize

TIMEOUT = 15.0
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 4
PAGE_TEXT_LIMIT = 15000
_UA = "Mozilla/5.0 (compatible; FreedomBot research; +https://cepoch.com)"


def _clean(text: Any, limit: int) -> str:
    return neutralize(" ".join(unescape(str(text or "")).split()))[0][:limit]


# --------------------------------------------------------------------------- search

#: What the search model is asked to do with the query (the query itself is data, not an instruction).
_SEARCH_PROMPT = (
    "Search the web and report what reliable, recent sources say about the topic below. Give the key "
    "facts concisely, with dates where they matter, and say when sources disagree or little was found. "
    "Cite every fact.\n\nTopic: "
)


def _openai(query: str, limit: int) -> tuple[str, list[dict[str, str]], str]:
    """(answer, sources, model) from OpenAI's built-in web search (Responses API, web_search tool)."""
    if not settings.openai_api_key():
        raise ConnectorError("Web search needs OPENAI_API_KEY on this server.")
    from openai import OpenAI

    model = settings.web_search_model_id()
    extra: dict[str, Any] = {}
    effort = settings.web_search_reasoning_effort()
    if effort and model.startswith(("gpt-5", "gpt-6", "o")):  # reasoning models only
        extra["reasoning"] = {"effort": effort}
    try:
        response = OpenAI().responses.create(model=model, tools=[{"type": "web_search"}],
                                             input=_SEARCH_PROMPT + query, timeout=90, **extra)
    except Exception as exc:  # noqa: BLE001 - the SDK raises many specific types
        raise ConnectorError(f"Web search failed: {type(exc).__name__}.") from exc
    answer = str(getattr(response, "output_text", "") or "")
    sources: dict[str, dict[str, str]] = {}
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "message":
            continue
        for part in getattr(item, "content", None) or []:
            text = str(getattr(part, "text", "") or "")
            for note in getattr(part, "annotations", None) or []:
                url = str(getattr(note, "url", "") or "")
                if getattr(note, "type", "") != "url_citation" or not url or url in sources:
                    continue
                start, end = getattr(note, "start_index", None), getattr(note, "end_index", None)
                cited = text[max(0, (start or 0) - 240):start] if isinstance(start, int) and isinstance(end, int) else ""
                sources[url] = {"title": _clean(getattr(note, "title", ""), 200), "url": url,
                                "snippet": _clean(cited.split("\n")[-1], 300)}
    return answer, list(sources.values())[:limit], model


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


def search(query: str, *, limit: int = 6) -> dict[str, Any]:
    """What the public web says about ``query``: a cited answer and its sources (title, url, snippet)."""
    query = " ".join(str(query or "").split())
    if not query:
        raise ConnectorError("query is required: say what to search for.")
    limit = max(1, min(int(limit or 6), 10))
    if settings.web_search_provider() == "tavily":
        key = settings.tavily_api_key()
        if not key:
            raise ConnectorError("WEB_SEARCH_PROVIDER is tavily but TAVILY_API_KEY is not set.")
        try:
            rows = _tavily(query, limit, key)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Web search failed: {type(exc).__name__}.") from exc
        return {"query": query, "provider": "tavily", "results": rows, "_untrusted_source": "web_search"}
    answer, rows, model = _openai(query, limit)
    return {"query": query, "provider": "openai", "model": model, "answer": _clean_answer(answer),
            "results": rows, "_untrusted_source": "web_search"}


def _clean_answer(text: str) -> str:
    """The search model's cited summary, neutralized; line breaks kept for readability."""
    return neutralize(unescape(str(text or "")))[0].strip()[:6000]


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
