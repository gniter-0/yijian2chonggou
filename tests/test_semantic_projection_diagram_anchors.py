"""受控图示节点标签隔离 span 道（V25 投影扩锚卡）回归钉。

矩阵（检查点 A 核准 + T3(d) 追加）：
- 合成层（语料无关）：铸造规则六形态（kind 过滤 / 空文本 / 政策排除 / 未绑定 /
  未评审图 / keep-first 去重）、无图页字节形（键不出现）、v5/v6 版本门、
  v24 请求面（版本对 / 一般池隔离 / prompt 渲染面隔离 / 变体与多页拒绝 /
  V25 家族拒绝）。
- 真语料层（金样镜像 + A_RUN 冻结产物，缺席自动 skip；r810-replay 口径）：
  gonglu_41 可锚证明（铸 4 锚 4，node-drainage 铸 0，非受控图不铸，一般池 40 对拍）、
  gonglu_01 宁缺毋滥卫兵（铸 0 仍 fail-closed）、A_RUN/A2_RUN 冻结 request
  sections 逐字节 round-trip（序列化纪律）。
gonglu_62/55 既有锚判定回归由 tests/test_migration_b1_diagram_admission.py 承担
（全量道 T7 同场覆盖，不重复钉）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    BBox,
    EvidenceKind,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.contracts import (
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_REQUEST_VERSION,
    PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION,
    STEP2A_REQUEST_VERSION,
    STEP2A_SEMANTIC_PROJECTION_VERSION,
    Step2ASectionInput,
    Step2ASemanticProjection,
    build_step2a_request,
    canonical_step2_sha256,
)
from examcard.knowledge.contracts.request_helpers import _step2a_anchor_sources
from examcard.knowledge.diagram.sidecar import (
    DiagramEdge as SidecarDiagramEdge,
    DiagramNode as SidecarDiagramNode,
    DiagramReviewAudit,
    DiagramReviewEntry,
    DiagramReviewSidecar,
    DiagramReviewState,
    DiagramSemanticContractVersion,
    DiagramType,
)
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy
from examcard.materials.schema import SemanticRole

POLICY_PATH = ROOT / "configs/v2/content_policies/core_only_v1.yaml"
PDF_SHA = "b" * 64
PAGE_NUM = 3
SAMPLE_ID = "synthetic_diagram_anchor_case"

_SURVEY_TOOL_PATH = ROOT / "migration" / "b1_echo_diagram_admission_survey_v2.py"
_SURVEY_SPEC = importlib.util.spec_from_file_location("b1e_survey_under_test", _SURVEY_TOOL_PATH)
assert _SURVEY_SPEC is not None and _SURVEY_SPEC.loader is not None
survey_tool = importlib.util.module_from_spec(_SURVEY_SPEC)
_SURVEY_SPEC.loader.exec_module(survey_tool)

corpus = pytest.mark.skipif(not survey_tool._corpus_ready(), reason="machine-local frozen corpus not present")


def _unit(
    *,
    suffix: str,
    kind: EvidenceKind,
    text: str,
    order: int,
    parent_id: str | None = None,
    role: SemanticRole = SemanticRole.CORE,
) -> EvidenceUnit:
    locator = SourceLocator(document_sha256=PDF_SHA, page_num=PAGE_NUM, bbox=BBox(x0=10, y0=20 + order, x1=100, y1=40 + order))
    fingerprint = make_evidence_fingerprint(kind=kind.value, canonical_text=text or suffix, semantic_context={"case": suffix})
    return EvidenceUnit(
        evidence_id=make_evidence_id(locator_id=make_source_locator_id(locator), fingerprint=fingerprint),
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=text,
            source_channels=[str(SourceChannel.VISUAL_OCR)],
            engine_versions={"ocr": "test"},
            config_hash="cfg-test",
        ),
        document_id=make_document_id(PDF_SHA),
        kind=kind,
        locator=locator,
        raw_text=text,
        canonical_text=text,
        semantic_role=role,
        source_quality=SourceQuality.VISUAL_ONLY,
        confidence=0.97,
        parent_id=parent_id,
        order=order,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.VISUAL_OCR}),
            engine_versions={"ocr": "test"},
            config_hash="cfg-test",
            generated_at_utc="2026-08-13T00:00:00Z",
        ),
    )


def _synthetic_case(tmp_path: Path, *, with_diagram: bool = True):
    """一页合成语料：正文 1 + 受控图 F1（节点四形态）+ 未评审图 F2。"""
    paragraph = _unit(suffix="p1", kind=EvidenceKind.PARAGRAPH, text="本页正文用于一般锚道对照。", order=1)
    units = [paragraph]
    reviewed_figure = None
    node_minted = node_blank = node_noise = node_unbound = other_figure_node = None
    if with_diagram:
        reviewed_figure = _unit(suffix="f1", kind=EvidenceKind.FIGURE, text="image:synthetic-f1", order=2)
        node_minted = _unit(suffix="n1", kind=EvidenceKind.DIAGRAM_NODE, text="  围岩支护  ", order=3, parent_id=reviewed_figure.evidence_id)
        node_blank = _unit(suffix="n2", kind=EvidenceKind.DIAGRAM_NODE, text="   ", order=4, parent_id=reviewed_figure.evidence_id)
        node_noise = _unit(
            suffix="n3",
            kind=EvidenceKind.DIAGRAM_NODE,
            text="环球网校水印",
            order=5,
            parent_id=reviewed_figure.evidence_id,
            role=SemanticRole.WATERMARK,
        )
        node_unbound = _unit(suffix="n4", kind=EvidenceKind.DIAGRAM_NODE, text="未绑定节点", order=6, parent_id=reviewed_figure.evidence_id)
        unreviewed_figure = _unit(suffix="f2", kind=EvidenceKind.FIGURE, text="image:synthetic-f2", order=7)
        other_figure_node = _unit(suffix="n5", kind=EvidenceKind.DIAGRAM_NODE, text="他图节点", order=8, parent_id=unreviewed_figure.evidence_id)
        units.extend([reviewed_figure, node_minted, node_blank, node_noise, node_unbound, unreviewed_figure, other_figure_node])
    page = PageEvidence(
        page_id="page:synthetic-anchor-case",
        page_num=PAGE_NUM,
        width=595.0,
        height=842.0,
        evidence_units=tuple(units),
        quality=PageQuality(native_text_coverage=0.5, ocr_alignment_rate=0.5, unresolved_regions=0, conflict_count=0),
    )
    manifest_path = tmp_path / f"synthetic_page_{PAGE_NUM:04d}.json"
    manifest_path.write_text(page.model_dump_json(), encoding="utf-8")

    sidecar = None
    sidecar_path = None
    if with_diagram:
        crop_sha = "c" * 64
        entry = DiagramReviewEntry(
            review_id="diagram:" + hashlib.sha256(b"synthetic-f1").hexdigest()[:24],
            sample_id=SAMPLE_ID,
            pdf_sha256=PDF_SHA,
            page_num=PAGE_NUM,
            figure_evidence_id=reviewed_figure.evidence_id,
            figure_crop_path="crops/synthetic_f1.png",
            figure_crop_sha256=crop_sha,
            state=DiagramReviewState.REVIEWED,
            semantic_contract_version=DiagramSemanticContractVersion.SEMANTIC_V2,
            title="合成受控图",
            diagram_type=DiagramType.CROSS_SECTION,
            nodes=(
                SidecarDiagramNode(node_id="node-minted", label="围岩支护", evidence_ids=(reviewed_figure.evidence_id, node_minted.evidence_id)),
                SidecarDiagramNode(node_id="node-blank", label="空白节点", evidence_ids=(reviewed_figure.evidence_id, node_blank.evidence_id)),
                SidecarDiagramNode(node_id="node-noise", label="噪声节点", evidence_ids=(reviewed_figure.evidence_id, node_noise.evidence_id)),
                # keep-first 去重钉：再次声明 node_minted 的证据单元。
                SidecarDiagramNode(node_id="node-duplicate", label="围岩支护重复", evidence_ids=(reviewed_figure.evidence_id, node_minted.evidence_id)),
            ),
            edges=(
                SidecarDiagramEdge(
                    source_node_id="node-minted",
                    target_node_id="node-blank",
                    relation="相邻",
                    evidence_ids=(reviewed_figure.evidence_id,),
                    basis="synthetic layout adjacency",
                    confidence=0.9,
                ),
            ),
            audit=DiagramReviewAudit(
                reviewer="synthetic-reviewer",
                reviewed_at_utc="2026-08-13T00:00:00Z",
                crop_path="crops/synthetic_f1.png",
                crop_sha256=crop_sha,
                basis="synthetic reviewed diagram for casting-rule pins",
            ),
        )
        sidecar = DiagramReviewSidecar(
            sidecar_id="synthetic-anchor-case.reviewed",
            registry_id="synthetic_anchor_case_v1",
            material_profile_id="yijian_gonglu_2026",
            annotation_revision=1,
            entries=(entry,),
        )
        sidecar_path = tmp_path / "synthetic_diagram_review.yaml"
        import yaml

        sidecar_path.write_text(yaml.safe_dump(json.loads(sidecar.model_dump_json()), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {
        "page": page,
        "manifest_path": manifest_path,
        "sidecar": sidecar,
        "sidecar_path": sidecar_path,
        "paragraph": paragraph,
        "node_minted": node_minted,
        "node_blank": node_blank,
        "node_noise": node_noise,
        "node_unbound": node_unbound,
        "other_figure_node": other_figure_node,
    }


def _build(case, *, mint: bool):
    return build_step2a_semantic_projection(
        sample_id=SAMPLE_ID,
        page=case["page"],
        source_manifest_path=case["manifest_path"],
        content_policy=load_content_policy(POLICY_PATH),
        diagram_review_sidecar=case["sidecar"],
        diagram_review_sidecar_path=case["sidecar_path"],
        mint_controlled_diagram_anchor_spans=mint,
    )


class TestCastingRules:
    def test_minting_covers_all_six_shapes_and_dedupes_keep_first(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path)
        build = _build(case, mint=True)
        projection = build.projection
        spans = projection.controlled_diagram_anchor_spans
        # 六形态：只有 reviewed 绑定、kind=diagram_node、政策 INCLUDED、非空文本的
        # node_minted 铸一枚；空白 / 噪声（政策非 INCLUDED）/ 未绑定 / 他图节点不铸；
        # 重复绑定 keep-first 只铸一次。
        assert len(spans) == 1
        span = spans[0]
        assert span.parent_evidence_id == case["node_minted"].evidence_id
        assert span.parent_evidence_kind == EvidenceKind.DIAGRAM_NODE
        assert span.canonical_text == "围岩支护"  # 全文单 span，首尾空白已修剪
        raw = case["node_minted"].canonical_text
        assert raw[span.start : span.end] == span.canonical_text
        assert span.exact_text_sha256 == hashlib.sha256(span.canonical_text.encode("utf-8")).hexdigest()
        assert span.source_manifest_sha256 == projection.source_manifest_sha256
        excluded_parents = {
            case["node_blank"].evidence_id,
            case["node_noise"].evidence_id,
            case["node_unbound"].evidence_id,
            case["other_figure_node"].evidence_id,
        }
        assert not ({item.parent_evidence_id for item in spans} & excluded_parents)
        # 隔离：一般可选池不含节点 span；section 与投影镜像一致。
        assert span.anchor_span_id not in {item.anchor_span_id for item in projection.anchor_spans}
        assert build.section.controlled_diagram_anchor_span_ids == (span.anchor_span_id,)
        assert projection.projection_version == STEP2A_SEMANTIC_PROJECTION_VERSION
        assert "controlled_diagram_anchor_spans" in projection.model_dump(mode="json")

    def test_mint_flag_defaults_off_and_only_adds_the_quarantined_key(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path)
        minted = _build(case, mint=True).projection.model_dump(mode="json")
        plain = _build(case, mint=False).projection.model_dump(mode="json")
        assert "controlled_diagram_anchor_spans" not in plain
        # 版本字面量随载而变：铸了才 v6，未铸保持 v5（冻结人签工件的
        # projection_id 钉——如表续评审侧车——逐字节不动）。
        assert minted["projection_version"] == STEP2A_SEMANTIC_PROJECTION_VERSION
        assert plain["projection_version"] == PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION
        minted_without_lane = {
            key: value
            for key, value in minted.items()
            if key not in {"controlled_diagram_anchor_spans", "projection_id", "projection_version"}
        }
        plain_reduced = {key: value for key, value in plain.items() if key not in {"projection_id", "projection_version"}}
        assert minted_without_lane == plain_reduced

    def test_no_diagram_page_keeps_byte_shape_regardless_of_flag(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path, with_diagram=False)
        minted = _build(case, mint=True).projection.model_dump(mode="json")
        plain = _build(case, mint=False).projection.model_dump(mode="json")
        assert minted == plain
        assert "controlled_diagram_anchor_spans" not in plain
        assert plain["projection_version"] == PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION


class TestVersionGates:
    def test_v5_payload_without_lane_still_parses_with_stable_fingerprint(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path, with_diagram=False)
        payload = _build(case, mint=False).projection.model_dump(mode="json")
        payload["projection_version"] = PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION
        fingerprint = canonical_step2_sha256({key: value for key, value in payload.items() if key != "projection_id"})
        payload["projection_id"] = f"step2a-projection:{fingerprint[:24]}"
        parsed = Step2ASemanticProjection.model_validate(payload)
        assert parsed.fingerprint_sha256 == fingerprint
        assert parsed.model_dump(mode="json") == payload

    def test_pre_v6_payload_carrying_the_lane_is_refused(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path)
        payload = _build(case, mint=True).projection.model_dump(mode="json")
        payload["projection_version"] = PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION
        with pytest.raises(ValueError, match="require the v6 projection contract"):
            Step2ASemanticProjection.model_validate(payload)

    def test_unknown_projection_version_is_refused(self, tmp_path: Path) -> None:
        case = _synthetic_case(tmp_path, with_diagram=False)
        payload = _build(case, mint=False).projection.model_dump(mode="json")
        payload["projection_version"] = "step2a_semantic_projection_v99_unknown"
        with pytest.raises(ValueError, match="incompatible version"):
            Step2ASemanticProjection.model_validate(payload)

    def test_section_refuses_foreign_or_colliding_quarantine(self, tmp_path: Path) -> None:
        from examcard.knowledge.contracts import EvidenceAnchorSpan, make_evidence_anchor_span_id

        case = _synthetic_case(tmp_path)
        build = _build(case, mint=True)
        section_dump = build.section.model_dump(mode="json")
        # 合法铸造但亲本是 paragraph（非 diagram_node）且不在一般池（子区间不同）
        # 的外来 span：kind 守卫必须拒。
        ordinary = build.projection.anchor_spans[0]
        sub_text = ordinary.canonical_text[:2]
        sub_sha = hashlib.sha256(sub_text.encode("utf-8")).hexdigest()
        foreign_span = EvidenceAnchorSpan(
            anchor_span_id=make_evidence_anchor_span_id(
                parent_evidence_id=ordinary.parent_evidence_id,
                start=ordinary.start,
                end=ordinary.start + 2,
                exact_text_sha256=sub_sha,
                content_policy_fingerprint_sha256=ordinary.content_policy_fingerprint_sha256,
                source_manifest_sha256=ordinary.source_manifest_sha256,
            ),
            parent_evidence_id=ordinary.parent_evidence_id,
            parent_evidence_kind=ordinary.parent_evidence_kind,
            start=ordinary.start,
            end=ordinary.start + 2,
            canonical_text=sub_text,
            exact_text_sha256=sub_sha,
            content_policy_id=ordinary.content_policy_id,
            content_policy_revision=ordinary.content_policy_revision,
            content_policy_fingerprint_sha256=ordinary.content_policy_fingerprint_sha256,
            policy_decision=ordinary.policy_decision,
            source_manifest_sha256=ordinary.source_manifest_sha256,
        )
        foreign = dict(section_dump)
        foreign["controlled_diagram_anchor_spans"] = [foreign_span.model_dump(mode="json")]
        with pytest.raises(ValueError, match="reviewed diagram-node Evidence"):
            Step2ASectionInput.model_validate(foreign)
        # 碰撞形态：把一般池 span 原样塞进隔离道 → 去重/相交守卫拒。
        colliding = dict(section_dump)
        colliding["controlled_diagram_anchor_spans"] = [ordinary.model_dump(mode="json")]
        with pytest.raises(ValueError, match="unique and disjoint|reviewed diagram-node Evidence"):
            Step2ASectionInput.model_validate(colliding)


class TestRequestFace:
    def _requests(self, tmp_path: Path):
        case = _synthetic_case(tmp_path)
        minted = _build(case, mint=True)
        plain = _build(case, mint=False)
        make = lambda build: build_step2a_request(  # noqa: E731
            sample_id=SAMPLE_ID,
            material_profile_id="yijian_gonglu_2026",
            projection=build.projection,
            sections=(build.section,),
            model="deterministic-fake-v1",
        )
        return case, minted, plain, make

    def test_v24_pair_pool_isolation_and_prompt_surface(self, tmp_path: Path) -> None:
        from examcard.knowledge.llm import OpenAICompatibleStep2Transport

        case, minted, plain, make = self._requests(tmp_path)
        request = make(minted)
        assert request.identity.request_version == CONTROLLED_DIAGRAM_ANCHOR_STEP2A_REQUEST_VERSION
        assert request.model_input_version == CONTROLLED_DIAGRAM_ANCHOR_STEP2A_MODEL_INPUT_VERSION
        quarantined_ids = {span.anchor_span_id for span in minted.projection.controlled_diagram_anchor_spans}
        spans_by_id, _, _, _ = _step2a_anchor_sources(request)
        assert not (quarantined_ids & set(spans_by_id))
        # T3(d) prompt 渲染面：模型可见载荷不含隔离道 span id；受控图节点的
        # anchor_span_ids 仍由一般池导出（本例节点无一般池 span → 保持为空）。
        model_payload = OpenAICompatibleStep2Transport.model_payload(request)
        rendered = json.dumps(model_payload, ensure_ascii=False)
        assert not any(span_id in rendered for span_id in quarantined_ids)
        for section in model_payload["sections"]:
            for diagram in section.get("controlled_diagrams", ()):
                assert all(node["anchor_span_ids"] == [] for node in diagram["nodes"])
        plain_request = make(plain)
        assert plain_request.identity.request_version == STEP2A_REQUEST_VERSION
        assert "controlled_diagram_anchor_spans" not in json.dumps(
            [section.model_dump(mode="json") for section in plain_request.sections], ensure_ascii=False
        )

    def test_variant_and_multipage_builders_refuse_the_lane(self, tmp_path: Path) -> None:
        case, minted, plain, make = self._requests(tmp_path)
        with pytest.raises(ValueError, match="single-page V24 base contract"):
            build_step2a_request(
                sample_id=SAMPLE_ID,
                material_profile_id="yijian_gonglu_2026",
                projection=minted.projection,
                sections=(minted.section,),
                model="deterministic-fake-v1",
                use_single_role_anchor_contract=True,
            )
        with pytest.raises(ValueError, match="single-page V24 base contract"):
            build_step2a_request(
                sample_id=SAMPLE_ID,
                material_profile_id="yijian_gonglu_2026",
                projection=minted.projection,
                sections=(minted.section,),
                model="deterministic-fake-v1",
                additional_projections=(plain.projection,),
            )

    def test_v25_family_refuses_the_lane(self, tmp_path: Path) -> None:
        from examcard.knowledge.contracts.v25_generic_contracts import V25Step2ARequest, build_v25_step2a_request

        case, minted, plain, make = self._requests(tmp_path)
        policy = load_content_policy(POLICY_PATH)
        base = build_v25_step2a_request(
            sample_id=SAMPLE_ID,
            material_profile_id="yijian_gonglu_2026",
            source_document_sha256=plain.projection.source_document_sha256,
            source_manifest_sha256s=(plain.projection.source_manifest_sha256,),
            projection_sha256s=(plain.projection.fingerprint_sha256,),
            content_policy_id=policy.policy_id,
            content_policy_revision=policy.revision,
            content_policy_fingerprint_sha256=plain.projection.content_policy_fingerprint_sha256,
            sections=(plain.section,),
            model="deterministic-fake-v1",
            relay_base_url="https://offline.invalid/v1",
            manual_reissue_reason="synthetic-quarantine-guard",
        )
        payload = base.model_dump(mode="json")
        payload["sections"] = [minted.section.model_dump(mode="json")]
        with pytest.raises(ValueError, match="V25 request families do not admit"):
            V25Step2ARequest.model_validate(payload)


@corpus
class TestRealCorpusWidening:
    def test_gonglu41_widening_proof(self) -> None:
        case = survey_tool.survey_case("gonglu_41_diagram_mixed", 2, survey_tool.b1d.A_RUN, survey_tool.b1d.A_RUN, 1)
        minted_texts = [item["canonical_text"] for item in case["controlled_diagram_anchor_spans"]]
        # ==4：node-drainage 仅绑 figure 本体铸 0（负断言含在计数里）。
        assert minted_texts == ["围岩", "初期支护", "防水层", "二次衬砌"]
        assert case["ordinary_anchor_span_count"] == 40  # 与 A_RUN 冻结请求同参对拍
        assert case["quarantine_disjoint_from_selectable_pool"] is True
        assert case["anchorable"] and case["anchor_count"] == 4
        resolution = case["resolution"]
        assert all(item["lane"] == "label" and item["source"] == "controlled_diagram" for item in resolution["anchors"])
        assert {item["page"] for item in resolution["anchors"]} == {2}
        # 非受控 figure（鹉桃山随道节点）不得被铸。
        minted_parents = {item["parent_evidence_id"] for item in case["controlled_diagram_anchor_spans"]}
        assert "ev:2f26bf82436a57de5a9a7c70" not in minted_parents

    def test_gonglu01_stays_fail_closed(self) -> None:
        case = survey_tool.survey_case("gonglu_01_text_list", 1, survey_tool.b1d.A_RUN, survey_tool.b1d.A2_RUN, 2)
        assert case["controlled_diagram_anchor_spans"] == []
        assert not case["anchorable"]
        assert case["resolution"]["reason"]

    def test_frozen_run_sections_roundtrip_byte_identical(self) -> None:
        total = 0
        for run_root in (survey_tool.b1d.A_RUN, survey_tool.b1d.A2_RUN):
            for path in sorted((run_root / "step2a" / "requests").glob("*.json")):
                request = (json.loads(path.read_text(encoding="utf-8")).get("request") or {})
                for stored in request.get("sections") or ():
                    parsed = Step2ASectionInput.model_validate(stored)
                    assert parsed.model_dump(mode="json") == stored
                    total += 1
        assert total >= 20
