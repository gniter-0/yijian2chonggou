"""Offline contract tests for Step 2A Evidence Anchor Span Projection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_page_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    EvidenceKind,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.schema import FactType
from examcard.knowledge.contracts import (
    AnchorSpanSelectionDerivationState,
    ConditionClaimDerivationState,
    ModelNumericClaim,
    NumericTokenClass,
    Step2AModelFactProposal,
    Step2AResultPayload,
    Step2DecoderConfig,
    Step2ResponseFormat,
    Step2ResultState,
    Step2AResponse,
    build_step2a_request,
    condition_operators_in_text,
    classify_numeric_tokens,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    derive_step2a_table_cell_anchors,
    validate_step2a_response,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy
from examcard.materials.schema import SemanticRole

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


def _projected():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_text(encoding="utf-8"))
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference="data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json",
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )


def _request():
    built = _projected()
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        decoder=Step2DecoderConfig(max_tokens=1000, response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT),
    )
    return request, built


def _numeric_45_request(tmp_path: Path):
    source = "路基工程共计45 题。"
    pdf_sha256 = "b" * 64
    document_id = make_document_id(pdf_sha256)
    locator = SourceLocator(document_sha256=pdf_sha256, page_num=1, native_block_index=0)
    fingerprint = make_evidence_fingerprint(kind=EvidenceKind.PARAGRAPH.value, canonical_text=source)
    evidence_id = make_evidence_id(locator_id=make_source_locator_id(locator), fingerprint=fingerprint)
    unit = EvidenceUnit(
        evidence_id=evidence_id,
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=source,
            source_channels=(SourceChannel.NATIVE_TEXT.value,),
            engine_versions={"test": "1"},
            config_hash="test-config",
        ),
        document_id=document_id,
        kind=EvidenceKind.PARAGRAPH,
        locator=locator,
        raw_text=source,
        canonical_text=source,
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1,
        order=0,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.NATIVE_TEXT}),
            engine_versions={"test": "1"},
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )
    page = PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(native_text_coverage=1, ocr_alignment_rate=0, unresolved_regions=0, conflict_count=0),
    )
    page_path = tmp_path / "page_0001.json"
    page_path.write_text(page.model_dump_json(), encoding="utf-8")
    built = build_step2a_semantic_projection(
        sample_id="exam-counter-45",
        page=page,
        source_manifest_path=page_path,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )
    return build_step2a_request(
        sample_id="exam-counter-45",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
    )


def _condition_span(built):
    return next(
        span
        for span in built.projection.anchor_spans
        if span.parent_evidence_kind == EvidenceKind.TABLE_CELL and condition_operators_in_text(span.canonical_text)
    )


def _proposal(
    request,
    span,
    *,
    assertion: str | None = None,
    extra_spans: tuple[str, ...] = (),
    numeric_claims=(),
    fact_type: FactType = FactType.CONDITION,
):
    return Step2AModelFactProposal(
        proposal_key="proposal:span-grounding",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=fact_type,
        label="Span grounded rule",
        assertion=assertion or span.canonical_text,
        primary_anchor_span_ids=(span.anchor_span_id, *extra_spans),
        numeric_claims=numeric_claims,
    )


def _required_complete_table_proposals(request):
    proposals = []
    for section_index, section in enumerate(request.sections):
        for table_index, table in enumerate(section.tables):
            proposals.append(
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
    return tuple(proposals)


def _required_table_atomic_proposals(request, *, skip_anchor_span_ids=()):
    skipped = set(skip_anchor_span_ids)
    proposals = []
    for section in request.sections:
        for table in section.tables:
            for requirement in table.atomic_subfact_requirements:
                if requirement.anchor_span_id in skipped:
                    continue
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
                        label="表格原子规则",
                        assertion=requirement.required_assertion,
                        primary_anchor_span_ids=(requirement.anchor_span_id,),
                        numeric_claims=numeric_claims,
                    )
                )
    return tuple(proposals)


def test_model_response_schema_accepts_only_anchor_selections():
    schema = Step2AResultPayload.model_json_schema()
    definition = schema["$defs"]["Step2AModelFactProposal"]
    numeric = schema["$defs"]["ModelNumericClaim"]

    assert "primary_anchor_span_ids" in definition["properties"]
    assert "primary_evidence_ids" not in definition["properties"]
    assert "condition_claims" not in definition["properties"]
    assert set(numeric["properties"]) == {"value", "anchor_span_id"}


def test_canonical_fact_derives_parent_evidence_and_table_cell_from_selected_span():
    request, built = _request()
    span = _condition_span(built)
    selected = derive_step2a_anchor_span_selection(
        request=request,
        proposals=(
            _proposal(request, span),
            *_required_complete_table_proposals(request),
            *_required_table_atomic_proposals(request, skip_anchor_span_ids=(span.anchor_span_id,)),
        ),
    )
    table = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    conditions = derive_step2a_condition_claims(request=request, proposals=table.proposals)
    response = Step2AResponse(request=request.identity, state=Step2ResultState.SUCCEEDED, proposals=conditions.proposals)

    fact = response.proposals[0]
    assert fact.primary_anchor_span_ids == (span.anchor_span_id,)
    assert fact.primary_evidence_ids == (span.parent_evidence_id,)
    assert fact.table_cell_anchor_span_ids == (span.anchor_span_id,)
    assert fact.table_cell_evidence_ids == (span.parent_evidence_id,)
    assert all(claim.anchor_span_id == span.anchor_span_id for claim in fact.condition_claims)
    assert validate_step2a_response(request=request, response=response).valid is True


def test_unknown_or_out_of_section_anchor_never_creates_canonical_fact():
    request, built = _request()
    span = _condition_span(built)
    unknown = _proposal(request, span).model_copy(update={"primary_anchor_span_ids": ("span:" + "f" * 24,)})
    result = derive_step2a_anchor_span_selection(request=request, proposals=(unknown,))
    assert not result.proposals
    assert result.audits[0].state == AnchorSpanSelectionDerivationState.UNKNOWN_SPAN

    out_of_section = _proposal(request, span).model_copy(update={"source_section_ids": ("section:" + "0" * 24,)})
    result = derive_step2a_anchor_span_selection(request=request, proposals=(out_of_section,))
    assert not result.proposals
    assert result.audits[0].state == AnchorSpanSelectionDerivationState.OUT_OF_SECTION


def test_condition_gate_uses_only_selected_span_and_fails_closed_for_uncovered_or_isolated_operator():
    request, built = _request()
    condition = _condition_span(built)
    unrelated = next(span for span in built.projection.anchor_spans if span.anchor_span_id != condition.anchor_span_id and not condition_operators_in_text(span.canonical_text))

    selected = derive_step2a_anchor_span_selection(
        request=request,
        proposals=(_proposal(request, unrelated, assertion=condition.canonical_text),),
    )
    derivation = derive_step2a_condition_claims(request=request, proposals=selected.proposals)
    assert ConditionClaimDerivationState.ASSERTION_WITHOUT_EVIDENCE in {audit.state for audit in derivation.audits}

    isolated = _proposal(request, condition, assertion=condition_operators_in_text(condition.canonical_text)[0].value)
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(isolated,))
    derivation = derive_step2a_condition_claims(request=request, proposals=selected.proposals)
    assert ConditionClaimDerivationState.ISOLATED_OPERATOR in {audit.state for audit in derivation.audits}


def test_condition_gate_fails_closed_when_one_selected_span_has_ambiguous_matches(tmp_path):
    original = _projected()
    page = PageEvidence.model_validate_json(PAGE_PATH.read_text(encoding="utf-8"))
    parent_id = next(
        span.parent_evidence_id
        for span in original.projection.anchor_spans
        if span.parent_evidence_kind == EvidenceKind.PARAGRAPH
    )
    synthetic_text = "模板不得使用废油，模板不得使用柴油。"
    synthetic_units = tuple(
        unit.model_copy(update={"canonical_text": synthetic_text, "raw_text": synthetic_text})
        if unit.evidence_id == parent_id
        else unit
        for unit in page.evidence_units
    )
    synthetic_page = page.model_copy(update={"evidence_units": synthetic_units})
    manifest_path = tmp_path / "page_0001.json"
    manifest_path.write_text(synthetic_page.model_dump_json(indent=2), encoding="utf-8")
    synthetic_page = PageEvidence.model_validate_json(manifest_path.read_bytes())
    built = build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=synthetic_page,
        source_manifest_path=manifest_path,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
    )
    span = next(span for span in built.projection.anchor_spans if span.parent_evidence_id == parent_id)
    proposal = _proposal(request, span, assertion="模板不得使用")
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(proposal,))
    derivation = derive_step2a_condition_claims(request=request, proposals=selected.proposals)

    assert ConditionClaimDerivationState.MULTIPLE_CANDIDATE_SPANS in {audit.state for audit in derivation.audits}
    assert not derivation.proposals[0].condition_claims


def test_numeric_claims_use_selected_span_and_classification_remains_local():
    request, built = _request()
    numeric = next(span for span in built.projection.anchor_spans if "65MPa" in span.canonical_text)
    proposal = _proposal(
        request,
        numeric,
        assertion=numeric.canonical_text,
        numeric_claims=(ModelNumericClaim(value="65MPa", anchor_span_id=numeric.anchor_span_id),),
        fact_type=FactType.THRESHOLD,
    )
    selected = derive_step2a_anchor_span_selection(
        request=request,
        proposals=(
            proposal,
            *_required_complete_table_proposals(request),
            *_required_table_atomic_proposals(request, skip_anchor_span_ids=(numeric.anchor_span_id,)),
        ),
    )
    selected_tables = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
    response = Step2AResponse(
        request=request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=derive_step2a_condition_claims(request=request, proposals=selected_tables.proposals).proposals,
    )
    assert validate_step2a_response(request=request, response=response).valid is True

    bad = proposal.model_copy(update={"numeric_claims": (ModelNumericClaim(value="65MPa", anchor_span_id="span:" + "e" * 24),)})
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(bad,))
    assert selected.audits[0].state == AnchorSpanSelectionDerivationState.NUMERIC_SPAN_UNSELECTED


def test_numeric_expression_tokenization_keeps_ratios_ranges_units_and_ordinals_distinct():
    tokens = classify_numeric_tokens(
        "（2）模板；第3章第2行；HPB300；3.2-1；1/400；1／250；10-20mm；65 MPa；20%"
    )

    assert [(item.value, item.classification) for item in tokens] == [
        ("（2）", NumericTokenClass.LIST_INDEX),
        ("第3章", NumericTokenClass.LIST_INDEX),
        ("第2行", NumericTokenClass.LIST_INDEX),
        ("HPB300", NumericTokenClass.MATERIAL_GRADE),
        ("3.2-1", NumericTokenClass.SECTION_NUMBER),
        ("1/400", NumericTokenClass.QUANTITATIVE_VALUE),
        ("1／250", NumericTokenClass.QUANTITATIVE_VALUE),
        ("10-20mm", NumericTokenClass.QUANTITATIVE_VALUE),
        ("65 MPa", NumericTokenClass.QUANTITATIVE_VALUE),
        ("20%", NumericTokenClass.QUANTITATIVE_VALUE),
    ]


def test_exam_question_counts_are_complete_quantitative_tokens():
    for source, expected in (("45题", "45题"), ("45 题", "45 题")):
        tokens = classify_numeric_tokens(source)

        assert len(tokens) == 1
        assert tokens[0].value == expected
        assert tokens[0].classification == NumericTokenClass.QUANTITATIVE_VALUE


def test_exam_question_count_and_existing_numeric_classifications_remain_stable():
    cases = {
        "2020、": NumericTokenClass.LIST_INDEX,
        "3.2-1": NumericTokenClass.SECTION_NUMBER,
        "P6": NumericTokenClass.IDENTIFIER,
        "HPB300": NumericTokenClass.MATERIAL_GRADE,
        "65 MPa": NumericTokenClass.QUANTITATIVE_VALUE,
        "10-20mm": NumericTokenClass.QUANTITATIVE_VALUE,
        "20%": NumericTokenClass.QUANTITATIVE_VALUE,
        "45年": NumericTokenClass.QUANTITATIVE_VALUE,
    }

    for source, classification in cases.items():
        tokens = classify_numeric_tokens(source)

        assert len(tokens) == 1
        assert tokens[0].classification == classification


def test_exam_question_count_numeric_grounding_is_strict_and_normalized(tmp_path: Path):
    request = _numeric_45_request(tmp_path)
    span = request.projection.anchor_spans[0]

    def response_for(proposal):
        selected = derive_step2a_anchor_span_selection(request=request, proposals=(proposal,))
        canonical = derive_step2a_condition_claims(request=request, proposals=selected.proposals)
        return Step2AResponse(
            request=request.identity,
            state=Step2ResultState.SUCCEEDED,
            proposals=canonical.proposals,
        )

    proposal = _proposal(
        request,
        span,
        assertion="路基工程共计45 题。",
        numeric_claims=(ModelNumericClaim(value="45题", anchor_span_id=span.anchor_span_id),),
        fact_type=FactType.THRESHOLD,
    )
    valid_response = response_for(proposal)
    assert validate_step2a_response(request=request, response=valid_response).valid is True

    wrong_value = proposal.model_copy(
        update={"numeric_claims": (ModelNumericClaim(value="44题", anchor_span_id=span.anchor_span_id),)}
    )
    assert validate_step2a_response(request=request, response=response_for(wrong_value)).valid is False

    wrong_anchor = proposal.model_copy(
        update={"numeric_claims": (ModelNumericClaim(value="45题", anchor_span_id="span:" + "e" * 24),)}
    )
    with pytest.raises(ValueError, match="successful Step 2A responses require proposals and no failure"):
        response_for(wrong_anchor)


def test_ratio_expression_is_one_claimable_token_not_two_bare_numbers():
    tokens = classify_numeric_tokens("挠度不超过模板构件跨度的1/400。")

    assert [(item.value, item.classification) for item in tokens] == [
        ("1/400", NumericTokenClass.QUANTITATIVE_VALUE),
    ]


def test_legacy_parent_evidence_shape_is_strictly_rejected():
    request, built = _request()
    span = _condition_span(built)
    payload = {
        "proposals": [
            {
                "proposal_key": "proposal:legacy",
                "source_section_ids": [request.sections[0].section_id],
                "fact_type": "condition",
                "label": "Legacy evidence selection",
                "assertion": span.canonical_text,
                "primary_evidence_ids": [span.parent_evidence_id],
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        Step2AResultPayload.model_validate(payload)
    errors = {(tuple(error["loc"]), error["type"]) for error in exc_info.value.errors()}
    assert (("proposals", 0, "primary_anchor_span_ids"), "missing") in errors
    assert (("proposals", 0, "primary_evidence_ids"), "extra_forbidden") in errors


def test_request_identity_binds_span_projection_and_schema_version():
    request, built = _request()
    altered = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        prompt_template="different anchor-span prompt",
    )
    assert request.identity.cache_key != altered.identity.cache_key
    assert request.identity.response_schema_sha256 != ""


def test_manual_reissue_reason_is_audited_in_identity_and_hidden_from_model_payload():
    request, built = _request()
    reason = "manual_reissue_after_phase2r051_unknown_outcome_v1"
    reissued = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        manual_reissue_reason=reason,
    )
    repeated = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        manual_reissue_reason=reason,
    )

    assert reissued.manual_reissue_reason == reason
    assert reissued.identity.manual_reissue_reason == reason
    assert reissued.identity.request_id != request.identity.request_id
    assert reissued.identity.cache_key != request.identity.cache_key
    assert reissued.identity.idempotency_key != request.identity.idempotency_key
    assert reissued.identity == repeated.identity
    assert reason not in json.dumps(OpenAICompatibleStep2Transport.model_payload(reissued), ensure_ascii=False)

    with pytest.raises(ValidationError):
        type(reissued).model_validate(
            reissued.model_copy(update={"manual_reissue_reason": "manual reissue with spaces"}).model_dump(mode="json")
        )

    with pytest.raises(ValidationError):
        type(reissued).model_validate(
            reissued.model_copy(update={"manual_reissue_reason": None}).model_dump(mode="json")
        )
