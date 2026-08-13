"""B1 echo 生产道·diagram 族锚判定 v2 勘察器（V25 投影扩锚卡的一次性证明工具，b1e 期）。

背景（task-2d412bfc，扩锚卡；立项预研 task-842fc3a7）：gonglu_41/01 形态的图页
「同页同源 span 充足、语义绑定断供」——投影只给 direct 文本族铸 span，受控图示的
diagram_node 证据单元从不产锚，caption 道则因 census 无题注单元而无米下锅。本卡
在投影内核落地「受控图示节点标签隔离 span 道」（controlled_diagram_anchor_spans，
缺省不铸、显式 opt-in，永不进一般可选池），本工具据此离线重建目标页投影并给出
三同判定的可锚证明 / 宁缺毋滥卫兵证词。

与 b1d 工具（b1_echo_diagram_admission_materials.py）的关系：b1d 是检查点①物料
生成器且其产物哈希已入 ② 报告，**零改动**；本工具复用其 doctrine 常量与判定逻辑
（CAPTION_PATTERN / MIN_LABEL_LENGTH / _load_stage / _find_diagram），只把锚源池
从「text/cell spans」扩为「text/cell spans ∪ controlled_diagram_anchor_spans」。

输入（全冻结、只读；与 A_RUN=20260812-rehearsal-a-x7qpwvii 的 r14 装配道同源）：
  - 证据页：data/evidence_v2/golden/gonglu_phase0_v1/<sample>/pages/page_%04d.json
  - 内容策略：configs/v2/content_policies/core_only_v1.yaml
  - 语义侧车：configs/v2/diagram_review_sidecars/gonglu_phase2r12_v25_semantic_reviewed.yaml
  - 处置侧车：configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25e1_audited.yaml
  - 事实桥：A_RUN step2b 冻结 request/record（semantic_diagram_bridge）

输出：--run-root 下 anchor-widening-audit.json（输入指纹、逐案铸造表、三同判定、
隔离证明、卫兵证词）。纪律：语料只读、零 live 零外呼、只写自属 run 根
（.gitignore /exams/*/data/ 覆盖）。

用法（仓根）：
  uv run python migration/b1_echo_diagram_admission_survey_v2.py \
      --run-root exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1e-diagram-anchor-widening
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

_B1D_PATH = NEW_ROOT / "migration" / "b1_echo_diagram_admission_materials.py"
_B1D_SPEC = importlib.util.spec_from_file_location("b1_diagram_admission_doctrine", _B1D_PATH)
assert _B1D_SPEC is not None and _B1D_SPEC.loader is not None
b1d = importlib.util.module_from_spec(_B1D_SPEC)
_B1D_SPEC.loader.exec_module(b1d)

EVIDENCE_ROOT = NEW_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"
POLICY_PATH = NEW_ROOT / "configs/v2/content_policies/core_only_v1.yaml"
SEMANTIC_SIDECAR_PATH = NEW_ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase2r12_v25_semantic_reviewed.yaml"
DISPOSITION_SIDECAR_PATH = NEW_ROOT / "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25e1_audited.yaml"
DEFAULT_RUN_ROOT = NEW_ROOT / "exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1e-diagram-anchor-widening"

# (sample_id, diagram page, step2a_run, step2b_run, proposal index)：
# gonglu_41 = 可锚证明目标；gonglu_01 = 宁缺毋滥卫兵（侧车节点无 census 绑定，铸 0）。
CASES = (
    ("gonglu_41_diagram_mixed", 2, b1d.A_RUN, b1d.A_RUN, 1),
    ("gonglu_01_text_list", 1, b1d.A_RUN, b1d.A2_RUN, 2),
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_ready() -> bool:
    return (
        EVIDENCE_ROOT.is_dir()
        and POLICY_PATH.is_file()
        and SEMANTIC_SIDECAR_PATH.is_file()
        and DISPOSITION_SIDECAR_PATH.is_file()
        and (b1d.A_RUN / "step2b" / "records").is_dir()
        and (b1d.A2_RUN / "step2b" / "records").is_dir()
    )


def rebuild_diagram_page(sample_id: str, page_num: int):
    """以 v6 投影内核（mint opt-in）离线重建目标页；返回 SemanticProjectionBuild。

    表修复侧车沿用 r14 装配道的同款映射（phase2r13 _TABLE_REPAIR_PATHS），
    保证一般 span 池与 A_RUN 冻结请求同参可对拍。
    """
    from examcard.evidence.schema import PageEvidence
    from examcard.knowledge.contracts.phase2r13_v25_reconciliation import _TABLE_REPAIR_PATHS
    from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar
    from examcard.knowledge.diagram.disposition import load_diagram_disposition_sidecar
    from examcard.knowledge.diagram.sidecar import load_diagram_review_sidecar
    from examcard.knowledge.projection import build_step2a_semantic_projection
    from examcard.materials.catalog import load_content_policy

    manifest_path = EVIDENCE_ROOT / sample_id / "pages" / f"page_{page_num:04d}.json"
    if not manifest_path.is_file():
        raise SystemExit(f"frozen evidence page is missing: {manifest_path}")
    repair_kwargs = {}
    repair_relative = _TABLE_REPAIR_PATHS.get((sample_id, page_num))
    if repair_relative is not None:
        repair_path = NEW_ROOT / repair_relative
        repair_kwargs = {
            "table_repair_sidecar": load_table_repair_sidecar(repair_path),
            "table_repair_sidecar_path": repair_path,
        }
    return build_step2a_semantic_projection(
        sample_id=sample_id,
        page=PageEvidence.model_validate_json(manifest_path.read_text(encoding="utf-8")),
        source_manifest_path=manifest_path,
        source_manifest_reference=manifest_path.relative_to(NEW_ROOT).as_posix(),
        content_policy=load_content_policy(POLICY_PATH),
        diagram_review_sidecar=load_diagram_review_sidecar(SEMANTIC_SIDECAR_PATH),
        diagram_review_sidecar_path=SEMANTIC_SIDECAR_PATH,
        diagram_disposition_sidecar=load_diagram_disposition_sidecar(DISPOSITION_SIDECAR_PATH),
        repository_root=NEW_ROOT,
        mint_controlled_diagram_anchor_spans=True,
        **repair_kwargs,
    )


def resolve_widened_anchors(section_dump: dict[str, Any], fact: dict[str, Any], diagram: dict[str, Any]) -> dict[str, Any]:
    """三同判定 v2：锚源池 = 一般 text/cell spans ∪ controlled_diagram_anchor_spans。

    复用 b1d 的 doctrine 常量与遍历序（caption 先、label 后；一般池先、隔离道后），
    空集 fail-closed 语义不变。
    """
    bridge = fact.get("semantic_diagram_bridge") or {}
    manifest = bridge.get("source_manifest_sha256")
    if not manifest:
        return {"anchors": [], "manifest": None, "page": None, "reason": "fact lacks semantic_diagram_bridge.source_manifest_sha256"}
    page = int(diagram["page_num"])
    labels = [
        (node.get("label") or "").strip()
        for node in diagram.get("nodes") or ()
        if len((node.get("label") or "").strip()) >= b1d.MIN_LABEL_LENGTH
    ]
    request_shape = {"sections": [section_dump]}
    span_stream = list(b1d._iter_spans(request_shape))
    span_stream.extend(
        (span, int(section_dump.get("page_num") or page))
        for span in section_dump.get("controlled_diagram_anchor_spans") or ()
    )
    captions: list[dict[str, Any]] = []
    label_hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for span, span_page in span_stream:
        if span.get("source_manifest_sha256") != manifest or span_page != page:
            continue
        span_id = span.get("anchor_span_id")
        if not span_id or span_id in seen:
            continue
        text = (span.get("canonical_text") or "").strip()
        if not text:
            continue
        lane_source = "controlled_diagram" if span.get("parent_evidence_kind") == "diagram_node" else "projection"
        if b1d.CAPTION_PATTERN.match(text):
            seen.add(span_id)
            captions.append({"span_id": span_id, "lane": "caption", "source": lane_source, "text": text, "page": span_page, "matched_labels": []})
            continue
        matched = [label for label in labels if label in text]
        if matched:
            seen.add(span_id)
            label_hits.append({"span_id": span_id, "lane": "label", "source": lane_source, "text": text, "page": span_page, "matched_labels": matched})
    anchors = captions + label_hits
    reason = None if anchors else "no same-page same-manifest caption/label span in the V25 projection (widened pool included)"
    return {"anchors": anchors, "manifest": manifest, "page": page, "reason": reason}


def survey_case(sample_id: str, page_num: int, _step2a_run: Path, step2b_run: Path, proposal_index: int) -> dict[str, Any]:
    """单案勘察：重建投影（mint 开）→ 冻结事实桥 → 三同判定 v2 → 隔离证明。"""
    step2b_requests_by_sample, step2b_records_by_sample = b1d._load_stage(step2b_run, "step2b")
    step2b_request = step2b_requests_by_sample.get(sample_id)
    step2b_record = step2b_records_by_sample.get(sample_id)
    if step2b_request is None or step2b_record is None:
        raise SystemExit(f"frozen step2b artifacts incomplete for {sample_id} under {step2b_run}")
    proposal = ((step2b_record.get("response") or {}).get("proposals") or ())[proposal_index]
    if proposal.get("presentation") != "diagram":
        raise SystemExit(f"{sample_id} proposal[{proposal_index}] presentation drifted: {proposal.get('presentation')}")
    fact_id = (proposal.get("primary_fact_ids") or (None,))[0]
    facts = {item["fact_id"]: item for item in step2b_request.get("facts") or ()}
    fact = facts.get(fact_id)
    if fact is None:
        raise SystemExit(f"{sample_id} diagram fact missing from frozen step2b request: {fact_id}")
    figure_evidence_id = (fact.get("primary_evidence_ids") or ("",))[0]

    build = rebuild_diagram_page(sample_id, page_num)
    projection, section = build.projection, build.section
    bridge_manifest = (fact.get("semantic_diagram_bridge") or {}).get("source_manifest_sha256")
    if projection.source_manifest_sha256 != bridge_manifest:
        raise SystemExit(
            f"{sample_id} rebuilt page manifest {projection.source_manifest_sha256[:16]} does not equal "
            f"the frozen bridge manifest {str(bridge_manifest)[:16]}: mirror drift, fail-closed"
        )
    section_dump = section.model_dump(mode="json")
    section_dump.setdefault("page_num", projection.page_num)
    diagram = b1d._find_diagram({"sections": [section_dump]}, figure_evidence_id)
    if diagram is None:
        raise SystemExit(f"{sample_id} rebuilt section lacks the bridge controlled diagram: {figure_evidence_id}")

    resolution = resolve_widened_anchors(section_dump, fact, diagram)
    minted = [
        {
            "anchor_span_id": span.anchor_span_id,
            "parent_evidence_id": span.parent_evidence_id,
            "parent_evidence_kind": str(span.parent_evidence_kind.value if hasattr(span.parent_evidence_kind, "value") else span.parent_evidence_kind),
            "canonical_text": span.canonical_text,
        }
        for span in projection.controlled_diagram_anchor_spans
    ]
    ordinary_ids = {span.anchor_span_id for span in projection.anchor_spans}
    minted_ids = {item["anchor_span_id"] for item in minted}
    return {
        "sample_id": sample_id,
        "page_num": page_num,
        "proposal_index": proposal_index,
        "fact_id": fact_id,
        "figure_evidence_id": figure_evidence_id,
        "projection_id": projection.projection_id,
        "projection_version": projection.projection_version,
        "projection_fingerprint_sha256": projection.fingerprint_sha256,
        "ordinary_anchor_span_count": len(projection.anchor_spans),
        "controlled_diagram_anchor_spans": minted,
        "quarantine_disjoint_from_selectable_pool": not (ordinary_ids & minted_ids),
        "anchorable": bool(resolution["anchors"]),
        "anchor_count": len(resolution["anchors"]),
        "resolution": resolution,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    args = parser.parse_args()
    if not _corpus_ready():
        raise SystemExit("machine-local frozen corpus is not present; this one-shot survey cannot run here")

    cases = [survey_case(*case) for case in CASES]
    gonglu_41 = next(item for item in cases if item["sample_id"] == "gonglu_41_diagram_mixed")
    gonglu_01 = next(item for item in cases if item["sample_id"] == "gonglu_01_text_list")
    if not gonglu_41["anchorable"] or gonglu_41["anchor_count"] < 1:
        raise SystemExit("widening proof failed: gonglu_41 is still unanchorable under the widened pool")
    if gonglu_01["controlled_diagram_anchor_spans"] or gonglu_01["anchorable"]:
        raise SystemExit("fail-closed guard violated: gonglu_01 must stay unanchorable (no census-bound sidecar nodes)")

    audit = {
        "audit_version": "b1e_diagram_anchor_widening_audit_v1",
        "doctrine": (
            "diagram 卡锚点 = step2a V25 投影中满足三同（同源 manifest / 同页 / 语义绑定 caption|label）的"
            " anchor span；本 v2 勘察把锚源池扩为一般 text/cell spans ∪ controlled_diagram_anchor_spans"
            "（受控图示节点标签隔离道，缺省不铸、显式 opt-in、永不进一般可选池）；空集 fail-closed。"
        ),
        "inputs": {
            "evidence_root": EVIDENCE_ROOT.relative_to(NEW_ROOT).as_posix(),
            "content_policy": {"path": POLICY_PATH.relative_to(NEW_ROOT).as_posix(), "sha256": _file_sha256(POLICY_PATH)},
            "semantic_sidecar": {"path": SEMANTIC_SIDECAR_PATH.relative_to(NEW_ROOT).as_posix(), "sha256": _file_sha256(SEMANTIC_SIDECAR_PATH)},
            "disposition_sidecar": {"path": DISPOSITION_SIDECAR_PATH.relative_to(NEW_ROOT).as_posix(), "sha256": _file_sha256(DISPOSITION_SIDECAR_PATH)},
            "step2b_runs": {
                "gonglu_41_diagram_mixed": b1d.A_RUN.name,
                "gonglu_01_text_list": b1d.A2_RUN.name,
            },
            "page_manifests": {
                item["sample_id"]: _file_sha256(EVIDENCE_ROOT / item["sample_id"] / "pages" / f"page_{item['page_num']:04d}.json")
                for item in cases
            },
        },
        "cases": cases,
        "verdicts": {
            "gonglu_41_diagram_mixed": "anchorable via the widened controlled-diagram node-label lane",
            "gonglu_01_text_list": "stays fail-closed (its bridge figure has no census-bound sidecar node evidence)",
        },
    }
    run_root = args.run_root
    target = run_root / "anchor-widening-audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"anchor-widening audit written: {target}")
    print(
        f"gonglu_41: minted={len(gonglu_41['controlled_diagram_anchor_spans'])} anchors={gonglu_41['anchor_count']} | "
        f"gonglu_01: minted={len(gonglu_01['controlled_diagram_anchor_spans'])} anchorable={gonglu_01['anchorable']}"
    )


if __name__ == "__main__":
    main()
