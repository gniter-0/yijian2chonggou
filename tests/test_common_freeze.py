"""common/freeze：冻结构建、重验与快照 diff（无 glob、无 git、仓库相对路径）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from examcard.common.freeze import (
    FreezeSnapshot,
    build_freeze,
    diff_freeze,
    freeze_artifact,
    verify_freeze,
)
from examcard.common.hashing import canonical_hash, file_hash


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return tmp_path


def test_freeze_artifact_captures_posix_path_bytes_and_sha(root: Path) -> None:
    artifact = freeze_artifact(root, Path("sub") / "b.txt", role="code")
    assert artifact.path == "sub/b.txt"
    assert artifact.role == "code"
    assert artifact.bytes == 4
    assert artifact.sha256 == file_hash(root / "sub" / "b.txt")


def test_freeze_artifact_defaults_role(root: Path) -> None:
    assert freeze_artifact(root, "a.txt").role == "artifact"


def test_freeze_artifact_rejects_empty_file(root: Path) -> None:
    (root / "empty.txt").write_text("", encoding="utf-8")
    with pytest.raises(ValidationError):
        freeze_artifact(root, "empty.txt")


def test_freeze_artifact_rejects_absolute_path(root: Path) -> None:
    with pytest.raises(ValueError):
        freeze_artifact(root, root / "a.txt")


def test_freeze_artifact_rejects_escape(root: Path) -> None:
    with pytest.raises(ValueError):
        freeze_artifact(root, "../outside.txt")


def test_build_freeze_dedupes_case_insensitively_and_sorts(root: Path) -> None:
    snapshot = build_freeze(root, ["sub/b.txt", "A.TXT", ("a.txt", "code")])
    assert [item.path for item in snapshot.artifacts] == ["a.txt", "sub/b.txt"]
    assert snapshot.artifacts[0].role == "code"
    assert snapshot.artifact_count == 2
    assert snapshot.aggregate_sha256 == canonical_hash(snapshot.artifacts)


def test_build_freeze_requires_entries(root: Path) -> None:
    with pytest.raises(ValueError):
        build_freeze(root, [])


def test_verify_freeze_true_then_detects_content_drift(root: Path) -> None:
    snapshot = build_freeze(root, ["a.txt", "sub/b.txt"])
    assert verify_freeze(root, snapshot) is True
    (root / "a.txt").write_text("mutated", encoding="utf-8")
    assert verify_freeze(root, snapshot) is False
    assert diff_freeze(root, snapshot) == ("a.txt",)


def test_diff_freeze_reports_missing_file(root: Path) -> None:
    snapshot = build_freeze(root, ["a.txt", "sub/b.txt"])
    (root / "sub" / "b.txt").unlink()
    assert diff_freeze(root, snapshot) == ("sub/b.txt",)


def test_snapshot_rejects_count_mismatch(root: Path) -> None:
    snapshot = build_freeze(root, ["a.txt"])
    payload = snapshot.model_dump(mode="json")
    payload["artifact_count"] = 2
    with pytest.raises(ValidationError):
        FreezeSnapshot.model_validate(payload)


def test_snapshot_rejects_duplicate_paths(root: Path) -> None:
    snapshot = build_freeze(root, ["a.txt"])
    payload = snapshot.model_dump(mode="json")
    payload["artifacts"] = payload["artifacts"] * 2
    payload["artifact_count"] = 2
    payload["aggregate_sha256"] = canonical_hash(payload["artifacts"])
    with pytest.raises(ValidationError):
        FreezeSnapshot.model_validate(payload)


def test_snapshot_rejects_aggregate_tamper(root: Path) -> None:
    snapshot = build_freeze(root, ["a.txt"])
    payload = snapshot.model_dump(mode="json")
    payload["aggregate_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        FreezeSnapshot.model_validate(payload)
