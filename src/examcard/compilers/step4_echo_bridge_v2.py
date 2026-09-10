"""Step 3 V3 / Echo Release V2 compiler bridge for the Step 4 surface."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..materials.schema import StrictModel
from ..knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
from ..knowledge.contracts.echo_release_v2 import (
    EchoBatchReleaseCardV2,
    EchoBatchReviewedReleaseFixtureV2,
    EchoBatchStep4HandoffV2,
    EchoReleaseSemanticEdgeV2,
    EchoReleaseTableCellV2,
    EchoReleaseTableViewV2,
    EchoReleaseVisibleTextV2,
    EchoStep3ReviewSheetCardV2,
    EchoStep3ReviewSheetV2,
    load_echo_handoff_v2,
    load_echo_release_fixture_v2,
    review_sheet_binding_hash,
)
from .step3_card_v3 import Step3CompilationV3, compile_reviewed_fixture_v3
from .step3_response_v3 import build_single_card_model_input_v3, make_single_card_inspector_v3
from .step4_contract_derive import render_presentation_for_source

ECHO_REVIEW_SHEET_NAME_V2 = "echo_step3_review_sheet_v2.json"


class EchoStep4BundleV2(StrictModel):
    schema_version: Literal["echo-step4-bundle/2.0"] = "echo-step4-bundle/2.0"
    batch_id: str = Field(min_length=1)
    source_release_sha256: str = Field(pattern=SHA256_HEX)
    source_handoff_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_file_sha256: str = Field(pattern=SHA256_HEX)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    cards: tuple[EchoBatchReleaseCardV2, ...] = Field(min_length=1)
    prior_reviewed_indexes: tuple[int, ...] = ()
    bundle_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_bundle(self) -> "EchoStep4BundleV2":
        if any(
            card.source_typed_fixture_file_sha256 != self.source_typed_fixture_file_sha256
            or card.source_typed_fixture_sha256 != self.source_typed_fixture_sha256
            for card in self.cards
        ):
            raise ValueError("V2 Step4 bundle fixture hash mismatch")
        if self.bundle_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "bundle_sha256"})):
            raise ValueError("V2 Step4 bundle hash mismatch")
        return self


class EchoStep4InputViewV2(StrictModel):
    fact_count: int = Field(ge=1)


def _fixture_and_compilation(path: Path) -> tuple[str, str, ReviewedTypedEvidenceFixtureV2, Step3CompilationV3]:
    data = Path(path).read_bytes()
    file_sha = hashlib.sha256(data).hexdigest()
    fixture = ReviewedTypedEvidenceFixtureV2.model_validate_json(data.decode("utf-8"))
    compilation = compile_reviewed_fixture_v3(fixture, source_typed_fixture_file_sha256=file_sha)
    return file_sha, fixture.fixture_sha256, fixture, compilation


def emit_echo_step3_review_sheet_v2(*, echo_run_dir: Path, typed_fixture_path: Path, out_dir: Path) -> Path:
    file_sha, authority_sha, _fixture, compilation = _fixture_and_compilation(typed_fixture_path)
    cards: list[EchoStep3ReviewSheetCardV2] = []
    for sequence, contract in enumerate(compilation.contracts, start=1):
        record_dir = Path(echo_run_dir) / "step3" / "cards" / f"{sequence:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
        raw_files = sorted(record_dir.glob("attempt-*.response.raw.json"))
        gate_files = sorted(record_dir.glob("attempt-*.gate.json"))
        if not raw_files:
            raise ValueError(f"V2 echo run lacks a response record for {contract.contract_id}")
        gate = json.loads(gate_files[-1].read_text(encoding="utf-8")) if gate_files else None
        cards.append(
            EchoStep3ReviewSheetCardV2(
                card_seq=sequence,
                contract_id=contract.contract_id,
                source_candidate_id=contract.source_candidate_id,
                presentation=contract.presentation,
                source_contract_sha256=contract.contract_sha256,
                echo_record_sha256=hashlib.sha256(raw_files[-1].read_bytes()).hexdigest(),
                echo_gate_valid=bool(gate and gate.get("valid") is True),
                source_typed_fixture_file_sha256=file_sha,
                source_typed_fixture_sha256=authority_sha,
                visible_text_summary=tuple(item.text for item in contract.display_elements[:3]),
            )
        )
    payload: dict[str, Any] = {
        "batch_id": f"echo-batch:{canonical_hash({'file': file_sha, 'run': str(echo_run_dir)})[:24]}",
        "source_echo_run_dir": Path(echo_run_dir).as_posix(),
        "source_typed_fixture_path": Path(typed_fixture_path).as_posix(),
        "source_typed_fixture_file_sha256": file_sha,
        "source_typed_fixture_sha256": authority_sha,
        "cards": tuple(cards),
    }
    draft = EchoStep3ReviewSheetV2.model_construct(**{**payload, "sheet_sha256": "0" * 64})
    sheet = EchoStep3ReviewSheetV2.model_validate({**payload, "sheet_sha256": review_sheet_binding_hash(draft)})
    out = Path(out_dir) / ECHO_REVIEW_SHEET_NAME_V2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sheet.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _project_visible(contract: Any) -> tuple[EchoReleaseVisibleTextV2, ...]:
    diagram_ids = {item.element_id for item in contract.layout.diagram_nodes}
    return tuple(
        EchoReleaseVisibleTextV2(
            element_id=item.element_id,
            visible_text=item.text,
            fact_id=item.provenance.fact_id,
            source_order=item.source_order,
            source_role=item.source_role,
            text_sha256=item.text_sha256,
            provenance=item.provenance,
            semantic_node_id=item.element_id if item.element_id in diagram_ids else None,
        )
        for item in contract.display_elements
    )


def _project_table(contract: Any) -> tuple[EchoReleaseTableViewV2, ...]:
    if contract.presentation != "table" or contract.layout.table_rows is None or contract.layout.table_columns is None:
        return ()
    text_by_id = {item.element_id: item.text for item in contract.display_elements}
    cells = tuple(
        EchoReleaseTableCellV2(
            row=ref.row - 1,
            column=ref.column - 1,
            row_span=ref.row_span,
            column_span=ref.column_span,
            text=text_by_id[ref.element_id],
        )
        for ref in contract.layout.table_cells
    )
    return (EchoReleaseTableViewV2(row_count=contract.layout.table_rows, column_count=contract.layout.table_columns, cells=cells),)


def _project_diagram(contract: Any) -> tuple[tuple[str, ...], tuple[EchoReleaseSemanticEdgeV2, ...]]:
    if contract.presentation != "diagram":
        return (), ()
    return (
        tuple(item.element_id for item in contract.layout.diagram_nodes),
        tuple(EchoReleaseSemanticEdgeV2(source_node_id=item.source_node_id, target_node_id=item.target_node_id) for item in contract.layout.diagram_edges),
    )


def build_echo_release_fixture_v2(
    *,
    typed_fixture_path: Path,
    echo_run_dir: Path,
    review_sheet_path: Path,
    approved_rows: Sequence[Mapping[str, Any]],
    source_echo_run_id: str,
) -> EchoBatchReviewedReleaseFixtureV2:
    file_sha, authority_sha, _fixture, compilation = _fixture_and_compilation(typed_fixture_path)
    sheet = EchoStep3ReviewSheetV2.model_validate_json(Path(review_sheet_path).read_text(encoding="utf-8"))
    if sheet.source_typed_fixture_file_sha256 != file_sha or sheet.source_typed_fixture_sha256 != authority_sha:
        raise ValueError("V2 release review sheet does not bind the current fixture")
    row_by_contract = {str(row["contract_id"]): row for row in approved_rows}
    if len(row_by_contract) != len(approved_rows):
        raise ValueError("V2 approved rows contain duplicate contracts")
    expected_approved = {card.contract_id for card in sheet.cards if card.disposition == "approved"}
    if set(row_by_contract) != expected_approved:
        raise ValueError("V2 approved rows do not exactly match approved review-sheet decisions")
    contracts = {item.contract_id: item for item in compilation.contracts}
    sheet_cards = {item.contract_id: item for item in sheet.cards}
    cards: list[EchoBatchReleaseCardV2] = []
    for index, (contract_id, row) in enumerate(sorted(row_by_contract.items(), key=lambda item: int(item[1]["card_seq"])), start=1):
        contract = contracts.get(contract_id)
        source_row = sheet_cards.get(contract_id)
        if contract is None or source_row is None:
            raise ValueError("V2 approved row references an unknown contract")
        source_payload = source_row.model_dump(mode="json")
        for field in (
            "card_seq",
            "contract_id",
            "source_candidate_id",
            "presentation",
            "source_contract_sha256",
            "echo_record_sha256",
            "echo_gate_valid",
            "source_typed_fixture_file_sha256",
            "source_typed_fixture_sha256",
            "visible_text_summary",
            "reviewer",
            "basis",
            "b2_review",
        ):
            if row.get(field) != source_payload[field]:
                raise ValueError(f"V2 approved row is not an exact review-sheet decision for {field}")
        if source_row.disposition != "approved" or source_row.echo_gate_valid is not True:
            raise ValueError("V2 release requires an approved row with a valid echo gate")
        if contract.source_candidate_id != source_row.source_candidate_id or contract.presentation != source_row.presentation:
            raise ValueError("V2 review sheet crossed the current source contract")
        if contract.contract_sha256 != source_row.source_contract_sha256:
            raise ValueError("V2 review sheet source contract hash is stale")
        if str(row.get("reviewer", "")).startswith("<") or not str(row.get("reviewer", "")).strip():
            raise ValueError("V2 release row lacks real reviewer")
        if str(row.get("review_signoff_sha256")) != canonical_hash(dict(row.get("b2_review") or {})):
            raise ValueError("V2 release review signoff hash mismatch")
        record_dir = Path(echo_run_dir) / "step3" / "cards" / f"{int(row['card_seq']):02d}_{contract_id.rsplit(':', 1)[-1]}"
        raw_files = sorted(record_dir.glob("attempt-*.response.raw.json"))
        if not raw_files:
            raise ValueError("V2 release lacks echo response record")
        raw_bytes = raw_files[-1].read_bytes()
        current_record_sha = hashlib.sha256(raw_bytes).hexdigest()
        if current_record_sha != source_row.echo_record_sha256:
            raise ValueError("V2 echo response record changed after human review")
        model_input = build_single_card_model_input_v3(compilation, index=next(i for i, item in enumerate(compilation.contracts) if item.contract_id == contract_id))
        inspection = make_single_card_inspector_v3(model_input)(raw_bytes)
        if not inspection.valid:
            raise ValueError("V2 release requires a valid V3 exact-echo response")
        gate_files = sorted(record_dir.glob("attempt-*.gate.json"))
        if not gate_files:
            raise ValueError("V2 release lacks an echo gate record")
        stored_gate = json.loads(gate_files[-1].read_text(encoding="utf-8"))
        if stored_gate.get("valid") is not True or stored_gate != inspection.model_dump(mode="json"):
            raise ValueError("V2 stored echo gate does not match the current V3 response")
        node_ids, edges = _project_diagram(contract)
        payload = {
            "card_index": index,
            "source_card_id": contract.contract_id,
            "source_candidate_id": contract.source_candidate_id,
            "sample_id": contract.display_elements[0].provenance.subject_id,
            "source_presentation": contract.presentation,
            "render_presentation": render_presentation_for_source(contract.presentation),
            "fact_ids": contract.fact_ids,
            "visible_texts": _project_visible(contract),
            "diagram_node_ids": node_ids,
            "semantic_layout_edges": edges,
            "table_contracts": _project_table(contract),
            "source_typed_fixture_file_sha256": file_sha,
            "source_typed_fixture_sha256": authority_sha,
            "source_contract_sha256": contract.contract_sha256,
            "echo_record_sha256": current_record_sha,
            "review_signoff_sha256": str(row["review_signoff_sha256"]),
            "reviewer": str(row["reviewer"]),
        }
        cards.append(hash_model(EchoBatchReleaseCardV2, payload, "card_sha256"))
    if not cards:
        raise ValueError("no approved V2 cards")
    sheet_file_sha = hashlib.sha256(Path(review_sheet_path).read_bytes()).hexdigest()
    return hash_model(
        EchoBatchReviewedReleaseFixtureV2,
        {
            "batch_id": sheet.batch_id,
            "source_typed_fixture_path": Path(typed_fixture_path).as_posix(),
            "source_typed_fixture_file_sha256": file_sha,
            "source_typed_fixture_sha256": authority_sha,
            "source_echo_run_id": source_echo_run_id,
            "review_sheet_sha256": sheet_file_sha,
            "card_count": len(cards),
            "cards": tuple(cards),
        },
        "fixture_sha256",
    )


def derive_step4_bundle_echo_v2(
    release: EchoBatchReviewedReleaseFixtureV2,
    handoff: EchoBatchStep4HandoffV2,
    *,
    release_sha256: str,
    handoff_sha256: str,
) -> tuple[EchoStep4BundleV2, EchoStep4InputViewV2]:
    if handoff.batch_id != release.batch_id or handoff.source_release_sha256 != release.fixture_sha256:
        raise ValueError("V2 handoff does not bind the release")
    if handoff.source_typed_fixture_file_sha256 != release.source_typed_fixture_file_sha256:
        raise ValueError("V2 handoff crossed the fixture file hash")
    if handoff.source_typed_fixture_sha256 != release.source_typed_fixture_sha256:
        raise ValueError("V2 handoff crossed the fixture authority hash")
    if handoff.card_count != release.card_count:
        raise ValueError("V2 handoff card count mismatch")
    release_ids = {item.card_index: item.source_card_id for item in release.cards}
    if len(release_ids) != len(release.cards) or len({item.source_card_id for item in release.cards}) != len(release.cards):
        raise ValueError("V2 release card identities must be unique")
    if len({item.card_index for item in handoff.cards}) != len(handoff.cards) or len({item.source_card_id for item in handoff.cards}) != len(handoff.cards):
        raise ValueError("V2 handoff card identities must be unique")
    if any(release_ids.get(item.card_index) != item.source_card_id for item in handoff.cards):
        raise ValueError("V2 handoff card identity mismatch")
    reviewed_indexes = {item.card_index for item in handoff.cards if item.disposition == "reviewed"}
    referenced_indexes = {item.card_index for item in handoff.reviewed_image_references}
    if tuple(sorted(handoff.prior_reviewed_indexes)) != tuple(sorted(reviewed_indexes)):
        raise ValueError("V2 handoff reviewed-index binding mismatch")
    if reviewed_indexes != referenced_indexes:
        raise ValueError("V2 handoff reviewed-image evidence mismatch")
    payload = {
        "batch_id": release.batch_id,
        "source_release_sha256": release_sha256,
        "source_handoff_sha256": handoff_sha256,
        "source_typed_fixture_file_sha256": release.source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": release.source_typed_fixture_sha256,
        "cards": release.cards,
        "prior_reviewed_indexes": tuple(sorted(item.card_index for item in handoff.cards if item.disposition == "reviewed")),
    }
    bundle = hash_model(EchoStep4BundleV2, payload, "bundle_sha256")
    fact_ids = {fact_id for card in release.cards for fact_id in card.fact_ids}
    return bundle, EchoStep4InputViewV2(fact_count=len(fact_ids))


def load_step4_bundle_source_v2(*, release_fixture_path: Path, handoff_path: Path) -> tuple[EchoStep4BundleV2, EchoStep4InputViewV2]:
    fixture_bytes = Path(release_fixture_path).read_bytes()
    handoff_bytes = Path(handoff_path).read_bytes()
    return derive_step4_bundle_echo_v2(
        load_echo_release_fixture_v2(Path(release_fixture_path)),
        load_echo_handoff_v2(Path(handoff_path)),
        release_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        handoff_sha256=hashlib.sha256(handoff_bytes).hexdigest(),
    )


__all__ = [
    "ECHO_REVIEW_SHEET_NAME_V2", "EchoStep4BundleV2", "EchoStep4InputViewV2",
    "build_echo_release_fixture_v2", "derive_step4_bundle_echo_v2", "emit_echo_step3_review_sheet_v2", "load_step4_bundle_source_v2",
]
