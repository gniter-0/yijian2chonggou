"""Offline scale and isolation tests for the source-owned pilot measurement wrapper."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import examcard.knowledge.extraction_v1 as extraction_module
import migration.run_source_owned_scale_pilot_v1 as scale_module
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from migration.run_source_owned_real_pilot_v1 import SourceOwnedRealPilotSummary
from migration.run_source_owned_scale_pilot_v1 import (
    SourceOwnedScalePilotSummary,
    run_source_owned_scale_pilot,
)


def _pdf(path: Path, *, page_count: int, lines_per_page: int = 1) -> Path:
    page_start = 3
    content_start = page_start + page_count
    font_id = content_start + page_count
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{ ' '.join(f'{page_start + i} 0 R' for i in range(page_count)) }] "
            f"/Count {page_count} >>"
        ).encode("ascii"),
    ]
    for index in range(page_count):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_start + index} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode("ascii")
        )
    for page_number in range(page_count):
        commands = ["BT /F1 10 Tf 24 760 Td"]
        for line_number in range(lines_per_page):
            text = f"page {page_number + 1} evidence line {line_number + 1}"
            commands.append(f"({text}) Tj 0 -55 Td")
        commands.append("ET")
        content = " ".join(commands).encode("ascii")
        objects.append(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

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


def test_small_pdf_scale_run_materializes_one_card(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "small.pdf", page_count=1)

    summary = run_source_owned_scale_pilot(pdf, output_dir=tmp_path / "small-pilot")

    assert isinstance(summary, SourceOwnedScalePilotSummary)
    assert summary.document_sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert summary.pages == 1
    assert summary.evidence_units > 0
    assert summary.elapsed_seconds >= 0
    assert summary.peak_python_allocation_mb is not None
    assert summary.knowledge_card_count == 1
    assert summary.stage_statuses["evidence_ingestion"] == "completed"
    assert summary.stage_statuses["materialization"] == "materialized"


def test_scale_run_handles_ten_plus_pages(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "ten-plus.pdf", page_count=12)

    summary = run_source_owned_scale_pilot(pdf, output_dir=tmp_path / "ten-plus-pilot")

    assert summary.pages == 12
    assert summary.evidence_units >= 12
    assert summary.knowledge_card_count == 1
    assert summary.stage_statuses["source_owned_extraction"] == "extracted"


def test_scale_run_handles_a_large_evidence_unit_fixture(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "large-evidence.pdf", page_count=10, lines_per_page=8)

    summary = run_source_owned_scale_pilot(pdf, output_dir=tmp_path / "large-pilot")

    assert summary.pages == 10
    assert summary.evidence_units >= 10
    assert summary.elapsed_seconds >= 0
    assert summary.knowledge_card_count == 1


def test_empty_evidence_fails_closed(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "empty.pdf", page_count=1, lines_per_page=0)

    summary = run_source_owned_scale_pilot(
        pdf,
        output_dir=tmp_path / "empty-pilot",
        scenario="empty_evidence",
    )

    assert summary.pages == 1
    assert summary.evidence_units == 0
    assert summary.knowledge_card_count == 0
    assert summary.stage_statuses["evidence_ingestion"] == "failed_closed"
    assert all(
        status in {"failed_closed", "rejected"}
        for status in summary.stage_statuses.values()
        if status != "completed"
    )


@pytest.mark.parametrize("scenario", ("missing_anchor", "derivation_failure", "materialization_failure"))
def test_scale_failure_scenarios_remain_fail_closed(tmp_path: Path, scenario: str) -> None:
    pdf = _pdf(tmp_path / f"{scenario}.pdf", page_count=1)

    summary = run_source_owned_scale_pilot(
        pdf,
        output_dir=tmp_path / scenario,
        scenario=scenario,  # type: ignore[arg-type]
    )

    assert summary.knowledge_card_count == 0
    assert summary.stage_statuses["materialization"] == "failed_closed"


def test_summary_is_allow_listed_and_contains_no_source_content(tmp_path: Path) -> None:
    source_text = "private source text must not be persisted"
    pdf = _pdf(tmp_path / "secure.pdf", page_count=1, lines_per_page=1)
    output_dir = tmp_path / "secure-pilot"

    run_source_owned_scale_pilot(pdf, output_dir=output_dir)
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    serialized = json.dumps(summary, sort_keys=True).lower()

    assert set(summary) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "elapsed_seconds",
        "peak_python_allocation_mb",
        "knowledge_card_count",
        "stage_statuses",
    }
    assert source_text not in serialized
    for forbidden in (
        "raw_text",
        "canonical_text",
        "prompt",
        "response",
        "token_value",
        "secret",
        "credential",
        "provider",
    ):
        assert forbidden not in serialized
    assert sorted(path.name for path in output_dir.iterdir()) == ["summary.json"]


def test_scale_runner_delegates_to_existing_real_pilot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "delegated.pdf", page_count=1)
    calls: list[tuple[Path, Path | None]] = []

    def fake_real_pilot(
        pdf_path: Path,
        *,
        output_dir: Path | None = None,
        scenario: str = "success",
    ) -> SourceOwnedRealPilotSummary:
        calls.append((pdf_path, output_dir))
        return SourceOwnedRealPilotSummary(
            document_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            pages=1,
            evidence_units=1,
            extraction_status="extracted",
            anchor_selection_status="selected",
            derivation_status="no_quantitative_tokens",
            canonical_fact_status="assembled",
            admission_status="admitted",
            bridge_status="bridged",
            materialization_status="materialized",
            knowledge_card_count=1,
        )

    monkeypatch.setattr(scale_module, "run_source_owned_real_pilot", fake_real_pilot)
    summary = run_source_owned_scale_pilot(pdf, output_dir=tmp_path / "delegated-pilot")

    assert len(calls) == 1
    assert calls[0][0] == pdf.resolve()
    assert summary.knowledge_card_count == 1


def test_scale_run_does_not_enter_legacy_extraction_or_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf = _pdf(tmp_path / "isolated.pdf", page_count=1)

    def forbidden_legacy(**_: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider path was entered")

    monkeypatch.setattr(extraction_module, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_provider)
    summary = run_source_owned_scale_pilot(pdf, output_dir=tmp_path / "isolated-pilot")

    assert summary.stage_statuses["source_owned_extraction"] == "extracted"
    assert summary.knowledge_card_count == 1
