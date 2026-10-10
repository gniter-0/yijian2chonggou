"""Ground Step 2A proposals against immutable document Evidence.

The model is allowed to propose a semantic type, label, and source anchors.  It
is not allowed to author the final card content.  This module resolves the
canonical proposal's Evidence IDs, reconstructs the assertion from source
Evidence, and delegates the actual Fact/Card schema assembly to the existing
deterministic builders.

Primary ownership is exclusive with one deliberate exception for tables.  The
Step 2A v10 table contract requires a complete-table ``composition`` Fact *and*
a separate atomic ``threshold``/``condition`` Fact that selects the rule cell as
primary.  A table cell may therefore have at most one composition owner plus
any number of atomic owners (one cell can hold several numbered rules, and the
Step 2A audit already requires exactly one atomic Fact per detected rule).
Every other overlap still fails closed.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..evidence.schema import DocumentEvidenceManifest, EvidenceKind, EvidenceUnit
from ..materials.schema import ContentPolicy, PolicyDisposition

from .contracts import NumericTokenClass, Step2AFactProposal, classify_numeric_tokens
from .fact_build import _make_card, _make_fact
from .fact_policy import decide_policy
from .fact_render import _group_text, _render_evidence_map
from .fact_types import FactBundle
from .identity import make_fact_id
from .schema import AtomicFact, CardPlan, CompletenessMode, FactType, KnowledgeCard

_TABLE_EVIDENCE_KINDS = frozenset({EvidenceKind.TABLE, EvidenceKind.TABLE_ROW, EvidenceKind.TABLE_CELL})
# The only Fact types a Step 2A table atomic sub-fact requirement can demand
# (TableAtomicSubfactRequirement.expected_fact_type).
_TABLE_ATOMIC_FACT_TYPES = frozenset({FactType.THRESHOLD, FactType.CONDITION})
_PRIMARY_ROLE_FACT = "fact"
_PRIMARY_ROLE_TABLE_COMPOSITION = "table_composition"
_PRIMARY_ROLE_TABLE_ATOMIC = "table_atomic"


@dataclass(frozen=True)
class GroundedFactCard:
    """One source-grounded Fact and its deterministic Card projection."""

    fact: AtomicFact
    card_plan: CardPlan
    card: KnowledgeCard
    proposal_key: str


def _compact_numeric_text(value: str) -> str:
    return (
        "".join(value.split())
        .replace("％", "%")
        .replace("．", ".")
        .replace("／", "/")
    )


def _validate_numeric_grounding(*, proposal: Step2AFactProposal, units: tuple[EvidenceUnit, ...]) -> None:
    source = _compact_numeric_text("\n".join(unit.canonical_text for unit in units))
    for token in classify_numeric_tokens(proposal.assertion):
        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE:
            if _compact_numeric_text(token.value) not in source:
                raise ValueError("numeric proposal claim is not present in selected source Evidence")
    for claim in proposal.numeric_claims:
        if claim.evidence_id not in {unit.evidence_id for unit in units}:
            raise ValueError("numeric proposal claim references unselected Evidence")
        if _compact_numeric_text(claim.value) not in source:
            raise ValueError("numeric proposal claim is not present in selected source Evidence")


def _validate_formula_grounding(*, proposal: Step2AFactProposal, units: tuple[EvidenceUnit, ...]) -> None:
    if proposal.fact_type != FactType.FORMULA:
        return
    source = "\n".join(unit.canonical_text for unit in units)
    if EvidenceKind.FORMULA not in {unit.kind for unit in units} and "=" not in source:
        raise ValueError("formula proposal has no formula-bearing source Evidence")


def _primary_role(
    *,
    proposal: Step2AFactProposal,
    primary_units: tuple[EvidenceUnit, ...],
    secondary_units: tuple[EvidenceUnit, ...],
) -> str:
    """Classify how a proposal owns its primary Evidence."""

    if not primary_units or any(unit.kind not in _TABLE_EVIDENCE_KINDS for unit in primary_units):
        return _PRIMARY_ROLE_FACT
    if proposal.fact_type == FactType.COMPOSITION:
        return _PRIMARY_ROLE_TABLE_COMPOSITION
    if (
        proposal.fact_type in _TABLE_ATOMIC_FACT_TYPES
        and proposal.completeness_mode == CompletenessMode.ATOMIC
        and all(unit.kind in _TABLE_EVIDENCE_KINDS for unit in secondary_units)
    ):
        return _PRIMARY_ROLE_TABLE_ATOMIC
    return _PRIMARY_ROLE_FACT


def _check_primary_ownership(
    owners: dict[str, frozenset[str]],
    *,
    primary_ids: tuple[str, ...],
    role: str,
) -> None:
    for evidence_id in primary_ids:
        existing = owners.get(evidence_id, frozenset())
        if not existing:
            continue
        if (
            role == _PRIMARY_ROLE_FACT
            or _PRIMARY_ROLE_FACT in existing
            or (role == _PRIMARY_ROLE_TABLE_COMPOSITION and _PRIMARY_ROLE_TABLE_COMPOSITION in existing)
        ):
            raise ValueError("one Evidence unit cannot have multiple primary Fact owners")


def _as_table_atomic_fact(*, fact: AtomicFact, proposal: Step2AFactProposal) -> AtomicFact:
    """Re-type a table-cell Fact as the atomic rule the Step 2A contract required.

    ``_make_fact`` types any table-cell selection as a composition.  For a
    validated table atomic proposal the source text stays exactly as rendered
    from Evidence; only the type and completeness follow the contract.
    """

    return AtomicFact.model_validate(
        {
            **fact.model_dump(),
            "fact_type": proposal.fact_type,
            "completeness_mode": CompletenessMode.ATOMIC,
            "ordered_member_ids": (),
        }
    )


def _fact_with_proposal_metadata(
    *,
    proposal: Step2AFactProposal,
    fact: AtomicFact,
    primary_units: tuple[EvidenceUnit, ...],
    secondary_units: tuple[EvidenceUnit, ...],
) -> AtomicFact:
    """Bind proposal roles and recompute the stable Fact ID from primary source."""

    if fact.fact_type != proposal.fact_type:
        raise ValueError("proposal Fact type is not grounded by selected source Evidence")
    if fact.completeness_mode != proposal.completeness_mode:
        raise ValueError("proposal completeness mode is not grounded by selected source Evidence")
    all_units = (*primary_units, *secondary_units)
    selected_ids = {unit.evidence_id for unit in all_units}
    updated = fact.model_copy(
        update={
            "label": proposal.label,
            "primary_evidence_ids": tuple(unit.evidence_id for unit in primary_units),
            "secondary_evidence_ids": tuple(unit.evidence_id for unit in secondary_units),
        }
    )
    assertion_contract = {
        "assertion": updated.assertion,
        "completeness_mode": updated.completeness_mode.value,
        "numbers": [item.model_dump(mode="json") for item in updated.numbers],
        "conditions": list(updated.conditions),
    }
    return updated.model_copy(
        update={
            "fact_id": make_fact_id(
                fact_type=updated.fact_type.value,
                primary_evidence_fingerprints=[unit.fingerprint for unit in primary_units],
                assertion_contract=assertion_contract,
            ),
            "presentation_transforms": tuple(
                transform for transform in fact.presentation_transforms if transform.evidence_id in selected_ids
            ),
        }
    )


def ground_step2a_proposals(
    *,
    manifest: DocumentEvidenceManifest,
    proposals: Iterable[Step2AFactProposal],
    content_policy: ContentPolicy,
    material_profile_id: str | None = None,
    sample_id: str | None = None,
) -> tuple[GroundedFactCard, ...]:
    """Convert canonical Step 2A proposals into source-grounded cards.

    Only Evidence selected by the supplied Content Policy may become a Fact.
    Proposal assertions are used for validation and identity metadata, never as
    the rendered card body; the body is reconstructed from selected Evidence.
    """

    profile_id = material_profile_id or manifest.material_profile_id
    if profile_id != manifest.material_profile_id:
        raise ValueError("material profile does not match the Evidence manifest")
    stable_sample_id = sample_id or manifest.document_id.removeprefix("doc:")
    units = tuple(unit for page in manifest.pages for unit in page.evidence_units)
    by_id = {unit.evidence_id: unit for unit in units}
    if len(by_id) != len(units):
        raise ValueError("document Evidence IDs must be unique")
    decisions = {unit.evidence_id: decide_policy(unit=unit, policy=content_policy) for unit in units}
    rendered = _render_evidence_map(units, content_policy)

    grounded: list[GroundedFactCard] = []
    proposal_keys: set[str] = set()
    primary_owners: dict[str, frozenset[str]] = {}
    for proposal in proposals:
        if proposal.proposal_key in proposal_keys:
            raise ValueError("duplicate Step 2A proposal keys are not accepted")
        proposal_keys.add(proposal.proposal_key)
        primary_ids = tuple(proposal.primary_evidence_ids)
        secondary_ids = tuple(proposal.secondary_evidence_ids)
        if any(evidence_id not in by_id for evidence_id in (*primary_ids, *secondary_ids)):
            raise ValueError("Step 2A proposal references Evidence outside the document manifest")
        primary_units = tuple(by_id[evidence_id] for evidence_id in primary_ids)
        secondary_units = tuple(by_id[evidence_id] for evidence_id in secondary_ids)
        role = _primary_role(proposal=proposal, primary_units=primary_units, secondary_units=secondary_units)
        _check_primary_ownership(primary_owners, primary_ids=primary_ids, role=role)
        all_units = (*primary_units, *secondary_units)
        if any(decisions[unit.evidence_id].disposition != PolicyDisposition.INCLUDED for unit in all_units):
            raise ValueError("Step 2A proposal selects Evidence excluded by Content Policy")
        _validate_numeric_grounding(proposal=proposal, units=all_units)
        _validate_formula_grounding(proposal=proposal, units=all_units)

        # `_make_fact` is intentionally given source units and no model text.
        # Its assertion is therefore a deterministic Evidence rendering.
        source_assertion = _group_text(all_units, rendered)
        if not source_assertion:
            raise ValueError("selected Evidence cannot produce a non-empty Fact assertion")
        fact = _make_fact(units=all_units, rendered=rendered)
        if role == _PRIMARY_ROLE_TABLE_ATOMIC:
            fact = _as_table_atomic_fact(fact=fact, proposal=proposal)
        fact = _fact_with_proposal_metadata(
            proposal=proposal,
            fact=fact,
            primary_units=primary_units,
            secondary_units=secondary_units,
        )
        bundle = FactBundle(fact=fact, units=all_units)
        card_plan, card = _make_card(
            sample_id=stable_sample_id,
            bundle=bundle,
            material_profile_id=profile_id,
            policy=content_policy,
            rendered=rendered,
        )
        grounded.append(
            GroundedFactCard(
                fact=fact,
                card_plan=card_plan,
                card=card,
                proposal_key=proposal.proposal_key,
            )
        )
        for evidence_id in primary_ids:
            primary_owners[evidence_id] = primary_owners.get(evidence_id, frozenset()) | {role}
    return tuple(grounded)


__all__ = ["GroundedFactCard", "ground_step2a_proposals"]
