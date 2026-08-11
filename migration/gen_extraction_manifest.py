# -*- coding: utf-8 -*-
"""阶段1·迁移清点 v3：只读扫描 yijian2/scripts，生成 knowledge_v2 抽包清单。

v3 修订（n4 施工实测回填，2026-08-12）：

  RC3 · 两处路由修正 + 扫描盲区留痕
    learning_fixture / step2b_card_golden 实测在运行时 import 闭包内：前者被 V25 运行时闭包
    五模块（phase2r11/12/13/14/142）顶层 import，后者被 table_continuation（新仓
    continuation/core.py）函数内缩进式惰性 import。二者若按 v2 落 tests/，新仓包内 import 即断
    → 路由改为 src/examcard/knowledge/ 包内（见 CORE_TARGETS）。
    另补动态字符串导入检测（__import__/importlib.import_module("knowledge_v2.x")，见 _imp_dyn）；
    缩进式惰性 import 本就被未锚定行首的 scan_kv2_imports 正则覆盖，此次仅加注说明。

v2 修订（人工复核后按两处根因修正；根因诊断见 report_task / verify_manifest_audit*.py）：

  RC1 · archive 安全性（原地归档会漏搬现役依赖）
    迁走集(core/current)的传递 import 闭包会拉入若干 V25 运行时 phase 模块
    （直接触发：v25_teaching_gate_layers → phase2r142_v25_projection）。这些模块此前因
    命名不含 distill 关键字而 fall-through 到 archive/留旧仓，但它们是现役契约的运行时依赖，
    必须随现役契约“物理迁走”，否则新仓 import 断。
    → v2 改为按 **import 闭包** 自动上提（kind=current-runtime），而不再仅靠关键字命名判去向。

  RC2 · 测试归属（旧规则过粗）
    旧规则“测试引用任一 core 模块 → 迁走”会把只借用 semantic_llm/contracts 类型的 phase 伴生
    测试一起拽进迁走集，而它们还依赖 tools/*、phase、historical 等留旧仓物，搬到新仓开箱即挂。
    → v2 改为 **import 闭包判定**：仅当测试的全部依赖都能在新仓满足（只引迁走的 knowledge_v2
      模块 + evidence_v2/materials_v2，且不引 tools/*、fact_check_lib、prompts、legacy、留仓
      phase/historical）才判“随核心迁移”；否则随其伴生物“留旧仓”。同时把只引 evidence_v2/
      materials_v2（整包搬运）的测试也纳入迁走判定（旧版完全漏判）。

对旧仓零写入（只读）。分类逻辑抽为可导入函数，供 verify_manifest_audit*.py 复用做自检。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

OLD_SCRIPTS = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts")
KV2 = OLD_SCRIPTS / "knowledge_v2"
OUT = Path(__file__).resolve().parents[1] / "docs" / "extraction_manifest.md"

# 迁走 / 留旧仓 的语义分组
MIGRATE_KINDS = ("core", "core-current", "current-runtime")
STAY_KINDS = ("phase", "historical")
# 测试依赖里“会随迁走”的本地包（整包搬运） vs “留旧仓”的本地包
MIGRATING_PKGS = {"evidence_v2", "materials_v2"}
STAYING_PKGS = {"tools", "fact_check_lib", "prompts", "legacy", "templates"}

# 核心模块 → 新仓目标位置（草案，人工可改）
CORE_TARGETS = {
    "schema": "src/examcard/knowledge/schema.py",
    "identity": "src/examcard/knowledge/identity.py",
    "state_machine": "src/examcard/knowledge/state_machine.py",
    "fact_engine": "src/examcard/knowledge/fact_engine.py",
    "semantic_contracts": "src/examcard/knowledge/contracts/*  (389KB 在此拆分)",
    "semantic_llm": "src/examcard/knowledge/llm.py",
    "semantic_projection": "src/examcard/knowledge/projection.py",
    "learning_fixture": "src/examcard/knowledge/learning_fixture.py  (黄金样本装置·RC3 路由修正：被 V25 运行时闭包顶层 import)",
    "teaching_quality": "src/examcard/knowledge/quality.py",
    "continuation_review": "src/examcard/knowledge/continuation/review.py",
    "table_continuation": "src/examcard/knowledge/continuation/core.py",
    "table_continuation_integration": "src/examcard/knowledge/continuation/integration.py",
    "table_continuation_request_fixture": "tests/fixtures/",
    "table_repair_sidecar": "src/examcard/knowledge/continuation/repair_sidecar.py",
    "diagram_disposition": "src/examcard/knowledge/diagram/disposition.py",
    "diagram_sidecar": "src/examcard/knowledge/diagram/sidecar.py",
    "step2b_bridge": "src/examcard/knowledge/planner/bridge.py",
    "step2b_card_golden": "src/examcard/knowledge/step2b_card_golden.py  (RC3 路由修正：被 continuation/core.py 函数内惰性 import)",
    "v1_adapter": "src/examcard/knowledge/adapter_v1.py",
}
CURRENT_RUNTIME_TARGET = "src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提)"

# 总指挥 A 案裁决（2026-08-11，依据 docs/design/distill_gates_review_design.md §4/§5-2/§5-6）：
# 以下 distill 模块的『去向』不变（逻辑仍留各自桶），但其【跨桶消费的纯数据契约模型】部分
# 下沉到 knowledge/contracts/admission_fixtures.py（法定哈希搬运件 admission_legacy_hash.py 随之下沉，
# 铁律 bc-b8b9aca6：逐字节原样搬运、禁入 common/hashing）。仅注解、不改迁走/留仓分类。
ADMISSION_FIXTURE_NOTE = {
    "phase2r810_r89_reviewed_admission_authorization":
        "；【A案】跨桶数据模型 ReviewedTypedEvidenceFixture → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r10_generic_step3_human_review_admission":
        "；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r103_step3_production_raw_decision_ingestion":
        "；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r104_step3_explicit_production_authorization":
        "；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card + 运行证物三型(AcceptanceReport/OfflineGate/PriorFreeze/FrozenArtifact) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r2242_step3_reviewed_release_merge":
        "；【A案】跨桶数据模型 合并三型(Step3UnifiedReviewedReleaseFixture/Step3MergedReviewedReleaseCard/Step3MergedSourceOwnedDisplayText) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r319_step4_card4_reviewed_image_fixture":
        "；【A案】跨桶数据模型 Step4PilotHandoffRefresh → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    "phase2r9_generic_step3_card_compiler":
        "；【A案】跨桶数据模型 Step3CardContract → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)",
    # 补遗（gates/review 设计者 2026-08-11，其稿 §5 契约 3；实证 r98 自哈希为 r9/r10x 变体：
    # _jsonable 不预排序 + _hash_model 排除 schema_version、canonical_hash 同源 r9 编译器）：
    "phase2r98_generic_step3_deterministic_release_closure":
        "；【A案】跨桶数据模型 DeterministicDraftCard/DeterministicReleaseBundle → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py·r9/r10x 变体)",
}


def base_kind(name: str) -> tuple[str, str]:
    """按命名/前缀的基础分类（RC1 上提前的初判）。返回 (大类, 去向/家族)。"""
    if name == "v1_adapter":
        return "core", CORE_TARGETS[name]
    if name.startswith("phase2r"):
        if "generic" in name and ("compiler" in name or "contract" in name):
            return "phase", "distill:compilers"
        if any(k in name for k in ("compatibility", "remediation", "envelope", "relay")):
            return "phase", "distill:providers"
        if any(k in name for k in (
            "human", "review", "admission", "decision", "workbench",
            "ingestion", "authorization",
        )):
            return "phase", "distill:gates-review"
        if "release" in name or "readiness" in name:
            return "phase", "distill:release-gate"
        return "phase", "archive"
    if re.match(r"v\d+_", name):
        if name.startswith("v25"):
            return "core-current", "src/examcard/knowledge/contracts/ (V25 现役契约)"
        return "historical", "留旧仓 (v19/v23/v24 历史台阶)"
    return "core", CORE_TARGETS.get(name, "src/examcard/knowledge/ (待定)")


# ---- import 扫描（与 v1 一致的正则，只读）----
# 注：以下正则均未锚定行首，函数体内的缩进式惰性 import（如 table_continuation 第 525 行
# `from .step2b_card_golden import …`）同样能扫到——RC3 复核确认这不是漏检面。
_imp_dotted = re.compile(r"(?:from|import)\s+knowledge_v2\.([A-Za-z0-9_]+)")
_imp_pkg = re.compile(r"from\s+knowledge_v2\s+import\s+([A-Za-z0-9_,\s()]+)")
_imp_rel_mod = re.compile(r"from\s+\.([A-Za-z0-9_]+)\s+import")
_imp_rel_pkg = re.compile(r"from\s+\.\s+import\s+([A-Za-z0-9_,\s()]+)")
_imp_pkgroot = re.compile(r"(?:^|\n)\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)")
# RC3 兜底：动态字符串导入（__import__("knowledge_v2.x") / importlib.import_module("knowledge_v2.x")）
# 是 from/import 正则的真实盲区（实例：phase2r14_v25_live 第 336 行）。
_imp_dyn = re.compile(r"""(?:__import__|import_module)\(\s*["']knowledge_v2\.([A-Za-z0-9_]+)["']""")


def scan_kv2_imports(text: str, mods: set[str], is_kv2: bool) -> set[str]:
    found = set(_imp_dotted.findall(text))
    found.update(_imp_dyn.findall(text))
    for grp in _imp_pkg.findall(text):
        for token in re.split(r"[,\s()]+", grp):
            if token in mods:
                found.add(token)
    if is_kv2:
        found.update(_imp_rel_mod.findall(text))
        for grp in _imp_rel_pkg.findall(text):
            for token in re.split(r"[,\s()]+", grp):
                if token in mods:
                    found.add(token)
    return {m for m in found if m in mods}


def scan_pkg_roots(text: str) -> set[str]:
    """本地包顶层根（tools/evidence_v2/materials_v2/...），用于测试闭包判定。"""
    roots = set(_imp_pkgroot.findall(text))
    return roots & (MIGRATING_PKGS | STAYING_PKGS)


def build_classification(old_scripts: Path = OLD_SCRIPTS):
    """一次性构建全量分类结果，供生成器与自检脚本共享（单一事实源）。

    返回 dict：mods/sizes/kinds/dests/fwd/importers/test_kv2/test_pkgs/pulled。
    kinds 已应用 RC1 的 import-闭包上提。
    """
    kv2 = old_scripts / "knowledge_v2"
    mods = {p.stem: p.stat().st_size for p in kv2.glob("*.py") if p.stem != "__init__"}
    modset = set(mods)

    fwd: dict[str, set[str]] = defaultdict(set)          # kv2 模块 -> 它 import 的 kv2 模块
    importers: dict[str, set[str]] = defaultdict(set)    # kv2 模块 -> 谁 import 它（rel 路径）
    test_kv2: dict[str, set[str]] = {}                   # tests/*.py -> 引用的 kv2 模块
    test_pkgs: dict[str, set[str]] = {}                  # tests/*.py -> 引用的本地包根

    for py in old_scripts.rglob("*.py"):
        rel = py.relative_to(old_scripts).as_posix()
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        is_kv2 = rel.startswith("knowledge_v2/")
        found = scan_kv2_imports(text, modset, is_kv2)
        if is_kv2:
            selfname = Path(rel).stem
            fwd[selfname] |= (found - {selfname})
        for mod in found:
            if rel == f"knowledge_v2/{mod}.py":
                continue
            importers[mod].add(rel)
        if rel.startswith("tests/"):
            test_kv2[rel] = found
            test_pkgs[rel] = scan_pkg_roots(text)

    base = {name: base_kind(name) for name in mods}
    kinds = {name: base[name][0] for name in mods}
    dests = {name: base[name][1] for name in mods}

    # RC1：迁走闭包上提。seed=core/core-current，沿 fwd 求传递闭包；
    # 闭包内被判 phase/historical 的模块 → 上提为 current-runtime（必须物理迁走）。
    seed = {n for n in mods if kinds[n] in ("core", "core-current")}
    closure, stack = set(seed), list(seed)
    while stack:
        cur = stack.pop()
        for dep in fwd.get(cur, ()):  # noqa: B007
            if dep not in closure:
                closure.add(dep)
                stack.append(dep)
    pulled = sorted(n for n in closure if kinds[n] in ("phase", "historical"))
    for n in pulled:
        kinds[n] = "current-runtime"
        dests[n] = CURRENT_RUNTIME_TARGET

    notes = {name: ADMISSION_FIXTURE_NOTE.get(name, "") for name in mods}

    return {
        "mods": mods, "kinds": kinds, "dests": dests, "fwd": fwd,
        "importers": importers, "test_kv2": test_kv2, "test_pkgs": test_pkgs,
        "pulled": pulled, "notes": notes,
    }


def classify_test(rel: str, C: dict) -> tuple[str, dict]:
    """RC2 测试归属：返回 ('migrate'|'stay'|'irrelevant', 详情)。"""
    kinds = C["kinds"]
    migrate_mods = {n for n, k in kinds.items() if k in MIGRATE_KINDS}
    stay_mods = {n for n, k in kinds.items() if k in STAY_KINDS}
    kv2 = C["test_kv2"].get(rel, set())
    pkgs = C["test_pkgs"].get(rel, set())

    mig_kv2 = kv2 & migrate_mods
    stay_kv2 = kv2 & stay_mods
    mig_pkg = pkgs & MIGRATING_PKGS
    stay_pkg = pkgs & STAYING_PKGS

    relevant = bool(mig_kv2 or stay_kv2 or mig_pkg)
    detail = {
        "mig_kv2": sorted(mig_kv2), "stay_kv2": sorted(stay_kv2),
        "mig_pkg": sorted(mig_pkg), "stay_pkg": sorted(stay_pkg),
    }
    if not relevant:
        return "irrelevant", detail
    can_move = bool(mig_kv2 or mig_pkg) and not stay_kv2 and not stay_pkg
    return ("migrate" if can_move else "stay"), detail


def breakdown(mod: str, importers: dict[str, set[str]]) -> dict[str, int]:
    cats = {"tests": 0, "export": 0, "phase": 0, "core": 0, "tools": 0, "other": 0}
    for rel in importers.get(mod, ()):  # noqa: B007
        if rel.startswith("tests/"):
            cats["tests"] += 1
        elif rel == "tools/export_v2_schemas.py":
            cats["export"] += 1
        elif rel.startswith("knowledge_v2/phase2r"):
            cats["phase"] += 1
        elif rel.startswith("knowledge_v2/"):
            cats["core"] += 1
        elif rel.startswith("tools/"):
            cats["tools"] += 1
        else:
            cats["other"] += 1
    return cats


def main() -> None:
    C = build_classification()
    mods, kinds, dests = C["mods"], C["kinds"], C["dests"]
    importers, pulled, notes = C["importers"], C["pulled"], C["notes"]

    rows = []
    for name in sorted(mods):
        cats = breakdown(name, importers)
        rows.append((name, kinds[name], dests[name], mods[name], cats))

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[row[1]].append(row)

    fam_groups: dict[str, list] = defaultdict(list)
    for row in groups.get("phase", []):
        fam_groups[row[2]].append(row)

    # 测试归属（RC2 闭包判定）
    tests_migrate, tests_stay = [], []
    for rel in sorted(C["test_kv2"]):
        verdict, det = classify_test(rel, C)
        if verdict == "migrate":
            tests_migrate.append((rel, det))
        elif verdict == "stay":
            tests_stay.append((rel, det))
    relevant_n = len(tests_migrate) + len(tests_stay)

    # scripts/tools 构成
    tools_dir = OLD_SCRIPTS / "tools"
    tools_all = list(tools_dir.glob("*.py"))
    tools_phase = [p for p in tools_all if re.search(r"phase2r|_v2|v1\d", p.name)]
    tools_v1 = [p for p in tools_all if p not in tools_phase]

    kb = lambda n: f"{n / 1024:.0f}KB"  # noqa: E731
    lines: list[str] = []
    w = lines.append
    w("# knowledge_v2 抽包清点清单（extraction manifest v3）")
    w("")
    w(f"- 生成日期：{date.today().isoformat()}；方法：正则扫描 import 图（只读）")
    w(f"- 扫描范围：yijian2/scripts 全部 .py（含 tests/tools）；对象：knowledge_v2 下 {len(mods)} 个模块")
    w("- v2：已按复核根因修正——RC1 迁走 import 闭包自动上提 V25 运行时模块；RC2 测试按 import 闭包判归属。详见文末『七』。")
    w("- v3：RC3 施工实测回填——`learning_fixture` / `step2b_card_golden` 属运行时 import 闭包，"
      "路由改落 `src/examcard/knowledge/` 包内（不入 tests/）；补记扫描盲区（缩进式惰性 import、动态字符串导入）。详见文末『七』。")
    w("")
    total = sum(mods.values())
    w("## 总览")
    w("")
    w("| 大类 | 模块数 | 体量 | 去向 |")
    w("|---|---|---|---|")
    label = {
        "core": ("core", "迁走·新仓"),
        "core-current": ("core-current", "迁走·新仓(V25 现役)"),
        "current-runtime": ("current-runtime", "迁走·新仓(V25 运行时闭包·RC1 上提)"),
        "historical": ("historical", "留旧仓"),
        "phase": ("phase", "留旧仓(distill 提炼/archive 归档)"),
    }
    for kind in ("core", "core-current", "current-runtime", "historical", "phase"):
        rs = groups.get(kind, [])
        nm, dr = label[kind]
        w(f"| {nm} | {len(rs)} | {kb(sum(r[3] for r in rs))} | {dr} |")
    w(f"| 合计 | {len(rows)} | {kb(total)} | — |")
    w("")
    mig_total = sum(len(groups.get(k, [])) for k in MIGRATE_KINDS)
    w(f"> 迁走(core+core-current+current-runtime)= **{mig_total} 个**；留旧仓(historical+phase)= **{len(rows) - mig_total} 个**。")
    w("")

    def table(rs, title):
        w(f"## {title}（{len(rs)} 个，{kb(sum(r[3] for r in rs))}）")
        w("")
        w("| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |")
        w("|---|---|---|---|")
        for name, _k, dest, size, c in sorted(rs, key=lambda r: -r[3]):
            ref = (f"{c['tests']}/{c['export']}/{c['phase']}/{c['core']}"
                   f"/{c['tools']}/{c['other']}")
            w(f"| {name} | {kb(size)} | {ref} | {dest}{notes.get(name, '')} |")
        w("")

    table(groups.get("core", []), "一、核心模块 → 新仓 src/examcard")
    table(groups.get("core-current", []), "二、现役契约（V25 声明层）→ 新仓")

    # 二-2：RC1 上提的 V25 运行时闭包
    cr = groups.get("current-runtime", [])
    w(f"## 二-2、V25 运行时闭包（RC1·import 闭包上提·必须随现役契约迁走）（{len(cr)} 个，{kb(sum(r[3] for r in cr))}）")
    w("")
    w("> 这些模块被迁走集的 import 闭包拉入（起点：`v25_teaching_gate_layers → phase2r142_v25_projection`），"
      "是 V25 现役契约的**运行时依赖**。旧版按命名把它们判成 archive/留旧仓会导致新仓 import 断（archive 安全性缺陷）。")
    w("> ⚠ 该簇内部相互成环（142_projection↔14_live↔12/13_review↔11_five_golden），说明**现役契约与黄金/复核脚手架发生耦合**；"
      "阶段2集成时建议解耦（把纯黄金样本落 tests/golden、契约运行时仅保留必要类型），但**当前为保 import 闭包必须整簇迁走**。")
    w("")
    w("| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 被谁拉入(直接 importer) | 去向 |")
    w("|---|---|---|---|---|")
    for name, _k, dest, size, c in sorted(cr, key=lambda r: -r[3]):
        ref = f"{c['tests']}/{c['export']}/{c['phase']}/{c['core']}/{c['tools']}/{c['other']}"
        who = sorted(x for x in C["fwd"] if name in C["fwd"][x] and kinds.get(x) in MIGRATE_KINDS)
        w(f"| {name} | {kb(size)} | {ref} | {', '.join(who)} | {dest} |")
    w("")

    table(groups.get("historical", []), "三、历史台阶（v19/v23/v24）→ 留旧仓归档")

    w(f"## 四、phase 脚本（{len(groups.get('phase', []))} 个）")
    w("")
    w("distill = 通用逻辑的提炼来源（逻辑进新仓对应包，脚本本体仍留旧仓）；archive = 原地归档。")
    w("注：原 v1 归在 phase 的 5 个 V25 运行时模块已按 RC1 上提至『二-2』，故本节较 v1 少 5 个。")
    w("注：标【A案】的行，其去向桶不变，但【跨桶消费的纯数据模型】另下沉 knowledge/contracts/admission_fixtures.py"
      "（+法定哈希搬运件 admission_legacy_hash.py），依据 docs/design/distill_gates_review_design.md §4/§5。")
    w("")
    order = [
        ("distill:compilers", "4.1 提炼源：compilers（Step3/4 编译器）"),
        ("distill:providers", "4.2 提炼源：providers（供应商兼容层）"),
        ("distill:gates-review", "4.3 提炼源：gates/review（门禁与人工准入）"),
        ("distill:release-gate", "4.4 提炼源：release gate（发布就绪）"),
        ("archive", "4.5 原地归档（pilot/smoke/freeze/checkpoint 等一次性）"),
    ]
    for key, title in order:
        rs = fam_groups.get(key, [])
        if rs:
            table(rs, title)

    w("## 五、evidence_v2 / materials_v2 —— 原样整包搬运")
    w("")
    for pkg in ("evidence_v2", "materials_v2"):
        d = OLD_SCRIPTS / pkg
        files = sorted(p.name for p in d.glob("*.py"))
        size = sum(p.stat().st_size for p in d.glob("*.py"))
        w(f"- `{pkg}`：{len(files)} 个文件，{kb(size)} → `src/examcard/{pkg.replace('_v2','')}/`（只改 import 路径）")
    w("")

    w("## 五-2、scripts/tools 构成")
    w("")
    w(f"- 共 {len(tools_all)} 个 .py；其中 **{len(tools_phase)} 个是 phase/v2 伴生工具**"
      "（reconcile_phase2r*/preflight_phase2r* 等）→ 全部留旧仓，与对应 phase 同进退；")
    w(f"- 其余 {len(tools_v1)} 个为 V1 生产工具（full_check/reconcile/safe_writeback 等）→ 随 V1 生产留守旧仓；")
    w("- `export_v2_schemas.py` 不迁移：新仓改为自动发现 BaseModel 子类导出，替代手工注册。")
    w("- ⚠ tools/* 全部留旧仓，是 RC2 里大量 phase 伴生测试无法干净迁走的主因（见下）。")
    w("")

    w(f"## 六、测试归属（RC2·import 闭包判定；扫描到与 v2 子系统相关的测试共 {relevant_n} 个）")
    w("")
    w(f"### 随核心迁移（依赖闭包在新仓自洽，{len(tests_migrate)} 个）")
    w("")
    w("判据：只引用迁走的 knowledge_v2 模块（core/current/current-runtime）或 evidence_v2/materials_v2，"
      "且**不**引用 tools/*、留仓 phase/historical、fact_check_lib/prompts/legacy。")
    w("")
    for rel, det in tests_migrate:
        deps = det["mig_kv2"] + [p for p in det["mig_pkg"]]
        w(f"- `{rel}`（引用：{', '.join(deps[:5])}{' …' if len(deps) > 5 else ''}）")
    w("")
    w(f"### 留旧仓（还依赖留仓物，随伴生 phase/tools 同进退，{len(tests_stay)} 个）")
    w("")
    w("以下测试虽然触及迁走代码，但仍依赖留旧仓的东西（tools/* 运行器、留仓 phase/historical 模块等），"
      "搬到新仓会 import 失败，故随其伴生物留旧仓：")
    w("")
    w("| 测试 | 卡住原因(留仓依赖) |")
    w("|---|---|")
    for rel, det in tests_stay:
        blockers = []
        if det["stay_pkg"]:
            blockers.append("pkg:" + ",".join(det["stay_pkg"]))
        if det["stay_kv2"]:
            blockers.append("kv2:" + ",".join(det["stay_kv2"][:4]) + ("…" if len(det["stay_kv2"]) > 4 else ""))
        w(f"| `{Path(rel).name}` | {'; '.join(blockers)} |")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    # 七、修订留痕
    w("## 七、修订说明与复核留痕（v2/v3）")
    w("")
    w("### RC1 · archive 安全性修正")
    w(f"- 迁走集 import 闭包自动上提的 phase 模块（{len(pulled)} 个）：`{'`, `'.join(pulled)}`。")
    w("- 触发边：`v25_teaching_gate_layers`（现役契约）第 8 行 `from .phase2r142_v25_projection import "
      "V25GoldenTargetState, V25ResponseGoldenProjectionBundle`。")
    w("- 旧版把这些判为 archive/留旧仓 → 新仓 `contracts` 包会 import 断。v2 归入 `current-runtime` 随现役契约物理迁走。")
    w("")
    w("### RC2 · 测试归属修正")
    w(f"- 相关测试 {relevant_n} 个：随核心迁移 **{len(tests_migrate)}**、留旧仓 **{len(tests_stay)}**。")
    w("- 旧版规则“引用任一 core 即迁走”把大量只借类型的 phase 伴生测试误判为迁走（其真实依赖 tools/*、"
      "phase、historical 全部留旧仓，开箱即挂）。v2 改为 import 闭包判定，并纳入 evidence_v2/materials_v2。")
    w("")
    w("### RC3 · 两处路由修正 + 扫描盲区（n4 施工实测回填，2026-08-12）")
    w("- 路由修正 ①：`learning_fixture` 由 v2 的 `tests/golden/` 改落 `src/examcard/knowledge/learning_fixture.py`。")
    w("  实测其被 V25 运行时闭包五模块**顶层 import**（`phase2r11_generic_five_golden` / `phase2r12_v25_golden_review` / "
      "`phase2r13_v25_reconciliation` / `phase2r14_v25_live` / `phase2r142_v25_projection`，均迁 `knowledge/contracts/`）；"
      "若落 tests/（不随包安装、不在包内），新仓 contracts 包 import 即断。")
    w("- 路由修正 ②：`step2b_card_golden` 由 v2 的 `tests/golden/` 改落 `src/examcard/knowledge/step2b_card_golden.py`。")
    w("  实测其被 `table_continuation`（新仓 `continuation/core.py`）**函数内缩进式惰性 import**"
      "（旧仓 `table_continuation.py` 第 525 行，原地注释：为避免模块级循环依赖而局部导入），同属运行时闭包。")
    w("- 扫描盲区留痕（供后续清点/复核者防坑）：")
    w("  - **缩进式惰性 import**（函数体内 `from .x import y`）：行首锚定的正则会漏检。本生成器 `scan_kv2_imports` 的"
      "正则未锚定行首、实际可扫到（step2b_card_golden 的引用计数即含此边）；v2 误路由的根因是 `CORE_TARGETS` "
      "手工路由草案未消费该闭包信号，v3 已修正路由。")
    w("  - **动态字符串导入**（`__import__(\"knowledge_v2.x\")` / `importlib.import_module(\"knowledge_v2.x\")`）："
      "一切 from/import 文本正则均扫不到。实例：`phase2r14_v25_live` 第 336 行 "
      "`__import__(\"knowledge_v2.v25_generic_contracts\", fromlist=[…])`；因该模块第 55 行另有同目标的顶层静态 import，"
      "本例未造成闭包漏边。v3 生成器已补字符串字面量检测兜底（`_imp_dyn`）。")
    w("- 兜底检测的实际影响（v2 → v3 复扫对比）：**模块分类与迁走/留仓集合零变化**（RC1 上提五模块不变）；"
      "新浮出 2 个此前完全不可见的『留旧仓』测试——`phase2r1110_historical_pytest_compat.py`、"
      "`phase2r1191_historical_pytest_compat.py`（均经 `importlib.import_module` 动态引用留仓 phase 模块），"
      "相关测试总数 171 → 173（迁走 21 不变，留旧仓 150 → 152）。")
    w("")
    w("### A 案 · 跨桶数据模型下沉注解（总指挥指派，2026-08-11）")
    annotated = sorted(n for n in mods if notes.get(n))
    w(f"- 依据 `docs/design/distill_gates_review_design.md` §4「当前生产形态清单」/§5 契约 2·6：以下 {len(annotated)} 个 "
      "distill 模块的**跨桶消费数据模型**下沉 `knowledge/contracts/admission_fixtures.py`，法定哈希搬运件 "
      "`admission_legacy_hash.py` 随之下沉（铁律 bc-b8b9aca6：逐字节原样、禁入 common/hashing）；其判定/编译逻辑仍归各自桶。")
    for n in annotated:
        w(f"  - `{n}`（{dests[n]}）{notes[n].lstrip('；')}")
    w("- 该注解只补『去向』列语义，不改任何模块的迁走/留仓归类，故自检两条不变式不受影响。")
    w("")
    w("### 自检")
    w("- `migration/verify_manifest_audit.py` / `verify_manifest_audit2.py` 复用本文件的 `build_classification`，"
      "断言：①无迁走模块 import 留仓模块（archive 安全）；②每个『随核心迁移』测试的依赖闭包在新仓自洽。")
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {OUT}")
    print(f"modules={len(rows)} "
          f"core={len(groups.get('core', []))} current={len(groups.get('core-current', []))} "
          f"current-runtime={len(groups.get('current-runtime', []))} "
          f"historical={len(groups.get('historical', []))} phase={len(groups.get('phase', []))}")
    print(f"RC1 pulled={pulled}")
    print(f"tests: relevant={relevant_n} migrate={len(tests_migrate)} stay={len(tests_stay)}")


if __name__ == "__main__":
    main()
