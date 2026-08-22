"""Phase 2R.0.5 complete-table Fact and Card grouping contract tests.

All model responses in this file use CallableStep2Transport.  They exercise
the persisted V2 adapter but have no network path and never modify Evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import PageEvidence
from examcard.knowledge.schema import FactType
from examcard.knowledge.contracts import (
    CardPresentation,
    CompletenessMode,
    FactSummaryInput,
    ModelNumericClaim,
    NumericTokenClass,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2BCardProposal,
    Step2BResponse,
    Step2ResultState,
    TableSemanticCoverageState,
    build_step2a_request,
    build_step2b_request,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    derive_step2a_table_cell_anchors,
    classify_numeric_tokens,
    validate_step2a_response,
    validate_step2b_response,
)
from examcard.knowledge.llm import CallableStep2Transport, OpenAICompatibleStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar
from examcard.materials.catalog import load_content_policy
import pytest

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
SIDECAR_PATH = ROOT / "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
SOURCE_REFERENCE = "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"


def _built_request():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_bytes())
    sidecar = load_table_repair_sidecar(SIDECAR_PATH)
    built = build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference=SOURCE_REFERENCE,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
        table_repair_sidecar=sidecar,
        table_repair_sidecar_path=SIDECAR_PATH,
        repository_root=ROOT,
    )
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
    )
    target = next(table for table in built.section.tables if table.table_evidence_id == sidecar.table_evidence_id)
    return page, sidecar, built, request, target


def _table_spans(table) -> tuple[str, ...]:
    return tuple(
        span.anchor_span_id
        for row in table.rows
        for cell in row.cells
        for span in cell.anchor_spans
    )


def _complete_table_fact(request, table, *, proposal_key: str) -> Step2AModelFactProposal:
    return Step2AModelFactProposal(
        proposal_key=proposal_key,
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.COMPOSITION,
        label="荷载组合",
        assertion="荷载组合",
        primary_anchor_span_ids=_table_spans(table),
    )


def _all_complete_table_facts(request, *, target_table, target_proposal: Step2AModelFactProposal | None = None):
    compositions = tuple(
        target_proposal
        if table.table_evidence_id == target_table.table_evidence_id and target_proposal is not None
        else _complete_table_fact(request, table, proposal_key=f"proposal:complete-table-{index}")
        for index, table in enumerate(request.sections[0].tables)
    )
    atomic = []
    for table in request.sections[0].tables:
        for requirement in table.atomic_subfact_requirements:
            numeric_claims = ()
            if requirement.expected_fact_type == FactType.THRESHOLD:
                numeric_claims = tuple(
                    ModelNumericClaim(value=token.value, anchor_span_id=requirement.anchor_span_id)
                    for token in classify_numeric_tokens(requirement.required_assertion)
                    if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                )
            atomic.append(
                Step2AModelFactProposal(
                    proposal_key=f"proposal:atomic-{requirement.requirement_id.split(':', 1)[1]}",
                    source_section_ids=(request.sections[0].section_id,),
                    fact_type=requirement.expected_fact_type,
                    label="表格原子规则",
                    assertion=requirement.required_assertion,
                    primary_anchor_span_ids=(requirement.anchor_span_id,),
                    numeric_claims=numeric_claims,
                )
            )
    return (*compositions, *atomic)


def _canonical_response(request, model_proposals) -> Step2AResponse:
    selected = derive_step2a_anchor_span_selection(request=request, proposals=model_proposals)
    tables = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    conditions = derive_step2a_condition_claims(request=request, proposals=tables.proposals)
    return Step2AResponse(
        request=request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=conditions.proposals,
    )


def _summary(*, fact_id: str, fact_type: FactType, selected_table_ids=(), complete_table_ids=()):
    return FactSummaryInput(
        fact_id=fact_id,
        fact_type=fact_type,
        label="荷载组合" if fact_type == FactType.COMPOSITION else "组合行",
        assertion_summary="荷载组合" if fact_type == FactType.COMPOSITION else "组合行",
        completeness_mode=CompletenessMode.ATOMIC,
        must_cover=False,
        selected_table_evidence_ids=selected_table_ids,
        complete_table_evidence_ids=complete_table_ids,
    )


def test_repaired_6x3_table_requires_and_derives_one_complete_composition_fact():
    before = PAGE_PATH.read_bytes()
    _, sidecar, _, request, target = _built_request()
    response = _canonical_response(
        request,
        _all_complete_table_facts(
            request,
            target_table=target,
            target_proposal=_complete_table_fact(request, target, proposal_key="proposal:load-combination-complete"),
        ),
    )
    report = validate_step2a_response(request=request, response=response)

    audit = next(item for item in report.table_semantic_coverage_audit if item.table_evidence_id == sidecar.table_evidence_id)
    fact = next(item for item in response.proposals if item.proposal_key == "proposal:load-combination-complete")
    coverage = fact.complete_table_semantic_coverages[0]
    assert PAGE_PATH.read_bytes() == before
    assert report.valid is True
    assert audit.state == TableSemanticCoverageState.COMPLETE
    assert audit.canonical_proposal_key == fact.proposal_key
    assert len(coverage.row_evidence_ids) == 6
    assert len(coverage.header_cell_evidence_ids) == 5
    assert len(coverage.cell_evidence_ids) == len(sidecar.cells) == 14
    assert set(coverage.cell_anchor_span_ids) == set(_table_spans(target))
    assert fact.selected_table_evidence_ids == (sidecar.table_evidence_id,)


def test_row_level_formula_fact_cannot_substitute_for_the_repaired_complete_table(tmp_path):
    _, sidecar, _, request, target = _built_request()
    row_level = Step2AModelFactProposal(
        proposal_key="proposal:load-combination-row-only",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.FORMULA,
        label="荷载组合行",
        assertion="荷载组合行",
        primary_anchor_span_ids=(target.rows[0].cells[0].anchor_spans[0].anchor_span_id,),
    )
    proposals = _all_complete_table_facts(request, target_table=target, target_proposal=row_level)
    response = _canonical_response(request, proposals)
    report = validate_step2a_response(request=request, response=response)
    audit = next(item for item in report.table_semantic_coverage_audit if item.table_evidence_id == sidecar.table_evidence_id)

    record = Step2OpenAIAdapter(
        storage_root=tmp_path / "fake-step2a",
        transport=CallableStep2Transport(
            lambda **kwargs: TransportCompletion(
                content=json.dumps({"proposals": [proposal.model_dump(mode="json") for proposal in proposals]}, ensure_ascii=False)
            )
        ),
        idempotency_supported=False,
    ).complete_step2a(request)

    assert report.valid is False
    assert audit.state == TableSemanticCoverageState.ROW_LEVEL_SUBSTITUTION
    assert record.response.state == Step2ResultState.FAILED
    assert record.contract_report.table_semantic_coverage_audit == report.table_semantic_coverage_audit


def test_incomplete_composition_fact_fails_closed_instead_of_claiming_complete_grid():
    _, sidecar, _, request, target = _built_request()
    incomplete = _complete_table_fact(request, target, proposal_key="proposal:load-combination-incomplete").model_copy(
        update={"primary_anchor_span_ids": _table_spans(target)[:-1]}
    )
    response = _canonical_response(
        request,
        _all_complete_table_facts(request, target_table=target, target_proposal=incomplete),
    )
    report = validate_step2a_response(request=request, response=response)
    audit = next(item for item in report.table_semantic_coverage_audit if item.table_evidence_id == sidecar.table_evidence_id)

    assert report.valid is False
    assert audit.state == TableSemanticCoverageState.INCOMPLETE_COMPOSITION
    assert audit.canonical_proposal_key is None
    assert audit.candidate_proposal_keys == ("proposal:load-combination-incomplete",)


def test_step2a_model_payload_marks_complete_table_requirement_without_exposing_a_parent_selection_field():
    _, sidecar, _, request, target = _built_request()
    payload = OpenAICompatibleStep2Transport.model_payload(request)
    table = next(item for section in payload["sections"] for item in section["tables"] if item["table_evidence_id"] == sidecar.table_evidence_id)

    assert table["requires_complete_composition"] is True
    assert set(table["complete_cell_anchor_span_ids"]) == set(_table_spans(target))
    assert "primary_evidence_ids" not in json.dumps(payload, ensure_ascii=False)


def test_card_grouping_requires_the_canonical_complete_table_fact_and_rejects_row_substitution(tmp_path):
    _, sidecar, _, _, target = _built_request()
    complete = _summary(
        fact_id="fact:" + "1" * 24,
        fact_type=FactType.COMPOSITION,
        selected_table_ids=(sidecar.table_evidence_id,),
        complete_table_ids=(sidecar.table_evidence_id,),
    )
    row = _summary(
        fact_id="fact:" + "2" * 24,
        fact_type=FactType.FORMULA,
        selected_table_ids=(sidecar.table_evidence_id,),
    )
    valid_request = build_step2b_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        facts=(complete, row),
        model="deterministic-fake-v1",
    )
    valid_response = Step2BResponse(
        request=valid_request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=(
            Step2BCardProposal(
                proposal_key="card-proposal:load-combination-table",
                title="荷载组合",
                primary_fact_ids=(complete.fact_id,),
                presentation=CardPresentation.TABLE,
                split_reason="完整表格必须保留全部行列和单元格关系。",
            ),
            Step2BCardProposal(
                proposal_key="card-proposal:load-combination-row",
                title="组合行",
                primary_fact_ids=(row.fact_id,),
                presentation=CardPresentation.FORMULA,
                split_reason="行级公式作为补充，不替代整表结构。",
            ),
        ),
    )
    invalid_response = Step2BResponse(
        request=valid_request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=(
            Step2BCardProposal(
                proposal_key="card-proposal:invalid-table",
                title="荷载组合",
                primary_fact_ids=(row.fact_id,),
                presentation=CardPresentation.TABLE,
                split_reason="错误地使用行级内容代替完整结构化表。",
            ),
            Step2BCardProposal(
                proposal_key="card-proposal:invalid-complete",
                title="荷载组合",
                primary_fact_ids=(complete.fact_id,),
                presentation=CardPresentation.FORMULA,
                split_reason="错误地将完整表格放入非表格呈现中。",
            ),
        ),
    )
    valid_report = validate_step2b_response(request=valid_request, response=valid_response)
    invalid_report = validate_step2b_response(request=valid_request, response=invalid_response)
    record = Step2OpenAIAdapter(
        storage_root=tmp_path / "fake-step2b",
        transport=CallableStep2Transport(
            lambda **kwargs: TransportCompletion(
                content=json.dumps({"proposals": [item.model_dump(mode="json") for item in invalid_response.proposals]}, ensure_ascii=False)
            )
        ),
        idempotency_supported=False,
    ).complete_step2b(valid_request)

    assert target.row_count == 6
    assert valid_report.valid is True
    assert invalid_report.valid is False
    assert any("table cards require a canonical complete-table Fact" in error for error in invalid_report.errors)
    assert any("canonical complete-table Facts must use table presentation" in error for error in invalid_report.errors)
    assert record.response.state == Step2ResultState.FAILED
    assert record.contract_report.errors == invalid_report.errors
