# -*- coding: utf-8 -*-
"""重生成后自检 #2（只读）：迁走测试的完整依赖闭包明细 + 留仓测试卡点清单。

复用 gen_extraction_manifest.build_classification 单一事实源。用于人工核对
『随核心迁移』集是否确实干净、以及每个留仓测试到底被什么留仓物卡住。
"""
from __future__ import annotations

import gen_extraction_manifest as G

C = G.build_classification()
kinds = C["kinds"]

mig, stay = [], []
for rel in sorted(C["test_kv2"]):
    verdict, det = G.classify_test(rel, C)
    if verdict == "migrate":
        mig.append((rel, det))
    elif verdict == "stay":
        stay.append((rel, det))

print(f"相关测试={len(mig) + len(stay)}  migrate={len(mig)}  stay={len(stay)}")

print("\n== 随核心迁移（依赖闭包在新仓自洽）==")
for rel, det in mig:
    deps = det["mig_kv2"] + det["mig_pkg"]
    print(f"  {rel}")
    print(f"      迁走依赖: {deps}")

print("\n== 留旧仓（被留仓物卡住，随伴生留旧仓）==")
for rel, det in stay:
    blk = []
    if det["stay_pkg"]:
        blk.append(f"pkg={det['stay_pkg']}")
    if det["stay_kv2"]:
        hist = [m for m in det["stay_kv2"] if kinds[m] == "historical"]
        phase = [m for m in det["stay_kv2"] if kinds[m] == "phase"]
        if hist:
            blk.append(f"historical={hist}")
        if phase:
            blk.append(f"phase={phase[:4]}{'…' if len(phase) > 4 else ''}")
    print(f"  {rel}  <=  {'; '.join(blk) if blk else '(仅引留仓，无迁走依赖)'}")
