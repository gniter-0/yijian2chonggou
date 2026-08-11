"""Phase 2R.0.9.8.1 composition numeric-occurrence grounding regressions."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import PageEvidence  # noqa: E402
from examcard.knowledge.schema import FactType  # noqa: E402
from examcard.knowledge.contracts import (  # noqa: E402
    Step2AModelFactProposal,
    Step2AV20ModelFactProposal,
    Step2ANumericClaimDerivationResult,
    Step2ResponseFormat,
    Step2DecoderConfig,
    Step2ResultState,
    TableNumericOccurrenceState,
    build_step2a_request,
    derive_step2a_anchor_span_selection,
    derive_step2a_numeric_claims,
    derive_step2a_table_cell_anchors,
)
from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion  # noqa: E402
from examcard.knowledge.projection import build_step2a_semantic_projection  # noqa: E402
from examcard.knowledge.continuation.integration import (  # noqa: E402
    build_phase2r097_request,
    build_phase2r09_fake_payload,
)
from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar  # noqa: E402
from examcard.materials.catalog import load_content_policy  # noqa: E402


EXPOSED = "span:a812441493c325cc2deb9596"
ITEM_THREE_CLOSURE = "span:d87fbe681f7f5144123448fc"
PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
SIDECAR_PATH = ROOT / "configs/v2/table_repair_sidecars/gonglu_phase0_v1.gonglu_24_p0001_load_combination.reviewed.yaml"
V20_REPORT_PATH = ROOT / "data/semantic_llm_v2/gonglu_phase2r098_v20_live_step2a/phase2r098_v20_live_step2a_report.json"


def _v20_build():
    return build_phase2r097_request(repository_root=ROOT)


def test_persisted_v20_forensic_remains_terminal_read_only_input():
    report = json.loads(V20_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["phase"] == "2R.0.9.8"
    assert report["network_calls"] == 2
    assert report["stop_reason"] == "step2a_grounding_contract_failed"
    assert report["step2b_requests"] == 0
    assert report["source_mutations"] == 0
    assert report["accepted"] is False


def _v20_payload(build):
    return build_phase2r09_fake_payload(request=build.request)


def _stiffness_composition(payload: dict[str, object]) -> dict[str, object]:
    return next(
        proposal
        for proposal in payload["proposals"]
        if proposal["fact_type"] == "composition" and EXPOSED in proposal["primary_anchor_span_ids"]
    )


def _run_v20(tmp_path: Path, mutate=None):
    build = _v20_build()
    payload = _v20_payload(build)
    if mutate is not None:
        mutate(payload)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record = Step2OpenAIAdapter(
        storage_root=tmp_path,
        transport=CallableStep2Transport(
            lambda **kwargs: TransportCompletion(content=raw, provider_request_id="fake-phase2r0981")
        ),
        idempotency_supported=False,
    ).complete_step2a(build.request)
    return build, record


def _repaired_6x3_request():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_bytes())
    sidecar = load_table_repair_sidecar(SIDECAR_PATH)
    built = build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference=PAGE_PATH.relative_to(ROOT).as_posix(),
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
        table_repair_sidecar=sidecar,
        table_repair_sidecar_path=SIDECAR_PATH,
        repository_root=ROOT,
    )
    return build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-phase2r0981",
        relay_base_url="offline://deterministic-fake",
        decoder=Step2DecoderConfig(max_tokens=800, response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT),
    )


def test_complete_stiffness_table_uses_occurrence_provenance_for_repeated_thresholds(tmp_path):
    def mutate(payload):
        _stiffness_composition(payload)["assertion"] = (
            "刚度验算表中外露模板挠度限值为1/400，隐蔽模板挠度限值为1/250，③条跨页续接限值仍为1/400。"
        )

    _, record = _run_v20(tmp_path, mutate)

    assert record.response.state == Step2ResultState.SUCCEEDED
    assert record.contract_report.valid is True
    composition = next(
        proposal
        for proposal in record.response.proposals
        if proposal.fact_type == FactType.COMPOSITION and EXPOSED in proposal.primary_anchor_span_ids
    )
    audits = [
        audit
        for audit in record.table_numeric_occurrence_audit
        if audit.proposal_key == composition.proposal_key
    ]
    assert len(audits) == 3
    assert all(audit.state == TableNumericOccurrenceState.DERIVED_TABLE_CELL_OCCURRENCES for audit in audits)
    assert composition.numeric_claims == ()
    repeated = [audit for audit in audits if len(audit.occurrences) > 1]
    assert len(repeated) == 2
    assert all({item.page_num for item in audit.occurrences} == {2, 3} for audit in repeated)
    assert all(item.cell_evidence_id and item.anchor_span_id and item.source_span for audit in audits for item in audit.occurrences)
    assert all(
        audit.state.value == "derived"
        for audit in record.numeric_claim_derivation_audit
        if audit.proposal_key != composition.proposal_key
    )


def test_repaired_6x3_grid_dimension_is_explicit_bound_table_metadata():
    request = _repaired_6x3_request()
    table = next(
        table
        for section in request.sections
        for table in section.tables
        if (table.row_count, table.column_count) == (6, 3)
    )
    proposal = Step2AModelFactProposal(
        proposal_key="proposal:repaired-6x3-composition",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.COMPOSITION,
        label="荷载组合表",
        assertion="该6×3表格必须保留完整组合关系。",
        primary_anchor_span_ids=tuple(
            span.anchor_span_id for row in table.rows for cell in row.cells for span in cell.anchor_spans
        ),
    )
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(proposal,))
    canonical = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    numeric = derive_step2a_numeric_claims(request=request, proposals=canonical.proposals)

    assert isinstance(numeric, Step2ANumericClaimDerivationResult)
    assert numeric.proposals[0].numeric_claims == ()
    assert len(numeric.table_occurrence_audits) == 2
    assert all(audit.state == TableNumericOccurrenceState.DERIVED_GRID_METADATA for audit in numeric.table_occurrence_audits)
    assert {audit.grid_metadata.row_count for audit in numeric.table_occurrence_audits} == {6}
    assert {audit.grid_metadata.column_count for audit in numeric.table_occurrence_audits} == {3}
    assert all(audit.grid_metadata.cell_anchor_span_ids == canonical.proposals[0].complete_table_semantic_coverages[0].cell_anchor_span_ids for audit in numeric.table_occurrence_audits)


def test_composition_invented_numeric_value_remains_fail_closed(tmp_path):
    def mutate(payload):
        _stiffness_composition(payload)["assertion"] = "刚度验算表还要求挠度限值为1/999。"

    _, record = _run_v20(tmp_path, mutate)

    assert record.response.state == Step2ResultState.FAILED
    assert record.contract_report.valid is False
    assert any(
        audit.state == TableNumericOccurrenceState.ASSERTION_WITHOUT_TABLE_OCCURRENCE
        for audit in record.contract_report.table_numeric_occurrence_audit
    )
    assert any("table numeric occurrence assertion_without_table_occurrence" in error for error in record.contract_report.errors)


def test_atomic_fact_with_duplicate_selected_thresholds_remains_ambiguous_and_fail_closed():
    build = _v20_build()
    proposal = Step2AV20ModelFactProposal(
        fact_type=FactType.THRESHOLD,
        label="外露模板限值",
        assertion="挠度不超过模板构件跨度的1/400。",
        primary_anchor_span_ids=(EXPOSED, ITEM_THREE_CLOSURE),
    )
    selected = derive_step2a_anchor_span_selection(request=build.request, proposals=(proposal,))
    canonical = derive_step2a_table_cell_anchors(request=build.request, proposals=selected.proposals)
    numeric = derive_step2a_numeric_claims(request=build.request, proposals=canonical.proposals)

    assert not numeric.table_occurrence_audits
    assert numeric.proposals[0].numeric_claims == ()
    assert numeric.audits[0].state.value == "ambiguous_evidence"


def test_cross_page_atomic_threshold_with_wrong_closure_anchor_still_fails_coverage(tmp_path):
    def mutate(payload):
        item_three = next(
            proposal
            for proposal in payload["proposals"]
            if proposal["fact_type"] == "threshold" and ITEM_THREE_CLOSURE in proposal["primary_anchor_span_ids"]
        )
        item_three["primary_anchor_span_ids"] = [
            item_three["primary_anchor_span_ids"][0],
            EXPOSED,
        ]

    _, record = _run_v20(tmp_path, mutate)

    assert record.response.state == Step2ResultState.FAILED
    assert record.contract_report.valid is False
    assert any(item.state.value != "complete" for item in record.contract_report.coverage_obligation_audit)
    assert record.coverage_obligation_forensics
