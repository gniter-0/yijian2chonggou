"""Typed Echo Release V2 contracts.

This module contains only serializable contracts and loaders.  Human review
authority remains in ``gates.review.echo_step3_review_v2`` and compiler
projection remains in ``compilers.step4_echo_bridge_v2``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash
from ...materials.schema import StrictModel
from .step3_card_v3 import SourceProvenanceV3
from .admission_legacy_hash import text_hash

ECHO_REVIEW_SHEET_VERSION: Final = "echo-step3-review-sheet/2.0"
ECHO_RELEASE_CARD_VERSION: Final = "echo-batch-release-card/2.0"
ECHO_RELEASE_VERSION: Final = "echo-batch-release/2.0"
ECHO_HANDOFF_VERSION: Final = "echo-batch-step4-handoff/2.0"

_ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
_BATCH_ID = r"^echo-batch:[0-9a-f]{24}$"
SourcePresentationV2 = Literal["diagram", "table", "ordered_list", "constraint", "threshold", "formula"]
RenderPresentationV2 = Literal["diagram", "table", "ordered_list", "constraint", "formula"]


class EchoStep3ReviewSheetCardV2(StrictModel):
    schema_version: Literal["echo-step3-review-sheet-card/2.0"] = "echo-step3-review-sheet-card/2.0"
    card_seq: int = Field(ge=1)
    contract_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    source_candidate_id: str = Field(pattern=_ID)
    presentation: SourcePresentationV2
    source_contract_sha256: str = Field(pattern=SHA256_HEX)
    echo_record_sha256: str = Field(pattern=SHA256_HEX)
    echo_gate_valid: bool
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    visible_text_summary: tuple[str, ...] = Field(min_length=1)
    disposition: Literal["approved", "rejected", "manual_review"] | None = None
    reviewer: str | None = None
    basis: str | None = None
    b2_review: dict[str, object] | None = None


class EchoStep3ReviewSheetV2(StrictModel):
    schema_version: Literal["echo-step3-review-sheet/2.0"] = ECHO_REVIEW_SHEET_VERSION
    batch_id: str = Field(pattern=_BATCH_ID)
    source_echo_run_dir: str = Field(min_length=1)
    source_typed_fixture_path: str = Field(min_length=1)
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    cards: tuple[EchoStep3ReviewSheetCardV2, ...] = Field(min_length=1)
    sheet_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_sheet(self) -> "EchoStep3ReviewSheetV2":
        if tuple(item.card_seq for item in self.cards) != tuple(range(1, len(self.cards) + 1)):
            raise ValueError("V2 review sheet card sequence must be dense")
        if any(
            item.source_typed_fixture_file_sha256 != self.source_typed_fixture_file_sha256
            or item.source_typed_fixture_sha256 != self.source_typed_fixture_sha256
            for item in self.cards
        ):
            raise ValueError("V2 review sheet fixture hashes are not uniformly bound")
        expected = review_sheet_binding_hash(self)
        if self.sheet_sha256 != expected:
            raise ValueError("V2 review sheet hash mismatch")
        return self


def review_sheet_binding_hash(sheet: EchoStep3ReviewSheetV2) -> str:
    """Hash the immutable review-sheet binding, excluding human-fill cells."""

    payload = sheet.model_dump(mode="json", exclude={"schema_version", "sheet_sha256"})
    for card in payload["cards"]:
        for key in ("disposition", "reviewer", "basis", "b2_review"):
            card.pop(key, None)
    return canonical_hash(payload)


class EchoStep3ReviewReceiptCardV2(StrictModel):
    """One approved, immutable decision returned by V2 review ingest."""

    schema_version: Literal["echo-step3-review-receipt-card/2.0"] = "echo-step3-review-receipt-card/2.0"
    card_seq: int = Field(ge=1)
    contract_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    source_candidate_id: str = Field(pattern=_ID)
    presentation: SourcePresentationV2
    source_contract_sha256: str = Field(pattern=SHA256_HEX)
    echo_record_sha256: str = Field(pattern=SHA256_HEX)
    echo_gate_valid: Literal[True] = True
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    visible_text_summary: tuple[str, ...] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    b2_review: dict[str, object] = Field(min_length=1)
    review_signoff_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt_card(self) -> "EchoStep3ReviewReceiptCardV2":
        if self.reviewer.startswith("<"):
            raise ValueError("V2 receipt reviewer must be real")
        if self.review_signoff_sha256 != canonical_hash(dict(self.b2_review)):
            raise ValueError("V2 receipt signoff hash mismatch")
        return self


class EchoStep3ReviewReceiptV2(StrictModel):
    """Typed, self-hashed proof that a V2 sheet was ingested by the gate."""

    schema_version: Literal["echo-step3-review-receipt/2.0"] = "echo-step3-review-receipt/2.0"
    sheet_sha256: str = Field(pattern=SHA256_HEX)
    batch_id: str = Field(pattern=_BATCH_ID)
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    released: tuple[EchoStep3ReviewReceiptCardV2, ...] = Field(min_length=1)
    receipt_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt(self) -> "EchoStep3ReviewReceiptV2":
        if tuple(item.card_seq for item in self.released) != tuple(sorted(item.card_seq for item in self.released)):
            raise ValueError("V2 receipt cards must be source ordered")
        if len({item.contract_id for item in self.released}) != len(self.released):
            raise ValueError("V2 receipt contract IDs must be unique")
        if any(
            item.source_typed_fixture_file_sha256 != self.source_typed_fixture_file_sha256
            or item.source_typed_fixture_sha256 != self.source_typed_fixture_sha256
            for item in self.released
        ):
            raise ValueError("V2 receipt fixture hashes are not uniformly bound")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "receipt_sha256"}))
        if self.receipt_sha256 != expected:
            raise ValueError("V2 review receipt hash mismatch")
        return self

    def __getitem__(self, key: str) -> object:
        """Keep the small mapping-shaped CLI/test surface source-compatible."""

        return getattr(self, key)


class EchoReleaseVisibleTextV2(StrictModel):
    element_id: str = Field(pattern=_ID)
    visible_text: str = Field(min_length=1)
    fact_id: str = Field(pattern=_ID)
    source_order: int = Field(ge=1)
    source_role: Literal["diagram_node", "table_cell", "ordered_list_item", "constraint_token", "threshold_token", "formula_token"]
    text_sha256: str = Field(pattern=SHA256_HEX)
    provenance: SourceProvenanceV3
    semantic_node_id: str | None = None

    @model_validator(mode="after")
    def validate_source_text(self) -> "EchoReleaseVisibleTextV2":
        if self.text_sha256 != text_hash(self.visible_text):
            raise ValueError("V2 release visible text hash mismatch")
        if self.provenance.fact_id != self.fact_id:
            raise ValueError("V2 release visible text Fact/provenance mismatch")
        return self


class EchoReleaseSemanticEdgeV2(StrictModel):
    source_node_id: str = Field(pattern=_ID)
    target_node_id: str = Field(pattern=_ID)
    layout_relation: str = Field(default="connects", min_length=1)


class EchoReleaseTableCellV2(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str = Field(min_length=1)


class EchoReleaseTableViewV2(StrictModel):
    table_state: Literal["reviewed_grid"] = "reviewed_grid"
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cells: tuple[EchoReleaseTableCellV2, ...] = Field(min_length=1)


class EchoBatchReleaseCardV2(StrictModel):
    schema_version: Literal["echo-batch-release-card/2.0"] = ECHO_RELEASE_CARD_VERSION
    card_index: int = Field(ge=1)
    source_card_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    source_candidate_id: str = Field(pattern=_ID)
    sample_id: str = Field(pattern=_ID)
    source_presentation: SourcePresentationV2
    render_presentation: RenderPresentationV2
    fact_ids: tuple[str, ...] = Field(min_length=1)
    visible_texts: tuple[EchoReleaseVisibleTextV2, ...] = Field(min_length=1)
    diagram_node_ids: tuple[str, ...] = ()
    semantic_layout_edges: tuple[EchoReleaseSemanticEdgeV2, ...] = ()
    table_contracts: tuple[EchoReleaseTableViewV2, ...] = ()
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    source_contract_sha256: str = Field(pattern=SHA256_HEX)
    echo_record_sha256: str = Field(pattern=SHA256_HEX)
    review_signoff_sha256: str = Field(pattern=SHA256_HEX)
    reviewer: str = Field(min_length=1)
    card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card(self) -> "EchoBatchReleaseCardV2":
        expected_render = "constraint" if self.source_presentation == "threshold" else self.source_presentation
        if self.render_presentation != expected_render:
            raise ValueError("V2 source/render presentation projection mismatch")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("V2 release Fact IDs must be unique")
        if any(item.fact_id not in self.fact_ids for item in self.visible_texts):
            raise ValueError("V2 visible text crossed Fact binding")
        element_ids = tuple(item.element_id for item in self.visible_texts)
        if len(element_ids) != len(set(element_ids)) or tuple(item.source_order for item in self.visible_texts) != tuple(range(1, len(element_ids) + 1)):
            raise ValueError("V2 visible text identity/order is not source exact")
        expected_role = {
            "diagram": "diagram_node",
            "table": "table_cell",
            "ordered_list": "ordered_list_item",
            "constraint": "constraint_token",
            "threshold": "threshold_token",
            "formula": "formula_token",
        }[self.source_presentation]
        if any(item.source_role != expected_role for item in self.visible_texts):
            raise ValueError("V2 release visible text source role mismatch")
        if self.source_presentation == "threshold" and any(item.source_role != "threshold_token" for item in self.visible_texts):
            raise ValueError("V2 threshold visible text source role mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "card_sha256"}))
        if self.card_sha256 != expected:
            raise ValueError("V2 release card hash mismatch")
        return self


class EchoBatchReviewedReleaseFixtureV2(StrictModel):
    schema_version: Literal["echo-batch-release/2.0"] = ECHO_RELEASE_VERSION
    batch_id: str = Field(pattern=_BATCH_ID)
    source_typed_fixture_path: str = Field(min_length=1)
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    source_echo_run_id: str = Field(min_length=1)
    review_sheet_sha256: str = Field(pattern=SHA256_HEX)
    card_count: int = Field(ge=1)
    cards: tuple[EchoBatchReleaseCardV2, ...] = Field(min_length=1)
    fixture_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_fixture(self) -> "EchoBatchReviewedReleaseFixtureV2":
        if self.card_count != len(self.cards) or tuple(item.card_index for item in self.cards) != tuple(range(1, len(self.cards) + 1)):
            raise ValueError("V2 release card sequence mismatch")
        if any(
            item.source_typed_fixture_file_sha256 != self.source_typed_fixture_file_sha256
            or item.source_typed_fixture_sha256 != self.source_typed_fixture_sha256
            for item in self.cards
        ):
            raise ValueError("V2 release fixture hashes do not match cards")
        if self.fixture_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"})):
            raise ValueError("V2 release fixture hash mismatch")
        return self


class EchoBatchCardDispositionV2(StrictModel):
    card_index: int = Field(ge=1)
    source_card_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    disposition: Literal["pending", "reviewed"]


class EchoReviewedImageReferenceV2(StrictModel):
    card_index: int = Field(ge=1)
    image_sha256: str = Field(pattern=SHA256_HEX)
    reference_path: str = Field(min_length=1)


class EchoBatchStep4HandoffV2(StrictModel):
    schema_version: Literal["echo-batch-step4-handoff/2.0"] = ECHO_HANDOFF_VERSION
    batch_id: str = Field(pattern=_BATCH_ID)
    source_release_sha256: str = Field(pattern=SHA256_HEX)
    source_handoff_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    review_ingest_receipt_sha256: str = Field(pattern=SHA256_HEX)
    card_count: int = Field(ge=1)
    cards: tuple[EchoBatchCardDispositionV2, ...] = Field(min_length=1)
    reviewed_image_references: tuple[EchoReviewedImageReferenceV2, ...] = ()
    prior_reviewed_indexes: tuple[int, ...] = ()
    batch_generation_authorized: Literal[True] = True
    authorized_by: str = Field(min_length=1)
    authorization_evidence_sha256: str = Field(pattern=SHA256_HEX)
    handoff_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_handoff(self) -> "EchoBatchStep4HandoffV2":
        if self.card_count != len(self.cards) or tuple(item.card_index for item in self.cards) != tuple(range(1, len(self.cards) + 1)):
            raise ValueError("V2 handoff card sequence mismatch")
        card_indexes = {item.card_index for item in self.cards}
        reviewed_indexes = {item.card_index for item in self.cards if item.disposition == "reviewed"}
        referenced_indexes = {item.card_index for item in self.reviewed_image_references}
        if len(referenced_indexes) != len(self.reviewed_image_references):
            raise ValueError("V2 reviewed image references contain duplicate card indexes")
        if not referenced_indexes <= card_indexes:
            raise ValueError("V2 reviewed image reference points to an unknown card")
        if reviewed_indexes != referenced_indexes:
            raise ValueError("V2 reviewed dispositions and image references must cover the same cards")
        if tuple(self.prior_reviewed_indexes) != tuple(sorted(reviewed_indexes)):
            raise ValueError("V2 prior reviewed indexes do not match reviewed image evidence")
        if self.handoff_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "handoff_sha256"})):
            raise ValueError("V2 handoff hash mismatch")
        return self


def load_echo_release_fixture_v2(path: Path) -> EchoBatchReviewedReleaseFixtureV2:
    return EchoBatchReviewedReleaseFixtureV2.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_echo_handoff_v2(path: Path) -> EchoBatchStep4HandoffV2:
    return EchoBatchStep4HandoffV2.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


__all__ = [
    "ECHO_HANDOFF_VERSION", "ECHO_RELEASE_CARD_VERSION", "ECHO_RELEASE_VERSION", "ECHO_REVIEW_SHEET_VERSION",
    "EchoBatchCardDispositionV2", "EchoBatchReleaseCardV2", "EchoBatchReviewedReleaseFixtureV2", "EchoBatchStep4HandoffV2",
    "EchoReleaseSemanticEdgeV2", "EchoReleaseTableCellV2", "EchoReleaseTableViewV2", "EchoReleaseVisibleTextV2",
    "EchoReviewedImageReferenceV2", "EchoStep3ReviewReceiptCardV2", "EchoStep3ReviewReceiptV2",
    "EchoStep3ReviewSheetCardV2", "EchoStep3ReviewSheetV2",
    "load_echo_handoff_v2", "load_echo_release_fixture_v2", "review_sheet_binding_hash",
]
