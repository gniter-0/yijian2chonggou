"""step2a 质量深评工作底稿导出（零 API 成本，pilot 质量漂移诊断用）。

把 2chat 轮五章 step2a live 响应与 r12 人工评审学习黄金并排导出：
- 每章基准目标：fact_key / fact_type / title(+aliases) / required_terms / 锚点选择器文本
- 每章 live 提案：fact_type / label / assertion / 所选锚点的 canonical 原文（截断）
输出：<storage>/step2a_quality_workpad.json（供人工逐条评审出报告）。

运行：uv run python migration/build_step2a_quality_workpad.py data/pilot_gonglu5/step2_live_2chat
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

OLD_ROOT = resolve_legacy_root(NEW_ROOT)

from examcard.knowledge.learning_fixture import load_learning_golden_fixture  # noqa: E402

FIXTURE = OLD_ROOT / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r12_v25_reviewed.yaml"


def main() -> int:
    storage = Path(sys.argv[1] if len(sys.argv) > 1 else "data/pilot_gonglu5/step2_live_2chat").resolve()
    fixture = load_learning_golden_fixture(FIXTURE)
    targets_by_sample = {
        sample.sample_id: [
            {
                "fact_key": fact.fact_key,
                "fact_type": fact.fact_type.value,
                "title": fact.title,
                "title_aliases": list(fact.title_aliases),
                "required_terms": list(fact.required_terms),
                "anchor_selectors": [
                    {"kind": anchor.kind.value if anchor.kind else None, "text": anchor.text}
                    for anchor in fact.primary_anchors
                ],
            }
            for fact in sample.facts
        ]
        for sample in fixture.samples
    }

    workpad: list[dict[str, object]] = []
    for request_path in sorted((storage / "requests").glob("*.json")):
        request_document = json.loads(request_path.read_text(encoding="utf-8"))
        request = request_document["request"]
        if request["identity"]["stage"] != "step2a":
            continue
        sample_id = request["sample_id"]
        # span 原文在 model_input 投影里（text_evidence / 表格单元格 各自携带 anchor_spans）
        model_input = request_document["model_input"]
        spans: dict[str, str] = {}
        for section in model_input["sections"]:
            for item in section.get("text_evidence", ()):
                for span in item.get("anchor_spans", ()):
                    spans[span["anchor_span_id"]] = span["canonical_text"]
            for table in section.get("tables", ()):
                for row in table.get("rows", ()):
                    for cell in row.get("cells", ()):
                        for span in cell.get("anchor_spans", ()):
                            spans[span["anchor_span_id"]] = span["canonical_text"]
        record_path = storage / "records" / request_path.name
        record = json.loads(record_path.read_text(encoding="utf-8"))
        proposals = []
        if record.get("response"):
            for proposal in record["response"]["proposals"]:
                proposals.append(
                    {
                        "fact_type": proposal["fact_type"],
                        "label": proposal["label"],
                        "assertion": proposal["assertion"][:220],
                        "anchor_texts": [
                            spans.get(anchor_id, "<missing>")[:120]
                            for anchor_id in proposal["primary_anchor_span_ids"]
                        ],
                    }
                )
        workpad.append(
            {
                "sample_id": sample_id,
                "record_state": record["state"],
                "reviewed_targets": targets_by_sample.get(sample_id, []),
                "live_proposals": proposals,
            }
        )

    output = storage / "step2a_quality_workpad.json"
    output.write_text(json.dumps(workpad, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"workpad": str(output), "samples": len(workpad), "proposals": sum(len(item["live_proposals"]) for item in workpad)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
