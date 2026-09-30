"""Regression tests for the fail-closed PageEvidence manifest binding seam."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_page_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    EvidenceKind,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.contracts import build_step2a_request
from examcard.knowledge.projection import (
    _manifest_bound_page,
    build_step2a_semantic_projection,
)
from examcard.materials.schema import ContentPolicy, SemanticRole

ROOT = Path(__file__).resolve().parents[1]
REAL_MANIFEST = (
    ROOT / "pilot_runs" / "real" / "yijian" / "final_diagnostic_retry_v1" / "evidence" / "manifest.json"
)


def _page() -> PageEvidence:
    document_sha256 = "a" * 64
    document_id = make_document_id(document_sha256)
    locator = SourceLocator(document_sha256=document_sha256, page_num=1, native_block_index=0)
    text = "施工准备应保持稳定。"
    fingerprint = make_evidence_fingerprint(
        kind=EvidenceKind.PARAGRAPH.value,
        canonical_text=text,
    )
    unit = EvidenceUnit(
        evidence_id=make_evidence_id(
            locator_id=make_source_locator_id(locator),
            fingerprint=fingerprint,
        ),
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=text,
            source_channels=(SourceChannel.NATIVE_TEXT.value,),
            engine_versions={"test": "1"},
            config_hash="test-config",
        ),
        document_id=document_id,
        kind=EvidenceKind.PARAGRAPH,
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
        metadata={"ocr_polygon": ((1.0, 2.0), (3.0, 4.0))},
    )
    return PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(
            native_text_coverage=1,
            ocr_alignment_rate=0,
            unresolved_regions=0,
            conflict_count=0,
        ),
    )


def _write_page(path: Path, page: PageEvidence) -> None:
    path.write_text(
        json.dumps(page.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _policy() -> ContentPolicy:
    return ContentPolicy(
        policy_id="test-policy",
        revision=1,
        display_name="test policy",
        included_roles=frozenset({SemanticRole.CORE}),
    )


@pytest.mark.skipif(not REAL_MANIFEST.is_file(), reason="real pilot Evidence artifact is absent")
def test_current_real_manifest_page_record_binds_successfully() -> None:
    from examcard.evidence.schema import DocumentEvidenceManifest

    manifest = DocumentEvidenceManifest.model_validate_json(REAL_MANIFEST.read_bytes())
    page_path = REAL_MANIFEST.parent / "pages" / "page_0001.json"
    page = PageEvidence.model_validate_json(page_path.read_bytes())

    bound = _manifest_bound_page(source_manifest_path=page_path, page=page)

    assert bound == manifest.pages[0]


def test_missing_page_binding_fails_closed_without_path_or_exception_text(tmp_path: Path) -> None:
    path = tmp_path / "page_0001.json"
    secret = "secret-provider-body Bearer secret-value"
    path.write_text(json.dumps({"secret": secret}), encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        _manifest_bound_page(source_manifest_path=path, page=_page())

    message = str(raised.value)
    assert message == "source manifest page binding is invalid"
    assert secret not in message
    assert str(path) not in message


def test_hash_mismatch_fails_closed_with_fixed_message(tmp_path: Path) -> None:
    page = _page()
    altered_locator = page.evidence_units[0].locator.model_copy(
        update={"document_sha256": "b" * 64}
    )
    altered_unit = page.evidence_units[0].model_copy(update={"locator": altered_locator})
    altered_page = page.model_copy(update={"evidence_units": (altered_unit,)})
    path = tmp_path / "page_0001.json"
    _write_page(path, altered_page)

    with pytest.raises(ValueError, match="exactly match") as raised:
        _manifest_bound_page(source_manifest_path=path, page=page)

    assert str(path) not in str(raised.value)


def test_valid_binding_preserves_step2_request_identity(tmp_path: Path) -> None:
    page = _page()
    path = tmp_path / "page_0001.json"
    _write_page(path, page)
    bound = _manifest_bound_page(source_manifest_path=path, page=page)

    first = build_step2a_semantic_projection(
        sample_id="sample-1",
        page=page,
        source_manifest_path=path,
        content_policy=_policy(),
    )
    second = build_step2a_semantic_projection(
        sample_id="sample-1",
        page=bound,
        source_manifest_path=path,
        content_policy=_policy(),
    )
    request_one = build_step2a_request(
        sample_id="sample-1",
        material_profile_id="profile-1",
        projection=first.projection,
        sections=(first.section,),
        model="fake-step2-v1",
    )
    request_two = build_step2a_request(
        sample_id="sample-1",
        material_profile_id="profile-1",
        projection=second.projection,
        sections=(second.section,),
        model="fake-step2-v1",
    )

    assert request_one.identity == request_two.identity
