"""gates.release.readiness 单测（蓝本：旧仓 test_phase2r15_*.py + test_phase2r152_*.py）。

合成 spec + repo_tmp_path 运行目录 fixture；五金样本实跑与钉死路径集留旧仓。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from examcard.gates.release.readiness import (
    RemediationExpectations,
    SampleSpec,
    audit_remediation_document,
    audit_stage_transport,
    build_freeze_audit_from_snapshots,
    _collect_forbidden_keys,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_transport_audit_fails_closed_on_missing_artifacts(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    report = {
        "sample_id": "sample_x",
        "step2a": {
            "request_path": "runs/missing-request.json",
            "record_path": "runs/missing-record.json",
            "cache_path": "runs/missing-cache.json",
            "journal_path": "runs/missing-journal.jsonl",
            "state": "failed",
            "http_status": 500,
            "schema_valid": False,
            "automatic_retry_enabled": True,
        },
    }
    _write_json(root / "runs" / "report.json", report)
    audit = audit_stage_transport(root=root, report=report, report_path="runs/report.json", stage="step2a")
    assert not audit.valid
    assert "missing_step2a_request_path" in audit.failure_codes
    assert "step2a_transport_not_success_200" in audit.failure_codes
    assert "step2a_schema_not_valid" in audit.failure_codes
    assert "step2a_automatic_retry_not_disabled" in audit.failure_codes
    assert audit.request_id == "missing"


def test_freeze_audit_from_snapshots_detects_drift(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    artifact = root / "frozen" / "a.json"
    _write_json(artifact, {"value": 1})
    digest = _sha(artifact.read_bytes())
    before = {"frozen/a.json": digest}
    unchanged = build_freeze_audit_from_snapshots(root=root, before=before, after=dict(before))
    assert unchanged.valid and unchanged.inputs_unchanged
    drifted = build_freeze_audit_from_snapshots(root=root, before=before, after={"frozen/a.json": "0" * 64})
    assert not drifted.valid
    assert drifted.changed_paths == ("frozen/a.json",)


def test_collect_forbidden_keys_finds_nested_pollution() -> None:
    payload = {"facts": [{"fact_id": "f1", "grouping_obligations": []}], "meta": {"review_state": "x"}}
    assert _collect_forbidden_keys(payload) == {"grouping_obligations", "review_state"}


def test_remediation_audit_parameterized_expectations(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    document = {
        "phase": "9Z.remediation",
        "sample_id": "sample_y",
        "accepted": False,
        "frozen_inputs_unchanged": False,
        "network_calls": 2,
        "source_mutations": 1,
        "step2a": {
            "request_path": "runs/none-a.json",
            "record_path": "runs/none-b.json",
            "cache_path": "runs/none-c.json",
            "journal_path": "runs/none-d.jsonl",
            "state": "failed",
            "http_status": 500,
            "schema_valid": False,
            "automatic_retry_enabled": True,
        },
        "step2b": {
            "request_path": "runs/none-e.json",
            "record_path": "runs/none-f.json",
            "cache_path": "runs/none-g.json",
            "journal_path": "runs/none-h.jsonl",
            "state": "failed",
            "http_status": 500,
            "schema_valid": False,
            "automatic_retry_enabled": True,
        },
    }
    _write_json(root / "runs" / "remediation.json", document)
    expectations = RemediationExpectations(
        phase="9Z.expected",
        sample_id="sample_z",
        network_calls=4,
        expected_obligation_count=42,
        expected_projection_count=11,
        expected_grouping_obligation_count=4,
        expected_projected_card_count=4,
    )
    audit = audit_remediation_document(
        root=root,
        document=document,
        expectations=expectations,
        forbidden_request_ids=set(),
        report_path="runs/remediation.json",
    )
    assert not audit.valid
    for code in (
        "remediation_phase_mismatch",
        "remediation_sample_mismatch",
        "remediation_not_accepted",
        "remediation_frozen_input_drift",
        "remediation_network_call_budget_mismatch",
        "remediation_source_mutation_detected",
        "remediation_source_obligation_gate_failed",
        "remediation_canonical_projection_failed",
        "remediation_v25b_grouping_gate_failed",
    ):
        assert code in audit.failure_codes, code


def test_sample_spec_carries_all_audited_paths() -> None:
    spec = SampleSpec(
        sample_id="sample_q",
        step2a_report="runs/a.json",
        step2b_report="runs/b.json",
        expected_obligation_count=1,
        expected_projected_fact_count=2,
        expected_card_count=3,
    )
    assert spec.expected_diagram_nodes is None
    assert spec.expected_diagram_edges is None
