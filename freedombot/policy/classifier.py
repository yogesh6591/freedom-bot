"""
Regulated-content classifier (FB-039)
=====================================

Heuristic category detection over tool arguments. Not a clinical/PCI scanner —
it is a deterministic rule set that flags licensed / regulated subject matter so
the policy engine can force human approval or deny PHI/card when not permitted.

Categories: legal, medical, credit, employment, insurance, safety, financial_advice,
minor (personal data about a child) and biometrics (biometric identifiers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: category -> compiled patterns that indicate that category of content.
_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "legal": tuple(
        re.compile(p, re.I)
        for p in (
            r"\blegal advice\b",
            r"\battorney[-\s]?client\b",
            r"\bbreach of contract\b",
            r"\bliable\b|\bliability\b",
            r"\bindemnif",
            r"\bstatute of limitations\b",
            r"\byou (are|were) in violation\b",
            r"\bthis (is|constitutes) (a )?breach\b",
        )
    ),
    "medical": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bdiagnos(e|is|ed)\b",
            r"\bpatient\b",
            r"\bHIPAA\b",
            r"\bPHI\b",
            r"\bprescription\b",
            r"\bICD[-\s]?10\b",
            r"\bmedical record\b",
            r"\btreatment plan\b",
            r"\bblood (pressure|type|glucose)\b",
            r"\bmedical history\b",
            r"\ballerg(y|ies)\b",
            r"\broot canal\b",
            r"\bperiodontal\b",
            r"\bdental (chart|record|x-?ray)s?\b",
        )
    ),
    "credit": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bcredit (score|report|decision)\b",
            r"\bFICO\b",
            r"\bdeny(ing)? (the )?loan\b",
            r"\bunderwrit",
            r"\bSSN\b|\bsocial security\b",
            r"\bcard number\b|\bcvv\b|\bpan\b",
            r"\b4[0-9]{12}(?:[0-9]{3})?\b",  # crude card-shaped digit run
        )
    ),
    "employment": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bterminat(e|ion|ed)\b.*(employee|staff|worker)",
            r"\bfire (him|her|them|the employee)\b",
            r"\bdisciplinary action\b",
            r"\bwage (garnish|claim)\b",
            r"\bEEOC\b|\bunfair dismissal\b",
        )
    ),
    "insurance": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bclaim (denial|denied|approval)\b",
            r"\bcoverage determination\b",
            r"\binsurability\b",
            r"\bpolicy (void|cancelled)\b",
        )
    ),
    "safety": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bsafety critical\b",
            r"\blife[-\s]?threatening\b",
            r"\bemergency shutdown\b",
            r"\bhazard(ous)? (material|waste)\b",
        )
    ),
    "financial_advice": tuple(
        re.compile(p, re.I)
        for p in (
            r"\binvest(ment)? advice\b",
            r"\byou should (buy|sell|hold)\b",
            r"\bguaranteed returns?\b",
            r"\bportfolio allocation\b",
            r"\bSEC[-\s]?regulated\b",
        )
    ),
}

#: M07-13: personal data about a child. Strong signals are enough on their own;
#: a family word ("son", "kid") counts only next to a personal-data marker, so
#: "our childcare benefit" is not flagged.
_MINOR_STRONG = tuple(
    re.compile(p, re.I)
    for p in (
        r"\b(?:[1-9]|1[0-7])[-\s]?(?:year|yr)s?[-\s]?old\b",
        r"\bage[ds]?\s*:?\s*(?:[1-9]|1[0-7])\b",
        r"\b(?:elementary|primary|middle|junior high) school\b",
        r"\b(?:kindergarten(?:er)?|preschool(?:er)?|daycare|nursery school)\b",
        r"\b(?:[A-Z][a-z]+ )?Elementary\b",
        r"\bminors?\b",
        r"\b(?:parent|guardian)(?:'s)? (?:phone|contact|email|consent|name)\b",
        r"\blegal guardian\b",
        r"\bCOPPA\b",
    )
)
_MINOR_WEAK = re.compile(r"\b(?:child|children|kid|kids|son|daughter|toddler|infant|baby|student|pupil)\b", re.I)
_PERSONAL_MARKER = re.compile(
    r"\b(?:name|phone|email|address|school|birth|dob|age|grade|photo|contact)\b|@|\d{3}[-.\s]\d{4}",
    re.I,
)


def _minor_match(blob: str) -> str | None:
    for pattern in _MINOR_STRONG:
        m = pattern.search(blob)
        if m:
            return m.group(0)
    m = _MINOR_WEAK.search(blob)
    if m and _PERSONAL_MARKER.search(blob):
        return m.group(0)
    return None


#: FB-039: biometric identifiers (BIPA-style: fingerprint, face/hand geometry,
#: iris/retina scan, voiceprint). Strong signals name the identifier itself; the
#: bare word "biometric" or a device feature (Face ID) counts only next to a
#: personal-data marker, so "compare biometric time clocks" is not flagged.
_BIOMETRIC_STRONG = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bfinger\s*prints?\b",
        r"\bthumb\s*prints?\b",
        r"\bface\s*(?:scan|print|template|embedding|geometry|vector)s?\b",
        r"\bfacial\s*(?:recognition|geometry|template|scan|embedding)s?\b",
        r"\biris\s*(?:scan|print|template|image)s?\b",
        r"\bretina(?:l)?\s*(?:scan|print|image)s?\b",
        r"\bvoice\s*prints?\b",
        r"\bpalm\s*(?:print|vein)s?\b",
        r"\bhand\s*geometry\b",
        r"\bgait\s*(?:analysis|signature)s?\b",
        r"\bbiometric\s*(?:template|identifier|data|record|scan|sample|enrol+ment)s?\b",
    )
)
_BIOMETRIC_WEAK = re.compile(r"\b(?:biometric(?:s|al)?|face\s*id|touch\s*id)\b", re.I)
#: "fingerprint" in the technical sense (a hash), not a person's finger.
_TECH_FINGERPRINT = re.compile(
    r"\b(?:browser|digital|device|file|document|key|ssh|tls|ssl|certificate|gpg|pgp|"
    r"audio|content|request|schema|hash)\s+finger\s*prints?\b",
    re.I,
)


def _biometric_match(blob: str) -> str | None:
    cleaned = _TECH_FINGERPRINT.sub(" ", blob)
    for pattern in _BIOMETRIC_STRONG:
        m = pattern.search(cleaned)
        if m:
            return m.group(0)
    m = _BIOMETRIC_WEAK.search(cleaned)
    if m and _PERSONAL_MARKER.search(cleaned):
        return m.group(0)
    return None


#: Clinical terms specific enough to mean health information on their own.
#: The bare word "patient" is not one ("our patient retention rate").
_PHI_STRONG = re.compile(
    r"\b(?:diagnos(?:e|is|ed)|prescription|ICD[-\s]?10|medical (?:record|history)|"
    r"treatment plan|blood (?:pressure|type|glucose)|allerg(?:y|ies)|root canal|"
    r"periodontal|dental (?:chart|record|x-?ray)s?|MRN)\b",
    re.I,
)


#: Health details in plain words: conditions, procedures, treatment, care.
_HEALTH_TERMS = re.compile(
    r"\b(?:surger(?:y|ies)|surgical|operated on|post-?op|physio(?:therapy)?|physical therapy|"
    r"chemo(?:therapy)?|radiotherapy|radiation therapy|rehab(?:ilitation)?|recover(?:ing|y) from|"
    r"hospitali[sz]ed|in (?:the )?hospital|admitted to (?:the )?hospital|discharged from|medications?|"
    r"prescribed|dosage|diagnosed|illness|diseases?|cancer|tumou?rs?|diabet(?:es|ic)|asthma|depression|"
    r"anxiety disorder|bipolar|schizophreni\w*|adhd|autis\w*|pregnan(?:t|cy)|miscarriage|hiv|hepatitis|"
    r"had a stroke|heart attack|mental health|psychiatri\w*|therapy sessions?|injur(?:y|ies|ed)|"
    r"fractur(?:e|ed)|medical leave|health (?:condition|issue|problem|information|records?)|"
    r"medical (?:condition|appointment|procedure)|doctor'?s appointment)\b",
    re.I,
)
#: Someone the health details are about: a pronoun, a title, or a name doing something ("Nathan is ...").
_PERSON_REF = re.compile(
    r"(?i:\b(?:he|she|him|his|her|hers|patient|employee|client)\b)"
    r"|\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z]"
    r"|\b[A-Z][a-z]+(?:'s)?\s+(?:is|was|has|had|attends|takes|needs|underwent|got|will|recently)\b"
)


#: A particular person (not the generic "a patient"): a pronoun, a title, or a name doing something.
_NAMED_PERSON = re.compile(
    r"(?i:\b(?:he|she|him|his|her|hers)\b)"
    r"|\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z]"
    r"|\b[A-Z][a-z]+(?:'s)?\s+(?:is|was|has|had|attends|takes|needs|underwent|got|will|recently)\b"
)


def health_about_person(text: str) -> bool:
    """Health details about a particular person, in plain words ("Nathan is recovering from knee surgery").

    No clinical keyword or "store this" is needed: sharing these details is enough for them to count as PHI.
    """
    return bool(text and _HEALTH_TERMS.search(text) and _PERSON_REF.search(text))


def phi_about_someone(text: str) -> bool:
    """Health information that is about a particular person: plain-word health details tied to someone, or a
    clinical term next to a person or a personal detail. A policy that only mentions "diagnosis" is not."""
    if not text:
        return False
    if health_about_person(text):
        return True
    return bool(_PHI_STRONG.search(text) and (_NAMED_PERSON.search(text) or _PERSONAL_MARKER.search(text)))


def identifiable_phi(text: str) -> bool:
    """FB-039: health information tied to a person, not just a clinical word.

    Used before the model call (dental BAA vendor pattern): a strong clinical term, "patient" next to
    a personal-data marker, or health details about a person in plain words.
    """
    if not text:
        return False
    if _PHI_STRONG.search(text) or health_about_person(text):
        return True
    return bool(re.search(r"\bpatients?\b", text, re.I) and _PERSONAL_MARKER.search(text))


#: A US Social Security number: dashed on its own, or with any spacing next to a label. A dashed number
#: the SSA never issues (000, 666, 9xx; group 00; serial 0000) is treated as an ordinary reference.
_SSN_DASHED = re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])")
_SSN_LABELLED = re.compile(
    r"(?i)\b(?:ssn|ss#|social security(?: number| no\.?)?)\s*(?:is|was|:|#|=)?\s*\d{3}[ -]?\d{2}[ -]?\d{4}\b"
)
_SSN_WORD = re.compile(r"(?i)\bssns?\b|\bsocial security\b")


def find_ssn(text: str) -> bool:
    """Whether ``text`` holds an actual Social Security number, not just the word."""
    return bool(_SSN_LABELLED.search(text or "") or _SSN_DASHED.search(text or ""))


_PHI_CATEGORIES = frozenset({"medical"})
_CARD_CATEGORIES = frozenset({"credit"})
_BIOMETRIC_CATEGORIES = frozenset({"biometrics"})
#: Categories that always require a licensed human before any write/executes.
_LICENSED_CATEGORIES = frozenset(
    {
        "legal",
        "medical",
        "credit",
        "employment",
        "insurance",
        "safety",
        "financial_advice",
        "biometrics",
        "ssn",
    }
)


@dataclass(frozen=True)
class Classification:
    categories: frozenset[str]
    contains_phi: bool
    contains_card_data: bool
    requires_licensed_human: bool
    matches: tuple[tuple[str, str], ...]  # (category, snippet)
    #: M07-13: personal data about a child (under 18).
    contains_child_data: bool = False
    #: FB-039: biometric identifiers.
    contains_biometrics: bool = False
    #: A Social Security number, or a mention of one.
    contains_ssn: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": sorted(self.categories),
            "contains_phi": self.contains_phi,
            "contains_card_data": self.contains_card_data,
            "requires_licensed_human": self.requires_licensed_human,
            "contains_child_data": self.contains_child_data,
            "contains_biometrics": self.contains_biometrics,
            "contains_ssn": self.contains_ssn,
            "matches": [{"category": c, "snippet": s} for c, s in self.matches],
        }


def _walk_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _walk_strings(v)


def classify_text(*texts: str) -> Classification:
    """Classify free text for regulated / licensed subject matter."""
    found: set[str] = set()
    matches: list[tuple[str, str]] = []
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return Classification(frozenset(), False, False, False, ())
    for category, patterns in _PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(blob)
            if m:
                found.add(category)
                snippet = m.group(0)[:80]
                matches.append((category, snippet))
                break
    minor = _minor_match(blob)
    if minor:
        found.add("minor")
        matches.append(("minor", minor[:80]))
    if "medical" not in found and health_about_person(blob):
        found.add("medical")
        matches.append(("medical", "health details about a person"))
    biometric = _biometric_match(blob)
    if biometric:
        found.add("biometrics")
        matches.append(("biometrics", biometric[:80]))
    if find_ssn(blob) or _SSN_WORD.search(blob):
        found.add("ssn")
        matches.append(("ssn", "SSN"))  # never the number itself
    cats = frozenset(found)
    return Classification(
        categories=cats,
        contains_phi=bool(cats & _PHI_CATEGORIES),
        contains_card_data=bool(cats & _CARD_CATEGORIES),
        requires_licensed_human=bool(cats & _LICENSED_CATEGORIES),
        matches=tuple(matches),
        contains_child_data="minor" in cats,
        contains_biometrics=bool(cats & _BIOMETRIC_CATEGORIES),
        contains_ssn="ssn" in cats,
    )


def classify_arguments(arguments: dict[str, Any] | None) -> Classification:
    """Classify a tool payload (all string leaves)."""
    texts = list(_walk_strings(arguments or {}))
    return classify_text(*texts)


# --------------------------------------------------------------------------- licensed advice

_SPAN = r"[^.?!\n]{0,60}"
_DRUGS = (r"(?:\d+\s?mg|\d+\s?ml|pills?|tablets?|ibuprofen|paracetamol|acetaminophen|aspirin|tylenol|advil|insulin|"
          r"antibiotics?|medicines?|medications?|dose|dosage)")
_SECURITIES = (r"(?:stocks?|shares?|bonds?|crypto|cryptocurrenc(?:y|ies)|bitcoin|btc|ethereum|etfs?|mutual funds?|"
               r"index funds?|securities)")
_STAFF = r"(?:employees?|staff|workers?|contractors?|interns?|managers?|team members?|reps?)"

#: Someone asking the assistant for a professional's judgment on their own situation. Each pattern needs the
#: asking frame, not only the subject, so "what is our medical leave policy" or "find the liability clause"
#: stay ordinary lookups.
_ADVICE: dict[str, tuple[re.Pattern[str], ...]] = {
    "legal": tuple(
        re.compile(p)
        for p in (
            r"(?i)\blegal (?:advice|opinion)\b",
            rf"(?i)\b(?:is|are|was|would|will)\b{_SPAN}(?<!\bin )\b(?:legal|illegal|lawful|unlawful|enforceable|binding)"
            r"\s*(?:[?.!]|$|\b(?:to|for|in|under|if|here|or)\b)",
            r"(?i)\b(?:can|could|may|might|should)\s+(?:i|we|they|he|she)\s+(?:legally\b|sue\b|be sued\b|get sued\b)",
            r"(?i)\b(?:are we|am i)\s+(?:legally\s+)?(?:liable|in breach|at fault)\b",
            r"(?i)\bdo (?:i|we) have (?:a|any) (?:legal )?(?:case|claim)\b",
            r"(?i:\b(?:should|can|could)\s+(?:i|we)\s+(?:fire|sack|lay\s+off)\s+)"
            rf"(?:[A-Z][a-z]+|(?i:him|her|them|someone|(?:an?|the|this|that|our|my)\s+{_STAFF}))",
        )
    ),
    "tax": tuple(
        re.compile(p)
        for p in (
            r"(?i)\btax advice\b",
            rf"(?i)\bhow much\b{_SPAN}\btax(?:es)?\b{_SPAN}\b(?:do|should|will|would|must|did)\s+(?:i|we)\b",
            rf"(?i)\b(?:do|should|will|would|must)\s+(?:i|we)\b{_SPAN}\b(?:owe|pay|file)\b{_SPAN}\btax(?:es)?\b",
            r"(?is)(?=.*\b(?:tax|taxes|irs)\b).*\b(?:can|could|should|may)\s+(?:i|we)\s+(?:deduct|write\s+off|claim)\b",
            r"(?i)\b(?:can|could|should|may)\s+(?:i|we)\s+(?:deduct|write\s+off)\s+(?:the|my|our|a|an)?\s*"
            r"(?:car|vehicle|laptop|home office|travel|meals?|mileage|rent)\b",
            rf"(?i)\b(?:is|are)\b{_SPAN}\btax[- ]deductible\b",
            r"(?i)\bhow (?:do|can|should|could) (?:i|we) (?:avoid|reduce|lower|minimi[sz]e|cut|save on)\b[^.?!\n]{0,20}\btax(?:es)?\b",
        )
    ),
    "medical": tuple(
        re.compile(p)
        for p in (
            r"(?i)\bmedical advice\b",
            r"(?i)\bwhat\b[^.?!\n]{0,30}\b(?:medication|medicine|drug|dose|dosage|pills?|antibiotics?)\b"
            r"[^.?!\n]{0,40}\b(?:should|can|do|must)\s+(?:i|he|she|they|we)\b",
            rf"(?i)\bhow (?:much|many)\b{_SPAN}\b{_DRUGS}\b{_SPAN}\b(?:should|can|do)\s+(?:i|he|she|they)\s+take\b",
            rf"(?i)\bshould\s+(?:i|he|she|they)\s+(?:take|stop taking|start taking)\b{_SPAN}\b{_DRUGS}\b",
            r"(?i)\bshould\s+(?:i|he|she|they)\s+(?:see|go to|visit)\s+(?:a|the|my)\s+(?:doctor|gp|hospital|specialist)\b",
            r"(?i)\bwhat\b[^.?!\n]{0,20}\b(?:should|can)\s+(?:i|he|she|they)\s+take\s+for\b",
            r"(?i)\b(?:do|could|might)\s+(?:i|he|she|they)\s+have\b[^.?!\n]{0,30}\b"
            r"(?:cancer|diabetes|covid|an infection|depression|adhd|a tumou?r|an ulcer|pneumonia)\b",
            r"(?i)\bdiagnose\s+(?:my|his|her|their|this)\b[^.?!\n]{0,30}\b"
            r"(?:rash|pain|symptoms?|condition|illness|infection|headache|cough|fever)\b",
        )
    ),
    "investment": tuple(
        re.compile(p)
        for p in (
            r"(?i)\b(?:invest(?:ment)?|financial) advice\b",
            r"(?i)\b(?:should|can|could)\s+(?:i|we)\s+(?:buy|sell|hold|short|invest\s+in)\b"
            rf"{_SPAN}\b{_SECURITIES}\b",
            rf"(?i)\b(?:best|top|good|safest)\b[^.?!\n]{{0,20}}\b{_SECURITIES}\b[^.?!\n]{{0,20}}\b(?:to|for)\s+(?:buy|invest|hold)",
            r"(?i)\b(?:where|how)\s+should\s+(?:i|we)\s+(?:invest|put)\s+(?:my|our|the|this|company)?\s*"
            r"(?:cash|money|savings|profits?|reserves?|retirement|surplus)\b",
            rf"(?i)\b(?:will|is)\b{_SPAN}\b{_SECURITIES}\b{_SPAN}\b(?:go up|rise|fall|crash|a good (?:buy|investment))\b",
        )
    ),
}

#: Asking to hand the question to a professional is the right move, so it is never refused.
_ADVICE_REFERRAL = re.compile(
    r"(?i)\b(?:flag|escalate|raise|send|forward|refer|route|ask|book|find)\b[^.?!\n]{0,60}\b"
    r"(?:counsel|lawyers?|attorneys?|legal team|accountants?|cpa|doctors?|advis[eo]rs?)\b"
)


def licensed_advice_topic(text: str) -> Optional[str]:
    """The field (legal, tax, medical, investment) ``text`` asks for a professional's advice on, if any."""
    message = text or ""
    if _ADVICE_REFERRAL.search(message):
        return None
    return next((topic for topic, patterns in _ADVICE.items() if any(p.search(message) for p in patterns)), None)


#: A sentence that asks for something, used to tell a request with other parts from a bare advice question.
_OTHER_ASK = re.compile(
    r"(?i)\?|\b(?:also|what|how|who|when|where|show|list|find|draft|send|create|add|enable|update|book|schedule|tell me)\b"
)


def advice_is_one_part(text: str) -> bool:
    """Whether ``text`` also asks for other things, in sentences that do not ask for advice."""
    sentences = re.split(r"(?<=[.?!])\s+|\n+", text or "")
    return any(_OTHER_ASK.search(s) and not licensed_advice_topic(s) for s in sentences)

