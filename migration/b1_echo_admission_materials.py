"""B1 echo 生产道·检查点①物料生成器（驱动外置人工准入道，一次性工具）。

背景（改进 B1，task-7d955232）：用彩排 a/a2 段合规滞留的 draft step2 产物作原料，
把「新考试正路」检查点①走全须全尾人工道。驱动 CLI 仅在 step2b 同 run 时自动触发
准入物料（§7.1）；本工具即驱动外置等价物——从 draft 产物 + 工作区证据组装
AdmissionQueueItem 队列，产出 §7.1 全件（queue.json / workbench.html / progress.json /
inbox/ / INSTRUCTIONS.txt），另附：

- ``pages/``：选中源页的 200dpi 现渲 PNG（工作区证据产物只有页 JSON 与 crops，无整页
  渲染图——人工准入评审需要可视源页，此为新考试接入的真实人工触点之一，计入报告）；
- ``spec-drafts/``：五卡 ManualSelectionSpec 草稿（机械预填元素文本/结构参数，元素
  granularity 待评审人 B2 修订签字——草稿**不含**封印与签字，非权威输入）。

批选定（阶段①收口）：gonglu_62（formula/ordered_list/diagram 三卡，step2a=run a、
step2b=run a2）+ gonglu_24（table/constraint 两卡，均 run a），两章五卡覆盖全五族。

纪律：draft 产物与工作区证据**只读**；仅写入新建 run 根（--run-root）；页渲染 DPI=200
与事实底座 EvidenceEngineConfig 同参；SourcePageBinding 引擎期语义——image_sha256=
渲染文件哈希、宽高=PNG 头、page_sha256=页证据 JSON 文件哈希、source_manifest_sha256=
该章 span 所携清单哈希（跨 span 一致性校验后取值）。

用法（仓根）：
  uv run python migration/b1_echo_admission_materials.py --run-root exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1-admission
"""
from __future__ import annotations

import argparse
import hashlib
import json
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

# (sample_id, step2a_run, step2b_run, [(presentation, step2b proposal index), ...])
# 勘误（阶段②实测）：diagram 卡裁撤——diagram_relation 事实无文本主锚，受控图示的
# 节点证据 id 与 V25 投影的 span 父证据零交集（图示典型证据不产文本锚；r810 生产
# 语料六候选亦仅 table/ordered_list/formula，同一结构性局限）。按任务书「覆盖 ≥3
# 呈现族即可，宁小勿全」取 4 卡 4 族；figure-evidence 锚定语义列为 runbook 坑位。
SELECTION = (
    ("gonglu_62_cost_flow_incident", A_RUN, A2_RUN, (("formula", 0), ("ordered_list", 1))),
    ("gonglu_24_table_formula", A_RUN, A_RUN, (("table", 0), ("constraint", 1))),
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


def _span_index(step2a_request: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """(anchor_span_id → {text, page_num, source_manifest_sha256}, parent_evidence_id → [span_id])。"""
    index: dict[str, dict[str, Any]] = {}
    by_parent: dict[str, list[str]] = {}

    def _add(span: dict[str, Any], page_num: int) -> None:
        span_id = span["anchor_span_id"]
        index[span_id] = {
            "text": span.get("canonical_text") or "",
            "page_num": page_num,
            "source_manifest_sha256": span.get("source_manifest_sha256"),
        }
        parent = span.get("parent_evidence_id")
        if parent and span_id not in by_parent.setdefault(parent, []):
            by_parent[parent].append(span_id)

    for section in step2a_request.get("sections") or ():
        for item in section.get("text_evidence") or ():
            page = int(item.get("page_num") or section.get("page_num") or 1)
            for span in item.get("anchor_spans") or ():
                _add(span, page)
        for table in section.get("tables") or ():
            page = int(table.get("page_num") or section.get("page_num") or 1)
            for row in table.get("rows") or ():
                for cell in row.get("cells") or ():
                    for span in cell.get("anchor_spans") or ():
                        _add(span, page)
    return index, by_parent


def _find_diagram(step2a_request: dict[str, Any], figure_evidence_id: str | None = None) -> dict[str, Any] | None:
    for section in step2a_request.get("sections") or ():
        for diagram in section.get("controlled_diagrams") or ():
            if figure_evidence_id and diagram.get("figure_evidence_id") != figure_evidence_id:
                continue
            return {**diagram, "page_num": int(section.get("page_num") or 1)}
    return None


def _find_table(step2a_request: dict[str, Any], table_evidence_id: str | None = None) -> dict[str, Any] | None:
    """按 table_evidence_id 定位表格；缺省首表（B2 检查点①驳回实证：必须按事实
    source_table_ids 定位，同页多表时首表启发式会绑错表）。"""
    for section in step2a_request.get("sections") or ():
        for table in section.get("tables") or ():
            if table_evidence_id and table.get("table_evidence_id") != table_evidence_id:
                continue
            return {**table, "page_num": int(table.get("page_num") or section.get("page_num") or 1)}
    return None


def _sample_pdf(sample_id: str) -> Path:
    prefix = sample_id.split("_")[1]  # gonglu_62_... → 62
    matches = sorted(INPUT_PDF_DIR.glob(f"{prefix} *.pdf"))
    if len(matches) != 1:
        raise SystemExit(f"cannot resolve input pdf for {sample_id}: {matches}")
    return matches[0]


def _render_page(pdf_path: Path, page_num: int, target: Path) -> None:
    import fitz  # pymupdf；与 EvidenceEngineConfig(render_dpi=200) 同参

    if target.is_file():
        return
    document = fitz.open(pdf_path)
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

    queue_payloads: list[dict[str, Any]] = []
    spec_drafts: list[dict[str, Any]] = []
    audit_notes: list[str] = []

    for sample_id, step2a_run, step2b_run, cards in SELECTION:
        step2a_requests, _ = _load_stage(step2a_run, "step2a")
        step2b_requests, step2b_records = _load_stage(step2b_run, "step2b")
        step2a_request = step2a_requests.get(sample_id)
        step2b_request = step2b_requests.get(sample_id)
        step2b_record = step2b_records.get(sample_id)
        if step2a_request is None or step2b_request is None or step2b_record is None:
            raise SystemExit(f"draft products incomplete for {sample_id}")
        spans, spans_by_parent = _span_index(step2a_request)
        facts = {item["fact_id"]: item for item in step2b_request.get("facts") or ()}
        proposals = (step2b_record.get("response") or {}).get("proposals") or ()
        pdf_path = _sample_pdf(sample_id)
        record_id = f"record:b1:{sample_id.replace('_', '-')}"

        for presentation, proposal_index in cards:
            proposal = proposals[proposal_index]
            if proposal.get("presentation") != presentation:
                raise SystemExit(
                    f"{sample_id} proposal[{proposal_index}] presentation drifted: "
                    f"{proposal.get('presentation')} != {presentation}"
                )
            fact_id = (proposal.get("primary_fact_ids") or (None,))[0]
            fact = facts.get(fact_id)
            if fact is None:
                raise SystemExit(f"{sample_id} card fact missing from step2b request: {fact_id}")
            evidence_id = (fact.get("primary_evidence_ids") or ("",))[0]
            anchor_ids = tuple(fact.get("primary_anchor_span_ids") or ())
            if not anchor_ids and presentation == "diagram":
                # diagram_relation 事实无文本主锚：锚点自受控图示的节点证据映射（引擎期口径）。
                diagram = _find_diagram(step2a_request, evidence_id)
                if diagram is None:
                    raise SystemExit(f"{sample_id} diagram card lacks its controlled diagram: {evidence_id}")
                collected: list[str] = []
                for node in diagram.get("nodes") or ():
                    for node_evidence in node.get("evidence_ids") or ():
                        for span_id in spans_by_parent.get(node_evidence, ()):
                            if span_id not in collected:
                                collected.append(span_id)
                anchor_ids = tuple(collected)
            if not anchor_ids:
                raise SystemExit(f"{sample_id} fact has no primary anchors: {fact_id}")
            known = [spans[anchor] for anchor in anchor_ids if anchor in spans]
            if not known:
                raise SystemExit(f"{sample_id} fact anchors not found in step2a spans: {fact_id}")
            manifest_hashes = {item["source_manifest_sha256"] for item in known}
            if len(manifest_hashes) != 1:
                raise SystemExit(f"{sample_id} fact anchors cross source manifests: {fact_id}")
            source_manifest_sha256 = next(iter(manifest_hashes))
            page_num = known[0]["page_num"]

            image_name = f"{sample_id}_p{page_num:04d}.png"
            image_target = pages_dir / image_name
            _render_page(pdf_path, page_num, image_target)
            width, height = _png_dimensions(image_target)
            page_binding = make_source_page_binding(
                page_ref_id=f"page:b1:{sample_id.replace('_', '-')}-{page_num:04d}",
                page=page_num,
                source_file=str(pdf_path.relative_to(NEW_ROOT)).replace("\\", "/"),
                image_path=f"checkpoints/admission/pages/{image_name}",
                image_sha256=_file_sha256(image_target),
                image_width=width,
                image_height=height,
                page_sha256=_page_evidence_sha256(sample_id, page_num),
                source_manifest_sha256=source_manifest_sha256,
            )

            seed = f"{sample_id}|{fact_id}|{presentation}"
            candidate_id = f"candidate:b1:{canonical_hash(seed)[:24]}"
            queue_item_id = f"queue:b1:{canonical_hash('queue|' + seed)[:24]}"
            queue_payloads.append(
                {
                    "candidate_id": candidate_id,
                    "queue_item_id": queue_item_id,
                    "subject_id": SUBJECT_ID,
                    "record_id": record_id,
                    "fact_id": fact_id,
                    "evidence_id": evidence_id,
                    "presentation": presentation,
                    "source_manifest_sha256": source_manifest_sha256,
                    "anchor_ids": anchor_ids,
                    "pages": (page_binding,),
                    "source_decision_id": None,
                    "decision_audit_id": f"audit:b1:pending-{canonical_hash(seed)[:12]}",
                    "reason": "awaiting checkpoint-1 human admission review (B1 echo lane)",
                }
            )

            # ---- ManualSelectionSpec 草稿（机械预填，B2 修订签字后才封印） ----
            bindings: list[dict[str, Any]] = []
            extra: dict[str, Any] = {}
            role = ROLE_BY_PRESENTATION[presentation]
            if presentation == "table":
                source_table_ids = tuple(fact.get("source_table_ids") or ())
                table = _find_table(step2a_request, source_table_ids[0] if source_table_ids else None)
                if table is None:
                    raise SystemExit(f"{sample_id} table card lacks its source table: {source_table_ids}")
                extra["table_rows"] = int(table["row_count"])
                extra["table_columns"] = int(table["column_count"])
                for row in table.get("rows") or ():
                    for cell in row.get("cells") or ():
                        cell_spans = cell.get("anchor_spans") or ()
                        text = "".join(span.get("canonical_text") or "" for span in cell_spans).strip()
                        bindings.append(
                            {
                                "source_role": role,
                                "text": text or "[空单元格-请评审人补文本或调整坐标]",
                                "row": int(cell.get("row_index", 0)) + 1,
                                "column": int(cell.get("column_index", 0)) + 1,
                                "row_span": int(cell.get("row_span", 1)),
                                "column_span": int(cell.get("col_span", 1)),
                            }
                        )
            elif presentation == "diagram":
                diagram = _find_diagram(step2a_request, evidence_id)
                if diagram is None:
                    raise SystemExit(f"{sample_id} diagram card lacks a controlled diagram")
                node_ids: list[str] = []
                for node in diagram.get("nodes") or ():
                    node_ids.append(node.get("node_id") or "")
                    bindings.append({"source_role": role, "text": (node.get("label") or "").strip()})
                edges = []
                for edge in diagram.get("edges") or ():
                    try:
                        source_index = node_ids.index(edge.get("source_node_id")) + 1
                        target_index = node_ids.index(edge.get("target_node_id")) + 1
                    except ValueError:
                        continue
                    edges.append((source_index, target_index))
                extra["diagram_edges"] = edges
                audit_notes.append(
                    f"{sample_id}/{presentation}: diagram 结构自 controlled_diagram "
                    f"{diagram.get('review_id')}（{len(node_ids)} 节点 {len(edges)} 边），请评审人对照源页确认"
                )
            else:
                for anchor in anchor_ids:
                    info = spans.get(anchor)
                    if info is None:
                        continue
                    binding: dict[str, Any] = {"source_role": role, "text": info["text"].strip()}
                    if presentation == "ordered_list":
                        binding["marker"] = ""  # B2 必填：step3 编译道要求列表 marker 非空
                    bindings.append(binding)
            spec_drafts.append(
                {
                    "_instructions": (
                        "评审人（B2）修订说明：本文件为机械预填草稿，非权威输入。请对照 workbench 与 pages/ 源页："
                        "① 修订 bindings 文本使其与源页逐字一致（噪声剔除）；② ordered_list 必填非空 marker；"
                        "③ 确认结构参数（table_rows/columns、diagram_edges）；④ 你认可后回传本文件并附签字串"
                        "（reviewer 字段值），签字即五确认背书（source_text/anchor/source_page/image_hash/typed_contract）。"
                    ),
                    "sample_id": sample_id,
                    "card_title": proposal.get("title"),
                    "queue_item_id": queue_item_id,
                    "candidate_id": candidate_id,
                    "payload": {
                        "selection_id": f"selection:b1:{canonical_hash(seed)[:24]}",
                        "subject_id": SUBJECT_ID,
                        "record_id": record_id,
                        "fact_id": fact_id,
                        "evidence_id": evidence_id,
                        "presentation": presentation,
                        "page_ref_id": page_binding.page_ref_id,
                        "bindings": bindings,
                        **extra,
                        "excluded_noise_kinds": ("watermark",),
                        "reviewer": "<B2 签字待填>",
                        "notes": "checkpoint-1 admission: explicit human source-page selection (B1 echo lane)",
                        "recorded_at": "<B2 签字时间待填>",
                    },
                }
            )

    # 两步产出：先落 queue.json（拿到 queue_sha256），再以该哈希渲染 workbench.html
    # （render_review_workbench 需要 queue_sha256 关键字，回调形态拿不到）。
    ticket = build_admission_checkpoint(run_root=run_root, queue_items=queue_payloads, workbench_renderer=None)
    queue_doc = _read(run_root / "checkpoints" / "admission" / "queue.json")
    workbench_html = render_review_workbench(
        queue_doc["candidates"],
        queue_sha256=ticket["queue_sha256"],
        title="B1 echo 生产道·检查点① 人工准入评审",
    )
    (run_root / "checkpoints" / "admission" / "workbench.html").write_text(workbench_html, encoding="utf-8")
    drafts_dir.mkdir(parents=True, exist_ok=True)
    for draft in spec_drafts:
        name = f"{draft['sample_id']}_{draft['payload']['presentation']}.json"
        (drafts_dir / name).write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (run_root / "checkpoints" / "admission" / "b1-materials-audit.json").write_text(
        json.dumps(
            {
                "task": "task-7d955232 B1 echo lane / checkpoint-1 materials",
                "queue_sha256": ticket["queue_sha256"],
                "queue_count": ticket["queue_count"],
                "cards": [
                    {
                        "sample_id": draft["sample_id"],
                        "presentation": draft["payload"]["presentation"],
                        "fact_id": draft["payload"]["fact_id"],
                        "bindings": len(draft["payload"]["bindings"]),
                    }
                    for draft in spec_drafts
                ],
                "notes": audit_notes,
                "page_binding_semantics": (
                    "engine-period: image_sha256=render file hash (200dpi, EvidenceEngineConfig parity), "
                    "page_sha256=page evidence json file hash, source_manifest_sha256=span-carried manifest"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"run_root": str(run_root), **{k: v for k, v in ticket.items() if k != 'status'}, "spec_drafts": len(spec_drafts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
