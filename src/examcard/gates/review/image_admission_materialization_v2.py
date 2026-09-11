"""Deterministic, offline materialization of frozen image-review authority.

This module is deliberately downstream of ``image_admission_batch``.  The
batch authorization manifest remains the human authority; this module only
routes its already validated decisions into the reviewed-image fixture,
post-review queue, and complete admission audit.  It has no filesystem,
provider, network, or RunState dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel
from .image_admission_batch import BatchImageAuthorizationManifest

__all__ = (
    "BATCH_REVIEWED_IMAGE_CARD_V2_SCHEMA",
    "BATCH_REVIEWED_IMAGE_FIXTURE_V2_SCHEMA",
    "BATCH_POST_REVIEW_ITEM_V2_SCHEMA",
    "BATCH_POST_REVIEW_QUEUE_V2_SCHEMA",
    "BATCH_IMAGE_ADMISSION_AUDIT_ITEM_V2_SCHEMA",
    "BATCH_IMAGE_ADMISSION_AUDIT_V2_SCHEMA",
    "IMAGE_REVIEW_MATERIALIZATION_RECEIPT_V2_SCHEMA",
    "BatchReviewedImageCardV2",
    "BatchReviewedImageFixtureV2",
    "BatchPostReviewItemV2",
    "BatchPostReviewQueueV2",
    "BatchImageAdmissionAuditItemV2",
    "BatchImageAdmissionAuditV2",
    "ImageReviewMaterializationReceiptV2",
    "ImageReviewMaterializationArtifactsV2",
    "derive_image_review_materialization_v2",
)

BATCH_REVIEWED_IMAGE_CARD_V2_SCHEMA: Final = "image-reviewed-card/2.0"
BATCH_REVIEWED_IMAGE_FIXTURE_V2_SCHEMA: Final = "image-reviewed-fixture/2.0"
BATCH_POST_REVIEW_ITEM_V2_SCHEMA: Final = "image-post-review-item/2.0"
BATCH_POST_REVIEW_QUEUE_V2_SCHEMA: Final = "image-post-review-queue/2.0"
BATCH_IMAGE_ADMISSION_AUDIT_ITEM_V2_SCHEMA: Final = "image-admission-audit-item/2.0"
BATCH_IMAGE_ADMISSION_AUDIT_V2_SCHEMA: Final = "image-admission-audit/2.0"
IMAGE_REVIEW_MATERIALIZATION_RECEIPT_V2_SCHEMA: Final = "image-review-materialization/2.0"

_Disposition = Literal["approved", "rejected", "manual_review"]
_PostReviewDisposition = Literal["rejected", "manual_review"]
_RoutedOutput = Literal["reviewed_image", "post_review"]


def _self_hash(model: StrictModel, field: str) -> str:
    return canonical_hash(model.model_dump(mode="json", exclude={"schema_version", field}))


class BatchReviewedImageCardV2(StrictModel):
    schema_version: Literal["image-reviewed-card/2.0"] = BATCH_REVIEWED_IMAGE_CARD_V2_SCHEMA
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
    reviewer: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    reviewed_state: Literal["reviewed_image"] = "reviewed_image"
    card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card_hash(self) -> "BatchReviewedImageCardV2":
        if self.card_sha256 != _self_hash(self, "card_sha256"):
            raise ValueError("V2 reviewed image card hash mismatch")
        return self


class BatchReviewedImageFixtureV2(StrictModel):
    schema_version: Literal["image-reviewed-fixture/2.0"] = BATCH_REVIEWED_IMAGE_FIXTURE_V2_SCHEMA
    image_review_source_binding_sha256: str = Field(pattern=SHA256_HEX)
    review_sheet_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    request_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    cards: tuple[BatchReviewedImageCardV2, ...] = Field(min_length=1)
    reviewed_card_count: int = Field(ge=1)
    reviewed_card_indexes: tuple[int, ...] = Field(min_length=1)
    fixture_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_fixture(self) -> "BatchReviewedImageFixtureV2":
        indexes = tuple(card.card_index for card in self.cards)
        if self.reviewed_card_count != len(self.cards) or self.reviewed_card_indexes != indexes:
            raise ValueError("V2 reviewed image fixture card count/order mismatch")
        if len(set(indexes)) != len(indexes) or len({card.source_card_id for card in self.cards}) != len(self.cards):
            raise ValueError("V2 reviewed image fixture card identities are not unique")
        if any(card.image_review_source_binding_sha256 != self.image_review_source_binding_sha256 for card in self.cards):
            raise ValueError("V2 reviewed image fixture source binding mismatch")
        if any(card.authorization_manifest_sha256 != self.authorization_manifest_sha256 for card in self.cards):
            raise ValueError("V2 reviewed image fixture authorization mismatch")
        if self.fixture_sha256 != _self_hash(self, "fixture_sha256"):
            raise ValueError("V2 reviewed image fixture hash mismatch")
        return self


class BatchPostReviewItemV2(StrictModel):
    schema_version: Literal["image-post-review-item/2.0"] = BATCH_POST_REVIEW_ITEM_V2_SCHEMA
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
    disposition: _PostReviewDisposition
    reviewer: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    decision_sha256: str = Field(pattern=SHA256_HEX)
    item_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_item_hash(self) -> "BatchPostReviewItemV2":
        if self.item_sha256 != _self_hash(self, "item_sha256"):
            raise ValueError("V2 post-review item hash mismatch")
        return self


class BatchPostReviewQueueV2(StrictModel):
    schema_version: Literal["image-post-review-queue/2.0"] = BATCH_POST_REVIEW_QUEUE_V2_SCHEMA
    image_review_source_binding_sha256: str = Field(pattern=SHA256_HEX)
    review_sheet_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    request_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    items: tuple[BatchPostReviewItemV2, ...] = ()
    queue_count: int = Field(ge=0)
    card_indexes: tuple[int, ...] = ()
    queue_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_queue(self) -> "BatchPostReviewQueueV2":
        indexes = tuple(item.card_index for item in self.items)
        if self.queue_count != len(self.items) or self.card_indexes != indexes:
            raise ValueError("V2 post-review queue card count/order mismatch")
        if len(set(indexes)) != len(indexes) or len({item.source_card_id for item in self.items}) != len(self.items):
            raise ValueError("V2 post-review queue card identities are not unique")
        if any(item.image_review_source_binding_sha256 != self.image_review_source_binding_sha256 for item in self.items):
            raise ValueError("V2 post-review queue source binding mismatch")
        if any(item.authorization_manifest_sha256 != self.authorization_manifest_sha256 for item in self.items):
            raise ValueError("V2 post-review queue authorization mismatch")
        if self.queue_sha256 != _self_hash(self, "queue_sha256"):
            raise ValueError("V2 post-review queue hash mismatch")
        return self


class BatchImageAdmissionAuditItemV2(StrictModel):
    schema_version: Literal["image-admission-audit-item/2.0"] = BATCH_IMAGE_ADMISSION_AUDIT_ITEM_V2_SCHEMA
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    disposition: _Disposition
    admitted: bool
    decision_sha256: str = Field(pattern=SHA256_HEX)
    image_sha256: str = Field(pattern=SHA256_HEX)
    image_provenance_sha256: str = Field(pattern=SHA256_HEX)
    routed_output: _RoutedOutput
    routed_item_sha256: str = Field(pattern=SHA256_HEX)
    reviewer: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    audit_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_audit_item(self) -> "BatchImageAdmissionAuditItemV2":
        if self.admitted != (self.disposition == "approved"):
            raise ValueError("V2 image admission audit admission invariant failed")
        expected_route = "reviewed_image" if self.admitted else "post_review"
        if self.routed_output != expected_route:
            raise ValueError("V2 image admission audit route invariant failed")
        if self.audit_sha256 != _self_hash(self, "audit_sha256"):
            raise ValueError("V2 image admission audit item hash mismatch")
        return self


class BatchImageAdmissionAuditV2(StrictModel):
    schema_version: Literal["image-admission-audit/2.0"] = BATCH_IMAGE_ADMISSION_AUDIT_V2_SCHEMA
    image_review_source_binding_sha256: str = Field(pattern=SHA256_HEX)
    review_sheet_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    request_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    audits: tuple[BatchImageAdmissionAuditItemV2, ...] = Field(min_length=1)
    decision_count: int = Field(ge=1)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    reviewed_fixture_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    post_review_queue_sha256: str = Field(pattern=SHA256_HEX)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    audit_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_audit(self) -> "BatchImageAdmissionAuditV2":
        if self.decision_count != len(self.audits):
            raise ValueError("V2 image admission audit decision count mismatch")
        counts = {name: sum(1 for item in self.audits if item.disposition == name) for name in ("approved", "rejected", "manual_review")}
        if (self.approved_count, self.rejected_count, self.manual_review_count) != (
            counts["approved"], counts["rejected"], counts["manual_review"]
        ):
            raise ValueError("V2 image admission audit disposition counts mismatch")
        if self.reviewed_fixture_sha256 is None and self.approved_count != 0:
            raise ValueError("V2 image admission audit requires a reviewed fixture for approvals")
        if self.reviewed_fixture_sha256 is not None and self.approved_count == 0:
            raise ValueError("V2 image admission audit must not bind a fixture without approvals")
        if self.audit_sha256 != _self_hash(self, "audit_sha256"):
            raise ValueError("V2 image admission audit hash mismatch")
        return self


class ImageReviewMaterializationReceiptV2(StrictModel):
    schema_version: Literal["image-review-materialization/2.0"] = IMAGE_REVIEW_MATERIALIZATION_RECEIPT_V2_SCHEMA
    image_review_source_binding_sha256: str = Field(pattern=SHA256_HEX)
    review_sheet_file_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_sha256: str = Field(pattern=SHA256_HEX)
    authorization_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    request_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    reviewed_fixture_present: bool
    reviewed_fixture_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    reviewed_fixture_file_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    post_review_queue_sha256: str = Field(pattern=SHA256_HEX)
    post_review_queue_file_sha256: str = Field(pattern=SHA256_HEX)
    admission_audit_sha256: str = Field(pattern=SHA256_HEX)
    admission_audit_file_sha256: str = Field(pattern=SHA256_HEX)
    decision_count: int = Field(ge=1)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    reviewed_card_indexes: tuple[int, ...] = ()
    post_review_card_indexes: tuple[int, ...] = ()
    receipt_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt(self) -> "ImageReviewMaterializationReceiptV2":
        if self.reviewed_fixture_present != (self.approved_count > 0):
            raise ValueError("V2 materialization receipt fixture presence mismatch")
        if self.reviewed_fixture_present:
            if self.reviewed_fixture_sha256 is None or self.reviewed_fixture_file_sha256 is None:
                raise ValueError("V2 materialization receipt fixture hashes are missing")
        elif self.reviewed_fixture_sha256 is not None or self.reviewed_fixture_file_sha256 is not None:
            raise ValueError("V2 materialization receipt must not bind a zero-approval fixture")
        if len(self.reviewed_card_indexes) != self.approved_count:
            raise ValueError("V2 materialization receipt reviewed index count mismatch")
        if len(self.post_review_card_indexes) != self.rejected_count + self.manual_review_count:
            raise ValueError("V2 materialization receipt post-review index count mismatch")
        if self.decision_count != self.approved_count + self.rejected_count + self.manual_review_count:
            raise ValueError("V2 materialization receipt conservation mismatch")
        if self.receipt_sha256 != _self_hash(self, "receipt_sha256"):
            raise ValueError("V2 materialization receipt hash mismatch")
        return self


@dataclass(frozen=True)
class ImageReviewMaterializationArtifactsV2:
    """Pure compiler output; receipt is intentionally persistence-owned."""

    reviewed_fixture: BatchReviewedImageFixtureV2 | None
    post_review_queue: BatchPostReviewQueueV2
    admission_audit: BatchImageAdmissionAuditV2


def _require_hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex string")
    return value


def _row_value(row: Mapping[str, Any], key: str, *, card_index: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"image review row {card_index} is missing {key}")
    return value


def _normalize_provenance_hashes(provenance_file_sha256_by_card: Mapping[Any, str]) -> dict[int, str]:
    normalized: dict[int, str] = {}
    for raw_index, value in provenance_file_sha256_by_card.items():
        try:
            card_index = int(raw_index)
        except (TypeError, ValueError) as error:
            raise ValueError("provenance file hash mapping contains an invalid card index") from error
        if card_index in normalized:
            raise ValueError("provenance file hash mapping contains duplicate card indexes")
        normalized[card_index] = _require_hash(value, label=f"provenance file hash for card {card_index}")
    return normalized


def derive_image_review_materialization_v2(
    *,
    rows: Sequence[Mapping[str, Any]],
    authorization_manifest: BatchImageAuthorizationManifest,
    image_review_source_binding_sha256: str,
    review_sheet_file_sha256: str,
    authorization_manifest_file_sha256: str,
    request_manifest_file_sha256: str,
    provenance_file_sha256_by_card: Mapping[Any, str],
) -> ImageReviewMaterializationArtifactsV2:
    """Route a validated P2 authority batch into deterministic V2 artifacts."""

    if not isinstance(authorization_manifest, BatchImageAuthorizationManifest):
        raise TypeError("V2 image materialization requires BatchImageAuthorizationManifest")
    source_binding = _require_hash(image_review_source_binding_sha256, label="image review source binding")
    sheet_file = _require_hash(review_sheet_file_sha256, label="review sheet file")
    manifest_file = _require_hash(authorization_manifest_file_sha256, label="authorization manifest file")
    request_file = _require_hash(request_manifest_file_sha256, label="request manifest file")
    decisions = authorization_manifest.decisions
    if len(rows) != len(decisions):
        raise ValueError("V2 image materialization rows do not cover every authorization decision")
    provenance = _normalize_provenance_hashes(provenance_file_sha256_by_card)
    decision_indexes = tuple(decision.card_index for decision in decisions)
    if tuple(int(row.get("card_index", -1)) for row in rows) != decision_indexes:
        raise ValueError("V2 image materialization rows are not in frozen decision order")
    if set(provenance) != set(decision_indexes):
        raise ValueError("V2 image materialization provenance hashes do not cover decisions exactly")

    reviewed: list[BatchReviewedImageCardV2] = []
    post_review: list[BatchPostReviewItemV2] = []
    audits: list[BatchImageAdmissionAuditItemV2] = []
    for row, decision in zip(rows, decisions, strict=True):
        if not isinstance(row, Mapping):
            raise ValueError("V2 image materialization row is not an object")
        card_index = decision.card_index
        for key in (
            "source_card_id",
            "render_presentation",
            "image_sha256",
            "disposition",
            "reviewer",
            "basis",
        ):
            if row.get(key) != getattr(decision, key):
                raise ValueError(f"V2 image materialization decision/row mismatch for card {card_index}: {key}")
        row_decision_hash = row.get("decision_sha256")
        if row_decision_hash is not None and row_decision_hash != decision.decision_sha256:
            raise ValueError(f"V2 image materialization decision hash mismatch for card {card_index}")
        optional_bindings = {
            "provenance_file_sha256": provenance[card_index],
            "image_review_source_binding_sha256": source_binding,
            "authorization_manifest_sha256": authorization_manifest.manifest_sha256,
        }
        for key, expected in optional_bindings.items():
            if key in row and row[key] != expected:
                raise ValueError(f"V2 image materialization binding mismatch for card {card_index}: {key}")
        common = {
            "card_index": card_index,
            "source_card_id": decision.source_card_id,
            "sample_id": _row_value(row, "sample_id", card_index=card_index),
            "render_presentation": decision.render_presentation,
            "image_path": _row_value(row, "image_path", card_index=card_index),
            "image_sha256": decision.image_sha256,
            "image_provenance_sha256": _row_value(row, "image_provenance_sha256", card_index=card_index),
            "provenance_file_sha256": provenance[card_index],
            "http_payload_sha256": _row_value(row, "http_payload_sha256", card_index=card_index),
            "image_review_source_binding_sha256": source_binding,
            "authorization_manifest_sha256": authorization_manifest.manifest_sha256,
            "decision_sha256": decision.decision_sha256,
            "reviewer": decision.reviewer,
            "basis": decision.basis,
        }
        if decision.disposition == "approved":
            reviewed.append(
                hash_model(
                    BatchReviewedImageCardV2,
                    {**common, "reviewed_state": "reviewed_image"},
                    "card_sha256",
                )
            )
        else:
            post_review.append(hash_model(BatchPostReviewItemV2, {**common, "disposition": decision.disposition}, "item_sha256"))

    reviewed_tuple = tuple(reviewed)
    post_review_tuple = tuple(post_review)
    fixture: BatchReviewedImageFixtureV2 | None = None
    if reviewed_tuple:
        fixture = hash_model(
            BatchReviewedImageFixtureV2,
            {
                "image_review_source_binding_sha256": source_binding,
                "review_sheet_file_sha256": sheet_file,
                "authorization_manifest_sha256": authorization_manifest.manifest_sha256,
                "authorization_manifest_file_sha256": manifest_file,
                "request_manifest_file_sha256": request_file,
                "cards": reviewed_tuple,
                "reviewed_card_count": len(reviewed_tuple),
                "reviewed_card_indexes": tuple(card.card_index for card in reviewed_tuple),
            },
            "fixture_sha256",
        )
    queue = hash_model(
        BatchPostReviewQueueV2,
        {
            "image_review_source_binding_sha256": source_binding,
            "review_sheet_file_sha256": sheet_file,
            "authorization_manifest_sha256": authorization_manifest.manifest_sha256,
            "authorization_manifest_file_sha256": manifest_file,
            "request_manifest_file_sha256": request_file,
            "items": post_review_tuple,
            "queue_count": len(post_review_tuple),
            "card_indexes": tuple(item.card_index for item in post_review_tuple),
        },
        "queue_sha256",
    )
    routed_hash_by_index = {item.card_index: item.card_sha256 for item in reviewed_tuple}
    routed_hash_by_index.update({item.card_index: item.item_sha256 for item in post_review_tuple})
    for row, decision in zip(rows, decisions, strict=True):
        routed_hash = routed_hash_by_index[decision.card_index]
        audits.append(
            hash_model(
                BatchImageAdmissionAuditItemV2,
                {
                    "card_index": decision.card_index,
                    "source_card_id": decision.source_card_id,
                    "disposition": decision.disposition,
                    "admitted": decision.disposition == "approved",
                    "decision_sha256": decision.decision_sha256,
                    "image_sha256": decision.image_sha256,
                    "image_provenance_sha256": _row_value(row, "image_provenance_sha256", card_index=decision.card_index),
                    "routed_output": "reviewed_image" if decision.disposition == "approved" else "post_review",
                    "routed_item_sha256": routed_hash,
                    "reviewer": decision.reviewer,
                    "basis": decision.basis,
                },
                "audit_sha256",
            )
        )
    audit = hash_model(
        BatchImageAdmissionAuditV2,
        {
            "image_review_source_binding_sha256": source_binding,
            "review_sheet_file_sha256": sheet_file,
            "authorization_manifest_sha256": authorization_manifest.manifest_sha256,
            "authorization_manifest_file_sha256": manifest_file,
            "request_manifest_file_sha256": request_file,
            "audits": tuple(audits),
            "decision_count": authorization_manifest.decision_count,
            "approved_count": authorization_manifest.approved_count,
            "rejected_count": authorization_manifest.rejected_count,
            "manual_review_count": authorization_manifest.manual_review_count,
            "reviewed_fixture_sha256": fixture.fixture_sha256 if fixture is not None else None,
            "post_review_queue_sha256": queue.queue_sha256,
        },
        "audit_sha256",
    )
    return ImageReviewMaterializationArtifactsV2(fixture, queue, audit)
