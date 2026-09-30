"""Offline tests for the document-level typed Step 2A extraction bridge."""
from __future__ import annotations

import json
from pathlib import Path

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
from examcard.knowledge.extraction_v1 import run_document_fact_pipeline
from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.materials.schema import ContentPolicy, SemanticRole


def _policy() -> ContentPolicy:
    return ContentPolicy(
        policy_id="test_policy",
        revision=1,
        display_name="test",
        included_roles=frozenset({SemanticRole.CORE}),
    )


def _manifest(text: str) -> DocumentEvidenceManifest:
    pdf_sha256 = "a" * 64
    document_id = make_document_id(pdf_sha256)
    locator = SourceLocator(document_sha256=pdf_sha256, page_num=1, native_block_index=0)
    fingerprint = make_evidence_fingerprint(kind=EvidenceKind.PARAGRAPH.value, canonical_text=text)
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
    )
    page = PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(native_text_coverage=1, ocr_alignment_rate=0, unresolved_regions=0, conflict_count=0),
    )
    return DocumentEvidenceManifest(
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


def test_document_pipeline_uses_fake_transport_and_returns_grounded_card(tmp_path: Path):
    source = "钢材抗拉强度不得低于65 MPa。"
    manifest = _manifest(source)
    calls: list[str] = []

    def callback(**kwargs):
        request = kwargs["request"]
        calls.append(request.identity.request_id)
        span = request.projection.anchor_spans[0]
        return TransportCompletion(
            content=json.dumps(
                {
                    "proposals": [
                        {
                            "proposal_key": "proposal:strength",
                            "source_section_ids": [request.sections[0].section_id],
                            "fact_type": "exception",
                            "label": "抗拉强度",
                            "assertion": source,
                            "primary_anchor_span_ids": [span.anchor_span_id],
                            "numeric_claims": [
                                {"value": "65 MPa", "anchor_span_id": span.anchor_span_id}
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=CallableStep2Transport(callback),
        idempotency_supported=False,
    )
    result = run_document_fact_pipeline(
        manifest=manifest,
        content_policy=_policy(),
        adapter=adapter,
        model="fake-step2-v1",
    )

    assert calls
    assert len(result.extraction.proposals) == 1
    assert result.facts[0].assertion == source
    assert result.cards[0].content == source
    assert "模型说明" not in result.cards[0].content
    assert result.cards[0].evidence_refs[0].evidence_id == result.facts[0].primary_evidence_ids[0]
    assert result.facts[0].numbers[0].normalized == "65MPa"
    assert result.facts[0].numbers[0].evidence_id == result.facts[0].primary_evidence_ids[0]


def test_same_manifest_and_config_produce_stable_fact_and_card_ids(tmp_path: Path):
    manifest = _manifest("定义：质量应保持稳定。")

    def callback(**kwargs):
        request = kwargs["request"]
        span = request.projection.anchor_spans[0]
        return TransportCompletion(
            content=json.dumps(
                {
                    "proposals": [
                        {
                            "proposal_key": "proposal:stable",
                            "source_section_ids": [request.sections[0].section_id],
                            "fact_type": "definition",
                            "label": "稳定性",
                            "assertion": "另一种模型表述",
                            "primary_anchor_span_ids": [span.anchor_span_id],
                        }
                    ]
                }
            )
        )

    def run(root: Path):
        return run_document_fact_pipeline(
            manifest=manifest,
            content_policy=_policy(),
            adapter=Step2OpenAIAdapter(
                storage_root=root,
                transport=CallableStep2Transport(callback),
                idempotency_supported=False,
            ),
            model="fake-step2-v1",
        )

    first = run(tmp_path / "first")
    second = run(tmp_path / "second")
    assert first.facts[0].fact_id == second.facts[0].fact_id
    assert first.cards[0].raw_id == second.cards[0].raw_id
    assert first.extraction.requests[0].identity.idempotency_key == second.extraction.requests[0].identity.idempotency_key


def test_unknown_model_anchor_fails_before_fact_creation(tmp_path: Path):
    manifest = _manifest("仅允许来源证据。")

    def callback(**kwargs):
        request = kwargs["request"]
        return TransportCompletion(
            content=json.dumps(
                {
                    "proposals": [
                        {
                            "proposal_key": "proposal:unknown",
                            "source_section_ids": [request.sections[0].section_id],
                            "fact_type": "definition",
                            "label": "未知",
                            "assertion": "未知来源",
                            "primary_anchor_span_ids": ["span:" + "f" * 24],
                        }
                    ]
                }
            )
        )

    with pytest.raises(ValueError, match="extraction failed"):
        run_document_fact_pipeline(
            manifest=manifest,
            content_policy=_policy(),
            adapter=Step2OpenAIAdapter(
                storage_root=tmp_path / "step2",
                transport=CallableStep2Transport(callback),
                idempotency_supported=False,
            ),
            model="fake-step2-v1",
        )


def test_bridge_has_no_delivery_authority_or_network_client():
    assert not hasattr(run_document_fact_pipeline, "upload")
    assert not hasattr(run_document_fact_pipeline, "lookup")
    assert "openai" not in run_document_fact_pipeline.__module__
