"""Evidence-bound source-owned extraction adapter.

This module is the provider-neutral boundary between an explicitly validated
source-owned Step 2A response and the local Evidence collection.  It performs
only the identity join needed to make a semantic proposal eligible for later
local derivation.  Numeric claims, table occurrences, grounding, and
provenance remain downstream concerns.
"""
from __future__ import annotations

from pydantic import model_validator

from ...materials.schema import StrictModel

from .anchors import EvidenceAnchorSpan
from .proposals import Step2AProductionSourceOwnedArbitraryPDFModelFactProposal
from .responses import Step2AProductionSourceOwnedArbitraryPDFResultPayload
from .source_owned_contract import (
    SourceOwnedCanonicalProposal,
    canonicalize_source_owned_model_proposals,
    source_owned_select_anchor_spans,
)


class SourceOwnedEvidenceCollection(StrictModel):
    """The explicit local anchor collection available to extraction.

    The collection is deliberately made of typed ``EvidenceAnchorSpan``
    records, rather than an opaque lookup or a provider response.  It may be
    empty so callers receive a deterministic fail-closed result when a model
    proposal names an unavailable anchor.
    """

    anchor_spans: tuple[EvidenceAnchorSpan, ...] = ()

    @model_validator(mode="after")
    def validate_anchor_collection(self) -> "SourceOwnedEvidenceCollection":
        anchor_ids = tuple(span.anchor_span_id for span in self.anchor_spans)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("source-owned Evidence anchor IDs must be unique")
        return self


def source_owned_extract_document_proposals_from_evidence(
    *,
    evidence: SourceOwnedEvidenceCollection,
    response: Step2AProductionSourceOwnedArbitraryPDFResultPayload,
) -> tuple[SourceOwnedCanonicalProposal, ...]:
    """Bind a validated semantic response to the supplied local Evidence.

    ``response`` is already the source-owned response schema, so forbidden
    model-owned fields fail at that schema boundary before this function is
    entered.  This adapter then performs an exact anchor-ID join.  A missing,
    duplicated, or inconsistent anchor fails closed; no proposal is returned
    in that case.  The returned canonical proposals contain only semantic
    fields plus the local proposal key and selected anchor references.
    """

    if not isinstance(evidence, SourceOwnedEvidenceCollection):
        raise ValueError("source-owned extraction requires a typed Evidence collection")
    if not isinstance(response, Step2AProductionSourceOwnedArbitraryPDFResultPayload):
        raise ValueError("source-owned extraction requires the source-owned response payload")

    model_proposals: tuple[Step2AProductionSourceOwnedArbitraryPDFModelFactProposal, ...] = response.proposals
    canonical = canonicalize_source_owned_model_proposals(model_proposals)
    candidate_anchor_spans = {span.anchor_span_id: span for span in evidence.anchor_spans}

    for proposal in canonical:
        selection = source_owned_select_anchor_spans(
            proposal=proposal,
            candidate_anchor_spans=candidate_anchor_spans,
        )
        if selection.anchor_selection_status != "selected":
            raise ValueError("source-owned extraction anchor selection failed closed")

    return canonical


__all__ = [
    "SourceOwnedEvidenceCollection",
    "source_owned_extract_document_proposals_from_evidence",
]
