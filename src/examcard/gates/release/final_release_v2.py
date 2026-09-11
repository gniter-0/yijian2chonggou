"""Deterministic final-release authority derived from frozen P3 evidence.

P4 is deliberately a pure, downstream-only compiler.  It consumes the
already materialized P3 image-review contracts and produces an authority
closure for a later packaging/publication step.  It never chooses, edits, or
reinterprets a human image-review decision and has no filesystem, provider,
network, or RunState dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel
from ..review.image_admission_materialization_v2 import (
    BatchImageAdmissionAuditV2,
    BatchPostReviewQueueV2,
    BatchPostReviewItemV2,
    BatchReviewedImageCardV2,
    BatchReviewedImageFixtureV2,
    ImageReviewMaterializationReceiptV2,
)

__all__ = (
    "FINAL_RELEASE_CARD_V2_SCHEMA",
    "FINAL_RELEASE_BUNDLE_V2_SCHEMA",
    "FINAL_RELEASE_EXCLUSION_ITEM_V2_SCHEMA",
    "FINAL_RELEASE_EXCLUSION_LEDGER_V2_SCHEMA",
    "FINAL_RELEASE_AUDIT_ITEM_V2_SCHEMA",
    "FINAL_RELEASE_AUDIT_V2_SCHEMA",
    "FINAL_RELEASE_CLOSURE_RECEIPT_V2_SCHEMA",
    "FinalReleaseCardV2",
    "FinalReleaseBundleV2",
    "FinalReleaseExclusionItemV2",
    "FinalReleaseExclusionLedgerV2",
    "FinalReleaseAuditItemV2",
    "FinalReleaseAuditV2",
    "FinalReleaseClosureReceiptV2",
    "FinalReleaseArtifactsV2",
    "derive_final_release_v2",
)


FINAL_RELEASE_CARD_V2_SCHEMA: Final = "final-release-card/2.0"
FINAL_RELEASE_BUNDLE_V2_SCHEMA: Final = "final-release-bundle/2.0"
FINAL_RELEASE_EXCLUSION_ITEM_V2_SCHEMA: Final = "final-release-exclusion-item/2.0"
FINAL_RELEASE_EXCLUSION_LEDGER_V2_SCHEMA: Final = "final-release-exclusion-ledger/2.0"
FINAL_RELEASE_AUDIT_ITEM_V2_SCHEMA: Final = "final-release-audit-item/2.0"
FINAL_RELEASE_AUDIT_V2_SCHEMA: Final = "final-release-audit/2.0"
FINAL_RELEASE_CLOSURE_RECEIPT_V2_SCHEMA: Final = "final-release-closure/2.0"

_Disposition = Literal["approved", "rejected", "manual_review"]
_ExclusionDisposition = Literal["rejected", "manual_review"]
_RoutedOutput = Literal["release", "exclusion"]


def _self_hash(model: StrictModel, field: str) -> str:
    return canonical_hash(model.model_dump(mode="json", exclude={"schema_version", field}))


class FinalReleaseCardV2(StrictModel):
    schema_version: Literal["final-release-card/2.0"] = FINAL_RELEASE_CARD_V2_SCHEMA
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    render_presentation: str = Field(min_length=1)
    image_path: str = Field(min_length=1)
    image_sha256: str = Field(pattern=SHA256_HEX)
    image_provenance_sha256: str = Field(pattern=SHA256_HEX)
    provenance_file_sha256: str = Field(pattern=SHA256_HEX)
    http_payload_sha256: str = Field(pattern=SHA256_HEX)
    image_review_source_binding_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_sha256: str = Field(pattern=SHA256_HEX)
    decision_sha256: str = Field(pattern=SHA256_HEX)
    reviewed_card_sha256: str = Field(pattern=SHA256_HEX)
    reviewed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    materialization_receipt_sha256: str = Field(pattern=SHA256_HEX)
    release_card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card_hash(self) -> "FinalReleaseCardV2":
        if self.release_card_sha256 != _self_hash(self, "release_card_sha256"):
            raise ValueError("V2 final release card hash mismatch")
        return self


class FinalReleaseBundleV2(StrictModel):
    schema_version: Literal["final-release-bundle/2.0"] = FINAL_RELEASE_BUNDLE_V2_SCHEMA
    reviewed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    materialization_receipt_sha256: str = Field(pattern=SHA256_HEX)
    release_cards: tuple[FinalReleaseCardV2, ...] = Field(min_length=1)
    release_card_indexes: tuple[int, ...] = Field(min_length=1)
    decision_count: int = Field(ge=1)
    released_count: int = Field(ge=1)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    bundle_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_bundle(self) -> "FinalReleaseBundleV2":
        indexes = tuple(card.card_index for card in self.release_cards)
        if self.release_card_indexes != indexes or self.released_count != len(indexes):
            raise ValueError("V2 final release bundle card order/count mismatch")
        if len(set(indexes)) != len(indexes):
            raise ValueError("V2 final release bundle contains duplicate cards")
        if self.decision_count != self.released_count + self.rejected_count + self.manual_review_count:
            raise ValueError("V2 final release bundle conservation mismatch")
        if any(
            card.reviewed_fixture_sha256 != self.reviewed_fixture_sha256
            or card.materialization_receipt_sha256 != self.materialization_receipt_sha256
            for card in self.release_cards
        ):
            raise ValueError("V2 final release bundle upstream binding mismatch")
        if self.bundle_sha256 != _self_hash(self, "bundle_sha256"):
            raise ValueError("V2 final release bundle hash mismatch")
        return self


class FinalReleaseExclusionItemV2(StrictModel):
    schema_version: Literal["final-release-exclusion-item/2.0"] = FINAL_RELEASE_EXCLUSION_ITEM_V2_SCHEMA
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    disposition: _ExclusionDisposition
    decision_sha256: str = Field(pattern=SHA256_HEX)
    post_review_item_sha256: str = Field(pattern=SHA256_HEX)
    exclusion_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_item_hash(self) -> "FinalReleaseExclusionItemV2":
        if self.exclusion_sha256 != _self_hash(self, "exclusion_sha256"):
            raise ValueError("V2 final release exclusion item hash mismatch")
        return self


class FinalReleaseExclusionLedgerV2(StrictModel):
    schema_version: Literal["final-release-exclusion-ledger/2.0"] = FINAL_RELEASE_EXCLUSION_LEDGER_V2_SCHEMA
    materialization_receipt_sha256: str = Field(pattern=SHA256_HEX)
    items: tuple[FinalReleaseExclusionItemV2, ...] = ()
    exclusion_count: int = Field(ge=0)
    excluded_card_indexes: tuple[int, ...] = ()
    exclusion_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_ledger(self) -> "FinalReleaseExclusionLedgerV2":
        indexes = tuple(item.card_index for item in self.items)
        if self.exclusion_count != len(indexes) or self.excluded_card_indexes != indexes:
            raise ValueError("V2 final release exclusion ledger count/order mismatch")
        if len(set(indexes)) != len(indexes):
            raise ValueError("V2 final release exclusion ledger contains duplicate cards")
        if self.exclusion_sha256 != _self_hash(self, "exclusion_sha256"):
            raise ValueError("V2 final release exclusion ledger hash mismatch")
        return self


class FinalReleaseAuditItemV2(StrictModel):
    schema_version: Literal["final-release-audit-item/2.0"] = FINAL_RELEASE_AUDIT_ITEM_V2_SCHEMA
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    disposition: _Disposition
    decision_sha256: str = Field(pattern=SHA256_HEX)
    routed_output: _RoutedOutput
    source_routed_item_sha256: str = Field(pattern=SHA256_HEX)
    output_artifact_sha256: str = Field(pattern=SHA256_HEX)
    audit_item_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_item_hash(self) -> "FinalReleaseAuditItemV2":
        if self.audit_item_sha256 != _self_hash(self, "audit_item_sha256"):
            raise ValueError("V2 final release audit item hash mismatch")
        if self.routed_output == "release" and self.disposition != "approved":
            raise ValueError("V2 final release audit release route is not approved")
        if self.routed_output == "exclusion" and self.disposition == "approved":
            raise ValueError("V2 final release audit exclusion route is approved")
        return self


class FinalReleaseAuditV2(StrictModel):
    schema_version: Literal["final-release-audit/2.0"] = FINAL_RELEASE_AUDIT_V2_SCHEMA
    materialization_receipt_sha256: str = Field(pattern=SHA256_HEX)
    audit_items: tuple[FinalReleaseAuditItemV2, ...] = Field(min_length=1)
    decision_count: int = Field(ge=1)
    released_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    audit_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_audit(self) -> "FinalReleaseAuditV2":
        if self.decision_count != len(self.audit_items):
            raise ValueError("V2 final release audit decision count mismatch")
        indexes = tuple(item.card_index for item in self.audit_items)
        if len(set(indexes)) != len(indexes):
            raise ValueError("V2 final release audit contains duplicate cards")
        counts = {name: sum(1 for item in self.audit_items if item.disposition == name) for name in ("approved", "rejected", "manual_review")}
        if (self.released_count, self.rejected_count, self.manual_review_count) != (
            counts["approved"], counts["rejected"], counts["manual_review"]
        ):
            raise ValueError("V2 final release audit disposition counts mismatch")
        if self.audit_sha256 != _self_hash(self, "audit_sha256"):
            raise ValueError("V2 final release audit hash mismatch")
        return self


class FinalReleaseClosureReceiptV2(StrictModel):
    schema_version: Literal["final-release-closure/2.0"] = FINAL_RELEASE_CLOSURE_RECEIPT_V2_SCHEMA
    reviewed_fixture_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    reviewed_fixture_file_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    post_review_queue_sha256: str = Field(pattern=SHA256_HEX)
    post_review_queue_file_sha256: str = Field(pattern=SHA256_HEX)
    admission_audit_sha256: str = Field(pattern=SHA256_HEX)
    admission_audit_file_sha256: str = Field(pattern=SHA256_HEX)
    materialization_receipt_sha256: str = Field(pattern=SHA256_HEX)
    materialization_receipt_file_sha256: str = Field(pattern=SHA256_HEX)
    bundle_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    bundle_file_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    exclusion_sha256: str = Field(pattern=SHA256_HEX)
    exclusion_file_sha256: str = Field(pattern=SHA256_HEX)
    audit_sha256: str = Field(pattern=SHA256_HEX)
    audit_file_sha256: str = Field(pattern=SHA256_HEX)
    decision_count: int = Field(ge=1)
    released_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    released_card_indexes: tuple[int, ...] = ()
    excluded_card_indexes: tuple[int, ...] = ()
    release_bundle_present: bool
    closure_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt(self) -> "FinalReleaseClosureReceiptV2":
        if self.reviewed_fixture_sha256 is None and self.reviewed_fixture_file_sha256 is not None:
            raise ValueError("V2 final release receipt has a fixture file hash without a fixture")
        if (self.bundle_sha256 is None) != (self.bundle_file_sha256 is None):
            raise ValueError("V2 final release receipt bundle hash presence mismatch")
        if self.release_bundle_present != (self.bundle_sha256 is not None):
            raise ValueError("V2 final release receipt bundle presence mismatch")
        if self.decision_count != self.released_count + self.rejected_count + self.manual_review_count:
            raise ValueError("V2 final release receipt conservation mismatch")
        if len(self.released_card_indexes) != self.released_count:
            raise ValueError("V2 final release receipt released index count mismatch")
        if len(self.excluded_card_indexes) != self.rejected_count + self.manual_review_count:
            raise ValueError("V2 final release receipt excluded index count mismatch")
        if self.closure_sha256 != _self_hash(self, "closure_sha256"):
            raise ValueError("V2 final release closure receipt hash mismatch")
        return self


@dataclass(frozen=True)
class FinalReleaseArtifactsV2:
    """Pure P4 output; byte/file receipt binding belongs to persistence."""

    release_bundle: FinalReleaseBundleV2 | None
    exclusion_ledger: FinalReleaseExclusionLedgerV2
    audit: FinalReleaseAuditV2


def _require_p3_types(
    *,
    reviewed_fixture: BatchReviewedImageFixtureV2 | None,
    post_review_queue: BatchPostReviewQueueV2,
    admission_audit: BatchImageAdmissionAuditV2,
    materialization_receipt: ImageReviewMaterializationReceiptV2,
) -> None:
    if reviewed_fixture is not None and not isinstance(reviewed_fixture, BatchReviewedImageFixtureV2):
        raise TypeError("V2 final release requires a BatchReviewedImageFixtureV2 or None")
    if not isinstance(post_review_queue, BatchPostReviewQueueV2):
        raise TypeError("V2 final release requires BatchPostReviewQueueV2")
    if not isinstance(admission_audit, BatchImageAdmissionAuditV2):
        raise TypeError("V2 final release requires BatchImageAdmissionAuditV2")
    if not isinstance(materialization_receipt, ImageReviewMaterializationReceiptV2):
        raise TypeError("V2 final release requires ImageReviewMaterializationReceiptV2")


def _validate_p3_chain(
    *,
    reviewed_fixture: BatchReviewedImageFixtureV2 | None,
    post_review_queue: BatchPostReviewQueueV2,
    admission_audit: BatchImageAdmissionAuditV2,
    materialization_receipt: ImageReviewMaterializationReceiptV2,
) -> tuple[dict[int, BatchReviewedImageCardV2], dict[int, BatchPostReviewItemV2]]:
    _require_p3_types(
        reviewed_fixture=reviewed_fixture,
        post_review_queue=post_review_queue,
        admission_audit=admission_audit,
        materialization_receipt=materialization_receipt,
    )
    if materialization_receipt.reviewed_fixture_present != (reviewed_fixture is not None):
        raise ValueError("P3 fixture presence does not match its materialization receipt")
    if reviewed_fixture is not None:
        if materialization_receipt.reviewed_fixture_sha256 != reviewed_fixture.fixture_sha256:
            raise ValueError("P3 reviewed fixture hash does not match its materialization receipt")
    elif materialization_receipt.reviewed_fixture_sha256 is not None:
        raise ValueError("P3 zero-approval receipt must not bind a reviewed fixture")
    if materialization_receipt.post_review_queue_sha256 != post_review_queue.queue_sha256:
        raise ValueError("P3 post-review queue hash does not match its materialization receipt")
    if materialization_receipt.admission_audit_sha256 != admission_audit.audit_sha256:
        raise ValueError("P3 admission audit hash does not match its materialization receipt")
    if admission_audit.post_review_queue_sha256 != post_review_queue.queue_sha256:
        raise ValueError("P3 admission audit does not bind the post-review queue")
    if admission_audit.reviewed_fixture_sha256 != (reviewed_fixture.fixture_sha256 if reviewed_fixture is not None else None):
        raise ValueError("P3 admission audit does not bind the reviewed fixture")
    for field in (
        "image_review_source_binding_sha256",
        "authorization_manifest_sha256",
    ):
        values = [getattr(post_review_queue, field), getattr(admission_audit, field)]
        if reviewed_fixture is not None:
            values.append(getattr(reviewed_fixture, field))
        if any(value != values[0] for value in values[1:]):
            raise ValueError(f"P3 authority binding mismatch: {field}")

    audits = admission_audit.audits
    audit_indexes = tuple(item.card_index for item in audits)
    if len(set(audit_indexes)) != len(audit_indexes):
        raise ValueError("P3 decisions contain duplicate card indexes")
    approved_indexes = tuple(item.card_index for item in audits if item.disposition == "approved")
    excluded_indexes = tuple(item.card_index for item in audits if item.disposition != "approved")
    if materialization_receipt.reviewed_card_indexes != approved_indexes:
        raise ValueError("P3 reviewed indexes do not match its decisions")
    if materialization_receipt.post_review_card_indexes != excluded_indexes:
        raise ValueError("P3 post-review indexes do not match its decisions")
    if reviewed_fixture is not None and reviewed_fixture.reviewed_card_indexes != approved_indexes:
        raise ValueError("P3 reviewed fixture indexes do not match its decisions")
    if post_review_queue.card_indexes != excluded_indexes:
        raise ValueError("P3 post-review queue indexes do not match its decisions")

    fixture_by_index = {card.card_index: card for card in reviewed_fixture.cards} if reviewed_fixture is not None else {}
    queue_by_index = {item.card_index: item for item in post_review_queue.items}
    if len(fixture_by_index) != len(approved_indexes) or len(queue_by_index) != len(excluded_indexes):
        raise ValueError("P3 routed artifacts do not cover decisions exactly")
    if set(fixture_by_index).intersection(queue_by_index):
        raise ValueError("P3 routed artifacts contain a cross-card substitution")
    source_ids: set[str] = set()
    for audit in audits:
        if audit.source_card_id in source_ids:
            raise ValueError("P3 decisions contain duplicate source card identities")
        source_ids.add(audit.source_card_id)
        routed = fixture_by_index.get(audit.card_index) if audit.disposition == "approved" else queue_by_index.get(audit.card_index)
        if routed is None:
            raise ValueError(f"P3 decision is missing its routed card: {audit.card_index}")
        if audit.source_card_id != routed.source_card_id:
            raise ValueError(f"P3 routed card identity mismatch: {audit.card_index}")
        if audit.decision_sha256 != routed.decision_sha256:
            raise ValueError(f"P3 routed decision hash mismatch: {audit.card_index}")
        if audit.image_sha256 != routed.image_sha256 or audit.image_provenance_sha256 != routed.image_provenance_sha256:
            raise ValueError(f"P3 routed image identity mismatch: {audit.card_index}")
        expected_route = "reviewed_image" if audit.disposition == "approved" else "post_review"
        if audit.routed_output != expected_route or audit.routed_item_sha256 != getattr(routed, "card_sha256", getattr(routed, "item_sha256", None)):
            raise ValueError(f"P3 routed artifact hash mismatch: {audit.card_index}")
    return fixture_by_index, queue_by_index


def _resolve_input(primary: object | None, alias: object | None, label: str) -> object:
    if primary is not None and alias is not None and primary is not alias:
        raise TypeError(f"{label} was supplied more than once")
    value = primary if primary is not None else alias
    if value is None:
        raise TypeError(f"{label} is required")
    return value


def derive_final_release_v2(
    *,
    reviewed_fixture: BatchReviewedImageFixtureV2 | None = None,
    post_review_queue: BatchPostReviewQueueV2 | None = None,
    admission_audit: BatchImageAdmissionAuditV2 | None = None,
    materialization_receipt: ImageReviewMaterializationReceiptV2 | None = None,
    fixture: BatchReviewedImageFixtureV2 | None = None,
    queue: BatchPostReviewQueueV2 | None = None,
    audit: BatchImageAdmissionAuditV2 | None = None,
    receipt: ImageReviewMaterializationReceiptV2 | None = None,
) -> FinalReleaseArtifactsV2:
    """Derive deterministic P4 authority from one frozen P3 decision order."""

    resolved_fixture = reviewed_fixture if reviewed_fixture is not None else fixture
    resolved_queue = _resolve_input(post_review_queue, queue, "post_review_queue")
    resolved_audit = _resolve_input(admission_audit, audit, "admission_audit")
    resolved_receipt = _resolve_input(materialization_receipt, receipt, "materialization_receipt")
    if not isinstance(resolved_queue, BatchPostReviewQueueV2):
        raise TypeError("post_review_queue must be BatchPostReviewQueueV2")
    if not isinstance(resolved_audit, BatchImageAdmissionAuditV2):
        raise TypeError("admission_audit must be BatchImageAdmissionAuditV2")
    if not isinstance(resolved_receipt, ImageReviewMaterializationReceiptV2):
        raise TypeError("materialization_receipt must be ImageReviewMaterializationReceiptV2")
    fixture_by_index, queue_by_index = _validate_p3_chain(
        reviewed_fixture=resolved_fixture,
        post_review_queue=resolved_queue,
        admission_audit=resolved_audit,
        materialization_receipt=resolved_receipt,
    )
    fixture_sha = resolved_fixture.fixture_sha256 if resolved_fixture is not None else None
    receipt_sha = resolved_receipt.receipt_sha256

    release_cards = []
    for card in (resolved_fixture.cards if resolved_fixture is not None else ()):
        release_cards.append(
            hash_model(
                FinalReleaseCardV2,
                {
                    "card_index": card.card_index,
                    "source_card_id": card.source_card_id,
                    "sample_id": card.sample_id,
                    "render_presentation": card.render_presentation,
                    "image_path": card.image_path,
                    "image_sha256": card.image_sha256,
                    "image_provenance_sha256": card.image_provenance_sha256,
                    "provenance_file_sha256": card.provenance_file_sha256,
                    "http_payload_sha256": card.http_payload_sha256,
                    "image_review_source_binding_sha256": card.image_review_source_binding_sha256,
                    "authorization_manifest_sha256": card.authorization_manifest_sha256,
                    "decision_sha256": card.decision_sha256,
                    "reviewed_card_sha256": card.card_sha256,
                    "reviewed_fixture_sha256": fixture_sha,
                    "materialization_receipt_sha256": receipt_sha,
                },
                "release_card_sha256",
            )
        )
    release_cards_tuple = tuple(release_cards)
    exclusion_items = tuple(
        hash_model(
            FinalReleaseExclusionItemV2,
            {
                "card_index": item.card_index,
                "source_card_id": item.source_card_id,
                "disposition": item.disposition,
                "decision_sha256": item.decision_sha256,
                "post_review_item_sha256": item.item_sha256,
            },
            "exclusion_sha256",
        )
        for item in resolved_queue.items
    )
    exclusion_ledger = hash_model(
        FinalReleaseExclusionLedgerV2,
        {
            "materialization_receipt_sha256": receipt_sha,
            "items": exclusion_items,
            "exclusion_count": len(exclusion_items),
            "excluded_card_indexes": tuple(item.card_index for item in exclusion_items),
        },
        "exclusion_sha256",
    )
    release_by_index = {card.card_index: card for card in release_cards_tuple}
    exclusion_by_index = {item.card_index: item for item in exclusion_items}
    audit_items = []
    for source_audit in resolved_audit.audits:
        if source_audit.disposition == "approved":
            output: FinalReleaseCardV2 | FinalReleaseExclusionItemV2 | None = release_by_index.get(source_audit.card_index)
            routed_output: _RoutedOutput = "release"
        else:
            output = exclusion_by_index.get(source_audit.card_index)
            routed_output = "exclusion"
        if output is None:
            raise ValueError(f"P4 cannot route P3 decision {source_audit.card_index}")
        audit_items.append(
            hash_model(
                FinalReleaseAuditItemV2,
                {
                    "card_index": source_audit.card_index,
                    "source_card_id": source_audit.source_card_id,
                    "disposition": source_audit.disposition,
                    "decision_sha256": source_audit.decision_sha256,
                    "routed_output": routed_output,
                    "source_routed_item_sha256": source_audit.routed_item_sha256,
                    "output_artifact_sha256": getattr(output, "release_card_sha256", getattr(output, "exclusion_sha256", "")),
                },
                "audit_item_sha256",
            )
        )
    final_audit = hash_model(
        FinalReleaseAuditV2,
        {
            "materialization_receipt_sha256": receipt_sha,
            "audit_items": tuple(audit_items),
            "decision_count": resolved_audit.decision_count,
            "released_count": resolved_audit.approved_count,
            "rejected_count": resolved_audit.rejected_count,
            "manual_review_count": resolved_audit.manual_review_count,
        },
        "audit_sha256",
    )
    bundle = None
    if release_cards_tuple:
        bundle = hash_model(
            FinalReleaseBundleV2,
            {
                "reviewed_fixture_sha256": fixture_sha,
                "materialization_receipt_sha256": receipt_sha,
                "release_cards": release_cards_tuple,
                "release_card_indexes": tuple(card.card_index for card in release_cards_tuple),
                "decision_count": resolved_audit.decision_count,
                "released_count": resolved_audit.approved_count,
                "rejected_count": resolved_audit.rejected_count,
                "manual_review_count": resolved_audit.manual_review_count,
            },
            "bundle_sha256",
        )
    return FinalReleaseArtifactsV2(bundle, exclusion_ledger, final_audit)
