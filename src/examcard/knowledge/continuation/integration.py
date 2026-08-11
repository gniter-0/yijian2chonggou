"""V17 request integration for one reviewed, adjacent-page table continuation.

This module is intentionally outside ``semantic_contracts`` to avoid its
dependency cycle with ``table_continuation``.  It verifies the reviewed
sidecar against immutable PDF/manifests before constructing the narrow,
table-only V17 input.  It never changes a source manifest or normal projection.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ...evidence.schema import EvidenceUnit, PageEvidence
from ...materials.catalog import load_content_policy
from ...materials.schema import ContentPolicy

from .review import load_continuation_review_sidecar
from ..schema import FactType
from ..contracts import (
    ContinuationBindingState,
    Step2ARequest,
    Step2ASemanticProjection,
    Step2ASectionInput,
    Step2DecoderConfig,
    Step2ResponseFormat,
    StructuredTableInput,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
    TableAtomicSubfactKind,
    TableAtomicSubfactRequirement,
    TableCellInput,
    TableContinuationRequestInput,
    TableContinuationSourceCellInput,
    TableContinuationSourcePageInput,
    TableRowInput,
    build_step2a_section,
    build_step2a_request,
    build_continuation_binding,
    build_reviewed_continuation_bindings,
    make_table_atomic_subfact_requirement_id,
    make_table_continuation_controlled_section_id,
    make_table_continuation_request_input_id,
)
from ..projection import build_step2a_semantic_projection
from .core import (
    TableContinuationBinding,
    TableContinuationAnchorRole,
    load_table_continuation_binding,
    sha256_file,
    verify_table_continuation_binding,
)


SAMPLE_ID = "gonglu_24_continuation_real"
MATERIAL_PROFILE_ID = "yijian_gonglu_2026"
POLICY_REFERENCE = Path("configs/v2/content_policies/core_only_v1.yaml")
CONTINUATION_REVIEW_REFERENCE = Path(
    "configs/v2/continuation_review_sidecars/gonglu_24_continuation_real.reviewed.yaml"
)
TABLE_CONTINUATION_REFERENCE = Path(
    "configs/v2/table_continuation_sidecars/gonglu_24_stiffness_page_2_3.reviewed.yaml"
)
PAGE_REFERENCES = tuple(
    Path(f"data/shadow_v2/gonglu/phase3b11_smoke_20260726/chapters/24/evidence/pages/page_{page_num:04d}.json")
    for page_num in (1, 2)
)


def build_phase2r09_fake_payload(*, request: Step2ARequest) -> dict[str, object]:
    """Return the independent V17 fake-LLM payload used only by offline tests.

    It deliberately uses no V16 request, response, proposal key, or Card
    Golden artifact.  Local V17 derivation creates all proposal IDs.
    """

    if request.identity.request_version not in {
        TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
        TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    }:
        raise ValueError("Phase 2R.0.9 fake payload requires the V17-V22 request contracts")
    if len(request.table_continuation_inputs) != 1:
        raise ValueError("Phase 2R.0.9 fake payload requires one controlled table input")
    controlled = request.table_continuation_inputs[0]
    required = (
        controlled.item_three_source_anchor_span_id,
        controlled.item_three_closure_anchor_span_id,
    )
    return {
        "proposals": [
            {
                "fact_type": "enumeration",
                "label": "模板、支架和拱架设计荷载",
                "assertion": "模板、支架设计应考虑的荷载包括：模板、支架自重；新浇筑混凝土、钢筋、预应力筋或其他圬工结构物的重力；施工人员及施工设备、施工材料等荷载；振捣混凝土时产生的振动荷载；新浇筑混凝土对模板侧面的压力；混凝土入模时产生的水平方向的冲击荷载；水中支架需考虑水流压力、波浪力、流冰压力、船只及其他漂浮物的撞击力；其他可能产生的荷载，如风荷载、雪荷载、冬季保温设施荷载、温度应力等。",
                "primary_anchor_span_ids": [
                    "span:cea2035ddacabc721b271e21",
                    "span:a1dfa1f665ec21bdb3b97075",
                    "span:9d62f96bafdb8f9472a44f23",
                    "span:bb66d05868e704c34e0c6369",
                    "span:2c796fa64b63ca67d6eae4a5",
                    "span:82018b60699459ef22613315",
                    "span:9fa1963ec8e8550b1192cbee",
                    "span:13ae8ebf3ed18d1fb4b3c0a7",
                    "span:267ce7a9b632f1a1bdc5f20f",
                    "span:014f65f569ffe6811eac35fa",
                    "span:590551ea7dc2068a9c0e1cb4",
                    "span:3fe62f2d39d3c4fb615b190d",
                    "span:898df627d76ee86d34977d70",
                    "span:44c778bbaa62168eabd5d9a3",
                ],
            },
            {
                "fact_type": "composition",
                "label": "模板和支架要求表",
                "assertion": "该表由模板、支架及其对应要求组成。",
                "primary_anchor_span_ids": [
                    "span:476ebd60eb860a6bb19256e1",
                    "span:51a283208982491fcc193a60",
                    "span:c16daf27d09188296c4569f0",
                    "span:b919486f40b4ac019800c011",
                    "span:009fdbc4b16370bbab8cb1f4",
                    "span:27fe6799c472e389766c5cf4",
                    "span:6484bb3d2e48843a9788b5a9",
                    "span:23620274e8a990e169e07352",
                    "span:3c89620e5dbec074dae5e483",
                    "span:7407086a2d1ff27d1b8718de",
                    "span:08670bbde10f3ee673f23225",
                    "span:9889154bd9d76de0be530666",
                    "span:8e89102fc8dc7e6b288b5a3c",
                    "span:317ee5f329cbfec7820662de",
                ],
            },
            {
                "fact_type": "condition",
                "label": "模板和支架强度刚度稳定性要求",
                "assertion": "（2）模板和支架应具有足够的强度、刚度和稳定性。",
                "primary_anchor_span_ids": ["span:51a283208982491fcc193a60"],
            },
            {
                "fact_type": "condition",
                "label": "模板接触面隔离剂要求",
                "assertion": "（4）模板与混凝土的接触面应涂刷隔离剂，但不得采用废机油等油料。",
                "primary_anchor_span_ids": ["span:c16daf27d09188296c4569f0"],
            },
            {
                "fact_type": "condition",
                "label": "模板吊环钢筋要求",
                "assertion": "（5）在模板上设置的吊环应采用HPB300 钢筋（一级钢），严禁采用冷加工",
                "primary_anchor_span_ids": ["span:b919486f40b4ac019800c011"],
            },
            {
                "fact_type": "threshold",
                "label": "模板吊环拉应力限值",
                "assertion": "在模板自重标准值作用下，吊环的拉应力应不大于65MPa。",
                "primary_anchor_span_ids": ["span:27fe6799c472e389766c5cf4"],
            },
            {
                "fact_type": "condition",
                "label": "支架与应急安全通道连接限制",
                "assertion": "（6）支架不得与应急安全通道相连接。",
                "primary_anchor_span_ids": ["span:08670bbde10f3ee673f23225"],
            },
            {
                "fact_type": "composition",
                "label": "刚度验算要求表",
                "assertion": "刚度验算表由刚度验算及其对应要求组成。",
                "primary_anchor_span_ids": list(controlled.complete_table_anchor_span_ids),
            },
            {
                "fact_type": "threshold",
                "label": "外露模板挠度限值",
                "assertion": "① 结构表面外露的模板，挠度不超过模板构件跨度的1/400。",
                "primary_anchor_span_ids": ["span:a812441493c325cc2deb9596"],
            },
            {
                "fact_type": "threshold",
                "label": "隐蔽模板挠度限值",
                "assertion": "② 结构表面隐蔽的模板，挠度不超过模板构件跨度的1/250。",
                "primary_anchor_span_ids": ["span:db4c9c76f16395683824ff02"],
            },
            {
                "fact_type": "threshold",
                "label": "支架受荷载后挠曲杆件弹性挠度限值",
                "assertion": controlled.item_three_required_assertion,
                "primary_anchor_span_ids": list(required),
            },
        ]
    }


@dataclass(frozen=True)
class TableContinuationRequestBuild:
    request: Step2ARequest
    controlled_input: TableContinuationRequestInput
    ordinary_projections: tuple[Step2ASemanticProjection, ...]
    ordinary_sections: tuple[Step2ASectionInput, ...]


def _exclude_controlled_table_from_ordinary_page(
    *,
    projection: Step2ASemanticProjection,
    page: PageEvidence,
    controlled_table_evidence_id: str,
) -> tuple[Step2ASemanticProjection, Step2ASectionInput]:
    """Keep page 2 ordinary input while removing every member of the reviewed table.

    The raw page manifest and its full policy-decision ledger remain unchanged.
    Only this request-local ordinary projection loses the table hierarchy; the
    same source table then enters through the independently verified V17 input.
    """

    units = tuple(sorted(page.evidence_units, key=lambda item: item.order))
    blocked = {controlled_table_evidence_id}
    changed = True
    while changed:
        changed = False
        for unit in units:
            if unit.parent_id in blocked and unit.evidence_id not in blocked:
                blocked.add(unit.evidence_id)
                changed = True
    direct_evidence_ids = tuple(item for item in projection.direct_evidence_ids if item not in blocked)
    anchor_spans = tuple(item for item in projection.anchor_spans if item.parent_evidence_id not in blocked)
    provisional = projection.model_copy(
        update={
            "direct_evidence_ids": direct_evidence_ids,
            "context_only_evidence_ids": tuple(
                item for item in projection.context_only_evidence_ids if item not in blocked
            ),
            "anchor_spans": anchor_spans,
        }
    )
    narrowed = Step2ASemanticProjection.model_validate(
        {
            "projection_id": f"step2a-projection:{provisional.fingerprint_sha256[:24]}",
            **provisional.model_dump(mode="json", exclude={"projection_id"}),
        }
    )
    units_by_id = {item.evidence_id: item for item in units}
    section = build_step2a_section(
        units=(units_by_id[item] for item in narrowed.direct_evidence_ids),
        anchor_spans=narrowed.anchor_spans,
        context_only_evidence_ids=narrowed.context_only_evidence_ids,
    )
    return narrowed, section


def _page_units(repository_root: Path, reference: str) -> dict[str, EvidenceUnit]:
    page_path = (repository_root / reference).resolve()
    page = PageEvidence.model_validate_json(page_path.read_bytes())
    return {unit.evidence_id: unit for unit in page.evidence_units}


def _source_pages(
    *, binding: TableContinuationBinding, repository_root: Path
) -> tuple[TableContinuationSourcePageInput, ...]:
    pages: dict[tuple[int, str], list] = {}
    for evidence in binding.evidence_bindings:
        pages.setdefault((evidence.page_num, evidence.source_manifest_sha256), []).append(evidence)

    result: list[TableContinuationSourcePageInput] = []
    for (page_num, manifest_sha256), entries in sorted(pages.items()):
        first = entries[0]
        if any(
            item.source_manifest_path != first.source_manifest_path
            or item.table_evidence_id != first.table_evidence_id
            or item.table_bbox != first.table_bbox
            or item.row_evidence_id != first.row_evidence_id
            or item.row_bbox != first.row_bbox
            for item in entries
        ):
            raise ValueError("reviewed table continuation page hierarchy is internally inconsistent")
        cells = tuple(
            TableContinuationSourceCellInput(
                evidence_id=item.cell_evidence_id,
                bbox=item.cell_bbox,
                canonical_text_sha256=item.cell_canonical_text_sha256,
            )
            for item in entries
        )
        evidence_ids = (first.table_evidence_id, first.row_evidence_id, *(item.evidence_id for item in cells))
        result.append(
            TableContinuationSourcePageInput(
                controlled_section_id=make_table_continuation_controlled_section_id(
                    page_num=page_num,
                    source_manifest_sha256=manifest_sha256,
                    binding_id=binding.binding_id,
                    evidence_ids=evidence_ids,
                ),
                page_num=page_num,
                source_manifest_path=first.source_manifest_path,
                source_manifest_sha256=manifest_sha256,
                table_evidence_id=first.table_evidence_id,
                table_bbox=first.table_bbox,
                row_evidence_id=first.row_evidence_id,
                row_bbox=first.row_bbox,
                cells=cells,
            )
        )
    return tuple(result)


def build_table_continuation_request_input(
    *,
    binding_path: Path,
    repository_root: Path,
    content_policy: ContentPolicy,
    use_stiffness_atomic_threshold_coverage: bool = False,
) -> TableContinuationRequestInput:
    """Rebind a reviewed sidecar and make the V17 table-only projection input."""

    binding_path = Path(binding_path)
    binding = load_table_continuation_binding(binding_path)
    verified_spans = verify_table_continuation_binding(
        binding=binding,
        repository_root=repository_root,
        content_policy=content_policy,
    )
    source_pages = _source_pages(binding=binding, repository_root=repository_root)
    spans_by_parent: dict[str, tuple] = {}
    for span in verified_spans:
        spans_by_parent[span.parent_evidence_id] = (*spans_by_parent.get(span.parent_evidence_id, ()), span)

    page_units = {
        page.source_manifest_sha256: _page_units(repository_root, page.source_manifest_path)
        for page in source_pages
    }
    first_page, second_page = source_pages
    first_cells = {item.evidence_id: item for item in first_page.cells}
    second_cells = {item.evidence_id: item for item in second_page.cells}
    if len(first_cells) != 2 or len(second_cells) != 1:
        raise ValueError("reviewed stiffness-table continuation requires two source cells and one adjacent closure cell")
    first_content_id = binding.item_three_source_anchor_span_id
    first_content_id = next(
        item.parent_evidence_id
        for item in binding.anchor_bindings
        if item.anchor_span_id == first_content_id
    )
    first_header_id = next(item.evidence_id for item in first_page.cells if item.evidence_id != first_content_id)
    second_content_id = next(iter(second_cells))
    first_units = page_units[first_page.source_manifest_sha256]
    second_units = page_units[second_page.source_manifest_sha256]
    first_header = first_units[first_header_id]
    first_content = first_units[first_content_id]
    second_content = second_units[second_content_id]
    item_three_source = next(item for item in verified_spans if item.anchor_span_id == binding.item_three_source_anchor_span_id)
    def threshold_requirement(*, span, assertion: str) -> TableAtomicSubfactRequirement:
        assertion_sha256 = hashlib.sha256(assertion.encode("utf-8")).hexdigest()
        return TableAtomicSubfactRequirement(
            requirement_id=make_table_atomic_subfact_requirement_id(
                table_evidence_id=binding.binding_id,
                cell_evidence_id=span.parent_evidence_id,
                anchor_span_id=span.anchor_span_id,
                kind=TableAtomicSubfactKind.THRESHOLD,
                required_assertion_sha256=assertion_sha256,
            ),
            table_evidence_id=binding.binding_id,
            cell_evidence_id=span.parent_evidence_id,
            anchor_span_id=span.anchor_span_id,
            kind=TableAtomicSubfactKind.THRESHOLD,
            expected_fact_type=FactType.THRESHOLD,
            required_assertion=assertion,
            required_assertion_sha256=assertion_sha256,
        )

    complete_row_span_ids = tuple(
        item.anchor_span_id
        for item in binding.anchor_bindings
        if item.role == TableContinuationAnchorRole.COMPLETE_ROW
    )
    if len(complete_row_span_ids) != 2 or len(set(complete_row_span_ids)) != 2:
        raise ValueError("reviewed stiffness-table continuation requires exactly two complete-row threshold anchors")
    complete_row_spans = tuple(
        next(item for item in verified_spans if item.anchor_span_id == anchor_span_id)
        for anchor_span_id in complete_row_span_ids
    )
    requirements = (
        *(
            threshold_requirement(span=span, assertion=span.canonical_text)
            for span in complete_row_spans
        ),
        threshold_requirement(span=item_three_source, assertion=binding.item_three_required_assertion),
    ) if use_stiffness_atomic_threshold_coverage else (
        threshold_requirement(span=item_three_source, assertion=binding.item_three_required_assertion),
    )
    table = StructuredTableInput(
        table_evidence_id=binding.binding_id,
        page_num=first_page.page_num,
        row_count=2,
        column_count=2,
        rows=(
            TableRowInput(
                evidence_id=first_page.row_evidence_id,
                row_index=0,
                cells=(
                    TableCellInput(
                        evidence_id=first_header_id,
                        row_index=0,
                        column_index=0,
                        row_span=2,
                        canonical_text=first_header.canonical_text,
                        anchor_spans=spans_by_parent[first_header_id],
                    ),
                    TableCellInput(
                        evidence_id=first_content_id,
                        row_index=0,
                        column_index=1,
                        canonical_text=first_content.canonical_text,
                        anchor_spans=spans_by_parent[first_content_id],
                    ),
                ),
            ),
            TableRowInput(
                evidence_id=second_page.row_evidence_id,
                row_index=1,
                cells=(
                    TableCellInput(
                        evidence_id=second_content_id,
                        row_index=1,
                        column_index=1,
                        canonical_text=second_content.canonical_text,
                        anchor_spans=spans_by_parent[second_content_id],
                    ),
                ),
            ),
        ),
        atomic_subfact_requirements=requirements,
    )
    payload = {
        "input_version": (
            STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION
            if use_stiffness_atomic_threshold_coverage
            else "step2a_table_continuation_input_v1"
        ),
        "binding_id": binding.binding_id,
        "binding_fingerprint_sha256": binding.fingerprint_sha256,
        "binding_sidecar_sha256": sha256_file(binding_path),
        "sample_id": binding.sample_id,
        "source_document_sha256": binding.source_document_sha256,
        "content_policy_id": binding.content_policy_id,
        "content_policy_revision": binding.content_policy_revision,
        "content_policy_fingerprint_sha256": binding.content_policy_fingerprint_sha256,
        "source_pages": [item.model_dump(mode="json") for item in source_pages],
        "table": table.model_dump(mode="json"),
        "anchor_spans": [item.model_dump(mode="json") for item in verified_spans],
        "complete_table_anchor_span_ids": list(binding.complete_table_anchor_span_ids),
        "item_three_source_anchor_span_id": binding.item_three_source_anchor_span_id,
        "item_three_closure_anchor_span_id": binding.item_three_closure_anchor_span_id,
        "item_three_required_assertion": binding.item_three_required_assertion,
    }
    return TableContinuationRequestInput.model_validate(
        {
            "input_id": make_table_continuation_request_input_id(payload),
            **payload,
        }
    )


def build_phase2r09_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v17",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r09_offline_deterministic_fixture_v1",
    use_coverage_obligation_contract: bool = False,
    use_coverage_obligation_context_contract: bool = False,
    use_stiffness_atomic_threshold_coverage_contract: bool = False,
    use_composition_numeric_occurrence_contract: bool = False,
    use_structural_index_occurrence_contract: bool = False,
    use_source_owned_structural_projection_contract: bool = False,
) -> TableContinuationRequestBuild:
    """Construct an independent V17 request from immutable V2 assets.

    ``manual_reissue_reason`` is part of the request identity.  A real V17
    verification therefore cannot reuse the offline candidate request ID.
    """

    policy = load_content_policy(repository_root / POLICY_REFERENCE)
    ordinary_builds = tuple(
        build_step2a_semantic_projection(
            sample_id=SAMPLE_ID,
            page=PageEvidence.model_validate_json((repository_root / reference).read_bytes()),
            source_manifest_path=repository_root / reference,
            source_manifest_reference=reference.as_posix(),
            content_policy=policy,
            repository_root=repository_root,
        )
        for reference in PAGE_REFERENCES
    )
    controlled_input = build_table_continuation_request_input(
        binding_path=repository_root / TABLE_CONTINUATION_REFERENCE,
        repository_root=repository_root,
        content_policy=policy,
        use_stiffness_atomic_threshold_coverage=(
            use_stiffness_atomic_threshold_coverage_contract
            or use_composition_numeric_occurrence_contract
            or use_structural_index_occurrence_contract
            or use_source_owned_structural_projection_contract
        ),
    )
    page_two = PageEvidence.model_validate_json((repository_root / PAGE_REFERENCES[1]).read_bytes())
    narrowed_page_two_projection, narrowed_page_two_section = _exclude_controlled_table_from_ordinary_page(
        projection=ordinary_builds[1].projection,
        page=page_two,
        controlled_table_evidence_id=controlled_input.source_pages[0].table_evidence_id,
    )
    ordinary_projections = (ordinary_builds[0].projection, narrowed_page_two_projection)
    ordinary_sections = (ordinary_builds[0].section, narrowed_page_two_section)
    review_path = repository_root / CONTINUATION_REVIEW_REFERENCE
    review_sidecar = load_continuation_review_sidecar(review_path)
    reviewed_bindings = build_reviewed_continuation_bindings(review_sidecar)
    rebound_bindings = tuple(
        build_continuation_binding(
            continuation_id=binding.continuation_id,
            state=binding.state,
            source_projection_id=binding.source_projection_id,
            source_manifest_sha256=binding.source_manifest_sha256,
            source_page_num=binding.source_page_num,
            source_section_id=binding.source_section_id,
            source_anchor_span_ids=binding.source_anchor_span_ids,
            adjacent_projection_id=narrowed_page_two_projection.projection_id,
            adjacent_manifest_sha256=narrowed_page_two_projection.source_manifest_sha256,
            adjacent_page_num=narrowed_page_two_projection.page_num,
            adjacent_section_id=narrowed_page_two_section.section_id,
            closure_anchor_span_ids=binding.closure_anchor_span_ids,
            closure_basis=binding.closure_basis,
        )
        if binding.state == ContinuationBindingState.CLOSED
        else binding
        for binding in reviewed_bindings
    )
    request = build_step2a_request(
        sample_id=SAMPLE_ID,
        material_profile_id=MATERIAL_PROFILE_ID,
        projection=ordinary_projections[0],
        additional_projections=(ordinary_projections[1],),
        sections=ordinary_sections,
        continuation_review_sidecar=review_sidecar,
        continuation_review_sidecar_path=review_path,
        continuation_repository_root=repository_root,
        continuation_bindings=rebound_bindings,
        table_continuation_inputs=(controlled_input,),
        model=model,
        relay_base_url=relay_base_url,
        decoder=Step2DecoderConfig(max_tokens=3000, response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT),
        manual_reissue_reason=manual_reissue_reason,
        use_table_continuation_contract=True,
        use_table_coverage_obligation_contract=use_coverage_obligation_contract,
        use_table_coverage_obligation_context_contract=use_coverage_obligation_context_contract,
        use_table_stiffness_atomic_threshold_coverage_contract=use_stiffness_atomic_threshold_coverage_contract,
        use_composition_numeric_occurrence_contract=use_composition_numeric_occurrence_contract,
        use_structural_index_occurrence_contract=use_structural_index_occurrence_contract,
        use_source_owned_structural_projection_contract=use_source_owned_structural_projection_contract,
    )
    expected_version = (
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION
        if use_source_owned_structural_projection_contract
        else STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION
        if use_structural_index_occurrence_contract
        else COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION
        if use_composition_numeric_occurrence_contract
        else TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION
        if use_stiffness_atomic_threshold_coverage_contract
        else TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION
        if use_coverage_obligation_context_contract
        else TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION
        if use_coverage_obligation_contract
        else TABLE_CONTINUATION_STEP2A_REQUEST_VERSION
    )
    if request.identity.request_version != expected_version:
        raise ValueError("Phase 2R.0.9 request did not select its expected controlled-table contract")
    return TableContinuationRequestBuild(
        request=request,
        controlled_input=controlled_input,
        ordinary_projections=ordinary_projections,
        ordinary_sections=ordinary_sections,
    )


def build_phase2r093_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v18",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r093_offline_coverage_obligation_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build the V18 coverage-obligation successor without reading Learning Golden data."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_coverage_obligation_contract=True,
    )


def build_phase2r095_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v19",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r095_offline_coverage_obligation_context_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build V19 source-derived obligation context without reading Learning Golden data."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_coverage_obligation_context_contract=True,
    )


def build_phase2r097_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v20",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r097_offline_stiffness_atomic_threshold_coverage_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build V20 from the reviewed stiffness binding without Learning Golden input."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_stiffness_atomic_threshold_coverage_contract=True,
    )


def build_phase2r0982_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v21",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r0982_offline_composition_numeric_occurrence_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build V21 from immutable sources without reusing a V20 request or response."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_composition_numeric_occurrence_contract=True,
    )


def build_phase2r0984_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v22",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r0984_offline_structural_index_occurrence_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build V22 from immutable sources without reusing any V21 request or response."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_structural_index_occurrence_contract=True,
    )


def build_phase2r0985_request(
    *,
    repository_root: Path,
    model: str = "deterministic-fake-step2a-v23",
    relay_base_url: str = "offline://deterministic-fake",
    manual_reissue_reason: str = "phase2r0985_offline_source_owned_structural_projection_fixture_v1",
) -> TableContinuationRequestBuild:
    """Build V23 from immutable sources without reusing V21 or V22 history."""

    return build_phase2r09_request(
        repository_root=repository_root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
        use_source_owned_structural_projection_contract=True,
    )
