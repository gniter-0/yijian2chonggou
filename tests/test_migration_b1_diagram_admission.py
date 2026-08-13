"""diagram 族语料补齐（改进2-②，migration/b1_echo_diagram_admission_materials.py）离线测试。

三层覆盖，零网络：
1. 图题定式单元用例（无语料依赖，处处可跑）；
2. 真语料锚判定：gonglu_62（caption+label 五锚）/ gonglu_55（label 单锚）可锚，
   gonglu_41 / gonglu_01 fail-closed——正是 B1-P4 的普查终态钉扎；
3. 「可进正路」类型级证明与残余断点钉扎（真语料 gonglu_62 卡）：
   - nodes-only 降级件走全须全尾封印转换链（AdmissionQueueItem 封印 →
     ExtractionSource + ManualSelectionSpec → extract_reviewed_fixture →
     export_reviewed_typed_evidence_fixture → r810 fixture）全程无拒；
   - 全保真件（带边）被 r84 决策门拒于「unauthorized derivation or noise flag」——
     P4b 取证钉扎：contract_elements 把无旗标的 DiagramEdge 计入 fail-closed 旗标遍历，
     旧仓规范源逐行同款（phase2r84_selected_batch_human_decision_ingestion.py
     L1000-1031、DiagramEdge L258-274），内核卡落地时本用例应当着修订。
   生产道真人环节（B2 签字 + b2_review 取证块 + 显式授权）不在本测试伪造，
   生产执行仍走 b1_echo_typed_fixture.py。

语料（exams/*/data/ 与 input_pdf）为机器本地物（.gitignore /exams/*/data/），
缺席即整层跳过（与 r810 金标回放测试同口径）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "migration" / "b1_echo_diagram_admission_materials.py"
_SPEC = importlib.util.spec_from_file_location("b1_diagram_admission_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)

_CORPUS_READY = (
    (tool.A_RUN / "step2a" / "requests").is_dir()
    and (tool.A2_RUN / "step2b" / "records").is_dir()
    and tool.EVIDENCE_GOLDEN.is_dir()
    and tool.INPUT_PDF_DIR.is_dir()
)
corpus = pytest.mark.skipif(not _CORPUS_READY, reason="machine-local rehearsal corpus not present")


class TestCaptionPattern:
    def test_caption_forms_match(self) -> None:
        for text in ("图12.1-1项目标后总费用构成", "图1 概览", "图３．２节点关系", "图 4-1 结构"):
            assert tool.CAPTION_PATTERN.match(text), text

    def test_non_caption_forms_refused(self) -> None:
        # 「意图/设计图纸/裸『图』字」不得误判为图题（gonglu_01 勘察中的假阳形态）。
        for text in ("（1）熟悉设计文件，领会设计意图。", "设计图纸的设计工程量", "图示卡片说明", "图"):
            assert tool.CAPTION_PATTERN.match(text) is None, text


@corpus
class TestRealCorpusAnchorResolution:
    def test_gonglu62_caption_and_label_anchors(self) -> None:
        case = tool._survey_case("gonglu_62_cost_flow_incident", tool.A_RUN, tool.A2_RUN, 2)
        resolution = case["resolution"]
        anchors = resolution["anchors"]
        assert case["anchorable"] and len(anchors) == 5
        assert anchors[0]["lane"] == "caption"
        assert anchors[0]["span_id"] == "span:313f4a69fdbb3124bbfb94e3"
        assert anchors[0]["text"].startswith("图12.1-1")
        assert all(item["lane"] == "label" and item["matched_labels"] for item in anchors[1:])
        # 三同：同页（受控图示 section 页）+ 同源（bridge manifest）。
        bridge = case["_fact"]["semantic_diagram_bridge"]
        assert resolution["manifest"] == bridge["source_manifest_sha256"]
        assert {item["page"] for item in anchors} == {case["diagram_page"]} == {resolution["page"]}

    def test_gonglu55_single_label_anchor(self) -> None:
        case = tool._survey_case("gonglu_55_standard_constraints", tool.A_RUN, tool.A_RUN, 2)
        anchors = case["resolution"]["anchors"]
        assert case["anchorable"] and len(anchors) == 1
        assert anchors[0]["lane"] == "label"
        assert anchors[0]["span_id"] == "span:82beff6839f22d22621db8fc"
        assert "直接经济损失" in anchors[0]["matched_labels"]

    def test_unanchorable_samples_fail_closed(self) -> None:
        # B1-P4 普查终态：图页无同源文本 span 的卡不得被锚（宁缺毋滥）。
        for sample_id, a_run, b_run, proposal_index in (
            ("gonglu_41_diagram_mixed", tool.A_RUN, tool.A_RUN, 1),
            ("gonglu_01_text_list", tool.A_RUN, tool.A2_RUN, 2),
        ):
            case = tool._survey_case(sample_id, a_run, b_run, proposal_index)
            assert not case["anchorable"], sample_id
            assert case["resolution"]["reason"], sample_id


@corpus
class TestConverterChainAdmissibility:
    @staticmethod
    def _gonglu62_chain_inputs(tmp_path: Path, *, with_edges: bool):
        """真语料 gonglu_62 卡的链条输入组装（queue_item, source, spec, anchor_ids）。"""
        from examcard.gates.review.extraction import (
            ROLE_BY_PRESENTATION,
            make_extraction_source,
            make_manual_selection_spec,
            make_source_page_binding,
        )
        from examcard.gates.review.queue import AdmissionQueueItem
        from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x

        case = tool._survey_case("gonglu_62_cost_flow_incident", tool.A_RUN, tool.A2_RUN, 2)
        resolution = case["resolution"]
        anchor_ids = tuple(anchor["span_id"] for anchor in resolution["anchors"])
        page_num = int(resolution["page"])
        sample_id = case["sample_id"]
        record_id = f"record:b1:{sample_id.replace('_', '-')}"

        pdf_path = tool._sample_pdf(sample_id)
        image_target = tmp_path / f"{sample_id}_p{page_num:04d}.png"
        tool._render_page(pdf_path, page_num, image_target)
        width, height = tool._png_dimensions(image_target)
        page_binding = make_source_page_binding(
            page_ref_id=f"page:b1d:{sample_id.replace('_', '-')}-{page_num:04d}",
            page=page_num,
            source_file=str(pdf_path.relative_to(tool.NEW_ROOT)).replace("\\", "/"),
            image_path=image_target.name,
            image_sha256=tool._file_sha256(image_target),
            image_width=width,
            image_height=height,
            page_sha256=tool._page_evidence_sha256(sample_id, page_num),
            source_manifest_sha256=resolution["manifest"],
        )

        # 队列可入性：工具形状的载荷按 r8x 谱系封印为 AdmissionQueueItem 无拒。
        seed = f"{sample_id}|{case['fact_id']}|diagram"
        queue_item = hash_model_r8x(
            AdmissionQueueItem,
            {
                "candidate_id": f"candidate:b1d:{canonical_hash(seed)[:24]}",
                "queue_item_id": f"queue:b1d:{canonical_hash('queue|' + seed)[:24]}",
                "subject_id": tool.SUBJECT_ID,
                "record_id": record_id,
                "fact_id": case["fact_id"],
                "evidence_id": case["evidence_id"],
                "presentation": "diagram",
                "source_manifest_sha256": resolution["manifest"],
                "anchor_ids": anchor_ids,
                "pages": (page_binding,),
                "disposition": "manual_review",
                "source_decision_id": None,
                "decision_audit_id": f"audit:b1d:pending-{canonical_hash(seed)[:12]}",
                "reason": "offline admissibility proof (diagram corpus completion)",
            },
            "entry_sha256",
        )

        diagram = case["_diagram"]
        node_ids = [node.get("node_id") or "" for node in diagram["nodes"]]
        bindings = tuple(
            {"source_role": ROLE_BY_PRESENTATION["diagram"], "text": (node.get("label") or "").strip()}
            for node in diagram["nodes"]
        )
        edges = tuple(
            (node_ids.index(edge["source_node_id"]) + 1, node_ids.index(edge["target_node_id"]) + 1)
            for edge in diagram["edges"]
        )
        source = make_extraction_source(
            subject_id=tool.SUBJECT_ID,
            record_id=record_id,
            fact_id=case["fact_id"],
            evidence_id=case["evidence_id"],
            presentation="diagram",
            source_manifest_sha256=resolution["manifest"],
            anchor_ids=anchor_ids,
            pages=(page_binding,),
        )
        spec = make_manual_selection_spec(
            {
                "selection_id": f"selection:b1d:{canonical_hash(seed)[:24]}",
                "subject_id": tool.SUBJECT_ID,
                "record_id": record_id,
                "fact_id": case["fact_id"],
                "evidence_id": case["evidence_id"],
                "presentation": "diagram",
                "page_ref_id": page_binding.page_ref_id,
                "bindings": bindings,
                "diagram_edges": edges if with_edges else (),
                "excluded_noise_kinds": ("watermark",),
                "reviewer": "offline-admissibility-test",
                "notes": "type-level chain proof only; production signing stays with the human lane",
                "recorded_at": "2026-08-13T00:00:00Z",
            }
        )
        return queue_item, source, spec, anchor_ids

    def test_nodes_only_draft_passes_the_sealed_chain(self, tmp_path: Path) -> None:
        from examcard.gates.review.extraction import extract_reviewed_fixture
        from examcard.gates.review.typed_evidence_export import (
            ExplicitProductionAuthorization,
            export_reviewed_typed_evidence_fixture,
        )

        queue_item, source, spec, anchor_ids = self._gonglu62_chain_inputs(tmp_path, with_edges=False)
        assert queue_item.presentation == "diagram" and len(queue_item.anchor_ids) == 5
        batch = extract_reviewed_fixture((source,), (spec,), identity_period="b1d")
        queue_items_by_candidate = {
            decision.candidate_id: {item.queue_item_id: item for item in batch.queue_items}[decision.queue_item_id]
            for decision in batch.manifest.decisions
        }
        fixture = export_reviewed_typed_evidence_fixture(
            released=batch.manifest.decisions,
            queue_items_by_candidate=queue_items_by_candidate,
            queue_sha256=batch.queue_sha256,
            authorization=ExplicitProductionAuthorization(
                reviewer="offline-admissibility-test",
                reason="offline diagram-family admissibility proof over the sealed converter chain",
                recorded_at="2026-08-13T00:00:00Z",
                identity_period="b1d",
            ),
        )
        assert fixture.reviewed_candidate_count == 1
        candidate = fixture.candidates[0]
        assert candidate.presentation == "diagram" and candidate.anchor_ids == anchor_ids
        contract = candidate.typed_contract
        assert contract.presentation == "diagram"
        assert len(contract.nodes) == 12 and len(contract.edges) == 0
        assert all(node.provenance.anchor_ids == anchor_ids for node in contract.nodes)
        assert contract.nodes[0].text == "标后预算总费用"

    def test_edged_draft_refused_by_r84_gate_p4b_pinned(self, tmp_path: Path) -> None:
        # P4b 取证钉扎：带边 diagram 被决策门 fail-closed 拒（旧仓规范源同款语义）。
        # 内核卡落地放行带边 diagram 时，本用例应当着修订为通过性证明。
        from examcard.gates.review.extraction import extract_reviewed_fixture

        _queue_item, source, spec, _anchor_ids = self._gonglu62_chain_inputs(tmp_path, with_edges=True)
        with pytest.raises(ValueError, match="unauthorized derivation or noise flag"):
            extract_reviewed_fixture((source,), (spec,), identity_period="b1d")
