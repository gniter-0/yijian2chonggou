from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.evidence.archive_v1 import (
    ARCHIVE_OUTPUT_FILES,
    build_release_evidence_archive,
)


def _write_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    verification = tmp_path / "verification.json"
    publication = tmp_path / "publication-receipt-v2.json"
    delivery = tmp_path / "delivery-receipt-v2.json"
    artifacts = tmp_path / "artifact-manifest.json"
    verification.write_text(
        json.dumps({"eolaudit": "PASS", "ruff": "PASS", "pytest": "158 passed"}),
        encoding="utf-8",
    )
    publication.write_bytes(b'{"receipt_sha256":"publication"}\n')
    delivery.write_bytes(b'{"receipt_sha256":"delivery"}\n')
    artifacts.write_bytes(b'{"manifest_sha256":"artifacts"}\n')
    return verification, publication, delivery, artifacts


def _build(
    archive_dir: Path,
    *,
    verification: Path | None,
    publication: Path | None,
    delivery: Path | None,
    artifacts: Path | None,
) -> Path:
    return build_release_evidence_archive(
        archive_dir=archive_dir,
        release_id="v1.0.0",
        commit_sha="a" * 40,
        tag="v1.0.0",
        verification=verification,
        publication_receipt=publication,
        delivery_receipt=delivery,
        artifact_manifest=artifacts,
    )


def _bundle_bytes(path: Path) -> dict[str, bytes]:
    return {name: (path / name).read_bytes() for name in ARCHIVE_OUTPUT_FILES}


def test_same_input_produces_identical_bytes(tmp_path: Path) -> None:
    verification, publication, delivery, artifacts = _write_sources(tmp_path)
    first = _build(
        tmp_path / "first",
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    second = _build(
        tmp_path / "second",
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    assert _bundle_bytes(first) == _bundle_bytes(second)


def test_missing_optional_evidence_is_explicit_and_deterministic(tmp_path: Path) -> None:
    archive = _build(
        tmp_path / "release-evidence",
        verification=None,
        publication=None,
        delivery=None,
        artifacts=None,
    )
    assert json.loads((archive / "publication.json").read_text(encoding="utf-8")) == {"receipt": None}
    assert json.loads((archive / "delivery.json").read_text(encoding="utf-8")) == {"receipt": None}
    assert json.loads((archive / "artifacts.json").read_text(encoding="utf-8")) == {"manifest": None}
    assert json.loads((archive / "verification.json").read_text(encoding="utf-8")) == {
        "results": {},
        "source": None,
    }


def test_archive_does_not_mutate_source_evidence(tmp_path: Path) -> None:
    verification, publication, delivery, artifacts = _write_sources(tmp_path)
    sources = (verification, publication, delivery, artifacts)
    before = {path: path.read_bytes() for path in sources}
    _build(
        tmp_path / "release-evidence",
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    assert {path: path.read_bytes() for path in sources} == before


def _all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys: set[str] = set()
        for key, item in value.items():
            keys.add(key)
            keys.update(_all_mapping_keys(item))
        return keys
    if isinstance(value, list):
        keys = set()
        for item in value:
            keys.update(_all_mapping_keys(item))
        return keys
    return set()


def test_archive_does_not_introduce_authority_or_override_fields(tmp_path: Path) -> None:
    verification, publication, delivery, artifacts = _write_sources(tmp_path)
    archive = _build(
        tmp_path / "release-evidence",
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    values = [
        json.loads((archive / name).read_text(encoding="utf-8"))
        for name in ARCHIVE_OUTPUT_FILES
        if name.endswith(".json")
    ]
    keys = {key.casefold() for value in values for key in _all_mapping_keys(value)}
    assert not any("authority" in key or "override" in key for key in keys)


def test_generated_files_are_stable_and_sensitive_inputs_are_rejected(tmp_path: Path) -> None:
    verification, publication, delivery, artifacts = _write_sources(tmp_path)
    archive = _build(
        tmp_path / "release-evidence",
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    first = _bundle_bytes(archive)
    _build(
        archive,
        verification=verification,
        publication=publication,
        delivery=delivery,
        artifacts=artifacts,
    )
    assert tuple(sorted(item.name for item in archive.iterdir())) == tuple(sorted(ARCHIVE_OUTPUT_FILES))
    assert _bundle_bytes(archive) == first
    with pytest.raises(ValueError, match="forbidden field"):
        build_release_evidence_archive(
            archive_dir=tmp_path / "sensitive",
            release_id="v1.0.0",
            commit_sha="a" * 40,
            verification={"api_token": "must not enter archive"},
        )
