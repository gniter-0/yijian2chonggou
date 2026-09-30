"""Offline tests for the Evidence-bound source-owned extraction seam."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

import examcard.knowledge.extraction_v1 as legacy_extraction
from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedCanonicalProposal,
    SourceOwnedEvidenceCollection,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    make_evidence_anchor_span_id,
    source_owned_extract_document_proposals_from_evidence,
)
from examcard.knowledge.contracts.enums import NumericTokenClass
from examcard.knowledge.contracts.numeric_tokens import classify_numeric_tokens
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.llm_transport import TransportCompletion
from examcard.knowledge.schema import FactType
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, suffix: str = "a") -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + suffix * 24
    policy_fingerprint = "b" * 64
    source_manifest_sha256 = "c" * 64
    exact_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=parent_evidence_id,
        start=0,
        end=len(text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=policy_fingerprint,
        source_manifest_sha256=source_manifest_sha256,
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
        content_policy_fingerprint_sha256=policy_fingerprint,
        policy_decision=PolicyDecision(
            evidence_id=parent_evidence_id,
            semantic_role=SemanticRole.CORE,
            disposition=PolicyDisposition.INCLUDED,
            coverage_requirement=CoverageRequirement.REQUIRED,
            policy_id="core-policy-v1",
            policy_revision=1,
            reason="selected source anchor",
        ),
        source_manifest_sha256=source_manifest_sha256,
    )


def _payload(anchor_ids: tuple[str, ...]) -> Step2AProductionSourceOwnedArbitraryPDFResultPayload:
    return Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": FactType.DEFINITION.value,
                    "label": "Source-owned fact",
                    "assertion": "The selected source defines the fact.",
                    "primary_anchor_span_ids": list(anchor_ids),
                }
            ]
        }
    )


def test_real_extraction_seam_binds_valid_response_to_evidence() -> None:
    anchor = _make_anchor("The selected source defines the fact.")

    proposals = source_owned_extract_document_proposals_from_evidence(
        evidence=SourceOwnedEvidenceCollection(anchor_spans=(anchor,)),
        response=_payload((anchor.anchor_span_id,)),
    )

    assert len(proposals) == 1
    assert isinstance(proposals[0], SourceOwnedCanonicalProposal)
    assert set(proposals[0].model_dump(mode="json")) == {
        "proposal_key",
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
    }
    assert not any(
        token.classification == NumericTokenClass.QUANTITATIVE_VALUE
        for token in classify_numeric_tokens(proposals[0].assertion)
    )


@pytest.mark.parametrize(
    "forbidden_field",
    ("numeric_claims", "table_occurrences", "structural_indexes", "evidence_ids", "provenance"),
)
def test_forbidden_authority_fields_fail_at_response_schema(
    forbidden_field: str,
) -> None:
    anchor = _make_anchor("The selected source defines the fact.")
    payload = {
        "proposals": [
            {
                "fact_type": FactType.DEFINITION.value,
                "label": "Source-owned fact",
                "assertion": "The selected source defines the fact.",
                "primary_anchor_span_ids": [anchor.anchor_span_id],
                forbidden_field: [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_missing_anchor_fails_closed_before_a_canonical_proposal_is_returned() -> None:
    available = _make_anchor("The available source statement.")
    missing = _make_anchor("The missing source statement.", suffix="d")

    with pytest.raises(ValueError, match="failed closed"):
        source_owned_extract_document_proposals_from_evidence(
            evidence=SourceOwnedEvidenceCollection(anchor_spans=(available,)),
            response=_payload((missing.anchor_span_id,)),
        )


def test_numeric_derivation_remains_downstream_only() -> None:
    anchor = _make_anchor("The threshold is 65MPa.")
    proposals = source_owned_extract_document_proposals_from_evidence(
        evidence=SourceOwnedEvidenceCollection(anchor_spans=(anchor,)),
        response=_payload((anchor.anchor_span_id,)),
    )

    payload = proposals[0].model_dump(mode="json")
    assert "numeric_claims" not in payload
    assert "grounded_numeric_claims" not in payload
    assert "table_occurrences" not in payload
    assert "provenance" not in payload


def test_source_owned_extraction_does_not_enter_legacy_or_provider_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = _make_anchor("The selected source defines the fact.")

    def forbidden_legacy(*_: object, **__: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider path was entered")

    monkeypatch.setattr(legacy_extraction, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_provider)

    proposals = source_owned_extract_document_proposals_from_evidence(
        evidence=SourceOwnedEvidenceCollection(anchor_spans=(anchor,)),
        response=_payload((anchor.anchor_span_id,)),
    )

    assert proposals


def test_legacy_payload_cannot_enter_source_owned_evidence_adapter() -> None:
    anchor = _make_anchor("The selected source defines the fact.")
    legacy_payload = {
        "proposals": [
            {
                "proposal_key": "proposal:legacy-001",
                "source_section_ids": ["section:" + "b" * 24],
                "fact_type": FactType.DEFINITION.value,
                "label": "Legacy fact",
                "assertion": "The legacy contract remains available.",
                "primary_anchor_span_ids": [anchor.anchor_span_id],
                "numeric_claims": [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(legacy_payload)


def test_unknown_response_metadata_is_not_accepted_by_the_adapter() -> None:
    anchor = _make_anchor("The selected source defines the fact.")
    payload = {
        "proposals": [
            {
                "fact_type": FactType.DEFINITION.value,
                "label": "Source-owned fact",
                "assertion": "The selected source defines the fact.",
                "primary_anchor_span_ids": [anchor.anchor_span_id],
                "unknown_field": "must fail closed",
            }
        ]
    }
    completion = TransportCompletion(content=json.dumps(payload), http_status=200)

    with pytest.raises(ValidationError):
        Step2OpenAIAdapter.decode_source_owned_completion(
            request_version="step2a_contract_production_source_owned_arbitrary_pdf_v1",
            completion=completion,
        )
