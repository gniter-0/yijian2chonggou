"""Deterministic publication authority derived from frozen P4 release data.

This module deliberately has no filesystem, provider, network, or RunState
dependency.  It only validates an already frozen P4 closure and describes the
exact byte artifacts that a later persistence layer may materialize.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel
from .final_release_v2 import (
    FinalReleaseAuditV2,
    FinalReleaseBundleV2,
    FinalReleaseClosureReceiptV2,
)

PUBLICATION_ARTIFACT_V2_SCHEMA: Final = "publication-artifact/2.0"
PUBLICATION_MANIFEST_V2_SCHEMA: Final = "publication-manifest/2.0"
PUBLICATION_AUTHORIZATION_V2_SCHEMA: Final = "publication-authorization/2.0"
PUBLICATION_AUDIT_V2_SCHEMA: Final = "publication-audit/2.0"
PUBLICATION_RECEIPT_V2_SCHEMA: Final = "publication-receipt/2.0"
PUBLICATION_PACKAGE_V2_SCHEMA: Final = "publication-package/2.0"
PUBLICATION_SCOPE_V2: Final = "publication-package-ready"


def _self_hash(model: StrictModel, field: str) -> str:
    return canonical_hash(model.model_dump(mode="json", exclude={"schema_version", field}))


class PublicationArtifactV2(StrictModel):
    schema_version: Literal["publication-artifact/2.0"] = PUBLICATION_ARTIFACT_V2_SCHEMA
    artifact_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_HEX)
    source_release_card_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    artifact_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_artifact(self) -> "PublicationArtifactV2":
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path:
            raise ValueError("publication artifact path must be a relative POSIX path")
        if path.as_posix() != self.relative_path or "." in path.parts:
            raise ValueError("publication artifact path is not normalized")
        if self.artifact_sha256 != _self_hash(self, "artifact_sha256"):
            raise ValueError("publication artifact hash mismatch")
        return self


class PublicationManifestV2(StrictModel):
    schema_version: Literal["publication-manifest/2.0"] = PUBLICATION_MANIFEST_V2_SCHEMA
    final_release_closure_sha256: str = Field(pattern=SHA256_HEX)
    final_release_receipt_file_sha256: str = Field(pattern=SHA256_HEX)
    final_release_bundle_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    final_release_bundle_file_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    final_release_audit_sha256: str = Field(pattern=SHA256_HEX)
    final_release_audit_file_sha256: str = Field(pattern=SHA256_HEX)
    final_release_exclusion_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    final_release_exclusion_file_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    release_card_sha256s: tuple[str, ...] = ()
    release_count: int = Field(ge=0)
    artifacts: tuple[PublicationArtifactV2, ...] = ()
    artifact_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_manifest(self) -> "PublicationManifestV2":
        if (self.final_release_bundle_sha256 is None) != (self.final_release_bundle_file_sha256 is None):
            raise ValueError("publication manifest bundle binding presence mismatch")
        if self.artifact_count != len(self.artifacts):
            raise ValueError("publication manifest artifact count mismatch")
        if self.release_count != len(self.release_card_sha256s):
            raise ValueError("publication manifest release count mismatch")
        paths = tuple(item.relative_path for item in self.artifacts)
        if len(set(paths)) != len(paths):
            raise ValueError("publication manifest contains duplicate artifact paths")
        if paths != tuple(sorted(paths)):
            raise ValueError("publication manifest artifact order is not deterministic")
        ids = tuple(item.artifact_id for item in self.artifacts)
        if len(set(ids)) != len(ids):
            raise ValueError("publication manifest contains duplicate artifact ids")
        if self.manifest_sha256 != _self_hash(self, "manifest_sha256"):
            raise ValueError("publication manifest hash mismatch")
        return self


class PublicationAuthorizationV2(StrictModel):
    schema_version: Literal["publication-authorization/2.0"] = PUBLICATION_AUTHORIZATION_V2_SCHEMA
    authorization_id: str = Field(min_length=1)
    publication_scope: str = Field(min_length=1)
    final_release_closure_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_sha256: str = Field(pattern=SHA256_HEX)
    authorized_by: str = Field(min_length=1)
    authorization_reason: str = Field(min_length=1)
    authorized_at: str = Field(min_length=1)
    authorization_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_authorization(self) -> "PublicationAuthorizationV2":
        if self.authorization_sha256 != _self_hash(self, "authorization_sha256"):
            raise ValueError("publication authorization hash mismatch")
        return self


class PublicationAuditV2(StrictModel):
    schema_version: Literal["publication-audit/2.0"] = PUBLICATION_AUDIT_V2_SCHEMA
    final_release_closure_sha256: str = Field(pattern=SHA256_HEX)
    package_present: bool
    artifact_count: int = Field(ge=0)
    status: Literal["ready", "no_release"]
    audit_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_audit(self) -> "PublicationAuditV2":
        if self.status == "ready" and not self.package_present:
            raise ValueError("ready publication audit must have a package")
        if self.status == "no_release" and self.package_present:
            raise ValueError("no-release publication audit must not have a package")
        if self.audit_sha256 != _self_hash(self, "audit_sha256"):
            raise ValueError("publication audit hash mismatch")
        return self


class PublicationReceiptV2(StrictModel):
    schema_version: Literal["publication-receipt/2.0"] = PUBLICATION_RECEIPT_V2_SCHEMA
    publication_manifest_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_sha256: str = Field(pattern=SHA256_HEX)
    final_release_closure_sha256: str = Field(pattern=SHA256_HEX)
    artifact_count: int = Field(ge=0)
    package_sha256: str = Field(pattern=SHA256_HEX)
    receipt_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt(self) -> "PublicationReceiptV2":
        if self.receipt_sha256 != _self_hash(self, "receipt_sha256"):
            raise ValueError("publication receipt hash mismatch")
        return self


@dataclass(frozen=True)
class PublicationArtifactsV2:
    manifest: PublicationManifestV2
    authorization: PublicationAuthorizationV2
    audit: PublicationAuditV2
    receipt: PublicationReceiptV2


def build_publication_artifact_v2(
    *,
    artifact_id: str,
    relative_path: str,
    media_type: str,
    content: bytes,
    source_release_card_sha256: str | None = None,
) -> PublicationArtifactV2:
    """Build a descriptor from already-existing bytes; never reads a path."""

    from ...common.hashing import sha256_bytes

    return hash_model(
        PublicationArtifactV2,
        {
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "media_type": media_type,
            "byte_size": len(content),
            "sha256": sha256_bytes(content),
            "source_release_card_sha256": source_release_card_sha256,
        },
        "artifact_sha256",
    )


def build_publication_authorization_v2(
    *,
    authorization_id: str,
    publication_scope: str,
    final_release_closure_sha256: str,
    publication_manifest_sha256: str,
    authorized_by: str,
    authorization_reason: str,
    authorized_at: str,
) -> PublicationAuthorizationV2:
    """Create an authorization only from explicit caller-supplied human fields."""

    return hash_model(
        PublicationAuthorizationV2,
        {
            "authorization_id": authorization_id,
            "publication_scope": publication_scope,
            "final_release_closure_sha256": final_release_closure_sha256,
            "publication_manifest_sha256": publication_manifest_sha256,
            "authorized_by": authorized_by,
            "authorization_reason": authorization_reason,
            "authorized_at": authorized_at,
        },
        "authorization_sha256",
    )


def _p4_value(identity: Mapping[str, object], canonical: str, *aliases: str) -> object:
    for key in (canonical, *aliases):
        if key in identity:
            return identity[key]
    raise ValueError(f"P4 identity is missing: {canonical}")


def _validate_p4(
    *,
    receipt: FinalReleaseClosureReceiptV2,
    bundle: FinalReleaseBundleV2 | None,
    audit: FinalReleaseAuditV2,
    p4_receipt_identity: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(receipt, FinalReleaseClosureReceiptV2):
        raise TypeError("publication requires FinalReleaseClosureReceiptV2")
    if bundle is not None and not isinstance(bundle, FinalReleaseBundleV2):
        raise TypeError("publication requires FinalReleaseBundleV2 or None")
    if not isinstance(audit, FinalReleaseAuditV2):
        raise TypeError("publication requires FinalReleaseAuditV2")
    closure_sha = _p4_value(
        p4_receipt_identity,
        "final_release_closure_sha256",
        "final_release_receipt_sha256",
    )
    values = {
        "final_release_closure_sha256": closure_sha,
        "final_release_receipt_file_sha256": _p4_value(
            p4_receipt_identity, "final_release_receipt_file_sha256"
        ),
        "final_release_bundle_sha256": _p4_value(
            p4_receipt_identity, "final_release_bundle_sha256"
        ),
        "final_release_bundle_file_sha256": _p4_value(
            p4_receipt_identity, "final_release_bundle_file_sha256"
        ),
        "final_release_audit_sha256": _p4_value(
            p4_receipt_identity, "final_release_audit_sha256"
        ),
        "final_release_audit_file_sha256": _p4_value(
            p4_receipt_identity, "final_release_audit_file_sha256"
        ),
        "final_release_exclusion_sha256": p4_receipt_identity.get(
            "final_release_exclusion_sha256", receipt.exclusion_sha256
        ),
        "final_release_exclusion_file_sha256": p4_receipt_identity.get(
            "final_release_exclusion_file_sha256", receipt.exclusion_file_sha256
        ),
    }
    if closure_sha != receipt.closure_sha256:
        raise ValueError("P4 closure hash does not match its receipt")
    if values["final_release_bundle_sha256"] != receipt.bundle_sha256:
        raise ValueError("P4 bundle hash does not match its receipt")
    if values["final_release_audit_sha256"] != audit.audit_sha256 or receipt.audit_sha256 != audit.audit_sha256:
        raise ValueError("P4 audit hash does not match its receipt")
    if values["final_release_exclusion_sha256"] != receipt.exclusion_sha256:
        raise ValueError("P4 exclusion hash does not match its receipt")
    if bundle is None:
        if receipt.bundle_sha256 is not None or values["final_release_bundle_file_sha256"] is not None:
            raise ValueError("P4 bundle is missing while its identity is present")
    else:
        if receipt.bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("P4 bundle object does not match its receipt")
        if bundle.released_count != receipt.released_count:
            raise ValueError("P4 release count does not match its receipt")
    if audit.released_count != receipt.released_count or audit.decision_count != receipt.decision_count:
        raise ValueError("P4 audit counts do not match its receipt")
    return values


def build_publication_manifest_v2(
    *,
    final_release_receipt: FinalReleaseClosureReceiptV2,
    final_release_bundle: FinalReleaseBundleV2 | None,
    final_release_audit: FinalReleaseAuditV2,
    p4_receipt_identity: Mapping[str, object],
    artifacts: Sequence[PublicationArtifactV2],
) -> PublicationManifestV2:
    values = _validate_p4(
        receipt=final_release_receipt,
        bundle=final_release_bundle,
        audit=final_release_audit,
        p4_receipt_identity=p4_receipt_identity,
    )
    ordered = tuple(sorted(artifacts, key=lambda item: item.relative_path))
    if final_release_bundle is None:
        if ordered:
            raise ValueError("zero-release P4 cannot produce publication artifacts")
        card_hashes: tuple[str, ...] = ()
        release_count = 0
    else:
        card_hashes = tuple(card.release_card_sha256 for card in final_release_bundle.release_cards)
        release_count = final_release_bundle.released_count
        allowed = set(card_hashes)
        for artifact in ordered:
            if artifact.source_release_card_sha256 is not None and artifact.source_release_card_sha256 not in allowed:
                raise ValueError("publication artifact references an unknown release card")
    return hash_model(
        PublicationManifestV2,
        {
            **values,
            "release_card_sha256s": card_hashes,
            "release_count": release_count,
            "artifacts": ordered,
            "artifact_count": len(ordered),
        },
        "manifest_sha256",
    )


def derive_publication_v2(
    *,
    final_release_receipt: FinalReleaseClosureReceiptV2,
    final_release_bundle: FinalReleaseBundleV2 | None,
    final_release_audit: FinalReleaseAuditV2,
    p4_receipt_identity: Mapping[str, object],
    artifacts: Sequence[PublicationArtifactV2],
    authorization: PublicationAuthorizationV2,
    publication_manifest: PublicationManifestV2 | None = None,
    publication_manifest_file_sha256: str | None = None,
) -> PublicationArtifactsV2:
    """Validate P4, authorization, and deterministic publication identity."""

    if not isinstance(authorization, PublicationAuthorizationV2):
        raise TypeError("publication authorization must be PublicationAuthorizationV2")
    manifest = build_publication_manifest_v2(
        final_release_receipt=final_release_receipt,
        final_release_bundle=final_release_bundle,
        final_release_audit=final_release_audit,
        p4_receipt_identity=p4_receipt_identity,
        artifacts=artifacts,
    )
    if publication_manifest is not None and publication_manifest.model_dump(mode="json") != manifest.model_dump(mode="json"):
        raise ValueError("publication manifest conflicts with P4 and its artifacts")
    if authorization.publication_scope != PUBLICATION_SCOPE_V2:
        raise ValueError("publication authorization scope is invalid")
    if authorization.publication_manifest_sha256 != manifest.manifest_sha256:
        raise ValueError("publication authorization does not bind the manifest")
    if authorization.final_release_closure_sha256 != manifest.final_release_closure_sha256:
        raise ValueError("publication authorization does not bind P4 closure")
    if publication_manifest_file_sha256 is None:
        publication_manifest_file_sha256 = "0" * 64
    package_present = final_release_bundle is not None
    package_sha256 = (
        canonical_hash(
            {
                "schema_version": PUBLICATION_PACKAGE_V2_SCHEMA,
                "manifest_sha256": manifest.manifest_sha256,
                "artifacts": [item.model_dump(mode="json") for item in manifest.artifacts],
            }
        )
        if package_present
        else "0" * 64
    )
    status: Literal["ready", "no_release"] = "ready" if package_present else "no_release"
    audit = hash_model(
        PublicationAuditV2,
        {
            "final_release_closure_sha256": manifest.final_release_closure_sha256,
            "package_present": package_present,
            "artifact_count": manifest.artifact_count,
            "status": status,
        },
        "audit_sha256",
    )
    receipt = hash_model(
        PublicationReceiptV2,
        {
            "publication_manifest_sha256": manifest.manifest_sha256,
            "publication_manifest_file_sha256": publication_manifest_file_sha256,
            "authorization_sha256": authorization.authorization_sha256,
            "final_release_closure_sha256": manifest.final_release_closure_sha256,
            "artifact_count": manifest.artifact_count,
            "package_sha256": package_sha256,
        },
        "receipt_sha256",
    )
    return PublicationArtifactsV2(
        manifest=manifest,
        authorization=authorization,
        audit=audit,
        receipt=receipt,
    )


__all__ = (
    "PUBLICATION_ARTIFACT_V2_SCHEMA",
    "PUBLICATION_MANIFEST_V2_SCHEMA",
    "PUBLICATION_AUTHORIZATION_V2_SCHEMA",
    "PUBLICATION_AUDIT_V2_SCHEMA",
    "PUBLICATION_RECEIPT_V2_SCHEMA",
    "PUBLICATION_PACKAGE_V2_SCHEMA",
    "PUBLICATION_SCOPE_V2",
    "PublicationArtifactV2",
    "PublicationManifestV2",
    "PublicationAuthorizationV2",
    "PublicationAuditV2",
    "PublicationReceiptV2",
    "PublicationArtifactsV2",
    "build_publication_artifact_v2",
    "build_publication_authorization_v2",
    "build_publication_manifest_v2",
    "derive_publication_v2",
)
