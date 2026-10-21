"""Company identity resolution + source relevance for public discovery/research.

One Prospect must map to one real company/domain. Research evidence, decision-makers,
contact data, and timing signals must be attributable to that same company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from acquisition.services.prospect_identity import normalize_company_name, normalize_domain

# Generic service / category page titles — not company brand names.
_GENERIC_SERVICE_TITLE_RE = re.compile(
    r"(?i)\b("
    r"fractional\s+(?:executive|cxo|cio|coo|cto|cfo)|"
    r"(?:cxo|cio|coo|cto|cfo)\s+services?|"
    r"consulting\s+services?|"
    r"leadership\s+solutions?|"
    r"staffing(?:\s+[&/]\s*hr)?|"
    r"professional\s+services?|"
    r"our\s+services?|"
    r"managed\s+services?|"
    r"advisory\s+services?|"
    r"business\s+solutions?|"
    r"what\s+we\s+do|"
    r"careers?|"
    r"job\s+openings?|"
    r"contact\s+us|"
    r"about\s+us|"
    r"home"
    r")\b"
)

_GENERIC_TITLE_ONLY_RE = re.compile(
    r"(?i)^(services?|solutions?|consulting|leadership|about|home|careers?|"
    r"contact|team|blog|news|resources?|industries|insights?)$"
)

# Marketing / nav / CTA phrases that must never be treated as person names.
_INVALID_PERSON_PHRASES = frozenset(
    {
        "we focus on",
        "learn more",
        "our team",
        "contact us",
        "get started",
        "read more",
        "click here",
        "sign up",
        "subscribe",
        "book a call",
        "schedule a call",
        "talk to us",
        "meet the team",
        "about us",
        "who we are",
        "what we do",
        "how it works",
        "request a demo",
        "free consultation",
        "unknown decision maker",
        "unknown decision-maker",
        "decision maker",
        "decision-maker",
        "interim cbh",
    }
)

# Role/status words that are titles, not given names.
_NAME_ROLE_TOKENS = frozenset(
    {
        "interim",
        "acting",
        "former",
        "current",
        "incoming",
        "outgoing",
        "lead",
        "senior",
        "junior",
        "team",
        "staff",
        "group",
        "department",
        "services",
        "service",
        "solutions",
        "consulting",
        "fractional",
    }
)

_PRONOUNS = frozenset({"we", "our", "us", "they", "their", "you", "your", "i", "me", "he", "she"})

_CREDIBLE_TITLES = (
    "founder",
    "co-founder",
    "cofounder",
    "owner",
    "ceo",
    "chief executive officer",
    "president",
    "managing partner",
    "managing director",
    "principal",
    "partner",
    "chairman",
    "chairwoman",
    "executive chair",
)

_PERSON_NAME_RE = re.compile(
    r"^[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){1,3}$"
)

_STOP_NAME_TOKENS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "about",
        "services",
        "service",
        "solutions",
        "solution",
        "consulting",
        "fractional",
        "executive",
        "leadership",
        "company",
        "group",
        "partners",
        "partner",
        "llc",
        "inc",
        "corp",
    }
)

_SOCIAL_PROFILE_DOMAINS = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "youtube.com",
        "youtu.be",
    }
)

_DIRECTORY_DOMAINS = frozenset(
    {
        "crunchbase.com",
        "apollo.io",
        "zoominfo.com",
        "clay.com",
        "wikipedia.org",
        "bloomberg.com",
        "reuters.com",
        "wsj.com",
        "forbes.com",
        "businesswire.com",
        "prnewswire.com",
        "globenewswire.com",
    }
)

IDENTITY_CONFIDENCE_HIGH = 0.85
IDENTITY_CONFIDENCE_MEDIUM = 0.55
IDENTITY_CONFIDENCE_LOW = 0.30
IDENTITY_UNCERTAIN_THRESHOLD = 0.45


@dataclass(slots=True, frozen=True)
class CompanyIdentity:
    """Resolved company identity for one discovery/research candidate."""

    company_name: str
    domain: str | None
    website: str | None
    confidence: float
    uncertain: bool
    source_url: str | None = None
    resolution_notes: str | None = None


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return normalize_domain(host) or (host or None)
    except Exception:  # noqa: BLE001
        return None


def domains_match(a: str | None, b: str | None) -> bool:
    left = normalize_domain(a) or (a or "").strip().lower().removeprefix("www.")
    right = normalize_domain(b) or (b or "").strip().lower().removeprefix("www.")
    if not left or not right:
        return False
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def is_generic_service_title(title: str | None) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    # Strip common trailing site branding for the check of the primary phrase.
    primary = text
    for sep in (" | ", " - ", " — ", " – ", ":"):
        if sep in primary:
            # Evaluate each side; generic if the dominant label is a service phrase.
            left, right = [p.strip() for p in primary.split(sep, 1)]
            if _looks_like_brand(right) and (
                _GENERIC_SERVICE_TITLE_RE.search(left) or _GENERIC_TITLE_ONLY_RE.match(left)
            ):
                return True
            if _looks_like_brand(left) and (
                _GENERIC_SERVICE_TITLE_RE.search(right) or _GENERIC_TITLE_ONLY_RE.match(right)
            ):
                return False
            primary = left
            break
    if _GENERIC_TITLE_ONLY_RE.match(primary):
        return True
    if _GENERIC_SERVICE_TITLE_RE.search(primary):
        # Brand + service in same string without separator may still be generic-only.
        if not _looks_like_brand(primary):
            return True
        # e.g. "Fractional Executive (CxO) Services" — no distinct brand tokens.
        tokens = [t for t in re.findall(r"[A-Za-z]+", primary) if t.lower() not in _STOP_NAME_TOKENS]
        return len(tokens) < 2
    return False


def is_service_keyword_page_not_company_type(
    *,
    title: str | None,
    snippet: str | None,
    company: str | None,
    priority_categories: list[Any] | None,
    identity_uncertain: bool,
) -> bool:
    """True when a search hit matches ICP keywords as a service offering page.

    Distinguishes "company is a fractional CXO firm" from "company page mentions
    fractional CXO services" (e.g. an accounting firm marketing interim CXO help).
    Uncertain service-only hits should not be provisionally accepted as ICP fits.
    """
    title_text = (title or "").strip()
    if not title_text or not is_generic_service_title(title_text):
        return False
    if not identity_uncertain:
        return False
    cats = [str(c).strip() for c in (priority_categories or []) if str(c).strip()]
    if not cats:
        return False
    title_l = title_text.lower()
    company_l = (company or "").strip().lower()
    snippet_l = (snippet or "").strip().lower()

    def _category_in(text: str, category: str) -> bool:
        cat = category.lower().strip()
        if not cat or not text:
            return False
        if cat in text:
            return True
        tokens = [t for t in re.findall(r"[a-z0-9]+", cat) if len(t) > 2]
        if tokens and all(t in text for t in tokens):
            return True
        # Common ICP shorthand: "fractional CXO" vs "Fractional Executive (CxO)".
        if "fractional" in cat and "fractional" in text:
            if any(tok in text for tok in ("cxo", "cio", "coo", "cfo", "cto", "executive")):
                return True
        return False

    title_hit = next((c for c in cats if _category_in(title_l, c)), None)
    if not title_hit:
        return False
    # Company name itself encodes the priority category → real business-type signal.
    if company_l and any(_category_in(company_l, c) for c in cats):
        return False
    # Snippet evidence that the org *is* that type (not merely offering a related service).
    type_markers = (
        "we are a",
        "we're a",
        "is a",
        "specializes in",
        "specialising in",
        "firm focused on",
        "boutique",
        "practice focused",
    )
    if any(marker in snippet_l for marker in type_markers) and any(
        _category_in(snippet_l, c) for c in cats
    ):
        return False
    # Priority keyword appears as offering language only.
    offering_markers = (
        "services",
        "solutions",
        "offering",
        "offers",
        "provide",
        "provides",
        "hire",
        "hiring",
    )
    if any(m in title_l for m in offering_markers) or any(m in snippet_l for m in offering_markers):
        return True
    # Uncertain brand + category only in generic title → treat as service-page keyword.
    return True


def _looks_like_brand(text: str | None) -> bool:
    """Heuristic brand check — must not call is_generic_service_title (avoids recursion)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if _GENERIC_TITLE_ONLY_RE.match(cleaned):
        return False
    if _GENERIC_SERVICE_TITLE_RE.fullmatch(cleaned.strip()):
        return False
    words = [w for w in re.findall(r"[A-Za-z0-9&'.]+", cleaned) if w]
    if not words:
        return False
    serviceish = sum(
        1
        for w in words
        if w.lower() in _STOP_NAME_TOKENS or _GENERIC_SERVICE_TITLE_RE.search(w)
    )
    if serviceish >= max(1, len(words) - 1) and len(words) <= 5:
        return False
    return any(
        (w[0].isupper() or w.isupper()) and w.lower() not in _STOP_NAME_TOKENS for w in words
    )


def brand_from_domain(domain: str | None) -> str | None:
    host = normalize_domain(domain) or (domain or "").strip().lower().removeprefix("www.")
    if not host or "." not in host:
        return None
    slug = host.split(".")[0]
    if not slug or slug in {"www", "mail", "app", "blog", "shop"}:
        return None
    # cbh → CBH; cherry-bekaert → Cherry Bekaert
    if "-" in slug or "_" in slug:
        return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part)
    if len(slug) <= 4 and slug.isalpha():
        return slug.upper()
    return slug.capitalize()


def extract_brand_from_text(text: str | None, *, domain: str | None = None) -> str | None:
    """Pull a likely company brand from title/snippet near domain cues."""
    blob = (text or "").strip()
    if not blob:
        return None
    # "Brand | Service" / "Service | Brand"
    for sep in (" | ", " - ", " — ", " – "):
        if sep in blob:
            left, right = [p.strip() for p in blob.split(sep, 1)]
            if _looks_like_brand(left) and not is_generic_service_title(left):
                return left[:255]
            if _looks_like_brand(right) and not is_generic_service_title(right):
                return right[:255]
    # Explicit "at Brand" / "Brand announces"
    at_match = re.search(
        r"\b(?:at|from|by)\s+([A-Z][A-Za-z0-9&'.]+(?:\s+[A-Z][A-Za-z0-9&'.]+){0,3})\b",
        blob,
    )
    if at_match:
        candidate = at_match.group(1).strip()
        if _looks_like_brand(candidate) and not is_generic_service_title(candidate):
            return candidate[:255]
    if domain:
        host = (normalize_domain(domain) or domain).lower()
        # Prefer quoted or Proper-case phrases that appear with the domain mention.
        if host.split(".")[0].lower() in blob.lower():
            proper = re.findall(
                r"\b([A-Z][a-zA-Z0-9&'.]+(?:\s+[A-Z][a-zA-Z0-9&'.]+){0,3})\b",
                blob,
            )
            for cand in proper:
                if is_generic_service_title(cand):
                    continue
                if _looks_like_brand(cand):
                    return cand[:255]
    return None


def resolve_company_identity(
    *,
    title: str | None = None,
    url: str | None = None,
    snippet: str | None = None,
    company_hint: str | None = None,
    domain_hint: str | None = None,
    website_hint: str | None = None,
) -> CompanyIdentity:
    """Resolve canonical company name + domain before research is persisted."""
    domain = (
        normalize_domain(domain_hint)
        or domain_from_url(website_hint)
        or domain_from_url(url)
    )
    website = website_hint or (f"https://{domain}" if domain else None) or url

    hint = (company_hint or "").strip() or None
    title_text = (title or "").strip()
    snippet_text = (snippet or "").strip()
    combined = f"{title_text} {snippet_text}".strip()

    # 1) Explicit non-generic hint already provided (e.g. prior research upgrade).
    if hint and not is_generic_service_title(hint) and _looks_like_brand(hint):
        return CompanyIdentity(
            company_name=hint[:255],
            domain=domain,
            website=website,
            confidence=IDENTITY_CONFIDENCE_HIGH if domain else IDENTITY_CONFIDENCE_MEDIUM,
            uncertain=False,
            source_url=url,
            resolution_notes="company_hint",
        )

    # 2) Title branding: prefer brand side of "Service | Brand".
    brand_from_title = None
    if title_text:
        for sep in (" | ", " - ", " — ", " – ", ":"):
            if sep in title_text:
                left, right = [p.strip() for p in title_text.split(sep, 1)]
                if is_generic_service_title(left) and _looks_like_brand(right):
                    brand_from_title = right
                    break
                if _looks_like_brand(left) and (
                    is_generic_service_title(right) or not is_generic_service_title(left)
                ):
                    brand_from_title = left
                    break
        if brand_from_title is None and not is_generic_service_title(title_text):
            # Take first segment cleaned of trailing fluff.
            primary = title_text
            for sep in (" | ", " - ", " — ", " – ", ":"):
                if sep in primary:
                    primary = primary.split(sep, 1)[0].strip()
                    break
            primary = re.sub(
                r"\s+(Home|Official Site|About Us|Careers)$",
                "",
                primary,
                flags=re.I,
            ).strip()
            if primary and not is_generic_service_title(primary) and _looks_like_brand(primary):
                brand_from_title = primary

    if brand_from_title:
        brand_from_title = _clean_brand_label(brand_from_title) or brand_from_title
        return CompanyIdentity(
            company_name=brand_from_title[:255],
            domain=domain,
            website=website,
            confidence=IDENTITY_CONFIDENCE_HIGH if domain else IDENTITY_CONFIDENCE_MEDIUM,
            uncertain=False,
            source_url=url,
            resolution_notes="title_brand",
        )

    # 3) Snippet / combined text brand extraction.
    from_text = extract_brand_from_text(combined, domain=domain)
    if from_text:
        return CompanyIdentity(
            company_name=from_text[:255],
            domain=domain,
            website=website,
            confidence=IDENTITY_CONFIDENCE_MEDIUM if domain else IDENTITY_CONFIDENCE_LOW,
            uncertain=not bool(domain),
            source_url=url,
            resolution_notes="snippet_brand",
        )

    # 4) Domain-derived provisional name when title is a generic service page.
    domain_brand = brand_from_domain(domain)
    if domain_brand and (is_generic_service_title(title_text) or not title_text):
        return CompanyIdentity(
            company_name=domain_brand[:255],
            domain=domain,
            website=website,
            confidence=IDENTITY_CONFIDENCE_LOW,
            uncertain=True,
            source_url=url,
            resolution_notes="domain_provisional_generic_title",
        )

    # 5) Last resort: cleaned title or domain brand.
    fallback = (hint or title_text or domain_brand or "Unknown Company").strip()[:255]
    uncertain = is_generic_service_title(fallback) or fallback.lower() in {
        "unknown company",
        "unknown",
    }
    return CompanyIdentity(
        company_name=fallback,
        domain=domain,
        website=website,
        confidence=IDENTITY_CONFIDENCE_LOW if uncertain else IDENTITY_CONFIDENCE_MEDIUM,
        uncertain=uncertain or not domain,
        source_url=url,
        resolution_notes="fallback",
    )


def company_name_mentioned(
    company_name: str | None,
    *texts: str | None,
    min_token_hits: int = 1,
    require_all_tokens: bool = False,
) -> bool:
    normalized = normalize_company_name(company_name)
    if not normalized:
        return False
    blob = " ".join(t or "" for t in texts).lower()
    if not blob:
        return False
    # Treat URL separators as spaces so query strings can match brand tokens.
    blob = re.sub(r"[+_/]+", " ", blob)
    if normalized in (normalize_company_name(blob) or "") or normalized in blob:
        return True
    tokens = [t for t in normalized.split() if len(t) >= 3 and t not in _STOP_NAME_TOKENS]
    if not tokens:
        tokens = [t for t in normalized.split() if t]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", blob))
    if require_all_tokens:
        return hits >= len(tokens)
    need = min_token_hits if len(tokens) == 1 else max(min_token_hits, (len(tokens) + 1) // 2)
    return hits >= need


def domain_acronym(domain: str | None) -> str | None:
    host = normalize_domain(domain) or (domain or "").strip().lower().removeprefix("www.")
    if not host or "." not in host:
        return None
    slug = host.split(".")[0]
    if not slug or not slug.isalpha() or len(slug) > 5:
        return None
    return slug.lower()


def has_strong_external_entity_match(
    company_name: str | None,
    domain: str | None,
    *texts: str | None,
) -> bool:
    """External sources require explicit canonical company identity — not acronyms."""
    brand_norm = normalize_company_name(company_name) or ""
    if not brand_norm:
        return False
    # Bare acronym brands (CBH) never authorize a foreign domain alone.
    if " " not in brand_norm and len(brand_norm) <= 4:
        return False

    blob = " ".join(t or "" for t in texts).lower()
    blob = re.sub(r"[+_/]+", " ", blob)
    if not blob:
        return False

    # Exact / full-phrase canonical name (all significant tokens).
    if not company_name_mentioned(
        company_name, *texts, require_all_tokens=True
    ):
        return False

    # If the only org cue is the domain acronym (CBH), reject even when tokens
    # somehow matched weakly — acronym-only references are insufficient.
    acronym = domain_acronym(domain)
    if acronym and re.search(rf"\b{re.escape(acronym)}\b", blob):
        # Allow only when the full brand also appears (already required above).
        # Still reject if brand tokens are absent as a contiguous/explicit name
        # and the acronym is the primary identifier in the text.
        if brand_norm not in blob and brand_norm not in (normalize_company_name(blob) or ""):
            # Token match already passed; keep acceptance when all tokens present.
            pass
    return True


def is_source_relevant_to_prospect(
    *,
    source_url: str | None,
    company_name: str | None,
    domain: str | None,
    source_title: str | None = None,
    source_snippet: str | None = None,
    source_note: str | None = None,
) -> bool:
    """Central relevance gate: evidence must belong to the prospect company."""
    prospect_domain = normalize_domain(domain)
    source_domain = domain_from_url(source_url)

    # Same-company web property → accept.
    if prospect_domain and source_domain and domains_match(prospect_domain, source_domain):
        return True

    texts = (source_title, source_snippet, source_note)

    # External / URL-less: strong entity match only (no acronym-only).
    if source_domain and prospect_domain and not domains_match(prospect_domain, source_domain):
        return has_strong_external_entity_match(company_name, prospect_domain, *texts)

    if not source_url:
        return has_strong_external_entity_match(company_name, prospect_domain, *texts)

    # Social / directory without prospect domain known: still require strong match.
    if source_domain and any(
        source_domain == d or source_domain.endswith("." + d)
        for d in (_SOCIAL_PROFILE_DOMAINS | _DIRECTORY_DOMAINS)
    ):
        return has_strong_external_entity_match(company_name, prospect_domain, *texts)

    return has_strong_external_entity_match(company_name, prospect_domain, *texts)


def filter_sources_for_ai_validation(
    sources: list[dict[str, Any]],
    *,
    company_name: str | None,
    domain: str | None,
) -> list[dict[str, Any]]:
    """Hard-filter before AI: never send clearly unrelated domains to the model."""
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    prospect_domain = normalize_domain(domain)
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        snippet = str(raw.get("snippet") or raw.get("note") or "").strip()
        url = str(raw.get("url") or raw.get("source_url") or raw.get("link") or "").strip()
        if not title and not snippet and not url:
            continue
        key = (url or f"{title}|{snippet[:80]}").lower()
        if key in seen:
            continue
        source_domain = domain_from_url(url) or str(raw.get("domain") or "").strip() or None
        same_domain = bool(
            prospect_domain
            and source_domain
            and domains_match(prospect_domain, source_domain)
        )
        relevant = is_source_relevant_to_prospect(
            source_url=url or None,
            company_name=company_name,
            domain=domain,
            source_title=title or None,
            source_snippet=snippet or None,
        )
        # Accepted: same-domain (even if title is generic) OR strong external match.
        # Rejected unrelated domains never reach AI.
        if not same_domain and not relevant:
            continue
        seen.add(key)
        accepted.append(
            {
                **raw,
                "title": title,
                "snippet": snippet,
                "url": url or None,
                "domain": source_domain,
                "hard_relevance": "same_company" if same_domain else "external_verified",
            }
        )
    return accepted


def is_valid_person_name(name: str | None, *, company_domain: str | None = None) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    lowered = re.sub(r"\s+", " ", text.lower()).strip()
    if lowered in _INVALID_PERSON_PHRASES:
        return False
    if any(phrase in lowered for phrase in _INVALID_PERSON_PHRASES):
        return False
    # Allow common honorifics; reject other punctuation/sentence marks.
    honorific = re.match(r"^(Dr|Mr|Mrs|Ms|Miss|Prof)\.?\s+(.+)$", text, flags=re.I)
    core = honorific.group(2).strip() if honorific else text
    if any(ch in core for ch in ".!?;:"):
        return False
    if len(text) > 60:
        return False
    words = core.split()
    if len(words) < 2 or len(words) > 4:
        return False
    if words[0].lower() in _PRONOUNS:
        return False
    if any(w.lower() in _STOP_NAME_TOKENS for w in words):
        return False
    if any(w.lower() in _NAME_ROLE_TOKENS for w in words):
        return False
    # Reject "Interim CBH" / "Acting ACME" style title + org-acronym pairs.
    for w in words:
        if w.isupper() and 2 <= len(w) <= 5 and not w.endswith("."):
            return False
    domain_slug = None
    host = normalize_domain(company_domain) or (company_domain or "").strip().lower()
    if host and "." in host:
        domain_slug = host.split(".")[0].lower()
        if any(w.lower() == domain_slug for w in words):
            return False
    # Rebuild a Proper-Case check on the core name.
    if not _PERSON_NAME_RE.match(core) and not _PERSON_NAME_RE.match(
        " ".join(w.capitalize() if w.islower() else w for w in words)
    ):
        # Accept already-proper names; reject lowercase marketing phrases.
        if not all(re.match(r"^[A-Z][a-zA-Z'’\-]+$", w) for w in words):
            return False
    if words[0].lower() in {"we", "our", "the", "a", "an"}:
        return False
    return True


def is_credible_leadership_title(title: str | None) -> bool:
    text = (title or "").strip().lower()
    if not text:
        return False
    return any(t in text for t in _CREDIBLE_TITLES)


def email_matches_company_domain(email: str | None, domain: str | None) -> bool:
    text = (email or "").strip().lower()
    host = normalize_domain(domain)
    if not text or "@" not in text or not host:
        return False
    email_host = text.rsplit("@", 1)[-1].strip().lower().removeprefix("www.")
    if email_host in {"example.com", "email.com", "sentry.io", "pending.invalid"}:
        return False
    return domains_match(email_host, host)


def _clean_brand_label(text: str | None) -> str | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    cleaned = re.sub(
        r"^(About(?:\s+Us)?|Home|Official Site|Welcome to)\s+",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    cleaned = re.sub(
        r"\s+(Home|Official Site|About Us|Careers|Contact)$",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    return cleaned or None


def prefer_canonical_company_name(
    *,
    current: str | None,
    candidate: str | None,
) -> str | None:
    """Prefer a stronger brand name over a generic service title."""
    cur = _clean_brand_label(current) or (current or "").strip() or None
    cand = _clean_brand_label(candidate) or (candidate or "").strip() or None
    if not cand:
        return cur
    if not cur:
        return cand
    if is_generic_service_title(cur) and not is_generic_service_title(cand):
        return cand
    if is_generic_service_title(cand) and not is_generic_service_title(cur):
        return cur
    if _looks_like_brand(cand) and (
        not _looks_like_brand(cur) or len(cand) >= len(cur)
    ):
        if cur.isupper() and len(cur) <= 4 and not cand.isupper():
            return cand
        if cand.isupper() and len(cand) <= 4 and not cur.isupper() and _looks_like_brand(cur):
            return cur
    return cand if _looks_like_brand(cand) else cur
