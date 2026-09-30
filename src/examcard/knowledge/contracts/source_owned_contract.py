"""Skeleton boundary for arbitrary-PDF source-owned Step 2A extraction."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from pydantic import Field, model_validator

from ...evidence.schema import EvidenceKind
from ...materials.schema import StrictModel
from ..schema import FactType

from .anchors import EvidenceAnchorSpan
from .claims import GroundedNumericClaim
from .enums import NumericTokenClass
from .hashing import _canonical_sha256
from .numeric_tokens import classify_numeric_tokens
from .proposals import Step2AProductionSourceOwnedArbitraryPDFModelFactProposal
from .request_identity import Step2RequestIdentity
from .versions import (
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
)

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


SourceOwnedModelProposal: TypeAlias = Step2AProductionSourceOwnedArbitraryPDFModelFactProposal
SourceOwnedTableAtomicDerivationStatus: TypeAlias = Literal[
    "derived",
    "failed_closed",
    "unsupported_composition",
]
SourceOwnedAnchorSelectionStatus: TypeAlias = Literal[
    "selected",
    "no_matching_anchor",
    "ambiguous_anchor",
    "failed_closed",
]
CanonicalFactAssemblyStatus: TypeAlias = Literal["assembled", "failed_closed"]


class SourceOwnedAnchorSelectionResult(StrictModel):
    """Deterministic anchor selection output without source content authority."""

    selected_anchor_span_ids: tuple[str, ...] = ()
    anchor_selection_status: SourceOwnedAnchorSelectionStatus


class SourceOwnedCanonicalProposal(StrictModel):
    """Minimal local hand-off shape before provenance and numeric derivation.

    This is intentionally not ``Step2AFactProposal``.  The full canonical Fact
    requires locally-derived Evidence, table, numeric, and condition fields;
    this boundary carries only the semantic proposal and its selected anchors.
    """

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_anchor_selection(self) -> "SourceOwnedCanonicalProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("source-owned canonical proposals require selected primary anchors")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("source-owned canonical proposal anchors must be unique")
        return self

    @classmethod
    def from_model_proposal(
        cls, proposal: SourceOwnedModelProposal
    ) -> "SourceOwnedCanonicalProposal":
        return cls(
            proposal_key=proposal.derived_proposal_key,
            fact_type=proposal.fact_type,
            label=proposal.label,
            assertion=proposal.assertion,
            primary_anchor_span_ids=proposal.primary_anchor_span_ids,
        )


def canonicalize_source_owned_model_proposals(
    model_proposals: tuple[SourceOwnedModelProposal, ...],
) -> tuple[SourceOwnedCanonicalProposal, ...]:
    """Convert semantic model proposals into the minimal local boundary.

    No numeric, table, grounding, Evidence, or structural derivation occurs at
    this seam.  Those fields remain outside this skeleton until the production
    integration phase.
    """

    canonical = tuple(SourceOwnedCanonicalProposal.from_model_proposal(proposal) for proposal in model_proposals)
    keys = tuple(proposal.proposal_key for proposal in canonical)
    if len(keys) != len(set(keys)):
        raise ValueError("source-owned canonical proposals require unique local identities")
    return canonical


def _candidate_anchor_map(
    candidate_anchor_spans: object,
) -> dict[str, EvidenceAnchorSpan] | None:
    """Validate candidate container shape without inspecting source text."""

    if isinstance(candidate_anchor_spans, Mapping):
        candidates = tuple(candidate_anchor_spans.items())
        result: dict[str, EvidenceAnchorSpan] = {}
        for candidate_id, span in candidates:
            if not isinstance(candidate_id, str) or not isinstance(span, EvidenceAnchorSpan):
                return None
            if span.anchor_span_id != candidate_id or candidate_id in result:
                return None
            result[candidate_id] = span
        return result

    if not isinstance(candidate_anchor_spans, Iterable):
        return None
    candidates = tuple(candidate_anchor_spans)
    result = {}
    for span in candidates:
        if not isinstance(span, EvidenceAnchorSpan) or span.anchor_span_id in result:
            return None
        result[span.anchor_span_id] = span
    return result


def source_owned_select_anchor_spans(
    *,
    proposal: SourceOwnedCanonicalProposal,
    candidate_anchor_spans: Mapping[str, EvidenceAnchorSpan] | Iterable[EvidenceAnchorSpan],
) -> SourceOwnedAnchorSelectionResult:
    """Select only proposal-declared anchors that exist in local candidates.

    The selector is deliberately an identity join. It does not inspect or
    compare assertion text, numeric values, or source text, and it does not
    manufacture a selection when the proposal is empty, duplicated, or
    inconsistent with the candidate metadata.
    """

    if not isinstance(proposal, SourceOwnedCanonicalProposal):
        raise ValueError("source-owned anchor selection requires a canonical source-owned proposal")

    raw_anchor_ids = proposal.primary_anchor_span_ids
    if not isinstance(raw_anchor_ids, (tuple, list)) or any(
        not isinstance(anchor_span_id, str) for anchor_span_id in raw_anchor_ids
    ):
        return SourceOwnedAnchorSelectionResult(anchor_selection_status="failed_closed")
    requested_anchor_ids = tuple(raw_anchor_ids)
    if not requested_anchor_ids:
        return SourceOwnedAnchorSelectionResult(anchor_selection_status="no_matching_anchor")
    if len(requested_anchor_ids) != len(set(requested_anchor_ids)):
        return SourceOwnedAnchorSelectionResult(anchor_selection_status="ambiguous_anchor")

    candidates = _candidate_anchor_map(candidate_anchor_spans)
    if candidates is None or any(anchor_span_id not in candidates for anchor_span_id in requested_anchor_ids):
        return SourceOwnedAnchorSelectionResult(anchor_selection_status="failed_closed")

    return SourceOwnedAnchorSelectionResult(
        selected_anchor_span_ids=requested_anchor_ids,
        anchor_selection_status="selected",
    )


class SourceOwnedLocalDerivedFacts(StrictModel):
    """Local-only anchor derivation state for the source-owned vertical slice."""

    anchor_count: int = Field(ge=0)
    derived_from_local_anchor: Literal[True] = True
    source_anchor_count: int = Field(ge=0, default=0)
    local_extraction_status: Literal["derived", "no_derivation", "failed_closed"] = "no_derivation"


class SourceOwnedLocalDerivationResult(StrictModel):
    """Typed local result for source-owned derivation lanes."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]
    derived_facts: SourceOwnedLocalDerivedFacts
    grounded_numeric_claims: tuple[GroundedNumericClaim, ...] = ()
    numeric_derivation_status: Literal["derived", "no_quantitative_tokens", "failed_closed"] = "no_quantitative_tokens"
    table_atomic_derivation_status: SourceOwnedTableAtomicDerivationStatus | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_result_anchor_selection(self) -> "SourceOwnedLocalDerivationResult":
        if not self.primary_anchor_span_ids:
            raise ValueError("source-owned local results require selected primary anchors")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("source-owned local result anchors must be unique")
        if self.derived_facts.anchor_count != len(self.primary_anchor_span_ids):
            raise ValueError("source-owned local result anchor count must match selected anchors")
        if self.derived_facts.source_anchor_count > self.derived_facts.anchor_count:
            raise ValueError("source-owned local result source anchor count cannot exceed selected anchors")
        if (
            self.derived_facts.local_extraction_status == "derived"
            and self.derived_facts.source_anchor_count != self.derived_facts.anchor_count
        ):
            raise ValueError("derived source-owned local results require every selected anchor")
        selected_anchor_ids = set(self.primary_anchor_span_ids)
        if any(claim.anchor_span_id not in selected_anchor_ids for claim in self.grounded_numeric_claims):
            raise ValueError("source-owned numeric claims must use selected primary anchors")
        if self.table_atomic_derivation_status == "derived" and not self.grounded_numeric_claims:
            raise ValueError("derived source-owned table results require grounded claims")
        if self.table_atomic_derivation_status in {"failed_closed", "unsupported_composition"} and self.grounded_numeric_claims:
            raise ValueError("non-derived source-owned table results cannot contain grounded claims")
        if self.numeric_derivation_status == "derived" and not self.grounded_numeric_claims:
            raise ValueError("derived source-owned numeric results require grounded claims")
        if (
            self.numeric_derivation_status != "derived"
            and self.table_atomic_derivation_status != "derived"
            and self.grounded_numeric_claims
        ):
            raise ValueError("non-derived source-owned numeric results cannot contain grounded claims")
        return self


def _source_owned_canonical_fact_id(
    *,
    proposal_key: str,
    fact_type: FactType,
    anchor_span_ids: tuple[str, ...],
) -> str:
    """Build a stable local fact identity from contract-owned identifiers only."""

    digest = _canonical_sha256(
        {
            "proposal_key": proposal_key,
            "fact_type": fact_type.value,
            "anchor_span_ids": list(anchor_span_ids),
        }
    )
    return f"fact:{digest[:24]}"


class SourceOwnedCanonicalFact(StrictModel):
    """Canonical source-owned fact assembled from local derivation outputs."""

    fact_id: str = Field(pattern=r"^fact:[0-9a-f]{24}$")
    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    anchor_span_ids: tuple[str, ...] = ()
    grounded_numeric_claims: tuple[GroundedNumericClaim, ...] = ()
    derivation_status: CanonicalFactAssemblyStatus

    @model_validator(mode="after")
    def validate_canonical_fact(self) -> "SourceOwnedCanonicalFact":
        expected_fact_id = _source_owned_canonical_fact_id(
            proposal_key=self.proposal_key,
            fact_type=self.fact_type,
            anchor_span_ids=self.anchor_span_ids,
        )
        if self.fact_id != expected_fact_id:
            raise ValueError("source-owned canonical fact ID must be locally deterministic")
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("source-owned canonical fact anchors must be unique")
        if any(claim.anchor_span_id not in self.anchor_span_ids for claim in self.grounded_numeric_claims):
            raise ValueError("source-owned canonical numeric claims must use canonical fact anchors")
        if self.derivation_status == "assembled" and not self.anchor_span_ids:
            raise ValueError("assembled source-owned canonical facts require anchors")
        if self.derivation_status == "failed_closed" and self.grounded_numeric_claims:
            raise ValueError("failed source-owned canonical facts cannot contain grounded claims")
        return self


def _make_source_owned_local_result(
    *,
    proposal: SourceOwnedCanonicalProposal,
    source_anchor_count: int,
    local_extraction_status: Literal["derived", "no_derivation", "failed_closed"],
    grounded_numeric_claims: tuple[GroundedNumericClaim, ...] = (),
    numeric_derivation_status: Literal["derived", "no_quantitative_tokens", "failed_closed"] = "no_quantitative_tokens",
) -> SourceOwnedLocalDerivationResult:
    return SourceOwnedLocalDerivationResult(
        proposal_key=proposal.proposal_key,
        fact_type=proposal.fact_type,
        label=proposal.label,
        assertion=proposal.assertion,
        primary_anchor_span_ids=proposal.primary_anchor_span_ids,
        derived_facts=SourceOwnedLocalDerivedFacts(
            anchor_count=len(proposal.primary_anchor_span_ids),
            source_anchor_count=source_anchor_count,
            local_extraction_status=local_extraction_status,
            derived_from_local_anchor=True,
        ),
        grounded_numeric_claims=grounded_numeric_claims,
        numeric_derivation_status=numeric_derivation_status,
    )


def source_owned_local_derivation(
    *, proposal: SourceOwnedCanonicalProposal
) -> SourceOwnedLocalDerivationResult:
    """Derive only local anchor metadata from one canonical proposal.

    The function deliberately has no provider, Evidence mutation, numeric
    parser, table reconstruction, fuzzy matcher, grounding validator, or card
    construction dependency.  Its result is a sealed hand-off for later local
    derivation phases.
    """

    if not isinstance(proposal, SourceOwnedCanonicalProposal):
        raise ValueError("source-owned local derivation requires a canonical source-owned proposal")
    return _make_source_owned_local_result(
        proposal=proposal,
        source_anchor_count=0,
        local_extraction_status="no_derivation",
    )


def source_owned_derive_from_evidence(
    *,
    proposal: SourceOwnedCanonicalProposal,
    evidence_spans: Mapping[str, EvidenceAnchorSpan],
) -> SourceOwnedLocalDerivationResult:
    """Derive a closed local result from explicitly supplied Evidence anchors.

    Only anchor identity metadata is inspected here.  The anchor's source text
    is intentionally not read or copied, and the mapping is never written
    back to Evidence or included in the result.  A missing, mismatched, or
    incorrectly typed entry fails closed while preserving the selected-anchor
    shape for later diagnostics.
    """

    if not isinstance(proposal, SourceOwnedCanonicalProposal):
        raise ValueError("source-owned evidence derivation requires a canonical source-owned proposal")
    if not isinstance(evidence_spans, Mapping):
        raise ValueError("source-owned evidence derivation requires local Evidence span metadata")

    source_anchor_count = 0
    for anchor_span_id in proposal.primary_anchor_span_ids:
        span = evidence_spans.get(anchor_span_id)
        if not isinstance(span, EvidenceAnchorSpan) or span.anchor_span_id != anchor_span_id:
            return _make_source_owned_local_result(
                proposal=proposal,
                source_anchor_count=source_anchor_count,
                local_extraction_status="failed_closed",
                numeric_derivation_status="failed_closed",
            )
        source_anchor_count += 1

    return _make_source_owned_local_result(
        proposal=proposal,
        source_anchor_count=source_anchor_count,
        local_extraction_status="derived",
    )


def source_owned_derive_atomic_numeric_claims(
    *,
    proposal: SourceOwnedCanonicalProposal,
    evidence_spans: Mapping[str, EvidenceAnchorSpan],
) -> SourceOwnedLocalDerivationResult:
    """Derive atomic numeric claims from locally-owned selected Evidence spans.

    This lane intentionally mirrors the existing deterministic atomic contract:
    complete quantitative token values must match exactly, and one assertion
    token must resolve to exactly one selected source candidate.  Structural
    numeric classes and complete-table composition remain outside this slice.
    """

    local = source_owned_derive_from_evidence(proposal=proposal, evidence_spans=evidence_spans)
    if local.derived_facts.local_extraction_status != "derived":
        return local.model_copy(update={"numeric_derivation_status": "failed_closed"})
    if proposal.fact_type == FactType.COMPOSITION:
        return local.model_copy(update={"numeric_derivation_status": "failed_closed"})

    source_candidates = tuple(
        (token.value, span.anchor_span_id, span.parent_evidence_id)
        for anchor_span_id in proposal.primary_anchor_span_ids
        for span in (evidence_spans[anchor_span_id],)
        for token in classify_numeric_tokens(span.canonical_text)
        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
    )
    assertion_tokens = tuple(
        token
        for token in classify_numeric_tokens(proposal.assertion)
        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
    )
    if not assertion_tokens:
        return local.model_copy(update={"numeric_derivation_status": "no_quantitative_tokens"})

    claims: list[GroundedNumericClaim] = []
    for assertion_token in assertion_tokens:
        candidates = tuple(candidate for candidate in source_candidates if candidate[0] == assertion_token.value)
        if len(candidates) != 1:
            return local.model_copy(update={"numeric_derivation_status": "failed_closed"})
        value, anchor_span_id, evidence_id = candidates[0]
        claim = GroundedNumericClaim(
            value=value,
            anchor_span_id=anchor_span_id,
            evidence_id=evidence_id,
        )
        if claim not in claims:
            claims.append(claim)

    return local.model_copy(
        update={
            "grounded_numeric_claims": tuple(claims),
            "numeric_derivation_status": "derived",
        }
    )


def source_owned_derive_table_atomic_numeric_claims(
    *,
    proposal: SourceOwnedCanonicalProposal,
    evidence_spans: Mapping[str, EvidenceAnchorSpan],
) -> SourceOwnedLocalDerivationResult:
    """Derive one exact quantitative claim per selected table-cell anchor.

    This lane treats the selected anchors as the complete local cell input. It
    does not reconstruct a table, infer row/column relationships, or use the
    model assertion as numeric authority. Each selected anchor must contain
    exactly one quantitative token; repeated values across anchors are
    rejected because this slice has no safe cell disambiguation contract.
    """

    local = source_owned_derive_from_evidence(proposal=proposal, evidence_spans=evidence_spans)
    if local.derived_facts.local_extraction_status != "derived":
        return local.model_copy(update={"table_atomic_derivation_status": "failed_closed"})
    if proposal.fact_type == FactType.COMPOSITION:
        return local.model_copy(update={"table_atomic_derivation_status": "unsupported_composition"})

    claims: list[GroundedNumericClaim] = []
    values_seen: set[str] = set()
    for anchor_span_id in proposal.primary_anchor_span_ids:
        span = evidence_spans[anchor_span_id]
        if (
            span.parent_evidence_kind != EvidenceKind.TABLE_CELL
            or not span.parent_evidence_id
            or span.policy_decision.evidence_id != span.parent_evidence_id
        ):
            return local.model_copy(update={"table_atomic_derivation_status": "failed_closed"})

        quantitative_tokens = tuple(
            token
            for token in classify_numeric_tokens(span.canonical_text)
            if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
        )
        if len(quantitative_tokens) != 1:
            return local.model_copy(update={"table_atomic_derivation_status": "failed_closed"})

        token = quantitative_tokens[0]
        if token.value in values_seen:
            return local.model_copy(update={"table_atomic_derivation_status": "failed_closed"})
        values_seen.add(token.value)
        claims.append(
            GroundedNumericClaim(
                value=token.value,
                anchor_span_id=span.anchor_span_id,
                evidence_id=span.parent_evidence_id,
            )
        )

    if not claims:
        return local.model_copy(update={"table_atomic_derivation_status": "failed_closed"})
    return local.model_copy(
        update={
            "grounded_numeric_claims": tuple(claims),
            "table_atomic_derivation_status": "derived",
        }
    )


def _source_owned_failed_canonical_fact(
    *, proposal: SourceOwnedCanonicalProposal
) -> SourceOwnedCanonicalFact:
    """Return a typed failed hand-off without admitting local claims."""

    anchor_span_ids = proposal.primary_anchor_span_ids
    return SourceOwnedCanonicalFact(
        fact_id=_source_owned_canonical_fact_id(
            proposal_key=proposal.proposal_key,
            fact_type=proposal.fact_type,
            anchor_span_ids=anchor_span_ids,
        ),
        proposal_key=proposal.proposal_key,
        fact_type=proposal.fact_type,
        label=proposal.label,
        assertion=proposal.assertion,
        anchor_span_ids=anchor_span_ids,
        grounded_numeric_claims=(),
        derivation_status="failed_closed",
    )


def _source_owned_derivation_is_acceptable(result: SourceOwnedLocalDerivationResult) -> bool:
    """Accept only complete local derivation states for canonical assembly."""

    if result.derived_facts.local_extraction_status != "derived":
        return False
    if result.numeric_derivation_status == "failed_closed":
        return False
    if result.table_atomic_derivation_status in {"failed_closed", "unsupported_composition"}:
        return False
    if result.table_atomic_derivation_status == "derived":
        return bool(result.grounded_numeric_claims)
    return result.numeric_derivation_status in {"derived", "no_quantitative_tokens"}


def source_owned_assemble_canonical_fact(
    *,
    proposal: SourceOwnedCanonicalProposal,
    anchor_selection: SourceOwnedAnchorSelectionResult,
    derivation_result: SourceOwnedLocalDerivationResult,
) -> SourceOwnedCanonicalFact:
    """Assemble one canonical fact from local authority boundaries.

    The function only accepts the exact selected-anchor and proposal identity
    carried by the local derivation result. Model-owned numeric or provenance
    fields are not read; numeric claims are copied only from that local result.
    Any incomplete or inconsistent boundary fails closed.
    """

    if not isinstance(proposal, SourceOwnedCanonicalProposal):
        raise ValueError("canonical fact assembly requires a canonical source-owned proposal")
    if not isinstance(anchor_selection, SourceOwnedAnchorSelectionResult):
        raise ValueError("canonical fact assembly requires source-owned anchor selection")
    if not isinstance(derivation_result, SourceOwnedLocalDerivationResult):
        raise ValueError("canonical fact assembly requires source-owned local derivation")

    proposal_identity_matches = (
        derivation_result.proposal_key == proposal.proposal_key
        and derivation_result.fact_type == proposal.fact_type
        and derivation_result.label == proposal.label
        and derivation_result.assertion == proposal.assertion
        and derivation_result.primary_anchor_span_ids == proposal.primary_anchor_span_ids
    )
    selection_matches = (
        anchor_selection.anchor_selection_status == "selected"
        and anchor_selection.selected_anchor_span_ids == proposal.primary_anchor_span_ids
    )
    if not selection_matches or not proposal_identity_matches or not _source_owned_derivation_is_acceptable(derivation_result):
        return _source_owned_failed_canonical_fact(proposal=proposal)

    anchor_span_ids = proposal.primary_anchor_span_ids
    return SourceOwnedCanonicalFact(
        fact_id=_source_owned_canonical_fact_id(
            proposal_key=proposal.proposal_key,
            fact_type=proposal.fact_type,
            anchor_span_ids=anchor_span_ids,
        ),
        proposal_key=proposal.proposal_key,
        fact_type=proposal.fact_type,
        label=proposal.label,
        assertion=proposal.assertion,
        anchor_span_ids=anchor_span_ids,
        grounded_numeric_claims=derivation_result.grounded_numeric_claims,
        derivation_status="assembled",
    )


class Step2AContractProfile(StrEnum):
    """Explicit contract selection; no legacy boolean flags are implied."""

    LEGACY_V10 = "legacy_v10"
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF = "production_source_owned_arbitrary_pdf_v1"


class Step2AExtractionPath(StrEnum):
    """Document-extraction dispatch selected only from immutable request identity."""

    EXISTING_VERSIONED = "existing_versioned"
    SOURCE_OWNED_ARBITRARY_PDF = "source_owned_arbitrary_pdf"


class Step2AResponseHandlingMode(StrEnum):
    """Response dispatch mode reserved for future adapter integration."""

    EXISTING_VERSIONED = "existing_versioned"
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF = "production_source_owned_arbitrary_pdf"


def step2a_request_version_for_profile(profile: Step2AContractProfile) -> str:
    """Resolve an explicit profile without changing the existing default path."""

    if profile == Step2AContractProfile.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF:
        return PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    if profile == Step2AContractProfile.LEGACY_V10:
        return STEP2A_REQUEST_VERSION
    raise ValueError(f"unsupported Step 2A contract profile: {profile}")


def step2a_response_handling_mode(request_version: str) -> Step2AResponseHandlingMode:
    """Return the future adapter dispatch mode for an immutable request version."""

    if request_version == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION:
        return Step2AResponseHandlingMode.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF
    return Step2AResponseHandlingMode.EXISTING_VERSIONED


def select_step2a_extraction_path(identity: Step2RequestIdentity) -> Step2AExtractionPath:
    """Select extraction ownership from request identity, never response content."""

    if identity.request_version == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION:
        return Step2AExtractionPath.SOURCE_OWNED_ARBITRARY_PDF
    return Step2AExtractionPath.EXISTING_VERSIONED


class SourceOwnedStep2ACanonicalizer(Protocol):
    """Model-response to the minimal source-owned canonical boundary."""

    def canonicalize(
        self,
        *,
        request: Step2ARequest,
        model_proposals: tuple[SourceOwnedModelProposal, ...],
    ) -> tuple[SourceOwnedCanonicalProposal, ...]: ...
