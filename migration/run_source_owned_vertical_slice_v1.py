"""Run the source-owned arbitrary-PDF vertical slice entirely offline.

The runner is deliberately a migration seam, not a replacement for the
legacy extraction path.  It loads one existing ``PageEvidence`` fixture,
constructs a local ``EvidenceAnchorSpan`` view, and feeds a schema-validated
fake source-owned response through the source-owned boundaries.  The command
prints only the typed stage summary; source content and fake model payloads
never enter that summary.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

# The migration runner bootstraps the local source tree before importing the
# package, so its imports intentionally follow the guarded sys.path setup.
# ruff: noqa: E402
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from examcard.evidence.schema import EvidenceKind, PageEvidence
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedCanonicalFact,
    SourceOwnedKnowledgeCardBridgeResult,
    SourceOwnedKnowledgeCardCandidate,
    SourceOwnedKnowledgeCardMaterializationInput,
    SourceOwnedKnowledgeCardMaterializationResult,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    make_evidence_anchor_span_id,
    source_owned_admit_card_candidate,
    source_owned_assemble_canonical_fact,
    source_owned_bridge_to_knowledge_card,
    source_owned_derive_atomic_numeric_claims,
    source_owned_fact_to_card_candidate,
    source_owned_materialize_knowledge_card,
    source_owned_select_anchor_spans,
)
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals
from examcard.knowledge.schema import EvidenceRef
from examcard.materials.schema import (
    CoverageRequirement,
    PolicyDecision,
    PolicyDisposition,
    SemanticRole,
    StrictModel,
)

type SourceOwnedVerticalSliceScenario = Literal[
    "success",
    "missing_anchor",
    "numeric_derivation_failure",
    "identity_mismatch",
    "incomplete_materialization",
]
type SourceOwnedVerticalSliceStageStatus = Literal[
    "extracted",
    "failed_closed",
]
type SourceOwnedVerticalSliceDerivationStatus = Literal[
    "derived",
    "no_quantitative_tokens",
    "failed_closed",
]

DEFAULT_EVIDENCE_FIXTURE = (
    REPOSITORY_ROOT
    / "data"
    / "evidence_v2"
    / "golden"
    / "gonglu_phase0_v1"
    / "gonglu_24_table_formula"
    / "pages"
    / "page_0001.json"
)
_FIXTURE_EVIDENCE_ID = "ev:7fdf390ab8741b110562d87d"


class SourceOwnedVerticalSliceSummary(StrictModel):
    """Typed, deliberately minimal output of one offline vertical slice."""

    extraction_status: SourceOwnedVerticalSliceStageStatus
    anchor_selection_status: Literal[
        "selected",
        "no_matching_anchor",
        "ambiguous_anchor",
        "failed_closed",
    ]
    derivation_status: SourceOwnedVerticalSliceDerivationStatus
    assembly_status: Literal["assembled", "failed_closed"]
    admission_status: Literal["admitted", "rejected"]
    bridge_status: Literal["bridged", "failed_closed"]
    materialization_status: Literal["materialized", "failed_closed"]
    knowledge_card_id: str = ""


def _summary(
    *,
    extraction_status: SourceOwnedVerticalSliceStageStatus = "failed_closed",
    anchor_selection_status: Literal[
        "selected",
        "no_matching_anchor",
        "ambiguous_anchor",
        "failed_closed",
    ] = "failed_closed",
    derivation_status: SourceOwnedVerticalSliceDerivationStatus = "failed_closed",
    assembly_status: Literal["assembled", "failed_closed"] = "failed_closed",
    admission_status: Literal["admitted", "rejected"] = "rejected",
    bridge_status: Literal["bridged", "failed_closed"] = "failed_closed",
    materialization_status: Literal["materialized", "failed_closed"] = "failed_closed",
    knowledge_card_id: str = "",
) -> SourceOwnedVerticalSliceSummary:
    return SourceOwnedVerticalSliceSummary(
        extraction_status=extraction_status,
        anchor_selection_status=anchor_selection_status,
        derivation_status=derivation_status,
        assembly_status=assembly_status,
        admission_status=admission_status,
        bridge_status=bridge_status,
        materialization_status=materialization_status,
        knowledge_card_id=knowledge_card_id,
    )


def _load_fixture_page(evidence_fixture: Path) -> PageEvidence:
    """Load the existing immutable Evidence fixture without writing to it."""

    return PageEvidence.model_validate_json(evidence_fixture.read_text(encoding="utf-8"))


def _make_local_anchor(page: PageEvidence) -> tuple[EvidenceAnchorSpan, EvidenceRef]:
    """Build a selectable local anchor view over the fixture's formula unit."""

    unit = next(
        (
            item
            for item in page.evidence_units
            if item.evidence_id == _FIXTURE_EVIDENCE_ID
            and item.kind == EvidenceKind.FORMULA
            and item.canonical_text
        ),
        None,
    )
    if unit is None:
        raise ValueError("expected formula Evidence fixture unit is unavailable")

    exact_text_sha256 = hashlib.sha256(unit.canonical_text.encode("utf-8")).hexdigest()
    source_manifest_sha256 = unit.locator.document_sha256
    content_policy_fingerprint_sha256 = unit.provenance.config_hash
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=unit.evidence_id,
        start=0,
        end=len(unit.canonical_text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint_sha256,
        source_manifest_sha256=source_manifest_sha256,
    )
    policy_decision = unit.policy_decision or PolicyDecision(
        evidence_id=unit.evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        policy_id="core-policy-v1",
        policy_revision=1,
        reason="offline source-owned fixture anchor",
    )
    anchor = EvidenceAnchorSpan(
        anchor_span_id=anchor_span_id,
        parent_evidence_id=unit.evidence_id,
        parent_evidence_kind=unit.kind,
        start=0,
        end=len(unit.canonical_text),
        canonical_text=unit.canonical_text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id=policy_decision.policy_id,
        content_policy_revision=policy_decision.policy_revision,
        content_policy_fingerprint_sha256=content_policy_fingerprint_sha256,
        policy_decision=policy_decision,
        source_manifest_sha256=source_manifest_sha256,
    )
    return anchor, EvidenceRef(evidence_id=unit.evidence_id, page_num=unit.locator.page_num)


def _fake_response(anchor_span_id: str, *, numeric_failure: bool) -> Step2AProductionSourceOwnedArbitraryPDFResultPayload:
    assertion = "吊环的拉应力应不大于42MPa。" if numeric_failure else "吊环的拉应力应不大于65MPa。"
    return Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": "threshold",
                    "label": "吊环拉应力限制",
                    "assertion": assertion,
                    "primary_anchor_span_ids": [anchor_span_id],
                }
            ]
        }
    )


def _failed_materialization(
    *,
    bridge: SourceOwnedKnowledgeCardBridgeResult,
    candidate: SourceOwnedKnowledgeCardCandidate,
    fact: SourceOwnedCanonicalFact,
    evidence_refs: tuple[EvidenceRef, ...],
) -> SourceOwnedKnowledgeCardMaterializationResult:
    materialization_input = SourceOwnedKnowledgeCardMaterializationInput(
        bridge_result=bridge,
        candidate=candidate,
        canonical_fact=fact,
        evidence_refs=evidence_refs,
    )
    return source_owned_materialize_knowledge_card(materialization_input)


def run_source_owned_vertical_slice(
    *,
    scenario: SourceOwnedVerticalSliceScenario = "success",
    evidence_fixture: Path = DEFAULT_EVIDENCE_FIXTURE,
) -> SourceOwnedVerticalSliceSummary:
    """Run one deterministic source-owned scenario and return only its summary."""

    if scenario not in {
        "success",
        "missing_anchor",
        "numeric_derivation_failure",
        "identity_mismatch",
        "incomplete_materialization",
    }:
        raise ValueError(f"unsupported source-owned vertical slice scenario: {scenario}")

    page = _load_fixture_page(evidence_fixture)
    anchor, evidence_ref = _make_local_anchor(page)
    evidence_spans = {anchor.anchor_span_id: anchor}
    response = _fake_response(
        anchor.anchor_span_id,
        numeric_failure=scenario == "numeric_derivation_failure",
    )

    proposals = source_owned_extract_document_proposals(response=response)
    proposal = proposals[0]
    extraction_status: SourceOwnedVerticalSliceStageStatus = "extracted"

    selection_candidates = {} if scenario == "missing_anchor" else evidence_spans
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=selection_candidates,
    )
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={} if scenario == "missing_anchor" else evidence_spans,
    )
    anchor_selection_status = selection.anchor_selection_status
    derivation_status = derivation.numeric_derivation_status
    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )
    assembly_status = fact.derivation_status
    candidate = source_owned_fact_to_card_candidate(fact)

    admission = source_owned_admit_card_candidate(candidate)
    admission_status = admission.status
    bridge = source_owned_bridge_to_knowledge_card(candidate)
    bridge_status = bridge.status
    if scenario == "identity_mismatch" and bridge.status == "bridged":
        bridge = bridge.model_copy(update={"source_fact_id": "fact:" + "0" * 24})

    materialization_refs = () if scenario == "incomplete_materialization" else (evidence_ref,)
    materialization = _failed_materialization(
        bridge=bridge,
        candidate=candidate,
        fact=fact,
        evidence_refs=materialization_refs,
    )

    return _summary(
        extraction_status=extraction_status,
        anchor_selection_status=anchor_selection_status,
        derivation_status=derivation_status,
        assembly_status=assembly_status,
        admission_status=admission_status,
        bridge_status=bridge_status,
        materialization_status=materialization.status,
        knowledge_card_id=materialization.knowledge_card_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=(
            "success",
            "missing_anchor",
            "numeric_derivation_failure",
            "identity_mismatch",
            "incomplete_materialization",
        ),
        default="success",
    )
    parser.add_argument("--evidence-fixture", type=Path, default=DEFAULT_EVIDENCE_FIXTURE)
    args = parser.parse_args(argv)
    summary = run_source_owned_vertical_slice(
        scenario=args.scenario,
        evidence_fixture=args.evidence_fixture,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
