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


# ---------------------------------------------------------------------------
# Table cells: one composition owner plus one atomic owner (Step 2A v10).

_TABLE_PDF_SHA256 = "c" * 64


def _table_unit(*, kind: EvidenceKind, text: str, order: int) -> EvidenceUnit:
    document_id = make_document_id(_TABLE_PDF_SHA256)
    locator = SourceLocator(document_sha256=_TABLE_PDF_SHA256, page_num=1, native_block_index=order)
    fingerprint = make_evidence_fingerprint(kind=kind.value, canonical_text=text)
    return EvidenceUnit(
        evidence_id=make_evidence_id(locator_id=make_source_locator_id(locator), fingerprint=fingerprint),
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=text,
            source_channels=(SourceChannel.NATIVE_TEXT.value,),
            engine_versions={"test": "1"},
            config_hash="test-config",
        ),
        document_id=document_id,
        kind=kind,
        locator=locator,
        raw_text=text,
        canonical_text=text,
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1,
        order=order,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.NATIVE_TEXT}),
            engine_versions={"test": "1"},
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )


def _table_manifest() -> tuple[DocumentEvidenceManifest, dict[str, str]]:
    units = (
        _table_unit(kind=EvidenceKind.TABLE_CELL, text="试验路段长度", order=0),
        _table_unit(kind=EvidenceKind.TABLE_CELL, text="宜不小于200m。", order=1),
        _table_unit(kind=EvidenceKind.PARAGRAPH, text="试验路段要求如下。", order=2),
    )
    document_id = make_document_id(_TABLE_PDF_SHA256)
    page = PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=units,
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
        source=SourceDocument(relative_path="table.pdf", sha256=_TABLE_PDF_SHA256, byte_size=1, page_count=1),
        pages=(page,),
        lineage=EvidenceLineage(
            run_id="run:test",
            profile_id="test_profile",
            profile_revision=1,
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )
    ids = {"header": units[0].evidence_id, "value": units[1].evidence_id, "paragraph": units[2].evidence_id}
    return manifest, ids


def _table_proposal(
    *,
    key: str,
    fact_type: str,
    completeness_mode: str,
    primary: tuple[str, ...],
    secondary: tuple[str, ...] = (),
    assertion: str = "试验路段长度宜不小于200m。",
) -> Step2AFactProposal:
    return Step2AFactProposal(
        proposal_key=f"proposal:{key}",
        source_section_ids=("section:test",),
        fact_type=fact_type,
        label="试验路段长度",
        assertion=assertion,
        completeness_mode=completeness_mode,
        primary_anchor_span_ids=tuple("span:" + str(index) * 24 for index in range(1, len(primary) + 1)),
        secondary_anchor_span_ids=tuple("span:" + str(index) * 24 for index in range(7, 7 + len(secondary))),
        primary_evidence_ids=primary,
        secondary_evidence_ids=secondary,
    )


def _composition(ids: dict[str, str], key: str = "composition") -> Step2AFactProposal:
    return _table_proposal(
        key=key,
        fact_type="composition",
        completeness_mode="full_table_row",
        primary=(ids["header"], ids["value"]),
    )


def test_table_cell_may_have_one_composition_and_one_atomic_owner():
    manifest, ids = _table_manifest()
    atomic = _table_proposal(
        key="threshold",
        fact_type="threshold",
        completeness_mode="atomic",
        primary=(ids["value"],),
        secondary=(ids["header"],),
    )

    result = ground_step2a_proposals(
        manifest=manifest,
        proposals=(_composition(ids), atomic),
        content_policy=_policy(),
    )

    composition_fact, atomic_fact = (item.fact for item in result)
    assert composition_fact.fact_type.value == "composition"
    assert composition_fact.completeness_mode.value == "full_table_row"
    assert atomic_fact.fact_type.value == "threshold"
    assert atomic_fact.completeness_mode.value == "atomic"
    assert atomic_fact.ordered_member_ids == ()
    assert atomic_fact.primary_evidence_ids == (ids["value"],)
    assert atomic_fact.fact_id != composition_fact.fact_id
    # Card text is still reconstructed from source Evidence, never from the model assertion.
    assert "宜不小于200m。" in result[1].card.content


def test_atomic_owner_may_come_before_the_composition_owner():
    manifest, ids = _table_manifest()
    atomic = _table_proposal(key="threshold", fact_type="threshold", completeness_mode="atomic", primary=(ids["value"],))

    result = ground_step2a_proposals(manifest=manifest, proposals=(atomic, _composition(ids)), content_policy=_policy())

    assert [item.fact.fact_type.value for item in result] == ["threshold", "composition"]


def test_one_table_cell_may_hold_several_atomic_rules():
    manifest, ids = _table_manifest()
    first = _table_proposal(key="threshold-a", fact_type="threshold", completeness_mode="atomic", primary=(ids["value"],))
    second = _table_proposal(key="threshold-b", fact_type="condition", completeness_mode="atomic", primary=(ids["value"],))

    result = ground_step2a_proposals(
        manifest=manifest,
        proposals=(_composition(ids), first, second),
        content_policy=_policy(),
    )

    assert [item.fact.fact_type.value for item in result] == ["composition", "threshold", "condition"]


def test_table_cell_cannot_be_shared_with_a_non_atomic_fact():
    manifest, ids = _table_manifest()
    definition = _table_proposal(key="definition", fact_type="definition", completeness_mode="atomic", primary=(ids["value"],))

    with pytest.raises(ValueError, match="multiple primary Fact owners"):
        ground_step2a_proposals(
            manifest=manifest,
            proposals=(_composition(ids), definition),
            content_policy=_policy(),
        )


def test_two_composition_owners_of_one_table_cell_fail_closed():
    manifest, ids = _table_manifest()

    with pytest.raises(ValueError, match="multiple primary Fact owners"):
        ground_step2a_proposals(
            manifest=manifest,
            proposals=(_composition(ids, "composition-a"), _composition(ids, "composition-b")),
            content_policy=_policy(),
        )


def test_non_table_evidence_keeps_exclusive_primary_ownership():
    manifest, evidence_id = _manifest()
    first = _proposal(evidence_id=evidence_id)
    second = first.model_copy(update={"proposal_key": "proposal:formula-again"})

    with pytest.raises(ValueError, match="multiple primary Fact owners"):
        ground_step2a_proposals(manifest=manifest, proposals=(first, second), content_policy=_policy())


def test_table_atomic_retyping_is_limited_to_contract_fact_types():
    manifest, ids = _table_manifest()
    definition = _table_proposal(key="definition", fact_type="definition", completeness_mode="atomic", primary=(ids["value"],))

    with pytest.raises(ValueError, match="Fact type is not grounded"):
        ground_step2a_proposals(manifest=manifest, proposals=(definition,), content_policy=_policy())


def test_table_atomic_retyping_requires_table_only_evidence():
    manifest, ids = _table_manifest()
    mixed = _table_proposal(
        key="threshold",
        fact_type="threshold",
        completeness_mode="atomic",
        primary=(ids["value"],),
        secondary=(ids["paragraph"],),
    )

    with pytest.raises(ValueError, match="Fact type is not grounded"):
        ground_step2a_proposals(manifest=manifest, proposals=(mixed,), content_policy=_policy())
