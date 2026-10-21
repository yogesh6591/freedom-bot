"""Root-domain normalization and non-company page detection for discovery.

Dedup relies on one durable company key. Subdomains (blog.acme.com, www.acme.com)
must collapse to the registrable root (acme.com), except on hosting platforms
where each customer lives on its own subdomain (acme.squarespace.com).

The Public Suffix List snapshot bundled with tldextract is used offline — no
network fetch at runtime.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

try:  # pragma: no cover - import guard exercised by environment
    import tldextract

    _EXTRACT = tldextract.TLDExtract(
        suffix_list_urls=(),
        cache_dir=None,
        include_psl_private_domains=True,
    )
except Exception:  # noqa: BLE001
    _EXTRACT = None

# Platforms where the customer site is the subdomain, not the registrable root.
HOSTING_PLATFORM_ROOTS: frozenset[str] = frozenset(
    {
        "squarespace.com",
        "wordpress.com",
        "wixsite.com",
        "wix.com",
        "weebly.com",
        "godaddysites.com",
        "business.site",
        "webflow.io",
        "square.site",
        "carrd.co",
        "strikingly.com",
        "jimdosite.com",
        "site123.me",
        "myshopify.com",
        "hubspotpagebuilder.com",
        "hs-sites.com",
        "blogspot.com",
    }
)

# Pages that list or review many companies — never a company themselves.
# Also: social networks, search engines, paid data vendors, job boards, news.
NON_COMPANY_DOMAINS: frozenset[str] = frozenset(
    {
        # social / platforms
        "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
        "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "reddit.com",
        "quora.com", "medium.com", "substack.com", "threads.net",
        # search / reference
        "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "wikipedia.org",
        # paid data vendors (never a core dependency, never a "company" hit)
        "crunchbase.com", "apollo.io", "zoominfo.com", "clay.com", "rocketreach.co",
        "lusha.com", "seamless.ai", "cognism.com", "dnb.com", "owler.com",
        "pitchbook.com", "cbinsights.com", "signalhire.com", "contactout.com",
        # review / directory / marketplace sites (ToS forbid building DBs from them)
        "clutch.co", "upcity.com", "designrush.com", "goodfirms.co", "sortlist.com",
        "agencyspotter.com", "themanifest.com", "g2.com", "capterra.com",
        "trustpilot.com", "yelp.com", "bbb.org", "angi.com", "thumbtack.com",
        "expertise.com", "upwork.com", "fiverr.com", "toptal.com", "bark.com",
        "psychologytoday.com", "healthgrades.com", "zocdoc.com", "vitals.com",
        "webmd.com", "headway.co", "alma.com", "growtherapy.com", "betterhelp.com",
        "manta.com", "yellowpages.com", "chamberofcommerce.com", "mapquest.com",
        "inc.com", "entrepreneur.com", "builtin.com", "tracxn.com",
        "comparably.com", "vendordirectory.shrm.org",
        # jobs
        "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
        "simplyhired.com", "careerbuilder.com", "wellfound.com",
        # news / press wires
        "forbes.com", "bloomberg.com", "reuters.com", "wsj.com", "nytimes.com",
        "businesswire.com", "prnewswire.com", "globenewswire.com",
        "bizjournals.com", "prweb.com", "accessnewswire.com",
        # government / registries (used via their APIs, not as company sites)
        "npiregistry.cms.hhs.gov", "cms.gov", "hhs.gov", "sam.gov", "opencorporates.com",
    }
)

_LISTICLE_PATTERNS = (
    re.compile(r"^\s*(top|best)\s+\d*", re.I),
    re.compile(r"\b(top|best)\s+\d+\b", re.I),
    re.compile(r"\b\d+\s+(best|top|leading)\b", re.I),
    re.compile(r"\b(companies|agencies|firms|practices|consultants|providers)\s+(in|near)\s+", re.I),
    re.compile(r"\b(reviews?|rankings?|ranked|directory|near me|list of)\b", re.I),
    re.compile(r"\b20\d\d\s+(rankings|reviews|list)\b", re.I),
)


def _host(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "@" in raw and "://" not in raw:
        raw = raw.split("@", 1)[1]
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").strip(".").lower()
    if not host or "." not in host or any(ch.isspace() for ch in host):
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


@lru_cache(maxsize=4096)
def root_domain(value: str | None) -> str | None:
    """Registrable root domain for a URL/host/email, e.g. blog.acme.co.uk → acme.co.uk.

    Customer subdomains on hosting platforms are kept (acme.squarespace.com).
    """
    host = _host(value)
    if not host:
        return None
    if _EXTRACT is None:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    ext = _EXTRACT(host)
    registered = getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain
    if not registered:
        return host
    if registered in HOSTING_PLATFORM_ROOTS and host != registered:
        return host
    return registered


def is_non_company_domain(value: str | None) -> bool:
    """True for directories, reviews, social, data vendors, jobs, news, registries."""
    host = _host(value)
    if not host:
        return True
    root = root_domain(host) or host
    for blocked in NON_COMPANY_DOMAINS:
        if host == blocked or host.endswith("." + blocked) or root == blocked:
            return True
    return False


def looks_like_listicle(title: str | None) -> bool:
    """'Top 10 Marketing Agencies in Austin – 2026 Reviews' is a list page, not a firm."""
    text = (title or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _LISTICLE_PATTERNS)


def blocked_domains_for_search(limit: int = 100) -> list[str]:
    """Stable list for search-API domain exclusion filters (API caps ~100)."""
    return sorted(NON_COMPANY_DOMAINS)[:limit]
