from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from examcard.knowledge.contracts.phase2r14_v25_live import build_live_sample_contexts
from examcard.knowledge.contracts.phase2r142_v25_projection import (
    V25GoldenTargetState,
    V25ProjectionDisposition,
    assess_projected_step2b_bridge,
    build_v25_response_golden_projection,
    freeze_phase2r141_run1,
)
from examcard.knowledge.contracts.v25_generic_contracts import (
    V25Step2AModelFactProposal,
    V25Step2AResultPayload,
    build_v25_source_fake_response,
)
from examcard.knowledge.schema import FactType


ROOT = Path(__file__).resolve().parents[1]
# T5 金样镜像守护：模块级 next(glob) 会在收集期执行，镜像缺席时必须先整模块跳过。
_RECORDS_DIR = ROOT / "data/semantic_llm_v2/gonglu_phase2r141_v25b_recovery_live_run1/records"
if not _RECORDS_DIR.is_dir() or not any(_RECORDS_DIR.glob("*.json")):
    import pytest

    pytest.skip(
        "金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
        allow_module_level=True,
    )
FROZEN_RECORD = next(_RECORDS_DIR.glob("*.json"))
FIXTURE_PATH = ROOT / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r12_v25_reviewed.yaml"
REISSUE_REASON = "manual-reissue-phase2r141-v25b-relay-recovery-20260729-run1"


def _context(sample_id: str):
    return next(
        item
        for item in build_live_sample_contexts(
            repository_root=ROOT,
            model="gpt-5.5",
            relay_base_url="https://2chat.cc/v1",
            manual_reissue_reason=REISSUE_REASON,
        )
        if item.sample_id == sample_id
    )


def _response() -> tuple[V25Step2AResultPayload, str]:
    record = json.loads(FROZEN_RECORD.read_text(encoding="utf-8"))
    return V25Step2AResultPayload.model_validate(record["response"]), record["response_sha256"]


def _bundle(*, context=None, response=None, response_sha256=None, required_continuation_binding_ids=None):
    context = context or _context("gonglu_24_table_formula")
    source_response, stored_hash = _response()
    return build_v25_response_golden_projection(
        context=context,
        response=response or source_response,
        response_sha256=response_sha256 or stored_hash,
        fixture_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        frozen_phase2r141_manifest_sha256=__import__("examcard.knowledge.contracts", fromlist=["canonical_step2_sha256"]).canonical_step2_sha256(
            freeze_phase2r141_run1(repository_root=ROOT)
        ),
        required_continuation_binding_ids=required_continuation_binding_ids,
    )


def _proposal_index(bundle, fact_key: str) -> int:
    audit = next(item for item in bundle.target_audits if item.fact_key == fact_key)
    assert audit.state == V25GoldenTargetState.PROJECTED
    return audit.candidate_proposal_indexes[0]


def _replace_proposal(response, index: int, **updates):
    proposals = list(response.proposals)
    proposals[index] = proposals[index].model_copy(update=updates)
    return response.model_copy(update={"proposals": tuple(proposals)})


def test_phase2r142_freezes_real_run_and_projects_titles_without_model_label_equality():
    frozen = freeze_phase2r141_run1(repository_root=ROOT)
    assert any("/probes/" in f"/{path}" for path in frozen)
    assert any("/records/" in f"/{path}" for path in frozen)
    bundle = _bundle()
    states = {item.fact_key: item.state for item in bundle.target_audits}
    assert bundle.source_gate.valid is True
    assert bundle.valid is False
    assert states["formwork.load-combination-table"] == V25GoldenTargetState.PROJECTED
    assert states["formwork.lifting-ring-material"] == V25GoldenTargetState.MISSING
    assert states["formwork.preload"] == V25GoldenTargetState.MISSING
    assert states["formwork.arch-removal"] == V25GoldenTargetState.MISSING
    load_projection = next(item for item in bundle.projections if item.fact_key == "formwork.load-combination-table")
    assert load_projection.model_label != load_projection.canonical_title
    assert bundle.teaching_title_audit.model_label_failure_count == 4
    assert bundle.teaching_title_audit.card_facing_title_gate_valid is True
    assert bundle.step2b_bridge_audit.eligible is False
    assert bundle.step2b_bridge_audit.missing_fact_keys == (
        "formwork.lifting-ring-material",
        "formwork.preload",
        "formwork.arch-removal",
    )
    assert sum(item.disposition == V25ProjectionDisposition.PROJECTED for item in bundle.proposal_audits) == 8
    assert sum(item.disposition == V25ProjectionDisposition.OUT_OF_SCOPE for item in bundle.proposal_audits) == 28
    assert sum(item.disposition == V25ProjectionDisposition.MANUAL_REVIEW for item in bundle.proposal_audits) == 6


def test_phase2r142_title_alias_does_not_change_source_match_or_canonical_projection():
    response, response_sha256 = _response()
    baseline = _bundle(response=response, response_sha256=response_sha256)
    index = _proposal_index(baseline, "formwork.load-combination-table")
    changed = _replace_proposal(response, index, label="表3.1-2模板、支架和拱架设计计算的荷载组合")
    bundle = _bundle(response=changed, response_sha256="a" * 64)
    projection = next(item for item in bundle.projections if item.fact_key == "formwork.load-combination-table")
    assert projection.canonical_title == "模板、支架和拱架荷载组合"
    assert projection.model_label == "表3.1-2模板、支架和拱架设计计算的荷载组合"


def test_phase2r142_wrong_type_anchor_and_incomplete_table_fail_closed():
    context = _context("gonglu_24_table_formula")
    response, _ = _response()
    baseline = _bundle(context=context, response=response, response_sha256="b" * 64)
    speed_index = _proposal_index(baseline, "formwork.climb-speed")
    wrong_type = _replace_proposal(response, speed_index, fact_type=FactType.DEFINITION)
    assert next(item for item in _bundle(context=context, response=wrong_type, response_sha256="c" * 64).target_audits if item.fact_key == "formwork.climb-speed").state == V25GoldenTargetState.MISSING

    load_index = _proposal_index(baseline, "formwork.load-combination-table")
    load = response.proposals[load_index]
    wrong_anchor = _replace_proposal(response, load_index, primary_anchor_span_ids=(load.primary_anchor_span_ids[0],))
    wrong_anchor_bundle = _bundle(context=context, response=wrong_anchor, response_sha256="d" * 64)
    assert wrong_anchor_bundle.source_gate.valid is False
    assert next(item for item in wrong_anchor_bundle.target_audits if item.fact_key == "formwork.load-combination-table").state == V25GoldenTargetState.MISSING

    incomplete = _replace_proposal(response, load_index, primary_anchor_span_ids=load.primary_anchor_span_ids[:-1])
    incomplete_bundle = _bundle(context=context, response=incomplete, response_sha256="e" * 64)
    assert incomplete_bundle.source_gate.valid is False
    assert next(item for item in incomplete_bundle.target_audits if item.fact_key == "formwork.load-combination-table").state == V25GoldenTargetState.MISSING


def test_phase2r142_missing_continuation_binding_is_fail_closed():
    context = _context("gonglu_24_table_formula")
    expected = tuple(item.binding_id for item in context.step2a_request.reviewed_continuation_bindings)
    unbound = replace(context, step2a_request=context.step2a_request.model_copy(update={"reviewed_continuation_bindings": ()}))
    bundle = _bundle(
        context=unbound,
        response_sha256="f" * 64,
        required_continuation_binding_ids=expected,
    )
    assert bundle.continuation_binding_valid is False
    assert bundle.valid is False


def test_phase2r142_semantic_diagram_bridge_and_step2b_exactly_once_preflight():
    diagram_context = _context("gonglu_41_diagram_mixed")
    response = build_v25_source_fake_response(request=diagram_context.step2a_request)
    bundle = _bundle(context=diagram_context, response=response, response_sha256="1" * 64)
    diagram = next(item for item in bundle.projections if item.fact_key == "tunnel.composite-lining-section")
    assert diagram.semantic_diagram_bridge_id is not None
    assert diagram.model_label is None
    bridge = assess_projected_step2b_bridge(context=diagram_context, facts_by_key=diagram_context.facts_by_key)
    assert bridge.eligible is True
    reduced = dict(diagram_context.facts_by_key)
    reduced.pop("tunnel.span-classification")
    blocked = assess_projected_step2b_bridge(context=diagram_context, facts_by_key=reduced)
    assert blocked.eligible is False
    assert blocked.exactly_once is False


def test_phase2r142_source_fake_records_duplicate_subfact_and_schema_is_strict():
    context = _context("gonglu_24_table_formula")
    fake = build_v25_source_fake_response(request=context.step2a_request)
    table = next(
        table
        for section in context.step2a_request.sections
        for table in section.tables
        if table.table_evidence_id == context.facts_by_key["formwork.general-requirements-table"].source_table_ids[0]
    )
    reviewed_atomic = {
        anchor_id
        for fact_key, summary in context.facts_by_key.items()
        if summary.fact_type not in {FactType.COMPOSITION, FactType.DIAGRAM_RELATION}
        for anchor_id in summary.primary_anchor_span_ids
    }
    unrelated_anchor = next(
        span
        for row in table.rows
        for cell in row.cells
        for span in cell.anchor_spans
        if span.anchor_span_id not in reviewed_atomic
    )
    duplicate = V25Step2AModelFactProposal(
        fact_type=FactType.CONDITION,
        label="同表非目标原子规则",
        assertion=unrelated_anchor.canonical_text,
        primary_anchor_span_ids=(unrelated_anchor.anchor_span_id,),
    )
    bundle = _bundle(
        context=context,
        response=fake.model_copy(update={"proposals": (*fake.proposals, duplicate)}),
        response_sha256="2" * 64,
    )
    assert any(item.disposition == V25ProjectionDisposition.DUPLICATE_SUBFACT for item in bundle.proposal_audits)
    schema = type(bundle).model_json_schema()
    assert schema["additionalProperties"] is False
