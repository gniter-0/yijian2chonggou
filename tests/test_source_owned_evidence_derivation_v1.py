"""Offline tests for evidence-owned source-owned derivation."""
from __future__ import annotations

import hashlib
import json

import pytest

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    EvidenceAnchorSpan,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2AResponseHandlingMode,
    Step2Stage,
    make_evidence_anchor_span_id,
    make_step2_request_identity,
    source_owned_derive_from_evidence,
    source_owned_local_derivation,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import (
    select_document_extraction_path,
    source_owned_extract_document_proposals,
)
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str = "secret evidence span") -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + "a" * 24
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
        parent_evidence_kind=EvidenceKind.PARAGRAPH,
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
                    "label": "Source-owned fact",
                    "assertion": "The selected source defines the fact.",
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
        model="offline-evidence-derivation-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_valid_local_anchor_metadata_is_derived_without_evidence_mutation() -> None:
    anchor = _make_anchor()
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_from_evidence(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.derived_facts.source_anchor_count == 1
    assert result.derived_facts.local_extraction_status == "derived"
    assert result.derived_facts.anchor_count == 1
    assert result.primary_anchor_span_ids == (anchor.anchor_span_id,)


def test_missing_or_mismatched_anchor_fails_closed() -> None:
    anchor = _make_anchor()
    proposal = _canonical_proposal(anchor.anchor_span_id)

    missing = source_owned_derive_from_evidence(proposal=proposal, evidence_spans={})
    mismatched = source_owned_derive_from_evidence(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: _make_anchor("different local span")},
    )

    for result in (missing, mismatched):
        assert result.derived_facts.local_extraction_status == "failed_closed"
        assert result.derived_facts.source_anchor_count == 0
        assert result.derived_facts.anchor_count == 1


def test_result_does_not_copy_raw_evidence_or_provider_metadata() -> None:
    secret_text = "SECRET_RAW_EVIDENCE_TEXT"
    anchor = _make_anchor(secret_text)
    proposal = _canonical_proposal(anchor.anchor_span_id)
    result = source_owned_derive_from_evidence(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    dumped = result.model_dump(mode="json")
    serialized = json.dumps(dumped, sort_keys=True)
    forbidden_field_names = {
        "raw_text",
        "canonical_text",
        "prompt",
        "response",
        "credentials",
        "provider_metadata",
        "numeric_claims",
        "table_occurrences",
        "provenance",
    }

    assert secret_text not in serialized
    assert forbidden_field_names.isdisjoint(dumped)
    assert set(dumped["derived_facts"]) == {
        "anchor_count",
        "derived_from_local_anchor",
        "source_anchor_count",
        "local_extraction_status",
    }


def test_marker_skeleton_is_explicitly_not_evidence_derivation() -> None:
    anchor = _make_anchor()
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_local_derivation(proposal=proposal)

    assert result.derived_facts.local_extraction_status == "no_derivation"
    assert result.derived_facts.source_anchor_count == 0


@pytest.mark.parametrize(
    "request_version",
    (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    ),
)
def test_v10_v21_v23_do_not_enter_source_owned_evidence_derivation(request_version: str) -> None:
    identity = _identity(request_version)

    assert select_document_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
    assert step2a_response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    assert request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION


def test_source_owned_contract_identity_and_published_shape_are_unchanged() -> None:
    legacy = _identity(STEP2A_REQUEST_VERSION)
    production = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)

    assert legacy.cache_key != production.cache_key
    assert legacy.response_schema_sha256 != production.response_schema_sha256
    assert "numeric_claim_failure_diagnostics" not in Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema()
