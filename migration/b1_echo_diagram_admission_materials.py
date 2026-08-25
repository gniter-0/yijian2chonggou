"""B1 echo 生产道·diagram 族检查点①物料生成器（改进2-②：解 B1-P4 语料缺口，一次性工具）。

背景（task-57cce254，改进2-②）：B1 实走（task-7d955232）时 diagram 卡因 P4 裁撤——
diagram_relation 事实无文本主锚（primary_anchor_span_ids 恒空），受控图示节点证据 id
与 V25 投影 span 父证据零交集（diagram_node 证据单元不产 anchor_span，只进
controlled_diagrams 结构）。本工具落地 figure-evidence 的**文本主锚语义（同页同源道）**，
把 diagram 族草稿卡备齐到能进 echo 正路（检查点①物料 §7.1 全件），真人环节不在本卡执行。

文本主锚语义（doctrine，勘察实据见 b1-diagram-materials-audit.json 的 survey 块）：

    diagram 卡锚点 = step2a V25 投影中满足「三同」的 anchor_span：
      ① 同源：span.source_manifest_sha256 == fact.semantic_diagram_bridge.source_manifest_sha256；
      ② 同页：span 所在页 == 受控图示所在 section 的 page_num（评审人一页内可同时核锚文本与图形）；
      ③ 语义绑定（二选一）：
         - caption 道：span 文本匹配图题定式（^图<编号>…，如「图12.1-1项目标后总费用构成」）；
         - label 道：span 文本含任一图示节点 label（label 长度 ≥2 防噪）。
    排序：caption 先、label 后，各按投影遍历序；锚集为空即 fail-closed（该卡不可锚，弃卡）。

与 B1 主工具（b1_echo_admission_materials.py）的关系：主工具的 diagram 回退（节点证据→
span 父证据映射）在本语料上恒空，属结构性缺锚；本工具是该缺口的补齐道，不改动主工具
（其产物 20260813-b1-admission 队列哈希已入报告，保持审计可复现）。新对象身份用
``b1d`` 期前缀（candidate:b1d:/queue:b1d:/…），后续转换器链应传 --identity-period b1d。

批选定（勘察终态）：四个 diagram 提案里仅两卡满足同页同源锚：
  - gonglu_62_cost_flow_incident（a2 提案2，「标后预算总费用结构」）：caption 1 + label 4，主力卡；
  - gonglu_55_standard_constraints（a 提案2，「生产安全事故等级」）：label 1（无图题段落），次选卡；
  - gonglu_41_diagram_mixed / gonglu_01_text_list：图页无同源文本 span（label 命中全跨
    manifest），维持不可锚——需上游 V25 投影给图页补 caption/邻近文本 span 后方可准入。

残余断点 P4b（本卡勘察新发现，报告 §4）：r84 决策门 validate_typed_contract_binding
对**带边** diagram 契约结构性拒绝——contract_elements 把 edges 计入元素遍历，而
DiagramEdge（layout_only 件）天生无 human_confirmed/ocr_derived 等旗标，getattr 缺省
True 即 fail-closed 拒。旧仓规范源 phase2r84_selected_batch_human_decision_ingestion.py
L1000-1031/L258-274 逐行同款（蒸馏保真，非新仓走样）；内核写面修复需正式派卡。故本工具
对每卡产出**两份**spec 草稿：全保真件（带边，待内核卡）与 nodes-only 降级件（零边，
现行内核下离线已证可通过转换链到 r810 fixture——见 tests/test_migration_b1_diagram_admission.py）。

纪律：draft 产物与工作区证据**只读**；仅写入新建 run 根（--run-root）；页渲染 DPI=200
与事实底座同参；SourcePageBinding 语义与 B1 主工具一致；不实际执行真人评审/live。

用法（仓根）：
  uv run python migration/b1_echo_diagram_admission_materials.py --run-root exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1-diagram-admission
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

EXAM_ROOT = NEW_ROOT / "exams" / "gonglu_golden5_rehearsal"
RUNS = EXAM_ROOT / "data" / "live_runs"
A_RUN = RUNS / "20260812-rehearsal-a-x7qpwvii"
A2_RUN = RUNS / "20260812-rehearsal-a2-ryw5ujtn"
EVIDENCE_GOLDEN = EXAM_ROOT / "data" / "evidence_v2" / "golden" / "gonglu_golden5_rehearsal_phase0_v1"
INPUT_PDF_DIR = EXAM_ROOT / "input_pdf"
RENDER_DPI = 200
SUBJECT_ID = "gonglu"
IDENTITY_MARK = "b1d"  # diagram 补齐道期前缀；转换器链须 --identity-period b1d

# 图题定式：全/半角数字与常见分节符（12.1-1 / １２．１－１ 等）。
CAPTION_PATTERN = re.compile(r"^图\s*[0-9０-９]+(?:[.．\-－、][0-9０-９]+)*")
MIN_LABEL_LENGTH = 2

# (sample_id, step2a_run, step2b_run, diagram proposal index)
SELECTION = (
    ("gonglu_62_cost_flow_incident", A_RUN, A2_RUN, 2),
    ("gonglu_55_standard_constraints", A_RUN, A_RUN, 2),
)
# 勘察对照组（不可锚，入 survey 存证）：
SURVEY_ONLY = (
    ("gonglu_41_diagram_mixed", A_RUN, A_RUN, 1),
    ("gonglu_01_text_list", A_RUN, A2_RUN, 2),
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_stage(run_root: Path, stage: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """returns (request_by_sample, record_by_sample)，只取 state==success 的记录。"""
    requests_by_id = {}
    for path in (run_root / stage / "requests").glob("*.json"):
        payload = _read(path)
        request = payload.get("request") or {}
        requests_by_id[(request.get("identity") or {}).get("request_id")] = request
    request_by_sample: dict[str, Any] = {}
    record_by_sample: dict[str, Any] = {}
    for path in (run_root / stage / "records").glob("*.json"):
        record = _read(path)
        if record.get("state") != "success":
            continue
        request = requests_by_id.get(record.get("request_id"))
        if request is None:
            continue
        sample = request.get("sample_id")
        request_by_sample[sample] = request
        record_by_sample[sample] = record
    return request_by_sample, record_by_sample


def _iter_spans(step2a_request: dict[str, Any]):
    """按 V25 投影遍历序产出 (span, page_num)——排序即锚序，须保持确定性。"""
    for section in step2a_request.get("sections") or ():
        for item in section.get("text_evidence") or ():
            page = int(item.get("page_num") or section.get("page_num") or 1)
            for span in item.get("anchor_spans") or ():
                yield span, page
        for table in section.get("tables") or ():
            page = int(table.get("page_num") or section.get("page_num") or 1)
            for row in table.get("rows") or ():
                for cell in row.get("cells") or ():
                    for span in cell.get("anchor_spans") or ():
                        yield span, page


def _find_diagram(step2a_request: dict[str, Any], figure_evidence_id: str | None = None) -> dict[str, Any] | None:
    for section in step2a_request.get("sections") or ():
        for diagram in section.get("controlled_diagrams") or ():
            if figure_evidence_id and diagram.get("figure_evidence_id") != figure_evidence_id:
                continue
            return {**diagram, "page_num": int(section.get("page_num") or 1)}
    return None


def resolve_diagram_text_anchors(
    step2a_request: dict[str, Any], fact: dict[str, Any], diagram: dict[str, Any]
) -> dict[str, Any]:
    """figure-evidence 文本主锚判定（同页同源道）。

    返回 {"anchors": [{span_id, lane, text, page, matched_labels}], "manifest": …,
    "page": …, "reason": …}；anchors 可为空（caller fail-closed），reason 记不可锚归因。
    """
    bridge = fact.get("semantic_diagram_bridge") or {}
    manifest = bridge.get("source_manifest_sha256")
    if not manifest:
        return {"anchors": [], "manifest": None, "page": None, "reason": "fact lacks semantic_diagram_bridge.source_manifest_sha256"}
    figure_evidence_id = (fact.get("primary_evidence_ids") or (None,))[0]
    if bridge.get("figure_evidence_id") != figure_evidence_id:
        return {"anchors": [], "manifest": manifest, "page": None, "reason": "bridge figure_evidence_id drifted from fact primary evidence"}
    page = int(diagram["page_num"])
    labels = [
        (node.get("label") or "").strip()
        for node in diagram.get("nodes") or ()
        if len((node.get("label") or "").strip()) >= MIN_LABEL_LENGTH
    ]
    captions: list[dict[str, Any]] = []
    label_hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for span, span_page in _iter_spans(step2a_request):
        if span.get("source_manifest_sha256") != manifest or span_page != page:
            continue
        span_id = span.get("anchor_span_id")
        if not span_id or span_id in seen:
            continue
        text = (span.get("canonical_text") or "").strip()
        if not text:
            continue
        if CAPTION_PATTERN.match(text):
            seen.add(span_id)
            captions.append({"span_id": span_id, "lane": "caption", "text": text, "page": span_page, "matched_labels": []})
            continue
        matched = [label for label in labels if label in text]
        if matched:
            seen.add(span_id)
            label_hits.append({"span_id": span_id, "lane": "label", "text": text, "page": span_page, "matched_labels": matched})
    anchors = captions + label_hits
    reason = None
    if not anchors:
        reason = "no same-page same-manifest caption/label span in the V25 projection"
    return {"anchors": anchors, "manifest": manifest, "page": page, "reason": reason}


def _survey_case(sample_id: str, step2a_run: Path, step2b_run: Path, proposal_index: int) -> dict[str, Any]:
    """单卡勘察：定位 diagram 提案并跑锚判定（只读，供 audit survey 块与选批断言）。"""
    step2a_requests, _ = _load_stage(step2a_run, "step2a")
    step2b_requests, step2b_records = _load_stage(step2b_run, "step2b")
    step2a_request = step2a_requests.get(sample_id)
    step2b_request = step2b_requests.get(sample_id)
    step2b_record = step2b_records.get(sample_id)
    if step2a_request is None or step2b_request is None or step2b_record is None:
        raise SystemExit(f"draft products incomplete for {sample_id}")
    proposal = ((step2b_record.get("response") or {}).get("proposals") or ())[proposal_index]
    if proposal.get("presentation") != "diagram":
        raise SystemExit(
            f"{sample_id} proposal[{proposal_index}] presentation drifted: {proposal.get('presentation')} != diagram"
        )
    fact_id = (proposal.get("primary_fact_ids") or (None,))[0]
    facts = {item["fact_id"]: item for item in step2b_request.get("facts") or ()}
    fact = facts.get(fact_id)
    if fact is None:
        raise SystemExit(f"{sample_id} diagram fact missing from step2b request: {fact_id}")
    evidence_id = (fact.get("primary_evidence_ids") or ("",))[0]
    diagram = _find_diagram(step2a_request, evidence_id)
    if diagram is None:
        raise SystemExit(f"{sample_id} diagram card lacks its controlled diagram: {evidence_id}")
    resolution = resolve_diagram_text_anchors(step2a_request, fact, diagram)
    return {
        "sample_id": sample_id,
        "proposal_index": proposal_index,
        "proposal_title": proposal.get("title"),
        "fact_id": fact_id,
        "evidence_id": evidence_id,
        "diagram_review_id": diagram.get("review_id"),
        "diagram_page": diagram.get("page_num"),
        "node_count": len(diagram.get("nodes") or ()),
        "edge_count": len(diagram.get("edges") or ()),
        "anchorable": bool(resolution["anchors"]),
        "anchor_count": len(resolution["anchors"]),
        "resolution": resolution,
        "_step2a_request": step2a_request,
        "_fact": fact,
        "_diagram": diagram,
        "_proposal": proposal,
    }


def _sample_pdf(sample_id: str) -> Path:
    prefix = sample_id.split("_")[1]  # gonglu_62_... → 62
    matches = sorted(INPUT_PDF_DIR.glob(f"{prefix} *.pdf"))
    if len(matches) != 1:
        raise SystemExit(f"cannot resolve input pdf for {sample_id}: {matches}")
    return matches[0]


def _render_page(pdf_path: Path, page_num: int, target: Path) -> None:
    import pymupdf  # 与 EvidenceEngineConfig(render_dpi=200) 同参

    if target.is_file():
        return
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_num - 1]
        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        target.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(target))
    finally:
        document.close()


def _png_dimensions(path: Path) -> tuple[int, int]:
    import struct

    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"rendered page is not PNG: {path}")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page_evidence_sha256(sample_id: str, page_num: int) -> str:
    page_json = EVIDENCE_GOLDEN / sample_id / "pages" / f"page_{page_num:04d}.json"
    if not page_json.is_file():
        raise SystemExit(f"page evidence json missing: {page_json}")
    return _file_sha256(page_json)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    run_root: Path = args.run_root
    if run_root.exists() and any(run_root.iterdir()):
        raise SystemExit(f"run root must be new or empty: {run_root}")
    pages_dir = run_root / "checkpoints" / "admission" / "pages"
    drafts_dir = run_root / "checkpoints" / "admission" / "spec-drafts"

    from examcard.gates.review.extraction import ROLE_BY_PRESENTATION, make_source_page_binding
    from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash
    from examcard.pipeline.live_run import build_admission_checkpoint
    from examcard.webui.review_workbench import render_review_workbench

    survey: list[dict[str, Any]] = []
    selected_cases: list[dict[str, Any]] = []
    for sample_id, step2a_run, step2b_run, proposal_index in SELECTION + SURVEY_ONLY:
        case = _survey_case(sample_id, step2a_run, step2b_run, proposal_index)
        survey.append({k: v for k, v in case.items() if not k.startswith("_")})
        if (sample_id, step2a_run, step2b_run, proposal_index) in SELECTION:
            if not case["anchorable"]:
                raise SystemExit(f"selected diagram card lost its text anchors: {sample_id}")
            selected_cases.append(case)
    for sample_id, *_rest in SURVEY_ONLY:
        entry = next(item for item in survey if item["sample_id"] == sample_id)
        if entry["anchorable"]:
            raise SystemExit(
                f"survey-only sample became anchorable, selection needs review: {sample_id}"
            )

    queue_payloads: list[dict[str, Any]] = []
    spec_drafts: list[dict[str, Any]] = []
    audit_cards: list[dict[str, Any]] = []

    for case in selected_cases:
        sample_id = case["sample_id"]
        diagram = case["_diagram"]
        proposal = case["_proposal"]
        resolution = case["resolution"]
        fact_id = case["fact_id"]
        evidence_id = case["evidence_id"]
        anchor_ids = tuple(anchor["span_id"] for anchor in resolution["anchors"])
        source_manifest_sha256 = resolution["manifest"]
        page_num = int(resolution["page"])
        record_id = f"record:b1:{sample_id.replace('_', '-')}"
        pdf_path = _sample_pdf(sample_id)

        image_name = f"{sample_id}_p{page_num:04d}.png"
        image_target = pages_dir / image_name
        _render_page(pdf_path, page_num, image_target)
        width, height = _png_dimensions(image_target)
        page_binding = make_source_page_binding(
            page_ref_id=f"page:{IDENTITY_MARK}:{sample_id.replace('_', '-')}-{page_num:04d}",
            page=page_num,
            source_file=str(pdf_path.relative_to(NEW_ROOT)).replace("\\", "/"),
            image_path=f"checkpoints/admission/pages/{image_name}",
            image_sha256=_file_sha256(image_target),
            image_width=width,
            image_height=height,
            page_sha256=_page_evidence_sha256(sample_id, page_num),
            source_manifest_sha256=source_manifest_sha256,
        )

        seed = f"{sample_id}|{fact_id}|diagram"
        candidate_id = f"candidate:{IDENTITY_MARK}:{canonical_hash(seed)[:24]}"
        queue_item_id = f"queue:{IDENTITY_MARK}:{canonical_hash('queue|' + seed)[:24]}"
        queue_payloads.append(
            {
                "candidate_id": candidate_id,
                "queue_item_id": queue_item_id,
                "subject_id": SUBJECT_ID,
                "record_id": record_id,
                "fact_id": fact_id,
                "evidence_id": evidence_id,
                "presentation": "diagram",
                "source_manifest_sha256": source_manifest_sha256,
                "anchor_ids": anchor_ids,
                "pages": (page_binding,),
                "source_decision_id": None,
                "decision_audit_id": f"audit:{IDENTITY_MARK}:pending-{canonical_hash(seed)[:12]}",
                "reason": "awaiting checkpoint-1 human admission review (B1 echo lane, diagram corpus completion)",
            }
        )

        # ---- ManualSelectionSpec 草稿（机械预填，评审人修订签字后才封印） ----
        node_ids: list[str] = []
        bindings: list[dict[str, Any]] = []
        role = ROLE_BY_PRESENTATION["diagram"]
        for node in diagram.get("nodes") or ():
            label = (node.get("label") or "").strip()
            if not label:
                raise SystemExit(f"{sample_id} controlled diagram carries an empty node label: {node.get('node_id')}")
            node_ids.append(node.get("node_id") or "")
            bindings.append({"source_role": role, "text": label})
        edges: list[tuple[int, int]] = []
        for edge in diagram.get("edges") or ():
            try:
                source_index = node_ids.index(edge.get("source_node_id")) + 1
                target_index = node_ids.index(edge.get("target_node_id")) + 1
            except ValueError:
                continue
            edges.append((source_index, target_index))
        base_instructions = (
            "评审人修订说明：本文件为机械预填草稿，非权威输入。diagram 卡锚定语义=同页同源文本主锚"
            "（caption/label 道，见 b1-diagram-materials-audit.json 的 anchors 块）。请对照 workbench 与 pages/ 源页："
            "① 核对锚点 span 文本在源页逐字存在（caption 为图题行、label 为含节点名的正文行）；"
            "② 对照源页图形逐一确认 bindings 节点文本与 diagram_edges 连线方向（噪声剔除）；"
            "③ 你认可后回传本文件并附签字串（reviewer 字段值）与 b2_review 取证块，"
            "签字即五确认背书（source_text/anchor/source_page/image_hash/typed_contract）。"
        )
        base_payload = {
            "selection_id": f"selection:{IDENTITY_MARK}:{canonical_hash(seed)[:24]}",
            "subject_id": SUBJECT_ID,
            "record_id": record_id,
            "fact_id": fact_id,
            "evidence_id": evidence_id,
            "presentation": "diagram",
            "page_ref_id": page_binding.page_ref_id,
            "bindings": bindings,
            "diagram_edges": [list(edge) for edge in edges],
            "excluded_noise_kinds": ("watermark",),
            "reviewer": "<评审签字待填>",
            "notes": "checkpoint-1 admission: explicit human source-page selection (B1 echo lane, diagram corpus completion)",
            "recorded_at": "<评审签字时间待填>",
        }
        spec_drafts.append(
            {
                "_instructions": (
                    base_instructions
                    + "【放行口径】本件为全保真草稿（带边）：受 P4b 内核边门（r84 决策门拒带边 diagram）"
                    "限制，签字后须待内核卡落地方可过转换链；如需现行内核下立即放行，改用同名 .nodes-only 件。"
                ),
                "variant": "full-fidelity",
                "sample_id": sample_id,
                "card_title": proposal.get("title"),
                "queue_item_id": queue_item_id,
                "candidate_id": candidate_id,
                "anchor_semantics": {
                    "doctrine": "same-page same-manifest text main anchors (caption/label lanes)",
                    "anchors": resolution["anchors"],
                },
                "payload": base_payload,
            }
        )
        spec_drafts.append(
            {
                "_instructions": (
                    base_instructions
                    + "【放行口径】本件为 nodes-only 降级草稿（diagram_edges 置空）：现行 r84 内核下可过"
                    "转换链（离线已证），代价是丢失连线结构；边语义仍留存于事实 semantic_diagram_bridge，"
                    "待 P4b 内核卡后可用全保真件重走。"
                ),
                "variant": "nodes-only",
                "sample_id": sample_id,
                "card_title": proposal.get("title"),
                "queue_item_id": queue_item_id,
                "candidate_id": candidate_id,
                "anchor_semantics": {
                    "doctrine": "same-page same-manifest text main anchors (caption/label lanes)",
                    "anchors": resolution["anchors"],
                },
                "payload": {**base_payload, "diagram_edges": []},
            }
        )
        audit_cards.append(
            {
                "sample_id": sample_id,
                "presentation": "diagram",
                "fact_id": fact_id,
                "evidence_id": evidence_id,
                "diagram_review_id": case["diagram_review_id"],
                "page": page_num,
                "bindings": len(bindings),
                "edges": len(edges),
                "spec_draft_variants": ("full-fidelity", "nodes-only"),
                "anchors": resolution["anchors"],
                "bridge_source_manifest_sha256": source_manifest_sha256,
            }
        )

    # 两步产出：先落 queue.json（拿到 queue_sha256），再以该哈希渲染 workbench.html。
    ticket = build_admission_checkpoint(run_root=run_root, queue_items=queue_payloads, workbench_renderer=None)
    queue_doc = _read(run_root / "checkpoints" / "admission" / "queue.json")
    workbench_html = render_review_workbench(
        queue_doc["candidates"],
        queue_sha256=ticket["queue_sha256"],
        title="B1 echo 生产道·diagram 族检查点① 人工准入评审（改进2-②）",
    )
    (run_root / "checkpoints" / "admission" / "workbench.html").write_text(workbench_html, encoding="utf-8")
    drafts_dir.mkdir(parents=True, exist_ok=True)
    for draft in spec_drafts:
        suffix = "" if draft["variant"] == "full-fidelity" else ".nodes-only"
        name = f"{draft['sample_id']}_diagram{suffix}.json"
        (drafts_dir / name).write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "checkpoints" / "admission" / "b1-diagram-materials-audit.json").write_text(
        json.dumps(
            {
                "task": "task-57cce254 improvement 2-(2) / diagram corpus completion (B1-P4)",
                "identity_period": IDENTITY_MARK,
                "doctrine": (
                    "diagram text main anchors = V25-projected anchor spans that are (1) same "
                    "source manifest as fact.semantic_diagram_bridge, (2) same page as the "
                    "controlled diagram's section, (3) caption-pattern text or node-label-"
                    "mentioning text; captions first, projection traversal order, fail-closed on empty"
                ),
                "queue_sha256": ticket["queue_sha256"],
                "queue_count": ticket["queue_count"],
                "cards": audit_cards,
                "survey": survey,
                "notes": [
                    "gonglu_62: caption span + 4 label spans, all page 1, manifest == bridge manifest (flagship card)",
                    "gonglu_55: single label span on the figure page (no caption paragraph on page 5); weaker anchor set, reviewer may reject per 宁缺毋滥",
                    "gonglu_41/gonglu_01: label mentions exist only under other manifests/pages; kept unanchorable pending an upstream V25 projection extension (figure-page caption spans)",
                    "r84 DiagramEdge carries no relation label: bridge edge relations stay in the fact bridge, so the step3 v2 relation-leak gate is structurally unaffected",
                    "P4b (residual dead-end, forensic): the r84 decision gate refuses EDGED diagram contracts - contract_elements iterates edges yet DiagramEdge carries no derivation/noise flags, so the fail-closed getattr(default=True) chain rejects; verbatim in the legacy authority (yijian2 scripts/knowledge_v2/phase2r84_selected_batch_human_decision_ingestion.py L1000-1031 w/ flagless DiagramEdge L258-274); kernel-surface fix needs its own card; per-card nodes-only spec drafts pass the current chain (offline-proven)",
                ],
                "page_binding_semantics": (
                    "engine-period: image_sha256=render file hash (200dpi, EvidenceEngineConfig parity), "
                    "page_sha256=page evidence json file hash, source_manifest_sha256=bridge-carried manifest"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "run_root": str(run_root),
                **{k: v for k, v in ticket.items() if k != "status"},
                "spec_drafts": len(spec_drafts),
                "survey_anchorable": [item["sample_id"] for item in survey if item["anchorable"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
