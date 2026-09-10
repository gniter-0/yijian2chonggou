"""fixture → 契约的确定性派生链（拆分层 3）。

_source_*/_extract_tokens/_derive_*/derive_contracts/derive_step4_bundle：
从 reviewed fixture 与 handoff 纯计算派生通用契约、模型输入、请求、fake 工件、
批次清单。derive_step4_bundle 是外部稳定入口（4 个 importer）。
"""
from __future__ import annotations

import re
from typing import Any
from examcard.knowledge.contracts.admission_fixtures import (
    Step3MergedReviewedReleaseCard,
    Step3MergedSourceOwnedDisplayText,
    Step3UnifiedReviewedReleaseFixture,
    Step4PilotHandoffRefresh,
)

from .step4_contract_constants import (
    DEFAULT_FAKE_FILENAME_PREFIX,
    MARKERS,
    MODEL_INPUT_VERSION,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    REQUEST_VERSION,
    SOURCE_PRESENTATIONS,
    TOKEN_PATTERN,
)
from .step4_contract_hashing import _canonical_hash, _json_bytes, _sha256
from .step4_contract_models_generic import (
    GenericCardContract,
    GenericGeometry,
    GenericOrderedListContract,
    GenericOrderedListItem,
    GenericSemanticEdge,
    GenericTableCell,
    GenericTableContract,
    GenericTokenBinding,
    GenericVisibleText,
)
from .step4_contract_models_phase import (
    Phase2R4BatchEntry,
    Phase2R4BatchManifest,
    Phase2R4ContractBundle,
    Phase2R4FakeArtifact,
    Phase2R4FakeArtifactManifest,
    Phase2R4ModelInput,
    Phase2R4PromptContract,
    Phase2R4Request,
)


def render_presentation_for_source(source_presentation: str) -> str:
    """Map source semantics to the renderer surface at the Step4 boundary."""

    if source_presentation not in {"diagram", "table", "ordered_list", "constraint", "threshold", "formula"}:
        raise ValueError(f"unsupported source presentation: {source_presentation}")
    return "constraint" if source_presentation == "threshold" else source_presentation


def _source_text_payload(source: Step3MergedSourceOwnedDisplayText) -> dict[str, Any]:
    return {
        "fact_id": source.fact_id,
        "text": source.text,
        "source_kind": source.source_kind,
        "evidence_ids": source.evidence_ids,
        "anchor_span_ids": source.anchor_span_ids,
        "source_table_ids": source.source_table_ids,
        "continuation_binding_ids": source.continuation_binding_ids,
        "complete_table_grid_verified": source.complete_table_grid_verified,
        "semantic_sidecar_sha256": source.semantic_sidecar_sha256,
        "semantic_node_id": source.semantic_node_id,
        "source_manifest_sha256s": source.source_manifest_sha256s,
        "crop_sha256s": source.crop_sha256s,
    }


def _source_card_payload(source: Step3MergedReviewedReleaseCard) -> dict[str, Any]:
    return {
        "card_index": source.card_index,
        "source_card_id": source.source_card_id,
        "sample_id": source.sample_id,
        "presentation": source.presentation,
        "fact_ids": source.fact_ids,
        "display_texts": [_source_text_payload(item) for item in source.display_text_provenance],
        "semantic_edges": [
            {
                "fact_id": edge.fact_id,
                "source_node_id": edge.source_node_id,
                "relation": edge.relation,
                "target_node_id": edge.target_node_id,
                "evidence_ids": edge.evidence_ids,
                "figure_evidence_id": edge.figure_evidence_id,
                "source_manifest_sha256": edge.source_manifest_sha256,
                "semantic_sidecar_sha256": edge.semantic_sidecar_sha256,
            }
            for edge in source.semantic_edge_provenance
        ],
    }


def _extract_tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(TOKEN_PATTERN.findall(text)))


def _derive_visible_text(source: Step3MergedSourceOwnedDisplayText) -> GenericVisibleText:
    return GenericVisibleText(
        fact_id=source.fact_id,
        visible_text=source.text,
        source_kind=source.source_kind,
        evidence_ids=source.evidence_ids,
        anchor_span_ids=source.anchor_span_ids,
        source_table_ids=source.source_table_ids,
        continuation_binding_ids=source.continuation_binding_ids,
        complete_table_grid_verified=source.complete_table_grid_verified,
        semantic_sidecar_sha256=source.semantic_sidecar_sha256,
        semantic_node_id=source.semantic_node_id,
        source_manifest_sha256s=source.source_manifest_sha256s,
        crop_sha256s=source.crop_sha256s,
        source_text_sha256=_sha256(source.text.encode("utf-8")),
        source_display_contract_sha256=_canonical_hash(_source_text_payload(source)),
    )


def _derive_table(source: Step3MergedSourceOwnedDisplayText) -> GenericTableContract:
    grid = source.table_grid
    if grid is None:
        return GenericTableContract(
            fact_id=source.fact_id,
            source_table_ids=source.source_table_ids,
            source_text_sha256=_sha256(source.text.encode("utf-8")),
            table_state="source_text_only",
        )
    cells = tuple(
        GenericTableCell(
            row=cell.row,
            column=cell.column,
            row_span=cell.row_span,
            column_span=cell.column_span,
            text=cell.text,
            evidence_id=cell.evidence_id,
            source_cell_contract_sha256=_canonical_hash(cell),
        )
        for cell in grid.cells
    )
    return GenericTableContract(
        fact_id=source.fact_id,
        source_table_ids=source.source_table_ids,
        source_text_sha256=_sha256(source.text.encode("utf-8")),
        table_state="reviewed_grid",
        table_evidence_id=grid.table_evidence_id,
        row_count=grid.row_count,
        column_count=grid.column_count,
        cells=cells,
        source_grid_contract_sha256=_canonical_hash(grid),
    )


def _derive_list(source: Step3MergedSourceOwnedDisplayText) -> GenericOrderedListContract | None:
    marker_re = re.compile("[" + "".join(MARKERS) + "]")
    matches = list(marker_re.finditer(source.text))
    if not matches:
        return None
    preamble = source.text[: matches[0].start()].strip()
    items: list[GenericOrderedListItem] = []
    for ordinal, match in enumerate(matches, start=1):
        end = matches[ordinal].start() if ordinal < len(matches) else len(source.text)
        text = source.text[match.end() : end].strip()
        payload = {"ordinal": ordinal, "marker": match.group(0), "text": text}
        items.append(GenericOrderedListItem(**payload, source_item_sha256=_canonical_hash(payload)))
    source_contract = {
        "fact_id": source.fact_id,
        "source_text_sha256": _sha256(source.text.encode("utf-8")),
        "preamble": preamble,
        "items": [item.model_dump(mode="json") for item in items],
    }
    return GenericOrderedListContract(
        **source_contract,
        source_list_contract_sha256=_canonical_hash(source_contract),
    )


def _derive_edge(edge: Any) -> GenericSemanticEdge:
    payload = {
        "fact_id": edge.fact_id,
        "source_node_id": edge.source_node_id,
        "relation": edge.relation,
        "target_node_id": edge.target_node_id,
        "evidence_ids": edge.evidence_ids,
        "figure_evidence_id": edge.figure_evidence_id,
        "source_manifest_sha256": edge.source_manifest_sha256,
        "semantic_sidecar_sha256": edge.semantic_sidecar_sha256,
    }
    return GenericSemanticEdge(
        fact_id=edge.fact_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        layout_relation=edge.relation,
        evidence_ids=edge.evidence_ids,
        figure_evidence_id=edge.figure_evidence_id,
        semantic_sidecar_sha256=edge.semantic_sidecar_sha256,
        source_manifest_sha256=edge.source_manifest_sha256,
        source_edge_contract_sha256=_canonical_hash(payload),
    )


def _derive_card(*, source: Step3MergedReviewedReleaseCard, source_fixture_sha256: str) -> GenericCardContract:
    if source.presentation not in SOURCE_PRESENTATIONS:
        raise ValueError(f"unsupported source presentation: {source.presentation}")
    render_presentation = render_presentation_for_source(source.presentation)
    visible = tuple(_derive_visible_text(item) for item in source.display_text_provenance)
    edges = tuple(_derive_edge(edge) for edge in source.semantic_edge_provenance)
    nodes = tuple(item.semantic_node_id for item in visible if item.semantic_node_id is not None)
    tables = tuple(_derive_table(item) for item in source.display_text_provenance) if render_presentation == "table" else ()
    lists = tuple(contract for item in source.display_text_provenance if (contract := _derive_list(item)) is not None) if render_presentation == "ordered_list" else ()
    tokens: tuple[GenericTokenBinding, ...] = ()
    if render_presentation in {"constraint", "formula"}:
        tokens = tuple(
            GenericTokenBinding(
                fact_id=item.fact_id,
                text_index=index,
                source_text_sha256=item.source_text_sha256,
                tokens=_extract_tokens(item.visible_text),
                tokens_sha256=_canonical_hash(_extract_tokens(item.visible_text)),
            )
            for index, item in enumerate(visible)
        )
    element_count = {
        "diagram": len(nodes),
        "table": sum(len(contract.cells) for contract in tables) or len(visible),
        "ordered_list": sum(len(contract.items) for contract in lists),
        "constraint": len(visible),
        "formula": len(visible),
    }[render_presentation]
    contract_kind = {
        "diagram": "diagram_nodes_edges",
        "table": "table_grid",
        "ordered_list": "ordered_list_rows",
        "constraint": "constraint_blocks",
        "formula": "formula_blocks",
    }[render_presentation]
    identity = {
        "card_index": source.card_index,
        "source_card_id": source.source_card_id,
        "source_fixture_sha256": source_fixture_sha256,
        "source_card_payload": _source_card_payload(source),
        "render_presentation": render_presentation,
    }
    return GenericCardContract(
        job_id=f"step4-generic-job:{_canonical_hash(identity)[:24]}",
        card_index=source.card_index,
        source_card_id=source.source_card_id,
        sample_id=source.sample_id,
        source_presentation=source.presentation,
        render_presentation=render_presentation,
        fact_ids=source.fact_ids,
        visible_texts=visible,
        semantic_layout_edges=edges,
        diagram_node_ids=nodes,
        table_contracts=tables,
        ordered_list_contracts=lists,
        token_bindings=tokens,
        geometry=GenericGeometry(
            contract_kind=contract_kind,
            element_count=element_count,
        ),
        source_fixture_sha256=source_fixture_sha256,
        source_card_contract_sha256=_canonical_hash(_source_card_payload(source)),
    )


def derive_contracts(fixture: Step3UnifiedReviewedReleaseFixture, *, unified_fixture_sha256: str, handoff_sha256: str) -> Phase2R4ContractBundle:
    """参数化改造：源 fixture/handoff 的文件哈希由调用方注入，不读盘。"""
    cards = tuple(_derive_card(source=card, source_fixture_sha256=unified_fixture_sha256) for card in fixture.cards)
    return Phase2R4ContractBundle(
        source_unified_fixture_sha256=unified_fixture_sha256,
        source_r319_handoff_sha256=handoff_sha256,
        cards=cards,
    )


def _derive_model_input(bundle: Phase2R4ContractBundle) -> Phase2R4ModelInput:
    payload = {
        "schema_version": MODEL_INPUT_VERSION,
        "source_unified_fixture_sha256": bundle.source_unified_fixture_sha256,
        "source_r319_handoff_sha256": bundle.source_r319_handoff_sha256,
        "cards": [card.model_dump(mode="json") for card in bundle.cards],
    }
    return Phase2R4ModelInput(
        model_input_id=f"step4-generic-model-input:{_canonical_hash(payload)[:24]}",
        source_unified_fixture_sha256=bundle.source_unified_fixture_sha256,
        source_r319_handoff_sha256=bundle.source_r319_handoff_sha256,
        cards=bundle.cards,
    )


def _derive_request(model_input: Phase2R4ModelInput) -> Phase2R4Request:
    prompt = Phase2R4PromptContract()
    payload = {
        "schema_version": REQUEST_VERSION,
        "model_input": model_input.model_dump(mode="json"),
        "prompt": prompt.model_dump(mode="json"),
    }
    return Phase2R4Request(
        request_id=f"step4-generic-offline-request:{_canonical_hash(payload)}",
        model_input=model_input,
        prompt_contract=prompt,
    )


def _derive_fake_artifact(card: GenericCardContract, *, filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX) -> Phase2R4FakeArtifact:
    visible = tuple(item.visible_text for item in card.visible_texts)
    table_hashes = tuple(cell.source_cell_contract_sha256 for table in card.table_contracts for cell in table.cells)
    list_hashes = tuple(item.source_item_sha256 for contract in card.ordered_list_contracts for item in contract.items)
    edge_hashes = tuple(item.source_edge_contract_sha256 for item in card.semantic_layout_edges)
    token_hashes = tuple(item.tokens_sha256 for item in card.token_bindings)
    filename = f"{filename_prefix}{card.card_index:02d}-{card.source_card_id.rsplit(':', 1)[-1][:8]}.png"
    payload = {
        "job_id": card.job_id,
        "card_index": card.card_index,
        "source_card_id": card.source_card_id,
        "presentation": card.render_presentation,
        "fact_ids": card.fact_ids,
        "rendered_visible_texts": visible,
        "rendered_visible_text_sha256s": tuple(_sha256(text.encode("utf-8")) for text in visible),
        "rendered_table_cell_sha256s": table_hashes,
        "rendered_list_item_sha256s": list_hashes,
        "rendered_semantic_edge_sha256s": edge_hashes,
        "rendered_token_sha256s": token_hashes,
        "output_filename": filename,
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
    }
    artifact_id = f"step4-generic-fake-artifact:{_canonical_hash(payload)[:24]}"
    simulated = _sha256(b"phase2r4-deterministic-fake-metadata-not-an-image\0" + _json_bytes(payload))
    return Phase2R4FakeArtifact(
        artifact_id=artifact_id,
        job_id=card.job_id,
        card_index=card.card_index,
        source_card_id=card.source_card_id,
        presentation=card.render_presentation,
        fact_ids=card.fact_ids,
        rendered_visible_texts=visible,
        rendered_visible_text_sha256s=tuple(_sha256(text.encode("utf-8")) for text in visible),
        rendered_table_cell_sha256s=table_hashes,
        rendered_list_item_sha256s=list_hashes,
        rendered_semantic_edge_sha256s=edge_hashes,
        rendered_token_sha256s=token_hashes,
        output_filename=filename,
        simulated_image_sha256=simulated,
        artifact_contract_sha256=_canonical_hash({**payload, "artifact_id": artifact_id, "simulated_image_sha256": simulated}),
    )


def _derive_fake_manifest(request: Phase2R4Request, *, filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX) -> Phase2R4FakeArtifactManifest:
    artifacts = tuple(_derive_fake_artifact(card, filename_prefix=filename_prefix) for card in request.model_input.cards)
    request_sha = _canonical_hash(request)
    aggregate = _sha256(_json_bytes([(item.output_filename, item.artifact_contract_sha256) for item in artifacts]))
    return Phase2R4FakeArtifactManifest(
        request_id=request.request_id,
        request_sha256=request_sha,
        artifacts=artifacts,
        aggregate_sha256=aggregate,
    )


def _derive_batch_manifest(*, bundle: Phase2R4ContractBundle, model_input: Phase2R4ModelInput, handoff: Step4PilotHandoffRefresh) -> Phase2R4BatchManifest:
    prior_reviewed = {item.card_index for item in handoff.cards if item.disposition == "reviewed"}
    entries = tuple(
        Phase2R4BatchEntry(
            card_index=card.card_index,
            source_card_id=card.source_card_id,
            sample_id=card.sample_id,
            source_presentation=card.source_presentation,
            render_presentation=card.render_presentation,
            fact_ids=card.fact_ids,
            source_owned_display_text_count=len(card.visible_texts),
            semantic_edge_count=len(card.semantic_layout_edges),
            contract_sha256=_canonical_hash(card),
            model_input_sha256=_canonical_hash(model_input.cards[card.card_index - 1]),
            disposition="prior_reviewed_reference" if card.card_index in prior_reviewed else "offline_compiled",
        )
        for card in bundle.cards
    )
    payload = {
        "source_unified_fixture_sha256": bundle.source_unified_fixture_sha256,
        "source_r319_handoff_sha256": bundle.source_r319_handoff_sha256,
        "cards": [item.model_dump(mode="json") for item in entries],
    }
    return Phase2R4BatchManifest(
        manifest_id=f"step4-generic-batch:{_canonical_hash(payload)[:24]}",
        source_unified_fixture_sha256=bundle.source_unified_fixture_sha256,
        source_r319_handoff_sha256=bundle.source_r319_handoff_sha256,
        cards=entries,
    )


def derive_step4_bundle(
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    *,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> tuple[Phase2R4ContractBundle, Phase2R4ModelInput, Phase2R4Request, Phase2R4FakeArtifactManifest, Phase2R4BatchManifest]:
    """纯计算入口（设计 §4.3）：已装载 fixture/handoff → 全套离线工件。

    装载与 handoff 秩序断言（reviewed inventory=(2,3,4,6) 等）是调用方职责
    （旧仓 `_read_fixture` 粘合或新流水线装载层）；本入口内 batch manifest 的
    模型校验仍会强制这些 v1 钉扎。
    """
    bundle = derive_contracts(fixture, unified_fixture_sha256=unified_fixture_sha256, handoff_sha256=handoff_sha256)
    model_input = _derive_model_input(bundle)
    request = _derive_request(model_input)
    artifacts = _derive_fake_manifest(request, filename_prefix=filename_prefix)
    batch = _derive_batch_manifest(bundle=bundle, model_input=model_input, handoff=handoff)
    return bundle, model_input, request, artifacts, batch
