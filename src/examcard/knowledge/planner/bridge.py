"""Deterministic bridge from reviewed continuation title audits to Step 2B."""
from __future__ import annotations

from collections.abc import Mapping

from ...evidence.schema import EvidenceUnit

from ..continuation.review import (
    ContinuationCanonicalTitleProjection,
    ContinuationCanonicalTitleProjectionState,
)
from ..contracts import (
    ContinuationTitleProjectionProvenance,
    FactSummaryInput,
    ReviewedCanonicalTitleProjection,
    Step2AFactProposal,
    fact_summary_from_proposal,
)
from ..llm import Step2AExecutionRecord


def reviewed_title_projection_from_continuation(
    projection: ContinuationCanonicalTitleProjection,
) -> ReviewedCanonicalTitleProjection:
    """Convert only a terminal projected audit; other states have no Step 2B form."""

    if projection.state != ContinuationCanonicalTitleProjectionState.PROJECTED:
        raise ValueError("only projected continuation title audits may enter Step 2B")
    if (
        projection.proposal_key is None
        or projection.model_label is None
        or projection.model_label_sha256 is None
        or projection.canonical_title is None
    ):
        raise ValueError("projected continuation title audit is missing its canonical source fields")
    provenance = ContinuationTitleProjectionProvenance(
        projection_id=projection.projection_id,
        projection_sha256=projection.projection_sha256,
        fixture_id=projection.fixture_id,
        fixture_sha256=projection.fixture_sha256,
        fact_key=projection.fact_key,
        source_request_id=projection.request_id,
        source_request_version=projection.request_version,
        source_response_sha256=projection.source_response_sha256,
        source_proposal_key=projection.proposal_key,
        model_label=projection.model_label,
        model_label_sha256=projection.model_label_sha256,
        canonical_title=projection.canonical_title,
    )
    return ReviewedCanonicalTitleProjection(
        fixture_id=projection.fixture_id,
        fact_key=projection.fact_key,
        canonical_title=projection.canonical_title,
        source_label=projection.model_label,
        source_label_sha256=projection.model_label_sha256,
        continuation_title_projection=provenance,
    )


def build_continuation_step2b_fact_summaries(
    *,
    record: Step2AExecutionRecord,
    title_projection: ContinuationCanonicalTitleProjection,
    evidence_by_id: Mapping[str, EvidenceUnit],
) -> tuple[FactSummaryInput, ...]:
    """Build all accepted V16 summaries, projecting exactly one reviewed title.

    Ordinary Facts remain eligible through the successful V16 execution record.
    The one Fact governed by a continuation-title audit must be terminally
    ``projected``; draft, out-of-scope and fail-closed audits cannot be used.
    """

    if record.response.state.value != "succeeded" or not record.contract_report.valid:
        raise ValueError("only a successful contract-valid Step 2A record may build Step 2B summaries")
    if record.raw_response_sha256 != title_projection.source_response_sha256:
        raise ValueError("continuation title projection response hash does not match the Step 2A record")
    if record.request.identity.request_id != title_projection.request_id:
        raise ValueError("continuation title projection request ID does not match the Step 2A record")
    if record.request.identity.request_version != title_projection.request_version:
        raise ValueError("continuation title projection request version does not match the Step 2A record")
    reviewed_projection = reviewed_title_projection_from_continuation(title_projection)
    target_key = reviewed_projection.continuation_title_projection.source_proposal_key
    proposals = {proposal.proposal_key: proposal for proposal in record.response.proposals}
    if target_key not in proposals:
        raise ValueError("continuation title projection proposal is absent from the Step 2A record")
    summaries: list[FactSummaryInput] = []
    for proposal in record.response.proposals:
        summary_projection = reviewed_projection if proposal.proposal_key == target_key else None
        summaries.append(
            fact_summary_from_proposal(
                proposal=proposal,
                evidence_by_id=evidence_by_id,
                reviewed_title_projection=summary_projection,
            )
        )
    if sum(item.reviewed_title_projection is not None for item in summaries) != 1:
        raise ValueError("continuation Step 2B bridge must project exactly one reviewed Fact title")
    if any(item.source_proposal_key is None for item in summaries):
        raise ValueError("continuation Step 2B bridge requires every Fact summary to retain its source proposal key")
    return tuple(summaries)
