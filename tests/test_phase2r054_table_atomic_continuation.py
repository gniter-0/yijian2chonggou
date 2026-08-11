"""Phase 2R.0.5.4 offline regressions for table rules and continuation safety."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import PageEvidence
from examcard.knowledge.learning_fixture import (
    LearningDispositionAuditState,
    build_reviewed_canonical_title_projections,
    compare_learning_golden_page,
    load_learning_golden_fixture,
)
from examcard.knowledge.schema import CompletenessMode, FactType
from examcard.knowledge.contracts import (
    ModelNumericClaim,
    NumericTokenClass,
    Step2AFactProposal,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2ResultState,
    TableAtomicSubfactCoverageState,
    classify_numeric_tokens,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    derive_step2a_table_cell_anchors,
    fact_summary_from_proposal,
    validate_step2a_response,
    build_step2a_request,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport, Step2AExecutionRecord
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar
from examcard.materials.catalog import load_content_policy


PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
SIDECAR_PATH = ROOT / "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
FIXTURE_PATH = ROOT / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r053_page1_reviewed.yaml"
RECORD_PATH = ROOT / (
    "data/semantic_llm_v2/gonglu_phase2r052_manual_reissue_live/responses/"
    "step2a-request_e163c47bd18556045a06738a3fb435e88e0b22bed1a50e07c240c5f501f29140.json"
)


def _built_request():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_bytes())
    sidecar = load_table_repair_sidecar(SIDECAR_PATH)
    built = build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference="data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json",
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
    return page, built, request


def _complete_table_models(request):
    models = []
    for table_index, table in enumerate(request.sections[0].tables):
        models.append(
            Step2AModelFactProposal(
                proposal_key=f"proposal:complete-table-{table_index}",
                source_section_ids=(request.sections[0].section_id,),
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
    return models


def _atomic_rule_models(request, *, omit_requirement_ids=()):
    omitted = set(omit_requirement_ids)
    models = []
    for table in request.sections[0].tables:
        for requirement in table.atomic_subfact_requirements:
            if requirement.requirement_id in omitted:
                continue
            numeric_claims = ()
            if requirement.expected_fact_type == FactType.THRESHOLD:
                numeric_claims = tuple(
                    ModelNumericClaim(value=token.value, anchor_span_id=requirement.anchor_span_id)
                    for token in classify_numeric_tokens(requirement.required_assertion)
                    if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                )
            models.append(
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
    return models


def _canonical_response(request, models):
    selected = derive_step2a_anchor_span_selection(request=request, proposals=models)
    tables = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    conditions = derive_step2a_condition_claims(request=request, proposals=tables.proposals)
    return Step2AResponse(
        request=request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=conditions.proposals,
    )


def test_table_projection_derives_five_atomic_rules_and_exposes_them_to_the_fake_model():
    _, built, request = _built_request()
    rules = [rule for table in built.section.tables for rule in table.atomic_subfact_requirements]
    payload = OpenAICompatibleStep2Transport.model_payload(request)
    model_rules = [rule for section in payload["sections"] for table in section["tables"] for rule in table["atomic_subfact_requirements"]]

    assert request.identity.request_version == "step2a_contract_v10_table_atomic_continuation"
    assert len(rules) == 5
    assert {rule.expected_fact_type for rule in rules} == {FactType.CONDITION, FactType.THRESHOLD}
    assert len(model_rules) == len(rules)
    assert all(set(rule) == {"requirement_id", "expected_fact_type", "required_anchor_span_id", "required_assertion"} for rule in model_rules)


def test_atomic_rule_gate_rejects_missing_rule_but_accepts_complete_composition_plus_all_rules():
    _, built, request = _built_request()
    rules = [rule for table in built.section.tables for rule in table.atomic_subfact_requirements]
    missing_response = _canonical_response(
        request,
        [*_complete_table_models(request), *_atomic_rule_models(request, omit_requirement_ids=(rules[0].requirement_id,))],
    )
    complete_response = _canonical_response(request, [*_complete_table_models(request), *_atomic_rule_models(request)])
    missing_report = validate_step2a_response(request=request, response=missing_response)
    complete_report = validate_step2a_response(request=request, response=complete_response)

    assert missing_report.valid is False
    assert any(
        item.requirement_id == rules[0].requirement_id
        and item.state == TableAtomicSubfactCoverageState.COMPOSITION_SUBSTITUTION
        for item in missing_report.table_atomic_subfact_audit
    )
    assert complete_report.valid is True
    assert all(item.state == TableAtomicSubfactCoverageState.COMPLETE for item in complete_report.table_atomic_subfact_audit)


def test_explicit_page_end_marker_blocks_all_items_fact_and_requires_adjacent_source_page():
    _, built, request = _built_request()
    record = built.projection.continuation_records[0]
    model = Step2AModelFactProposal(
        proposal_key="proposal:partial-load-enumeration",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.ENUMERATION,
        label="模板支架设计荷载",
        assertion="模板支架设计荷载",
        completeness_mode=CompletenessMode.ALL_ITEMS,
        primary_anchor_span_ids=(record.anchor_span_ids[0],),
    )
    response = _canonical_response(request, [*_complete_table_models(request), *_atomic_rule_models(request), model])
    report = validate_step2a_response(request=request, response=response)

    assert record.state.value == "partial_continuation"
    assert record.required_adjacent_page_num == 2
    assert "↓" in "".join(
        span.canonical_text for span in built.projection.anchor_spans if span.anchor_span_id in record.anchor_span_ids
    )
    assert any("all_items Fact is blocked" in error for error in report.errors)


def test_new_request_treats_historical_dispositions_as_out_of_scope_and_projects_reviewed_canonical_titles():
    page, _, current_request = _built_request()
    persisted = Step2AExecutionRecord.model_validate_json(RECORD_PATH.read_bytes())
    fixture = load_learning_golden_fixture(FIXTURE_PATH)
    evidence_by_id = {unit.evidence_id: unit for unit in page.evidence_units}
    summaries = {
        proposal.proposal_key: fact_summary_from_proposal(proposal=proposal, evidence_by_id=evidence_by_id)
        for proposal in persisted.response.proposals
    }
    old_drift = compare_learning_golden_page(
        fixture=fixture,
        page=page,
        request=persisted.request,
        step2a_response=persisted.response,
        fact_ids_by_proposal_key=summaries,
        step2b_response=None,
        source_response_sha256=persisted.raw_response_sha256,
    )
    new_drift = compare_learning_golden_page(
        fixture=fixture,
        page=page,
        request=current_request,
        step2a_response=persisted.response,
        fact_ids_by_proposal_key=summaries,
        step2b_response=None,
        source_response_sha256=persisted.raw_response_sha256,
    )
    title_projections = build_reviewed_canonical_title_projections(
        fixture=fixture,
        request=persisted.request,
        drift=old_drift,
        step2a_response=persisted.response,
    )
    table_proposal = next(item for item in persisted.response.proposals if item.proposal_key == "proposal:table_3_1_2_load_combination_composition")
    summary = fact_summary_from_proposal(
        proposal=table_proposal,
        evidence_by_id=evidence_by_id,
        reviewed_title_projection=title_projections[table_proposal.proposal_key],
    )

    assert new_drift.disposition_mismatch_count == 0
    assert {audit.state for audit in new_drift.proposal_disposition_audits} == {LearningDispositionAuditState.OUT_OF_SCOPE}
    assert summary.label == "模板、支架和拱架荷载组合"
    assert summary.reviewed_title_projection is not None
    assert summary.reviewed_title_projection.source_label == table_proposal.label
