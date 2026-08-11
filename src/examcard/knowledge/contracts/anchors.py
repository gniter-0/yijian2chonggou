"""Evidence anchor spans: deterministic selectable subranges of immutable Evidence."""
from __future__ import annotations
import hashlib

from pydantic import Field, model_validator

from ...evidence.schema import EvidenceKind
from ...materials.schema import PolicyDecision, StrictModel

from .hashing import _canonical_sha256
from .request_identity import _SHA256_PATTERN
from .versions import EVIDENCE_ANCHOR_SPAN_VERSION


_TABLE_KINDS = frozenset({EvidenceKind.TABLE, EvidenceKind.TABLE_ROW, EvidenceKind.TABLE_CELL})


_DIRECT_TEXT_KINDS = frozenset(
    {
        EvidenceKind.HEADING,
        EvidenceKind.PARAGRAPH,
        EvidenceKind.LIST_GROUP,
        EvidenceKind.LIST_ITEM,
        EvidenceKind.FORMULA,
        EvidenceKind.CAPTION,
    }
)


def make_evidence_anchor_span_id(
    *,
    parent_evidence_id: str,
    start: int,
    end: int,
    exact_text_sha256: str,
    content_policy_fingerprint_sha256: str,
    source_manifest_sha256: str,
) -> str:
    """Create a stable child anchor ID without modifying immutable Evidence."""

    digest = _canonical_sha256(
        {
            "version": EVIDENCE_ANCHOR_SPAN_VERSION,
            "parent_evidence_id": parent_evidence_id,
            "start": start,
            "end": end,
            "exact_text_sha256": exact_text_sha256,
            "content_policy_fingerprint_sha256": content_policy_fingerprint_sha256,
            "source_manifest_sha256": source_manifest_sha256,
        }
    )
    return f"span:{digest[:24]}"


class EvidenceAnchorSpan(StrictModel):
    """One deterministic, policy-bound selectable subrange of immutable Evidence."""

    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    span_version: str = EVIDENCE_ANCHOR_SPAN_VERSION
    parent_evidence_id: str
    parent_evidence_kind: EvidenceKind
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    canonical_text: str = Field(min_length=1)
    exact_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    policy_decision: PolicyDecision
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_anchor_span(self) -> "EvidenceAnchorSpan":
        if self.end <= self.start:
            raise ValueError("anchor spans require a non-empty character range")
        if self.policy_decision.evidence_id != self.parent_evidence_id:
            raise ValueError("anchor span policy decision must bind the parent Evidence")
        if self.exact_text_sha256 != hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest():
            raise ValueError("anchor span exact-text hash does not match its canonical text")
        expected_id = make_evidence_anchor_span_id(
            parent_evidence_id=self.parent_evidence_id,
            start=self.start,
            end=self.end,
            exact_text_sha256=self.exact_text_sha256,
            content_policy_fingerprint_sha256=self.content_policy_fingerprint_sha256,
            source_manifest_sha256=self.source_manifest_sha256,
        )
        if self.anchor_span_id != expected_id:
            raise ValueError("anchor span ID does not match its immutable binding")
        return self
