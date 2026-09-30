"""Run an offline source-owned vertical pilot over a real PDF.

The PDF is processed by the existing local Evidence Engine.  Step 2 uses a
schema-validated fake source-owned response, so this runner never invokes a
provider and never enters the legacy extraction lanes.  Evidence objects stay
in memory; the pilot directory receives only the allow-listed summary.
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

from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from examcard.evidence.schema import EvidenceUnit, PageEvidence
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
from examcard.knowledge.fact_policy import decide_policy
from examcard.knowledge.schema import EvidenceRef, FactType
from examcard.materials import load_content_policy, load_material_profile
from examcard.materials.schema import ContentPolicy, PolicyDecision, PolicyDisposition, StrictModel


type SourceOwnedRealPilotScenario = Literal[
    "success",
    "empty_evidence",
    "missing_anchor",
    "derivation_failure",
    "identity_mismatch",
    "materialization_failure",
]
type PilotStageStatus = Literal["extracted", "failed_closed"]
type PilotAnchorStatus = Literal[
    "selected",
    "no_matching_anchor",
    "ambiguous_anchor",
    "failed_closed",
]
type PilotDerivationStatus = Literal["derived", "no_quantitative_tokens", "failed_closed"]
type PilotFactStatus = Literal["assembled", "failed_closed"]
type PilotAdmissionStatus = Literal["admitted", "rejected"]
type PilotBridgeStatus = Literal["bridged", "failed_closed"]
type PilotMaterializationStatus = Literal["materialized", "failed_closed"]


DEFAULT_PROFILE_PATH = REPOSITORY_ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml"
DEFAULT_POLICY_PATH = REPOSITORY_ROOT / "configs/v2/content_policies/core_only_v1.yaml"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "pilot_runs" / "source_owned"


class SourceOwnedRealPilotSummary(StrictModel):
    """The only persisted representation of one real-PDF pilot run."""

    document_sha256: str
    pages: int
    evidence_units: int
    extraction_status: PilotStageStatus
    anchor_selection_status: PilotAnchorStatus
    derivation_status: PilotDerivationStatus
    canonical_fact_status: PilotFactStatus
    admission_status: PilotAdmissionStatus
    bridge_status: PilotBridgeStatus
    materialization_status: PilotMaterializationStatus
    knowledge_card_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_summary(output_dir: Path, summary: SourceOwnedRealPilotSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "summary.json.tmp"
    temporary.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_dir / "summary.json")


def _failed_summary(*, document_sha256: str, pages: int = 0, evidence_units: int = 0) -> SourceOwnedRealPilotSummary:
    return SourceOwnedRealPilotSummary(
        document_sha256=document_sha256,
        pages=pages,
        evidence_units=evidence_units,
        extraction_status="failed_closed",
        anchor_selection_status="failed_closed",
        derivation_status="failed_closed",
        canonical_fact_status="failed_closed",
        admission_status="rejected",
        bridge_status="failed_closed",
        materialization_status="failed_closed",
        knowledge_card_count=0,
    )


def _policy_decision(unit: EvidenceUnit, *, policy: ContentPolicy) -> PolicyDecision:
    """Derive the policy decision locally without mutating Evidence."""

    return decide_policy(unit=unit, policy=policy)


def _select_local_unit(
    pages: tuple[PageEvidence, ...],
    *,
    policy: ContentPolicy,
) -> EvidenceUnit | None:
    for page in pages:
        for unit in page.evidence_units:
            if not unit.canonical_text.strip():
                continue
            decision = _policy_decision(unit, policy=policy)
            if decision.disposition == PolicyDisposition.INCLUDED:
                return unit
    return None


def _make_anchor(unit: EvidenceUnit, *, policy: ContentPolicy) -> EvidenceAnchorSpan:
    exact_text_sha256 = hashlib.sha256(unit.canonical_text.encode("utf-8")).hexdigest()
    policy_decision = decide_policy(unit=unit, policy=policy)
    content_policy_fingerprint_sha256 = unit.provenance.config_hash
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=unit.evidence_id,
        start=0,
        end=len(unit.canonical_text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint_sha256,
        source_manifest_sha256=unit.locator.document_sha256,
    )
    return EvidenceAnchorSpan(
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
        source_manifest_sha256=unit.locator.document_sha256,
    )


def _fake_response(anchor_span_id: str, *, derivation_failure: bool) -> Step2AProductionSourceOwnedArbitraryPDFResultPayload:
    assertion = "Local source-owned fact 999999999" if derivation_failure else "Local source-owned fact"
    return Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": FactType.DEFINITION.value,
                    "label": "Local source-owned fact",
                    "assertion": assertion,
                    "primary_anchor_span_ids": [anchor_span_id],
                }
            ]
        }
    )


def _materialize(
    *,
    bridge: SourceOwnedKnowledgeCardBridgeResult,
    candidate: SourceOwnedKnowledgeCardCandidate,
    fact: SourceOwnedCanonicalFact,
    evidence_refs: tuple[EvidenceRef, ...],
) -> SourceOwnedKnowledgeCardMaterializationResult:
    return source_owned_materialize_knowledge_card(
        SourceOwnedKnowledgeCardMaterializationInput(
            bridge_result=bridge,
            candidate=candidate,
            canonical_fact=fact,
            evidence_refs=evidence_refs,
        )
    )


def run_source_owned_real_pilot(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    scenario: SourceOwnedRealPilotScenario = "success",
    profile_path: Path = DEFAULT_PROFILE_PATH,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> SourceOwnedRealPilotSummary:
    """Ingest one real PDF and execute only the offline source-owned lane."""

    if scenario not in {
        "success",
        "empty_evidence",
        "missing_anchor",
        "derivation_failure",
        "identity_mismatch",
        "materialization_failure",
    }:
        raise ValueError(f"unsupported source-owned real pilot scenario: {scenario}")
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise ValueError("source-owned real pilot requires an existing PDF path")
    document_sha256 = _sha256_file(pdf_path)
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / pdf_path.stem

    policy = load_content_policy(policy_path)
    profile = load_material_profile(profile_path)
    if profile.content_policy_id != policy.policy_id or profile.content_policy_revision != policy.revision:
        raise ValueError("pilot material profile and content policy are inconsistent")

    # No output_dir is passed to the Evidence Engine: that option persists raw
    # page manifests/crops.  The disabled cache also prevents local Evidence
    # artifacts from being written outside the pilot summary.
    engine = EvidenceEngine(
        profile=profile,
        cache_root=target / "cache",
        config=EvidenceEngineConfig(enable_visual_review=False),
        cache_enabled=False,
    )
    try:
        manifest, _report = engine.ingest_document(
            pdf_path,
            source_relative_path=pdf_path.name,
            output_dir=None,
        )
    except Exception:
        summary = _failed_summary(document_sha256=document_sha256)
        _write_summary(target, summary)
        return summary

    page_count = len(manifest.pages)
    evidence_unit_count = sum(len(page.evidence_units) for page in manifest.pages)
    unit = _select_local_unit(
        manifest.pages,
        policy=policy,
    )
    if scenario == "empty_evidence" or unit is None:
        summary = _failed_summary(
            document_sha256=manifest.source.sha256,
            pages=page_count,
            evidence_units=evidence_unit_count,
        )
        _write_summary(target, summary)
        return summary

    anchor = _make_anchor(unit, policy=policy)
    evidence_spans = {anchor.anchor_span_id: anchor}
    requested_anchor_id = "span:" + "0" * 24 if scenario == "missing_anchor" else anchor.anchor_span_id
    response = _fake_response(
        requested_anchor_id,
        derivation_failure=scenario == "derivation_failure",
    )
    proposals = source_owned_extract_document_proposals(response=response)
    proposal = proposals[0]
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={} if scenario == "missing_anchor" else evidence_spans,
    )
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={} if scenario == "missing_anchor" else evidence_spans,
    )
    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )
    candidate = source_owned_fact_to_card_candidate(fact)
    admission = source_owned_admit_card_candidate(candidate)
    bridge = source_owned_bridge_to_knowledge_card(candidate)
    if scenario == "identity_mismatch" and bridge.status == "bridged":
        bridge = bridge.model_copy(update={"source_fact_id": "fact:" + "0" * 24})
    refs = () if scenario == "materialization_failure" else (
        EvidenceRef(evidence_id=unit.evidence_id, page_num=unit.locator.page_num),
    )
    materialization = _materialize(
        bridge=bridge,
        candidate=candidate,
        fact=fact,
        evidence_refs=refs,
    )
    summary = SourceOwnedRealPilotSummary(
        document_sha256=manifest.source.sha256,
        pages=page_count,
        evidence_units=evidence_unit_count,
        extraction_status="extracted",
        anchor_selection_status=selection.anchor_selection_status,
        derivation_status=derivation.numeric_derivation_status,
        canonical_fact_status=fact.derivation_status,
        admission_status=admission.status,
        bridge_status=bridge.status,
        materialization_status=materialization.status,
        knowledge_card_count=int(materialization.materialized_card is not None),
    )
    _write_summary(target, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--scenario",
        choices=(
            "success",
            "empty_evidence",
            "missing_anchor",
            "derivation_failure",
            "identity_mismatch",
            "materialization_failure",
        ),
        default="success",
    )
    args = parser.parse_args(argv)
    summary = run_source_owned_real_pilot(
        args.pdf_path,
        output_dir=args.output_dir,
        scenario=args.scenario,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
