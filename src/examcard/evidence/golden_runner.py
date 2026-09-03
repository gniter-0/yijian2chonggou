"""Run Phase 1 only against the registered golden pages, never the full corpus."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from examcard.materials.schema import MaterialProfile
from examcard.providers.base import sanitized_error_summary

from .engine_types import VisualReviewHint
from .golden import ExpectedEvidenceMatch, GoldenAssertion, GoldenSample, GoldenSampleRegistry, verify_golden_sources
from .ingest import EvidenceEngine
from .layout import normalized_compare_text


_PAGE_FROM_ASSERTION = re.compile(r"(?:^|_)page(?P<page>\d+)(?:_|$)")

_GOLDEN_REPORT_KEYS = frozenset(
    {
        "registry_id",
        "material_profile_id",
        "golden_registry_ready_for_phase1",
        "sample_count",
        "selected_page_count",
        "seeded_assertion_count",
        "seeded_assertions_passed",
        "samples",
        "status",
        "note",
    }
)
_GOLDEN_SAMPLE_KEYS = frozenset(
    {
        "sample_id",
        "pdf_relative_path",
        "pdf_sha256",
        "selected_pages",
        "annotation_status",
        "quality",
        "assertions",
        "assertions_passed",
        "error",
    }
)
_GOLDEN_ASSERTION_KEYS = frozenset(
    {
        "assertion_id",
        "pages",
        "passed",
        "missing_evidence_kinds",
        "missing_texts",
        "missing_conflicts",
        "missing_must_cover",
        "missing_expected_matches",
    }
)


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


def validate_golden_report(
    report: object,
    *,
    registry: GoldenSampleRegistry,
    sample_ids: set[str] | None = None,
) -> tuple[bool, str]:
    """Validate the persisted golden report before it can satisfy Step 1.

    The report is a mapping contract, not an object with optional attributes.  A
    resume is valid only when the report explicitly proves a successful run for
    the current registry and selected sample set.
    """

    if not isinstance(report, Mapping):
        return False, "golden report is not a mapping"
    if set(report) != _GOLDEN_REPORT_KEYS:
        return False, "golden report has missing or unknown fields"

    expected_samples = {sample.sample_id: sample for sample in registry.samples}
    requested_ids = set(expected_samples) if sample_ids is None else set(sample_ids)
    if not requested_ids or not requested_ids.issubset(expected_samples):
        return False, "golden report sample selection is not registry-bound"
    if not registry.ready_for_phase1:
        return False, "golden registry is not ready for phase 1"
    if report["registry_id"] != registry.registry_id:
        return False, "golden report registry identity mismatch"
    if report["material_profile_id"] != registry.material_profile_id:
        return False, "golden report material profile identity mismatch"
    if report["golden_registry_ready_for_phase1"] is not True:
        return False, "golden report does not confirm registry readiness"
    if report["status"] != "evidence_ready_for_review":
        return False, "golden report status is not successful"
    if report["seeded_assertions_passed"] is not True:
        return False, "golden report assertions did not pass"
    for key in ("sample_count", "selected_page_count", "seeded_assertion_count"):
        if not isinstance(report[key], int) or isinstance(report[key], bool):
            return False, f"golden report {key} is not an integer"

    selected = [sample for sample in registry.samples if sample.sample_id in requested_ids]
    expected_assertion_count = sum(len(sample.assertions) for sample in selected)
    expected_page_count = sum(len(sample.selected_pages) for sample in selected)
    if report["sample_count"] != len(selected):
        return False, "golden report sample count mismatch"
    if report["selected_page_count"] != expected_page_count:
        return False, "golden report selected page count mismatch"
    if report["seeded_assertion_count"] != expected_assertion_count:
        return False, "golden report assertion count mismatch"
    if not isinstance(report["samples"], list) or len(report["samples"]) != len(selected):
        return False, "golden report samples are not a complete list"
    if not isinstance(report["note"], str) or not report["note"]:
        return False, "golden report note is missing"

    seen_ids: set[str] = set()
    for raw_sample in report["samples"]:
        if not isinstance(raw_sample, Mapping) or set(raw_sample) != _GOLDEN_SAMPLE_KEYS:
            return False, "golden report sample has missing or unknown fields"
        sample_id = raw_sample["sample_id"]
        if not isinstance(sample_id, str) or sample_id not in requested_ids or sample_id in seen_ids:
            return False, "golden report sample identity is invalid"
        seen_ids.add(sample_id)
        expected = expected_samples[sample_id]
        if (
            raw_sample["pdf_relative_path"] != expected.pdf_relative_path
            or raw_sample["pdf_sha256"] != expected.pdf_sha256
            or raw_sample["selected_pages"] != list(expected.selected_pages)
            or raw_sample["annotation_status"] != expected.annotation_status.value
        ):
            return False, "golden report sample identity does not match registry"
        if raw_sample["error"] is not None or raw_sample["assertions_passed"] is not True:
            return False, "golden report contains a failed sample"
        if not isinstance(raw_sample["quality"], Mapping) or not isinstance(raw_sample["assertions"], list):
            return False, "golden report sample payload is malformed"
        if len(raw_sample["assertions"]) != len(expected.assertions):
            return False, "golden report assertion list length mismatch"
        for raw_assertion, expected_assertion in zip(raw_sample["assertions"], expected.assertions, strict=True):
            if not isinstance(raw_assertion, Mapping) or set(raw_assertion) != _GOLDEN_ASSERTION_KEYS:
                return False, "golden report assertion has missing or unknown fields"
            if (
                raw_assertion["assertion_id"] != expected_assertion.assertion_id
                or raw_assertion["pages"] != list(_assertion_page(expected_assertion, expected))
                or raw_assertion["passed"] is not True
                or any(raw_assertion[key] != [] for key in _GOLDEN_ASSERTION_KEYS - {"assertion_id", "pages", "passed"})
            ):
                return False, "golden report contains an incomplete assertion"

    if seen_ids != requested_ids:
        return False, "golden report sample selection is incomplete"
    return True, "golden evidence report validated"


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
                "error": sanitized_error_summary(exc),
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
