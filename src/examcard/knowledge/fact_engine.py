"""Deterministic Phase 2 Fact/Card/Coverage runner for reviewed V2 evidence.

The runner intentionally consumes only immutable Step 1 snapshots plus the
reviewed sidecar. It does not call an LLM, OCR, Vision, or any V1 module.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..evidence.annotations import ConflictClassification, GoldenReviewSidecar, load_golden_review_sidecar
from ..evidence.golden import GoldenSample, GoldenSampleRegistry, load_golden_registry
from ..evidence.schema import BBox, EvidenceKind, EvidenceUnit, PageEvidence, SourceQuality
from ..materials.catalog import load_content_policy
from ..materials.schema import (
    NOISE_ROLES,
    ContentPolicy,
    CoverageRequirement,
    PolicyDecision,
    PolicyDisposition,
    PresentationTransformAction,
    SemanticRole,
)

from .diagram.sidecar import DiagramReviewEntry, DiagramReviewSidecar, load_diagram_review_sidecar, reviewed_entry_map
from .diagram.disposition import (
    DiagramDisposition,
    DiagramDispositionEntry,
    DiagramDispositionSidecar,
    is_released_disposition,
    load_diagram_disposition_sidecar,
)
from .identity import make_card_raw_id, make_fact_id
from .schema import (
    AtomicFact,
    CardPlan,
    ChapterQualityGate,
    CompletenessMode,
    ContentQualityFlag,
    ContentSegment,
    DiagramDispositionTrace,
    CoverageDisposition,
    CoverageMap,
    CoverageRecord,
    EvidenceRef,
    FactType,
    GroundingStatus,
    KnowledgeCard,
    NumberClaim,
    PresentationTransform,
    Renderability,
)


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.．]\d+)?(?:\s*(?:%|％|mm|cm|m|MPa|kPa|h|小时|年|天|万元|亿元|倍))?(?![A-Za-z])"
)
_CONSTRAINT_TERMS = ("不得", "不应", "严禁", "除外", "至少", "不少于", "不超过")
_CONTAINER_KINDS = frozenset({EvidenceKind.FIGURE, EvidenceKind.TABLE})
_VISUAL_KINDS = frozenset({EvidenceKind.FIGURE, EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE})
_EMBEDDED_EXAMPLE_RE = re.compile(r"(?:【?例题】?|例题[：:]|例如[：:]|【?答案】?|答案[：:]|答[：:])")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DIAGRAM_PLACEHOLDER = "图示待人工结构化"
# 仓根锚点：旧仓 scripts/knowledge_v2/fact_engine.py 的 parents[2] 即仓根；
# 新仓 src 布局下需 parents[3]（src/examcard/knowledge/ → 仓根），承载 data/、configs/ 等仓库相对资产契约。
ROOT = Path(__file__).resolve().parents[3]


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


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _bbox_overlaps(left: BBox | None, right: BBox | None) -> bool:
    if left is None or right is None:
        return False
    return max(left.x0, right.x0) < min(left.x1, right.x1) and max(left.y0, right.y0) < min(left.y1, right.y1)


def _bbox_contains(outer: BBox | None, inner: BBox | None) -> bool:
    if outer is None or inner is None:
        return False
    center_x = (inner.x0 + inner.x1) / 2
    center_y = (inner.y0 + inner.y1) / 2
    return outer.x0 <= center_x <= outer.x1 and outer.y0 <= center_y <= outer.y1


def _policy_decision_id(*, policy: ContentPolicy, evidence_id: str) -> str:
    digest = hashlib.sha256(f"{policy.policy_id}:{policy.revision}:{evidence_id}".encode("utf-8")).hexdigest()[:24]
    return f"pdec:{digest}"


def _matches_known_noise(*, unit: EvidenceUnit, policy: ContentPolicy) -> bool:
    texts = (unit.raw_text, unit.canonical_text)
    return any(re.search(pattern, text) for pattern in policy.known_noise_patterns for text in texts)


def decide_policy(*, unit: EvidenceUnit, policy: ContentPolicy) -> PolicyDecision:
    """Project one evidence unit to exactly one policy disposition."""

    role = unit.semantic_role
    if _matches_known_noise(unit=unit, policy=policy):
        role = SemanticRole.WATERMARK
        disposition = PolicyDisposition.IGNORED_NOISE
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "命中当前 Content Policy 的已知资料水印规则；原始 evidence 保留并记录该非破坏性分流。"
    elif role in NOISE_ROLES:
        disposition = PolicyDisposition.IGNORED_NOISE
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "页眉页脚、水印、页码或装饰只保留为证据，不进入产品内容。"
    elif role in policy.included_roles:
        disposition = PolicyDisposition.INCLUDED
        requirement = CoverageRequirement.REQUIRED if unit.must_cover_by_source else CoverageRequirement.OPTIONAL
        reason = "该语义角色由当前 Content Policy 消费。"
    elif role in policy.optional_roles:
        disposition = PolicyDisposition.INCLUDED
        requirement = CoverageRequirement.OPTIONAL
        reason = "该语义角色可作为结构或辅助信息被当前 Content Policy 消费。"
    elif role in policy.excluded_roles:
        disposition = PolicyDisposition.EXCLUDED_BY_POLICY
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "证据完整保留，但当前 Content Policy 不允许其进入核心知识卡。"
    elif role in policy.manual_review_roles:
        disposition = PolicyDisposition.MANUAL_REVIEW
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "当前 Content Policy 要求先人工决定该未知语义角色。"
    else:
        disposition = PolicyDisposition.MANUAL_REVIEW
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "语义角色未被当前 Content Policy 覆盖，不能静默丢弃。"
    return PolicyDecision(
        evidence_id=unit.evidence_id,
        semantic_role=role,
        disposition=disposition,
        coverage_requirement=requirement,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        reason=reason,
    )


def _load_selected_pages(*, sample: GoldenSample, evidence_root: Path) -> tuple[PageEvidence, ...]:
    pages: list[PageEvidence] = []
    for page_num in sample.selected_pages:
        path = evidence_root / sample.sample_id / "pages" / f"page_{page_num:04d}.json"
        if not path.is_file():
            raise ValueError(f"{sample.sample_id}: missing evidence snapshot {path}")
        page = PageEvidence.model_validate(_read_json(path))
        if page.page_num != page_num:
            raise ValueError(f"{sample.sample_id}: snapshot page mismatch for {page_num}")
        if any(unit.locator.document_sha256 != sample.pdf_sha256 for unit in page.evidence_units):
            raise ValueError(f"{sample.sample_id}: evidence source hash mismatch on page {page_num}")
        pages.append(page)
    return tuple(pages)


def _validate_diagram_review_sidecar(
    *,
    sidecar: DiagramReviewSidecar,
    registry: GoldenSampleRegistry,
    evidence_root: Path,
) -> None:
    if sidecar.registry_id != registry.registry_id:
        raise ValueError("diagram review sidecar registry does not match golden registry")
    if sidecar.material_profile_id != registry.material_profile_id:
        raise ValueError("diagram review sidecar material profile does not match golden registry")
    samples = {sample.sample_id: sample for sample in registry.samples}
    units_by_sample: dict[str, dict[str, EvidenceUnit]] = {}
    for entry in sidecar.entries:
        sample = samples.get(entry.sample_id)
        if sample is None or entry.pdf_sha256 != sample.pdf_sha256:
            raise ValueError(f"diagram review references an unknown or mismatched sample: {entry.review_id}")
        units = units_by_sample.get(entry.sample_id)
        if units is None:
            pages = _load_selected_pages(sample=sample, evidence_root=evidence_root)
            units = {unit.evidence_id: unit for page in pages for unit in page.evidence_units}
            units_by_sample[entry.sample_id] = units
        figure = units.get(entry.figure_evidence_id)
        if figure is None or figure.kind != EvidenceKind.FIGURE:
            raise ValueError(f"diagram review must bind a selected figure evidence unit: {entry.review_id}")
        if figure.locator.page_num != entry.page_num or figure.provenance.crop_path != entry.figure_crop_path:
            raise ValueError(f"diagram review figure binding mismatch: {entry.review_id}")
        crop_root = (evidence_root / entry.sample_id).resolve()
        crop_path = (crop_root / entry.figure_crop_path).resolve()
        if not crop_path.is_relative_to(crop_root) or not crop_path.is_file():
            raise ValueError(f"diagram review crop is missing or outside sample root: {entry.review_id}")
        if hashlib.sha256(crop_path.read_bytes()).hexdigest() != entry.figure_crop_sha256:
            raise ValueError(f"diagram review crop hash mismatch: {entry.review_id}")
        audit = entry.audit
        if audit is not None and audit.review_asset is not None:
            manifest_ref = audit.review_crop_manifest
            if manifest_ref is None:
                raise ValueError(f"diagram review high-resolution audit manifest is missing: {entry.review_id}")
            manifest_relative_path = Path(manifest_ref.manifest_path)
            if manifest_relative_path.is_absolute():
                raise ValueError(f"diagram review high-resolution manifest path must be repository-relative: {entry.review_id}")
            manifest_path = (ROOT / manifest_relative_path).resolve()
            if not manifest_path.is_relative_to(ROOT) or not manifest_path.is_file():
                raise ValueError(f"diagram review high-resolution manifest is missing or outside the repository: {entry.review_id}")
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_ref.manifest_sha256:
                raise ValueError(f"diagram review high-resolution manifest hash does not match: {entry.review_id}")
            manifest = _read_json(manifest_path)
            raw_entries = manifest.get("entries")
            if not isinstance(raw_entries, list):
                raise ValueError(f"diagram review high-resolution manifest entries must be a list: {entry.review_id}")
            manifest_entry = next(
                (item for item in raw_entries if isinstance(item, dict) and item.get("review_id") == entry.review_id),
                None,
            )
            if manifest_entry is None:
                raise ValueError(f"diagram review high-resolution manifest does not bind review: {entry.review_id}")
            asset = audit.review_asset
            expected_binding = {
                "sample_id": entry.sample_id,
                "pdf_sha256": entry.pdf_sha256,
                "page_num": entry.page_num,
                "figure_evidence_id": entry.figure_evidence_id,
                "original_crop_path": entry.figure_crop_path,
                "original_crop_sha256": entry.figure_crop_sha256,
                "review_crop_path": asset.review_crop_path,
                "review_crop_sha256": asset.review_crop_sha256,
                "render_dpi": asset.render_dpi,
                "margin_pt": asset.margin_pt,
                "figure_bbox": asset.figure_bbox.model_dump(mode="json"),
            }
            if any(manifest_entry.get(name) != value for name, value in expected_binding.items()):
                raise ValueError(f"diagram review high-resolution binding disagrees with manifest: {entry.review_id}")
            review_crop_relative_path = Path(asset.review_crop_path)
            if review_crop_relative_path.is_absolute():
                raise ValueError(f"diagram review high-resolution crop path must be repository-relative: {entry.review_id}")
            review_crop_path = (ROOT / review_crop_relative_path).resolve()
            if not review_crop_path.is_relative_to(ROOT) or not review_crop_path.is_file():
                raise ValueError(f"diagram review high-resolution crop is missing or outside the repository: {entry.review_id}")
            if hashlib.sha256(review_crop_path.read_bytes()).hexdigest() != asset.review_crop_sha256:
                raise ValueError(f"diagram review high-resolution crop hash does not match: {entry.review_id}")
        if any(candidate.evidence_id not in units for candidate in entry.candidate_nodes):
            raise ValueError(f"diagram review candidate does not belong to selected evidence: {entry.review_id}")
        if any(
            evidence_id not in units
            for node in entry.nodes
            for evidence_id in node.evidence_ids
        ):
                raise ValueError(f"diagram review node anchor does not belong to selected evidence: {entry.review_id}")


def _validate_diagram_disposition_sidecar(
    *,
    sidecar: DiagramDispositionSidecar,
    diagram_sidecar: DiagramReviewSidecar,
    diagram_sidecar_sha256: str,
    registry: GoldenSampleRegistry,
) -> dict[tuple[str, str], DiagramDispositionBinding]:
    if sidecar.registry_id != registry.registry_id:
        raise ValueError("diagram disposition sidecar registry does not match golden registry")
    if sidecar.material_profile_id != registry.material_profile_id:
        raise ValueError("diagram disposition sidecar material profile does not match golden registry")
    if sidecar.source_diagram_review_sidecar_id != diagram_sidecar.sidecar_id:
        raise ValueError("diagram disposition sidecar source ID does not match diagram review sidecar")
    if sidecar.source_diagram_review_sidecar_sha256 != diagram_sidecar_sha256:
        raise ValueError("diagram disposition sidecar source hash does not match diagram review sidecar")
    if sidecar.review_crop_manifest is None:
        raise ValueError("diagram disposition sidecar requires a high-resolution review crop manifest")

    manifest_relative_path = Path(sidecar.review_crop_manifest.manifest_path)
    if manifest_relative_path.is_absolute():
        raise ValueError("diagram disposition review crop manifest path must be repository-relative")
    manifest_path = (ROOT / manifest_relative_path).resolve()
    if not manifest_path.is_relative_to(ROOT) or not manifest_path.is_file():
        raise ValueError("diagram disposition review crop manifest is missing or outside the repository")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if manifest_sha256 != sidecar.review_crop_manifest.manifest_sha256:
        raise ValueError("diagram disposition review crop manifest hash does not match")
    manifest = _read_json(manifest_path)
    if manifest.get("source_sidecar_id") != diagram_sidecar.sidecar_id:
        raise ValueError("diagram disposition review crop manifest source ID does not match diagram review sidecar")
    if manifest.get("source_sidecar_sha256") != diagram_sidecar_sha256:
        raise ValueError("diagram disposition review crop manifest source hash does not match diagram review sidecar")
    if manifest.get("registry_id") != registry.registry_id or manifest.get("material_profile_id") != registry.material_profile_id:
        raise ValueError("diagram disposition review crop manifest registry binding does not match")
    raw_manifest_entries = manifest.get("entries")
    if not isinstance(raw_manifest_entries, list):
        raise ValueError("diagram disposition review crop manifest entries must be a list")
    manifest_entries = {
        str(item.get("review_id")): item
        for item in raw_manifest_entries
        if isinstance(item, dict) and isinstance(item.get("review_id"), str)
    }
    if len(manifest_entries) != len(raw_manifest_entries):
        raise ValueError("diagram disposition review crop manifest review IDs must be unique")

    drafts = {entry.review_id: entry for entry in diagram_sidecar.entries if entry.state.value == "draft"}
    entries = {entry.review_id: entry for entry in sidecar.entries}
    if set(entries) != set(drafts):
        raise ValueError("diagram disposition sidecar must classify exactly every draft diagram review")
    if set(manifest_entries) != set(drafts):
        raise ValueError("diagram disposition review crop manifest must cover exactly every draft diagram review")
    bindings: dict[tuple[str, str], DiagramDispositionBinding] = {}
    for review_id, disposition in entries.items():
        source = drafts[review_id]
        if (
            disposition.sample_id != source.sample_id
            or disposition.pdf_sha256 != source.pdf_sha256
            or disposition.page_num != source.page_num
            or disposition.figure_evidence_id != source.figure_evidence_id
            or disposition.figure_crop_path != source.figure_crop_path
            or disposition.figure_crop_sha256 != source.figure_crop_sha256
        ):
            raise ValueError(f"diagram disposition binding disagrees with draft diagram review: {review_id}")
        asset = disposition.review_asset
        if asset is None:
            raise ValueError(f"diagram disposition entry lacks a high-resolution review crop: {review_id}")
        manifest_entry = manifest_entries[review_id]
        expected_binding = {
            "sample_id": disposition.sample_id,
            "pdf_sha256": disposition.pdf_sha256,
            "page_num": disposition.page_num,
            "figure_evidence_id": disposition.figure_evidence_id,
            "original_crop_path": disposition.figure_crop_path,
            "original_crop_sha256": disposition.figure_crop_sha256,
            "review_crop_path": asset.review_crop_path,
            "review_crop_sha256": asset.review_crop_sha256,
            "render_dpi": asset.render_dpi,
            "margin_pt": asset.margin_pt,
            "figure_bbox": asset.figure_bbox.model_dump(mode="json"),
        }
        if any(manifest_entry.get(name) != value for name, value in expected_binding.items()):
            raise ValueError(f"diagram disposition high-resolution review binding disagrees with manifest: {review_id}")
        review_crop_relative_path = Path(asset.review_crop_path)
        if review_crop_relative_path.is_absolute():
            raise ValueError(f"diagram disposition review crop path must be repository-relative: {review_id}")
        review_crop_path = (ROOT / review_crop_relative_path).resolve()
        if not review_crop_path.is_relative_to(ROOT) or not review_crop_path.is_file():
            raise ValueError(f"diagram disposition review crop is missing or outside the repository: {review_id}")
        if hashlib.sha256(review_crop_path.read_bytes()).hexdigest() != asset.review_crop_sha256:
            raise ValueError(f"diagram disposition review crop hash does not match: {review_id}")
        bindings[(disposition.sample_id, disposition.figure_evidence_id)] = DiagramDispositionBinding(
            entry=disposition,
            trace=DiagramDispositionTrace(
                disposition_id=disposition.disposition_id,
                disposition=disposition.disposition.value,
                source_diagram_review_sidecar_id=sidecar.source_diagram_review_sidecar_id,
                source_diagram_review_sidecar_sha256=sidecar.source_diagram_review_sidecar_sha256,
                original_crop_path=disposition.figure_crop_path,
                original_crop_sha256=disposition.figure_crop_sha256,
                review_crop_manifest_path=sidecar.review_crop_manifest.manifest_path,
                review_crop_manifest_sha256=sidecar.review_crop_manifest.manifest_sha256,
                review_crop_path=asset.review_crop_path,
                review_crop_sha256=asset.review_crop_sha256,
                review_crop_render_dpi=asset.render_dpi,
                review_crop_bbox=asset.figure_bbox,
            ),
        )
    return bindings


def _conflict_evidence_ids(*, pages: tuple[PageEvidence, ...], page_num: int, bbox: BBox | None) -> tuple[str, ...]:
    page = next((item for item in pages if item.page_num == page_num), None)
    if page is None:
        raise ValueError(f"reviewed conflict page is missing from Evidence: {page_num}")
    return tuple(
        sorted(
            unit.evidence_id
            for unit in page.evidence_units
            if unit.source_quality == SourceQuality.CONFLICT
            and _bbox_overlaps(unit.locator.bbox, bbox)
        )
    )


def _reviewed_conflicts(
    *, sidecar: GoldenReviewSidecar, sample_id: str, pages: tuple[PageEvidence, ...]
) -> tuple[ReviewedConflict, ...]:
    sample = next((item for item in sidecar.samples if item.sample_id == sample_id), None)
    if sample is None:
        raise ValueError(f"review sidecar is missing {sample_id}")
    conflicts: list[ReviewedConflict] = []
    for page in sample.pages:
        for conflict in page.conflicts:
            if conflict.classification == ConflictClassification.MANUAL_REVIEW or conflict.requires_human_confirmation:
                raise ValueError(f"{sample_id}: unresolved review conflict {conflict.conflict_id}")
            if conflict.proposed_canonical_text is None:
                raise ValueError(f"{sample_id}: reviewed conflict lacks canonical text {conflict.conflict_id}")
            evidence_ids = _conflict_evidence_ids(
                pages=pages,
                page_num=page.page_num,
                bbox=conflict.locator.bbox,
            )
            if not evidence_ids:
                raise ValueError(f"{sample_id}: reviewed conflict has no exact conflict Evidence anchor {conflict.conflict_id}")
            conflicts.append(
                ReviewedConflict(
                    conflict_id=conflict.conflict_id,
                    page_num=page.page_num,
                    bbox=conflict.locator.bbox,
                    evidence_ids=evidence_ids,
                    native_text=conflict.native_text,
                    canonical_text=conflict.proposed_canonical_text,
                )
            )
    return tuple(conflicts)


def _conflict_is_resolved(unit: EvidenceUnit, conflicts: Iterable[ReviewedConflict]) -> bool:
    return any(unit.evidence_id in conflict.evidence_ids for conflict in conflicts)


def _reviewed_override_groups(
    *, contexts: tuple[EvidenceContext, ...], conflicts: tuple[ReviewedConflict, ...]
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Apply sidecar corrections as fact text without ever mutating evidence."""

    groups: list[tuple[tuple[str, ...], str]] = []
    already_used: set[str] = set()
    for conflict in conflicts:
        if conflict.native_text == conflict.canonical_text:
            continue
        matches = [
            context.unit
            for context in contexts
            if context.decision.disposition == PolicyDisposition.INCLUDED
            and context.unit.evidence_id in conflict.evidence_ids
        ]
        matches.sort(key=lambda item: item.order)
        if not matches:
            raise ValueError(f"reviewed conflict cannot be anchored to included evidence: {conflict.conflict_id}")
        joined = "".join(item.canonical_text for item in matches)
        if conflict.native_text not in joined:
            raise ValueError(f"reviewed conflict cannot replace its native text in evidence: {conflict.conflict_id}")
        ids = tuple(item.evidence_id for item in matches)
        if already_used & set(ids):
            raise ValueError(f"overlapping reviewed corrections require explicit adjudication: {conflict.conflict_id}")
        already_used.update(ids)
        groups.append((ids, joined.replace(conflict.native_text, conflict.canonical_text, 1)))
    return tuple(groups)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_evidence(unit: EvidenceUnit, policy: ContentPolicy) -> RenderedEvidence:
    """Return safe derived text while leaving canonical Evidence immutable.

    Every rule is recorded with the evidence ID, source span and before/after
    hashes. This is deliberately a rendering transform, not an OCR correction.
    """

    text = unit.canonical_text.strip()
    if not text or text.startswith("image:"):
        return RenderedEvidence(unit=unit, text="")

    transforms: list[PresentationTransform] = []
    for rule in policy.presentation_transform_rules:
        matcher = re.compile(rule.pattern)
        matches = list(matcher.finditer(text))
        if not matches:
            continue
        if rule.action == PresentationTransformAction.REMOVE_MATCHES:
            for match in reversed(matches):
                before = text
                text = before[: match.start()] + before[match.end() :]
                transforms.append(
                    PresentationTransform(
                        evidence_id=unit.evidence_id,
                        start=match.start(),
                        end=match.end(),
                        rule_id=rule.rule_id,
                        before_sha256=_text_sha256(before),
                        after_sha256=_text_sha256(text),
                    )
                )
        elif rule.action == PresentationTransformAction.TRUNCATE_FROM_MATCH:
            match = matches[0]
            before = text
            text = before[: match.start()].rstrip()
            transforms.append(
                PresentationTransform(
                    evidence_id=unit.evidence_id,
                    start=match.start(),
                    end=match.end(),
                    rule_id=rule.rule_id,
                    before_sha256=_text_sha256(before),
                    after_sha256=_text_sha256(text),
                )
            )
    return RenderedEvidence(unit=unit, text=text.strip(), presentation_transforms=tuple(transforms))


def _render_evidence_map(units: tuple[EvidenceUnit, ...], policy: ContentPolicy) -> dict[str, RenderedEvidence]:
    return {unit.evidence_id: _render_evidence(unit, policy) for unit in units}


def _display_text(unit: EvidenceUnit, rendered: dict[str, RenderedEvidence] | None = None) -> str:
    if rendered is not None:
        return rendered[unit.evidence_id].text
    text = unit.canonical_text.strip()
    if text and not text.startswith("image:"):
        return text
    return ""


def _content_units(
    units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence] | None = None
) -> tuple[EvidenceUnit, ...]:
    """Choose one textual layer while retaining every source anchor in the fact.

    Tables expose a complete table, rows, and cells; rendering all three repeats
    the same text. Figures expose an image marker plus nodes/edges; only visible
    node/edge labels belong in deterministic card content.
    """

    table_units = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE and _display_text(unit, rendered))
    if table_units:
        return table_units
    table_rows = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE_ROW and _display_text(unit, rendered))
    if table_rows:
        return table_rows
    table_cells = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE_CELL and _display_text(unit, rendered))
    if table_cells:
        return table_cells
    diagram_labels = tuple(
        unit
        for unit in units
        if unit.kind in {EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE} and _display_text(unit, rendered)
    )
    if diagram_labels:
        return diagram_labels
    return tuple(unit for unit in units if _display_text(unit, rendered))


def _group_text(units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence]) -> str:
    texts = [_display_text(unit, rendered) for unit in _content_units(units, rendered)]
    if texts:
        return "\n".join(texts)
    if any(unit.kind in _VISUAL_KINDS for unit in units):
        return "图示结构"
    return units[0].kind.value


def _fact_type(units: tuple[EvidenceUnit, ...], text: str) -> FactType:
    kinds = {unit.kind for unit in units}
    if kinds & _VISUAL_KINDS:
        return FactType.DIAGRAM_RELATION
    if EvidenceKind.FORMULA in kinds or "=" in text:
        return FactType.FORMULA
    if EvidenceKind.TABLE in kinds or EvidenceKind.TABLE_ROW in kinds or EvidenceKind.TABLE_CELL in kinds:
        return FactType.COMPOSITION
    if all(unit.kind == EvidenceKind.LIST_ITEM for unit in units):
        return FactType.ENUMERATION
    if any(term in text for term in _CONSTRAINT_TERMS):
        return FactType.EXCEPTION
    if _NUMBER_RE.search(text):
        return FactType.THRESHOLD
    return FactType.DEFINITION


def _completeness_mode(fact_type: FactType) -> CompletenessMode:
    if fact_type == FactType.FORMULA:
        return CompletenessMode.COMPLETE_FORMULA
    if fact_type == FactType.ENUMERATION:
        return CompletenessMode.ALL_ITEMS
    if fact_type == FactType.COMPOSITION:
        return CompletenessMode.FULL_TABLE_ROW
    if fact_type == FactType.DIAGRAM_RELATION:
        return CompletenessMode.FULL_DIAGRAM_SUBGRAPH
    return CompletenessMode.ATOMIC


def _number_claims(
    *, text: str, units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence]
) -> tuple[NumberClaim, ...]:
    claims: list[NumberClaim] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        evidence_id = next((unit.evidence_id for unit in units if raw in _display_text(unit, rendered)), units[0].evidence_id)
        comparator = next((term for term in _CONSTRAINT_TERMS if term in text), None)
        unit = next((item for item in units if item.evidence_id == evidence_id), units[0])
        normalized = raw.replace("％", "%").replace("．", ".").replace(" ", "")
        claims.append(NumberClaim(raw=raw, normalized=normalized, comparator=comparator, evidence_id=unit.evidence_id))
    return tuple(claims)


def _label(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:64] if len(compact) >= 2 else "图示结构"


def _is_readable_diagram_label(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 2 or "\ufffd" in compact:
        return False
    if _CJK_RE.search(compact):
        return True
    return not bool(re.search(r"[A-Za-z]", compact)) and bool(re.search(r"\d", compact))


def _reviewed_diagram_for_units(
    *,
    sample_id: str,
    units: tuple[EvidenceUnit, ...],
    reviews: dict[tuple[str, str], DiagramReviewEntry],
) -> DiagramReviewEntry | None:
    figures = [unit for unit in units if unit.kind == EvidenceKind.FIGURE]
    if len(figures) != 1:
        return None
    figure = figures[0]
    entry = reviews.get((sample_id, figure.evidence_id))
    if entry is None:
        return None
    if entry.pdf_sha256 != figure.locator.document_sha256 or entry.page_num != figure.locator.page_num:
        raise ValueError(f"{sample_id}: diagram review binding disagrees with source figure {figure.evidence_id}")
    if entry.figure_crop_path != figure.provenance.crop_path:
        raise ValueError(f"{sample_id}: diagram review crop path disagrees with source figure {figure.evidence_id}")
    return entry


def _diagram_disposition_for_units(
    *,
    sample_id: str,
    units: tuple[EvidenceUnit, ...],
    dispositions: dict[tuple[str, str], DiagramDispositionBinding],
) -> DiagramDispositionBinding | None:
    figures = [unit for unit in units if unit.kind == EvidenceKind.FIGURE]
    if len(figures) != 1:
        return None
    figure = figures[0]
    binding = dispositions.get((sample_id, figure.evidence_id))
    if binding is None:
        return None
    entry = binding.entry
    if entry.pdf_sha256 != figure.locator.document_sha256 or entry.page_num != figure.locator.page_num:
        raise ValueError(f"{sample_id}: diagram disposition binding disagrees with source figure {figure.evidence_id}")
    if entry.figure_crop_path != figure.provenance.crop_path:
        raise ValueError(f"{sample_id}: diagram disposition crop path disagrees with source figure {figure.evidence_id}")
    return binding


def _reviewed_diagram_text(review: DiagramReviewEntry) -> str:
    nodes = "、".join(node.label for node in review.nodes)
    node_by_id = {node.node_id: node.label for node in review.nodes}
    relations = "；".join(
        f"{node_by_id[edge.source_node_id]}{edge.relation}{node_by_id[edge.target_node_id]}"
        for edge in review.edges
    )
    return f"{review.title}\n节点：{nodes}\n关系：{relations}"


def _diagram_assessment(
    *, units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence], review: DiagramReviewEntry | None = None
) -> DiagramAssessment:
    """Fail closed when visual OCR cannot form a readable directed relation."""

    if review is not None:
        return DiagramAssessment(renderability=Renderability.PUBLISHABLE, review=review)

    node_labels = [
        _display_text(unit, rendered)
        for unit in units
        if unit.kind == EvidenceKind.DIAGRAM_NODE and _display_text(unit, rendered)
    ]
    readable_nodes = [label for label in node_labels if _is_readable_diagram_label(label)]
    edge_texts = [
        _display_text(unit, rendered)
        for unit in units
        if unit.kind == EvidenceKind.DIAGRAM_EDGE and _display_text(unit, rendered)
    ]
    readable_edges = []
    for edge in edge_texts:
        if edge.count("->") != 1:
            continue
        source, target = (part.strip() for part in edge.split("->"))
        if 2 <= len(source) <= 40 and 2 <= len(target) <= 40 and _is_readable_diagram_label(source) and _is_readable_diagram_label(target):
            readable_edges.append(edge)

    flags: list[ContentQualityFlag] = []
    if len(readable_nodes) < 2 or not readable_edges:
        flags.append(ContentQualityFlag.DIAGRAM_MISSING_STRUCTURE)
    if node_labels and len(readable_nodes) * 2 < len(node_labels):
        flags.append(ContentQualityFlag.DIAGRAM_UNREADABLE_OCR)
    return DiagramAssessment(
        renderability=Renderability.MANUAL_REVIEW if flags else Renderability.PUBLISHABLE,
        quality_flags=tuple(flags),
    )


def _make_fact(
    *,
    units: tuple[EvidenceUnit, ...],
    rendered: dict[str, RenderedEvidence],
    override_text: str | None = None,
    diagram_review: DiagramReviewEntry | None = None,
    diagram_disposition: DiagramDispositionBinding | None = None,
) -> AtomicFact:
    if diagram_review is not None and diagram_disposition is not None:
        raise ValueError("a diagram fact cannot use both a structure review and a disposition")
    disposition_entry = diagram_disposition.entry if diagram_disposition is not None else None
    released_visual = disposition_entry is not None and is_released_disposition(disposition_entry.disposition)
    text = (
        _reviewed_diagram_text(diagram_review)
        if diagram_review is not None
        else (disposition_entry.title if released_visual else (override_text or _group_text(units, rendered)))
    )
    if text is None:
        raise ValueError("released visual disposition requires a display title")
    fact_type = FactType.VISUAL_REFERENCE if released_visual else _fact_type(units, text)
    assessment = (
        _diagram_assessment(units=units, rendered=rendered, review=diagram_review)
        if fact_type == FactType.DIAGRAM_RELATION
        else DiagramAssessment(renderability=Renderability.PUBLISHABLE)
    )
    if assessment.renderability == Renderability.MANUAL_REVIEW:
        text = _DIAGRAM_PLACEHOLDER
    mode = CompletenessMode.ATOMIC if fact_type == FactType.VISUAL_REFERENCE else _completeness_mode(fact_type)
    numbers = _number_claims(text=text, units=units, rendered=rendered)
    assertion_contract = {
        "assertion": text,
        "completeness_mode": mode.value,
        "numbers": [item.model_dump(mode="json") for item in numbers],
        "conditions": [term for term in _CONSTRAINT_TERMS if term in text],
    }
    return AtomicFact(
        fact_id=make_fact_id(
            fact_type=fact_type.value,
            primary_evidence_fingerprints=[unit.fingerprint for unit in units],
            assertion_contract=assertion_contract,
        ),
        fact_type=fact_type,
        label=(diagram_review.title if diagram_review is not None else (disposition_entry.title if released_visual else _label(text))),
        assertion=text,
        completeness_mode=mode,
        primary_evidence_ids=tuple(unit.evidence_id for unit in units),
        conditions=tuple(term for term in _CONSTRAINT_TERMS if term in text),
        numbers=numbers,
        ordered_member_ids=tuple(unit.evidence_id for unit in units) if mode != CompletenessMode.ATOMIC else (),
        must_cover=any(unit.must_cover_by_source for unit in units),
        confidence=min(unit.confidence for unit in units),
        renderability=assessment.renderability,
        quality_flags=(
            (ContentQualityFlag.VISUAL_REFERENCE_ONLY,)
            if released_visual
            else assessment.quality_flags
        ),
        presentation_transforms=tuple(
            transform
            for unit in units
            for transform in rendered[unit.evidence_id].presentation_transforms
        ),
        diagram_review_id=assessment.review.review_id if assessment.review is not None else None,
        diagram_disposition_id=disposition_entry.disposition_id if released_visual else None,
        diagram_disposition_trace=diagram_disposition.trace if released_visual and diagram_disposition is not None else None,
    )


def _group_included_evidence(
    *, contexts: tuple[EvidenceContext, ...], overrides: tuple[tuple[tuple[str, ...], str], ...]
) -> tuple[tuple[tuple[EvidenceUnit, ...], str | None], ...]:
    included = [context.unit for context in contexts if context.decision.disposition == PolicyDisposition.INCLUDED]
    by_id = {unit.evidence_id: unit for unit in included}
    consumed: set[str] = set()
    groups: list[tuple[tuple[EvidenceUnit, ...], str | None]] = []

    for ids, override_text in overrides:
        units = tuple(by_id[evidence_id] for evidence_id in ids)
        consumed.update(ids)
        groups.append((units, override_text))

    for container in sorted((unit for unit in included if unit.kind in _CONTAINER_KINDS), key=lambda item: (item.locator.page_num, item.order)):
        if container.evidence_id in consumed:
            continue
        members = [
            unit
            for unit in included
            if unit.evidence_id not in consumed
            and unit.locator.page_num == container.locator.page_num
            and (_bbox_contains(container.locator.bbox, unit.locator.bbox) or unit.evidence_id == container.evidence_id)
        ]
        if not members:
            members = [container]
        members.sort(key=lambda item: item.order)
        consumed.update(unit.evidence_id for unit in members)
        groups.append((tuple(members), None))

    current: list[EvidenceUnit] = []
    current_page: int | None = None
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            groups.append((tuple(current), None))
        current = []
        current_size = 0

    for unit in sorted((item for item in included if item.evidence_id not in consumed), key=lambda item: (item.locator.page_num, item.order)):
        unit_size = len(_display_text(unit))
        if current_page is not None and unit.locator.page_num != current_page:
            flush()
        if unit.kind == EvidenceKind.HEADING and current:
            flush()
        if current and current_size + unit_size > 500 and unit.kind not in {EvidenceKind.LIST_ITEM, EvidenceKind.FORMULA}:
            flush()
        current.append(unit)
        current_page = unit.locator.page_num
        current_size += unit_size
    flush()
    return tuple(groups)


def _presentation(fact_type: FactType) -> str:
    return {
        FactType.FORMULA: "formula",
        FactType.ENUMERATION: "ordered_list",
        FactType.COMPOSITION: "table",
        FactType.DIAGRAM_RELATION: "diagram",
        FactType.VISUAL_REFERENCE: "visual_reference",
        FactType.EXCEPTION: "constraint",
        FactType.THRESHOLD: "threshold",
    }.get(fact_type, "text")


def _make_card(
    *,
    sample_id: str,
    bundle: FactBundle,
    material_profile_id: str,
    policy: ContentPolicy,
    rendered: dict[str, RenderedEvidence],
) -> tuple[CardPlan, KnowledgeCard]:
    fact = bundle.fact
    raw_id = make_card_raw_id(
        material_profile_id=material_profile_id,
        content_policy_id=policy.policy_id,
        primary_fact_ids=[fact.fact_id],
    )
    plan = CardPlan(
        raw_id=raw_id,
        title=fact.label,
        primary_fact_ids=(fact.fact_id,),
        kp_type="visual_reference" if fact.fact_type == FactType.VISUAL_REFERENCE else "content",
        presentation=_presentation(fact.fact_type),
        split_reason="按唯一 primary evidence anchor span 分组；不与相似标题跨 span 合并。",
    )
    if bundle.diagram_review is not None:
        segments = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="reviewed_diagram",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition):
        segments = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="visual_reference",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif fact.renderability == Renderability.MANUAL_REVIEW:
        segments = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="manual_review",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif bundle.override_text is not None:
        segments = (
            ContentSegment(
                text=bundle.override_text,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="reviewed_conflict",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    else:
        content_units = _content_units(bundle.units, rendered)
        segments = tuple(
            ContentSegment(
                text=_display_text(unit, rendered),
                fact_ids=(fact.fact_id,),
                evidence_ids=(unit.evidence_id,),
                renderer=_presentation(fact.fact_type),
                presentation_transforms=rendered[unit.evidence_id].presentation_transforms,
            )
            for unit in content_units
        )
        if not segments:
            segments = (
                ContentSegment(
                    text=fact.assertion,
                    fact_ids=(fact.fact_id,),
                    evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                    renderer="structural_anchor",
                    presentation_transforms=fact.presentation_transforms,
                ),
            )
    refs = tuple(
        EvidenceRef(
            evidence_id=unit.evidence_id,
            page_num=unit.locator.page_num,
            bbox=unit.locator.bbox,
            visual_only=unit.source_quality == SourceQuality.VISUAL_ONLY,
        )
        for unit in bundle.units
    )
    card = KnowledgeCard(
        id=f"{sample_id}:{raw_id}",
        raw_id=raw_id,
        title=fact.label,
        content="\n".join(segment.text for segment in segments),
        content_segments=segments,
        evidence_refs=refs,
        grounding_status=(
            GroundingStatus.MANUAL_VERIFIED
            if bundle.diagram_review is not None or bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition)
            else (GroundingStatus.UNRESOLVED if fact.renderability == Renderability.MANUAL_REVIEW else GroundingStatus.VERIFIED)
        ),
        renderability=fact.renderability,
        quality_flags=fact.quality_flags,
        key_words=tuple(sorted({fact.fact_type.value, *fact.conditions})),
        diagram_type=(
            bundle.diagram_review.diagram_type
            if bundle.diagram_review is not None
            else (bundle.diagram_disposition.entry.disposition.value if bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition) else ("flow" if fact.fact_type == FactType.DIAGRAM_RELATION else ""))
        ),
        diagram_elements=(
            tuple(node.label for node in bundle.diagram_review.nodes)
            if bundle.diagram_review is not None
            else ()
        ),
        annotations=(
            tuple(
                f"{next(node.label for node in bundle.diagram_review.nodes if node.node_id == edge.source_node_id)}"
                f"{edge.relation}"
                f"{next(node.label for node in bundle.diagram_review.nodes if node.node_id == edge.target_node_id)}"
                for edge in bundle.diagram_review.edges
            )
            if bundle.diagram_review is not None
            else ()
        ),
        diagram_review_id=fact.diagram_review_id,
        diagram_disposition_id=fact.diagram_disposition_id,
        diagram_disposition_trace=fact.diagram_disposition_trace,
        source_pages=tuple(sorted({unit.locator.page_num for unit in bundle.units})),
        kp_type=plan.kp_type,
    )
    return plan, card


def _dedup_audit(cards: tuple[KnowledgeCard, ...], facts: tuple[AtomicFact, ...]) -> dict[str, Any]:
    fact_by_id = {fact.fact_id: fact for fact in facts}
    spans: dict[tuple[str, ...], list[KnowledgeCard]] = defaultdict(list)
    titles: dict[str, list[KnowledgeCard]] = defaultdict(list)
    for card in cards:
        fact = fact_by_id[card.content_segments[0].fact_ids[0]]
        spans[tuple(sorted(fact.primary_evidence_ids))].append(card)
        titles["".join(card.title.split())].append(card)
    exact_duplicates = [
        {"primary_evidence_ids": list(span), "card_raw_ids": [card.raw_id for card in group]}
        for span, group in spans.items()
        if len(group) > 1
    ]
    title_candidates = [
        {"title": title, "card_raw_ids": [card.raw_id for card in group], "decision": "candidate_only_distinct_anchor_span"}
        for title, group in titles.items()
        if len(group) > 1 and len({card.raw_id for card in group}) == len(group)
    ]
    return {
        "engine": "anchor-span-dedup-v1",
        "auto_merged": [],
        "exact_primary_span_duplicates": exact_duplicates,
        "title_similarity_candidates": title_candidates,
        "unresolved_candidates": 0,
    }


def _machine_assertions(
    *,
    sample: GoldenSample,
    units: tuple[EvidenceUnit, ...],
    coverage: CoverageMap,
    conflicts: tuple[ReviewedConflict, ...],
    policy: ContentPolicy,
) -> tuple[dict[str, Any], ...]:
    coverage_by_id = {record.evidence_id: record for record in coverage.records}
    results: list[dict[str, Any]] = []
    for assertion in sample.assertions:
        page_num = int(assertion.assertion_id.split("_", 1)[0].removeprefix("page"))
        page_units = [unit for unit in units if unit.locator.page_num == page_num]
        missing_kinds = [kind.value for kind in assertion.expected_evidence_kinds if not any(unit.kind == kind for unit in page_units)]
        match_results: list[dict[str, Any]] = []
        for expected in assertion.expected_matches:
            matches = [
                unit
                for unit in page_units
                if expected.text in unit.canonical_text
                and (expected.kind is None or unit.kind == expected.kind)
                and (expected.semantic_role is None or unit.semantic_role == expected.semantic_role)
                and (expected.must_cover is None or unit.must_cover_by_source == expected.must_cover)
            ]
            dispositions = [coverage_by_id[unit.evidence_id].disposition.value for unit in matches]
            expected_excluded = expected.semantic_role in policy.excluded_roles if expected.semantic_role else False
            passed = bool(matches) and (
                all(item == CoverageDisposition.EXCLUDED_BY_POLICY.value for item in dispositions)
                if expected_excluded
                else any(item == CoverageDisposition.PRIMARY_ASSIGNED.value for item in dispositions)
            )
            match_results.append(
                {
                    "text": expected.text,
                    "matched_evidence_ids": [unit.evidence_id for unit in matches],
                    "dispositions": dispositions,
                    "passed": passed,
                }
            )
        conflict_results = [
            {
                "native_text": native,
                "canonical_text": canonical,
                "passed": any(item.native_text == native and item.canonical_text == canonical for item in conflicts),
            }
            for native, canonical in assertion.expected_conflicts
        ]
        passed = not missing_kinds and all(item["passed"] for item in match_results + conflict_results)
        results.append(
            {
                "assertion_id": assertion.assertion_id,
                "passed": passed,
                "missing_evidence_kinds": missing_kinds,
                "expected_matches": match_results,
                "expected_conflicts": conflict_results,
            }
        )
    return tuple(results)


def _quality_gate(
    *,
    contexts: tuple[EvidenceContext, ...],
    bundles: tuple[FactBundle, ...],
    cards: tuple[KnowledgeCard, ...],
    coverage: CoverageMap,
    conflicts: tuple[ReviewedConflict, ...],
    dedup: dict[str, Any],
    policy: ContentPolicy,
    audit_excluded_evidence_ids: frozenset[str] = frozenset(),
) -> ChapterQualityGate:
    primary_ids = {evidence_id for bundle in bundles for evidence_id in bundle.fact.primary_evidence_ids}
    records = {record.evidence_id: record for record in coverage.records}
    required_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.decision.disposition == PolicyDisposition.INCLUDED
        and context.decision.coverage_requirement == CoverageRequirement.REQUIRED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    formula_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.unit.kind == EvidenceKind.FORMULA
        and context.decision.disposition == PolicyDisposition.INCLUDED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    list_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.unit.kind == EvidenceKind.LIST_ITEM
        and context.decision.disposition == PolicyDisposition.INCLUDED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    formula_fact_ids = {
        evidence_id
        for bundle in bundles
        if bundle.fact.completeness_mode == CompletenessMode.COMPLETE_FORMULA
        for evidence_id in bundle.fact.primary_evidence_ids
    }
    unresolved = [
        context.unit
        for context in contexts
        if context.unit.evidence_id not in audit_excluded_evidence_ids
        and (
            context.decision.disposition == PolicyDisposition.MANUAL_REVIEW
        or context.unit.source_quality == SourceQuality.UNRESOLVED
        or (context.unit.source_quality == SourceQuality.CONFLICT and not _conflict_is_resolved(context.unit, conflicts))
        )
    ]
    numeric_failures = sum(
        1
        for bundle in bundles
        for number in bundle.fact.numbers
        if number.raw not in bundle.fact.assertion or number.evidence_id not in bundle.fact.primary_evidence_ids
    )
    rendered_texts = tuple(bundle.fact.assertion for bundle in bundles) + tuple(card.content for card in cards)
    known_noise_patterns = tuple(policy.known_noise_patterns) + tuple(
        rule.pattern for rule in policy.presentation_transform_rules if rule.rule_id.startswith("watermark-")
    )
    known_noise_leaks = sum(
        any(re.search(pattern, text) for pattern in known_noise_patterns)
        for text in rendered_texts
    )
    embedded_example_leaks = sum(bool(_EMBEDDED_EXAMPLE_RE.search(text)) for text in rendered_texts)
    unrenderable_diagrams = sum(
        bundle.fact.fact_type == FactType.DIAGRAM_RELATION
        and bundle.fact.renderability == Renderability.MANUAL_REVIEW
        for bundle in bundles
    )
    return ChapterQualityGate(
        must_cover_total=len(required_ids),
        must_cover_primary_assigned=sum(
            records[evidence_id].disposition == CoverageDisposition.PRIMARY_ASSIGNED for evidence_id in required_ids
        ),
        unresolved_evidence=len(unresolved),
        fallback_content=0,
        numeric_failures=numeric_failures,
        enumeration_failures=len(list_ids - primary_ids),
        formula_failures=len(formula_ids - formula_fact_ids),
        unresolved_visual_only=sum(
            context.unit.source_quality == SourceQuality.VISUAL_ONLY
            and context.unit.must_cover_by_source
            and context.decision.disposition == PolicyDisposition.INCLUDED
            and context.unit.evidence_id not in primary_ids
            and context.unit.evidence_id not in audit_excluded_evidence_ids
            for context in contexts
        ),
        unresolved_dedup_candidates=int(dedup["unresolved_candidates"]),
        known_noise_leaks=known_noise_leaks,
        embedded_example_leaks=embedded_example_leaks,
        unrenderable_diagrams=unrenderable_diagrams,
        schema_valid=True,
        lineage_valid=True,
        adapter_valid=True,
    )


def run_sample(
    *,
    sample: GoldenSample,
    pages: tuple[PageEvidence, ...],
    policy: ContentPolicy,
    conflicts: tuple[ReviewedConflict, ...],
    material_profile_id: str,
    diagram_reviews: dict[tuple[str, str], DiagramReviewEntry] | None = None,
    diagram_dispositions: dict[tuple[str, str], DiagramDispositionBinding] | None = None,
) -> dict[str, Any]:
    units = tuple(unit for page in pages for unit in page.evidence_units)
    if len({unit.evidence_id for unit in units}) != len(units):
        raise ValueError(f"{sample.sample_id}: duplicate evidence IDs across selected pages")
    decisions = tuple(decide_policy(unit=unit, policy=policy) for unit in units)
    contexts = tuple(EvidenceContext(unit=unit, decision=decision) for unit, decision in zip(units, decisions, strict=True))
    rendered = _render_evidence_map(units, policy)
    overrides = _reviewed_override_groups(contexts=contexts, conflicts=conflicts)
    grouped = _group_included_evidence(contexts=contexts, overrides=overrides)
    diagram_reviews = diagram_reviews or {}
    diagram_dispositions = diagram_dispositions or {}
    bundles: list[FactBundle] = []
    audit_exclusions: dict[str, DiagramDispositionBinding] = {
        unit.evidence_id: binding
        for unit in units
        if unit.kind == EvidenceKind.FIGURE
        and (binding := diagram_dispositions.get((sample.sample_id, unit.evidence_id))) is not None
        and binding.entry.disposition == DiagramDisposition.NON_KP_VISUAL
    }
    for group, override_text in grouped:
        diagram_review = _reviewed_diagram_for_units(
            sample_id=sample.sample_id,
            units=group,
            reviews=diagram_reviews,
        )
        diagram_disposition = _diagram_disposition_for_units(
            sample_id=sample.sample_id,
            units=group,
            dispositions=diagram_dispositions,
        )
        if (
            diagram_disposition is not None
            and diagram_disposition.entry.disposition == DiagramDisposition.NON_KP_VISUAL
        ):
            audit_exclusions.update({unit.evidence_id: diagram_disposition for unit in group})
            continue
        bundles.append(
            FactBundle(
                fact=_make_fact(
                    units=group,
                    rendered=rendered,
                    override_text=override_text,
                    diagram_review=diagram_review,
                    diagram_disposition=diagram_disposition,
                ),
                units=group,
                override_text=override_text,
                diagram_review=diagram_review,
                diagram_disposition=diagram_disposition,
            )
        )
    bundles = tuple(bundles)
    facts = tuple(bundle.fact for bundle in bundles)
    if len({fact.fact_id for fact in facts}) != len(facts):
        raise ValueError(f"{sample.sample_id}: duplicate fact IDs require explicit dedup review")
    plans_and_cards = tuple(
        _make_card(
            sample_id=sample.sample_id,
            bundle=bundle,
            material_profile_id=material_profile_id,
            policy=policy,
            rendered=rendered,
        )
        for bundle in bundles
    )
    plans = tuple(item[0] for item in plans_and_cards)
    cards = tuple(item[1] for item in plans_and_cards)
    owner_by_evidence = {
        evidence_id: card.raw_id
        for bundle, card in zip(bundles, cards, strict=True)
        for evidence_id in bundle.fact.primary_evidence_ids
    }
    records: list[CoverageRecord] = []
    for context in contexts:
        unit = context.unit
        decision = context.decision
        decision_id = _policy_decision_id(policy=policy, evidence_id=unit.evidence_id)
        audit_binding = audit_exclusions.get(unit.evidence_id)
        if audit_binding is not None:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.EXCLUDED_BY_AUDIT,
                policy_decision_id=decision_id,
                reason=(
                    f"由图示处置审计 {audit_binding.entry.disposition_id} 标记为 "
                    "non_kp_visual；保留原始图像证据和高分辨率复核绑定，不生成 Fact/Card。"
                ),
            )
        elif decision.disposition == PolicyDisposition.INCLUDED:
            owner = owner_by_evidence.get(unit.evidence_id)
            if owner is None:
                raise ValueError(f"{sample.sample_id}: included evidence lacks primary fact {unit.evidence_id}")
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.PRIMARY_ASSIGNED,
                coverage_owner=owner,
                policy_decision_id=decision_id,
                reason="由唯一 primary fact/card 按 anchor span 主覆盖。",
            )
        elif decision.disposition == PolicyDisposition.EXCLUDED_BY_POLICY:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.EXCLUDED_BY_POLICY,
                policy_decision_id=decision_id,
                reason="例题或答案作为有效 evidence 保留，但被当前 Content Policy 非破坏性分流。",
            )
        elif decision.disposition == PolicyDisposition.IGNORED_NOISE:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.IGNORED_NOISE,
                policy_decision_id=decision_id,
                reason="已确认页面噪声或装饰。",
            )
        else:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.MANUAL_REVIEW,
                policy_decision_id=decision_id,
                reason="Content Policy 尚未允许自动消费该 evidence。",
            )
        records.append(record)
    coverage = CoverageMap(
        material_profile_id=material_profile_id,
        content_policy_id=policy.policy_id,
        content_policy_revision=policy.revision,
        records=tuple(records),
    )
    dedup = _dedup_audit(cards, facts)
    gate = _quality_gate(
        contexts=contexts,
        bundles=bundles,
        cards=cards,
        coverage=coverage,
        conflicts=conflicts,
        dedup=dedup,
        policy=policy,
        audit_excluded_evidence_ids=frozenset(audit_exclusions),
    )
    assertions = _machine_assertions(sample=sample, units=units, coverage=coverage, conflicts=conflicts, policy=policy)
    return {
        "sample_id": sample.sample_id,
        "pdf_sha256": sample.pdf_sha256,
        "selected_pages": list(sample.selected_pages),
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "card_plans": [plan.model_dump(mode="json") for plan in plans],
        "cards": [card.model_dump(mode="json") for card in cards],
        "policy_decisions": [decision.model_dump(mode="json") for decision in decisions],
        "coverage": coverage.model_dump(mode="json"),
        "quality_gate": gate.model_dump(mode="json"),
        "dedup": dedup,
        "machine_assertions": list(assertions),
        "summary": {
            "evidence_count": len(units),
            "fact_count": len(facts),
            "card_count": len(cards),
            "policy_dispositions": dict(Counter(decision.disposition.value for decision in decisions)),
            "renderability": dict(Counter(card.renderability.value for card in cards)),
            "presentation_transform_count": sum(
                len(fact.presentation_transforms) for fact in facts
            ),
            "must_cover_count": gate.must_cover_total,
            "quality_gate_passed": gate.publishable,
            "machine_assertions_passed": all(item["passed"] for item in assertions),
        },
    }


def run_golden_fact_pipeline(
    *, registry_path: Path,
    sidecar_path: Path,
    content_policy_path: Path,
    evidence_root: Path,
    output_root: Path | None = None,
    diagram_review_sidecar_path: Path | None = None,
    diagram_disposition_sidecar_path: Path | None = None,
) -> dict[str, Any]:
    registry = load_golden_registry(registry_path)
    sidecar = load_golden_review_sidecar(sidecar_path)
    policy = load_content_policy(content_policy_path)
    if not registry.ready_for_phase1:
        raise ValueError("Phase 2 requires every golden sample to be reviewed")
    if sidecar.state.value != "reviewed" or sidecar.registry_id != registry.registry_id:
        raise ValueError("Phase 2 requires the reviewed sidecar for this golden registry")
    if registry.material_profile_id != sidecar.material_profile_id:
        raise ValueError("golden registry and sidecar material profiles do not match")
    diagram_sidecar = (
        load_diagram_review_sidecar(diagram_review_sidecar_path)
        if diagram_review_sidecar_path is not None
        else None
    )
    if diagram_sidecar is not None:
        _validate_diagram_review_sidecar(sidecar=diagram_sidecar, registry=registry, evidence_root=evidence_root)
    diagram_reviews = reviewed_entry_map(diagram_sidecar) if diagram_sidecar is not None else {}
    diagram_disposition_sidecar = (
        load_diagram_disposition_sidecar(diagram_disposition_sidecar_path)
        if diagram_disposition_sidecar_path is not None
        else None
    )
    if diagram_disposition_sidecar is not None:
        if diagram_sidecar is None or diagram_review_sidecar_path is None:
            raise ValueError("diagram disposition sidecar requires its bound diagram review sidecar")
        diagram_dispositions = _validate_diagram_disposition_sidecar(
            sidecar=diagram_disposition_sidecar,
            diagram_sidecar=diagram_sidecar,
            diagram_sidecar_sha256=hashlib.sha256(diagram_review_sidecar_path.read_bytes()).hexdigest(),
            registry=registry,
        )
    else:
        diagram_dispositions = {}
    samples: list[dict[str, Any]] = []
    for sample in registry.samples:
        pages = _load_selected_pages(sample=sample, evidence_root=evidence_root)
        conflicts = _reviewed_conflicts(sidecar=sidecar, sample_id=sample.sample_id, pages=pages)
        result = run_sample(
            sample=sample,
            pages=pages,
            policy=policy,
            conflicts=conflicts,
            material_profile_id=registry.material_profile_id,
            diagram_reviews=diagram_reviews,
            diagram_dispositions=diagram_dispositions,
        )
        samples.append(result)
        if output_root is not None:
            _write_json(output_root / sample.sample_id / "fact_run.json", result)
    summary = {
        "schema_version": "2.0",
        "stage": "phase2_fact_card_coverage",
        "registry_id": registry.registry_id,
        "sidecar_id": sidecar.sidecar_id,
        "annotation_revision": sidecar.annotation_revision,
        "material_profile_id": registry.material_profile_id,
        "content_policy_id": policy.policy_id,
        "content_policy_revision": policy.revision,
        "diagram_review_sidecar_id": diagram_sidecar.sidecar_id if diagram_sidecar is not None else None,
        "diagram_review_annotation_revision": diagram_sidecar.annotation_revision if diagram_sidecar is not None else None,
        "diagram_review_entries": len(diagram_sidecar.entries) if diagram_sidecar is not None else 0,
        "diagram_reviewed_entries": len(diagram_reviews),
        "diagram_disposition_sidecar_id": (
            diagram_disposition_sidecar.sidecar_id if diagram_disposition_sidecar is not None else None
        ),
        "diagram_disposition_annotation_revision": (
            diagram_disposition_sidecar.annotation_revision if diagram_disposition_sidecar is not None else None
        ),
        "diagram_disposition_entries": len(diagram_dispositions),
        "diagram_disposition_released_entries": sum(
            is_released_disposition(binding.entry.disposition) for binding in diagram_dispositions.values()
        ),
        "sample_count": len(samples),
        "selected_page_count": sum(len(sample.selected_pages) for sample in registry.samples),
        "evidence_count": sum(sample["summary"]["evidence_count"] for sample in samples),
        "fact_count": sum(sample["summary"]["fact_count"] for sample in samples),
        "card_count": sum(sample["summary"]["card_count"] for sample in samples),
        "must_cover_count": sum(sample["summary"]["must_cover_count"] for sample in samples),
        "quality_gates_passed": all(sample["summary"]["quality_gate_passed"] for sample in samples),
        "machine_assertions_passed": all(sample["summary"]["machine_assertions_passed"] for sample in samples),
        "samples": samples,
    }
    if output_root is not None:
        _write_json(output_root / "golden_fact_run_report.json", summary)
    return summary
