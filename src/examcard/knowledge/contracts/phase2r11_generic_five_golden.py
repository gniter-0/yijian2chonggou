"""Phase 2R.1.1 generic V25/V25B five-sample offline integration."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from ...evidence.golden import GoldenSampleRegistry, load_golden_registry
from ...evidence.schema import EvidenceKind, PageEvidence
from ...materials.schema import StrictModel

from ..diagram.disposition import load_diagram_disposition_sidecar
from ..diagram.sidecar import DiagramReviewState, DiagramSemanticContractVersion, load_diagram_review_sidecar
from ..learning_fixture import LearningGoldenFixture, LearningReviewState, load_learning_golden_fixture
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
    V25BBlockedPreflight,
    V25JournalRecord,
    V25Step2AGate,
    V25Step2ARequest,
    assess_v25_step2a_response,
    build_v25_source_fake_response,
    build_v25_step2a_request,
    make_v25_offline_journal,
    v25_step2a_request_payload,
)


PHASE2R11_REPORT_VERSION = "phase2r11_generic_five_golden_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Phase2R11SampleKind(StrEnum):
    TEXT_LIST = "text_list"
    TABLE_FORMULA = "table_formula"
    DIAGRAM_MIXED = "diagram_mixed"
    STANDARD_CONSTRAINTS = "standard_constraints"
    COST_FLOW = "cost_flow"


class Phase2R11Readiness(StrEnum):
    CONTRACT_BLOCKED = "contract_blocked"
    READY = "ready"


class V25GoldenReviewKind(StrEnum):
    FACT = "fact"
    CARD = "card"
    DIAGRAM = "diagram"
    PROPOSAL_DISPOSITION = "proposal_disposition"


class V25GoldenReviewWorkItem(StrictModel):
    """One immutable-fixture-linked draft item for a human review workbench."""

    work_item_id: str = Field(pattern=r"^v25-golden-review:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    kind: V25GoldenReviewKind
    target_ref: str = Field(min_length=3)
    source_fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    state: LearningReviewState = LearningReviewState.DRAFT
    basis: str = Field(min_length=3, max_length=1600)

    @model_validator(mode="after")
    def validate_item(self) -> "V25GoldenReviewWorkItem":
        if self.state != LearningReviewState.DRAFT:
            raise ValueError("Phase 2R.1.1 workbench entries must remain draft")
        payload = self.model_dump(mode="json", exclude={"work_item_id"})
        expected = f"v25-golden-review:{_canonical_sha256(payload)[:24]}"
        if self.work_item_id != expected:
            raise ValueError("V25 Golden review work item ID must be deterministic")
        return self


class V25GoldenReviewQueue(StrictModel):
    queue_version: str = "v25_golden_review_queue_v1"
    source_fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    entries: tuple[V25GoldenReviewWorkItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_queue(self) -> "V25GoldenReviewQueue":
        ids = [item.work_item_id for item in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("V25 Golden review queue item IDs must be unique")
        if any(item.source_fixture_id != self.source_fixture_id for item in self.entries):
            raise ValueError("V25 Golden review queue items must share one fixture")
        if any(item.source_fixture_sha256 != self.source_fixture_sha256 for item in self.entries):
            raise ValueError("V25 Golden review queue item hashes must share one fixture")
        return self


class Phase2R11Step2ARequestAudit(StrictModel):
    request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    cache_key: str = Field(pattern=r"^step2-cache:[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^step2-[0-9a-f]{64}$")
    request_version: str
    model_input_version: str
    prompt_version: str
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256s: tuple[str, ...]
    projection_sha256s: tuple[str, ...]
    coverage_obligation_count: int = Field(ge=0)
    coverage_context_count: int = Field(ge=0)
    continuation_marker_count: int = Field(ge=0)
    reviewed_continuation_binding_count: int = Field(ge=0)
    reviewed_table_repair_count: int = Field(ge=0)
    model_payload_has_learning_golden: bool


class Phase2R11SampleIntegrationAudit(StrictModel):
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    sample_kind: Phase2R11SampleKind
    selected_pages: tuple[int, ...] = Field(min_length=1)
    request: Phase2R11Step2ARequestAudit
    journal: V25JournalRecord
    fake_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    fake_response_shape_valid: bool
    step2a_gate: V25Step2AGate
    teaching_gate: TeachingQualityGate
    step2b_preflight: V25BBlockedPreflight
    example_evidence_count: int = Field(ge=0)
    examples_excluded_by_policy_count: int = Field(ge=0)
    semantic_v2_diagram_count: int = Field(ge=0)
    golden_review_work_item_count: int = Field(ge=1)
    readiness_blockers: tuple[str, ...] = Field(min_length=1)
    real_llm_readiness: Phase2R11Readiness

    @model_validator(mode="after")
    def validate_audit(self) -> "Phase2R11SampleIntegrationAudit":
        if self.journal.request_id != self.request.request_id:
            raise ValueError("V25 journal must bind its request audit")
        if self.real_llm_readiness == Phase2R11Readiness.READY:
            if self.readiness_blockers or self.step2b_preflight.draft_learning_review_count:
                raise ValueError("ready V25 samples cannot carry unresolved review blockers")
        return self


class Phase2R11FiveGoldenReport(StrictModel):
    schema_version: str = PHASE2R11_REPORT_VERSION
    phase: str = "2R.1.1"
    mode: str = "offline"
    generated_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    registry_id: str
    material_profile_id: str
    content_policy_id: str
    content_policy_revision: int = Field(ge=1)
    phase2r0_freeze_valid: bool
    phase2r0_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    samples: tuple[Phase2R11SampleIntegrationAudit, ...]
    golden_review_queue: V25GoldenReviewQueue
    selected_page_count: int = Field(ge=1)
    successful_projection_count: int = Field(ge=0)
    fake_step2a_contract_pass_count: int = Field(ge=0)
    v25b_unreviewed_preflight_block_count: int = Field(ge=0)
    network_calls: int = 0
    real_step2a_requests: int = 0
    real_step2b_requests: int = 0
    source_mutations: int = 0
    frozen_inputs_unchanged: bool
    offline_integration_valid: bool
    accepted: bool
    next_required_phase: str

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R11FiveGoldenReport":
        if len(self.samples) != 5 or len({item.sample_id for item in self.samples}) != 5:
            raise ValueError("Phase 2R.1.1 requires exactly five unique sample audits")
        if self.selected_page_count != sum(len(item.selected_pages) for item in self.samples):
            raise ValueError("Phase 2R.1.1 selected page total must match sample audits")
        if any((self.network_calls, self.real_step2a_requests, self.real_step2b_requests, self.source_mutations)):
            raise ValueError("Phase 2R.1.1 must remain offline and source-read-only")
        if self.accepted != self.offline_integration_valid:
            raise ValueError("Phase 2R.1.1 acceptance must equal offline integration validity")
        return self


_SAMPLE_KINDS = {
    "gonglu_01_text_list": Phase2R11SampleKind.TEXT_LIST,
    "gonglu_24_table_formula": Phase2R11SampleKind.TABLE_FORMULA,
    "gonglu_41_diagram_mixed": Phase2R11SampleKind.DIAGRAM_MIXED,
    "gonglu_55_standard_constraints": Phase2R11SampleKind.STANDARD_CONSTRAINTS,
    "gonglu_62_cost_flow_incident": Phase2R11SampleKind.COST_FLOW,
}


def _canonical_sha256(value: object) -> str:
    from . import canonical_step2_sha256

    return canonical_step2_sha256(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _review_queue(*, fixture: LearningGoldenFixture, fixture_sha256: str) -> V25GoldenReviewQueue:
    entries: list[V25GoldenReviewWorkItem] = []

    def append(*, sample_id: str, kind: V25GoldenReviewKind, target_ref: str, basis: str) -> None:
        payload = {
            "sample_id": sample_id,
            "kind": kind.value,
            "target_ref": target_ref,
            "source_fixture_id": fixture.fixture_id,
            "source_fixture_sha256": fixture_sha256,
            "state": LearningReviewState.DRAFT.value,
            "basis": basis,
        }
        entries.append(V25GoldenReviewWorkItem(work_item_id=f"v25-golden-review:{_canonical_sha256(payload)[:24]}", **payload))

    for sample in fixture.samples:
        for fact in sample.facts:
            if fact.review.state == LearningReviewState.DRAFT:
                append(sample_id=sample.sample_id, kind=V25GoldenReviewKind.FACT, target_ref=f"fact:{fact.fact_key}", basis=fact.review.basis)
        for card in sample.cards:
            if card.review.state == LearningReviewState.DRAFT:
                append(sample_id=sample.sample_id, kind=V25GoldenReviewKind.CARD, target_ref=f"card:{card.card_key}", basis=card.review.basis)
        for diagram in sample.diagrams:
            if diagram.review.state == LearningReviewState.DRAFT:
                append(sample_id=sample.sample_id, kind=V25GoldenReviewKind.DIAGRAM, target_ref=f"diagram:{diagram.review_id}", basis=diagram.review.basis)
        for proposal in sample.proposal_dispositions:
            if proposal.review.state == LearningReviewState.DRAFT:
                append(
                    sample_id=sample.sample_id,
                    kind=V25GoldenReviewKind.PROPOSAL_DISPOSITION,
                    target_ref=f"proposal:{proposal.page_num}:{proposal.proposal_key}",
                    basis=proposal.review.basis,
                )
    return V25GoldenReviewQueue(
        source_fixture_id=fixture.fixture_id,
        source_fixture_sha256=fixture_sha256,
        entries=tuple(entries),
    )


def _example_counts(pages: tuple[PageEvidence, ...]) -> tuple[int, int]:
    examples = sum(
        unit.kind in {EvidenceKind.EXAMPLE, EvidenceKind.EXERCISE}
        for page in pages
        for unit in page.evidence_units
    )
    return examples, examples


def _rendered_table(table) -> RenderedTableInput:
    rows = [["" for _ in range(table.column_count)] for _ in range(table.row_count)]
    for row in table.rows:
        for cell in row.cells:
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for column_index in range(cell.column_index, cell.column_index + cell.col_span):
                    rows[row_index][column_index] = cell.canonical_text
    return RenderedTableInput(source_table_evidence_id=table.table_evidence_id, rows=tuple(tuple(row) for row in rows))


def _teaching_gate(*, request: V25Step2ARequest, response, pages: tuple[PageEvidence, ...], fixture_sample, diagram_sidecar) -> TeachingQualityGate:
    spans = {
        span.anchor_span_id: span
        for section in request.sections
        for item in section.text_evidence
        for span in item.anchor_spans
    }
    spans.update(
        {
            span.anchor_span_id: span
            for section in request.sections
            for table in section.tables
            for row in table.rows
            for cell in row.cells
            for span in cell.anchor_spans
        }
    )
    context_only = {
        span.anchor_span_id
        for section in request.sections
        for item in section.text_evidence
        if item.context_only
        for span in item.anchor_spans
    }
    titles = []
    heading_results = []
    for index, proposal in enumerate(response.proposals):
        source_texts = tuple(spans[item].canonical_text for item in proposal.primary_anchor_span_ids)
        titles.append(assess_title_quality(title=proposal.label, evidence_texts=source_texts))
        heading_results.append(
            assess_heading_only_fact(
                proposal_key=f"v25-proposal:{index}",
                selected_anchor_span_ids=proposal.primary_anchor_span_ids,
                context_only_anchor_span_ids=context_only,
            )
        )
    table_results = tuple(
        assess_table_structure(source=table, rendered=_rendered_table(table))
        for section in request.sections
        for table in section.tables
    )
    expected_diagram_ids = {item.review_id for item in fixture_sample.diagrams}
    diagrams = tuple(
        assess_diagram_semantics(entry)
        for entry in diagram_sidecar.entries
        if entry.sample_id == request.sample_id and entry.review_id in expected_diagram_ids
    )
    watermarks = detect_spatial_watermarks(
        SpatialTextToken(
            evidence_id=unit.evidence_id,
            page_num=page.page_num,
            text=unit.canonical_text.strip(),
            bbox=unit.locator.bbox,
        )
        for page in pages
        for unit in page.evidence_units
        if unit.canonical_text.strip() and unit.locator.bbox is not None
    )
    return build_teaching_quality_gate(
        titles=titles,
        tables=table_results,
        heading_only_facts=heading_results,
        diagrams=diagrams,
        watermark_candidates=watermarks,
    )


def _audit_sample(
    *,
    registry_sample,
    material_profile_id: str,
    fixture_sample,
    evidence_root: Path,
    repository_root: Path,
    content_policy,
    diagram_sidecar,
    diagram_path: Path,
    disposition_sidecar,
    table_repair,
    table_repair_path: Path,
    review_queue: V25GoldenReviewQueue,
) -> tuple[Phase2R11SampleIntegrationAudit, int]:
    pages: list[PageEvidence] = []
    builds = []
    for page_num in registry_sample.selected_pages:
        manifest_path = evidence_root / registry_sample.sample_id / "pages" / f"page_{page_num:04d}.json"
        page = PageEvidence.model_validate_json(manifest_path.read_bytes())
        repair_kwargs = {}
        if table_repair.sample_id == registry_sample.sample_id and table_repair.page_num == page_num:
            repair_kwargs = {
                "table_repair_sidecar": table_repair,
                "table_repair_sidecar_path": table_repair_path,
            }
        builds.append(
            build_step2a_semantic_projection(
                sample_id=registry_sample.sample_id,
                page=page,
                source_manifest_path=manifest_path,
                source_manifest_reference=manifest_path.relative_to(repository_root).as_posix(),
                content_policy=content_policy,
                diagram_review_sidecar=diagram_sidecar,
                diagram_review_sidecar_path=diagram_path,
                diagram_disposition_sidecar=disposition_sidecar,
                repository_root=repository_root,
                **repair_kwargs,
            )
        )
        pages.append(page)
    source_hashes = tuple(build.projection.source_manifest_sha256 for build in builds)
    projection_hashes = tuple(build.projection.fingerprint_sha256 for build in builds)
    document_hashes = {build.projection.source_document_sha256 for build in builds}
    policy_fingerprints = {build.projection.content_policy_fingerprint_sha256 for build in builds}
    reviewed_repair_ids = tuple(
        repair_id
        for build in builds
        for repair_id in build.projection.reviewed_table_repair_ids
    )
    repair_sidecar_hashes = tuple(
        value
        for build in builds
        for value in (build.projection.table_repair_sidecar_sha256,)
        if value is not None
    )
    if len(document_hashes) != 1:
        raise ValueError("V25 generic sample input must bind one source document")
    if len(policy_fingerprints) != 1:
        raise ValueError("V25 generic sample input must bind one Content Policy fingerprint")
    request = build_v25_step2a_request(
        sample_id=registry_sample.sample_id,
        material_profile_id=material_profile_id,
        source_document_sha256=document_hashes.pop(),
        source_manifest_sha256s=source_hashes,
        projection_sha256s=projection_hashes,
        content_policy_id=content_policy.policy_id,
        content_policy_revision=content_policy.revision,
        content_policy_fingerprint_sha256=policy_fingerprints.pop(),
        sections=tuple(build.section for build in builds),
        reviewed_table_repair_ids=reviewed_repair_ids,
        table_repair_sidecar_sha256s=repair_sidecar_hashes,
    )
    response = build_v25_source_fake_response(request=request)
    gate = assess_v25_step2a_response(request=request, response=response)
    teaching_gate = _teaching_gate(
        request=request,
        response=response,
        pages=tuple(pages),
        fixture_sample=fixture_sample,
        diagram_sidecar=diagram_sidecar,
    )
    payload = v25_step2a_request_payload(request)
    payload_text = repr(payload).lower()
    if any(term in payload_text for term in ("learning_golden", "card_key", "golden_assertion", "review_state")):
        raise ValueError("V25 Step 2A model payload unexpectedly includes Learning Golden material")
    sample_review_items = tuple(item for item in review_queue.entries if item.sample_id == registry_sample.sample_id)
    semantic_v2_diagram_count = sum(
        entry.sample_id == registry_sample.sample_id
        and entry.state == DiagramReviewState.REVIEWED
        and entry.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2
        for entry in diagram_sidecar.entries
    )
    blockers = ["unreviewed_learning_target"]
    continuation_markers = sum(len(build.projection.continuation_records) for build in builds)
    if continuation_markers:
        blockers.append("missing_sample_specific_reviewed_continuation_binding")
    if fixture_sample.diagrams and semantic_v2_diagram_count < len(fixture_sample.diagrams):
        blockers.append("diagram_not_semantic_v2")
    examples, excluded_examples = _example_counts(tuple(pages))
    request_audit = Phase2R11Step2ARequestAudit(
        request_id=request.identity.request_id,
        cache_key=request.identity.cache_key,
        idempotency_key=request.identity.idempotency_key,
        request_version=request.identity.request_version,
        model_input_version=request.model_input_version,
        prompt_version=request.prompt_version,
        response_schema_sha256=request.identity.response_schema_sha256,
        payload_sha256=request.identity.payload_sha256,
        source_manifest_sha256s=request.source_manifest_sha256s,
        projection_sha256s=request.projection_sha256s,
        coverage_obligation_count=len(request.coverage_obligations),
        coverage_context_count=len(request.coverage_contexts),
        continuation_marker_count=continuation_markers,
        reviewed_continuation_binding_count=len(request.reviewed_continuation_bindings),
        reviewed_table_repair_count=len(request.reviewed_table_repair_ids),
        model_payload_has_learning_golden=False,
    )
    audit = Phase2R11SampleIntegrationAudit(
        sample_id=registry_sample.sample_id,
        sample_kind=_SAMPLE_KINDS[registry_sample.sample_id],
        selected_pages=registry_sample.selected_pages,
        request=request_audit,
        journal=make_v25_offline_journal(request=request),
        fake_response_sha256=_canonical_sha256(response.model_dump(mode="json")),
        fake_response_shape_valid=True,
        step2a_gate=gate,
        teaching_gate=teaching_gate,
        step2b_preflight=V25BBlockedPreflight(
            sample_id=registry_sample.sample_id,
            source_fact_count=len(response.proposals),
            draft_learning_review_count=len(sample_review_items),
        ),
        example_evidence_count=examples,
        examples_excluded_by_policy_count=excluded_examples,
        semantic_v2_diagram_count=semantic_v2_diagram_count,
        golden_review_work_item_count=len(sample_review_items),
        readiness_blockers=tuple(blockers),
        real_llm_readiness=Phase2R11Readiness.CONTRACT_BLOCKED,
    )
    return audit, len(builds)


def build_phase2r11_five_golden_report(
    *, repository_root: Path, generated_at_utc: str
) -> Phase2R11FiveGoldenReport:
    """Build five generic V25 source contracts without changing source artifacts."""

    from ...materials.catalog import load_content_policy
    from tools.verify_phase2r1_phase2r0_freeze import verify_phase2r1_phase2r0_freeze

    repository_root = Path(repository_root).resolve()
    registry_path = repository_root / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
    evidence_root = repository_root / "data/evidence_v2/golden/gonglu_phase0_v1"
    learning_path = repository_root / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r053_page1_reviewed.yaml"
    policy_path = repository_root / "configs/v2/content_policies/core_only_v1.yaml"
    diagram_path = repository_root / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml"
    disposition_path = repository_root / "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25e1_audited.yaml"
    repair_path = repository_root / "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
    freeze_before = verify_phase2r1_phase2r0_freeze(repository_root=repository_root)
    registry: GoldenSampleRegistry = load_golden_registry(registry_path)
    fixture = load_learning_golden_fixture(learning_path)
    content_policy = load_content_policy(policy_path)
    diagram_sidecar = load_diagram_review_sidecar(diagram_path)
    disposition_sidecar = load_diagram_disposition_sidecar(disposition_path)
    table_repair = load_table_repair_sidecar(repair_path)
    queue = _review_queue(fixture=fixture, fixture_sha256=_sha256(learning_path))
    fixture_samples = {sample.sample_id: sample for sample in fixture.samples}
    audits_and_counts = tuple(
        _audit_sample(
            registry_sample=sample,
            material_profile_id=registry.material_profile_id,
            fixture_sample=fixture_samples[sample.sample_id],
            evidence_root=evidence_root,
            repository_root=repository_root,
            content_policy=content_policy,
            diagram_sidecar=diagram_sidecar,
            diagram_path=diagram_path,
            disposition_sidecar=disposition_sidecar,
            table_repair=table_repair,
            table_repair_path=repair_path,
            review_queue=queue,
        )
        for sample in registry.samples
    )
    audits = tuple(item[0] for item in audits_and_counts)
    projected_pages = sum(item[1] for item in audits_and_counts)
    freeze_after = verify_phase2r1_phase2r0_freeze(repository_root=repository_root)
    frozen_unchanged = freeze_before == freeze_after
    integration_valid = (
        bool(freeze_before["valid"])
        and frozen_unchanged
        and len(queue.entries) == 39
        and all(item.fake_response_shape_valid and item.step2a_gate.valid for item in audits)
        and all(item.step2b_preflight.state == "blocked_unreviewed_learning_target" for item in audits)
        and all(item.example_evidence_count == item.examples_excluded_by_policy_count for item in audits)
    )
    return Phase2R11FiveGoldenReport(
        registry_id=registry.registry_id,
        material_profile_id=registry.material_profile_id,
        content_policy_id=content_policy.policy_id,
        content_policy_revision=content_policy.revision,
        phase2r0_freeze_valid=bool(freeze_before["valid"]),
        phase2r0_freeze_sha256=freeze_before["aggregate_sha256"],
        samples=audits,
        golden_review_queue=queue,
        selected_page_count=sum(len(sample.selected_pages) for sample in registry.samples),
        successful_projection_count=projected_pages,
        fake_step2a_contract_pass_count=sum(item.step2a_gate.valid for item in audits),
        v25b_unreviewed_preflight_block_count=sum(
            item.step2b_preflight.state == "blocked_unreviewed_learning_target" for item in audits
        ),
        frozen_inputs_unchanged=frozen_unchanged,
        offline_integration_valid=integration_valid,
        accepted=integration_valid,
        next_required_phase="Phase 2R.1.2 human review of V25 Golden work items and semantic_v2 diagram closure",
        generated_at_utc=generated_at_utc,
    )
