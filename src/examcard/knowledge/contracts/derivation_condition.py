"""Step 2A condition-claim derivation and span helpers (cm36 split)."""
from __future__ import annotations
import hashlib

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .audits import (
    ConditionClaimDerivationState,
    Step2AConditionClaimAudit,
    Step2AConditionClaimDerivationResult,
)
from .claims import GroundedConditionClaim
from .enums import (
    ConditionOperator,
    _condition_operator_occurrences,
    condition_operators_in_text,
)
from .occurrences import ConditionSpanReference
from .proposals import Step2AFactProposal
from .request_helpers import _step2a_anchor_sources
from ...evidence.layout import normalized_compare_text

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


_CONDITION_SPAN_BOUNDARIES = frozenset("，,；;。！？!?：:\n\r")


@dataclass(frozen=True)
class _ConditionSpanCandidate:
    value: str
    assertion_start: int
    assertion_end: int
    evidence_id: str
    anchor_span_id: str
    parent_anchor_start: int
    source_start: int
    source_end: int
    operator: ConditionOperator


def _condition_span_reference(text: str, start: int, end: int, *, parent_offset: int = 0) -> ConditionSpanReference:
    return ConditionSpanReference(
        start=start + parent_offset,
        end=end + parent_offset,
        sha256=hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
    )


def _complete_condition_span(value: str) -> bool:
    """A condition span must contain more than a recognized operator."""

    operators = condition_operators_in_text(value)
    if not operators:
        return False
    remainder = normalized_compare_text(value)
    for operator in set(operators):
        remainder = remainder.replace(normalized_compare_text(operator.value), "")
    return bool(remainder)


def _exact_common_condition_span(
    *,
    assertion: str,
    assertion_start: int,
    assertion_end: int,
    source_text: str,
    source_start: int,
    source_end: int,
    evidence_id: str,
    anchor_span_id: str,
    parent_anchor_start: int,
    operator: ConditionOperator,
) -> _ConditionSpanCandidate:
    """Find the maximal exact, punctuation-bounded common span around one operator."""

    left_assertion = assertion_start
    left_source = source_start
    while left_assertion > 0 and left_source > 0:
        assertion_char = assertion[left_assertion - 1]
        source_char = source_text[left_source - 1]
        if (
            assertion_char in _CONDITION_SPAN_BOUNDARIES
            or source_char in _CONDITION_SPAN_BOUNDARIES
            or assertion_char != source_char
        ):
            break
        left_assertion -= 1
        left_source -= 1

    right_assertion = assertion_end
    right_source = source_end
    while right_assertion < len(assertion) and right_source < len(source_text):
        assertion_char = assertion[right_assertion]
        source_char = source_text[right_source]
        if (
            assertion_char in _CONDITION_SPAN_BOUNDARIES
            or source_char in _CONDITION_SPAN_BOUNDARIES
            or assertion_char != source_char
        ):
            break
        right_assertion += 1
        right_source += 1

    while left_assertion < right_assertion and assertion[left_assertion].isspace():
        left_assertion += 1
        left_source += 1
    while right_assertion > left_assertion and assertion[right_assertion - 1].isspace():
        right_assertion -= 1
        right_source -= 1
    value = assertion[left_assertion:right_assertion]
    if value != source_text[left_source:right_source]:
        raise ValueError("condition candidate must remain an exact assertion/Evidence span")
    return _ConditionSpanCandidate(
        value=value,
        assertion_start=left_assertion,
        assertion_end=right_assertion,
        evidence_id=evidence_id,
        anchor_span_id=anchor_span_id,
        parent_anchor_start=parent_anchor_start,
        source_start=left_source,
        source_end=right_source,
        operator=operator,
    )


def derive_step2a_condition_claims(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> Step2AConditionClaimDerivationResult:
    """Derive condition claims only from one exact assertion/Evidence span.

    There is deliberately no best-effort selection. A condition is accepted only
    when one complete span is uniquely shared by the assertion and one selected
    projection anchor span. Every other state is retained as non-sensitive audit data and
    is rejected by the Step 2A grounding contract.
    """

    spans_by_id, _, _, _ = _step2a_anchor_sources(request)
    canonical_proposals: list[Step2AFactProposal] = []
    audits: list[Step2AConditionClaimAudit] = []
    for proposal in proposals:
        if proposal.complete_table_semantic_coverages:
            # A complete-table composition Fact preserves every cell through
            # its structured grid. It is not an assertion of each individual
            # row rule, so condition claims remain the responsibility of any
            # separately selected row-level Fact.
            canonical_proposals.append(proposal.model_copy(update={"condition_claims": ()}))
            continue
        selected_anchor_span_ids = tuple(dict.fromkeys((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids)))
        selected_sources = tuple(
            (anchor_span_id, spans_by_id[anchor_span_id])
            for anchor_span_id in selected_anchor_span_ids
            if anchor_span_id in spans_by_id and spans_by_id[anchor_span_id].canonical_text
        )
        assertion_occurrences = _condition_operator_occurrences(proposal.assertion)
        source_occurrences = tuple(
            (anchor_span_id, start, end, operator)
            for anchor_span_id, anchor_span in selected_sources
            for start, end, operator in _condition_operator_occurrences(anchor_span.canonical_text)
        )
        source_occurrences_seen: set[tuple[str, int, int, ConditionOperator]] = set()
        claims: list[GroundedConditionClaim] = []

        for assertion_start, assertion_end, operator in assertion_occurrences:
            raw_candidates: list[_ConditionSpanCandidate] = []
            for anchor_span_id, source_start, source_end, source_operator in source_occurrences:
                if source_operator != operator:
                    continue
                anchor_span = spans_by_id[anchor_span_id]
                candidate = _exact_common_condition_span(
                    assertion=proposal.assertion,
                    assertion_start=assertion_start,
                    assertion_end=assertion_end,
                    source_text=anchor_span.canonical_text,
                    source_start=source_start,
                    source_end=source_end,
                    evidence_id=anchor_span.parent_evidence_id,
                    anchor_span_id=anchor_span_id,
                    parent_anchor_start=anchor_span.start,
                    operator=operator,
                )
                source_occurrences_seen.add((anchor_span_id, source_start, source_end, source_operator))
                raw_candidates.append(candidate)

            candidates = tuple(
                dict.fromkeys(
                    candidate
                    for candidate in raw_candidates
                    if _complete_condition_span(candidate.value)
                )
            )
            assertion_operator_span = _condition_span_reference(proposal.assertion, assertion_start, assertion_end)
            if len(candidates) == 1:
                candidate = candidates[0]
                claim = GroundedConditionClaim(
                    value=candidate.value,
                    evidence_id=candidate.evidence_id,
                    anchor_span_id=candidate.anchor_span_id,
                )
                if claim not in claims:
                    claims.append(claim)
                audits.append(
                    Step2AConditionClaimAudit(
                        proposal_key=proposal.proposal_key,
                        operator=operator,
                        state=ConditionClaimDerivationState.DERIVED,
                        assertion_span=_condition_span_reference(
                            proposal.assertion, candidate.assertion_start, candidate.assertion_end
                        ),
                        evidence_id=candidate.evidence_id,
                        anchor_span_id=candidate.anchor_span_id,
                        source_span=_condition_span_reference(
                            spans_by_id[candidate.anchor_span_id].canonical_text,
                            candidate.source_start,
                            candidate.source_end,
                            parent_offset=candidate.parent_anchor_start,
                        ),
                        derived_claim=claim,
                        basis="one unique punctuation-bounded exact condition span shared by assertion and selected Evidence",
                    )
                )
                continue

            audit_candidates = candidates or tuple(dict.fromkeys(raw_candidates))
            candidate_evidence_ids = tuple(dict.fromkeys(candidate.evidence_id for candidate in audit_candidates))
            candidate_anchor_span_ids = tuple(
                dict.fromkeys(candidate.anchor_span_id for candidate in audit_candidates)
            )
            candidate_span_sha256s = tuple(
                dict.fromkeys(
                    hashlib.sha256(candidate.value.encode("utf-8")).hexdigest() for candidate in audit_candidates
                )
            )
            if candidates:
                state = (
                    ConditionClaimDerivationState.AMBIGUOUS_EVIDENCE
                    if len(candidate_evidence_ids) > 1
                    else ConditionClaimDerivationState.MULTIPLE_CANDIDATE_SPANS
                )
                basis = "more than one exact complete condition candidate matched; no anchor span was selected"
            elif raw_candidates:
                state = ConditionClaimDerivationState.ISOLATED_OPERATOR
                basis = "the only assertion/Evidence overlap was an isolated condition operator"
            else:
                state = ConditionClaimDerivationState.ASSERTION_WITHOUT_EVIDENCE
                basis = "no exact complete condition span matched any selected anchor span"
            audits.append(
                Step2AConditionClaimAudit(
                    proposal_key=proposal.proposal_key,
                    operator=operator,
                    state=state,
                    assertion_span=(
                        _condition_span_reference(
                            proposal.assertion, raw_candidates[0].assertion_start, raw_candidates[0].assertion_end
                        )
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else assertion_operator_span
                    ),
                    evidence_id=(
                        raw_candidates[0].evidence_id
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    anchor_span_id=(
                        raw_candidates[0].anchor_span_id
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    source_span=(
                        _condition_span_reference(
                            spans_by_id[raw_candidates[0].anchor_span_id].canonical_text,
                            raw_candidates[0].source_start,
                            raw_candidates[0].source_end,
                            parent_offset=raw_candidates[0].parent_anchor_start,
                        )
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    candidate_evidence_ids=candidate_evidence_ids,
                    candidate_anchor_span_ids=candidate_anchor_span_ids,
                    candidate_span_sha256s=candidate_span_sha256s,
                    basis=basis,
                )
            )

        for anchor_span_id, source_start, source_end, operator in source_occurrences:
            source_key = (anchor_span_id, source_start, source_end, operator)
            if source_key in source_occurrences_seen:
                continue
            anchor_span = spans_by_id[anchor_span_id]
            audits.append(
                Step2AConditionClaimAudit(
                    proposal_key=proposal.proposal_key,
                    operator=operator,
                    state=ConditionClaimDerivationState.EVIDENCE_WITHOUT_ASSERTION,
                    evidence_id=anchor_span.parent_evidence_id,
                    anchor_span_id=anchor_span_id,
                    source_span=_condition_span_reference(
                        anchor_span.canonical_text,
                        source_start,
                        source_end,
                        parent_offset=anchor_span.start,
                    ),
                    basis="selected anchor span contains a condition operator with no matching assertion operator",
                )
            )
        canonical_proposals.append(proposal.model_copy(update={"condition_claims": tuple(claims)}))
    return Step2AConditionClaimDerivationResult(proposals=tuple(canonical_proposals), audits=tuple(audits))
