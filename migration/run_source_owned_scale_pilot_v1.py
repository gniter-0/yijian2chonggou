"""Measure the offline source-owned vertical slice over real PDF sizes.

This migration runner is intentionally a measurement wrapper.  It delegates
all PDF ingestion and source-owned pipeline behavior to the existing real
pilot runner and adds only elapsed-time and Python-allocation measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import tracemalloc
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

from examcard.materials.schema import StrictModel
from migration.run_source_owned_real_pilot_v1 import (
    SourceOwnedRealPilotSummary,
    run_source_owned_real_pilot,
)


type SourceOwnedScalePilotScenario = Literal[
    "success",
    "empty_evidence",
    "missing_anchor",
    "derivation_failure",
    "identity_mismatch",
    "materialization_failure",
]

DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "pilot_runs" / "source_owned" / "scale"


class SourceOwnedScalePilotSummary(StrictModel):
    """Allow-listed measurement output with no source or provider content."""

    document_sha256: str
    pages: int
    evidence_units: int
    elapsed_seconds: float
    peak_python_allocation_mb: float | None = None
    knowledge_card_count: int
    stage_statuses: dict[str, str]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_statuses(result: SourceOwnedRealPilotSummary) -> dict[str, str]:
    evidence_status = (
        "completed"
        if result.extraction_status == "extracted" and result.evidence_units > 0
        else "failed_closed"
    )
    return {
        "evidence_ingestion": evidence_status,
        "source_owned_extraction": result.extraction_status,
        "anchor_selection": result.anchor_selection_status,
        "local_derivation": result.derivation_status,
        "canonical_fact": result.canonical_fact_status,
        "admission": result.admission_status,
        "bridge": result.bridge_status,
        "materialization": result.materialization_status,
    }


def _failed_summary(*, document_sha256: str, elapsed_seconds: float, peak_mb: float) -> SourceOwnedScalePilotSummary:
    return SourceOwnedScalePilotSummary(
        document_sha256=document_sha256,
        pages=0,
        evidence_units=0,
        elapsed_seconds=elapsed_seconds,
        peak_python_allocation_mb=peak_mb,
        knowledge_card_count=0,
        stage_statuses={
            "evidence_ingestion": "failed_closed",
            "source_owned_extraction": "failed_closed",
            "anchor_selection": "failed_closed",
            "local_derivation": "failed_closed",
            "canonical_fact": "failed_closed",
            "admission": "rejected",
            "bridge": "failed_closed",
            "materialization": "failed_closed",
        },
    )


def _write_summary(output_dir: Path, summary: SourceOwnedScalePilotSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "summary.json.tmp"
    temporary.write_text(summary.model_dump_json(exclude_none=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_dir / "summary.json")


def run_source_owned_scale_pilot(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    scenario: SourceOwnedScalePilotScenario = "success",
) -> SourceOwnedScalePilotSummary:
    """Measure one real-PDF run while delegating all pipeline behavior."""

    if scenario not in {
        "success",
        "empty_evidence",
        "missing_anchor",
        "derivation_failure",
        "identity_mismatch",
        "materialization_failure",
    }:
        raise ValueError(f"unsupported source-owned scale pilot scenario: {scenario}")
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise ValueError("source-owned scale pilot requires an existing PDF path")
    document_sha256 = _sha256_file(pdf_path)
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / pdf_path.stem

    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = run_source_owned_real_pilot(
            pdf_path,
            output_dir=target,
            scenario=scenario,  # type: ignore[arg-type]
        )
        elapsed_seconds = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        summary = SourceOwnedScalePilotSummary(
            document_sha256=result.document_sha256,
            pages=result.pages,
            evidence_units=result.evidence_units,
            elapsed_seconds=elapsed_seconds,
            peak_python_allocation_mb=peak_bytes / (1024 * 1024),
            knowledge_card_count=result.knowledge_card_count,
            stage_statuses=_stage_statuses(result),
        )
    except Exception:
        elapsed_seconds = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        summary = _failed_summary(
            document_sha256=document_sha256,
            elapsed_seconds=elapsed_seconds,
            peak_mb=peak_bytes / (1024 * 1024),
        )
    finally:
        tracemalloc.stop()

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
    summary = run_source_owned_scale_pilot(
        args.pdf_path,
        output_dir=args.output_dir,
        scenario=args.scenario,
    )
    print(summary.model_dump_json(exclude_none=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
