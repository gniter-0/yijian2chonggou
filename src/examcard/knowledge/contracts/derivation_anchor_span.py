"""Step 2A anchor-span selection derivation and its version-shape tables (cm36 split)."""
from __future__ import annotations

from collections.abc import (
    Iterable,
    Mapping,
)
from typing import (
    TYPE_CHECKING,
    Final,
)
from pydantic import BaseModel

from .audits import (
    AnchorSpanSelectionDerivationState,
    Step2AAnchorSpanDerivationResult,
    Step2AAnchorSpanSelectionAudit,
)
from .claims import GroundedNumericClaim
from .proposals import (
    Step2AFactProposal,
    Step2AModelFactProposal,
    Step2AV13ModelFactProposal,
    Step2AV14ModelFactProposal,
    Step2AV15ModelFactProposal,
    Step2AV16ModelFactProposal,
    Step2AV17ModelFactProposal,
    Step2AV18ModelFactProposal,
    Step2AV19ModelFactProposal,
    Step2AV20ModelFactProposal,
    Step2AV21ModelFactProposal,
    Step2AV22ModelFactProposal,
    Step2AV23ModelFactProposal,
)
from .request_helpers import (
    _derive_v13_completeness_mode,
    _derive_v13_source_section_ids,
    _ordered_parent_evidence_ids,
    _step2a_anchor_sources,
)
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
)

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


_MINIMAL_MODEL_PROPOSAL_TYPES: Final = (
    Step2AV13ModelFactProposal,
    Step2AV14ModelFactProposal,
    Step2AV15ModelFactProposal,
    Step2AV16ModelFactProposal,
    Step2AV17ModelFactProposal,
    Step2AV18ModelFactProposal,
    Step2AV19ModelFactProposal,
    Step2AV20ModelFactProposal,
    Step2AV21ModelFactProposal,
    Step2AV22ModelFactProposal,
    Step2AV23ModelFactProposal,
)


_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES: Final = (
    Step2AModelFactProposal,
    Step2AV13ModelFactProposal,
    Step2AV14ModelFactProposal,
)


_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES: Final = (
    Step2AModelFactProposal,
    Step2AV13ModelFactProposal,
    Step2AV14ModelFactProposal,
    Step2AV15ModelFactProposal,
)


_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION: Final[Mapping[str, type[BaseModel]]] = {
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION: Step2AV13ModelFactProposal,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION: Step2AV14ModelFactProposal,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION: Step2AV15ModelFactProposal,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION: Step2AV16ModelFactProposal,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION: Step2AV17ModelFactProposal,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION: Step2AV18ModelFactProposal,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION: Step2AV19ModelFactProposal,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION: Step2AV20ModelFactProposal,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION: Step2AV21ModelFactProposal,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION: Step2AV22ModelFactProposal,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION: Step2AV23ModelFactProposal,
}


def derive_step2a_anchor_span_selection(
    *,
    request: Step2ARequest,
    proposals: Iterable[
        Step2AModelFactProposal
        | Step2AV13ModelFactProposal
        | Step2AV14ModelFactProposal
        | Step2AV15ModelFactProposal
        | Step2AV16ModelFactProposal
        | Step2AV17ModelFactProposal
        | Step2AV18ModelFactProposal
        | Step2AV19ModelFactProposal
        | Step2AV20ModelFactProposal
        | Step2AV21ModelFactProposal
        | Step2AV22ModelFactProposal
        | Step2AV23ModelFactProposal
    ],
) -> Step2AAnchorSpanDerivationResult:
    """Map model-selected child span IDs to immutable parent Evidence locally.

    The model cannot select parent Evidence. V15+ has one exhaustive selected
    role, so parent-role collision is structurally unrepresentable there.
    Unknown, out-of-section, duplicate, and incomplete selections remain
    auditable failures and never create a canonical Fact proposal.
    """

    spans_by_id, table_cell_span_ids, section_ids, span_sections = _step2a_anchor_sources(request)
    canonical: list[Step2AFactProposal] = []
    audits: list[Step2AAnchorSpanSelectionAudit] = []
    for proposal in proposals:
        # 形状校验：请求版本与提案类必须一对一咬合。先判 None 再 isinstance，mypy 由此不再
        # 需要 arg-type 豁免；下面 5 个版本相关字段各自就地 isinstance 窄化，proposal 全程
        # 保持 12 元 union 受检（cm30 之前这里靠一条 arg-type ignore 把 proposal 退化成 Any，
        # 循环体内 51 条 union-attr 因此全部失检）。
        minimal_type = _MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION.get(
            request.identity.request_version
        )
        is_minimal = minimal_type is not None
        if minimal_type is not None and not isinstance(proposal, minimal_type):
            raise ValueError("Step 2A model proposal shape does not match its immutable request version")
        if minimal_type is None and isinstance(proposal, _MINIMAL_MODEL_PROPOSAL_TYPES):
            raise ValueError("Step 2A model proposal shape does not match its immutable request version")
        secondary_anchor_span_ids = (
            proposal.secondary_anchor_span_ids
            if isinstance(proposal, _DUAL_ROLE_ANCHOR_PROPOSAL_TYPES)
            else ()
        )
        proposal_key = (
            proposal.derived_proposal_key
            if isinstance(proposal, _MINIMAL_MODEL_PROPOSAL_TYPES)
            else proposal.proposal_key
        )
        selected_span_ids = tuple(dict.fromkeys((*proposal.primary_anchor_span_ids, *secondary_anchor_span_ids)))
        unknown_ids = tuple(span_id for span_id in selected_span_ids if span_id not in spans_by_id)
        if unknown_ids:
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.UNKNOWN_SPAN,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="one or more model-selected anchor spans are absent from the immutable projection",
                )
            )
            continue
        source_section_ids = (
            _derive_v13_source_section_ids(request, selected_span_ids)
            if isinstance(proposal, _MINIMAL_MODEL_PROPOSAL_TYPES)
            else proposal.source_section_ids
        )
        declared_sections = set(source_section_ids)
        if not is_minimal and (
            not declared_sections <= section_ids
            or any(not (span_sections[span_id] & declared_sections) for span_id in selected_span_ids)
        ):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.OUT_OF_SECTION,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="a selected anchor span is not available from the proposal's declared source section",
                )
            )
            continue
        primary_parent_ids = _ordered_parent_evidence_ids(proposal.primary_anchor_span_ids, spans_by_id)
        secondary_parent_ids = _ordered_parent_evidence_ids(secondary_anchor_span_ids, spans_by_id)
        if set(primary_parent_ids) & set(secondary_parent_ids):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.PARENT_ROLE_COLLISION,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="one immutable parent Evidence cannot be primary and secondary through different selected child spans",
                )
            )
            continue
        if isinstance(proposal, _MINIMAL_MODEL_PROPOSAL_TYPES):
            completeness_mode, completeness_basis = _derive_v13_completeness_mode(
                request=request,
                fact_type=proposal.fact_type,
                selected_span_ids=selected_span_ids,
            )
            if completeness_mode is None:
                audits.append(
                    Step2AAnchorSpanSelectionAudit(
                        proposal_key=proposal_key,
                        state=AnchorSpanSelectionDerivationState.COMPLETENESS_AMBIGUOUS,
                        primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                        secondary_anchor_span_ids=secondary_anchor_span_ids,
                        basis=completeness_basis,
                    )
                )
                continue
        else:
            completeness_mode = proposal.completeness_mode
            completeness_basis = "model-declared legacy completeness retained under the pre-V13 contract"
        model_numeric_claims = (
            proposal.numeric_claims
            if isinstance(proposal, _MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES)
            else ()
        )
        numeric_span_ids = tuple(claim.anchor_span_id for claim in model_numeric_claims)
        if any(span_id not in selected_span_ids for span_id in numeric_span_ids):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.NUMERIC_SPAN_UNSELECTED,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="a numeric claim references an anchor span not selected by the same Fact",
                )
            )
            continue
        table_span_ids = tuple(span_id for span_id in selected_span_ids if span_id in table_cell_span_ids)
        table_parent_ids = _ordered_parent_evidence_ids(table_span_ids, spans_by_id)
        canonical.append(
            Step2AFactProposal(
                proposal_key=proposal_key,
                source_section_ids=source_section_ids,
                fact_type=proposal.fact_type,
                label=proposal.label,
                assertion=proposal.assertion,
                completeness_mode=completeness_mode,
                primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                secondary_anchor_span_ids=secondary_anchor_span_ids,
                primary_evidence_ids=primary_parent_ids,
                secondary_evidence_ids=secondary_parent_ids,
                table_cell_anchor_span_ids=table_span_ids,
                table_cell_evidence_ids=table_parent_ids,
                numeric_claims=tuple(
                    GroundedNumericClaim(
                        value=claim.value,
                        anchor_span_id=claim.anchor_span_id,
                        evidence_id=spans_by_id[claim.anchor_span_id].parent_evidence_id,
                    )
                    for claim in model_numeric_claims
                ),
            )
        )
        audits.append(
            Step2AAnchorSpanSelectionAudit(
                proposal_key=proposal_key,
                state=AnchorSpanSelectionDerivationState.DERIVED,
                primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                secondary_anchor_span_ids=secondary_anchor_span_ids,
                primary_evidence_ids=primary_parent_ids,
                secondary_evidence_ids=secondary_parent_ids,
                table_cell_anchor_span_ids=table_span_ids,
                table_cell_evidence_ids=table_parent_ids,
                basis=(
                    "all selected spans are projection-owned; proposal ID, source sections, parent Evidence, and "
                    f"completeness were derived locally ({completeness_basis})"
                    if is_minimal
                    else "all model-selected child spans are projection-owned and parent Evidence IDs were derived locally"
                ),
            )
        )
    return Step2AAnchorSpanDerivationResult(proposals=tuple(canonical), audits=tuple(audits))
