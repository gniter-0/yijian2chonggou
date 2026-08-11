"""一次性迁移脚本：把清单 v2 第六节「随核心迁移 21 个」测试从 yijian2 搬入本仓 tests/。

规则（对 yijian2 只读）：
- import 前缀改写 knowledge_v2/evidence_v2/materials_v2 → examcard.*（映射见 IMPORT_MAP）；
- 删除 sys.path 黑魔法；ROOT 一律指向新仓根（金样资产按旧仓相对布局镜像于仓根：
  data/ configs/ scripts/tests/fixtures/ schemas/v2/ docs/v2/ input_pdf_公路/，对用例只读，T5；
  与 fact_engine.ROOT=仓根 及 repo_tmp_path 的仓库相对路径契约同根）；
- full_check 为 V1 生产工具（清单五-2 留旧仓）：改为守卫导入，相关用例在新仓 skip；
- 其余测试逻辑逐字节保持。
"""
from __future__ import annotations

import re
from pathlib import Path

OLD_TESTS = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts\tests")
NEW_TESTS = Path(__file__).resolve().parents[1] / "tests"

FILES = [
    "test_diagram_disposition_v2.py",
    "test_evidence_v2_engine.py",
    "test_fact_v2_engine.py",
    "test_learning_fixture_v2.py",
    "test_phase25f_structural_reviews_v2.py",
    "test_phase2r04_learning_table_closure.py",
    "test_phase2r054_table_atomic_continuation.py",
    "test_phase2r05_complete_table_semantics.py",
    "test_phase2r06_multipage_continuation.py",
    "test_phase2r07_real_continuation_golden.py",
    "test_phase2r0981_composition_numeric_occurrences.py",
    "test_phase2r12_v25_golden_review.py",
    "test_phase2r13_v25_reconciliation.py",
    "test_phase2r142_v25_projection.py",
    "test_semantic_contracts_v2.py",
    "test_semantic_llm_v2.py",
    "test_semantic_projection_v2.py",
    "test_teaching_quality_v2.py",
    "test_v1_adapter_v2.py",
    "test_v2_contracts.py",
    "test_vision_idempotency.py",
]

IMPORT_MAP = [
    ("from knowledge_v2.semantic_contracts import", "from examcard.knowledge.contracts import"),
    ("from knowledge_v2.semantic_llm import", "from examcard.knowledge.llm import"),
    ("from knowledge_v2.semantic_projection import", "from examcard.knowledge.projection import"),
    ("from knowledge_v2.fact_engine import", "from examcard.knowledge.fact_engine import"),
    ("from knowledge_v2.learning_fixture import", "from examcard.knowledge.learning_fixture import"),
    ("from knowledge_v2.continuation_review import", "from examcard.knowledge.continuation.review import"),
    ("from knowledge_v2.table_repair_sidecar import", "from examcard.knowledge.continuation.repair_sidecar import"),
    ("from knowledge_v2.table_continuation_integration import", "from examcard.knowledge.continuation.integration import"),
    ("from knowledge_v2.diagram_sidecar import", "from examcard.knowledge.diagram.sidecar import"),
    ("from knowledge_v2.diagram_disposition import", "from examcard.knowledge.diagram.disposition import"),
    ("from knowledge_v2.teaching_quality import", "from examcard.knowledge.quality import"),
    ("from knowledge_v2.v1_adapter import", "from examcard.knowledge.adapter_v1 import"),
    ("from knowledge_v2.identity import", "from examcard.knowledge.identity import"),
    ("from knowledge_v2.state_machine import", "from examcard.knowledge.state_machine import"),
    ("from knowledge_v2.step2b_card_golden import", "from examcard.knowledge.step2b_card_golden import"),
    ("from knowledge_v2.v25_generic_contracts import", "from examcard.knowledge.contracts.v25_generic_contracts import"),
    ("from knowledge_v2.phase2r12_v25_golden_review import", "from examcard.knowledge.contracts.phase2r12_v25_golden_review import"),
    ("from knowledge_v2.phase2r13_v25_reconciliation import", "from examcard.knowledge.contracts.phase2r13_v25_reconciliation import"),
    ("from knowledge_v2.phase2r142_v25_projection import", "from examcard.knowledge.contracts.phase2r142_v25_projection import"),
    ("from knowledge_v2.phase2r14_v25_live import", "from examcard.knowledge.contracts.phase2r14_v25_live import"),
    ("from knowledge_v2.schema import", "from examcard.knowledge.schema import"),
    ("import evidence_v2.vision as vision_module", "import examcard.evidence.vision as vision_module"),
    ("from evidence_v2.", "from examcard.evidence."),
    ("from materials_v2.", "from examcard.materials."),
    ('__import__("knowledge_v2.semantic_contracts"', '__import__("examcard.knowledge.contracts"'),
]

LEGACY_ROOT = "Path(__file__).resolve().parents[1]"

DROP_LINE_PATTERNS = [
    re.compile(r"^SCRIPTS_DIR = Path\(__file__\)\.resolve\(\)\.parent\.parent$"),
    re.compile(r"^sys\.path\.insert\(0, str\(SCRIPTS_DIR\)\)$"),
    re.compile(r"^SCRIPTS = Path\(__file__\)\.resolve\(\)\.parent\.parent$"),
    re.compile(r'^TOOLS = SCRIPTS / "tools"$'),
    re.compile(r"^if str\(SCRIPTS\) not in sys\.path:$"),
    re.compile(r"^if str\(TOOLS\) not in sys\.path:$"),
    re.compile(r"^    sys\.path\.insert\(0, str\(SCRIPTS\)\)$"),
    re.compile(r"^    sys\.path\.insert\(0, str\(TOOLS\)\)$"),
]

FULL_CHECK_IMPORT = "from full_check import check_all, scan_chapter"
FULL_CHECK_GUARD = (
    "try:  # full_check 为 V1 生产工具，留守旧仓（清单五-2），未随核心迁移\n"
    "    from full_check import check_all, scan_chapter\n"
    "except ImportError:  # pragma: no cover\n"
    "    check_all = scan_chapter = None\n"
)
FULL_CHECK_TEST_DEF = "def test_full_check_v2_uses_grounding_and_coverage_not_v1_heuristics():"
FULL_CHECK_SKIP = (
    "    if check_all is None:\n"
    '        pytest.skip("full_check 为 V1 生产工具，留守旧仓，未随核心迁移（清单五-2）")\n'
)


def migrate(name: str) -> str:
    text = (OLD_TESTS / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if any(pattern.match(line) for pattern in DROP_LINE_PATTERNS):
            continue
        if line == "ROOT = SCRIPTS_DIR.parent" or line == "ROOT = SCRIPTS.parent":
            out.append(f"ROOT = {LEGACY_ROOT}")
            continue
        if line == "ROOT = Path(__file__).resolve().parents[2]":
            out.append(f"ROOT = {LEGACY_ROOT}")
            continue
        if line == FULL_CHECK_IMPORT:
            out.append(FULL_CHECK_GUARD.rstrip("\n"))
            continue
        if line == FULL_CHECK_TEST_DEF:
            out.append(line)
            out.append(FULL_CHECK_SKIP.rstrip("\n"))
            continue
        out.append(line)
    new_text = "\n".join(out) + "\n"
    for old, new in IMPORT_MAP:
        new_text = new_text.replace(old, new)
    # 清理已无实际使用的 import sys（仅当剩余文本不再出现 sys. 引用时）
    if "sys." not in new_text.replace("import sys", ""):
        new_text = re.sub(r"^import sys\n", "", new_text, flags=re.MULTILINE)
    (NEW_TESTS / name).write_text(new_text, encoding="utf-8", newline="\n")
    return name


def main() -> None:
    for name in FILES:
        migrate(name)
    print(f"migrated {len(FILES)} test files -> {NEW_TESTS}")


if __name__ == "__main__":
    main()
