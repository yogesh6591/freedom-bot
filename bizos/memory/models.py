"""Typed views over organizational memory rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from bizos.types import (
    ApprovalStatus,
    DataClassification,
    MemoryCategory,
    MemoryStatus,
    SourceType,
)


@dataclass
class MemoryVersion:
    """One value of one memory item at one point in its history.

    A version is never mutated in place except to be *closed* (status,
    ``effective_until``, ``superseded_by``). The value itself is immutable, which
    is what makes the history trustworthy.
    """

    id: str
    item_id: str
    version_no: int
    content: str
    status: MemoryStatus = MemoryStatus.ACTIVE
    attributes: dict[str, Any] = field(default_factory=dict)
    source_type: SourceType = SourceType.MANUAL
    source_id: Optional[str] = None
    created_by: str = ""
    updated_by: Optional[str] = None
    confidence: float = 1.0
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    superseded_by: Optional[str] = None
    supersedes: Optional[str] = None
    correction_reason: Optional[str] = None
    corrected_by: Optional[str] = None
    corrected_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.status == MemoryStatus.ACTIVE

    @property
    def is_authoritative(self) -> bool:
        """Active *and* approved — what the agent should prefer to state as fact."""
        return self.is_active and self.approval_status == ApprovalStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item_id": self.item_id,
            "version_no": self.version_no,
            "content": self.content,
            "status": str(self.status),
            "attributes": self.attributes,
            "source_type": str(self.source_type),
            "source_id": self.source_id,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "confidence": self.confidence,
            "approval_status": str(self.approval_status),
            "approved_by": self.approved_by,
            "approved_at": _iso(self.approved_at),
            "effective_from": _iso(self.effective_from),
            "effective_until": _iso(self.effective_until),
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "correction_reason": self.correction_reason,
            "corrected_by": self.corrected_by,
            "corrected_at": _iso(self.corrected_at),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "is_active": self.is_active,
            "is_authoritative": self.is_authoritative,
        }


@dataclass
class MemoryItem:
    """The stable identity of a piece of organizational knowledge."""

    id: str
    category: MemoryCategory
    memory_key: str
    title: str
    domain: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    classification: DataClassification = DataClassification.INTERNAL
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    current: Optional[MemoryVersion] = None
    #: Populated only by history reads.
    versions: list[MemoryVersion] = field(default_factory=list)

    def to_dict(self, *, include_versions: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "category": str(self.category),
            "memory_key": self.memory_key,
            "title": self.title,
            "domain": self.domain,
            "tags": self.tags,
            "classification": str(self.classification),
            "created_by": self.created_by,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "current": self.current.to_dict() if self.current else None,
        }
        if include_versions:
            data["versions"] = [v.to_dict() for v in self.versions]
        return data


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None
