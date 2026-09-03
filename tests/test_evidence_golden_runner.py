"""Golden evidence runner contract tests (offline and deterministic)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examcard.evidence.golden import GoldenAssertion, GoldenSample, GoldenSampleRegistry
from examcard.evidence.golden_runner import run_golden_registry, validate_golden_report
from examcard.materials.catalog import load_material_profile


ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_material_profile(ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml")


def _registry(*, expected_text: bool = False) -> GoldenSampleRegistry:
    assertion_texts = ("synthetic evidence",) if expected_text else ()
    samples = tuple(
        GoldenSample(
            sample_id=f"sample-{index}",
            pdf_relative_path=f"input/sample-{index}.pdf",
            pdf_sha256="0" * 64,
            selected_pages=(1, 2, 3, 4),
            focus=("synthetic",),
            annotation_status="reviewed",
            assertions=(
                GoldenAssertion(
                    assertion_id=f"sample-{index}-assertion",
                    description="synthetic assertion",
                    expected_texts=assertion_texts,
                ),
            ),
            reviewer="tester",
            reviewed_at_utc="2026-01-01T00:00:00Z",
        )
        for index in range(1, 6)
    )
    return GoldenSampleRegistry(
        registry_id="synthetic-golden-registry-v1",
        material_profile_id=PROFILE.profile_id,
        samples=samples,
    )


class _FakeEngine:
    def __init__(self, *, failed_sample_ids: set[str] | None = None) -> None:
        self.failed_sample_ids = failed_sample_ids or set()

    def process_pages(self, pdf_path: Path, *, page_numbers, output_dir, review_hints):
        sample_id = pdf_path.stem
        if sample_id in self.failed_sample_ids:
            raise RuntimeError("Authorization: Bearer synthetic-secret sk-12345678ABCDEFGHIJKLMNOP")
        pages = tuple(
            SimpleNamespace(page_num=page, evidence_units=(), source_conflicts=()) for page in page_numbers
        )
        return SimpleNamespace(pages=pages, report={"engine": "fake"})


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, registry: GoldenSampleRegistry, **kwargs):
    monkeypatch.setattr("examcard.evidence.golden_runner.verify_golden_sources", lambda *_args: [])
    return run_golden_registry(
        workspace_root=tmp_path,
        registry=registry,
        profile=PROFILE,
        output_root=tmp_path / "evidence",
        **kwargs,
    )


def test_success_report_is_persisted_and_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry()
    report = _run(monkeypatch, tmp_path, registry, engine=_FakeEngine())
    persisted = json.loads((tmp_path / "evidence" / "golden_run_report.json").read_text(encoding="utf-8"))

    assert persisted == report
    assert report["status"] == "evidence_ready_for_review"
    assert validate_golden_report(report, registry=registry) == (True, "golden evidence report validated")


def test_sample_exception_is_failed_and_cannot_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry()
    report = _run(monkeypatch, tmp_path, registry, engine=_FakeEngine(failed_sample_ids={"sample-3"}))
    persisted_text = (tmp_path / "evidence" / "golden_run_report.json").read_text(encoding="utf-8")

    assert report["status"] == "failed"
    assert report["samples"][2]["error"]
    assert "synthetic-secret" not in persisted_text
    assert validate_golden_report(report, registry=registry)[0] is False


def test_assertion_failure_is_not_a_ready_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry(expected_text=True)
    report = _run(monkeypatch, tmp_path, registry, engine=_FakeEngine())

    assert report["status"] == "evidence_ready_for_review"
    assert report["seeded_assertions_passed"] is False
    assert validate_golden_report(report, registry=registry) == (False, "golden report assertions did not pass")


def test_selected_sample_subset_is_bound_to_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry()
    selected = {"sample-1", "sample-4"}
    report = _run(monkeypatch, tmp_path, registry, engine=_FakeEngine(), sample_ids=selected)

    assert report["sample_count"] == 2
    assert report["selected_page_count"] == 8
    assert validate_golden_report(report, registry=registry, sample_ids=selected)[0] is True
    assert validate_golden_report(report, registry=registry)[0] is False


def test_material_profile_mismatch_is_rejected_before_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = _registry()
    mismatched = PROFILE.model_copy(update={"profile_id": "other-profile"})
    monkeypatch.setattr("examcard.evidence.golden_runner.verify_golden_sources", lambda *_args: [])

    with pytest.raises(ValueError, match="material_profile_id"):
        run_golden_registry(
            workspace_root=tmp_path,
            registry=registry,
            profile=mismatched,
            engine=_FakeEngine(),
            output_root=tmp_path / "evidence",
        )


def test_unknown_report_field_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry()
    report = _run(monkeypatch, tmp_path, registry, engine=_FakeEngine())
    forged = {**report, "unexpected": True}

    assert validate_golden_report(forged, registry=registry) == (
        False,
        "golden report has missing or unknown fields",
    )
