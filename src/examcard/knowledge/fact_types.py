"""事实链内部流转的冻结数据类（拆分层 0）。

这些 dataclass 只在 fact_* 家族内部传参，不进任何落纸的规范化 JSON，
因此换模块不触碰字节权威（哈希载荷从不写 __module__/__qualname__）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..evidence.schema import BBox, EvidenceUnit
from ..materials.schema import PolicyDecision

from .diagram.sidecar import DiagramReviewEntry
from .diagram.disposition import DiagramDispositionEntry
from .schema import (
    AtomicFact,
    ContentQualityFlag,
    DiagramDispositionTrace,
    PresentationTransform,
    Renderability,
)


@dataclass(frozen=True)
class ReviewedConflict:
    conflict_id: str
    page_num: int
    bbox: BBox | None
    evidence_ids: tuple[str, ...]
    native_text: str
    canonical_text: str


@dataclass(frozen=True)
class EvidenceContext:
    unit: EvidenceUnit
    decision: PolicyDecision


@dataclass(frozen=True)
class FactBundle:
    fact: AtomicFact
    units: tuple[EvidenceUnit, ...]
    override_text: str | None = None
    diagram_review: DiagramReviewEntry | None = None
    diagram_disposition: "DiagramDispositionBinding | None" = None


@dataclass(frozen=True)
class RenderedEvidence:
    unit: EvidenceUnit
    text: str
    presentation_transforms: tuple[PresentationTransform, ...] = ()


@dataclass(frozen=True)
class DiagramAssessment:
    renderability: Renderability
    quality_flags: tuple[ContentQualityFlag, ...] = ()
    review: DiagramReviewEntry | None = None


@dataclass(frozen=True)
class DiagramDispositionBinding:
    entry: DiagramDispositionEntry
    trace: DiagramDispositionTrace
