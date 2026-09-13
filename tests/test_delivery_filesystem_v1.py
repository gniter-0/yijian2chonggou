"""Offline contract tests for the P7 filesystem-directory-v1 backend."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import examcard.delivery.filesystem_v1 as filesystem_v1
from examcard.common.hashing import canonical_hash, hash_model, sha256_bytes
from examcard.common.io import atomic_write_bytes, atomic_write_json
from examcard.delivery.filesystem_v1 import (
    FILESYSTEM_DELIVERY_MARKER_FILENAME,
    FILESYSTEM_DELIVERY_TARGET_TYPE,
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
)
from examcard.gates.release.publication_v2 import (
    PUBLICATION_PACKAGE_V2_SCHEMA,
    PUBLICATION_SCOPE_V2,
    PublicationManifestV2,
    PublicationReceiptV2,
    build_publication_artifact_v2,
    build_publication_authorization_v2,
)


def _publication_fixture(root: Path) -> tuple[Path, str, bytes]:
    publication_dir = root / "checkpoints" / "publication"
    artifact_path = publication_dir / "artifacts" / "card-0001" / "image.png"
    artifact_bytes = b"frozen-p5-image-bytes"
    artifact = build_publication_artifact_v2(
        artifact_id="card:1:image",
        relative_path="artifacts/card-0001/image.png",
        media_type="image/png",
        content=artifact_bytes,
    )
    manifest = hash_model(
        PublicationManifestV2,
        {
            "final_release_closure_sha256": "1" * 64,
            "final_release_receipt_file_sha256": "2" * 64,
            "final_release_bundle_sha256": None,
            "final_release_bundle_file_sha256": None,
            "final_release_audit_sha256": "3" * 64,
            "final_release_audit_file_sha256": "4" * 64,
            "final_release_exclusion_sha256": "5" * 64,
            "final_release_exclusion_file_sha256": "6" * 64,
            "release_card_sha256s": (),
            "release_count": 0,
            "artifacts": (artifact,),
            "artifact_count": 1,
        },
        "manifest_sha256",
    )
    authorization = build_publication_authorization_v2(
        authorization_id="filesystem-test-authorization",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=manifest.final_release_closure_sha256,
        publication_manifest_sha256=manifest.manifest_sha256,
        authorized_by="test-operator",
        authorization_reason="offline backend contract test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    package_sha256 = canonical_hash(
        {
            "schema_version": PUBLICATION_PACKAGE_V2_SCHEMA,
            "manifest_sha256": manifest.manifest_sha256,
            "artifacts": [item.model_dump(mode="json") for item in manifest.artifacts],
        }
    )
    manifest_raw = json.dumps(
        manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    receipt = hash_model(
        PublicationReceiptV2,
        {
            "publication_manifest_sha256": manifest.manifest_sha256,
            "publication_manifest_file_sha256": sha256_bytes(manifest_raw),
            "authorization_sha256": authorization.authorization_sha256,
            "final_release_closure_sha256": manifest.final_release_closure_sha256,
            "artifact_count": manifest.artifact_count,
            "package_sha256": package_sha256,
        },
        "receipt_sha256",
    )
    publication_dir.mkdir(parents=True)
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(artifact_bytes)
    atomic_write_bytes(publication_dir / "publication-manifest-v2.json", manifest_raw)
    atomic_write_json(
        publication_dir / "publication-authorization-v2.json", authorization.model_dump(mode="json")
    )
    atomic_write_json(publication_dir / "publication-receipt-v2.json", receipt.model_dump(mode="json"))
    return publication_dir, package_sha256, artifact_bytes


def _adapter_fixture(root: Path) -> tuple[FilesystemDirectoryDeliveryAdapter, Path, str, bytes]:
    publication_dir, package_sha256, artifact_bytes = _publication_fixture(root)
    destination_root = root / "external-delivery"
    destination_root.mkdir()
    target = build_filesystem_delivery_target(destination_root)
    adapter = FilesystemDirectoryDeliveryAdapter(
        publication_dir=publication_dir,
        destination_root=destination_root,
        target=target,
    )
    return adapter, destination_root, package_sha256, artifact_bytes


def _deliver(adapter: FilesystemDirectoryDeliveryAdapter, package_sha256: str) -> str:
    key = "a" * 64
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key=key,
    )
    assert result.status == "success"
    assert result.remote_reference is not None
    return key


def test_target_identity_is_canonical_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "delivery"
    root.mkdir()
    first = build_filesystem_delivery_target(root)
    second = build_filesystem_delivery_target(root / ".")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.target_type == FILESYSTEM_DELIVERY_TARGET_TYPE
    assert first.allowed_scope == PUBLICATION_SCOPE_V2
    assert first.destination_reference == root.resolve().as_uri()
    assert "secret" not in first.model_dump(mode="json")


def test_different_root_has_different_target(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = build_filesystem_delivery_target(first_root)
    second = build_filesystem_delivery_target(second_root)
    assert first.target_sha256 != second.target_sha256
    assert first.destination_config_sha256 != second.destination_config_sha256


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_destination_root_is_refused(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "destination"
    if kind == "file":
        root.write_bytes(b"not a directory")
    with pytest.raises(ValueError):
        build_filesystem_delivery_target(root)


def test_symlink_destination_root_is_refused(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    link_root = tmp_path / "link"
    real_root.mkdir()
    try:
        link_root.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(ValueError):
        build_filesystem_delivery_target(link_root)


@pytest.mark.parametrize("destination_kind", ["checkpoint", "publication"])
def test_destination_inside_run_checkpoints_is_refused(
    tmp_path: Path, destination_kind: str
) -> None:
    publication_dir, package_sha256, _ = _publication_fixture(tmp_path)
    destination_root = (
        publication_dir.parent if destination_kind == "checkpoint" else publication_dir
    )
    before = {
        path.relative_to(publication_dir): path.read_bytes()
        for path in publication_dir.rglob("*")
        if path.is_file()
    }
    target = build_filesystem_delivery_target(destination_root)
    adapter = FilesystemDirectoryDeliveryAdapter(
        publication_dir=publication_dir,
        destination_root=destination_root,
        target=target,
    )

    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=target.target_sha256,
        idempotency_key="0" * 64,
    )

    assert result.status == "failure"
    assert not (destination_root / ("0" * 64)).exists()
    assert not (destination_root / FILESYSTEM_DELIVERY_MARKER_FILENAME).exists()
    assert {
        path.relative_to(publication_dir): path.read_bytes()
        for path in publication_dir.rglob("*")
        if path.is_file()
    } == before


def test_first_delivery_copies_exact_payload_and_writes_marker_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, destination_root, package_sha256, artifact_bytes = _adapter_fixture(tmp_path)
    events: list[str] = []
    real_bytes = filesystem_v1.atomic_write_bytes
    real_json = filesystem_v1.atomic_write_json

    def record_bytes(path: Path, content: bytes) -> None:
        events.append(path.name)
        real_bytes(path, content)

    def record_json(path: Path, value: object) -> None:
        events.append(path.name)
        real_json(path, value)

    monkeypatch.setattr(filesystem_v1, "atomic_write_bytes", record_bytes)
    monkeypatch.setattr(filesystem_v1, "atomic_write_json", record_json)
    key = _deliver(adapter, package_sha256)
    remote = destination_root / key
    assert (remote / "publication-manifest-v2.json").is_file()
    assert (remote / "artifacts" / "card-0001" / "image.png").read_bytes() == artifact_bytes
    assert events[-1] == FILESYSTEM_DELIVERY_MARKER_FILENAME
    marker = json.loads((remote / FILESYSTEM_DELIVERY_MARKER_FILENAME).read_text(encoding="utf-8"))
    assert marker["idempotency_key"] == key
    assert marker["package_sha256"] == package_sha256
    assert len(marker["files"]) == 2


def test_same_identity_maps_to_one_directory_and_second_delivery_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in (destination_root / key).rglob("*")
        if path.is_file()
    }

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("completed idempotent delivery rewrote a payload")

    monkeypatch.setattr(filesystem_v1, "atomic_write_bytes", fail_write)
    monkeypatch.setattr(filesystem_v1, "atomic_write_json", fail_write)
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key=key,
    )
    assert result.status == "success"
    assert result.remote_reference == (destination_root / key).resolve().as_uri()
    assert {
        path.relative_to(destination_root): path.read_bytes()
        for path in (destination_root / key).rglob("*")
        if path.is_file()
    } == before


def test_matching_partial_delivery_fills_missing_files(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, artifact_bytes = _adapter_fixture(tmp_path)
    key = "b" * 64
    remote = destination_root / key
    remote.mkdir()
    (remote / "publication-manifest-v2.json").write_bytes(
        (adapter.publication_dir / "publication-manifest-v2.json").read_bytes()
    )
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key=key,
    )
    assert result.status == "success"
    assert (remote / "artifacts" / "card-0001" / "image.png").read_bytes() == artifact_bytes


def test_conflicting_partial_bytes_fail_closed_without_overwrite(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = "c" * 64
    remote = destination_root / key
    remote.mkdir()
    conflicting = remote / "publication-manifest-v2.json"
    conflicting.write_bytes(b"conflicting bytes")
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key=key,
    )
    assert result.status == "failure"
    assert conflicting.read_bytes() == b"conflicting bytes"
    assert not (remote / FILESYSTEM_DELIVERY_MARKER_FILENAME).exists()


def test_package_and_target_mismatch_fail_before_destination_write(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = "d" * 64
    wrong_package = "e" * 64
    package_result = adapter.deliver(
        package_sha256=wrong_package,
        target_sha256=adapter.target.target_sha256,
        idempotency_key=key,
    )
    target_mismatch_key = "f" * 64
    target_result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256="e" * 64,
        idempotency_key=target_mismatch_key,
    )
    assert package_result.status == "failure"
    assert target_result.status == "failure"
    assert not (destination_root / key).exists()
    assert not (destination_root / target_mismatch_key).exists()


def test_lookup_is_exact_read_only_reconciliation(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    result = adapter.lookup(idempotency_key=key)
    assert result.status == "success"
    assert result.remote_reference == (destination_root / key).resolve().as_uri()
    after = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_lookup_does_not_promote_incomplete_destination(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = "h" * 64
    (destination_root / key).mkdir()
    result = adapter.lookup(idempotency_key=key)
    assert result.status == "failure"
    assert not (destination_root / key / FILESYSTEM_DELIVERY_MARKER_FILENAME).exists()
    assert package_sha256


def test_marker_read_oserror_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    real_read_bytes = Path.read_bytes

    def fail_marker_read(path: Path) -> bytes:
        if path.name == FILESYSTEM_DELIVERY_MARKER_FILENAME:
            raise OSError("marker inspection unavailable")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_marker_read)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("lookup must not write")

    monkeypatch.setattr(filesystem_v1, "atomic_write_bytes", fail_write)
    monkeypatch.setattr(filesystem_v1, "atomic_write_json", fail_write)

    assert adapter.lookup(idempotency_key=key).status == "unknown"
    assert {
        path.relative_to(destination_root): real_read_bytes(path)
        for path in destination_root.rglob("*")
        if path.is_file()
    } == before


def test_remote_hash_mismatch_is_failure(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    artifact_path = destination_root / key / "artifacts" / "card-0001" / "image.png"
    artifact_path.write_bytes(b"tampered-payload")
    assert adapter.lookup(idempotency_key=key).status == "failure"
    assert (
        adapter.deliver(
            package_sha256=package_sha256,
            target_sha256=adapter.target.target_sha256,
            idempotency_key=key,
        ).status
        == "failure"
    )


@pytest.mark.parametrize("tamper", ["missing_receipt", "manifest_file_hash", "authorization"])
def test_frozen_publication_cross_bindings_are_fail_closed(tmp_path: Path, tamper: str) -> None:
    adapter, _destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    publication_dir = adapter.publication_dir
    if tamper == "missing_receipt":
        (publication_dir / "publication-receipt-v2.json").unlink()
    elif tamper == "manifest_file_hash":
        manifest_path = publication_dir / "publication-manifest-v2.json"
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    else:
        authorization_path = publication_dir / "publication-authorization-v2.json"
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["publication_manifest_sha256"] = "9" * 64
        authorization["authorization_sha256"] = "0" * 64
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key="i" * 64,
    )
    assert result.status == "failure"


@pytest.mark.parametrize("mutation", [b"short", b"xrozen-p5-image-byte"])
def test_source_artifact_size_and_sha_are_revalidated(tmp_path: Path, mutation: bytes) -> None:
    adapter, _destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    source = adapter.publication_dir / "artifacts" / "card-0001" / "image.png"
    source.write_bytes(mutation)
    result = adapter.deliver(
        package_sha256=package_sha256,
        target_sha256=adapter.target.target_sha256,
        idempotency_key="j" * 64,
    )
    assert result.status == "failure"


def test_marker_and_target_have_no_credentials_or_network_surface(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    target_payload = adapter.target.model_dump(mode="json")
    marker_payload = json.loads(
        (destination_root / key / FILESYSTEM_DELIVERY_MARKER_FILENAME).read_text(encoding="utf-8")
    )
    assert not any("secret" in field.lower() or "token" in field.lower() for field in target_payload)
    assert not any("secret" in field.lower() or "token" in field.lower() for field in marker_payload)
    assert "frozen-p5-image-bytes" not in json.dumps(marker_payload)
    source = Path(filesystem_v1.__file__).read_text(encoding="utf-8")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in ("httpx", "openai", "socket", "boto", "requests"))


def test_marker_model_is_self_hashing_and_complete(tmp_path: Path) -> None:
    adapter, destination_root, package_sha256, _ = _adapter_fixture(tmp_path)
    key = _deliver(adapter, package_sha256)
    marker_path = destination_root / key / FILESYSTEM_DELIVERY_MARKER_FILENAME
    marker = filesystem_v1.FilesystemDeliveryMarker.model_validate_json(marker_path.read_bytes())
    assert marker.delivery_identity == marker.idempotency_key == key
    assert marker.artifact_count == 1
    assert {item.relative_path for item in marker.files} == {
        "publication-manifest-v2.json",
        "artifacts/card-0001/image.png",
    }
