"""Phase 2R.1.3 offline V25B reconciliation.

This phase adds only derived review artifacts.  It never mutates V1, raw V2
Evidence, historical reviewed sidecars, image assets, or frozen V23/V24 runs.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from ...evidence.golden import load_golden_registry
from ...evidence.layout import normalized_compare_text
from ...evidence.schema import EvidenceKind, PageEvidence
from ...materials.catalog import load_content_policy
from ...materials.schema import StrictModel

from ..continuation.review import load_continuation_review_sidecar
from ..diagram.disposition import load_diagram_disposition_sidecar
from ..diagram.sidecar import DiagramSemanticContractVersion, load_diagram_review_sidecar
from ..learning_fixture import LearningReviewState, load_learning_golden_fixture
from .anchors import EvidenceAnchorSpan
from .phase2r12_v25_golden_review import (
    _ADJACENT_PAGE2_MANIFEST,
    _EVIDENCE_ROOT,
    _SOURCE_DISPOSITION_SIDECAR,
    _TABLE_REPAIR_SIDECAR,
    _TARGET_CONTINUATION_SIDECAR,
    _TARGET_REVIEWED_FIXTURE,
    _TARGET_SEMANTIC_SIDECAR,
)
from ..projection import build_step2a_semantic_projection
from ..continuation.repair_sidecar import (
    TableRepairCellBinding,
    TableRepairReviewSidecar,
    TableRepairReviewState,
    TableRepairRowBinding,
    load_table_repair_sidecar,
    make_table_repair_id,
)
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
    V25BSourceFamilyBinding,
    V25BSourceFamilyKind,
    V25BGroupingGate,
    V25ReviewedContinuationBinding,
    V25SemanticDiagramBridge,
    V25SemanticDiagramEdge,
    V25SemanticDiagramNode,
    V25Step2AGate,
    V25Step2BFactSummary,
    assess_v25_step2a_response,
    assess_v25b_step2b_response,
    build_v25_source_fake_response,
    build_v25_step2a_request,
    build_v25b_source_fake_response,
    build_v25b_step2b_request,
)


PHASE2R13_REPORT_VERSION = "phase2r13_v25_reconciliation_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REVIEWER = "codex_operator_phase2r13_source_reconciliation"
_REVIEWED_AT_UTC = "2026-07-29T18:00:00Z"
_TABLE_REPAIR_PATHS = {
    ("gonglu_24_table_formula", 8): "configs/v2/table_repair_sidecars/gonglu_phase2r13_gonglu_24_p0008_arch_removal.reviewed.yaml",
    ("gonglu_41_diagram_mixed", 2): "configs/v2/table_repair_sidecars/gonglu_phase2r13_gonglu_41_p0002_span_classification.reviewed.yaml",
}
_SOURCE_GROUPING_PATH = "configs/v2/card_grouping_review_sidecars/gonglu_phase2r13_v25_source_grouping.reviewed.yaml"
_WATERMARK_REVIEW_PATH = "configs/v2/watermark_review_sidecars/gonglu_phase2r13_spatial_watermark.reviewed.yaml"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _source_topic_key(payload: object) -> str:
    return f"source-topic:{_canonical_sha256(payload)[:24]}"


class V25WatermarkDisposition(StrEnum):
    EXCLUDED_AS_WATERMARK = "excluded_as_watermark"
    MANUAL_REVIEW = "manual_review"


class V25WatermarkReview(StrictModel):
    review_id: str = Field(pattern=r"^v25-watermark-review:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    candidate_text: str = Field(min_length=1, max_length=8)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    page_nums: tuple[int, ...] = Field(min_length=1)
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    mean_x0: float
    mean_y0: float
    disposition: V25WatermarkDisposition
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_review(self) -> "V25WatermarkReview":
        for label, values in (
            ("Evidence", self.evidence_ids),
            ("page", self.page_nums),
            ("manifest", self.source_manifest_sha256s),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V25 watermark {label} bindings must be unique")
        payload = self.model_dump(mode="json", exclude={"review_id"})
        if self.review_id != f"v25-watermark-review:{_canonical_sha256(payload)[:24]}":
            raise ValueError("V25 watermark review ID must be deterministic")
        return self


class V25WatermarkReviewSidecar(StrictModel):
    sidecar_version: str = "v25_spatial_watermark_review_v1"
    reviewer: str = _REVIEWER
    reviewed_at_utc: str = _REVIEWED_AT_UTC
    reviews: tuple[V25WatermarkReview, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sidecar(self) -> "V25WatermarkReviewSidecar":
        ids = [item.review_id for item in self.reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("V25 watermark review IDs must be unique")
        return self


class V25SourceGroupingReview(StrictModel):
    """Source relationship review without any Golden Card title or key."""

    review_id: str = Field(pattern=r"^v25-source-grouping-review:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    family_kind: V25BSourceFamilyKind
    member_fact_keys: tuple[str, ...] = Field(min_length=2)
    source_evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_review(self) -> "V25SourceGroupingReview":
        for label, values in (
            ("Fact", self.member_fact_keys),
            ("Evidence", self.source_evidence_ids),
            ("anchor", self.source_anchor_span_ids),
            ("manifest", self.source_manifest_sha256s),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V25 source grouping {label} bindings must be unique")
        payload = self.model_dump(mode="json", exclude={"review_id"})
        if self.review_id != f"v25-source-grouping-review:{_canonical_sha256(payload)[:24]}":
            raise ValueError("V25 source grouping review ID must be deterministic")
        return self


class V25SourceGroupingReviewSidecar(StrictModel):
    sidecar_version: str = "v25_source_grouping_review_v1"
    reviewer: str = _REVIEWER
    reviewed_at_utc: str = _REVIEWED_AT_UTC
    reviews: tuple[V25SourceGroupingReview, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sidecar(self) -> "V25SourceGroupingReviewSidecar":
        ids = [item.review_id for item in self.reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("V25 source grouping review IDs must be unique")
        return self


class Phase2R13SampleAudit(StrictModel):
    sample_id: str
    v25_step2a_gate: V25Step2AGate
    v25b_grouping_gate: V25BGroupingGate
    reviewed_fact_count: int = Field(ge=1)
    fact_summary_count: int = Field(ge=1)
    semantic_diagram_bridge_count: int = Field(ge=0)
    eligible_repaired_fact_keys: tuple[str, ...] = ()
    source_grouping_review_count: int = Field(ge=0)
    reviewed_card_alignment_count: int = Field(ge=0)
    reviewed_card_drift_count: int = Field(ge=0)
    unresolved_watermark_count: int = Field(ge=0)
    teaching_gate: TeachingQualityGate


class V25SemanticDiagramBridgeDisposition(StrEnum):
    FACT_SUMMARY = "fact_summary"
    EVIDENCE_ONLY = "evidence_only"


class V25SemanticDiagramBridgeAudit(StrictModel):
    sample_id: str
    bridge: V25SemanticDiagramBridge
    disposition: V25SemanticDiagramBridgeDisposition
    fact_key: str | None = None
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_audit(self) -> "V25SemanticDiagramBridgeAudit":
        if self.disposition == V25SemanticDiagramBridgeDisposition.FACT_SUMMARY and not self.fact_key:
            raise ValueError("V25 semantic FactSummary bridge audit requires a reviewed Fact key")
        if self.disposition == V25SemanticDiagramBridgeDisposition.EVIDENCE_ONLY and self.fact_key is not None:
            raise ValueError("V25 evidence-only diagram bridge audit cannot claim a Fact key")
        return self


class Phase2R13Report(StrictModel):
    schema_version: str = PHASE2R13_REPORT_VERSION
    phase: str = "2R.1.3"
    mode: str = "offline_source_reconciliation"
    generated_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    reviewed_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_diagram_review_bridge_count: int = Field(ge=5)
    semantic_diagram_fact_summary_count: int = Field(ge=0)
    semantic_diagram_evidence_only_count: int = Field(ge=0)
    semantic_diagram_bridges: tuple[V25SemanticDiagramBridgeAudit, ...] = Field(min_length=5)
    new_table_repair_sidecar_sha256s: tuple[str, ...] = Field(min_length=2)
    source_grouping_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    watermark_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    samples: tuple[Phase2R13SampleAudit, ...] = Field(min_length=5)
    network_calls: int = 0
    real_step2a_requests: int = 0
    real_step2b_requests: int = 0
    source_mutations: int = 0
    frozen_inputs_unchanged: bool
    accepted: bool
    next_required_phase: str

    @model_validator(mode="after")
    def validate_diagram_bridge_audit(self) -> "Phase2R13Report":
        if self.semantic_diagram_review_bridge_count != len(self.semantic_diagram_bridges):
            raise ValueError("V25 semantic diagram bridge count must match the persisted bridge audits")
        fact_summary_count = sum(
            item.disposition == V25SemanticDiagramBridgeDisposition.FACT_SUMMARY
            for item in self.semantic_diagram_bridges
        )
        evidence_only_count = sum(
            item.disposition == V25SemanticDiagramBridgeDisposition.EVIDENCE_ONLY
            for item in self.semantic_diagram_bridges
        )
        if self.semantic_diagram_fact_summary_count != fact_summary_count:
            raise ValueError("V25 semantic FactSummary bridge count must match bridge audits")
        if self.semantic_diagram_evidence_only_count != evidence_only_count:
            raise ValueError("V25 evidence-only diagram bridge count must match bridge audits")
        if fact_summary_count != sum(item.semantic_diagram_bridge_count for item in self.samples):
            raise ValueError("V25 semantic FactSummary bridge audits must match sample FactSummary bridges")
        return self


_REPAIR_SPANS: dict[tuple[str, int], tuple[str, dict[str, tuple[int, int]], str]] = {
    (
        "gonglu_24_table_formula",
        8,
    ): (
        "ev:6a3d7510fca1e3542931b397",
        {
            "ev:e3af25ad2896d84ec833b0e8": (1, 1),
            "ev:7be568e3f143b4eddefdc1c3": (1, 2),
            "ev:fd153eadc009615fb92f4310": (3, 1),
            "ev:adc489cd1b9555145191417f": (1, 2),
            "ev:a7665c08b939c0f17040e087": (1, 1),
            "ev:5787e1c42bd76941983aec42": (1, 1),
            "ev:fa5c344a51449b7b2c96a37c": (1, 1),
            "ev:f9900a58bd6a29cec463d2cd": (1, 1),
            "ev:e655270303c462a8e279f35f": (1, 1),
            "ev:fad8a9662295265ec28129a4": (1, 2),
        },
        "Visible merged cells close the 5x3 arch-removal grid; source text, rows, cells, bboxes, and crop remain immutable.",
    ),
    (
        "gonglu_41_diagram_mixed",
        2,
    ): (
        "ev:b1e1478b0d3f4e33b6d26f90",
        {
            "ev:0ad87d513acdda57d6ac1771": (1, 3),
            "ev:68f4dfa7b4c9edf0b4bfc707": (1, 1),
            "ev:b7c02e7ba903600c401b59d9": (1, 1),
            "ev:ccdfea6b96dda3f6c0370a56": (1, 1),
            "ev:54355ba1394d463879f49e6c": (1, 1),
            "ev:ae663c437ee7eba6e4bc8315": (1, 1),
            "ev:7b59e50e5601b91dc9f2b8ac": (1, 1),
            "ev:95752e9c81381a2a7a9861ab": (1, 1),
            "ev:063b56a6a894ea05d7564089": (1, 1),
            "ev:d54e040b37fbd84c00338cf1": (1, 1),
            "ev:f0f2fd3e299e8b6178eaad21": (1, 1),
            "ev:d03d85072fc4e57dad4a2460": (1, 1),
            "ev:90118b97b3d4f3428f779a1a": (1, 1),
            "ev:04f0ef41eee0ec041274d323": (1, 1),
            "ev:026905e66203a727468495b8": (1, 1),
            "ev:ec879c502be6adae3992f5c3": (1, 1),
        },
        "The visible title cell spans the three columns of the 6x3 tunnel-span table; no source text or geometry is changed.",
    ),
}

_SOURCE_FAMILY_SPECS = (
    ("gonglu_24_table_formula", V25BSourceFamilyKind.RULE_FAMILY, ("formwork.strength-rigidity-stability", "formwork.release-agent-prohibition", "formwork.lifting-ring-material", "formwork.lifting-ring-stress", "formwork.emergency-passage-separation"), "The source table joins the template and support requirements as one rule family."),
    ("gonglu_24_table_formula", V25BSourceFamilyKind.RULE_FAMILY, ("formwork.anti-overturning", "formwork.climb-speed", "formwork.preload", "formwork.arch-removal"), "Source constraints and thresholds govern the same installation and removal operation family."),
    ("gonglu_41_diagram_mixed", V25BSourceFamilyKind.TABLE_CLASSIFICATION, ("tunnel.rock-classification-table", "tunnel.span-classification"), "The complete source table and its selected span-classification rule share one reviewed table provenance."),
    ("gonglu_55_standard_constraints", V25BSourceFamilyKind.RULE_FAMILY, ("safety.production-duties", "safety.risk-threshold"), "Source requirements bind safety duties and the reviewed applicability threshold as one rule family."),
    ("gonglu_55_standard_constraints", V25BSourceFamilyKind.TIMED_ENUMERATION, ("safety.accident-categories", "safety.report-deadline"), "Source evidence joins accident handling enumeration with its mandatory reporting deadline."),
    ("gonglu_62_cost_flow_incident", V25BSourceFamilyKind.FORMULA_FAMILY, ("cost.project-budget-formula", "cost.mechanical-rental-formula", "cost.plan-variance-formula"), "The three source formulas are calculation rules in one cost-management family."),
)


def _build_table_repair(*, repository_root: Path, sample_id: str, page_num: int) -> TableRepairReviewSidecar:
    table_id, overrides, basis = _REPAIR_SPANS[(sample_id, page_num)]
    path = repository_root / _EVIDENCE_ROOT / sample_id / "pages" / f"page_{page_num:04d}.json"
    page = PageEvidence.model_validate_json(path.read_bytes())
    units = {unit.evidence_id: unit for unit in page.evidence_units}
    table = units[table_id]
    rows = tuple(unit for unit in page.evidence_units if unit.kind == EvidenceKind.TABLE_ROW and unit.parent_id == table_id)
    cells = tuple(unit for row in rows for unit in page.evidence_units if unit.kind == EvidenceKind.TABLE_CELL and unit.parent_id == row.evidence_id)
    if set(overrides) != {cell.evidence_id for cell in cells}:
        raise ValueError(f"{sample_id} page {page_num}: reviewed table repair must bind every source cell")
    crop_path = (path.parent.parent / str(table.provenance.crop_path)).resolve()
    crop_relative = crop_path.relative_to(repository_root).as_posix()
    manifest_relative = path.relative_to(repository_root).as_posix()
    manifest_sha256 = _sha256(path)
    return TableRepairReviewSidecar(
        repair_id=make_table_repair_id(sample_id=sample_id, page_num=page_num, table_evidence_id=table_id, source_manifest_sha256=manifest_sha256, crop_sha256=_sha256(crop_path)),
        sample_id=sample_id,
        page_num=page_num,
        state=TableRepairReviewState.REVIEWED,
        reviewer=_REVIEWER,
        reviewed_at_utc=_REVIEWED_AT_UTC,
        review_basis=basis,
        source_manifest_path=manifest_relative,
        source_manifest_sha256=manifest_sha256,
        table_evidence_id=table_id,
        table_bbox=table.locator.bbox,
        crop_path=crop_relative,
        crop_sha256=_sha256(crop_path),
        declared_row_count=int(table.metadata["rows"]),
        declared_column_count=int(table.metadata["columns"]),
        rows=tuple(TableRepairRowBinding(evidence_id=row.evidence_id, row_index=int(row.metadata["row"])) for row in rows),
        cells=tuple(
            TableRepairCellBinding(
                evidence_id=cell.evidence_id,
                row_evidence_id=cell.parent_id or "",
                row_index=int(cell.metadata["row"]),
                column_index=int(cell.metadata["column"]),
                source_row_span=int(cell.metadata.get("row_span", 1)),
                source_col_span=int(cell.metadata.get("col_span", 1)),
                reviewed_row_span=overrides[cell.evidence_id][0],
                reviewed_col_span=overrides[cell.evidence_id][1],
                canonical_text_sha256=hashlib.sha256(cell.canonical_text.encode("utf-8")).hexdigest(),
                bbox=cell.locator.bbox,
            )
            for cell in cells
        ),
    )


def _materialize_table_repairs(*, repository_root: Path) -> dict[tuple[str, int], Path]:
    paths: dict[tuple[str, int], Path] = {}
    for key, relative in _TABLE_REPAIR_PATHS.items():
        sidecar = _build_table_repair(repository_root=repository_root, sample_id=key[0], page_num=key[1])
        path = repository_root / relative
        _write_yaml(path, sidecar.model_dump(mode="json"))
        paths[key] = path
    return paths


def _builds_for_sample(*, repository_root: Path, sample, policy, diagram_sidecar, diagram_path: Path, disposition, repairs_by_page: dict[tuple[str, int], Path]):
    paths = [repository_root / _EVIDENCE_ROOT / sample.sample_id / "pages" / f"page_{number:04d}.json" for number in sample.selected_pages]
    if sample.sample_id == "gonglu_24_table_formula":
        paths.insert(1, repository_root / _ADJACENT_PAGE2_MANIFEST)
    builds = []
    for path in paths:
        page = PageEvidence.model_validate_json(path.read_bytes())
        repair_path = repairs_by_page.get((sample.sample_id, page.page_num))
        if repair_path is None and sample.sample_id == "gonglu_24_table_formula" and page.page_num == 1:
            repair_path = repository_root / _TABLE_REPAIR_SIDECAR
        # 是否有人工审阅过的表格修复 sidecar 覆盖这一页。原先靠一份中间 kwargs 字典 `**`
        # 解包，字典被推成 dict[str, object]，解包处按剩余形参逐个报 arg-type；改成两个
        # 具名实参后类型逐点对上，且两个形参的默认值都是 None，「不传」与「传 None」等价。
        builds.append(
            build_step2a_semantic_projection(
                sample_id=sample.sample_id,
                page=page,
                source_manifest_path=path,
                source_manifest_reference=path.relative_to(repository_root).as_posix(),
                content_policy=policy,
                diagram_review_sidecar=diagram_sidecar,
                diagram_review_sidecar_path=diagram_path,
                diagram_disposition_sidecar=disposition,
                repository_root=repository_root,
                table_repair_sidecar=load_table_repair_sidecar(repair_path) if repair_path is not None else None,
                table_repair_sidecar_path=repair_path,
            )
        )
    return tuple(builds), tuple(paths)


def _continuation_binding(*, repository_root: Path) -> V25ReviewedContinuationBinding:
    path = repository_root / _TARGET_CONTINUATION_SIDECAR
    sidecar = load_continuation_review_sidecar(path)
    marker = sidecar.marker_reviews[0]
    closure = sidecar.closure_reviews[0]
    return V25ReviewedContinuationBinding(
        binding_id=f"v25-continuation:{_canonical_sha256(sidecar.model_dump(mode='json'))[:24]}",
        source_manifest_sha256s=(marker.source_manifest_sha256, closure.adjacent_manifest_sha256),
        page_nums=(marker.page_num, closure.adjacent_page_num),
        anchor_span_ids=tuple(dict.fromkeys((*marker.anchor_span_ids, *closure.closure_anchor_span_ids))),
        review_sidecar_sha256=_sha256(path),
    )


def _build_v25_request(*, sample, builds, registry, policy, continuation):
    return build_v25_step2a_request(
        sample_id=sample.sample_id,
        material_profile_id=registry.material_profile_id,
        source_document_sha256=builds[0].projection.source_document_sha256,
        source_manifest_sha256s=tuple(item.projection.source_manifest_sha256 for item in builds),
        projection_sha256s=tuple(item.projection.fingerprint_sha256 for item in builds),
        content_policy_id=policy.policy_id,
        content_policy_revision=policy.revision,
        content_policy_fingerprint_sha256=builds[0].projection.content_policy_fingerprint_sha256,
        sections=tuple(item.section for item in builds),
        reviewed_continuation_bindings=(continuation,) if sample.sample_id == "gonglu_24_table_formula" else (),
        reviewed_table_repair_ids=tuple(item for build in builds for item in build.projection.reviewed_table_repair_ids),
        table_repair_sidecar_sha256s=tuple(item for build in builds for item in (build.projection.table_repair_sidecar_sha256,) if item),
    )


# 返回值第一项原先写成 dict[str, object]，把 span 的类型在函数边界上擦掉了；调用点再取
# 出来就只能是 object，访问 canonical_text 报 attr-defined。三处 anchor_spans 字段的声明
# 都是 tuple[EvidenceAnchorSpan, ...]（projection / table_inputs / contracts.projection），
# 所以这里如实写成 EvidenceAnchorSpan，擦除从源头去掉。
def _span_maps(builds) -> tuple[dict[str, EvidenceAnchorSpan], dict[str, str], dict[str, str]]:
    spans: dict[str, EvidenceAnchorSpan] = {}
    table_by_span = {}
    manifest_by_span = {}
    for build in builds:
        for item in build.section.text_evidence:
            for span in item.anchor_spans:
                spans[span.anchor_span_id] = span
                manifest_by_span[span.anchor_span_id] = build.projection.source_manifest_sha256
        for table in build.section.tables:
            for row in table.rows:
                for cell in row.cells:
                    for span in cell.anchor_spans:
                        spans[span.anchor_span_id] = span
                        table_by_span[span.anchor_span_id] = table.table_evidence_id
                        manifest_by_span[span.anchor_span_id] = build.projection.source_manifest_sha256
    return spans, table_by_span, manifest_by_span


def _diagram_bridge_from_entry(*, entry, source_manifest_sha256: str, semantic_sidecar_sha256: str) -> V25SemanticDiagramBridge:
    nodes = tuple(V25SemanticDiagramNode(node_id=node.node_id, label=node.label, evidence_ids=node.evidence_ids) for node in entry.nodes)
    edges = tuple(
        V25SemanticDiagramEdge(
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            relation=edge.relation,
            evidence_ids=edge.evidence_ids,
            basis=edge.basis or "",
            confidence=edge.confidence if edge.confidence is not None else -1,
        )
        for edge in entry.edges
    )
    payload = {
        "review_id": entry.review_id,
        "semantic_sidecar_sha256": semantic_sidecar_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "figure_evidence_id": entry.figure_evidence_id,
        "semantic_title": entry.title or "",
        "nodes": [node.model_dump(mode="json") for node in nodes],
        "edges": [edge.model_dump(mode="json") for edge in edges],
        "basis": "Reviewed semantic_v2 diagram bridge preserves the original figure, reviewed nodes, edge Evidence, basis, and confidence without synthetic text anchors.",
    }
    payload["bridge_id"] = f"v25-semantic-diagram-bridge:{_canonical_sha256(payload)[:24]}"
    return V25SemanticDiagramBridge.model_validate(payload)


def _diagram_bridge(*, target, sample_id: str, diagram_sidecar, manifest_by_page: dict[int, str], semantic_sidecar_sha256: str) -> V25SemanticDiagramBridge:
    selectors = tuple(normalized_compare_text(item.text) for item in target.primary_anchors)
    sample_entries = [
        entry
        for entry in diagram_sidecar.entries
        if entry.sample_id == sample_id
        and entry.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2
    ]
    entries = [
        entry
        for entry in sample_entries
        if any(selector in normalized_compare_text(node.label) for selector in selectors for node in entry.nodes)
    ]
    if not entries and len(sample_entries) == 1:
        entries = sample_entries
    if len(entries) != 1:
        raise ValueError(f"{sample_id}:{target.fact_key} must resolve exactly one semantic_v2 diagram entry")
    return _diagram_bridge_from_entry(
        entry=entries[0],
        source_manifest_sha256=manifest_by_page[entries[0].page_num],
        semantic_sidecar_sha256=semantic_sidecar_sha256,
    )


def _fact_summaries(*, request, fixture_sample, builds, diagram_sidecar, semantic_sidecar_sha256: str):
    spans, table_by_span, manifest_by_span = _span_maps(builds)
    manifests_by_page = {build.projection.page_num: build.projection.source_manifest_sha256 for build in builds}
    facts: list[tuple[str, V25Step2BFactSummary]] = []
    for target in fixture_sample.facts:
        if target.review.state != LearningReviewState.REVIEWED:
            raise ValueError(f"{target.fact_key}: V25B rejects unreviewed Fact")
        if target.fact_type.value == "diagram_relation":
            bridge = _diagram_bridge(
                target=target,
                sample_id=request.sample_id,
                diagram_sidecar=diagram_sidecar,
                manifest_by_page=manifests_by_page,
                semantic_sidecar_sha256=semantic_sidecar_sha256,
            )
            fact_id = f"fact:{_canonical_sha256({'sample_id': request.sample_id, 'fact_key': target.fact_key, 'bridge_id': bridge.bridge_id})[:24]}"
            facts.append((target.fact_key, V25Step2BFactSummary(
                fact_id=fact_id,
                fact_type=target.fact_type,
                canonical_title=target.title,
                assertion_summary=bridge.basis,
                primary_evidence_ids=(bridge.figure_evidence_id,),
                primary_anchor_span_ids=(),
                source_table_ids=(),
                source_topic_key=_source_topic_key({"sample_id": request.sample_id, "review_id": bridge.review_id}),
                review_state=LearningReviewState.REVIEWED.value,
                semantic_diagram_bridge=bridge,
            )))
            continue
        # 空列表要显式注解，否则 mypy 只能报 var-annotated。元素类型取自 _span_maps 的
        # 返回声明。
        selected: list[EvidenceAnchorSpan] = []
        for selector in target.primary_anchors:
            needle = normalized_compare_text(selector.text)
            selected.extend(
                span for span in spans.values()
                if needle in normalized_compare_text(span.canonical_text)
            )
        selected = list({span.anchor_span_id: span for span in selected}.values())
        if not selected:
            raise ValueError(f"{request.sample_id}:{target.fact_key} has no policy-approved source anchor span")
        anchor_ids = tuple(item.anchor_span_id for item in selected)
        evidence_ids = tuple(dict.fromkeys(item.parent_evidence_id for item in selected))
        table_ids = tuple(dict.fromkeys(table_by_span[item] for item in anchor_ids if item in table_by_span))
        fact_id = f"fact:{_canonical_sha256({'sample_id': request.sample_id, 'fact_key': target.fact_key, 'anchors': anchor_ids})[:24]}"
        facts.append((target.fact_key, V25Step2BFactSummary(
            fact_id=fact_id,
            fact_type=target.fact_type,
            canonical_title=target.title,
            assertion_summary="；".join(target.required_terms) or target.title,
            primary_evidence_ids=evidence_ids,
            primary_anchor_span_ids=anchor_ids,
            source_table_ids=table_ids,
            source_topic_key=_source_topic_key({"sample_id": request.sample_id, "evidence_ids": evidence_ids, "anchor_ids": anchor_ids}),
            review_state=LearningReviewState.REVIEWED.value,
        )))
    return tuple(facts), manifest_by_span


def _source_grouping_sidecar(*, facts_by_sample: dict[str, dict[str, V25Step2BFactSummary]], manifest_by_span_by_sample: dict[str, dict[str, str]]) -> V25SourceGroupingReviewSidecar:
    reviews = []
    for sample_id, family_kind, fact_keys, basis in _SOURCE_FAMILY_SPECS:
        by_key = facts_by_sample[sample_id]
        members = tuple(by_key[key] for key in fact_keys)
        evidence_ids = tuple(dict.fromkeys(item for fact in members for item in fact.primary_evidence_ids))
        anchor_ids = tuple(dict.fromkeys(item for fact in members for item in fact.primary_anchor_span_ids))
        if not anchor_ids:
            raise ValueError("source grouping reviews require text/table source anchors")
        manifests = tuple(dict.fromkeys(manifest_by_span_by_sample[sample_id][anchor] for anchor in anchor_ids))
        payload = {
            "sample_id": sample_id,
            "family_kind": family_kind.value,
            "member_fact_keys": fact_keys,
            "source_evidence_ids": evidence_ids,
            "source_anchor_span_ids": anchor_ids,
            "source_manifest_sha256s": manifests,
            "basis": basis,
        }
        reviews.append(V25SourceGroupingReview(review_id=f"v25-source-grouping-review:{_canonical_sha256(payload)[:24]}", **payload))
    return V25SourceGroupingReviewSidecar(reviews=tuple(reviews))


def _bindings_for_sample(*, sample_id: str, sidecar: V25SourceGroupingReviewSidecar, facts_by_key: dict[str, V25Step2BFactSummary]) -> tuple[V25BSourceFamilyBinding, ...]:
    bindings = []
    for review in sidecar.reviews:
        if review.sample_id != sample_id:
            continue
        member_fact_ids = tuple(facts_by_key[key].fact_id for key in review.member_fact_keys)
        payload = {
            "family_kind": review.family_kind.value,
            "member_fact_ids": member_fact_ids,
            "source_evidence_ids": review.source_evidence_ids,
            "source_anchor_span_ids": review.source_anchor_span_ids,
            "source_manifest_sha256s": review.source_manifest_sha256s,
            "basis": review.basis,
        }
        bindings.append(V25BSourceFamilyBinding(family_id=f"v25b-source-family:{_canonical_sha256(payload)[:24]}", **payload))
    return tuple(bindings)


def _watermark_sidecar(*, sample_pages: dict[str, tuple[PageEvidence, ...]], manifest_hashes: dict[tuple[str, int], str]) -> V25WatermarkReviewSidecar:
    reviewed_samples = {"gonglu_01_text_list", "gonglu_41_diagram_mixed", "gonglu_55_standard_constraints"}
    reviews = []
    for sample_id, pages in sample_pages.items():
        candidates = detect_spatial_watermarks(
            SpatialTextToken(evidence_id=unit.evidence_id, page_num=page.page_num, text=unit.canonical_text.strip(), bbox=unit.locator.bbox)
            for page in pages
            for unit in page.evidence_units
            if unit.canonical_text.strip() and unit.locator.bbox is not None
        )
        for candidate in candidates:
            if sample_id not in reviewed_samples:
                continue
            manifests = tuple(dict.fromkeys(manifest_hashes[(sample_id, page_num)] for page_num in candidate.page_nums))
            payload = {
                "sample_id": sample_id,
                "candidate_text": candidate.text,
                "evidence_ids": candidate.evidence_ids,
                "page_nums": candidate.page_nums,
                "source_manifest_sha256s": manifests,
                "mean_x0": candidate.mean_x0,
                "mean_y0": candidate.mean_y0,
                "disposition": V25WatermarkDisposition.EXCLUDED_AS_WATERMARK.value,
                "basis": "The same semi-transparent publisher watermark is visible at a stable location across the original page crops; it is excluded from teaching output without changing source Evidence.",
            }
            reviews.append(V25WatermarkReview(review_id=f"v25-watermark-review:{_canonical_sha256(payload)[:24]}", **payload))
    if len(reviews) != 3:
        raise ValueError("Phase 2R.1.3 requires exactly three reviewed spatial-watermark candidates")
    return V25WatermarkReviewSidecar(reviews=tuple(reviews))


def _unresolved_watermarks(*, sample_id: str, pages: tuple[PageEvidence, ...], manifest_hashes, sidecar: V25WatermarkReviewSidecar):
    candidates = detect_spatial_watermarks(
        SpatialTextToken(evidence_id=unit.evidence_id, page_num=page.page_num, text=unit.canonical_text.strip(), bbox=unit.locator.bbox)
        for page in pages
        for unit in page.evidence_units
        if unit.canonical_text.strip() and unit.locator.bbox is not None
    )
    reviewed = {
        (item.candidate_text, item.evidence_ids, item.page_nums, item.mean_x0, item.mean_y0): item
        for item in sidecar.reviews
        if item.sample_id == sample_id
    }
    unresolved = []
    for candidate in candidates:
        key = (candidate.text, candidate.evidence_ids, candidate.page_nums, candidate.mean_x0, candidate.mean_y0)
        review = reviewed.get(key)
        if review is None or review.disposition == V25WatermarkDisposition.MANUAL_REVIEW:
            unresolved.append(candidate)
    return tuple(unresolved)


def _rendered_table(table) -> RenderedTableInput:
    rows = [["" for _ in range(table.column_count)] for _ in range(table.row_count)]
    for row in table.rows:
        for cell in row.cells:
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for column_index in range(cell.column_index, cell.column_index + cell.col_span):
                    rows[row_index][column_index] = cell.canonical_text
    return RenderedTableInput(source_table_evidence_id=table.table_evidence_id, rows=tuple(tuple(row) for row in rows))


def _teaching_gate(*, request, response, pages, diagram_sidecar, fixture_sample, unresolved_watermarks):
    spans = {span.anchor_span_id: span for section in request.sections for item in section.text_evidence for span in item.anchor_spans}
    spans.update({span.anchor_span_id: span for section in request.sections for table in section.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans})
    context_only = {span.anchor_span_id for section in request.sections for item in section.text_evidence if item.context_only for span in item.anchor_spans}
    titles = [assess_title_quality(title=item.label, evidence_texts=(spans[span_id].canonical_text for span_id in item.primary_anchor_span_ids)) for item in response.proposals]
    headings = [assess_heading_only_fact(proposal_key=f"v25-proposal:{index}", selected_anchor_span_ids=item.primary_anchor_span_ids, context_only_anchor_span_ids=context_only) for index, item in enumerate(response.proposals)]
    tables = [assess_table_structure(source=table, rendered=_rendered_table(table)) for section in request.sections for table in section.tables]
    wanted = {item.review_id for item in fixture_sample.diagrams}
    diagrams = [assess_diagram_semantics(item) for item in diagram_sidecar.entries if item.sample_id == request.sample_id and item.review_id in wanted]
    return build_teaching_quality_gate(titles=titles, tables=tables, heading_only_facts=headings, diagrams=diagrams, watermark_candidates=unresolved_watermarks)


def _card_alignment(*, request, fixture_sample, facts_by_key: dict[str, V25Step2BFactSummary]) -> tuple[int, int]:
    actual = {(tuple(card.primary_fact_ids), card.presentation.value) for card in build_v25b_source_fake_response(request=request).proposals}
    expected = {
        (tuple(facts_by_key[key].fact_id for key in card.primary_fact_keys), card.presentation.value)
        for card in fixture_sample.cards
    }
    return sum(item in actual for item in expected), sum(item not in actual for item in expected)


def _semantic_diagram_bridge_audits(*, fixture, diagram_sidecar, semantic_sidecar_sha256: str, manifest_hashes, facts_by_sample):
    fixture_samples = {sample.sample_id: sample for sample in fixture.samples}
    audits = []
    for entry in diagram_sidecar.entries:
        if entry.semantic_contract_version != DiagramSemanticContractVersion.SEMANTIC_V2:
            raise ValueError(f"{entry.review_id}: V25 only admits semantic_v2 diagram bridges")
        fixture_sample = fixture_samples.get(entry.sample_id)
        if fixture_sample is None:
            raise ValueError(f"{entry.review_id}: semantic diagram sample is outside the V25 reviewed fixture")
        reviewed_diagrams = [
            item
            for item in fixture_sample.diagrams
            if item.review_id == entry.review_id and item.figure_evidence_id == entry.figure_evidence_id
        ]
        if len(reviewed_diagrams) != 1:
            raise ValueError(f"{entry.review_id}: semantic bridge must bind exactly one reviewed diagram target")
        fact_matches = [
            (fact_key, fact)
            for fact_key, fact in facts_by_sample[entry.sample_id].items()
            if fact.semantic_diagram_bridge is not None and fact.semantic_diagram_bridge.review_id == entry.review_id
        ]
        if len(fact_matches) > 1:
            raise ValueError(f"{entry.review_id}: semantic bridge cannot bind multiple reviewed Facts")
        if fact_matches:
            fact_key, fact = fact_matches[0]
            audits.append(V25SemanticDiagramBridgeAudit(
                sample_id=entry.sample_id,
                bridge=fact.semantic_diagram_bridge,
                disposition=V25SemanticDiagramBridgeDisposition.FACT_SUMMARY,
                fact_key=fact_key,
                basis="Reviewed diagram_relation Fact enters V25B with its source figure, semantic_v2 nodes/edges, sidecar hash, and source manifest.",
            ))
            continue
        if (entry.sample_id, entry.page_num) not in manifest_hashes:
            raise ValueError(f"{entry.review_id}: semantic bridge page is outside the reviewed V25 source set")
        audits.append(V25SemanticDiagramBridgeAudit(
            sample_id=entry.sample_id,
            bridge=_diagram_bridge_from_entry(
                entry=entry,
                source_manifest_sha256=manifest_hashes[(entry.sample_id, entry.page_num)],
                semantic_sidecar_sha256=semantic_sidecar_sha256,
            ),
            disposition=V25SemanticDiagramBridgeDisposition.EVIDENCE_ONLY,
            basis="Reviewed semantic_v2 diagram has no reviewed Fact target, so it remains evidence-only and is excluded from V25B FactSummary fail-closed.",
        ))
    if len(audits) != len(diagram_sidecar.entries):
        raise ValueError("every reviewed semantic_v2 diagram must produce exactly one auditable V25 bridge record")
    return tuple(audits)


def materialize_phase2r13_artifacts(*, repository_root: Path) -> dict[str, Path]:
    repairs = _materialize_table_repairs(repository_root=repository_root)
    return {"repair_24": repairs[("gonglu_24_table_formula", 8)], "repair_41": repairs[("gonglu_41_diagram_mixed", 2)]}


def build_phase2r13_report(*, repository_root: Path, generated_at_utc: str) -> Phase2R13Report:
    repository_root = Path(repository_root).resolve()
    frozen_paths = (
        repository_root / _TARGET_REVIEWED_FIXTURE,
        repository_root / _TARGET_SEMANTIC_SIDECAR,
        repository_root / _TARGET_CONTINUATION_SIDECAR,
        repository_root / _TABLE_REPAIR_SIDECAR,
    )
    before_hashes = {path.relative_to(repository_root).as_posix(): _sha256(path) for path in frozen_paths}
    repair_paths = _materialize_table_repairs(repository_root=repository_root)
    fixture = load_learning_golden_fixture(repository_root / _TARGET_REVIEWED_FIXTURE)
    diagram_path = repository_root / _TARGET_SEMANTIC_SIDECAR
    diagram_sidecar = load_diagram_review_sidecar(diagram_path)
    semantic_sidecar_sha256 = _sha256(diagram_path)
    registry = load_golden_registry(repository_root / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml")
    policy = load_content_policy(repository_root / "configs/v2/content_policies/core_only_v1.yaml")
    disposition = load_diagram_disposition_sidecar(repository_root / _SOURCE_DISPOSITION_SIDECAR)
    continuation = _continuation_binding(repository_root=repository_root)
    sample_contexts = []
    facts_by_sample: dict[str, dict[str, V25Step2BFactSummary]] = {}
    manifest_by_span_by_sample: dict[str, dict[str, str]] = {}
    sample_pages: dict[str, tuple[PageEvidence, ...]] = {}
    manifest_hashes: dict[tuple[str, int], str] = {}
    for sample in registry.samples:
        builds, paths = _builds_for_sample(repository_root=repository_root, sample=sample, policy=policy, diagram_sidecar=diagram_sidecar, diagram_path=diagram_path, disposition=disposition, repairs_by_page=repair_paths)
        request = _build_v25_request(sample=sample, builds=builds, registry=registry, policy=policy, continuation=continuation)
        fixture_sample = next(item for item in fixture.samples if item.sample_id == sample.sample_id)
        fact_pairs, manifest_by_span = _fact_summaries(request=request, fixture_sample=fixture_sample, builds=builds, diagram_sidecar=diagram_sidecar, semantic_sidecar_sha256=semantic_sidecar_sha256)
        facts_by_sample[sample.sample_id] = dict(fact_pairs)
        manifest_by_span_by_sample[sample.sample_id] = manifest_by_span
        sample_pages[sample.sample_id] = tuple(PageEvidence.model_validate_json(path.read_bytes()) for path in paths)
        for build in builds:
            manifest_hashes[(sample.sample_id, build.projection.page_num)] = build.projection.source_manifest_sha256
        sample_contexts.append((sample, builds, request, fixture_sample, tuple(item for _, item in fact_pairs)))
    semantic_diagram_bridges = _semantic_diagram_bridge_audits(
        fixture=fixture,
        diagram_sidecar=diagram_sidecar,
        semantic_sidecar_sha256=semantic_sidecar_sha256,
        manifest_hashes=manifest_hashes,
        facts_by_sample=facts_by_sample,
    )
    grouping_sidecar = _source_grouping_sidecar(facts_by_sample=facts_by_sample, manifest_by_span_by_sample=manifest_by_span_by_sample)
    grouping_path = repository_root / _SOURCE_GROUPING_PATH
    _write_yaml(grouping_path, grouping_sidecar.model_dump(mode="json"))
    watermark_sidecar = _watermark_sidecar(sample_pages=sample_pages, manifest_hashes=manifest_hashes)
    watermark_path = repository_root / _WATERMARK_REVIEW_PATH
    _write_yaml(watermark_path, watermark_sidecar.model_dump(mode="json"))
    audits = []
    eligible_repaired = {
        "gonglu_24_table_formula": ("formwork.arch-removal",),
        "gonglu_41_diagram_mixed": ("tunnel.span-classification",),
    }
    for sample, builds, request, fixture_sample, facts in sample_contexts:
        response = build_v25_source_fake_response(request=request)
        step2a_gate = assess_v25_step2a_response(request=request, response=response)
        bindings = _bindings_for_sample(sample_id=sample.sample_id, sidecar=grouping_sidecar, facts_by_key=facts_by_sample[sample.sample_id])
        request2b = build_v25b_step2b_request(sample_id=sample.sample_id, material_profile_id=registry.material_profile_id, facts=facts, source_family_bindings=bindings)
        step2b_gate = assess_v25b_step2b_response(request=request2b, response=build_v25b_source_fake_response(request=request2b))
        unresolved = _unresolved_watermarks(sample_id=sample.sample_id, pages=sample_pages[sample.sample_id], manifest_hashes=manifest_hashes, sidecar=watermark_sidecar)
        teaching = _teaching_gate(request=request, response=response, pages=sample_pages[sample.sample_id], diagram_sidecar=diagram_sidecar, fixture_sample=fixture_sample, unresolved_watermarks=unresolved)
        exact, drift = _card_alignment(request=request2b, fixture_sample=fixture_sample, facts_by_key=facts_by_sample[sample.sample_id])
        audits.append(Phase2R13SampleAudit(
            sample_id=sample.sample_id,
            v25_step2a_gate=step2a_gate,
            v25b_grouping_gate=step2b_gate,
            reviewed_fact_count=len(fixture_sample.facts),
            fact_summary_count=len(facts),
            semantic_diagram_bridge_count=sum(item.semantic_diagram_bridge is not None for item in facts),
            eligible_repaired_fact_keys=eligible_repaired.get(sample.sample_id, ()),
            source_grouping_review_count=len(bindings),
            reviewed_card_alignment_count=exact,
            reviewed_card_drift_count=drift,
            unresolved_watermark_count=len(unresolved),
            teaching_gate=teaching,
        ))
    after_hashes = {path.relative_to(repository_root).as_posix(): _sha256(path) for path in frozen_paths}
    accepted = all(
        audit.v25_step2a_gate.valid
        and audit.v25b_grouping_gate.valid
        and audit.reviewed_card_drift_count == 0
        and audit.unresolved_watermark_count == 0
        and audit.teaching_gate.publishable
        for audit in audits
    )
    return Phase2R13Report(
        generated_at_utc=generated_at_utc,
        reviewed_fixture_sha256=_sha256(repository_root / _TARGET_REVIEWED_FIXTURE),
        semantic_sidecar_sha256=semantic_sidecar_sha256,
        semantic_diagram_review_bridge_count=len(semantic_diagram_bridges),
        semantic_diagram_fact_summary_count=sum(
            item.disposition == V25SemanticDiagramBridgeDisposition.FACT_SUMMARY
            for item in semantic_diagram_bridges
        ),
        semantic_diagram_evidence_only_count=sum(
            item.disposition == V25SemanticDiagramBridgeDisposition.EVIDENCE_ONLY
            for item in semantic_diagram_bridges
        ),
        semantic_diagram_bridges=semantic_diagram_bridges,
        new_table_repair_sidecar_sha256s=tuple(_sha256(path) for path in repair_paths.values()),
        source_grouping_sidecar_sha256=_sha256(grouping_path),
        watermark_sidecar_sha256=_sha256(watermark_path),
        samples=tuple(audits),
        frozen_inputs_unchanged=before_hashes == after_hashes,
        accepted=accepted,
        next_required_phase="Phase 2R.1.4 bounded real V25/V25B smoke only after explicit approval",
    )
