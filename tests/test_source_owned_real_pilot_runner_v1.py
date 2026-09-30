"""Offline tests for the real-PDF source-owned pilot runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import examcard.knowledge.extraction_v1 as extraction_module
from migration.run_source_owned_real_pilot_v1 import (
    SourceOwnedRealPilotSummary,
    run_source_owned_real_pilot,
)


def _make_pdf(path: Path, *, text: str | None) -> Path:
    escaped = "" if text is None else text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
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


def test_real_pdf_ingestion_runs_the_complete_offline_source_owned_path(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "source.pdf", text="A source-owned fact from the PDF.")
    output_dir = tmp_path / "pilot"

    summary = run_source_owned_real_pilot(pdf, output_dir=output_dir)

    assert isinstance(summary, SourceOwnedRealPilotSummary)
    assert summary.document_sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert summary.pages == 1
    assert summary.evidence_units > 0
    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "no_quantitative_tokens"
    assert summary.canonical_fact_status == "assembled"
    assert summary.admission_status == "admitted"
    assert summary.bridge_status == "bridged"
    assert summary.materialization_status == "materialized"
    assert summary.knowledge_card_count == 1
    assert sorted(path.name for path in output_dir.iterdir()) == ["summary.json"]


def test_empty_evidence_fails_closed(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "empty.pdf", text=None)

    summary = run_source_owned_real_pilot(
        pdf,
        output_dir=tmp_path / "empty-pilot",
        scenario="empty_evidence",
    )

    assert summary.pages == 1
    assert summary.evidence_units == 0
    assert summary.extraction_status == "failed_closed"
    assert summary.canonical_fact_status == "failed_closed"
    assert summary.knowledge_card_count == 0


@pytest.mark.parametrize(
    ("scenario", "expected_selection", "expected_derivation"),
    (
        ("missing_anchor", "failed_closed", "failed_closed"),
        ("derivation_failure", "selected", "failed_closed"),
    ),
)
def test_upstream_failures_fail_closed(
    tmp_path: Path,
    scenario: str,
    expected_selection: str,
    expected_derivation: str,
) -> None:
    pdf = _make_pdf(tmp_path / f"{scenario}.pdf", text="A source-owned fact from the PDF.")

    summary = run_source_owned_real_pilot(
        pdf,
        output_dir=tmp_path / scenario,
        scenario=scenario,  # type: ignore[arg-type]
    )

    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == expected_selection
    assert summary.derivation_status == expected_derivation
    assert summary.canonical_fact_status == "failed_closed"
    assert summary.admission_status == "rejected"
    assert summary.bridge_status == "failed_closed"
    assert summary.materialization_status == "failed_closed"
    assert summary.knowledge_card_count == 0


@pytest.mark.parametrize("scenario", ("identity_mismatch", "materialization_failure"))
def test_downstream_identity_and_materialization_failures_do_not_publish_a_card(
    tmp_path: Path,
    scenario: str,
) -> None:
    pdf = _make_pdf(tmp_path / f"{scenario}.pdf", text="A source-owned fact from the PDF.")

    summary = run_source_owned_real_pilot(
        pdf,
        output_dir=tmp_path / scenario,
        scenario=scenario,  # type: ignore[arg-type]
    )

    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "no_quantitative_tokens"
    assert summary.canonical_fact_status == "assembled"
    assert summary.admission_status == "admitted"
    assert summary.bridge_status == "bridged"
    assert summary.materialization_status == "failed_closed"
    assert summary.knowledge_card_count == 0


def test_summary_is_allow_listed_and_contains_no_source_or_provider_content(tmp_path: Path) -> None:
    source_text = "A source-owned fact that must never enter summary.json."
    pdf = _make_pdf(tmp_path / "secure.pdf", text=source_text)
    output_dir = tmp_path / "secure-pilot"

    run_source_owned_real_pilot(pdf, output_dir=output_dir)
    payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True).lower()

    assert set(payload) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "extraction_status",
        "anchor_selection_status",
        "derivation_status",
        "canonical_fact_status",
        "admission_status",
        "bridge_status",
        "materialization_status",
        "knowledge_card_count",
    }
    assert source_text.lower() not in serialized
    for forbidden in (
        "raw_text",
        "canonical_text",
        "prompt",
        "response",
        "token_value",
        "claim_value",
        "secret",
        "credential",
        "provider",
    ):
        assert forbidden not in serialized


def test_real_pilot_does_not_enter_legacy_extraction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "isolated.pdf", text="A source-owned fact from the PDF.")

    def legacy_must_not_run(**_: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    monkeypatch.setattr(extraction_module, "extract_document_proposals", legacy_must_not_run)
    summary = run_source_owned_real_pilot(pdf, output_dir=tmp_path / "isolated-pilot")

    assert summary.materialization_status == "materialized"
    assert summary.knowledge_card_count == 1
