"""Phase 2R.0.7 real adjacent-page continuation regressions (offline only)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import PageEvidence
from examcard.knowledge.continuation.review import (
    ContinuationLearningGoldenFixture,
    ContinuationMarkerDisposition,
    assess_continuation_learning_golden,
    load_continuation_review_sidecar,
    make_continuation_review_sidecar_id,
)
from examcard.knowledge.schema import CompletenessMode, FactType
from examcard.knowledge.contracts import (
    ContinuationAllItemsAuditState,
    ContinuationBindingFailureCode,
    ContinuationBindingState,
    ModelNumericClaim,
    NumericTokenClass,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2ResultState,
    build_continuation_binding,
    build_step2a_request,
    classify_numeric_tokens,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    derive_step2a_table_cell_anchors,
    validate_step2a_response,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "data/shadow_v2/gonglu/phase3b11_smoke_20260726",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



SAMPLE_ID = "gonglu_24_continuation_real"
PAGE_ROOT = ROOT / "data/shadow_v2/gonglu/phase3b11_smoke_20260726/chapters/24/evidence/pages"
PAGE_REFERENCES = tuple(
    f"data/shadow_v2/gonglu/phase3b11_smoke_20260726/chapters/24/evidence/pages/page_{page_num:04d}.json"
    for page_num in (1, 2)
)
SIDECAR_PATH = ROOT / "configs/v2/continuation_review_sidecars/gonglu_24_continuation_real.reviewed.yaml"
GOLDEN_PATH = ROOT / "scripts/tests/fixtures/v2/continuation_learning/gonglu_24_real_pages.reviewed.yaml"


def _load_golden() -> ContinuationLearningGoldenFixture:
    return ContinuationLearningGoldenFixture.model_validate(yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8")))


def _real_page_builds():
    policy = load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml")
    builds = []
    for page_num, source_reference in zip((1, 2), PAGE_REFERENCES, strict=True):
        path = PAGE_ROOT / f"page_{page_num:04d}.json"
        builds.append(
            build_step2a_semantic_projection(
                sample_id=SAMPLE_ID,
                page=PageEvidence.model_validate_json(path.read_bytes()),
                source_manifest_path=path,
                source_manifest_reference=source_reference,
                content_policy=policy,
                repository_root=ROOT,
            )
        )
    return tuple(builds)


def _closed_binding(first, adjacent, sidecar):
    review = sidecar.closure_reviews[0]
    return build_continuation_binding(
        continuation_id=review.continuation_id,
        state=ContinuationBindingState.CLOSED,
        source_projection_id=review.source_projection_id,
        source_manifest_sha256=review.source_manifest_sha256,
        source_page_num=review.source_page_num,
        source_section_id=review.source_section_id,
        source_anchor_span_ids=review.source_anchor_span_ids,
        adjacent_projection_id=review.adjacent_projection_id,
        adjacent_manifest_sha256=review.adjacent_manifest_sha256,
        adjacent_page_num=review.adjacent_page_num,
        adjacent_section_id=review.adjacent_section_id,
        closure_anchor_span_ids=review.closure_anchor_span_ids,
        closure_basis=review.basis,
    )


def _request(*, sidecar_path: Path = SIDECAR_PATH, binding_state: ContinuationBindingState = ContinuationBindingState.CLOSED):
    first, adjacent = _real_page_builds()
    sidecar = load_continuation_review_sidecar(sidecar_path)
    if binding_state == ContinuationBindingState.CLOSED:
        binding = _closed_binding(first, adjacent, sidecar)
    else:
        record = first.projection.continuation_records[0]
        binding = build_continuation_binding(
            continuation_id=record.continuation_id,
            state=ContinuationBindingState.MANUAL_REVIEW,
            source_projection_id=first.projection.projection_id,
            source_manifest_sha256=first.projection.source_manifest_sha256,
            source_page_num=first.projection.page_num,
            source_section_id=first.section.section_id,
            source_anchor_span_ids=record.anchor_span_ids,
            closure_basis="The reviewed marker remains unresolved and cannot publish an all-items Fact.",
            failure_code=ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE,
        )
    request = build_step2a_request(
        sample_id=SAMPLE_ID,
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        continuation_bindings=(binding,),
        continuation_review_sidecar=sidecar,
        continuation_review_sidecar_path=sidecar_path,
        continuation_repository_root=ROOT,
        model="deterministic-fake-v1",
    )
    return first, adjacent, request, binding


def _table_models(request):
    models = []
    for section_index, section in enumerate(request.sections):
        for table_index, table in enumerate(section.tables):
            models.append(
                Step2AModelFactProposal(
                    proposal_key=f"proposal:complete-table-{section_index}-{table_index}",
                    source_section_ids=(section.section_id,),
                    fact_type=FactType.COMPOSITION,
                    label="完整表格结构",
                    assertion="完整表格结构",
                    primary_anchor_span_ids=tuple(
                        span.anchor_span_id
                        for row in table.rows
                        for cell in row.cells
                        for span in cell.anchor_spans
                    ),
                )
            )
            for requirement in table.atomic_subfact_requirements:
                numeric_claims = tuple(
                    ModelNumericClaim(value=token.value, anchor_span_id=requirement.anchor_span_id)
                    for token in classify_numeric_tokens(requirement.required_assertion)
                    if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                )
                models.append(
                    Step2AModelFactProposal(
                        proposal_key=f"proposal:atomic-{requirement.requirement_id.split(':', 1)[1]}",
                        source_section_ids=(section.section_id,),
                        fact_type=requirement.expected_fact_type,
                        label=requirement.required_assertion,
                        assertion=requirement.required_assertion,
                        primary_anchor_span_ids=(requirement.anchor_span_id,),
                        numeric_claims=numeric_claims,
                    )
                )
    return models


def _all_items_model(fixture: ContinuationLearningGoldenFixture, request, *, omit_last_span: bool = False):
    spans = fixture.target.required_anchor_span_ids[:-1] if omit_last_span else fixture.target.required_anchor_span_ids
    return Step2AModelFactProposal(
        proposal_key=f"proposal:{fixture.target.fact_key}",
        source_section_ids=tuple(section.section_id for section in request.sections),
        fact_type=fixture.target.fact_type,
        label=fixture.target.canonical_title,
        assertion="；".join(fixture.target.required_assertion_terms),
        completeness_mode=CompletenessMode.ALL_ITEMS,
        primary_anchor_span_ids=spans[:4],
        secondary_anchor_span_ids=spans[4:],
    )


def _canonical_response(request, models):
    selected = derive_step2a_anchor_span_selection(request=request, proposals=models)
    tables = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    conditions = derive_step2a_condition_claims(request=request, proposals=tables.proposals)
    return Step2AResponse(request=request.identity, state=Step2ResultState.SUCCEEDED, proposals=conditions.proposals)


def _write_sidecar_variant(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(SIDECAR_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    raw.pop("sidecar_id", None)
    raw["sidecar_id"] = make_continuation_review_sidecar_id(raw)
    path = tmp_path / "continuation-review.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_real_adjacent_pages_close_v11_all_items_gate_under_reviewed_v12_and_align_golden():
    fixture = _load_golden()
    first, adjacent, request, binding = _request()
    auto_request = build_step2a_request(
        sample_id=SAMPLE_ID,
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        continuation_review_sidecar_path=SIDECAR_PATH,
        continuation_repository_root=ROOT,
        model="deterministic-fake-v1",
    )
    response = _canonical_response(request, [*_table_models(request), _all_items_model(fixture, request)])
    report = validate_step2a_response(request=request, response=response)
    alignment = assess_continuation_learning_golden(
        fixture=fixture,
        request=request,
        proposals=response.proposals,
    )
    payload = OpenAICompatibleStep2Transport.model_payload(request)

    assert request.identity.request_version == "step2a_contract_v12_reviewed_continuation_binding"
    assert request.model_input_version == "step2a_model_input_v7_reviewed_continuation_binding"
    assert len(request.continuation_bindings) == 1
    assert request.continuation_bindings[0].binding_id == binding.binding_id
    assert auto_request.continuation_bindings == request.continuation_bindings
    assert auto_request.identity.cache_key == request.identity.cache_key
    assert payload["reviewed_continuation_sidecar"]["marker_dispositions"][1]["disposition"] == "inline_direction_marker"
    assert report.valid is True
    assert report.continuation_audit[0].state == ContinuationAllItemsAuditState.CLOSED
    assert alignment.valid is True
    assert len(alignment.matched_evidence_ids) == 8
    assert len(alignment.matched_anchor_span_ids) == 14


def test_without_reviewed_inline_disposition_real_page_two_remains_fail_closed():
    first, adjacent = _real_page_builds()
    sidecar = load_continuation_review_sidecar(SIDECAR_PATH)
    binding = _closed_binding(first, adjacent, sidecar)
    with pytest.raises(ValueError, match="one binding for every non-inline continuation record"):
        build_step2a_request(
            sample_id=SAMPLE_ID,
            material_profile_id="yijian_gonglu_2026",
            projection=first.projection,
            additional_projections=(adjacent.projection,),
            sections=(first.section, adjacent.section),
            continuation_bindings=(binding,),
            model="deterministic-fake-v1",
        )


def test_missing_page_ambiguous_review_and_stale_manifest_or_span_fail_closed(tmp_path):
    first, adjacent = _real_page_builds()
    sidecar = load_continuation_review_sidecar(SIDECAR_PATH)
    binding = _closed_binding(first, adjacent, sidecar)
    with pytest.raises(ValueError, match="adjacent source projection"):
        build_step2a_request(
            sample_id=SAMPLE_ID,
            material_profile_id="yijian_gonglu_2026",
            projection=first.projection,
            sections=(first.section,),
            continuation_bindings=(binding,),
            continuation_review_sidecar=sidecar,
            continuation_review_sidecar_path=SIDECAR_PATH,
            continuation_repository_root=ROOT,
            model="deterministic-fake-v1",
        )

    def make_unresolved(raw):
        raw["marker_reviews"][0]["disposition"] = ContinuationMarkerDisposition.UNRESOLVED.value
        raw["closure_reviews"] = []

    unresolved_path = _write_sidecar_variant(tmp_path / "unresolved", make_unresolved)
    _, _, unresolved_request, _ = _request(
        sidecar_path=unresolved_path,
        binding_state=ContinuationBindingState.MANUAL_REVIEW,
    )
    fixture = _load_golden()
    unresolved_response = _canonical_response(
        unresolved_request,
        [*_table_models(unresolved_request), _all_items_model(fixture, unresolved_request)],
    )
    unresolved_report = validate_step2a_response(request=unresolved_request, response=unresolved_response)
    assert unresolved_report.valid is False
    assert unresolved_report.continuation_audit[0].state == ContinuationAllItemsAuditState.MANUAL_REVIEW

    def stale_manifest(raw):
        raw["marker_reviews"][1]["source_manifest_sha256"] = "0" * 64

    stale_manifest_path = _write_sidecar_variant(tmp_path / "manifest", stale_manifest)
    with pytest.raises(ValueError, match="marker sidecar binding"):
        _request(sidecar_path=stale_manifest_path)

    def stale_span(raw):
        raw["marker_reviews"][1]["anchor_span_ids"][-1] = "span:000000000000000000000000"

    stale_span_path = _write_sidecar_variant(tmp_path / "span", stale_span)
    with pytest.raises(ValueError, match="marker token spans|marker sidecar binding"):
        _request(sidecar_path=stale_span_path)


def test_missing_closure_anchor_blocks_gate_and_learning_golden_alignment():
    fixture = _load_golden()
    _, _, request, _ = _request()
    response = _canonical_response(
        request,
        [*_table_models(request), _all_items_model(fixture, request, omit_last_span=True)],
    )
    report = validate_step2a_response(request=request, response=response)
    alignment = assess_continuation_learning_golden(
        fixture=fixture,
        request=request,
        proposals=response.proposals,
    )

    assert report.valid is False
    assert report.continuation_audit[0].state == ContinuationAllItemsAuditState.CLOSURE_ANCHOR_MISSING
    assert alignment.valid is False
    assert "required_evidence_missing" in alignment.failure_codes
    assert "required_anchor_span_missing" in alignment.failure_codes
