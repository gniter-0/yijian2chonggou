"""Offline tests for numeric derivation over real PDF Evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import examcard.knowledge.extraction_v1 as extraction_module
from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedCanonicalProposal,
    make_evidence_anchor_span_id,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.schema import FactType
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole
from migration.run_source_owned_real_evidence_numeric_harness_v1 import (
    SourceOwnedRealEvidenceNumericHarnessSummary,
    derive_source_owned_real_evidence_numeric_input,
    effective_numeric_derivation_status,
    run_source_owned_real_evidence_numeric_harness,
)


def _make_pdf(path: Path, *, text: str) -> Path:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 24 48 Td ({escaped}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    document = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    document += b"0000000000 65535 f \n"
    document += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(document)
    return path


def _make_anchor(
    text: str,
    *,
    evidence_suffix: str = "a",
    evidence_kind: EvidenceKind = EvidenceKind.PARAGRAPH,
) -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + evidence_suffix * 24
    content_policy_fingerprint = "b" * 64
    source_manifest_sha256 = "c" * 64
    exact_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=parent_evidence_id,
        start=0,
        end=len(text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        source_manifest_sha256=source_manifest_sha256,
    )
    policy_decision = PolicyDecision(
        evidence_id=parent_evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        policy_id="core-policy-v1",
        policy_revision=1,
        reason="selected source anchor",
    )
    return EvidenceAnchorSpan(
        anchor_span_id=anchor_span_id,
        parent_evidence_id=parent_evidence_id,
        parent_evidence_kind=evidence_kind,
        start=0,
        end=len(text),
        canonical_text=text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id="core-policy-v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        policy_decision=policy_decision,
        source_manifest_sha256=source_manifest_sha256,
    )


def _proposal(assertion: str, anchor_ids: tuple[str, ...]) -> SourceOwnedCanonicalProposal:
    return SourceOwnedCanonicalProposal(
        proposal_key="proposal:harness-test",
        fact_type=FactType.DEFINITION,
        label="Harness test fact",
        assertion=assertion,
        primary_anchor_span_ids=anchor_ids,
    )


def test_real_pdf_evidence_with_one_quantitative_token_derives_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    pdf = _make_pdf(tmp_path / "numeric.pdf", text="65MPa")

    summary = run_source_owned_real_evidence_numeric_harness(
        pdf,
        output_dir=tmp_path / "numeric-output",
    )

    assert isinstance(summary, SourceOwnedRealEvidenceNumericHarnessSummary)
    assert summary.document_sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert summary.pages == 1
    assert summary.evidence_units > 0
    assert summary.numeric_derivation_status == "derived"
    assert summary.grounded_numeric_claim_count == 1


def test_real_pdf_without_quantitative_token_is_not_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    pdf = _make_pdf(tmp_path / "qualitative.pdf", text="The source states a qualitative rule.")

    summary = run_source_owned_real_evidence_numeric_harness(pdf, output_dir=tmp_path / "qualitative-output")

    assert summary.numeric_derivation_status == "no_quantitative_tokens"
    assert summary.grounded_numeric_claim_count == 0


def test_duplicate_quantitative_token_across_anchors_fails_closed() -> None:
    first = _make_anchor("The first source value is 42 units.", evidence_suffix="a")
    second = _make_anchor("The second source value is 42 units.", evidence_suffix="d")
    proposal = _proposal(
        "The asserted value is 42 units.",
        (first.anchor_span_id, second.anchor_span_id),
    )

    result = derive_source_owned_real_evidence_numeric_input(
        proposal=proposal,
        evidence_spans={first.anchor_span_id: first, second.anchor_span_id: second},
    )

    assert result.numeric_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_table_cell_with_multiple_quantitative_tokens_fails_closed() -> None:
    anchor = _make_anchor(
        "Table cell has 2020 and 45 units.",
        evidence_kind=EvidenceKind.TABLE_CELL,
    )
    proposal = _proposal("The table cell is locally selected.", (anchor.anchor_span_id,))

    result = derive_source_owned_real_evidence_numeric_input(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
        table_atomic=True,
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert effective_numeric_derivation_status(result) == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_summary_is_allow_listed_and_persists_no_source_or_provider_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    source_text = "PRIVATE_EVIDENCE_TEXT must not be persisted"
    pdf = _make_pdf(tmp_path / "secure.pdf", text=source_text)
    output_dir = tmp_path / "secure-output"

    run_source_owned_real_evidence_numeric_harness(pdf, output_dir=output_dir)
    summary_path = output_dir / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True).lower()

    assert set(payload) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "numeric_derivation_status",
        "grounded_numeric_claim_count",
    }
    assert source_text.lower() not in serialized
    for forbidden in (
        "raw_text",
        "canonical_text",
        "token_value",
        "assertion",
        "prompt",
        "response",
        "provider",
        "secret",
        "credential",
    ):
        assert forbidden not in serialized
    assert [path.name for path in output_dir.iterdir()] == ["summary.json"]


def test_harness_isolated_from_legacy_extraction_and_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    pdf = _make_pdf(tmp_path / "isolated.pdf", text="65MPa")

    def forbidden_legacy(**_: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider path was entered")

    monkeypatch.setattr(extraction_module, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_provider)
    summary = run_source_owned_real_evidence_numeric_harness(pdf, output_dir=tmp_path / "isolated-output")

    assert summary.numeric_derivation_status == "derived"
    assert summary.grounded_numeric_claim_count == 1


def test_harness_requires_offline_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(tmp_path / "network.pdf", text="65MPa")

    with pytest.raises(ValueError, match="REAL_NETWORK=0"):
        run_source_owned_real_evidence_numeric_harness(pdf, output_dir=tmp_path / "network-output")
