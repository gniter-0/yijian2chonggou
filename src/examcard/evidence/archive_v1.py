"""Read-only, deterministic release evidence packaging.

This module is deliberately downstream of the frozen P4-P8 contracts.  It
records references to existing evidence and verification results, but it does
not derive release, publication, or delivery authority and it never writes to
the source evidence files.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from ..common.hashing import sha256_bytes
from ..common.io import atomic_write_bytes

ARCHIVE_OUTPUT_FILES: Final[tuple[str, ...]] = (
    "release.json",
    "verification.json",
    "publication.json",
    "delivery.json",
    "artifacts.json",
    "README.md",
)

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_KEY_PARTS = (
    "secret",
    "token",
    "credential",
    "password",
    "private_key",
    "api_key",
    "authorization",
    "authority",
    "manual_override",
    "override",
)


def _reject_sensitive_key(key: str) -> None:
    lowered = key.casefold().replace("-", "_")
    if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
        raise ValueError(f"release evidence contains forbidden field: {key}")


def _safe_json(value: object) -> Any:
    """Copy JSON-shaped verification data while rejecting sensitive fields."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("release verification keys must be strings")
            _reject_sensitive_key(key)
            copied[key] = _safe_json(item)
        return copied
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    raise TypeError("release verification must contain JSON-compatible values")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _require_regular_file(path: Path, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return candidate


def _source_reference(path: Path | None, label: str) -> dict[str, object] | None:
    if path is None:
        return None
    source = _require_regular_file(path, label)
    if any(part in source.name.casefold() for part in _FORBIDDEN_KEY_PARTS):
        raise ValueError(f"{label} filename is not safe to archive")
    try:
        raw = source.read_bytes()
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error
    return {
        "source": label,
        "filename": source.name,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def _verification_payload(
    verification: Mapping[str, object] | Path | None,
) -> dict[str, object]:
    if verification is None:
        return {"source": None, "results": {}}
    if isinstance(verification, Path):
        source = _source_reference(verification, "verification-results")
        assert source is not None
        try:
            parsed = json.loads(verification.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("verification results file must contain valid JSON") from error
        return {"source": source, "results": _safe_json(parsed)}
    return {"source": None, "results": _safe_json(verification)}


def _validate_release_identity(release_id: str, commit_sha: str, tag: str | None) -> None:
    if not _RELEASE_ID.fullmatch(release_id):
        raise ValueError("release_id must be a path-safe release identifier")
    if not _COMMIT_SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha must be a full hexadecimal Git SHA")
    if tag is not None and (not tag or "\r" in tag or "\n" in tag):
        raise ValueError("tag must be a non-empty single-line string")


def _validate_output_dir(archive_dir: Path) -> Path:
    target = Path(archive_dir)
    if target.is_symlink():
        raise ValueError("evidence archive destination must not be a symlink")
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise ValueError("evidence archive destination is not a directory")
    unexpected = sorted(
        item.name for item in target.iterdir() if item.name not in ARCHIVE_OUTPUT_FILES
    )
    if unexpected:
        raise ValueError(f"evidence archive contains unexpected entries: {unexpected}")
    return target


def _assert_targets_do_not_overwrite_sources(
    archive_dir: Path,
    payloads: Mapping[str, bytes],
    source_paths: tuple[Path, ...],
) -> None:
    sources = {path.resolve() for path in source_paths}
    for filename in payloads:
        target = (archive_dir / filename).resolve()
        if target in sources:
            raise ValueError("evidence archive would overwrite source evidence")


def _readme(
    *,
    release_id: str,
    commit_sha: str,
    tag: str | None,
    publication_present: bool,
    delivery_present: bool,
    artifacts_present: bool,
) -> bytes:
    tag_line = tag if tag is not None else "(none)"
    lines = (
        "# Release Evidence Archive",
        "",
        "This bundle is observational evidence for a frozen release.",
        "It is not release, publication, or delivery authority.",
        "",
        f"Release ID: {release_id}",
        f"Commit SHA: {commit_sha}",
        f"Tag: {tag_line}",
        "",
        f"Publication receipt reference: {'present' if publication_present else 'absent'}",
        f"Delivery receipt reference: {'present' if delivery_present else 'absent'}",
        f"Artifact manifest reference: {'present' if artifacts_present else 'absent'}",
        "",
        "Source evidence remains authoritative and is not modified by this bundle.",
        "",
    )
    return "\n".join(lines).encode("utf-8")


def build_release_evidence_archive(
    *,
    archive_dir: Path,
    release_id: str,
    commit_sha: str,
    tag: str | None = None,
    verification: Mapping[str, object] | Path | None = None,
    publication_receipt: Path | None = None,
    delivery_receipt: Path | None = None,
    artifact_manifest: Path | None = None,
) -> Path:
    """Build a deterministic evidence bundle from existing evidence references.

    ``archive_dir`` is the exact output directory (normally named
    ``release-evidence``).  Source receipt and manifest files are read only;
    their contents are represented by safe filename, byte-count, and SHA256
    references rather than copied into the bundle.
    """

    _validate_release_identity(release_id, commit_sha, tag)
    target = _validate_output_dir(archive_dir)
    publication = _source_reference(publication_receipt, "publication-receipt")
    delivery = _source_reference(delivery_receipt, "delivery-receipt")
    artifacts = _source_reference(artifact_manifest, "artifact-manifest")
    verification_payload = _verification_payload(verification)

    release_payload = {
        "commit_sha": commit_sha,
        "release_id": release_id,
        "tag": tag,
    }
    payloads: dict[str, bytes] = {
        "release.json": _json_bytes(release_payload),
        "verification.json": _json_bytes(verification_payload),
        "publication.json": _json_bytes({"receipt": publication}),
        "delivery.json": _json_bytes({"receipt": delivery}),
        "artifacts.json": _json_bytes({"manifest": artifacts}),
        "README.md": _readme(
            release_id=release_id,
            commit_sha=commit_sha,
            tag=tag,
            publication_present=publication is not None,
            delivery_present=delivery is not None,
            artifacts_present=artifacts is not None,
        ),
    }
    source_paths = tuple(
        path
        for path in (publication_receipt, delivery_receipt, artifact_manifest)
        if path is not None
    ) + ((verification,) if isinstance(verification, Path) else ())
    _assert_targets_do_not_overwrite_sources(target, payloads, source_paths)
    for filename in ARCHIVE_OUTPUT_FILES:
        atomic_write_bytes(target / filename, payloads[filename])
    return target


create_release_evidence_archive = build_release_evidence_archive

__all__ = (
    "ARCHIVE_OUTPUT_FILES",
    "build_release_evidence_archive",
    "create_release_evidence_archive",
)
