"""gates.review：人工审阅准入门禁（自 27 个 gates-review phase 提炼，见 manifest 4.3 节）。

内核结构（distill_gates_review_design §2；准入流程：草稿→待审→人工决策→严格摄取→
显式授权→admitted）：

- dispositions：四态处置 + admission_basis + 处置守恒；
- queue：队列项基类 + queue_sha256 哈希链；
- contracts：typed contract 绑定校验（模型按 A 案在 knowledge/contracts/admission_fixtures）；
- decisions：r84 权威人工决策/清单（严格摄取入口，生产 schema_version 保真）；
- authorization：r810 显式生产授权 → 导出 r84 清单（含守恒投影）；
- browser：非权威浏览器界（pending 哈希草稿信封 / r82 分类 / 进度快照）；
- audit：决策审计与 browser demo 不可授权不变量；
- fixtures：r84 reviewed fixture 族与构建器（跨桶模型向下引用）;
- release_merge：r2242 双层合并判定与发布草稿投影；
- image_admission：r11101 图片目检准入全套状态机；
- handoff：pilot handoff 禁带字段判定；
- regression / gate / report：fail-closed 回归矩阵、聚合门禁与报告物化骨架
  （新代码域，基元与冻结机器消费 common.gates / common.junit / common.io / common.freeze）。

旧契约自哈希一律绑 knowledge/contracts/admission_legacy_hash（铁律 bc-b8b9aca6）；
新产出封印一律走 examcard.common.hashing，两个哈希域不掺混。
"""
from __future__ import annotations

from . import (
    audit,
    authorization,
    browser,
    contracts,
    decisions,
    dispositions,
    fixtures,
    gate,
    handoff,
    image_admission,
    queue,
    regression,
    release_merge,
    report,
)

__all__ = (
    "audit",
    "authorization",
    "browser",
    "contracts",
    "decisions",
    "dispositions",
    "fixtures",
    "gate",
    "handoff",
    "image_admission",
    "queue",
    "regression",
    "release_merge",
    "report",
)
