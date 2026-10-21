"""Normalization + reusable identity lookup for autonomous discovery.

DATABASE IDENTITY DEDUPLICATION (this module) is separate from
ACTIVE OUTREACH CONFLICT PREVENTION (outreach_conflict_service).
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from acquisition.models.prospect import Prospect

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|saas)\b",
    re.I,
)


def normalize_domain(value: str | None) -> str | None:
    """Durable company domain key: the registrable root (blog.acme.com → acme.com).

    Hosting-platform customer subdomains are preserved (acme.squarespace.com).
    Returns lowercase domain or None.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).netloc or urlparse(raw).path.split("/")[0]
    except Exception:  # noqa: BLE001
        return None
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0].strip(".")
    if not host or "." not in host:
        return None
    if any(ch.isspace() for ch in host):
        return None
    from acquisition.services.domain_tools import root_domain

    return root_domain(host) or host


def normalize_company_name(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    text = _COMPANY_SUFFIXES.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    text = " ".join(text.split())
    return text or None


def normalize_email(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text or "@" not in text:
        return None
    return text


def normalize_person_name(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split()) or None


PLACEHOLDER_EMAIL_DOMAIN = "example.com"
PLACEHOLDER_EMAIL_LOCAL = "pending-discovery"


def placeholder_email_for_domain(domain: str | None = None) -> str:
    """Syntactic email placeholder — never a legitimate outreach contact path.

    Uses example.com (documentation reserved) so EmailStr accepts it; scoring
    treats email_confidence=placeholder as unreachable.
    """
    del domain
    return f"{PLACEHOLDER_EMAIL_LOCAL}@{PLACEHOLDER_EMAIL_DOMAIN}"


def is_placeholder_email(email: str | None) -> bool:
    text = (email or "").strip().lower()
    if not text:
        return True
    if text == f"{PLACEHOLDER_EMAIL_LOCAL}@{PLACEHOLDER_EMAIL_DOMAIN}":
        return True
    if text.endswith("@pending.invalid") or ".pending.invalid" in text:
        return True
    return False


def domain_from_website_or_email(
    *,
    website: str | None = None,
    email: str | None = None,
) -> str | None:
    domain = normalize_domain(website)
    if domain:
        return domain
    email_norm = normalize_email(email)
    if not email_norm:
        return None
    host = email_norm.split("@", 1)[1]
    # Skip freewebmail and placeholder/reserved domains as company identity.
    if host in {PLACEHOLDER_EMAIL_DOMAIN, "example.org", "example.net"} or host.endswith(".invalid"):
        return None
    if host in {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
    }:
        return None
    return normalize_domain(host)


def find_existing_company_or_prospect(
    db: Session,
    *,
    email: str | None = None,
    website: str | None = None,
    domain: str | None = None,
    company: str | None = None,
    executive_name: str | None = None,
    exclude_prospect_id: uuid.UUID | None = None,
) -> Prospect | None:
    """Return an existing Prospect that matches durable identity signals.

    Match order (first hit wins):
    1. Exact normalized email
    2. Normalized domain (+ optional person name when provided)
    3. Normalized company name + person name
    """
    email_n = normalize_email(email)
    domain_n = normalize_domain(domain) or domain_from_website_or_email(
        website=website, email=email
    )
    company_n = normalize_company_name(company)
    person_n = normalize_person_name(executive_name)

    def _exclude(stmt):
        if exclude_prospect_id is not None:
            return stmt.where(Prospect.id != exclude_prospect_id)
        return stmt

    if email_n:
        row = db.scalar(
            _exclude(select(Prospect).where(func.lower(Prospect.email) == email_n)).limit(1)
        )
        if row is not None:
            return row

    if domain_n:
        stmt = select(Prospect).where(Prospect.domain == domain_n)
        if person_n:
            # Prefer same person at company when name known.
            candidates = list(db.scalars(_exclude(stmt).limit(25)).all())
            for cand in candidates:
                if normalize_person_name(cand.executive_name) == person_n:
                    return cand
            if candidates:
                return candidates[0]
        else:
            row = db.scalar(_exclude(stmt).limit(1))
            if row is not None:
                return row

    if company_n and not person_n and not domain_n:
        # No domain and no person (e.g. registry records without a website yet):
        # fall back to exact normalized company name among company-level records.
        candidates = list(
            db.scalars(
                _exclude(
                    select(Prospect).where(
                        func.lower(Prospect.company).ilike(f"%{(company or '').strip()[:60]}%")
                    )
                ).limit(50)
            ).all()
        )
        for cand in candidates:
            if normalize_company_name(cand.company) == company_n:
                return cand

    if company_n and person_n:
        # Soft match: scan recent rows with same person name, compare normalized company.
        candidates = list(
            db.scalars(
                _exclude(
                    select(Prospect).where(
                        func.lower(Prospect.executive_name) == (executive_name or "").strip().lower()
                    )
                ).limit(50)
            ).all()
        )
        for cand in candidates:
            if normalize_company_name(cand.company) == company_n:
                return cand
        # Broader: any company name soft-equal + similar person
        loose = list(
            db.scalars(
                _exclude(
                    select(Prospect).where(
                        or_(
                            func.lower(Prospect.company).ilike(f"%{(company or '').strip()}%"),
                        )
                    )
                ).limit(50)
            ).all()
        )
        for cand in loose:
            if (
                normalize_company_name(cand.company) == company_n
                and normalize_person_name(cand.executive_name) == person_n
            ):
                return cand

    return None
