"""Reusable offline helpers for the threshold V3/Echo V2 regression tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from examcard.compilers.step3_card_v3 import Step3CompilationV3, compile_reviewed_fixture_v3
from examcard.compilers.step3_response_v3 import (
    build_fake_single_card_result_v3,
    build_single_card_model_input_v3,
    encode_single_card_response_v3,
    make_single_card_inspector_v3,
)
from examcard.knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
from tests.test_gates_review_typed_evidence_export_v2 import _export


def make_v2_fixture() -> ReviewedTypedEvidenceFixtureV2:
    return _export()


def write_v2_fixture(root: Path) -> tuple[Path, ReviewedTypedEvidenceFixtureV2, str]:
    root.mkdir(parents=True, exist_ok=True)
    fixture = make_v2_fixture()
    path = root / "reviewed-typed-evidence-v2.json"
    path.write_text(json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    return path, fixture, hashlib.sha256(path.read_bytes()).hexdigest()


def compile_v3(root: Path) -> tuple[Path, ReviewedTypedEvidenceFixtureV2, str, Step3CompilationV3]:
    path, fixture, file_sha = write_v2_fixture(root)
    return path, fixture, file_sha, compile_reviewed_fixture_v3(
        fixture, source_typed_fixture_file_sha256=file_sha
    )


def valid_v3_response_content(compilation: Step3CompilationV3, index: int = 0) -> str:
    model_input = build_single_card_model_input_v3(compilation, index)
    encoded = encode_single_card_response_v3(build_fake_single_card_result_v3(model_input))
    return json.loads(encoded.decode("utf-8"))["choices"][0]["message"]["content"]


def seed_v3_echo_run(root: Path, compilation: Step3CompilationV3) -> Path:
    run_dir = root / "echo-run-v3"
    for sequence, contract in enumerate(compilation.contracts, start=1):
        card_dir = run_dir / "step3" / "cards" / f"{sequence:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
        card_dir.mkdir(parents=True, exist_ok=True)
        model_input = build_single_card_model_input_v3(compilation, sequence - 1)
        response_bytes = encode_single_card_response_v3(build_fake_single_card_result_v3(model_input))
        (card_dir / "attempt-1.response.raw.json").write_bytes(response_bytes)
        gate = make_single_card_inspector_v3(model_input)(response_bytes)
        (card_dir / "attempt-1.gate.json").write_text(
            json.dumps(gate.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
        )
    return run_dir


def approve_sheet(sheet_path: Path, *, reviewer: str = "b2-reviewer") -> None:
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    for row in sheet["cards"]:
        row["disposition"] = "approved"
        row["reviewer"] = reviewer
        row["basis"] = "逐卡核对 threshold source contract 与 echo 记录"
        row["b2_review"] = {"checked_at": "2026-09-10T00:00:00Z", "crop_sha256": "a" * 64}
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = (
    "approve_sheet",
    "compile_v3",
    "make_v2_fixture",
    "seed_v3_echo_run",
    "valid_v3_response_content",
    "write_v2_fixture",
)
