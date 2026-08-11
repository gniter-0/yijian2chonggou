"""Phase 2R.0.4 Learning Golden and reviewed table-repair regression tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import PageEvidence
from examcard.knowledge.learning_fixture import (
    EvidenceAnchorSelector,
    LearningReview,
    LearningReviewState,
    compare_learning_golden_page,
    load_learning_golden_fixture,
    resolve_reviewed_heading_only_allowance_span_ids,
)
from examcard.knowledge.schema import FactType
from examcard.knowledge.contracts import (
    CardPresentation,
    ModelNumericClaim,
    NumericTokenClass,
    Step2AFactProposal,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2AResultPayload,
    Step2BResultPayload,
    Step2ResultState,
    build_step2a_request,
    classify_numeric_tokens,
    fact_summary_from_proposal,
)
from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.knowledge.continuation.repair_sidecar import (
    TableRepairReviewSidecar,
    load_table_repair_sidecar,
)
from examcard.materials.catalog import load_content_policy

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1",
    _REPO_ROOT / "data/semantic_llm_v2/gonglu_phase2r0323_anchor_span_live",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
SIDECAR_PATH = ROOT / "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
FIXTURE_PATH = ROOT / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r0_draft.yaml"
OLD_STEP2A = ROOT / "data/semantic_llm_v2/gonglu_phase2r0323_anchor_span_live/responses/step2a-request_0036ece68cda82a30e0b92fccf9ba799309aab1a8ad2becbf7d1a2281015daee.json"
OLD_STEP2B = ROOT / "data/semantic_llm_v2/gonglu_phase2r0323_anchor_span_live/responses/step2b-request_c37fdbba2faa483496d72d755ccf8e21ea70402d7c9075e470e815d8e0258d45.json"
SOURCE_REFERENCE = "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"


def _page() -> PageEvidence:
    return PageEvidence.model_validate_json(PAGE_PATH.read_bytes())


def _build(*, sidecar: TableRepairReviewSidecar | None = None):
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=_page(),
        source_manifest_path=PAGE_PATH,
        source_manifest_reference=SOURCE_REFERENCE,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
        table_repair_sidecar=sidecar,
        table_repair_sidecar_path=SIDECAR_PATH if sidecar is not None else None,
        repository_root=ROOT,
    )


def _table_atomic_rule_proposals(request):
    proposals = []
    for section in request.sections:
        for table in section.tables:
            for requirement in table.atomic_subfact_requirements:
                numeric_claims = ()
                if requirement.expected_fact_type == FactType.THRESHOLD:
                    numeric_claims = tuple(
                        ModelNumericClaim(value=token.value, anchor_span_id=requirement.anchor_span_id)
                        for token in classify_numeric_tokens(requirement.required_assertion)
                        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                    )
                proposals.append(
                    Step2AModelFactProposal(
                        proposal_key=f"proposal:atomic-{requirement.requirement_id.split(':', 1)[1]}",
                        source_section_ids=(section.section_id,),
                        fact_type=requirement.expected_fact_type,
                        # Mirror the planner requirement instead of giving every
                        # independent table rule the same non-teaching title.
                        label=requirement.required_assertion,
                        assertion=requirement.required_assertion,
                        primary_anchor_span_ids=(requirement.anchor_span_id,),
                        numeric_claims=numeric_claims,
                    )
                )
    return tuple(proposals)


def test_reviewed_table_repair_is_exactly_bound_and_admits_the_6x3_grid_without_mutation():
    before = PAGE_PATH.read_bytes()
    sidecar = load_table_repair_sidecar(SIDECAR_PATH)
    built = _build(sidecar=sidecar)

    assert PAGE_PATH.read_bytes() == before
    assert len(sidecar.rows) == 6
    assert len(sidecar.cells) == 14
    repaired = next(item for item in built.projection.table_grid_integrity if item.table_evidence_id == sidecar.table_evidence_id)
    assert repaired.status.value == "passed_with_reviewed_repair"
    assert repaired.issue_codes == ("grid_coverage_hole",)
    assert repaired.repair_id == sidecar.repair_id
    assert built.projection.reviewed_table_repair_ids == (sidecar.repair_id,)
    table = next(item for item in built.section.tables if item.table_evidence_id == sidecar.table_evidence_id)
    assert (table.row_count, table.column_count, len(table.cell_ids)) == (6, 3, 14)
    spans = {cell.evidence_id: (cell.row_span, cell.col_span) for row in table.rows for cell in row.cells}
    assert spans["ev:fc09e3c8e912db4b82494820"] == (1, 3)
    assert spans["ev:782cd39ec20a92a9bbdb35a5"] == (2, 1)
    assert spans["ev:c9f32bad3bc0f45ac748a27f"] == (1, 2)


def test_draft_table_repair_cannot_generate_repaired_structured_input():
    reviewed = load_table_repair_sidecar(SIDECAR_PATH)
    draft = TableRepairReviewSidecar.model_validate(
        reviewed.model_dump(mode="json") | {"state": "draft", "reviewer": None, "reviewed_at_utc": None}
    )
    built = _build(sidecar=draft)

    assert all(table.table_evidence_id != reviewed.table_evidence_id for table in built.section.tables)
    record = next(item for item in built.projection.table_grid_integrity if item.table_evidence_id == reviewed.table_evidence_id)
    assert record.status.value == "manual_review"
    assert not built.projection.reviewed_table_repair_ids
    assert built.projection.table_repair_sidecar_sha256 is None


def test_reviewed_table_repair_fails_closed_on_cell_hash_tampering():
    sidecar = load_table_repair_sidecar(SIDECAR_PATH)
    bad_cell = sidecar.cells[0].model_copy(update={"canonical_text_sha256": "0" * 64})
    tampered = sidecar.model_copy(update={"cells": (bad_cell, *sidecar.cells[1:])})
    with pytest.raises(ValueError, match="text hash or bbox"):
        _build(sidecar=tampered)


def test_projection_marks_only_the_heading_like_paragraph_as_context_only():
    built = _build(sidecar=load_table_repair_sidecar(SIDECAR_PATH))
    assert built.projection.context_only_evidence_ids == ("ev:efce0bdb6145b51513d64b53",)
    assert built.section.context_only_evidence_ids == built.projection.context_only_evidence_ids


def test_heading_only_fact_gate_blocks_unless_request_has_explicit_allowance(tmp_path):
    built = _build(sidecar=load_table_repair_sidecar(SIDECAR_PATH))
    context_span = next(
        span for span in built.projection.anchor_spans if span.parent_evidence_id == "ev:efce0bdb6145b51513d64b53"
    )

    def execute(request, storage):
        proposal = Step2AModelFactProposal(
            proposal_key="proposal:heading-context-only",
            source_section_ids=(request.sections[0].section_id,),
            fact_type=FactType.DEFINITION,
            label="常用模板、支架设计与施工",
            assertion="常用模板、支架设计与施工",
            primary_anchor_span_ids=(context_span.anchor_span_id,),
        )
        table_proposals = tuple(
            Step2AModelFactProposal(
                proposal_key=f"proposal:complete-table-{section_index}-{table_index}",
                source_section_ids=(section.section_id,),
                fact_type=FactType.COMPOSITION,
                label="模板",
                assertion="模板",
                primary_anchor_span_ids=tuple(
                    span.anchor_span_id
                    for row in table.rows
                    for cell in row.cells
                    for span in cell.anchor_spans
                ),
            )
            for section_index, section in enumerate(request.sections)
            for table_index, table in enumerate(section.tables)
        )
        transport = CallableStep2Transport(
            lambda **kwargs: TransportCompletion(
                content=json.dumps(
                    Step2AResultPayload(
                        proposals=(proposal, *table_proposals, *_table_atomic_rule_proposals(request))
                    ).model_dump(mode="json"),
                    ensure_ascii=False,
                )
            )
        )
        return Step2OpenAIAdapter(storage_root=storage, transport=transport, idempotency_supported=False).complete_step2a(request)

    blocked_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
    )
    blocked = execute(blocked_request, tmp_path / "blocked")
    assert blocked.contract_report.valid is True
    assert blocked.teaching_review.gate.publishable is False
    assert blocked.teaching_review.gate.heading_only_fact_failures == 1

    allowed_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        heading_only_allowed_anchor_span_ids=(context_span.anchor_span_id,),
    )
    allowed = execute(allowed_request, tmp_path / "allowed")
    assert allowed.teaching_review.gate.publishable is True
    assert allowed_request.identity.cache_key != blocked_request.identity.cache_key


def test_heading_only_allowances_are_derived_only_from_reviewed_golden_targets():
    built = _build(sidecar=load_table_repair_sidecar(SIDECAR_PATH))
    fixture = load_learning_golden_fixture(FIXTURE_PATH)
    context_text = "P117~1213.2.1常用模板、支架设计与施工★★★"
    sample = next(item for item in fixture.samples if item.sample_id == "gonglu_24_table_formula")
    fact = sample.facts[0].model_copy(
        update={
            "primary_anchors": (EvidenceAnchorSelector(text=context_text),),
            "allow_heading_only": True,
            "review": LearningReview(
                state=LearningReviewState.REVIEWED,
                reviewer="test_reviewer",
                reviewed_at_utc="2026-07-27T00:00:00Z",
                basis="explicit golden assertion permits this heading-only target",
            ),
        }
    )
    reviewed_sample = sample.model_copy(update={"facts": (fact, *sample.facts[1:])})
    reviewed_fixture = fixture.model_copy(
        update={"samples": tuple(reviewed_sample if item.sample_id == sample.sample_id else item for item in fixture.samples)}
    )

    allowances = resolve_reviewed_heading_only_allowance_span_ids(
        fixture=reviewed_fixture,
        sample_id=sample.sample_id,
        page=_page(),
        sections=(built.section,),
    )
    assert allowances == (next(span for span in built.projection.anchor_spans if span.parent_evidence_id == "ev:efce0bdb6145b51513d64b53").anchor_span_id,)
    assert resolve_reviewed_heading_only_allowance_span_ids(
        fixture=fixture,
        sample_id=sample.sample_id,
        page=_page(),
        sections=(built.section,),
    ) == ()


def test_step2b_presentation_is_a_strict_renderer_enum():
    valid = {
        "proposal_key": "card-proposal:typed",
        "title": "模板荷载组合",
        "primary_fact_ids": ["fact:" + "1" * 24],
        "presentation": "table",
        "split_reason": "完整保留表格行列关系。",
    }
    payload = Step2BResultPayload.model_validate({"proposals": [valid]})
    assert payload.proposals[0].presentation == CardPresentation.TABLE

    with pytest.raises(ValidationError):
        Step2BResultPayload.model_validate(
            {"proposals": [valid | {"presentation": "这里错误地放入了卡片正文"}]}
        )


def test_previous_live_9_fact_7_card_run_has_reproducible_learning_golden_drift():
    built = _build()
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
    )
    old_a = json.loads(OLD_STEP2A.read_text(encoding="utf-8"))["response"]
    proposals = tuple(Step2AFactProposal.model_validate(item) for item in old_a["proposals"])
    response_a = Step2AResponse(request=request.identity, state=Step2ResultState.SUCCEEDED, proposals=proposals)
    evidence_by_id = {unit.evidence_id: unit for unit in _page().evidence_units}
    summaries = {
        proposal.proposal_key: fact_summary_from_proposal(proposal=proposal, evidence_by_id=evidence_by_id)
        for proposal in proposals
    }
    old_b = json.loads(OLD_STEP2B.read_text(encoding="utf-8"))["response"]
    historical_cards = tuple(SimpleNamespace(**item) for item in old_b["proposals"])
    report = compare_learning_golden_page(
        fixture=load_learning_golden_fixture(FIXTURE_PATH),
        page=_page(),
        request=request,
        step2a_response=response_a,
        fact_ids_by_proposal_key=summaries,
        step2b_response=SimpleNamespace(proposals=historical_cards),
    )

    assert report.missing_fact_count == 1
    assert report.extra_fact_count == 9
    assert report.missing_card_count == 1
    assert report.extra_card_count == 7
    assert report.presentation_drift_count == 7
    assert report.untyped_presentation_proposal_keys == tuple(card.proposal_key for card in historical_cards)
