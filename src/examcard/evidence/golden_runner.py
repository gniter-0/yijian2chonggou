"""Run Phase 1 only against the registered golden pages, never the full corpus."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from examcard.materials.schema import MaterialProfile

from .engine_types import VisualReviewHint
from .golden import ExpectedEvidenceMatch, GoldenAssertion, GoldenSample, GoldenSampleRegistry, verify_golden_sources
from .ingest import EvidenceEngine
from .layout import normalized_compare_text


_PAGE_FROM_ASSERTION = re.compile(r"(?:^|_)page(?P<page>\d+)(?:_|$)")


def _assertion_page(assertion: GoldenAssertion, sample: GoldenSample) -> tuple[int, ...]:
    match = _PAGE_FROM_ASSERTION.search(assertion.assertion_id)
    if match:
        page_num = int(match.group("page"))
        if page_num not in sample.selected_pages:
            raise ValueError(f"{assertion.assertion_id} refers to a page outside its sample")
        return (page_num,)
    return sample.selected_pages


def _review_hints(sample: GoldenSample) -> tuple[VisualReviewHint, ...]:
    hints: list[VisualReviewHint] = []
    for assertion in sample.assertions:
        pages = _assertion_page(assertion, sample)
        for native_text, _ in assertion.expected_conflicts:
            for page_num in pages:
                hints.append(
                    VisualReviewHint(
                        hint_id=f"{assertion.assertion_id}_{page_num}",
                        page_num=page_num,
                        observed_text=native_text,
                        description=assertion.description,
                    )
                )
    return tuple(hints)


def _contains_text(units: list[Any], expected: str) -> bool:
    needle = normalized_compare_text(expected)
    return any(
        needle in normalized_compare_text(unit.raw_text) or needle in normalized_compare_text(unit.canonical_text)
        for unit in units
    )


def _matches_expected_evidence(units: list[Any], expected: ExpectedEvidenceMatch) -> bool:
    needle = normalized_compare_text(expected.text)
    for unit in units:
        if not (
            needle in normalized_compare_text(unit.raw_text)
            or needle in normalized_compare_text(unit.canonical_text)
        ):
            continue
        if expected.kind is not None and unit.kind != expected.kind:
            continue
        if expected.semantic_role is not None and unit.semantic_role != expected.semantic_role:
            continue
        if expected.source_quality is not None and unit.source_quality != expected.source_quality:
            continue
        if expected.must_cover is not None and unit.must_cover_by_source != expected.must_cover:
            continue
        return True
    return False


def _evaluate_assertion(assertion: GoldenAssertion, sample: GoldenSample, pages: dict[int, Any]) -> dict[str, Any]:
    relevant_pages = _assertion_page(assertion, sample)
    units = [unit for page_num in relevant_pages for unit in pages[page_num].evidence_units]
    conflicts = [conflict for page_num in relevant_pages for conflict in pages[page_num].source_conflicts]
    missing_kinds = sorted(kind.value for kind in assertion.expected_evidence_kinds if kind not in {unit.kind for unit in units})
    missing_texts = [text for text in assertion.expected_texts if not _contains_text(units, text)]
    missing_conflicts = [
        [native_text, visual_text]
        for native_text, visual_text in assertion.expected_conflicts
        if not any(
            normalized_compare_text(native_text) in normalized_compare_text(conflict.native_text or "")
            and normalized_compare_text(visual_text) in normalized_compare_text(conflict.visual_text or "")
            for conflict in conflicts
        )
    ]
    missing_must_cover = []
    if assertion.must_cover:
        for expected in assertion.expected_texts:
            matched = [
                unit
                for unit in units
                if normalized_compare_text(expected) in normalized_compare_text(unit.raw_text)
                or normalized_compare_text(expected) in normalized_compare_text(unit.canonical_text)
            ]
            if not matched or not any(unit.must_cover_by_source for unit in matched):
                missing_must_cover.append(expected)
    missing_expected_matches = [
        expected.model_dump(mode="json")
        for expected in assertion.expected_matches
        if not _matches_expected_evidence(units, expected)
    ]
    passed = not (missing_kinds or missing_texts or missing_conflicts or missing_must_cover or missing_expected_matches)
    return {
        "assertion_id": assertion.assertion_id,
        "pages": list(relevant_pages),
        "passed": passed,
        "missing_evidence_kinds": missing_kinds,
        "missing_texts": missing_texts,
        "missing_conflicts": missing_conflicts,
        "missing_must_cover": missing_must_cover,
        "missing_expected_matches": missing_expected_matches,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_golden_registry(
    *,
    workspace_root: Path,
    registry: GoldenSampleRegistry,
    profile: MaterialProfile,
    engine: EvidenceEngine,
    output_root: Path,
    sample_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Run selected golden pages and evaluate only machine-readable seeded assertions."""
    if profile.profile_id != registry.material_profile_id:
        raise ValueError("golden registry material_profile_id must match the active Material Profile")
    source_errors = verify_golden_sources(registry, workspace_root)
    if source_errors:
        raise ValueError("golden source verification failed: " + "; ".join(source_errors))
    reports: list[dict[str, Any]] = []
    selected_samples = [sample for sample in registry.samples if sample_ids is None or sample.sample_id in sample_ids]
    if not selected_samples:
        raise ValueError("no golden samples matched")
    for sample in selected_samples:
        sample_output = output_root / sample.sample_id
        try:
            result = engine.process_pages(
                workspace_root / sample.pdf_relative_path,
                page_numbers=sample.selected_pages,
                output_dir=sample_output,
                review_hints=_review_hints(sample),
            )
            pages = {page.page_num: page for page in result.pages}
            assertions = [_evaluate_assertion(assertion, sample, pages) for assertion in sample.assertions]
            report: dict[str, Any] = {
                "sample_id": sample.sample_id,
                "pdf_relative_path": sample.pdf_relative_path,
                "pdf_sha256": sample.pdf_sha256,
                "selected_pages": list(sample.selected_pages),
                "annotation_status": sample.annotation_status.value,
                "quality": result.report,
                "assertions": assertions,
                "assertions_passed": all(item["passed"] for item in assertions),
                "error": None,
            }
        except Exception as exc:  # Preserve batch evidence when an individual golden sample fails.
            report = {
                "sample_id": sample.sample_id,
                "pdf_relative_path": sample.pdf_relative_path,
                "selected_pages": list(sample.selected_pages),
                "annotation_status": sample.annotation_status.value,
                "assertions": [],
                "assertions_passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        reports.append(report)
    assertion_reports = [assertion for sample in reports for assertion in sample["assertions"]]
    report = {
        "registry_id": registry.registry_id,
        "material_profile_id": registry.material_profile_id,
        "golden_registry_ready_for_phase1": registry.ready_for_phase1,
        "sample_count": len(reports),
        "selected_page_count": sum(len(sample["selected_pages"]) for sample in reports),
        "seeded_assertion_count": len(assertion_reports),
        "seeded_assertions_passed": all(assertion["passed"] for assertion in assertion_reports),
        "samples": reports,
        "status": "evidence_ready_for_review" if all(sample["error"] is None for sample in reports) else "failed",
        "note": (
            "This is a selected-page Evidence Engine run. It does not change golden annotation status, "
            "does not publish content, and does not permit a Phase 2 transition by itself."
        ),
    }
    _write_report(output_root / "golden_run_report.json", report)
    return report
