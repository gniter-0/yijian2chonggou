from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest


try:  # full_check 为 V1 生产工具，留守旧仓（清单五-2），未随核心迁移
    from full_check import check_all, scan_chapter
except ImportError:  # pragma: no cover
    check_all = scan_chapter = None
from examcard.knowledge.adapter_v1 import V2AdapterError, adapt_fact_run, load_fact_run_as_v1


ROOT = Path(__file__).resolve().parents[1]
FACT_ROOT = ROOT / "data" / "facts_v2" / "golden" / "gonglu_phase0_v1" / "core_only_v1"
SAMPLE = FACT_ROOT / "gonglu_01_text_list" / "fact_run.json"
VISUAL_SAMPLE = FACT_ROOT / "gonglu_24_table_formula" / "fact_run.json"

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)


def _payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_adapter_preserves_v2_lineage_without_writing_source():
    before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    chapter = load_fact_run_as_v1(SAMPLE)

    assert chapter["_v2_adapter"]["read_only"] is True
    assert chapter["_v2_adapter"]["quality_gate"]["publishable"] is True
    assert chapter["_v2_adapter"]["quality_gate"]["blockers"] == []
    assert len(chapter["knowledge_points"]) == 15
    reviewed = next(point for point in chapter["knowledge_points"] if point["_v2"]["diagram_review_id"])
    assert reviewed["raw_id"].startswith("kp:")
    assert reviewed["_anchor_status"] == "v2:manual_verified"
    assert reviewed["_v2"]["renderability"] == "publishable"
    assert reviewed["_v2"]["quality_flags"] == []
    assert reviewed["_v2"]["coverage"]["complete"] is True
    visual = next(anchor for anchor in reviewed["content_anchors"] if anchor["visual_only"])
    assert visual["anchor_type"] == "v2_evidence_ref"
    assert visual["bbox"] and "start" not in visual and "end" not in visual
    visual_before = hashlib.sha256(VISUAL_SAMPLE.read_bytes()).hexdigest()
    visual_chapter = load_fact_run_as_v1(VISUAL_SAMPLE)
    visual_reference = next(point for point in visual_chapter["knowledge_points"] if point["_v2"]["diagram_disposition_id"])
    trace = visual_reference["_v2"]["diagram_disposition"]
    assert visual_reference["kp_type"] == "visual_reference"
    assert trace["disposition"] == "photo_example"
    assert trace["source_diagram_review_sidecar_id"]
    assert trace["original_crop_path"] and trace["original_crop_sha256"]
    assert trace["review_crop_manifest_path"] and trace["review_crop_manifest_sha256"]
    assert trace["review_crop_path"] and trace["review_crop_sha256"]
    assert trace["review_crop_render_dpi"] == 600
    assert trace["review_crop_bbox"]
    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == before
    assert hashlib.sha256(VISUAL_SAMPLE.read_bytes()).hexdigest() == visual_before


def test_adapter_rejects_unknown_coverage_owner():
    payload = copy.deepcopy(_payload())
    record = next(item for item in payload["coverage"]["records"] if item["disposition"] == "primary_assigned")
    record["coverage_owner"] = "kp:000000000000000000000000"
    with pytest.raises(V2AdapterError, match="unknown card owners"):
        adapt_fact_run(payload)


def test_full_check_v2_uses_grounding_and_coverage_not_v1_heuristics():
    if check_all is None:
        pytest.skip("full_check 为 V1 生产工具，留守旧仓，未随核心迁移（清单五-2）")
    chapter = load_fact_run_as_v1(SAMPLE)
    codes = scan_chapter(chapter)
    assert codes == {}

    summary = check_all(str(FACT_ROOT), source="v2")
    assert summary["chapters"] == 5
    assert summary["kps"] == 74
    assert summary["critical"] == 0
    assert summary["issues"]["no_anchors"] == 0
    assert summary["issues"]["coverage_incomplete"] == 0
    assert summary["issues"]["grounding_unresolved"] == 0
    assert summary["issues"]["renderability_manual_review"] == 0
    assert summary["issues"]["v2_gate_blocker"] == 0
