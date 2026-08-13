"""B1 echo 生产道·阶段③转换器链执行器：B2 签字圈选件 → r810 reviewed typed-evidence fixture。

流程（驱动外置人工准入道的放行侧，task-7d955232 阶段③）：

1. 读检查点①产物：queue.json（人工评审队列，仅作对账）与 inbox/*.signed.json
   （评审人 B2 修订并签字的 ManualSelectionSpec payload——reviewer/recorded_at
   必须已由 B2 填写，占位符即 fail-closed）；
2. 由签字件重建 ExtractionSource（页绑定直接取签字件所对应队列项的 SourcePageBinding，
   零重算）并封印 ManualSelectionSpec（gates/review/extraction，新代码域封印）；
3. extract_reviewed_fixture（五确认权威决策 + r84 fixture 装配，内核三方绑定校验）；
4. **T4b 权威入口** export_reviewed_typed_evidence_fixture（released=放行决策集 +
   ExplicitProductionAuthorization 显式人工授权）→ r810 fixture，canonical json 落盘
   —— 驱动 echo 道以落盘字节 sha256 作 source_fixture_sha256（与 pilot 口径一致）。

authorization 的 reviewer/reason/recorded_at 是显式人工输入（B2 签字串 + 其回报文本），
经 CLI 参数传入，本工具不代造。

用法（仓根，B2 签字件回流后）：
  uv run python migration/b1_echo_typed_fixture.py ^
    --admission-root exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1-admission/checkpoints/admission ^
    --output exams/gonglu_golden5_rehearsal/data/live_runs/20260813-b1-admission/checkpoints/admission/reviewed-typed-evidence-fixture.json ^
    --reviewer "<B2 签字串>" --reason "<B2 授权语>" --recorded-at "<ISO 时间>"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer", required=True, help="B2 签字串（显式人工授权 reviewer）")
    parser.add_argument("--reason", required=True, help="B2 授权语（≥12 字）")
    parser.add_argument("--recorded-at", required=True, help="B2 签字时间（ISO）")
    parser.add_argument("--identity-period", default="b1")
    parser.add_argument(
        "--only",
        default=None,
        help="逗号分隔的签字件文件名白名单（不含路径）；缺省收 inbox 全部 *.signed.json。"
        "检查点①实测教训：批量签字可能留下与终审口径矛盾的误签残件，白名单钉死权威放行集。",
    )
    args = parser.parse_args()

    from examcard.gates.review.extraction import (
        extract_reviewed_fixture,
        make_extraction_source,
        make_manual_selection_spec,
    )
    from examcard.gates.review.typed_evidence_export import (
        ExplicitProductionAuthorization,
        export_reviewed_typed_evidence_fixture,
    )
    from examcard.knowledge.contracts.admission_fixtures import SourcePageBinding

    admission_root: Path = args.admission_root
    queue_doc = _read(admission_root / "queue.json")
    queue_by_identity: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in queue_doc["candidates"]:
        key = (item["subject_id"], item["record_id"], item["fact_id"], item["evidence_id"], item["presentation"])
        queue_by_identity[key] = item

    signed_paths = sorted((admission_root / "inbox").glob("*.signed.json"))
    if args.only:
        allow = {token.strip() for token in args.only.split(",") if token.strip()}
        signed_paths = [path for path in signed_paths if path.name in allow]
        missing = allow - {path.name for path in signed_paths}
        if missing:
            raise SystemExit(f"--only names not found in inbox: {sorted(missing)}")
    if not signed_paths:
        raise SystemExit("no signed specs in inbox/ — checkpoint-1 review has not returned")

    sources = []
    specs = []
    admitted: list[dict[str, Any]] = []
    for path in signed_paths:
        signed = _read(path)
        payload = dict(signed["payload"])
        reviewer = str(payload.get("reviewer") or "")
        recorded_at = str(payload.get("recorded_at") or "")
        if not reviewer or reviewer.startswith("<") or not recorded_at or recorded_at.startswith("<"):
            raise SystemExit(f"signed spec lacks a real reviewer signature: {path.name}")
        # 冒签事故（20260813 检查点①实录）后的鉴权硬化：B2 真签必携逐项取证块
        # b2_review；单纯 reviewer 字符串可被同名会话冒用，不再单独可信。
        if not signed.get("b2_review"):
            raise SystemExit(f"signed spec lacks the b2_review evidence block (forgery guard): {path.name}")
        key = (
            payload["subject_id"],
            payload["record_id"],
            payload["fact_id"],
            payload["evidence_id"],
            payload["presentation"],
        )
        queue_item = queue_by_identity.get(key)
        if queue_item is None:
            raise SystemExit(f"signed spec does not match any admission queue item: {path.name}")
        pages = tuple(SourcePageBinding.model_validate(page) for page in queue_item["pages"])
        sources.append(
            make_extraction_source(
                subject_id=queue_item["subject_id"],
                record_id=queue_item["record_id"],
                fact_id=queue_item["fact_id"],
                evidence_id=queue_item["evidence_id"],
                presentation=queue_item["presentation"],
                source_manifest_sha256=queue_item["source_manifest_sha256"],
                anchor_ids=tuple(queue_item["anchor_ids"]),
                pages=pages,
            )
        )
        specs.append(make_manual_selection_spec(payload))
        admitted.append(
            {
                "signed_file": path.name,
                "admission_queue_item_id": queue_item["queue_item_id"],
                "admission_candidate_id": queue_item["candidate_id"],
                "presentation": queue_item["presentation"],
                "fact_id": queue_item["fact_id"],
                "reviewer": reviewer,
            }
        )

    batch = extract_reviewed_fixture(tuple(sources), tuple(specs), identity_period=args.identity_period)
    queue_items_by_candidate = {}
    queue_items_by_id = {item.queue_item_id: item for item in batch.queue_items}
    for decision in batch.manifest.decisions:
        queue_items_by_candidate[decision.candidate_id] = queue_items_by_id[decision.queue_item_id]
    fixture = export_reviewed_typed_evidence_fixture(
        released=batch.manifest.decisions,
        queue_items_by_candidate=queue_items_by_candidate,
        queue_sha256=batch.queue_sha256,
        authorization=ExplicitProductionAuthorization(
            reviewer=args.reviewer,
            reason=args.reason,
            recorded_at=args.recorded_at,
            identity_period=args.identity_period,
        ),
    )

    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    import hashlib

    fixture_file_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    audit = {
        "task": "task-7d955232 B1 echo lane / stage-3 converter chain",
        "admitted_cards": admitted,
        "extraction_queue_sha256": batch.queue_sha256,
        "admission_materials_queue_sha256": queue_doc["queue_sha256"],
        "note": (
            "§7.1 admission queue（人工评审物料）与 extraction 队列（内容寻址）为两套身份域，"
            "以 (subject,record,fact,evidence,presentation) 五元组对账——runbook 整合机会已记录"
        ),
        "fixture": {
            "path": str(output),
            "file_sha256": fixture_file_sha256,
            "fixture_sha256": fixture.fixture_sha256,
            "candidates": fixture.reviewed_candidate_count,
            "chain": {
                "r89_admission_bundle_sha256": fixture.r89_admission_bundle_sha256,
                "authorization_manifest_sha256": fixture.authorization_manifest_sha256,
                "exported_r84_manifest_sha256": fixture.exported_r84_manifest_sha256,
                "r84_fixture_sha256": fixture.r84_fixture_sha256,
            },
        },
        "authorization": {"reviewer": args.reviewer, "recorded_at": args.recorded_at},
    }
    (admission_root / "b1-conversion-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"fixture_path": str(output), "file_sha256": fixture_file_sha256, "candidates": fixture.reviewed_candidate_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
