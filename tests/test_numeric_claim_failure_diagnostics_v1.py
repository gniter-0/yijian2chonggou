"""Offline tests for safe claim-level Step 2A grounding diagnostics."""
from __future__ import annotations

from pathlib import Path

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
from examcard.knowledge.contracts import (
    ContractValidationReport,
    ModelNumericClaim,
    NumericTokenClass,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2ResultState,
    Step2Stage,
    build_step2a_request,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    validate_step2a_response,
)
from examcard.knowledge.contracts.validation import _safe_numeric_claim_classification
from examcard.knowledge.schema import FactType
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy
from examcard.materials.schema import SemanticRole


ROOT = Path(__file__).resolve().parents[1]


def _request(tmp_path: Path, source: str):
    pdf_sha256 = "d" * 64
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
            config_hash="diagnostic-test",
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
            config_hash="diagnostic-test",
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
        sample_id="numeric-claim-diagnostic",
        page=page,
        source_manifest_path=page_path,
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )
    return (
        build_step2a_request(
            sample_id="numeric-claim-diagnostic",
            material_profile_id="yijian_gonglu_2026",
            projection=built.projection,
            sections=(built.section,),
            model="diagnostic-test-v1",
        ),
        built,
    )


def _response(request, built, *, assertion: str, values: tuple[str, ...]) -> Step2AResponse:
    span = built.projection.anchor_spans[0]
    proposal = Step2AModelFactProposal(
        proposal_key="proposal:numeric-diagnostic-001",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.THRESHOLD,
        label="Numeric diagnostic test",
        assertion=assertion,
        primary_anchor_span_ids=(span.anchor_span_id,),
        numeric_claims=tuple(
            ModelNumericClaim(value=value, anchor_span_id=span.anchor_span_id)
            for value in values
        ),
    )
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(proposal,))
    canonical = derive_step2a_condition_claims(request=request, proposals=selected.proposals)
    return Step2AResponse(
        request=request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=canonical.proposals,
    )


def test_numeric_claim_diagnostics_preserve_errors_and_report_safe_classifications(tmp_path: Path) -> None:
    assertion = "1、施工准备；3.2-1；P6；HPB300；数量为999。"
    request, built = _request(tmp_path, assertion)
    response = _response(
        request,
        built,
        assertion=assertion,
        values=("1、", "3.2-1", "P6", "HPB300", "999"),
    )

    report = validate_step2a_response(request=request, response=response)

    assert report.valid is False
    expected_error = "proposal:numeric-diagnostic-001: numeric claim is not classified as a quantitative value"
    assert report.errors.count(expected_error) == 4
    diagnostics = report.numeric_claim_failure_diagnostics
    assert [item.claim_index for item in diagnostics] == [0, 1, 2, 3]
    assert [item.classification for item in diagnostics] == [
        NumericTokenClass.LIST_INDEX,
        NumericTokenClass.SECTION_NUMBER,
        NumericTokenClass.IDENTIFIER,
        NumericTokenClass.MATERIAL_GRADE,
    ]
    assert [item.match_state for item in diagnostics] == ["matched_but_rejected"] * 4
    assert [item.failure_category for item in diagnostics] == ["non_quantitative_token"] * 4
    assert all(item.anchor_exists is True for item in diagnostics)

    serialized = [item.model_dump(mode="json") for item in diagnostics]
    assert report.model_dump(mode="json")["numeric_claim_failure_diagnostics"] == serialized
    assert all(
        set(item) == {
            "proposal_key",
            "claim_index",
            "classification",
            "match_state",
            "anchor_exists",
                "failure_category",
                "relation_state",
                "anchor_quantitative_count",
                "anchor_quantitative_count_category",
                "anchor_relation_state",
            }
        for item in serialized
    )
    serialized_text = str(serialized)
    for forbidden in ("1、", "3.2-1", "P6", "HPB300", "施工准备", "anchor_span_id"):
        assert forbidden not in serialized_text


def test_unmatched_numeric_claim_diagnostic_is_safe_and_anchor_is_boolean(tmp_path: Path) -> None:
    assertion = "数量为45。"
    request, built = _request(tmp_path, assertion)
    response = _response(request, built, assertion=assertion, values=("44",))

    report = validate_step2a_response(request=request, response=response)

    assert report.valid is False
    assert len(report.numeric_claim_failure_diagnostics) == 1
    diagnostic = report.numeric_claim_failure_diagnostics[0]
    assert diagnostic.classification == "unmatched"
    assert diagnostic.failure_category == "unmatched_assertion_expression"
    assert isinstance(diagnostic.anchor_exists, bool)
    assert diagnostic.anchor_exists is True
    assert "44" not in str(diagnostic.model_dump(mode="json"))


def test_unknown_classification_falls_back_without_error() -> None:
    assert _safe_numeric_claim_classification("future_numeric_class") == "unknown"


def test_valid_quantitative_claim_has_no_failure_diagnostic(tmp_path: Path) -> None:
    assertion = "路基工程共计45题。"
    request, built = _request(tmp_path, assertion)
    response = _response(request, built, assertion=assertion, values=("45题",))

    report = validate_step2a_response(request=request, response=response)

    assert report.valid is True
    assert report.numeric_claim_failure_diagnostics == ()


def test_diagnostics_are_optional_sidecar_and_not_contract_schema() -> None:
    report = ContractValidationReport(stage=Step2Stage.FACT_PLANNING, valid=False)

    assert report.numeric_claim_failure_diagnostics == ()
    schema = ContractValidationReport.model_json_schema()
    assert "numeric_claim_failure_diagnostics" not in schema["properties"]
    assert "NumericClaimFailureDiagnostic" not in schema.get("$defs", {})
