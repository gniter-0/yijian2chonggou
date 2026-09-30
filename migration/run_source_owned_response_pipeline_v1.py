"""Run the offline source-owned model-response-to-proposal pipeline.

This migration runner exercises only the response boundary, the Evidence-bound
source-owned extraction adapter, and the existing deterministic numeric
derivation.  The model input is a local fake payload; no provider, transport,
legacy extraction, or persisted source content is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

# Migration runners are executable from the repository root or directly from
# the migration directory.
# ruff: noqa: E402
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pydantic import ValidationError

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedEvidenceCollection,
    SourceOwnedLocalDerivationResult,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    make_evidence_anchor_span_id,
    source_owned_derive_atomic_numeric_claims,
    source_owned_extract_document_proposals_from_evidence,
    source_owned_select_anchor_spans,
)
from examcard.materials.schema import (
    CoverageRequirement,
    PolicyDecision,
    PolicyDisposition,
    SemanticRole,
    StrictModel,
)


type SourceOwnedResponsePipelineScenario = Literal[
    "success",
    "missing_anchor",
    "numeric_derivation_failure",
    "invalid_authority",
]
type ResponseValidationStatus = Literal["validated", "rejected"]
type ExtractionStatus = Literal["extracted", "failed_closed"]
type AnchorSelectionStatus = Literal[
    "selected",
    "no_matching_anchor",
    "ambiguous_anchor",
    "failed_closed",
]
type DerivationStatus = Literal["derived", "no_quantitative_tokens", "failed_closed"]


class SourceOwnedResponsePipelineSummary(StrictModel):
    """Allow-listed, content-free result of one offline response pipeline run."""

    response_validation_status: ResponseValidationStatus
    extraction_status: ExtractionStatus
    anchor_selection_status: AnchorSelectionStatus
    derivation_status: DerivationStatus
    proposal_count: int
    grounded_numeric_claim_count: int


def _summary(
    *,
    response_validation_status: ResponseValidationStatus = "rejected",
    extraction_status: ExtractionStatus = "failed_closed",
    anchor_selection_status: AnchorSelectionStatus = "failed_closed",
    derivation_status: DerivationStatus = "failed_closed",
    proposal_count: int = 0,
    grounded_numeric_claim_count: int = 0,
) -> SourceOwnedResponsePipelineSummary:
    return SourceOwnedResponsePipelineSummary(
        response_validation_status=response_validation_status,
        extraction_status=extraction_status,
        anchor_selection_status=anchor_selection_status,
        derivation_status=derivation_status,
        proposal_count=proposal_count,
        grounded_numeric_claim_count=grounded_numeric_claim_count,
    )


def _default_anchor() -> EvidenceAnchorSpan:
    """Create a deterministic in-memory Evidence anchor for the fake run."""

    text = "The source value is 65MPa."
    parent_evidence_id = "ev:" + "a" * 24
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
            reason="offline source-owned response pipeline anchor",
        ),
        source_manifest_sha256=source_manifest_sha256,
    )


def default_source_owned_evidence() -> SourceOwnedEvidenceCollection:
    """Return the explicit local Evidence input used by the offline runner."""

    return SourceOwnedEvidenceCollection(anchor_spans=(_default_anchor(),))


def validate_source_owned_response_payload(
    payload: Mapping[str, object] | Step2AProductionSourceOwnedArbitraryPDFResultPayload,
) -> Step2AProductionSourceOwnedArbitraryPDFResultPayload:
    """Validate the fake model response at the strict source-owned boundary."""

    if isinstance(payload, Step2AProductionSourceOwnedArbitraryPDFResultPayload):
        return payload
    if not isinstance(payload, Mapping):
        raise ValueError("source-owned response validation requires a mapping payload")
    return Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def _fake_response_payload(
    *,
    anchor_span_id: str,
    scenario: SourceOwnedResponsePipelineScenario,
) -> dict[str, object]:
    assertion = (
        "The asserted value is 42 units."
        if scenario == "numeric_derivation_failure"
        else "The asserted value is 65MPa."
    )
    proposal: dict[str, object] = {
        "fact_type": "definition",
        "label": "Source-owned response fact",
        "assertion": assertion,
        "primary_anchor_span_ids": [
            "span:" + "0" * 24 if scenario == "missing_anchor" else anchor_span_id
        ],
    }
    if scenario == "invalid_authority":
        proposal["numeric_claims"] = []
    return {"proposals": [proposal]}


def _aggregate_anchor_status(
    selections: tuple[str, ...],
) -> AnchorSelectionStatus:
    if not selections:
        return "failed_closed"
    for status in ("failed_closed", "ambiguous_anchor", "no_matching_anchor"):
        if status in selections:
            return status  # type: ignore[return-value]
    return "selected"


def _aggregate_derivation_status(
    results: tuple[SourceOwnedLocalDerivationResult, ...],
) -> DerivationStatus:
    if not results:
        return "failed_closed"
    statuses = tuple(result.numeric_derivation_status for result in results)
    if "failed_closed" in statuses:
        return "failed_closed"
    if "derived" in statuses:
        return "derived"
    return "no_quantitative_tokens"


def run_source_owned_response_pipeline(
    *,
    scenario: SourceOwnedResponsePipelineScenario = "success",
    evidence: SourceOwnedEvidenceCollection | None = None,
    response_payload: Mapping[str, object]
    | Step2AProductionSourceOwnedArbitraryPDFResultPayload
    | None = None,
) -> SourceOwnedResponsePipelineSummary:
    """Run response validation through canonical proposal and local derivation."""

    if os.environ.get("REAL_NETWORK", "0") != "0":
        raise ValueError("source-owned response pipeline requires REAL_NETWORK=0")
    if scenario not in {
        "success",
        "missing_anchor",
        "numeric_derivation_failure",
        "invalid_authority",
    }:
        raise ValueError(f"unsupported source-owned response pipeline scenario: {scenario}")

    local_evidence = evidence or default_source_owned_evidence()
    anchor_ids = tuple(anchor.anchor_span_id for anchor in local_evidence.anchor_spans)
    if response_payload is None:
        if not anchor_ids:
            return _summary(response_validation_status="rejected")
        response_payload = _fake_response_payload(
            anchor_span_id=anchor_ids[0],
            scenario=scenario,
        )

    try:
        response = validate_source_owned_response_payload(response_payload)
    except (ValidationError, TypeError, ValueError):
        return _summary(response_validation_status="rejected")

    try:
        proposals = source_owned_extract_document_proposals_from_evidence(
            evidence=local_evidence,
            response=response,
        )
    except (TypeError, ValueError):
        return _summary(
            response_validation_status="validated",
            extraction_status="failed_closed",
        )

    if not proposals:
        return _summary(
            response_validation_status="validated",
            extraction_status="failed_closed",
        )

    evidence_spans = {anchor.anchor_span_id: anchor for anchor in local_evidence.anchor_spans}
    selection_statuses: list[str] = []
    derivation_results: list[SourceOwnedLocalDerivationResult] = []
    for proposal in proposals:
        selection = source_owned_select_anchor_spans(
            proposal=proposal,
            candidate_anchor_spans=evidence_spans,
        )
        selection_statuses.append(selection.anchor_selection_status)
        derivation_results.append(
            source_owned_derive_atomic_numeric_claims(
                proposal=proposal,
                evidence_spans=evidence_spans,
            )
        )

    typed_results = tuple(derivation_results)
    return _summary(
        response_validation_status="validated",
        extraction_status="extracted",
        anchor_selection_status=_aggregate_anchor_status(tuple(selection_statuses)),
        derivation_status=_aggregate_derivation_status(typed_results),
        proposal_count=len(proposals),
        grounded_numeric_claim_count=sum(
            len(result.grounded_numeric_claims) for result in typed_results
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=(
            "success",
            "missing_anchor",
            "numeric_derivation_failure",
            "invalid_authority",
        ),
        default="success",
    )
    args = parser.parse_args(argv)
    summary = run_source_owned_response_pipeline(scenario=args.scenario)
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
