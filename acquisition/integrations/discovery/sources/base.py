"""Shared contracts for discovery sources (harvesters).

A harvester answers one question: "give me companies in this slice of the
market". It never writes to the database and never decides ICP fit on its own —
filtering, dedupe, verification, research and scoring happen downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from acquisition.integrations.discovery.types import DiscoveryCandidate

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_BY_NAME = {v.lower(): k for k, v in US_STATES.items()}

REGION_BY_STATE: dict[str, str] = {}
for _codes, _region in (
    (("CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"), "Northeast"),
    (("IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"), "Midwest"),
    (("DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV", "AL", "KY", "MS", "TN",
      "AR", "LA", "OK", "TX"), "South"),
    (("AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"), "West"),
):
    for _c in _codes:
        REGION_BY_STATE[_c] = _region


def state_code(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text.upper() in US_STATES:
        return text.upper()
    return STATE_BY_NAME.get(text.lower())


@dataclass(slots=True)
class SliceSpec:
    """One slice of the market handed to a harvester."""

    key: str
    source: str
    category: str
    state: str | None = None
    city: str | None = None
    angle: str | None = None
    cursor: int = 0
    exclude_domains: list[str] = field(default_factory=list)

    @property
    def state_name(self) -> str | None:
        return US_STATES.get(self.state or "", self.state)

    @property
    def place(self) -> str:
        if self.city and self.state:
            return f"{self.city}, {self.state_name}"
        return self.state_name or "the United States"


@dataclass(slots=True)
class HarvestResult:
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    raw_count: int = 0
    next_cursor: int | None = None
    exhausted: bool = False
    error: str | None = None
    cost_usd: float = 0.0
    request: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Harvester(Protocol):
    name: str
    label: str

    def configured(self) -> bool: ...

    def status_message(self) -> str: ...

    def supports(self, category: str) -> bool: ...

    def harvest(self, spec: SliceSpec, *, limit: int) -> HarvestResult: ...
