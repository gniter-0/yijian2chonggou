"""gates.release.checkpoint 单测（蓝本：旧仓 test_phase2r6_...py；以新仓自身产物为审计对象）。"""
from __future__ import annotations

import json
from pathlib import Path

from examcard.common.schema import strict_schema
from examcard.gates.release.checkpoint import (
    audit_domain_neutrality,
    audit_strict_schemas,
    digest_artifact,
)
from examcard.knowledge.contracts.admission_fixtures import ReviewedCandidate


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_strict_schema_export_passes_audit(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    schema = strict_schema(ReviewedCandidate)
    path = _write(root / "schemas" / "reviewed_candidate.schema.json", json.dumps(schema, ensure_ascii=False))
    audits = audit_strict_schemas(root, (path,))
    assert len(audits) == 1
    assert audits[0].valid
    assert audits[0].closed_object_count == audits[0].object_count
    assert audits[0].root_schema_version_required


def test_open_schema_fails_audit(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    open_schema = {
        "type": "object",
        "properties": {"anything": {"type": "string"}},
        "required": [],
    }
    path = _write(root / "schemas" / "open.schema.json", json.dumps(open_schema))
    audits = audit_strict_schemas(root, (path,))
    assert not audits[0].valid
    assert not audits[0].root_schema_version_required


def test_domain_neutrality_flags_markers(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    clean = _write(root / "src" / "clean_module.py", "def compile_cards():\n    return ()\n")
    dirty = _write(
        root / "src" / "dirty_module.py",
        "DATA = 'data/structured_gonglu'\nimport phase2r52_thing\n",
    )
    markers = ("gonglu", "data/structured_", "phase2r52")
    audits = audit_domain_neutrality(root, (clean, dirty), forbidden_markers=markers)
    by_path = {item.path: item for item in audits}
    clean_audit = by_path["src/clean_module.py"]
    assert clean_audit.domain_neutral and clean_audit.no_subject_paths and clean_audit.no_legacy_phase_imports
    dirty_audit = by_path["src/dirty_module.py"]
    assert not dirty_audit.domain_neutral
    assert set(dirty_audit.markers) == set(markers)
    assert not dirty_audit.no_subject_paths
    assert not dirty_audit.no_legacy_phase_imports


def test_digest_artifact_hashes_bytes(repo_tmp_path: Path) -> None:
    root = repo_tmp_path
    path = _write(root / "docs" / "note.md", "release note\n")
    digest = digest_artifact(root, path)
    assert digest.path == "docs/note.md"
    assert digest.bytes == path.stat().st_size
    assert len(digest.sha256) == 64
