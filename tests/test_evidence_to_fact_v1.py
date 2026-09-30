"""Offline grounding and duplicate handling tests for AI Fact proposals."""
from __future__ import annotations

import pytest

from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_manifest_id,
    make_page_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    DocumentEvidenceManifest,
    EvidenceKind,
    EvidenceLineage,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceDocument,
    SourceChannel,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.contracts import Step2AFactProposal
from examcard.knowledge.evidence_to_fact_v1 import ground_step2a_proposals
from examcard.materials.schema import ContentPolicy, SemanticRole


def _policy() -> ContentPolicy:
    return ContentPolicy(
        policy_id="test_policy",
        revision=1,
        display_name="test",
        included_roles=frozenset({SemanticRole.CORE}),
    )


def _manifest() -> tuple[DocumentEvidenceManifest, str]:
    text = "公式：F = ma，其中质量为10 kg。"
    pdf_sha256 = "b" * 64
    document_id = make_document_id(pdf_sha256)
    locator = SourceLocator(document_sha256=pdf_sha256, page_num=1, native_block_index=0)
    fingerprint = make_evidence_fingerprint(kind=EvidenceKind.FORMULA.value, canonical_text=text)
    evidence_id = make_evidence_id(locator_id=make_source_locator_id(locator), fingerprint=fingerprint)
    unit = EvidenceUnit(
        evidence_id=evidence_id,
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=text,
            source_channels=(SourceChannel.NATIVE_TEXT.value,),
            engine_versions={"test": "1"},
            config_hash="test-config",
        ),
        document_id=document_id,
        kind=EvidenceKind.FORMULA,
        locator=locator,
        raw_text=text,
        canonical_text=text,
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1,
        order=0,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.NATIVE_TEXT}),
            engine_versions={"test": "1"},
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )
    page = PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(native_text_coverage=1, ocr_alignment_rate=0, unresolved_regions=0, conflict_count=0),
    )
    manifest = DocumentEvidenceManifest(
        manifest_id=make_manifest_id(
            document_id=document_id,
            material_profile_id="test_profile",
            material_profile_revision=1,
            config_hash="test-config",
        ),
        document_id=document_id,
        material_profile_id="test_profile",
        material_profile_revision=1,
        source=SourceDocument(relative_path="input.pdf", sha256=pdf_sha256, byte_size=1, page_count=1),
        pages=(page,),
        lineage=EvidenceLineage(
            run_id="run:test",
            profile_id="test_profile",
            profile_revision=1,
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )
    return manifest, evidence_id


def _proposal(*, evidence_id: str, assertion: str = "模型公式 F = ma，其中质量为10 kg。") -> Step2AFactProposal:
    return Step2AFactProposal(
        proposal_key="proposal:formula",
        source_section_ids=("section:test",),
        fact_type="formula",
        label="动力学公式",
        assertion=assertion,
        completeness_mode="complete_formula",
        primary_anchor_span_ids=("span:" + "1" * 24,),
        primary_evidence_ids=(evidence_id,),
    )


def test_formula_and_numbers_are_reconstructed_from_source_evidence():
    manifest, evidence_id = _manifest()
    result = ground_step2a_proposals(
        manifest=manifest,
        proposals=(_proposal(evidence_id=evidence_id),),
        content_policy=_policy(),
    )
    assert result[0].fact.assertion == manifest.pages[0].evidence_units[0].canonical_text
    assert result[0].card.content == manifest.pages[0].evidence_units[0].canonical_text
    assert result[0].fact.fact_type.value == "formula"
    assert result[0].fact.numbers[0].evidence_id == evidence_id


def test_duplicate_proposals_fail_closed():
    manifest, evidence_id = _manifest()
    proposal = _proposal(evidence_id=evidence_id)
    with pytest.raises(ValueError, match="duplicate Step 2A proposal keys"):
        ground_step2a_proposals(
            manifest=manifest,
            proposals=(proposal, proposal),
            content_policy=_policy(),
        )


def test_unknown_evidence_cannot_become_a_fact():
    manifest, _ = _manifest()
    unknown = "ev:" + "f" * 24
    with pytest.raises(ValueError, match="outside the document manifest"):
        ground_step2a_proposals(
            manifest=manifest,
            proposals=(_proposal(evidence_id=unknown),),
            content_policy=_policy(),
        )


def test_numeric_claim_in_model_assertion_must_be_source_grounded():
    manifest, evidence_id = _manifest()
    with pytest.raises(ValueError, match="numeric proposal claim"):
        ground_step2a_proposals(
            manifest=manifest,
            proposals=(_proposal(evidence_id=evidence_id, assertion="模型公式 F = ma，其中质量为99 kg。"),),
            content_policy=_policy(),
        )


def test_grounding_does_not_expose_delivery_operations():
    assert not hasattr(ground_step2a_proposals, "upload")
    assert not hasattr(ground_step2a_proposals, "lookup")
