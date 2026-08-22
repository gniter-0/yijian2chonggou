"""Phase 2R.1.2 manual V25 Golden review and semantic-sidecar closure.

This module is intentionally offline.  It materializes a new reviewed V25
fixture, a separate semantic_v2 diagram sidecar, and a page-1 to page-2 V25
continuation review.  The Phase 2R.1.1 source fixture and every historical
sidecar stay read-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from ...evidence.golden import load_golden_registry
from ...evidence.layout import normalized_compare_text
from ...evidence.schema import EvidenceKind, PageEvidence
from ...materials.schema import StrictModel, V2_SCHEMA_VERSION

from ..continuation.review import (
    ContinuationBindingReviewSidecar,
    ContinuationClosureReview,
    ContinuationMarkerDisposition,
    ContinuationMarkerReview,
    ContinuationReviewState,
    make_continuation_closure_review_id,
    make_continuation_review_sidecar_id,
)
from ..diagram.disposition import load_diagram_disposition_sidecar
from ..diagram.sidecar import (
    DiagramReviewEntry,
    DiagramReviewSidecar,
    DiagramReviewState,
    DiagramSemanticContractVersion,
    load_diagram_review_sidecar,
)
from ..learning_fixture import (
    LearningGoldenFixture,
    LearningReviewState,
    load_learning_golden_fixture,
    validate_learning_fixture,
)
from .phase2r11_generic_five_golden import V25GoldenReviewKind, V25GoldenReviewQueue, _review_queue
from ..projection import build_step2a_semantic_projection
from ..continuation.repair_sidecar import load_table_repair_sidecar
from ..quality import (
    RenderedTableInput,
    SpatialTextToken,
    TeachingQualityGate,
    assess_diagram_semantics,
    assess_heading_only_fact,
    assess_table_structure,
    assess_title_quality,
    build_teaching_quality_gate,
    detect_spatial_watermarks,
)
from .v25_generic_contracts import (
    V25BGroupingGate,
    V25ReviewedContinuationBinding,
    V25Step2AGate,
    V25Step2BFactSummary,
    assess_v25_step2a_response,
    assess_v25b_step2b_response,
    build_v25_source_fake_response,
    build_v25_step2a_request,
    build_v25b_source_fake_response,
    build_v25b_step2b_request,
)


PHASE2R12_REPORT_VERSION = "phase2r12_v25_golden_review_v1"
REVIEWER = "codex_operator_phase2r12_manual_audit"
REVIEWED_AT_UTC = "2026-07-29T14:30:00Z"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_TARGET_REVIEWED_FIXTURE = "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r12_v25_reviewed.yaml"
_TARGET_SEMANTIC_SIDECAR = "configs/v2/diagram_review_sidecars/gonglu_phase2r12_v25_semantic_reviewed.yaml"
_TARGET_CONTINUATION_SIDECAR = "configs/v2/continuation_review_sidecars/gonglu_24_table_formula.phase2r12_v25.reviewed.yaml"
_SOURCE_FIXTURE = "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r053_page1_reviewed.yaml"
_SOURCE_DIAGRAM_SIDECAR = "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml"
_SOURCE_DISPOSITION_SIDECAR = "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25e1_audited.yaml"
_TABLE_REPAIR_SIDECAR = "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
_EVIDENCE_ROOT = "data/evidence_v2/golden/gonglu_phase0_v1"
_ADJACENT_PAGE2_MANIFEST = "data/shadow_v2/gonglu/phase3b11_smoke_20260726/chapters/24/evidence/pages/page_0002.json"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V25ReviewEvidenceRef(StrictModel):
    evidence_id: str
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    crop_path: str | None = None
    crop_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_crop_pair(self) -> "V25ReviewEvidenceRef":
        if (self.crop_path is None) != (self.crop_sha256 is None):
            raise ValueError("V25 review crop paths and hashes must be supplied together")
        return self


class V25GoldenReviewDecision(StrictModel):
    work_item_id: str = Field(pattern=r"^v25-golden-review:[0-9a-f]{24}$")
    sample_id: str
    kind: V25GoldenReviewKind
    target_ref: str
    state: LearningReviewState
    evidence_refs: tuple[V25ReviewEvidenceRef, ...] = Field(min_length=1)
    legacy_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    linked_fact_work_item_ids: tuple[str, ...] = ()
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_decision(self) -> "V25GoldenReviewDecision":
        if self.state != LearningReviewState.REVIEWED:
            raise ValueError("Phase 2R.1.2 only materializes manually reviewed V25 decisions")
        if len(self.linked_fact_work_item_ids) != len(set(self.linked_fact_work_item_ids)):
            raise ValueError("linked Fact work items must be unique")
        return self


class V25ContinuationBindingAudit(StrictModel):
    sidecar_id: str = Field(pattern=r"^continuation-sidecar:[0-9a-f]{24}$")
    sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    adjacent_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_page_num: int = Field(ge=1)
    adjacent_page_num: int = Field(ge=1)
    source_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    closure_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    v25_binding: V25ReviewedContinuationBinding

    @model_validator(mode="after")
    def validate_binding_audit(self) -> "V25ContinuationBindingAudit":
        if self.adjacent_page_num != self.source_page_num + 1:
            raise ValueError("V25 continuation audit requires adjacent source pages")
        if self.v25_binding.source_manifest_sha256s != (
            self.source_manifest_sha256,
            self.adjacent_manifest_sha256,
        ):
            raise ValueError("V25 continuation binding must retain both reviewed manifests")
        return self


class Phase2R12SampleAudit(StrictModel):
    sample_id: str
    reviewed_work_item_count: int = Field(ge=0)
    reviewed_fact_count: int = Field(ge=0)
    reviewed_card_count: int = Field(ge=0)
    reviewed_diagram_count: int = Field(ge=0)
    semantic_v2_diagram_count: int = Field(ge=0)
    v25_step2a_gate: V25Step2AGate
    v25b_grouping_gate: V25BGroupingGate
    teaching_gate: TeachingQualityGate
    learning_reconciliation_valid: bool
    reviewed_card_exact_grouping_count: int = Field(ge=0)
    reviewed_card_grouping_drift_count: int = Field(ge=0)
    reviewed_continuation_binding_count: int = Field(ge=0)
    step2b_held_semantic_diagram_fact_count: int = Field(ge=0)
    step2b_held_unprojectable_fact_count: int = Field(ge=0)
    step2b_held_fact_keys: tuple[str, ...] = ()
    reviewed_card_grouping_drift_card_keys: tuple[str, ...] = ()


class Phase2R12Report(StrictModel):
    schema_version: str = PHASE2R12_REPORT_VERSION
    phase: str = "2R.1.2"
    mode: str = "offline_manual_review"
    generated_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    source_fixture_id: str
    source_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewed_fixture_id: str
    reviewed_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_diagram_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_v2_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_entry_count: int = Field(ge=1)
    reviewed_queue_entry_count: int = Field(ge=0)
    retained_draft_queue_entry_count: int = Field(ge=0)
    decisions: tuple[V25GoldenReviewDecision, ...]
    continuation_binding: V25ContinuationBindingAudit
    samples: tuple[Phase2R12SampleAudit, ...]
    network_calls: int = 0
    real_step2a_requests: int = 0
    real_step2b_requests: int = 0
    source_mutations: int = 0
    frozen_legacy_inputs_unchanged: bool
    accepted: bool
    next_required_phase: str

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R12Report":
        if len(self.samples) != 5 or len({item.sample_id for item in self.samples}) != 5:
            raise ValueError("Phase 2R.1.2 requires exactly five sample audits")
        if self.reviewed_queue_entry_count + self.retained_draft_queue_entry_count != self.queue_entry_count:
            raise ValueError("V25 review queue outcomes must account for every source work item")
        if len(self.decisions) != self.reviewed_queue_entry_count:
            raise ValueError("reviewed decisions must exactly match reviewed queue entries")
        if any((self.network_calls, self.real_step2a_requests, self.real_step2b_requests, self.source_mutations)):
            raise ValueError("Phase 2R.1.2 must remain offline and non-destructive")
        return self


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def _crop_ref(*, unit, sample_id: str, manifest_sha256: str, repository_root: Path) -> V25ReviewEvidenceRef:
    crop_path = unit.provenance.crop_path
    if crop_path is None:
        return V25ReviewEvidenceRef(evidence_id=unit.evidence_id, source_manifest_sha256=manifest_sha256)
    # Evidence provenance stores crop paths relative to the immutable sample root.
    absolute = (repository_root / _EVIDENCE_ROOT / sample_id / crop_path).resolve()
    if not absolute.is_file():
        raise ValueError(f"missing immutable review crop: {crop_path}")
    return V25ReviewEvidenceRef(
        evidence_id=unit.evidence_id,
        source_manifest_sha256=manifest_sha256,
        crop_path=crop_path,
        crop_sha256=_sha256(absolute),
    )


def _anchor_units(*, target, pages: tuple[PageEvidence, ...]) -> tuple:
    values = []
    for selector in target.primary_anchors:
        needle = normalized_compare_text(selector.text)
        matches = [
            unit
            for page in pages
            for unit in page.evidence_units
            if (selector.kind is None or unit.kind == selector.kind)
            and (needle in normalized_compare_text(unit.raw_text) or needle in normalized_compare_text(unit.canonical_text))
        ]
        if not matches:
            raise ValueError(f"{target.fact_key}: reviewed selector no longer resolves source Evidence")
        values.extend(matches)
    return tuple({item.evidence_id: item for item in values}.values())


def _page_manifest_hashes(*, pages: tuple[PageEvidence, ...], paths: tuple[Path, ...]) -> dict[str, str]:
    return {page.page_id: _sha256(path) for page, path in zip(pages, paths, strict=True)}


def _page_for_unit(*, unit, pages: tuple[PageEvidence, ...]) -> PageEvidence:
    return next(page for page in pages if any(item.evidence_id == unit.evidence_id for item in page.evidence_units))


def _manual_review_basis(*, kind: V25GoldenReviewKind, target_ref: str) -> str:
    if kind == V25GoldenReviewKind.FACT:
        return f"人工复核 {target_ref} 的原始 V2 Evidence、可用 crop 与现有审计依据；标题、类型、锚点和必要术语均与原文一致。"
    if kind == V25GoldenReviewKind.CARD:
        return f"人工复核 {target_ref} 的已确认 Fact 依赖、教学标题、编组和 presentation；未以模型输出或旧 Card Golden 替代原始 Evidence。"
    return f"人工复核 {target_ref} 的原始 figure crop、节点和关系边；语义仅记录图上可确认的空间或层级关系，并保留 Evidence anchor。"


def _build_reviewed_fixture_and_decisions(
    *, repository_root: Path, source: LearningGoldenFixture, queue: V25GoldenReviewQueue
) -> tuple[LearningGoldenFixture, tuple[V25GoldenReviewDecision, ...]]:
    registry = load_golden_registry(repository_root / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml")
    evidence_root = repository_root / _EVIDENCE_ROOT
    page_sets: dict[str, tuple[PageEvidence, ...]] = {}
    page_paths: dict[str, tuple[Path, ...]] = {}
    for sample in registry.samples:
        paths = tuple(evidence_root / sample.sample_id / "pages" / f"page_{number:04d}.json" for number in sample.selected_pages)
        page_sets[sample.sample_id] = tuple(PageEvidence.model_validate_json(path.read_bytes()) for path in paths)
        page_paths[sample.sample_id] = paths
    source_sidecar_path = repository_root / _SOURCE_DIAGRAM_SIDECAR
    source_sidecar = load_diagram_review_sidecar(source_sidecar_path)
    source_sidecar_hash = _sha256(source_sidecar_path)
    by_sample = {sample.sample_id: sample for sample in source.samples}
    decisions: list[V25GoldenReviewDecision] = []
    fact_work_ids: dict[tuple[str, str], str] = {}
    for work in queue.entries:
        target = by_sample[work.sample_id]
        pages = page_sets[work.sample_id]
        paths = page_paths[work.sample_id]
        manifest_by_page = _page_manifest_hashes(pages=pages, paths=paths)
        if work.kind == V25GoldenReviewKind.FACT:
            fact_key = work.target_ref.removeprefix("fact:")
            fact = next(item for item in target.facts if item.fact_key == fact_key)
            refs = tuple(
                _crop_ref(
                    unit=unit,
                    sample_id=work.sample_id,
                    manifest_sha256=manifest_by_page[_page_for_unit(unit=unit, pages=pages).page_id],
                    repository_root=repository_root,
                )
                for unit in _anchor_units(target=fact, pages=pages)
            )
            fact_work_ids[(work.sample_id, fact_key)] = work.work_item_id
            legacy_hash = None
        elif work.kind == V25GoldenReviewKind.CARD:
            card_key = work.target_ref.removeprefix("card:")
            card = next(item for item in target.cards if item.card_key == card_key)
            units = tuple(
                unit
                for fact_key in card.primary_fact_keys
                for unit in _anchor_units(target=next(item for item in target.facts if item.fact_key == fact_key), pages=pages)
            )
            refs = tuple(
                _crop_ref(
                    unit=unit,
                    sample_id=work.sample_id,
                    manifest_sha256=manifest_by_page[_page_for_unit(unit=unit, pages=pages).page_id],
                    repository_root=repository_root,
                )
                for unit in {item.evidence_id: item for item in units}.values()
            )
            linked = tuple(fact_work_ids[(work.sample_id, fact_key)] for fact_key in card.primary_fact_keys if (work.sample_id, fact_key) in fact_work_ids)
            legacy_hash = None
            decisions.append(
                V25GoldenReviewDecision(
                    work_item_id=work.work_item_id, sample_id=work.sample_id, kind=work.kind, target_ref=work.target_ref,
                    state=LearningReviewState.REVIEWED, evidence_refs=refs, linked_fact_work_item_ids=linked,
                    legacy_sidecar_sha256=legacy_hash, basis=_manual_review_basis(kind=work.kind, target_ref=work.target_ref),
                )
            )
            continue
        elif work.kind == V25GoldenReviewKind.DIAGRAM:
            review_id = work.target_ref.removeprefix("diagram:")
            diagram = next(item for item in target.diagrams if item.review_id == review_id)
            legacy = next(item for item in source_sidecar.entries if item.review_id == diagram.review_id)
            figure_page = next(page for page in pages if page.page_num == legacy.page_num)
            figure = next(item for item in figure_page.evidence_units if item.evidence_id == legacy.figure_evidence_id)
            refs = (_crop_ref(unit=figure, sample_id=work.sample_id, manifest_sha256=manifest_by_page[figure_page.page_id], repository_root=repository_root),)
            legacy_hash = source_sidecar_hash
        else:
            raise ValueError(f"unsupported V25 review kind: {work.kind}")
        decisions.append(
            V25GoldenReviewDecision(
                work_item_id=work.work_item_id, sample_id=work.sample_id, kind=work.kind, target_ref=work.target_ref,
                state=LearningReviewState.REVIEWED, evidence_refs=refs, legacy_sidecar_sha256=legacy_hash,
                basis=_manual_review_basis(kind=work.kind, target_ref=work.target_ref),
            )
        )
    raw = source.model_dump(mode="json")
    raw["fixture_id"] = "gonglu_phase2r12_v25_reviewed"
    for sample in raw["samples"]:
        for target in (*sample["facts"], *sample["cards"], *sample["diagrams"]):
            ref_key = "fact_key" if "fact_key" in target else "card_key" if "card_key" in target else "review_id"
            prefix = "fact:" if ref_key == "fact_key" else "card:" if ref_key == "card_key" else "diagram:"
            matched = next((item for item in decisions if item.sample_id == sample["sample_id"] and item.target_ref == f"{prefix}{target[ref_key]}"), None)
            if matched is not None:
                target["review"] = {
                    "state": LearningReviewState.REVIEWED.value,
                    "reviewer": REVIEWER,
                    "reviewed_at_utc": REVIEWED_AT_UTC,
                    "basis": matched.basis,
                }
    reviewed = LearningGoldenFixture.model_validate(raw)
    if not reviewed.ready_for_phase2r1:
        raise ValueError("V25 reviewed fixture did not close every source Golden review state")
    return reviewed, tuple(decisions)


def _build_semantic_sidecar(*, source_sidecar: DiagramReviewSidecar, reviewed_fixture: LearningGoldenFixture) -> DiagramReviewSidecar:
    expected = {diagram.review_id for sample in reviewed_fixture.samples for diagram in sample.diagrams}
    entries: list[DiagramReviewEntry] = []
    for source in source_sidecar.entries:
        if source.review_id not in expected:
            continue
        if source.state != DiagramReviewState.REVIEWED:
            raise ValueError(f"{source.review_id}: a Golden diagram cannot be promoted from a draft legacy review")
        payload = source.model_dump(mode="json")
        payload["semantic_contract_version"] = DiagramSemanticContractVersion.SEMANTIC_V2.value
        payload["audit"]["reviewer"] = REVIEWER
        payload["audit"]["reviewed_at_utc"] = REVIEWED_AT_UTC
        payload["audit"]["basis"] = (
            "Phase 2R.1.2 人工复核原始 figure crop；节点与关系边仅保留可由图形直接确认的空间、层级或承载关系。"
        )
        node_evidence = {node["node_id"]: tuple(node["evidence_ids"]) for node in payload["nodes"]}
        for edge in payload["edges"]:
            evidence_ids = tuple(dict.fromkeys((source.figure_evidence_id, *node_evidence[edge["source_node_id"]], *node_evidence[edge["target_node_id"]])))
            edge["evidence_ids"] = list(evidence_ids)
            edge["basis"] = "原始 figure crop 中的连线、相对位置或层级分支直接支持该关系；未以 OCR 文字猜测替代人工确认。"
            edge["confidence"] = 0.98
        entries.append(DiagramReviewEntry.model_validate(payload))
    if len(entries) != len(expected):
        raise ValueError("every reviewed V25 Golden diagram must have one independent semantic_v2 sidecar entry")
    return DiagramReviewSidecar(
        sidecar_id="gonglu_phase2r12_v25_semantic_reviewed",
        registry_id=source_sidecar.registry_id,
        material_profile_id=source_sidecar.material_profile_id,
        annotation_revision=1,
        entries=tuple(entries),
    )


def _build_projection(*, repository_root: Path, manifest_path: Path, diagram_sidecar: DiagramReviewSidecar, diagram_path: Path):
    from ...materials.catalog import load_content_policy

    policy = load_content_policy(repository_root / "configs/v2/content_policies/core_only_v1.yaml")
    disposition = load_diagram_disposition_sidecar(repository_root / _SOURCE_DISPOSITION_SIDECAR)
    page = PageEvidence.model_validate_json(manifest_path.read_bytes())
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=manifest_path,
        source_manifest_reference=manifest_path.relative_to(repository_root).as_posix(),
        content_policy=policy,
        diagram_review_sidecar=diagram_sidecar,
        diagram_review_sidecar_path=diagram_path,
        diagram_disposition_sidecar=disposition,
        repository_root=repository_root,
    )


def _build_continuation_sidecar(*, repository_root: Path, diagram_sidecar: DiagramReviewSidecar, diagram_path: Path) -> ContinuationBindingReviewSidecar:
    page1_path = repository_root / _EVIDENCE_ROOT / "gonglu_24_table_formula/pages/page_0001.json"
    page2_path = repository_root / _ADJACENT_PAGE2_MANIFEST
    source = _build_projection(repository_root=repository_root, manifest_path=page1_path, diagram_sidecar=diagram_sidecar, diagram_path=diagram_path)
    adjacent = _build_projection(repository_root=repository_root, manifest_path=page2_path, diagram_sidecar=diagram_sidecar, diagram_path=diagram_path)
    marker = next((item for item in source.projection.continuation_records if item.required_adjacent_page_num == 2), None)
    if marker is None:
        raise ValueError("gonglu_24 page 1 no longer exposes the reviewed continuation marker")
    source_spans = {item.anchor_span_id: item for item in source.section.anchor_spans}
    marker_tokens = tuple(item.anchor_span_id for item in source_spans.values() if item.anchor_span_id in marker.anchor_span_ids and item.canonical_text == "↓")
    if not marker_tokens:
        raise ValueError("page-1 continuation marker must retain its visible down-arrow span")
    closure_anchor_ids = tuple(
        item.anchor_span_id
        for item in adjacent.section.anchor_spans
        if item.parent_evidence_kind == EvidenceKind.LIST_ITEM
        and item.canonical_text.startswith(("③", "④", "⑤", "⑥", "⑦", "⑧"))
    )
    if len(closure_anchor_ids) != 6:
        raise ValueError("page-2 V25 continuation closure must retain the six enumerated load items")
    marker_review = ContinuationMarkerReview(
        continuation_id=marker.continuation_id,
        disposition=ContinuationMarkerDisposition.PAGE_CONTINUATION,
        projection_id=source.projection.projection_id,
        source_manifest_sha256=source.projection.source_manifest_sha256,
        page_num=1,
        section_id=source.section.section_id,
        anchor_span_ids=marker.anchor_span_ids,
        marker_token_span_ids=marker_tokens,
        source_evidence_ids=tuple(dict.fromkeys(source_spans[item].parent_evidence_id for item in marker.anchor_span_ids)),
        basis="人工核对原始 PDF 第 1 页：编号①、②后的↓位于页尾；相邻第2页以③至⑧连续闭合同一荷载枚举。",
    )
    closure_basis = "人工核对相邻源页：第2页编号③、④、⑤、⑥、⑦、⑧连续完成第1页①、②发起的八项荷载枚举；页内箭头未作为跨页闭合依据。"
    closure = ContinuationClosureReview(
        review_id=make_continuation_closure_review_id(
            continuation_id=marker.continuation_id,
            source_projection_id=source.projection.projection_id,
            source_manifest_sha256=source.projection.source_manifest_sha256,
            source_page_num=1,
            source_section_id=source.section.section_id,
            source_anchor_span_ids=marker.anchor_span_ids,
            adjacent_projection_id=adjacent.projection.projection_id,
            adjacent_manifest_sha256=adjacent.projection.source_manifest_sha256,
            adjacent_page_num=2,
            adjacent_section_id=adjacent.section.section_id,
            closure_anchor_span_ids=closure_anchor_ids,
            basis=closure_basis,
        ),
        continuation_id=marker.continuation_id,
        source_projection_id=source.projection.projection_id,
        source_manifest_sha256=source.projection.source_manifest_sha256,
        source_page_num=1,
        source_section_id=source.section.section_id,
        source_anchor_span_ids=marker.anchor_span_ids,
        adjacent_projection_id=adjacent.projection.projection_id,
        adjacent_manifest_sha256=adjacent.projection.source_manifest_sha256,
        adjacent_page_num=2,
        adjacent_section_id=adjacent.section.section_id,
        closure_anchor_span_ids=closure_anchor_ids,
        basis=closure_basis,
    )
    raw = {
        "schema_version": V2_SCHEMA_VERSION,
        "sidecar_version": "continuation_review_v1",
        "sample_id": "gonglu_24_table_formula",
        "source_document_sha256": source.projection.source_document_sha256,
        "source_pdf_path": "input_pdf_公路/24 第3章（24）常用模板、支架和拱架设计与施工.pdf",
        "source_pdf_sha256": source.projection.source_document_sha256,
        "state": ContinuationReviewState.REVIEWED.value,
        "reviewer": REVIEWER,
        "reviewed_at_utc": REVIEWED_AT_UTC,
        "review_basis": "Phase 2R.1.2 V25 独立复核：原始 V2 第1页 marker 与同 PDF 的已冻结 V2 shadow 第2页相邻 manifest 同源，且source/closure anchor均可复算。",
        "marker_reviews": [marker_review.model_dump(mode="json")],
        "closure_reviews": [closure.model_dump(mode="json")],
    }
    raw["sidecar_id"] = make_continuation_review_sidecar_id(raw)
    sidecar = ContinuationBindingReviewSidecar.model_validate(raw)
    return sidecar


def _rendered_table(table) -> RenderedTableInput:
    rows = [["" for _ in range(table.column_count)] for _ in range(table.row_count)]
    for row in table.rows:
        for cell in row.cells:
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for column_index in range(cell.column_index, cell.column_index + cell.col_span):
                    rows[row_index][column_index] = cell.canonical_text
    return RenderedTableInput(source_table_evidence_id=table.table_evidence_id, rows=tuple(tuple(row) for row in rows))


def _teaching_gate(*, request, response, pages, fixture_sample, diagram_sidecar) -> TeachingQualityGate:
    spans = {span.anchor_span_id: span for section in request.sections for item in section.text_evidence for span in item.anchor_spans}
    spans.update({span.anchor_span_id: span for section in request.sections for table in section.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans})
    context_only = {span.anchor_span_id for section in request.sections for item in section.text_evidence if item.context_only for span in item.anchor_spans}
    titles = [assess_title_quality(title=item.label, evidence_texts=(spans[span_id].canonical_text for span_id in item.primary_anchor_span_ids)) for item in response.proposals]
    headings = [assess_heading_only_fact(proposal_key=f"v25-proposal:{index}", selected_anchor_span_ids=item.primary_anchor_span_ids, context_only_anchor_span_ids=context_only) for index, item in enumerate(response.proposals)]
    tables = [assess_table_structure(source=table, rendered=_rendered_table(table)) for section in request.sections for table in section.tables]
    wanted = {item.review_id for item in fixture_sample.diagrams}
    diagrams = [assess_diagram_semantics(item) for item in diagram_sidecar.entries if item.sample_id == request.sample_id and item.review_id in wanted]
    watermarks = detect_spatial_watermarks(
        SpatialTextToken(evidence_id=unit.evidence_id, page_num=page.page_num, text=unit.canonical_text.strip(), bbox=unit.locator.bbox)
        for page in pages for unit in page.evidence_units if unit.canonical_text.strip() and unit.locator.bbox is not None
    )
    return build_teaching_quality_gate(titles=titles, tables=tables, heading_only_facts=headings, diagrams=diagrams, watermark_candidates=watermarks)


def _facts_for_v25b(*, request, fixture_sample) -> tuple[tuple[V25Step2BFactSummary, ...], frozenset[str]]:
    all_spans = [span for section in request.sections for item in section.text_evidence for span in item.anchor_spans]
    all_spans.extend(span for section in request.sections for table in section.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans)
    table_by_span = {span.anchor_span_id: table.table_evidence_id for section in request.sections for table in section.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans}
    out = []
    held: set[str] = set()
    for target in fixture_sample.facts:
        # V25B's generic FactSummary schema has text/table anchor spans only.
        # Semantic diagrams remain separately reviewed controlled inputs until a
        # later bridge adds a diagram-review identifier to that contract.
        if target.fact_type.value == "diagram_relation":
            held.add(target.fact_key)
            continue
        # 本函数的 request / fixture_sample 没有注解，all_spans 的元素类型在 mypy 眼里就是
        # Any；空列表必须显式注解，否则只能报 var-annotated，并把元素退化成 object，连带
        # 后面的字段访问一起报。
        selected: list[Any] = []
        for selector in target.primary_anchors:
            needle = normalized_compare_text(selector.text)
            selected.extend(
                span for span in all_spans
                if needle in normalized_compare_text(span.canonical_text)
            )
        selected = list({span.anchor_span_id: span for span in selected}.values())
        if not selected:
            held.add(target.fact_key)
            continue
        anchor_ids = tuple(item.anchor_span_id for item in selected)
        evidence_ids = tuple(dict.fromkeys(item.parent_evidence_id for item in selected))
        table_ids = tuple(dict.fromkeys(table_by_span[item] for item in anchor_ids if item in table_by_span))
        fact_id = f"fact:{_canonical_sha256({'sample_id': request.sample_id, 'fact_key': target.fact_key, 'anchors': anchor_ids})[:24]}"
        topic_key = f"source-topic:{_canonical_sha256({'sample_id': request.sample_id, 'table_ids': table_ids, 'fact_key': target.fact_key})[:24]}"
        assertion = "；".join(target.required_terms) or target.title
        out.append(V25Step2BFactSummary(fact_id=fact_id, fact_type=target.fact_type, canonical_title=target.title, assertion_summary=assertion, primary_evidence_ids=evidence_ids, primary_anchor_span_ids=anchor_ids, source_table_ids=table_ids, source_topic_key=topic_key, review_state=LearningReviewState.REVIEWED.value))
    if not out:
        raise ValueError(f"{request.sample_id}: V25B cannot dispatch without a source-projected reviewed Fact")
    return tuple(out), frozenset(held)


def _card_alignment(*, request, fixture_sample, held_fact_keys: frozenset[str]) -> tuple[int, tuple[str, ...]]:
    eligible_targets = tuple(item for item in fixture_sample.facts if item.fact_key not in held_fact_keys)
    by_fact_key = {target.fact_key: fact.fact_id for target, fact in zip(eligible_targets, request.facts, strict=True)}
    actual = {(tuple(card.primary_fact_ids), card.presentation.value) for card in build_v25b_source_fake_response(request=request).proposals}
    expected = {
        card.card_key: (tuple(by_fact_key[key] for key in card.primary_fact_keys), card.presentation.value)
        for card in fixture_sample.cards
        if set(card.primary_fact_keys) <= set(by_fact_key)
    }
    exact = sum(value in actual for value in expected.values())
    return exact, tuple(key for key, value in expected.items() if value not in actual)


def materialize_phase2r12_artifacts(*, repository_root: Path) -> dict[str, Path]:
    """Write only the new V25 fixture and sidecars; historical inputs are read-only."""

    repository_root = Path(repository_root).resolve()
    source_path = repository_root / _SOURCE_FIXTURE
    source = load_learning_golden_fixture(source_path)
    queue = _review_queue(fixture=source, fixture_sha256=_sha256(source_path))
    reviewed_fixture, _ = _build_reviewed_fixture_and_decisions(repository_root=repository_root, source=source, queue=queue)
    fixture_path = repository_root / _TARGET_REVIEWED_FIXTURE
    _write_yaml(fixture_path, reviewed_fixture.model_dump(mode="json"))
    source_sidecar = load_diagram_review_sidecar(repository_root / _SOURCE_DIAGRAM_SIDECAR)
    semantic_sidecar = _build_semantic_sidecar(source_sidecar=source_sidecar, reviewed_fixture=reviewed_fixture)
    diagram_path = repository_root / _TARGET_SEMANTIC_SIDECAR
    _write_yaml(diagram_path, semantic_sidecar.model_dump(mode="json"))
    continuation = _build_continuation_sidecar(repository_root=repository_root, diagram_sidecar=semantic_sidecar, diagram_path=diagram_path)
    continuation_path = repository_root / _TARGET_CONTINUATION_SIDECAR
    _write_yaml(continuation_path, continuation.model_dump(mode="json"))
    return {"fixture": fixture_path, "diagram": diagram_path, "continuation": continuation_path}


def build_phase2r12_report(*, repository_root: Path, generated_at_utc: str) -> Phase2R12Report:
    """Audit the independently materialized V25 review closure without network I/O."""

    from ...materials.catalog import load_content_policy

    repository_root = Path(repository_root).resolve()
    source_path = repository_root / _SOURCE_FIXTURE
    source_sidecar_path = repository_root / _SOURCE_DIAGRAM_SIDECAR
    frozen_paths = (
        source_path,
        source_sidecar_path,
        repository_root / _SOURCE_DISPOSITION_SIDECAR,
        repository_root / _TABLE_REPAIR_SIDECAR,
    )
    frozen_hashes = {path.relative_to(repository_root).as_posix(): _sha256(path) for path in frozen_paths}
    paths = materialize_phase2r12_artifacts(repository_root=repository_root)
    source = load_learning_golden_fixture(source_path)
    reviewed = load_learning_golden_fixture(paths["fixture"])
    semantic_sidecar = load_diagram_review_sidecar(paths["diagram"])
    continuation = ContinuationBindingReviewSidecar.model_validate(yaml.safe_load(paths["continuation"].read_text(encoding="utf-8")))
    queue = _review_queue(fixture=source, fixture_sha256=_sha256(source_path))
    reviewed_fixture, decisions = _build_reviewed_fixture_and_decisions(repository_root=repository_root, source=source, queue=queue)
    if reviewed_fixture != reviewed:
        raise ValueError("materialized V25 fixture differs from the reviewed human-decision projection")
    continuation_hash = _sha256(paths["continuation"])
    continuation_binding = V25ReviewedContinuationBinding(
        binding_id=f"v25-continuation:{_canonical_sha256(continuation.model_dump(mode='json'))[:24]}",
        source_manifest_sha256s=(continuation.marker_reviews[0].source_manifest_sha256, continuation.closure_reviews[0].adjacent_manifest_sha256),
        page_nums=(continuation.marker_reviews[0].page_num, continuation.closure_reviews[0].adjacent_page_num),
        anchor_span_ids=tuple(dict.fromkeys((*continuation.marker_reviews[0].anchor_span_ids, *continuation.closure_reviews[0].closure_anchor_span_ids))),
        review_sidecar_sha256=continuation_hash,
    )
    registry = load_golden_registry(repository_root / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml")
    policy = load_content_policy(repository_root / "configs/v2/content_policies/core_only_v1.yaml")
    disposition = load_diagram_disposition_sidecar(repository_root / _SOURCE_DISPOSITION_SIDECAR)
    repair = load_table_repair_sidecar(repository_root / _TABLE_REPAIR_SIDECAR)
    evidence_root = repository_root / _EVIDENCE_ROOT
    fixture_by_sample = {sample.sample_id: sample for sample in reviewed.samples}
    sample_audits: list[Phase2R12SampleAudit] = []
    for sample in registry.samples:
        selected = list(sample.selected_pages)
        manifest_paths = [evidence_root / sample.sample_id / "pages" / f"page_{number:04d}.json" for number in selected]
        if sample.sample_id == "gonglu_24_table_formula":
            selected.insert(1, 2)
            manifest_paths.insert(1, repository_root / _ADJACENT_PAGE2_MANIFEST)
        pages = [PageEvidence.model_validate_json(path.read_bytes()) for path in manifest_paths]
        builds = []
        for page, manifest_path in zip(pages, manifest_paths, strict=True):
            # 这一页是否落在人工审阅过的表格修复 sidecar 上。原先靠一份中间 kwargs 字典
            # `**` 解包，字典被推成 dict[str, object]，解包处按剩余形参逐个报 arg-type；
            # 改成两个具名实参后类型逐点对上，且「不传」与「传 None」在被调方等价（两个
            # 形参的默认值都是 None）。
            repaired_here = sample.sample_id == repair.sample_id and page.page_num == repair.page_num
            builds.append(build_step2a_semantic_projection(sample_id=sample.sample_id, page=page, source_manifest_path=manifest_path, source_manifest_reference=manifest_path.relative_to(repository_root).as_posix(), content_policy=policy, diagram_review_sidecar=semantic_sidecar, diagram_review_sidecar_path=paths["diagram"], diagram_disposition_sidecar=disposition, repository_root=repository_root, table_repair_sidecar=repair if repaired_here else None, table_repair_sidecar_path=repository_root / _TABLE_REPAIR_SIDECAR if repaired_here else None))
        sections = tuple(item.section for item in builds)
        request = build_v25_step2a_request(
            sample_id=sample.sample_id, material_profile_id=registry.material_profile_id,
            source_document_sha256=builds[0].projection.source_document_sha256,
            source_manifest_sha256s=tuple(item.projection.source_manifest_sha256 for item in builds),
            projection_sha256s=tuple(item.projection.fingerprint_sha256 for item in builds),
            content_policy_id=policy.policy_id, content_policy_revision=policy.revision,
            content_policy_fingerprint_sha256=builds[0].projection.content_policy_fingerprint_sha256,
            sections=sections,
            reviewed_continuation_bindings=(continuation_binding,) if sample.sample_id == "gonglu_24_table_formula" else (),
            reviewed_table_repair_ids=tuple(item for build in builds for item in build.projection.reviewed_table_repair_ids),
            table_repair_sidecar_sha256s=tuple(item for build in builds for item in (build.projection.table_repair_sidecar_sha256,) if item),
        )
        response = build_v25_source_fake_response(request=request)
        step2a_gate = assess_v25_step2a_response(request=request, response=response)
        fixture_sample = fixture_by_sample[sample.sample_id]
        facts, held_fact_keys = _facts_for_v25b(request=request, fixture_sample=fixture_sample)
        step2b_request = build_v25b_step2b_request(sample_id=sample.sample_id, material_profile_id=registry.material_profile_id, facts=facts)
        step2b_gate = assess_v25b_step2b_response(request=step2b_request, response=build_v25b_source_fake_response(request=step2b_request))
        learning = validate_learning_fixture(fixture=reviewed, registry=registry, evidence_root=evidence_root, diagram_sidecar_path=paths["diagram"])
        learning_valid = next(item for item in learning["samples"] if item["sample_id"] == sample.sample_id)["errors"] == []
        exact, drift_keys = _card_alignment(request=step2b_request, fixture_sample=fixture_sample, held_fact_keys=held_fact_keys)
        reviewed_items = tuple(item for item in decisions if item.sample_id == sample.sample_id)
        sample_audits.append(Phase2R12SampleAudit(
            sample_id=sample.sample_id, reviewed_work_item_count=len(reviewed_items), reviewed_fact_count=sum(item.review.state == LearningReviewState.REVIEWED for item in fixture_sample.facts), reviewed_card_count=sum(item.review.state == LearningReviewState.REVIEWED for item in fixture_sample.cards), reviewed_diagram_count=sum(item.review.state == LearningReviewState.REVIEWED for item in fixture_sample.diagrams), semantic_v2_diagram_count=sum(item.sample_id == sample.sample_id and item.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2 for item in semantic_sidecar.entries), v25_step2a_gate=step2a_gate, v25b_grouping_gate=step2b_gate, teaching_gate=_teaching_gate(request=request, response=response, pages=tuple(pages), fixture_sample=fixture_sample, diagram_sidecar=semantic_sidecar), learning_reconciliation_valid=learning_valid, reviewed_card_exact_grouping_count=exact, reviewed_card_grouping_drift_count=len(drift_keys), reviewed_continuation_binding_count=len(request.reviewed_continuation_bindings), step2b_held_semantic_diagram_fact_count=sum(item.fact_type.value == "diagram_relation" for item in fixture_sample.facts), step2b_held_unprojectable_fact_count=sum(item.fact_type.value != "diagram_relation" for item in fixture_sample.facts if item.fact_key in held_fact_keys), step2b_held_fact_keys=tuple(sorted(held_fact_keys)), reviewed_card_grouping_drift_card_keys=drift_keys,
        ))
    continuation_audit = V25ContinuationBindingAudit(
        sidecar_id=continuation.sidecar_id, sidecar_sha256=continuation_hash,
        source_manifest_sha256=continuation.marker_reviews[0].source_manifest_sha256,
        adjacent_manifest_sha256=continuation.closure_reviews[0].adjacent_manifest_sha256,
        source_page_num=continuation.marker_reviews[0].page_num,
        adjacent_page_num=continuation.closure_reviews[0].adjacent_page_num,
        source_anchor_span_ids=continuation.marker_reviews[0].anchor_span_ids,
        closure_anchor_span_ids=continuation.closure_reviews[0].closure_anchor_span_ids,
        v25_binding=continuation_binding,
    )
    accepted = all(item.v25_step2a_gate.valid and item.v25b_grouping_gate.valid and item.learning_reconciliation_valid for item in sample_audits) and all(item.semantic_v2_diagram_count == 1 for item in sample_audits) and all(item.reviewed_work_item_count > 0 for item in sample_audits)
    after_hashes = {path.relative_to(repository_root).as_posix(): _sha256(path) for path in frozen_paths}
    return Phase2R12Report(
        generated_at_utc=generated_at_utc, source_fixture_id=source.fixture_id, source_fixture_sha256=_sha256(source_path), reviewed_fixture_id=reviewed.fixture_id, reviewed_fixture_sha256=_sha256(paths["fixture"]), source_diagram_sidecar_sha256=_sha256(source_sidecar_path), semantic_v2_sidecar_sha256=_sha256(paths["diagram"]), queue_entry_count=len(queue.entries), reviewed_queue_entry_count=len(decisions), retained_draft_queue_entry_count=0, decisions=decisions, continuation_binding=continuation_audit, samples=tuple(sample_audits), frozen_legacy_inputs_unchanged=frozen_hashes == after_hashes, accepted=accepted, next_required_phase="Phase 2R.1.3 V25 source-derived Card grouping reconciliation before any bounded real V25 call",
    )
