"""Migration CLI integration tests for Echo Review V2."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from migration import run_echo_step3_review
from tests.fixtures.step3_v3 import approve_sheet, compile_v3, seed_v3_echo_run


def test_echo_review_cli_v2_emits_and_ingests_typed_outputs(tmp_path: Path, monkeypatch) -> None:
    typed_path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    run_dir = seed_v3_echo_run(tmp_path, compilation)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_echo_step3_review.py",
            "emit",
            "--echo-run-dir",
            str(run_dir),
            "--typed-fixture",
            str(typed_path),
            "--out-dir",
            str(out_dir),
            "--contract-version",
            "v2",
        ],
    )
    assert run_echo_step3_review.main() == 0
    sheet_path = out_dir / "echo_step3_review_sheet_v2.json"
    assert sheet_path.is_file()
    approve_sheet(sheet_path)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_echo_step3_review.py",
            "ingest",
            "--sheet",
            str(sheet_path),
            "--echo-run-dir",
            str(run_dir),
            "--typed-fixture",
            str(typed_path),
            "--echo-run-id",
            "cli-v2",
            "--out-dir",
            str(out_dir),
            "--contract-version",
            "v2",
        ],
    )
    assert run_echo_step3_review.main() == 0
    release = json.loads((out_dir / "echo-batch-release.json").read_text(encoding="utf-8"))
    handoff = json.loads((out_dir / "echo-batch-handoff.json").read_text(encoding="utf-8"))
    assert release["schema_version"] == "echo-batch-release/2.0"
    assert handoff["schema_version"] == "echo-batch-step4-handoff/2.0"
