# -*- coding: utf-8 -*-
"""重生成后自检 #1（只读）：断言 extraction_manifest 的两条硬不变式。

复用 gen_extraction_manifest.build_classification 做单一事实源，避免与生成器分类漂移。

INV-A（archive 安全）：没有任何『迁走』模块(core/core-current/current-runtime)
        import 到『留旧仓』模块(phase/historical) —— 否则新仓 import 断。
INV-B（测试闭包）：每个判『随核心迁移』的测试，其依赖闭包都能在新仓满足
        （不 import 留仓 kv2 模块，也不 import tools/*、fact_check_lib/prompts/legacy）。

违反任一不变式 → 退出码 1。供重生成清单后 CI/人工回归。
"""
from __future__ import annotations

import sys
from collections import defaultdict

import gen_extraction_manifest as G

C = G.build_classification()
kinds = C["kinds"]
MIGR = {n for n, k in kinds.items() if k in G.MIGRATE_KINDS}
STAY = {n for n, k in kinds.items() if k in G.STAY_KINDS}

print("=" * 70)
print("计数：", {k: sum(1 for v in kinds.values() if v == k) for k in
               ("core", "core-current", "current-runtime", "historical", "phase")})
print(f"迁走={len(MIGR)} 留仓={len(STAY)}  RC1 上提={C['pulled']}")

# ---- INV-A ----
print("=" * 70)
print("INV-A · archive 安全：迁走模块是否 import 留仓模块")
a_bad = []
for m in sorted(MIGR):
    for dep in sorted(C["fwd"].get(m, set())):
        if dep in STAY:
            a_bad.append((m, kinds[m], dep, kinds[dep]))
if a_bad:
    for s, sk, d, dk in a_bad:
        print(f"  [FAIL] {s} [{sk}] -> {d} [{dk}]")
else:
    print("  [PASS] 无迁走→留仓依赖边")

# ---- INV-B ----
print("=" * 70)
print("INV-B · 测试闭包：随核心迁移的测试不得依赖留仓物")
b_bad = []
mig_tests = stay_tests = 0
for rel in sorted(C["test_kv2"]):
    verdict, det = G.classify_test(rel, C)
    if verdict == "migrate":
        mig_tests += 1
        if det["stay_kv2"] or det["stay_pkg"]:
            b_bad.append((rel, det))
    elif verdict == "stay":
        stay_tests += 1
print(f"  migrate={mig_tests} stay={stay_tests}")
if b_bad:
    for rel, det in b_bad:
        print(f"  [FAIL] {rel}  stay_kv2={det['stay_kv2']} stay_pkg={det['stay_pkg']}")
else:
    print("  [PASS] 每个迁走测试的依赖闭包在新仓自洽")

# ---- 留仓测试的卡点分布（信息性）----
print("=" * 70)
print("留仓测试卡点分布（信息性）")
reason = defaultdict(int)
for rel in C["test_kv2"]:
    verdict, det = G.classify_test(rel, C)
    if verdict != "stay":
        continue
    if det["stay_pkg"]:
        reason["tools/其他留仓包"] += 1
    if any(kinds[m] == "historical" for m in det["stay_kv2"]):
        reason["historical 模块"] += 1
    if any(kinds[m] == "phase" for m in det["stay_kv2"]):
        reason["phase 模块"] += 1
for k, v in sorted(reason.items(), key=lambda kv: -kv[1]):
    print(f"  {k}: {v}")

ok = not a_bad and not b_bad
print("=" * 70)
print("RESULT:", "PASS ✅ 两条不变式均满足" if ok else "FAIL ❌ 存在违反")
sys.exit(0 if ok else 1)
