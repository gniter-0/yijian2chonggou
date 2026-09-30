"""Offline tests for deterministic source-owned anchor selection."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    EvidenceAnchorSpan,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    SourceOwnedAnchorSelectionResult,
    STEP2A_REQUEST_VERSION,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2Stage,
    make_evidence_anchor_span_id,
    make_step2_request_identity,
    select_step2a_extraction_path,
    source_owned_select_anchor_spans,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, suffix: str) -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + suffix * 24
    content_policy_fingerprint = "b" * 64
    source_manifest_sha256 = "c" * 64
    exact_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=parent_evidence_id,
        start=0,
        end=len(text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        source_manifest_sha256=source_manifest_sha256,
    )
    policy_decision = PolicyDecision(
        evidence_id=parent_evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        policy_id="core-policy-v1",
        policy_revision=1,
        reason="selected source anchor",
    )
    return EvidenceAnchorSpan(
        anchor_span_id=anchor_span_id,
        parent_evidence_id=parent_evidence_id,
        parent_evidence_kind=EvidenceKind.TABLE_CELL,
        start=0,
        end=len(text),
        canonical_text=text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id="core-policy-v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        policy_decision=policy_decision,
        source_manifest_sha256=source_manifest_sha256,
    )


def _canonical_proposal(anchor_span_id: str):
    payload = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": "definition",
                    "label": "Selected source fact",
                    "assertion": "The selected source supports the fact.",
                    "primary_anchor_span_ids": [anchor_span_id],
                }
            ]
        }
    )
    return source_owned_extract_document_proposals(response=payload)[0]


def _identity(request_version: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model="offline-anchor-selection-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_all_declared_anchor_ids_are_selected_in_proposal_order() -> None:
    first = _make_anchor("first source", suffix="a")
    second = _make_anchor("second source", suffix="d")
    proposal = _canonical_proposal(first.anchor_span_id).model_copy(
        update={"primary_anchor_span_ids": (first.anchor_span_id, second.anchor_span_id)}
    )

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=(second, first),
    )

    assert isinstance(result, SourceOwnedAnchorSelectionResult)
    assert result.anchor_selection_status == "selected"
    assert result.selected_anchor_span_ids == (first.anchor_span_id, second.anchor_span_id)


def test_missing_anchor_id_fails_closed() -> None:
    selected = _make_anchor("selected source", suffix="a")
    missing = "span:" + "f" * 24
    proposal = _canonical_proposal(selected.anchor_span_id).model_copy(
        update={"primary_anchor_span_ids": (missing,)}
    )

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=(selected,),
    )

    assert result.anchor_selection_status == "failed_closed"
    assert result.selected_anchor_span_ids == ()


def test_empty_anchor_list_has_no_matching_anchor_status() -> None:
    anchor = _make_anchor("available source", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id).model_copy(update={"primary_anchor_span_ids": ()})

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=(anchor,),
    )

    assert result.anchor_selection_status == "no_matching_anchor"
    assert result.selected_anchor_span_ids == ()


def test_duplicate_anchor_ids_are_ambiguous() -> None:
    anchor = _make_anchor("duplicated source", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id).model_copy(
        update={"primary_anchor_span_ids": (anchor.anchor_span_id, anchor.anchor_span_id)}
    )

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=(anchor,),
    )

    assert result.anchor_selection_status == "ambiguous_anchor"
    assert result.selected_anchor_span_ids == ()


def test_anchor_identity_mismatch_fails_closed() -> None:
    selected = _make_anchor("selected source", suffix="a")
    other = _make_anchor("other source", suffix="d")
    proposal = _canonical_proposal(selected.anchor_span_id)

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={selected.anchor_span_id: other},
    )

    assert result.anchor_selection_status == "failed_closed"
    assert result.selected_anchor_span_ids == ()


def test_unknown_candidate_metadata_fails_closed_at_selection_boundary() -> None:
    anchor = _make_anchor("available source", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={anchor.anchor_span_id: {"unknown_field": "x"}},  # type: ignore[arg-type]
    )

    assert result.anchor_selection_status == "failed_closed"
    with pytest.raises(ValidationError):
        EvidenceAnchorSpan.model_validate({**anchor.model_dump(), "unknown_field": "x"})


def test_selection_result_does_not_copy_source_or_provider_content() -> None:
    secret = "SECRET_EVIDENCE_TEXT"
    anchor = _make_anchor(secret, suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)
    result = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=(anchor,),
    )
    dumped = result.model_dump(mode="json")

    assert json.dumps(dumped, sort_keys=True) == json.dumps(
        {
            "selected_anchor_span_ids": [anchor.anchor_span_id],
            "anchor_selection_status": "selected",
        },
        sort_keys=True,
    )
    assert secret not in json.dumps(dumped, sort_keys=True)
    assert set(dumped) == {"selected_anchor_span_ids", "anchor_selection_status"}


@pytest.mark.parametrize(
    "request_version",
    (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    ),
)
def test_legacy_v10_v21_v23_remain_on_existing_extraction_path(request_version: str) -> None:
    identity = _identity(request_version)

    assert select_step2a_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
    assert request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
