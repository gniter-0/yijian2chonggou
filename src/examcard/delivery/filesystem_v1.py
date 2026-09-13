"""Filesystem-directory-v1 implementation of the frozen P6 adapter seam.

The backend deliberately lives below the P5/P6 authority modules.  It reads a
materialized P5 publication, copies only its exact payload bytes, and writes a
completion marker last.  The marker is operational evidence for reconciliation;
it is not another release, publication, or delivery authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, sha256_bytes, sha256_text, hash_model
from ..common.io import atomic_write_bytes, atomic_write_json, resolve_within
from ..gates.release.delivery_v2 import (
    DeliveryAdapterResult,
    DeliveryTargetV2,
    build_delivery_target_v2,
)
from ..gates.release.publication_v2 import (
    PUBLICATION_PACKAGE_V2_SCHEMA,
    PUBLICATION_SCOPE_V2,
    PublicationArtifactV2,
    PublicationAuthorizationV2,
    PublicationManifestV2,
    PublicationReceiptV2,
)
from ..materials.schema import StrictModel

FILESYSTEM_DELIVERY_TARGET_TYPE: Final = "filesystem-directory-v1"
FILESYSTEM_DELIVERY_ALLOWED_SCOPE: Final = PUBLICATION_SCOPE_V2
FILESYSTEM_DELIVERY_MARKER_SCHEMA: Final = "filesystem-delivery-marker/1.0"
FILESYSTEM_DELIVERY_MARKER_FILENAME: Final = "delivery-complete-v1.json"
FILESYSTEM_DELIVERY_CONFIG_SCHEMA: Final = "filesystem-delivery-config/1.0"
FILESYSTEM_DELIVERY_LAYOUT: Final = "delivery-identity-directory-v1"
_MANIFEST_FILENAME: Final = "publication-manifest-v2.json"
_AUTHORIZATION_FILENAME: Final = "publication-authorization-v2.json"
_RECEIPT_FILENAME: Final = "publication-receipt-v2.json"


class _KnownFailure(Exception):
    """A failure whose outcome and destination state are known."""


class _UnknownOutcome(Exception):
    """A failure that prevents safely determining the destination state."""


class FilesystemDeliveredFile(StrictModel):
    """One exact payload file proven by the completion marker."""

    relative_path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_relative_path(self) -> "FilesystemDeliveredFile":
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path:
            raise ValueError("filesystem delivery path must be relative and POSIX-normalized")
        if path.as_posix() != self.relative_path or "." in path.parts:
            raise ValueError("filesystem delivery path is not normalized")
        return self


class FilesystemDeliveryMarker(StrictModel):
    """Internal, deterministic evidence that a filesystem delivery completed."""

    schema_version: Literal["filesystem-delivery-marker/1.0"] = FILESYSTEM_DELIVERY_MARKER_SCHEMA
    delivery_identity: str = Field(pattern=SHA256_HEX)
    idempotency_key: str = Field(pattern=SHA256_HEX)
    package_sha256: str = Field(pattern=SHA256_HEX)
    target_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    publication_receipt_sha256: str = Field(pattern=SHA256_HEX)
    publication_receipt_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_sha256: str = Field(pattern=SHA256_HEX)
    authorization_file_sha256: str = Field(pattern=SHA256_HEX)
    artifact_count: int = Field(ge=0)
    files: tuple[FilesystemDeliveredFile, ...]
    marker_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_marker_hash(self) -> "FilesystemDeliveryMarker":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "marker_sha256"}))
        if self.marker_sha256 != expected:
            raise ValueError("filesystem delivery marker hash mismatch")
        return self


@dataclass(frozen=True)
class _PayloadFile:
    relative_path: str
    content: bytes
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class _PublicationSource:
    manifest: PublicationManifestV2
    manifest_raw: bytes
    authorization: PublicationAuthorizationV2
    authorization_raw: bytes
    receipt: PublicationReceiptV2
    receipt_raw: bytes
    payload: tuple[_PayloadFile, ...]


def _filesystem_config(destination_reference: str) -> dict[str, str]:
    return {
        "schema_version": FILESYSTEM_DELIVERY_CONFIG_SCHEMA,
        "backend": FILESYSTEM_DELIVERY_TARGET_TYPE,
        "destination_reference": destination_reference,
        "layout": FILESYSTEM_DELIVERY_LAYOUT,
    }


def _canonical_destination_root(destination_root: Path) -> Path:
    root = Path(destination_root)
    if root.is_symlink():
        raise ValueError("filesystem delivery destination must not be a symlink")
    if not root.exists():
        raise ValueError("filesystem delivery destination is missing")
    if not root.is_dir():
        raise ValueError("filesystem delivery destination is not a directory")
    try:
        return root.resolve(strict=True)
    except OSError as error:
        raise ValueError("filesystem delivery destination cannot be resolved") from error


def build_filesystem_delivery_target(destination_root: Path) -> DeliveryTargetV2:
    """Build a target whose identity is only the canonical destination root."""

    canonical_root = _canonical_destination_root(Path(destination_root))
    destination_reference = canonical_root.as_uri()
    config = _filesystem_config(destination_reference)
    target_id = f"filesystem:{sha256_text(destination_reference)[:32]}"
    return build_delivery_target_v2(
        target_id=target_id,
        target_type=FILESYSTEM_DELIVERY_TARGET_TYPE,
        destination_reference=destination_reference,
        destination_config_sha256=canonical_hash(config),
        allowed_scope=FILESYSTEM_DELIVERY_ALLOWED_SCOPE,
    )


def _read_model(path: Path, model_type: type[StrictModel], label: str) -> tuple[StrictModel, bytes]:
    if path.is_symlink() or not path.is_file():
        raise _KnownFailure(f"publication {label} is missing or not a regular file")
    try:
        raw = path.read_bytes()
        return model_type.model_validate_json(raw), raw
    except (OSError, TypeError, ValueError) as error:
        raise _KnownFailure(f"publication {label} is invalid") from error


def _assert_no_symlink_components(root: Path, relative_path: str) -> None:
    current = root
    for component in PurePosixPath(relative_path).parts:
        current /= component
        if current.is_symlink():
            raise _KnownFailure("publication payload path contains a symlink")


def _source_file(publication_dir: Path, artifact: PublicationArtifactV2) -> _PayloadFile:
    try:
        _assert_no_symlink_components(publication_dir, artifact.relative_path)
        source = resolve_within(publication_dir, artifact.relative_path)
    except (OSError, ValueError) as error:
        raise _KnownFailure("publication artifact path escapes its source directory") from error
    if source.is_symlink() or not source.is_file():
        raise _KnownFailure("publication artifact is missing or not a regular file")
    try:
        content = source.read_bytes()
    except OSError as error:
        raise _KnownFailure("publication artifact cannot be read") from error
    if len(content) != artifact.byte_size:
        raise _KnownFailure("publication artifact byte size mismatch")
    if sha256_bytes(content) != artifact.sha256:
        raise _KnownFailure("publication artifact SHA256 mismatch")
    return _PayloadFile(artifact.relative_path, content, len(content), artifact.sha256)


def _load_publication_source(publication_dir: Path) -> _PublicationSource:
    if publication_dir.is_symlink() or not publication_dir.is_dir():
        raise _KnownFailure("frozen publication directory is missing or invalid")
    manifest_value, manifest_raw = _read_model(
        publication_dir / _MANIFEST_FILENAME, PublicationManifestV2, "manifest"
    )
    authorization_value, authorization_raw = _read_model(
        publication_dir / _AUTHORIZATION_FILENAME, PublicationAuthorizationV2, "authorization"
    )
    receipt_value, receipt_raw = _read_model(
        publication_dir / _RECEIPT_FILENAME, PublicationReceiptV2, "receipt"
    )
    manifest = manifest_value
    authorization = authorization_value
    receipt = receipt_value
    if not isinstance(manifest, PublicationManifestV2):
        raise _KnownFailure("publication manifest is not V2")
    if not isinstance(authorization, PublicationAuthorizationV2):
        raise _KnownFailure("publication authorization is not V2")
    if not isinstance(receipt, PublicationReceiptV2):
        raise _KnownFailure("publication receipt is not V2")
    if authorization.publication_scope != PUBLICATION_SCOPE_V2:
        raise _KnownFailure("publication authorization scope is invalid")
    if authorization.publication_manifest_sha256 != manifest.manifest_sha256:
        raise _KnownFailure("publication authorization does not bind the manifest")
    if receipt.publication_manifest_sha256 != manifest.manifest_sha256:
        raise _KnownFailure("publication receipt does not bind the manifest")
    if receipt.publication_manifest_file_sha256 != sha256_bytes(manifest_raw):
        raise _KnownFailure("publication manifest file hash mismatch")
    if receipt.authorization_sha256 != authorization.authorization_sha256:
        raise _KnownFailure("publication receipt does not bind authorization")
    if receipt.artifact_count != manifest.artifact_count:
        raise _KnownFailure("publication receipt artifact count mismatch")
    if receipt.artifact_count == 0 or receipt.package_sha256 == "0" * 64:
        raise _KnownFailure("filesystem delivery requires a non-empty publication package")
    expected_package = canonical_hash(
        {
            "schema_version": PUBLICATION_PACKAGE_V2_SCHEMA,
            "manifest_sha256": manifest.manifest_sha256,
            "artifacts": [item.model_dump(mode="json") for item in manifest.artifacts],
        }
    )
    if receipt.package_sha256 != expected_package:
        raise _KnownFailure("publication package identity mismatch")
    payload: list[_PayloadFile] = [
        _PayloadFile(_MANIFEST_FILENAME, manifest_raw, len(manifest_raw), sha256_bytes(manifest_raw))
    ]
    for artifact in manifest.artifacts:
        if artifact.relative_path in {_MANIFEST_FILENAME, FILESYSTEM_DELIVERY_MARKER_FILENAME}:
            raise _KnownFailure("publication artifact path conflicts with a delivery control file")
        payload.append(_source_file(publication_dir, artifact))
    return _PublicationSource(
        manifest=manifest,
        manifest_raw=manifest_raw,
        authorization=authorization,
        authorization_raw=authorization_raw,
        receipt=receipt,
        receipt_raw=receipt_raw,
        payload=tuple(payload),
    )


def _expected_marker(source: _PublicationSource, *, package_sha256: str, target_sha256: str, key: str) -> FilesystemDeliveryMarker:
    files = tuple(
        FilesystemDeliveredFile(
            relative_path=item.relative_path,
            byte_size=item.byte_size,
            sha256=item.sha256,
        )
        for item in source.payload
    )
    return hash_model(
        FilesystemDeliveryMarker,
        {
            "delivery_identity": key,
            "idempotency_key": key,
            "package_sha256": package_sha256,
            "target_sha256": target_sha256,
            "publication_manifest_sha256": source.manifest.manifest_sha256,
            "publication_manifest_file_sha256": sha256_bytes(source.manifest_raw),
            "publication_receipt_sha256": source.receipt.receipt_sha256,
            "publication_receipt_file_sha256": sha256_bytes(source.receipt_raw),
            "authorization_sha256": source.authorization.authorization_sha256,
            "authorization_file_sha256": sha256_bytes(source.authorization_raw),
            "artifact_count": source.manifest.artifact_count,
            "files": files,
        },
        "marker_sha256",
    )


def _validate_destination_target(adapter: "FilesystemDirectoryDeliveryAdapter") -> Path:
    canonical_root = _canonical_destination_root(adapter.destination_root)
    try:
        checkpoints_root = adapter.publication_dir.resolve(strict=True).parent
        canonical_root.relative_to(checkpoints_root)
    except ValueError:
        pass
    except OSError as error:
        raise _KnownFailure("filesystem delivery run checkpoint root cannot be resolved") from error
    else:
        raise _KnownFailure("filesystem delivery destination must be outside run checkpoints")
    destination_reference = canonical_root.as_uri()
    expected_config = _filesystem_config(destination_reference)
    expected_target = build_delivery_target_v2(
        target_id=f"filesystem:{sha256_text(destination_reference)[:32]}",
        target_type=FILESYSTEM_DELIVERY_TARGET_TYPE,
        destination_reference=destination_reference,
        destination_config_sha256=canonical_hash(expected_config),
        allowed_scope=FILESYSTEM_DELIVERY_ALLOWED_SCOPE,
    )
    if adapter.target.model_dump(mode="json") != expected_target.model_dump(mode="json"):
        raise _KnownFailure("filesystem delivery target does not match its destination")
    return canonical_root


def _validate_key(value: str) -> str:
    if not re.fullmatch(SHA256_HEX, value):
        raise _KnownFailure("delivery idempotency key is invalid")
    return value


def _destination_path(root: Path, final_dir: Path, relative_path: str) -> Path:
    _assert_no_symlink_components(final_dir, relative_path)
    try:
        return resolve_within(final_dir, relative_path)
    except (OSError, ValueError) as error:
        raise _KnownFailure("delivery payload path escapes its destination") from error


def _read_existing_marker(path: Path) -> FilesystemDeliveryMarker | None:
    if path.is_symlink():
        raise _KnownFailure("delivery completion marker is a symlink")
    if not path.exists():
        return None
    if not path.is_file():
        raise _KnownFailure("delivery completion marker is not a regular file")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise _UnknownOutcome("delivery completion marker cannot be inspected") from error
    try:
        return FilesystemDeliveryMarker.model_validate_json(raw)
    except (TypeError, ValueError) as error:
        raise _KnownFailure("delivery completion marker is invalid") from error


def _read_destination_payload(final_dir: Path, source: _PublicationSource) -> None:
    for item in source.payload:
        destination = _destination_path(final_dir, final_dir, item.relative_path)
        if destination.is_symlink() or not destination.is_file():
            raise _KnownFailure("delivered payload is missing or not a regular file")
        try:
            content = destination.read_bytes()
        except OSError as error:
            raise _UnknownOutcome("delivered payload cannot be inspected") from error
        if len(content) != item.byte_size or sha256_bytes(content) != item.sha256:
            raise _KnownFailure("delivered payload byte identity mismatch")


def _validate_complete_destination(
    final_dir: Path,
    source: _PublicationSource,
    marker: FilesystemDeliveryMarker,
    expected_marker: FilesystemDeliveryMarker,
) -> None:
    if marker.model_dump(mode="json") != expected_marker.model_dump(mode="json"):
        raise _KnownFailure("delivery completion marker identity mismatch")
    _read_destination_payload(final_dir, source)


def _check_existing_payload(final_dir: Path, source: _PublicationSource) -> None:
    for item in source.payload:
        destination = _destination_path(final_dir, final_dir, item.relative_path)
        if destination.is_symlink():
            raise _KnownFailure("existing delivery payload is a symlink")
        if not destination.exists():
            continue
        if not destination.is_file():
            raise _KnownFailure("existing delivery payload is not a regular file")
        try:
            current = destination.read_bytes()
        except OSError as error:
            raise _UnknownOutcome("existing delivery payload cannot be inspected") from error
        if current != item.content:
            raise _KnownFailure("existing delivery payload conflicts with the frozen package")


def _remote_reference(final_dir: Path) -> str:
    try:
        return final_dir.resolve(strict=True).as_uri()
    except OSError as error:
        raise _UnknownOutcome("delivery reference cannot be resolved") from error


class FilesystemDirectoryDeliveryAdapter:
    """Deliver one frozen P5 publication into a deterministic local directory."""

    def __init__(
        self,
        *,
        publication_dir: Path,
        destination_root: Path,
        target: DeliveryTargetV2 | None = None,
    ) -> None:
        self.publication_dir = Path(publication_dir)
        self.destination_root = Path(destination_root)
        self.target = target if target is not None else build_filesystem_delivery_target(self.destination_root)
        if self.target.target_type != FILESYSTEM_DELIVERY_TARGET_TYPE:
            raise ValueError("filesystem adapter requires a filesystem delivery target")

    def deliver(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> DeliveryAdapterResult:
        """Perform an exact, marker-last delivery using the P6 identity."""

        side_effect_attempted = False
        final_dir: Path | None = None
        source: _PublicationSource | None = None
        try:
            source = _load_publication_source(self.publication_dir)
            root = _validate_destination_target(self)
            key = _validate_key(idempotency_key)
            if target_sha256 != self.target.target_sha256:
                raise _KnownFailure("delivery target identity mismatch")
            if package_sha256 != source.receipt.package_sha256:
                raise _KnownFailure("delivery package identity mismatch")
            final_dir = root / key
            _assert_no_symlink_components(root, key)
            if final_dir.is_symlink():
                raise _KnownFailure("delivery identity directory is a symlink")
            marker_path = final_dir / FILESYSTEM_DELIVERY_MARKER_FILENAME
            existing_marker = _read_existing_marker(marker_path) if final_dir.exists() else None
            expected_marker = _expected_marker(
                source, package_sha256=package_sha256, target_sha256=target_sha256, key=key
            )
            if existing_marker is not None:
                _validate_complete_destination(final_dir, source, existing_marker, expected_marker)
                return DeliveryAdapterResult(status="success", remote_reference=_remote_reference(final_dir))
            if final_dir.exists() and not final_dir.is_dir():
                raise _KnownFailure("delivery identity path is not a directory")
            if not final_dir.exists():
                try:
                    final_dir.mkdir()
                except OSError as error:
                    if final_dir.exists():
                        raise _UnknownOutcome("delivery identity directory state is ambiguous") from error
                    raise _KnownFailure("delivery identity directory cannot be created") from error
            _check_existing_payload(final_dir, source)
            for item in source.payload:
                destination = _destination_path(final_dir, final_dir, item.relative_path)
                if destination.exists():
                    continue
                side_effect_attempted = True
                atomic_write_bytes(destination, item.content)
            _read_destination_payload(final_dir, source)
            side_effect_attempted = True
            atomic_write_json(marker_path, expected_marker.model_dump(mode="json"))
            stored_marker = _read_existing_marker(marker_path)
            if stored_marker is None:
                raise _UnknownOutcome("delivery completion marker did not persist")
            _validate_complete_destination(final_dir, source, stored_marker, expected_marker)
            return DeliveryAdapterResult(status="success", remote_reference=_remote_reference(final_dir))
        except _KnownFailure:
            return DeliveryAdapterResult(status="failure")
        except _UnknownOutcome:
            return DeliveryAdapterResult(status="unknown")
        except OSError:
            if side_effect_attempted and final_dir is not None and source is not None:
                try:
                    marker = _read_existing_marker(final_dir / FILESYSTEM_DELIVERY_MARKER_FILENAME)
                    if marker is not None:
                        expected = _expected_marker(
                            source,
                            package_sha256=package_sha256,
                            target_sha256=target_sha256,
                            key=idempotency_key,
                        )
                        _validate_complete_destination(final_dir, source, marker, expected)
                        return DeliveryAdapterResult(
                            status="success", remote_reference=_remote_reference(final_dir)
                        )
                    return DeliveryAdapterResult(status="failure")
                except _KnownFailure:
                    return DeliveryAdapterResult(status="failure")
                except (OSError, _UnknownOutcome):
                    return DeliveryAdapterResult(status="unknown")
            return DeliveryAdapterResult(status="failure")
        except (TypeError, ValueError):
            return DeliveryAdapterResult(status="failure")

    def lookup(self, *, idempotency_key: str) -> DeliveryAdapterResult:
        """Read-only reconciliation for the original P6 idempotency key."""

        try:
            source = _load_publication_source(self.publication_dir)
            root = _validate_destination_target(self)
            key = _validate_key(idempotency_key)
            final_dir = root / key
            _assert_no_symlink_components(root, key)
            if final_dir.is_symlink() or not final_dir.exists():
                return DeliveryAdapterResult(status="failure")
            if not final_dir.is_dir():
                return DeliveryAdapterResult(status="failure")
            marker = _read_existing_marker(final_dir / FILESYSTEM_DELIVERY_MARKER_FILENAME)
            if marker is None:
                return DeliveryAdapterResult(status="failure")
            expected = _expected_marker(
                source,
                package_sha256=source.receipt.package_sha256,
                target_sha256=self.target.target_sha256,
                key=key,
            )
            _validate_complete_destination(final_dir, source, marker, expected)
            return DeliveryAdapterResult(status="success", remote_reference=_remote_reference(final_dir))
        except _KnownFailure:
            return DeliveryAdapterResult(status="failure")
        except (_UnknownOutcome, OSError):
            return DeliveryAdapterResult(status="unknown")
        except (TypeError, ValueError):
            return DeliveryAdapterResult(status="failure")


__all__ = (
    "FILESYSTEM_DELIVERY_ALLOWED_SCOPE",
    "FILESYSTEM_DELIVERY_MARKER_FILENAME",
    "FILESYSTEM_DELIVERY_MARKER_SCHEMA",
    "FILESYSTEM_DELIVERY_TARGET_TYPE",
    "FilesystemDeliveredFile",
    "FilesystemDeliveryMarker",
    "FilesystemDirectoryDeliveryAdapter",
    "build_filesystem_delivery_target",
)
