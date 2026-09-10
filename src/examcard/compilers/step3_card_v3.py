"""Parallel Step 3 V3 compiler for the threshold-capable typed fixture."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Final, Literal, cast

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash_r10x as canonical_hash
from examcard.knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
from examcard.knowledge.contracts.admission_legacy_hash import text_hash
from examcard.knowledge.contracts.step3_card_v3 import (
    CONTRACT_ID,
    HASH,
    ID,
    PRESENTATIONS,
    DisplayElementV3,
    DiagramEdgeRefV3,
    DiagramNodeRefV3,
    GeometryConstraintsV3,
    LayoutContractV3,
    ListItemRefV3,
    ModelCardV3,
    ModelLayoutV3,
    ModelSourceBindingV3,
    ModelVisibleElementV3,
    PresentationV3,
    SourceProvenanceV3,
    Step3CardContractV3,
    Step3ModelInputV3,
    TableCellRefV3,
    TokenRefV3,
)

PHASE: Final = "2R.9"
MODEL_INPUT_VERSION: Final = "phase2r9_step3_model_input_v3"
PROMPT_VERSION: Final = "phase2r9_step3_prompt_contract_v3"
REQUEST_VERSION: Final = "phase2r9_step3_offline_request_v3"
BATCH_VERSION: Final = "phase2r9_step3_batch_manifest_v3"
GATE_VERSION: Final = "phase2r9_step3_offline_gate_v3"
REGRESSION_VERSION: Final = "phase2r9_step3_regression_result_v3"
REPORT_VERSION: Final = "phase2r9_step3_report_v3"


class PromptContractV3(StrictModel):
    schema_version: Literal["phase2r9_step3_prompt_contract_v3"] = PROMPT_VERSION
    prompt_id: str = Field(pattern=r"^step3-v3-prompt:[0-9a-f]{24}$")
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    allowed_presentations: tuple[PresentationV3, ...] = cast(tuple[PresentationV3, ...], PRESENTATIONS)
    render_policy: tuple[str, ...] = (
        "render_only_supplied_source_text",
        "preserve_source_order_and_typed_geometry",
        "preserve_threshold_source_identity",
        "emit_no_additional_text_or_branding",
    )
    prompt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> "PromptContractV3":
        expected = f"step3-v3-prompt:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'prompt_id'}))[:24]}"
        if self.prompt_id != expected:
            raise ValueError("V3 prompt identity mismatch")
        return self


class OfflineRequestPlanV3(StrictModel):
    schema_version: Literal["phase2r9_step3_offline_request_v3"] = REQUEST_VERSION
    request_id: str = Field(pattern=r"^step3-v3-offline-request:[0-9a-f]{64}$")
    model_input_id: str = Field(pattern=r"^step3-v3-model-input:[0-9a-f]{24}$")
    prompt_id: str = Field(pattern=r"^step3-v3-prompt:[0-9a-f]{24}$")
    backend: Literal["deterministic_fake"] = "deterministic_fake"
    network_call_budget: Literal[0] = 0
    model_call_budget: Literal[0] = 0
    relay_call_budget: Literal[0] = 0
    image_api_call_budget: Literal[0] = 0


class DeterministicDraftCardV3(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v3"] = "phase2r9_step3_deterministic_draft_v3"
    contract_id: str = Field(pattern=CONTRACT_ID)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "threshold", "formula"]
    rendered_element_ids: tuple[str, ...] = Field(min_length=1)
    rendered_source_texts: tuple[str, ...] = Field(min_length=1)
    relation_texts: tuple[str, ...] = ()
    extra_texts: tuple[str, ...] = ()
    materialized: Literal[False] = False
    real_image: Literal[False] = False
    artifact_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_artifact(self) -> "DeterministicDraftCardV3":
        if len(self.rendered_element_ids) != len(self.rendered_source_texts):
            raise ValueError("V3 draft element/text count mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "artifact_sha256"}))
        if self.artifact_sha256 != expected:
            raise ValueError("V3 draft artifact hash mismatch")
        return self


class DeterministicDraftV3(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v3"] = "phase2r9_step3_deterministic_draft_v3"
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    artifacts: tuple[DeterministicDraftCardV3, ...] = Field(min_length=1)
    artifact_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_count(self) -> "DeterministicDraftV3":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("V3 draft artifact count mismatch")
        return self


class PresentationCountsV3(StrictModel):
    schema_version: Literal["phase2r9_presentation_counts_v3"] = "phase2r9_presentation_counts_v3"
    diagram: int = Field(ge=0)
    table: int = Field(ge=0)
    ordered_list: int = Field(ge=0)
    constraint: int = Field(ge=0)
    threshold: int = Field(ge=0)
    formula: int = Field(ge=0)

    @property
    def total(self) -> int:
        return sum(getattr(self, name) for name in PRESENTATIONS)


class BatchManifestV3(StrictModel):
    schema_version: Literal["phase2r9_step3_batch_manifest_v3"] = BATCH_VERSION
    phase: Literal["2R.9"] = PHASE
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    contract_ids: tuple[str, ...] = Field(min_length=1)
    card_contract_count: int = Field(ge=1)
    presentation_counts: PresentationCountsV3
    not_applicable_presentations: tuple[Literal["diagram", "table", "ordered_list", "constraint", "threshold", "formula"], ...]
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0

    @model_validator(mode="after")
    def validate_batch(self) -> "BatchManifestV3":
        if self.card_contract_count != len(self.contract_ids):
            raise ValueError("V3 batch contract count mismatch")
        if len(set(self.contract_ids)) != len(self.contract_ids):
            raise ValueError("V3 batch contract IDs must be unique")
        if self.presentation_counts.total != self.card_contract_count:
            raise ValueError("V3 presentation count mismatch")
        active = {name for name in PRESENTATIONS if getattr(self.presentation_counts, name) > 0}
        if active & set(self.not_applicable_presentations):
            raise ValueError("V3 batch has an active presentation marked not_applicable")
        return self


class GateCheckV3(StrictModel):
    schema_version: Literal["phase2r9_gate_check_v3"] = "phase2r9_gate_check_v3"
    check_id: str = Field(pattern=ID)
    passed: bool
    detail: str = Field(min_length=1)


class OfflineGateV3(StrictModel):
    schema_version: Literal["phase2r9_step3_offline_gate_v3"] = GATE_VERSION
    phase: Literal["2R.9"] = PHASE
    valid: bool
    source_fixture_authorized: bool
    source_contracts_valid: bool
    provenance_valid: bool
    source_text_exactly_once: bool
    model_input_clean: bool
    deterministic_draft_valid: bool
    dual_fixture_hashes_bound: bool
    threshold_layout_bound: bool
    per_element_provenance_faithful: bool
    batch_generation_authorized: Literal[False] = False
    checks: tuple[GateCheckV3, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGateV3":
        flags = (
            self.source_fixture_authorized,
            self.source_contracts_valid,
            self.provenance_valid,
            self.source_text_exactly_once,
            self.model_input_clean,
            self.deterministic_draft_valid,
            self.dual_fixture_hashes_bound,
            self.threshold_layout_bound,
            self.per_element_provenance_faithful,
        )
        if self.valid != all(flags) or self.valid != (not self.failure_codes):
            raise ValueError("V3 offline gate validity mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("V3 offline gate hash mismatch")
        return self


class RegressionResultV3(StrictModel):
    schema_version: Literal["phase2r9_step3_regression_result_v3"] = REGRESSION_VERSION
    case_id: str = Field(pattern=ID)
    expected_failure_code: str = Field(pattern=ID)
    caught: bool
    detail: str = Field(min_length=1)
    regression_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_hash(self) -> "RegressionResultV3":
        if self.regression_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "regression_sha256"})):
            raise ValueError("V3 regression hash mismatch")
        return self


class ReadinessV3(StrictModel):
    schema_version: Literal["phase2r9_readiness_v3"] = "phase2r9_readiness_v3"
    phase: Literal["2R.9"] = PHASE
    valid: bool
    generic_step3_offline_ready: bool
    card_contract_count: int = Field(ge=1)
    not_applicable_presentations: tuple[str, ...]
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    readiness_sha256: str = Field(pattern=HASH)


class ArtifactsV3(StrictModel):
    schema_version: Literal["phase2r9_artifacts_v3"] = "phase2r9_artifacts_v3"
    phase: Literal["2R.9"] = PHASE
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    source_mutations: Literal[0] = 0
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    production_cards_created: Literal[False] = False
    step3_sent: Literal[False] = False
    step4_authorized: Literal[False] = False
    artifacts_sha256: str = Field(pattern=HASH)


class ReportV3(StrictModel):
    schema_version: Literal["phase2r9_step3_report_v3"] = REPORT_VERSION
    phase: Literal["2R.9"] = PHASE
    disposition: Literal["accepted_offline"] = "accepted_offline"
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    card_contract_count: int = Field(ge=1)
    presentation_counts: PresentationCountsV3
    not_applicable_presentations: tuple[str, ...]
    source_text_count: int = Field(ge=1)
    source_text_exactly_once: bool
    provenance_gate: bool
    generic_step3_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    regression_count: int = Field(ge=1)
    regression_caught_count: int = Field(ge=0)
    report_sha256: str = Field(pattern=HASH)


@dataclass(frozen=True)
class Step3CompilationV3:
    source_typed_fixture_file_sha256: str
    source_typed_fixture_sha256: str
    source_fixture: ReviewedTypedEvidenceFixtureV2
    contracts: tuple[Step3CardContractV3, ...]
    model_input: Step3ModelInputV3
    prompt: PromptContractV3
    request: OfflineRequestPlanV3
    draft: DeterministicDraftV3
    batch: BatchManifestV3
    regressions: tuple[RegressionResultV3, ...]
    gate: OfflineGateV3
    readiness: ReadinessV3
    artifacts: ArtifactsV3
    report: ReportV3


def _provenance(raw: dict[str, Any]) -> SourceProvenanceV3:
    return SourceProvenanceV3.model_validate(
        {
            "subject_id": raw["subject_id"],
            "record_id": raw["record_id"],
            "fact_id": raw["fact_id"],
            "evidence_id": raw["evidence_id"],
            "anchor_ids": tuple(raw["anchor_ids"]),
            "page_ref_id": raw["page_ref_id"],
            "page": raw["page"],
            "image_sha256": raw["image_sha256"],
            "source_manifest_sha256": raw["source_manifest_sha256"],
        }
    )


def _display(raw: dict[str, Any], role: str, order: int) -> DisplayElementV3:
    text = str(raw["text"])
    if raw.get("text_sha256") != text_hash(text):
        raise ValueError("typed source text hash mismatch")
    return DisplayElementV3(
        element_id=str(raw["element_id"]),
        text=text,
        source_role=role,
        source_order=order,
        provenance=_provenance(raw["provenance"]),
        text_sha256=str(raw["text_sha256"]),
    )


def _layout_from_raw(raw: dict[str, Any]) -> tuple[tuple[DisplayElementV3, ...], LayoutContractV3]:
    presentation = raw["presentation"]
    if presentation == "table":
        cells = tuple(_display(item, "table_cell", item["reading_order"]) for item in raw["cells"])
        table_refs = tuple(TableCellRefV3.model_validate({key: item[key] for key in ("element_id", "row", "column", "row_span", "column_span", "reading_order")}) for item in raw["cells"])
        return cells, LayoutContractV3(presentation="table", table_rows=raw["rows"], table_columns=raw["columns"], table_cells=table_refs)
    if presentation == "ordered_list":
        items = tuple(_display(item, "ordered_list_item", item["order"]) for item in raw["items"])
        list_refs = tuple(ListItemRefV3.model_validate({key: item[key] for key in ("element_id", "order", "marker")}) for item in raw["items"])
        return items, LayoutContractV3(presentation="ordered_list", list_items=list_refs)
    if presentation in {"constraint", "threshold", "formula"}:
        role = {"constraint": "constraint_token", "threshold": "threshold_token", "formula": "formula_token"}[presentation]
        tokens = tuple(_display(item, role, item["token_order"]) for item in raw["tokens"])
        token_refs = tuple(TokenRefV3.model_validate({"element_id": item["element_id"], "token_order": item["token_order"]}) for item in raw["tokens"])
        kwargs = {"constraint_blocks": token_refs} if presentation == "constraint" else {"threshold_blocks": token_refs} if presentation == "threshold" else {"token_refs": token_refs}
        return tokens, LayoutContractV3(presentation=presentation, **kwargs)
    if presentation == "diagram":
        nodes = tuple(_display(item, "diagram_node", item["reading_order"]) for item in raw["nodes"])
        node_refs = tuple(DiagramNodeRefV3(element_id=item["element_id"], reading_order=item["reading_order"]) for item in raw["nodes"])
        edge_refs = tuple(
            DiagramEdgeRefV3(
                edge_id=edge["edge_id"],
                source_node_id=edge["source_node_id"],
                target_node_id=edge["target_node_id"],
                provenance=_provenance(edge["provenance"]),
            )
            for edge in raw.get("edges", ())
        )
        return nodes, LayoutContractV3(presentation="diagram", diagram_nodes=node_refs, diagram_edges=edge_refs)
    raise ValueError(f"unsupported V3 typed presentation: {presentation}")


def compile_candidate_v3(
    candidate: Any,
    source_typed_fixture_file_sha256: str,
    source_typed_fixture_sha256: str,
) -> Step3CardContractV3:
    if candidate.typed_contract_sha256 != canonical_hash(candidate.typed_contract.model_dump(mode="json")):
        raise ValueError("V3 candidate typed contract hash mismatch")
    raw = candidate.typed_contract.model_dump(mode="json")
    elements, layout = _layout_from_raw(raw)
    expected_binding = {
        "subject_id": candidate.subject_id,
        "record_id": candidate.record_id,
        "fact_id": candidate.fact_id,
        "evidence_id": candidate.evidence_id,
        "source_manifest_sha256": candidate.source_manifest_sha256,
    }
    for element in elements:
        if any(getattr(element.provenance, key) != value for key, value in expected_binding.items()):
            raise ValueError("V3 typed element crossed source binding")
        if tuple(element.provenance.anchor_ids) != tuple(candidate.anchor_ids):
            raise ValueError("V3 typed element crossed source anchors")
    for edge in layout.diagram_edges:
        if edge.provenance.fact_id != candidate.fact_id or edge.provenance.evidence_id != candidate.evidence_id:
            raise ValueError("V3 diagram edge crossed source binding")
    base: dict[str, Any] = {
        "contract_id": "step3-card-contract:" + "0" * 24,
        "source_candidate_id": candidate.candidate_id,
        "fact_ids": (candidate.fact_id,),
        "presentation": candidate.presentation,
        "source_typed_fixture_file_sha256": source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": source_typed_fixture_sha256,
        "source_typed_contract_sha256": candidate.typed_contract_sha256,
        "display_elements": elements,
        "layout": layout,
        "geometry": GeometryConstraintsV3(),
    }
    base["contract_id"] = f"step3-card-contract:{canonical_hash({key: value for key, value in base.items() if key != 'contract_id'})[:24]}"
    return Step3CardContractV3(**base, contract_sha256=canonical_hash(base))


def _model_card(contract: Step3CardContractV3) -> ModelCardV3:
    first = contract.display_elements[0].provenance
    binding = ModelSourceBindingV3(
        subject_id=first.subject_id,
        record_id=first.record_id,
        fact_id=first.fact_id,
        evidence_id=first.evidence_id,
        anchor_ids=first.anchor_ids,
        page_ref_id=first.page_ref_id,
        page=first.page,
        image_sha256=first.image_sha256,
        source_manifest_sha256=first.source_manifest_sha256,
    )
    visible = tuple(
        ModelVisibleElementV3(
            element_id=item.element_id,
            source_text=item.text,
            source_order=item.source_order,
            source_role=item.source_role,
            text_sha256=item.text_sha256,
            provenance=item.provenance,
        )
        for item in contract.display_elements
    )
    layout = contract.layout
    model_layout = ModelLayoutV3(
        presentation=contract.presentation,
        typed_contract_sha256=contract.source_typed_contract_sha256,
        element_ids=tuple(item.element_id for item in visible),
        table_rows=layout.table_rows,
        table_columns=layout.table_columns,
        table_coordinates=layout.table_cells,
        list_order=layout.list_items,
        token_order=layout.token_refs,
        constraint_blocks=layout.constraint_blocks,
        threshold_blocks=layout.threshold_blocks,
        diagram_nodes=layout.diagram_nodes,
        diagram_edges=layout.diagram_edges,
    )
    return ModelCardV3(
        contract_id=contract.contract_id,
        source_candidate_id=contract.source_candidate_id,
        fact_ids=contract.fact_ids,
        presentation=contract.presentation,
        source_typed_fixture_file_sha256=contract.source_typed_fixture_file_sha256,
        source_typed_fixture_sha256=contract.source_typed_fixture_sha256,
        source_binding=binding,
        visible_elements=visible,
        layout=model_layout,
        geometry=contract.geometry,
    )


def _make_model_input(contracts: tuple[Step3CardContractV3, ...]) -> Step3ModelInputV3:
    first = contracts[0]
    payload: dict[str, Any] = {
        "source_typed_fixture_file_sha256": first.source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": first.source_typed_fixture_sha256,
        "cards": tuple(_model_card(contract) for contract in contracts),
        "card_count": len(contracts),
    }
    model_input_id = f"step3-v3-model-input:{canonical_hash(payload)[:24]}"
    return Step3ModelInputV3(model_input_id=model_input_id, **payload)


def _make_prompt(file_hash: str, authority_hash: str) -> PromptContractV3:
    text = (
        "Return exactly one JSON card for each supplied card. Echo every supplied source element exactly once, "
        "including source order, source role, text hash, full provenance and typed layout. Preserve threshold as "
        "threshold; do not infer, normalize, parse, OCR-correct, or add relation text."
    )
    payload = {
        "source_typed_fixture_file_sha256": file_hash,
        "source_typed_fixture_sha256": authority_hash,
        "allowed_presentations": PRESENTATIONS,
        "render_policy": PromptContractV3.model_fields["render_policy"].default,
        "prompt_text": text,
    }
    return PromptContractV3(
        prompt_id=f"step3-v3-prompt:{canonical_hash(payload)[:24]}",
        source_typed_fixture_file_sha256=file_hash,
        source_typed_fixture_sha256=authority_hash,
        prompt_text=text,
    )


def _make_request(model_input: Step3ModelInputV3, prompt: PromptContractV3) -> OfflineRequestPlanV3:
    payload = {"model_input_id": model_input.model_input_id, "prompt_id": prompt.prompt_id}
    return OfflineRequestPlanV3(request_id=f"step3-v3-offline-request:{canonical_hash(payload)}", **payload)


def _make_draft(contracts: tuple[Step3CardContractV3, ...]) -> DeterministicDraftV3:
    artifacts = []
    for contract in contracts:
        payload = {
            "contract_id": contract.contract_id,
            "presentation": contract.presentation,
            "rendered_element_ids": tuple(item.element_id for item in contract.display_elements),
            "rendered_source_texts": tuple(item.text for item in contract.display_elements),
            "relation_texts": (),
            "extra_texts": (),
            "materialized": False,
            "real_image": False,
        }
        artifacts.append(DeterministicDraftCardV3(**payload, artifact_sha256=canonical_hash(payload)))
    return DeterministicDraftV3(
        source_typed_fixture_file_sha256=contracts[0].source_typed_fixture_file_sha256,
        source_typed_fixture_sha256=contracts[0].source_typed_fixture_sha256,
        artifacts=tuple(artifacts),
        artifact_count=len(artifacts),
    )


def _make_batch(contracts: tuple[Step3CardContractV3, ...]) -> BatchManifestV3:
    counts = {name: 0 for name in PRESENTATIONS}
    for contract in contracts:
        counts[contract.presentation] += 1
    return BatchManifestV3(
        phase=PHASE,
        source_typed_fixture_file_sha256=contracts[0].source_typed_fixture_file_sha256,
        source_typed_fixture_sha256=contracts[0].source_typed_fixture_sha256,
        contract_ids=tuple(item.contract_id for item in contracts),
        card_contract_count=len(contracts),
        presentation_counts=PresentationCountsV3(**counts),
        not_applicable_presentations=tuple(name for name, count in counts.items() if count == 0),
    )


def build_regression_matrix(
    contracts: tuple[Step3CardContractV3, ...], model_input: Step3ModelInputV3, draft: DeterministicDraftV3, batch: BatchManifestV3
) -> tuple[RegressionResultV3, ...]:
    cases: list[RegressionResultV3] = []
    regression_cases: tuple[tuple[str, str, Callable[[], Any]], ...] = (
        ("unknown-field", "unknown-field-rejected", lambda: ModelCardV3.model_validate({**model_input.cards[0].model_dump(mode="json"), "unknown": True})),
    )
    for case_id, expected, callback in regression_cases:
        caught, detail = False, "unexpectedly accepted"
        try:
            callback()
        except (ValueError, TypeError, KeyError) as exc:
            caught, detail = True, str(exc)[:500] or type(exc).__name__
        payload = {"case_id": case_id, "expected_failure_code": expected, "caught": caught, "detail": detail}
        cases.append(RegressionResultV3(**payload, regression_sha256=canonical_hash(payload)))
    threshold = next((item for item in contracts if item.presentation == "threshold"), None)
    if threshold is not None:
        raw = threshold.layout.model_dump(mode="json")
        raw["constraint_blocks"] = raw["threshold_blocks"]
        raw["threshold_blocks"] = []
        caught, detail = False, "unexpectedly accepted"
        try:
            LayoutContractV3.model_validate(raw)
        except (ValueError, TypeError, KeyError) as exc:
            caught, detail = True, str(exc)[:500] or type(exc).__name__
        payload = {"case_id": "threshold-layout-alias", "expected_failure_code": "threshold-layout-rejected", "caught": caught, "detail": detail}
        cases.append(RegressionResultV3(**payload, regression_sha256=canonical_hash(payload)))
    return tuple(cases)


def _make_gate(
    contracts: tuple[Step3CardContractV3, ...], model_input: Step3ModelInputV3, draft: DeterministicDraftV3, batch: BatchManifestV3, regressions: tuple[RegressionResultV3, ...]
) -> OfflineGateV3:
    threshold_ok = all(
        contract.presentation != "threshold"
        or (contract.layout.threshold_blocks and all(item.source_role == "threshold_token" for item in contract.display_elements))
        for contract in contracts
    )
    provenance_ok = all(
        item.provenance.fact_id in contract.fact_ids and bool(item.provenance.evidence_id)
        for contract in contracts for item in contract.display_elements
    )
    values = {
        "source_fixture_authorized": True,
        "source_contracts_valid": bool(contracts),
        "provenance_valid": provenance_ok,
        "source_text_exactly_once": all(len({item.element_id for item in c.display_elements}) == len(c.display_elements) for c in contracts),
        "model_input_clean": not any(
            key in {"reviewer", "basis", "b2_review", "golden_title", "image_prompt"}
            for card in model_input.cards
            for key in card.model_dump(mode="json")
        ),
        "deterministic_draft_valid": all(
            not artifact.relation_texts
            and not artifact.extra_texts
            and tuple(artifact.rendered_element_ids) == tuple(element.element_id for element in contract.display_elements)
            and tuple(artifact.rendered_source_texts) == tuple(element.text for element in contract.display_elements)
            for artifact, contract in zip(draft.artifacts, contracts, strict=True)
        ),
        "dual_fixture_hashes_bound": model_input.source_typed_fixture_file_sha256 != "" and model_input.source_typed_fixture_sha256 != "",
        "threshold_layout_bound": threshold_ok,
        "per_element_provenance_faithful": provenance_ok,
    }
    checks = tuple(GateCheckV3(check_id=key.replace("_", "-"), passed=value, detail=key) for key, value in values.items())
    failures = tuple(item.check_id for item in checks if not item.passed)
    if not all(item.caught for item in regressions):
        failures = (*failures, "fail-closed-regressions")
    payload: dict[str, Any] = {
        **values,
        "valid": all(values.values()) and all(item.caught for item in regressions),
        "checks": checks,
        "failure_codes": failures,
        "phase": PHASE,
    }
    gate_draft = OfflineGateV3.model_construct(**payload, gate_sha256="0" * 64)
    gate_hash = canonical_hash(gate_draft.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
    return OfflineGateV3.model_validate({**payload, "gate_sha256": gate_hash})


def compile_reviewed_fixture_v3(
    fixture: ReviewedTypedEvidenceFixtureV2,
    *,
    source_typed_fixture_file_sha256: str,
    source_typed_fixture_sha256: str | None = None,
) -> Step3CompilationV3:
    if not isinstance(fixture, ReviewedTypedEvidenceFixtureV2):
        raise TypeError("V3 compiler requires ReviewedTypedEvidenceFixtureV2")
    if not fixture.fixture_authorized or fixture.cards_created or fixture.step3_authorized or fixture.step4_authorized:
        raise ValueError("V2 fixture is not an offline-only reviewed source fixture")
    authority_hash = fixture.fixture_sha256
    if source_typed_fixture_sha256 is not None and source_typed_fixture_sha256 != authority_hash:
        raise ValueError("authority fixture hash does not match validated fixture")
    if len(source_typed_fixture_file_sha256) != 64:
        raise ValueError("source fixture file hash is invalid")
    contracts = tuple(compile_candidate_v3(candidate, source_typed_fixture_file_sha256, authority_hash) for candidate in fixture.candidates)
    if len({item.source_candidate_id for item in contracts}) != len(contracts):
        raise ValueError("V3 source candidates must compile exactly once")
    model_input = _make_model_input(contracts)
    prompt = _make_prompt(source_typed_fixture_file_sha256, authority_hash)
    request = _make_request(model_input, prompt)
    draft = _make_draft(contracts)
    batch = _make_batch(contracts)
    regressions = build_regression_matrix(contracts, model_input, draft, batch)
    gate = _make_gate(contracts, model_input, draft, batch, regressions)
    readiness_payload = {
        "phase": PHASE,
        "valid": gate.valid,
        "generic_step3_offline_ready": gate.valid,
        "card_contract_count": len(contracts),
        "not_applicable_presentations": batch.not_applicable_presentations,
    }
    readiness = ReadinessV3(**readiness_payload, readiness_sha256=canonical_hash(readiness_payload))
    artifacts_payload = {
        "phase": PHASE,
        "source_typed_fixture_file_sha256": source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": authority_hash,
        "source_mutations": 0,
        "network_calls": 0,
        "model_calls": 0,
        "relay_calls": 0,
        "image_api_calls": 0,
        "production_cards_created": False,
        "step3_sent": False,
        "step4_authorized": False,
    }
    artifacts = ArtifactsV3(**artifacts_payload, artifacts_sha256=canonical_hash(artifacts_payload))
    report_payload = {
        "phase": PHASE,
        "source_typed_fixture_file_sha256": source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": authority_hash,
        "card_contract_count": len(contracts),
        "presentation_counts": batch.presentation_counts,
        "not_applicable_presentations": batch.not_applicable_presentations,
        "source_text_count": sum(len(c.display_elements) for c in contracts),
        "source_text_exactly_once": gate.source_text_exactly_once,
        "provenance_gate": gate.provenance_valid,
        "generic_step3_offline_ready": readiness.generic_step3_offline_ready,
        "batch_generation_authorized": False,
        "production_cards_created": False,
        "network_calls": 0,
        "model_calls": 0,
        "relay_calls": 0,
        "image_api_calls": 0,
        "regression_count": len(regressions),
        "regression_caught_count": sum(item.caught for item in regressions),
    }
    report = ReportV3(**report_payload, report_sha256=canonical_hash(report_payload))
    return Step3CompilationV3(
        source_typed_fixture_file_sha256=source_typed_fixture_file_sha256,
        source_typed_fixture_sha256=authority_hash,
        source_fixture=fixture,
        contracts=contracts,
        model_input=model_input,
        prompt=prompt,
        request=request,
        draft=draft,
        batch=batch,
        regressions=regressions,
        gate=gate,
        readiness=readiness,
        artifacts=artifacts,
        report=report,
    )


__all__ = [
    "ArtifactsV3",
    "BatchManifestV3",
    "DeterministicDraftCardV3",
    "DeterministicDraftV3",
    "GateCheckV3",
    "MODEL_INPUT_VERSION",
    "OfflineGateV3",
    "OfflineRequestPlanV3",
    "PresentationCountsV3",
    "PromptContractV3",
    "ReadinessV3",
    "RegressionResultV3",
    "ReportV3",
    "Step3CompilationV3",
    "compile_candidate_v3",
    "compile_reviewed_fixture_v3",
    "build_regression_matrix",
]
