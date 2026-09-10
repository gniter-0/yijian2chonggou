"""CLI option compatibility tests for the V3/Echo V2 migration surfaces."""
from __future__ import annotations

import sys

from migration import run_exam_pipeline


def test_run_exam_pipeline_accepts_v3_and_echo_batch_v2(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step3,step4",
            "--dry-run",
            "--step3-contract",
            "v3",
            "--step4-source",
            "echo-batch",
        ],
    )
    args = run_exam_pipeline._parse_args()
    assert args.step3_contract == "v3"
    assert args.step4_source == "echo-batch"


def test_run_exam_pipeline_keeps_v2_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_exam_pipeline.py", "--exam", "demo", "--stages", "step3", "--dry-run"],
    )
    args = run_exam_pipeline._parse_args()
    assert args.step3_contract == "v2"
    assert args.step4_source == "pilot-release"
