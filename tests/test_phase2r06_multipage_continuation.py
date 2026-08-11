"""Phase 2R.0.6 offline regressions for closed multi-page continuations."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import EvidenceKind, PageEvidence
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
from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar
from examcard.materials.catalog import load_content_policy

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


def _first_page_build():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_bytes())
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference=SOURCE_REFERENCE,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
        table_repair_sidecar=load_table_repair_sidecar(SIDECAR_PATH),
        table_repair_sidecar_path=SIDECAR_PATH,
        repository_root=ROOT,
    )


def _write_adjacent_fixture(tmp_path: Path, *, page_num: int = 2, retain_marker: bool = False) -> Path:
    """Create a distinct page-2 manifest without changing immutable fixture Evidence."""

    payload = json.loads(PAGE_PATH.read_text(encoding="utf-8"))
    payload["page_id"] = f"phase2r06-synthetic-page-{page_num:04d}"
    payload["page_num"] = page_num
    id_map = {
        item["evidence_id"]: f"ev:{index + 0x600000:024x}"
        for index, item in enumerate(payload["evidence_units"])
    }
    for index, item in enumerate(payload["evidence_units"]):
        item["evidence_id"] = id_map[item["evidence_id"]]
        item["fingerprint"] = f"evfp:{index + 0x700000:024x}"
        item["revision_id"] = f"evrev:{index + 0x800000:024x}"
        item["parent_id"] = id_map.get(item.get("parent_id"))
        item["locator"]["page_num"] = page_num
        if item.get("policy_decision") is not None:
            item["policy_decision"]["evidence_id"] = item["evidence_id"]
        if not retain_marker:
            item["raw_text"] = item["raw_text"].replace("↓", "")
            item["canonical_text"] = item["canonical_text"].replace("↓", "")
    for conflict in payload.get("source_conflicts", []):
        conflict["locator"]["page_num"] = page_num
    path = tmp_path / f"page_{page_num:04d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _adjacent_page_build(tmp_path: Path, *, page_num: int = 2, retain_marker: bool = False):
    path = _write_adjacent_fixture(tmp_path, page_num=page_num, retain_marker=retain_marker)
    page = PageEvidence.model_validate_json(path.read_bytes())
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=path,
        source_manifest_reference=f"tests/fixtures/phase2r06/page_{page_num:04d}.json",
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )


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


def _canonical_response(request, models):
    selected = derive_step2a_anchor_span_selection(request=request, proposals=models)
    tables = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    conditions = derive_step2a_condition_claims(request=request, proposals=tables.proposals)
    return Step2AResponse(request=request.identity, state=Step2ResultState.SUCCEEDED, proposals=conditions.proposals)


def _closed_request(tmp_path: Path):
    first = _first_page_build()
    adjacent = _adjacent_page_build(tmp_path)
    record = first.projection.continuation_records[0]
    closure_span = next(
        span
        for span in adjacent.projection.anchor_spans
        if span.parent_evidence_kind == EvidenceKind.LIST_ITEM
    )
    binding = build_continuation_binding(
        continuation_id=record.continuation_id,
        state=ContinuationBindingState.CLOSED,
        source_projection_id=first.projection.projection_id,
        source_manifest_sha256=first.projection.source_manifest_sha256,
        source_page_num=first.projection.page_num,
        source_section_id=first.section.section_id,
        source_anchor_span_ids=record.anchor_span_ids,
        adjacent_projection_id=adjacent.projection.projection_id,
        adjacent_manifest_sha256=adjacent.projection.source_manifest_sha256,
        adjacent_page_num=adjacent.projection.page_num,
        adjacent_section_id=adjacent.section.section_id,
        closure_anchor_span_ids=(closure_span.anchor_span_id,),
        closure_basis="The adjacent page is source-bound, has no later continuation marker, and supplies the reviewed list closure span.",
    )
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        continuation_bindings=(binding,),
        model="deterministic-fake-v1",
    )
    return first, adjacent, request, binding


def _all_items_model(first, adjacent, binding):
    source_spans = tuple(first.projection.continuation_records[0].anchor_span_ids)
    closure_spans = tuple(span_id for span_id in binding.closure_anchor_span_ids if span_id not in source_spans)
    return Step2AModelFactProposal(
        proposal_key="proposal:closed-load-enumeration",
        source_section_ids=(first.section.section_id, adjacent.section.section_id),
        fact_type=FactType.ENUMERATION,
        label="模板支架设计荷载",
        assertion="相邻页面的续接荷载条目。",
        completeness_mode=CompletenessMode.ALL_ITEMS,
        primary_anchor_span_ids=source_spans,
        secondary_anchor_span_ids=closure_spans,
    )


def test_closed_adjacent_page_binding_unlocks_an_all_items_fact_and_is_model_visible(tmp_path):
    first, adjacent, request, binding = _closed_request(tmp_path)
    response = _canonical_response(request, [*_table_models(request), _all_items_model(first, adjacent, binding)])
    report = validate_step2a_response(request=request, response=response)
    payload = OpenAICompatibleStep2Transport.model_payload(request)

    assert request.identity.request_version == "step2a_contract_v11_multipage_continuation_binding"
    assert request.identity.cache_key != build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        sections=(first.section,),
        model="deterministic-fake-v1",
    ).identity.cache_key
    assert report.valid is True
    assert report.continuation_audit[0].state == ContinuationAllItemsAuditState.CLOSED
    assert payload["continuation_bindings"][0]["binding_id"] == binding.binding_id
    assert {item["page_num"] for item in payload["sections"]} == {1, 2}

    altered_binding = build_continuation_binding(
        continuation_id=binding.continuation_id,
        state=ContinuationBindingState.CLOSED,
        source_projection_id=binding.source_projection_id,
        source_manifest_sha256=binding.source_manifest_sha256,
        source_page_num=binding.source_page_num,
        source_section_id=binding.source_section_id,
        source_anchor_span_ids=binding.source_anchor_span_ids,
        adjacent_projection_id=binding.adjacent_projection_id,
        adjacent_manifest_sha256=binding.adjacent_manifest_sha256,
        adjacent_page_num=binding.adjacent_page_num,
        adjacent_section_id=binding.adjacent_section_id,
        closure_anchor_span_ids=binding.closure_anchor_span_ids,
        closure_basis="A distinct reviewed closure basis must produce a distinct request identity.",
    )
    altered_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        continuation_bindings=(altered_binding,),
        model="deterministic-fake-v1",
    )
    assert altered_request.identity.cache_key != request.identity.cache_key

    bad_manifest_binding = build_continuation_binding(
        continuation_id=binding.continuation_id,
        state=ContinuationBindingState.CLOSED,
        source_projection_id=binding.source_projection_id,
        source_manifest_sha256=binding.source_manifest_sha256,
        source_page_num=binding.source_page_num,
        source_section_id=binding.source_section_id,
        source_anchor_span_ids=binding.source_anchor_span_ids,
        adjacent_projection_id=binding.adjacent_projection_id,
        adjacent_manifest_sha256="0" * 64,
        adjacent_page_num=binding.adjacent_page_num,
        adjacent_section_id=binding.adjacent_section_id,
        closure_anchor_span_ids=binding.closure_anchor_span_ids,
        closure_basis=binding.closure_basis,
    )
    with pytest.raises(ValueError, match="immediate adjacent source page"):
        build_step2a_request(
            sample_id="gonglu_24_table_formula",
            material_profile_id="yijian_gonglu_2026",
            projection=first.projection,
            additional_projections=(adjacent.projection,),
            sections=(first.section, adjacent.section),
            continuation_bindings=(bad_manifest_binding,),
            model="deterministic-fake-v1",
        )


def test_missing_or_ambiguous_closure_remains_manual_review_and_blocks_all_items(tmp_path):
    first = _first_page_build()
    adjacent = _adjacent_page_build(tmp_path)
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        model="deterministic-fake-v1",
    )
    binding = request.continuation_bindings[0]
    response = _canonical_response(request, [*_table_models(request), _all_items_model(first, adjacent, binding)])
    report = validate_step2a_response(request=request, response=response)

    assert binding.state == ContinuationBindingState.MANUAL_REVIEW
    assert binding.failure_code == ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE
    assert report.valid is False
    assert report.continuation_audit[0].state == ContinuationAllItemsAuditState.MANUAL_REVIEW


def test_missing_and_nonadjacent_pages_remain_explicit_manual_review(tmp_path):
    first = _first_page_build()
    record = first.projection.continuation_records[0]
    missing = build_continuation_binding(
        continuation_id=record.continuation_id,
        state=ContinuationBindingState.MANUAL_REVIEW,
        source_projection_id=first.projection.projection_id,
        source_manifest_sha256=first.projection.source_manifest_sha256,
        source_page_num=first.projection.page_num,
        source_section_id=first.section.section_id,
        source_anchor_span_ids=record.anchor_span_ids,
        closure_basis="The immediate adjacent source page is absent and the list must remain manual review.",
        failure_code=ContinuationBindingFailureCode.MISSING_ADJACENT_PAGE,
    )
    missing_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        sections=(first.section,),
        continuation_bindings=(missing,),
        model="deterministic-fake-v1",
    )
    missing_response = _canonical_response(
        missing_request,
        [
            *_table_models(missing_request),
            Step2AModelFactProposal(
                proposal_key="proposal:missing-adjacent-enumeration",
                source_section_ids=(first.section.section_id,),
                fact_type=FactType.ENUMERATION,
                label="模板支架设计荷载",
                assertion="续接荷载条目。",
                completeness_mode=CompletenessMode.ALL_ITEMS,
                primary_anchor_span_ids=record.anchor_span_ids,
            ),
        ],
    )
    missing_report = validate_step2a_response(request=missing_request, response=missing_response)
    nonadjacent = _adjacent_page_build(tmp_path / "page3", page_num=3)
    nonadjacent_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(nonadjacent.projection,),
        sections=(first.section, nonadjacent.section),
        model="deterministic-fake-v1",
    )

    assert missing_request.continuation_bindings[0].failure_code == ContinuationBindingFailureCode.MISSING_ADJACENT_PAGE
    assert missing_report.valid is False
    assert missing_report.continuation_audit[0].state == ContinuationAllItemsAuditState.MANUAL_REVIEW
    assert nonadjacent_request.continuation_bindings[0].failure_code == ContinuationBindingFailureCode.NON_ADJACENT_PAGE


def test_span_overreach_and_adjacent_chain_marker_cannot_be_promoted_to_closed_binding(tmp_path):
    first = _first_page_build()
    adjacent = _adjacent_page_build(tmp_path)
    record = first.projection.continuation_records[0]
    overreach = build_continuation_binding(
        continuation_id=record.continuation_id,
        state=ContinuationBindingState.MANUAL_REVIEW,
        source_projection_id=first.projection.projection_id,
        source_manifest_sha256=first.projection.source_manifest_sha256,
        source_page_num=first.projection.page_num,
        source_section_id=first.section.section_id,
        source_anchor_span_ids=record.anchor_span_ids,
        adjacent_projection_id=adjacent.projection.projection_id,
        adjacent_manifest_sha256=adjacent.projection.source_manifest_sha256,
        adjacent_page_num=adjacent.projection.page_num,
        adjacent_section_id=adjacent.section.section_id,
        closure_anchor_span_ids=(record.anchor_span_ids[0],),
        closure_basis="The proposed closure span belongs to the source page and must remain manual review.",
        failure_code=ContinuationBindingFailureCode.SPAN_OVERREACH,
    )
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(adjacent.projection,),
        sections=(first.section, adjacent.section),
        continuation_bindings=(overreach,),
        model="deterministic-fake-v1",
    )
    report = validate_step2a_response(
        request=request,
        response=_canonical_response(request, [*_table_models(request), _all_items_model(first, adjacent, overreach)]),
    )

    assert report.valid is False
    assert report.continuation_audit[0].state == ContinuationAllItemsAuditState.MANUAL_REVIEW

    chained = _adjacent_page_build(tmp_path / "chained", retain_marker=True)
    chained_record = chained.projection.continuation_records[0]
    chained_manual_request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=first.projection,
        additional_projections=(chained.projection,),
        sections=(first.section, chained.section),
        model="deterministic-fake-v1",
    )
    first_binding = next(item for item in chained_manual_request.continuation_bindings if item.continuation_id == record.continuation_id)
    assert first_binding.state == ContinuationBindingState.MANUAL_REVIEW
    assert first_binding.failure_code == ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE

    with pytest.raises(ValueError, match="no unresolved continuation marker"):
        build_step2a_request(
            sample_id="gonglu_24_table_formula",
            material_profile_id="yijian_gonglu_2026",
            projection=first.projection,
            additional_projections=(chained.projection,),
            sections=(first.section, chained.section),
            continuation_bindings=(
                build_continuation_binding(
                    continuation_id=record.continuation_id,
                    state=ContinuationBindingState.CLOSED,
                    source_projection_id=first.projection.projection_id,
                    source_manifest_sha256=first.projection.source_manifest_sha256,
                    source_page_num=first.projection.page_num,
                    source_section_id=first.section.section_id,
                    source_anchor_span_ids=record.anchor_span_ids,
                    adjacent_projection_id=chained.projection.projection_id,
                    adjacent_manifest_sha256=chained.projection.source_manifest_sha256,
                    adjacent_page_num=chained.projection.page_num,
                    adjacent_section_id=chained.section.section_id,
                    closure_anchor_span_ids=(chained.projection.anchor_spans[0].anchor_span_id,),
                    closure_basis="An adjacent page with its own continuation marker cannot close the source chain.",
                ),
                build_continuation_binding(
                    continuation_id=chained_record.continuation_id,
                    state=ContinuationBindingState.MANUAL_REVIEW,
                    source_projection_id=chained.projection.projection_id,
                    source_manifest_sha256=chained.projection.source_manifest_sha256,
                    source_page_num=chained.projection.page_num,
                    source_section_id=chained.section.section_id,
                    source_anchor_span_ids=chained_record.anchor_span_ids,
                    closure_basis="The adjacent page continues again and remains manual review until a later source page is bound.",
                    failure_code=ContinuationBindingFailureCode.MISSING_ADJACENT_PAGE,
                ),
            ),
            model="deterministic-fake-v1",
        )
