# P3 · 黄金 5 章离线重放·字节级平价验收报告

- 日期：2026-08-12；执行仓：exam-card-engine（新仓引擎 examcard.*）；基准仓：yijian2（只读）
- 方法：docs/prework/P2_黄金样本与字节级验收资产清点_20260811.md §五；重放脚本 migration/golden_five_replay.py（本文件同批提交，可复跑）
- 纪律：零网络；yijian2 零写入；重放产物在新仓 .test-tmp/golden_replay/（临时，不入库）

## 总判定：✅ 全部通过

## A. 事实链重放·字节对拍（run_golden_fact_pipeline，含 fact:/card:/ev: 内容寻址 ID）

| 文件 | 基准 SHA256 | 重放 SHA256 | 字节一致 |
|---|---|---|---|
| gonglu_01_text_list/fact_run.json | 40254d1f6c7919bd… | 40254d1f6c7919bd… | ✅ |
| gonglu_24_table_formula/fact_run.json | 3cd91a8359ee7c70… | 3cd91a8359ee7c70… | ✅ |
| gonglu_41_diagram_mixed/fact_run.json | d1620bcf33a51b74… | d1620bcf33a51b74… | ✅ |
| gonglu_55_standard_constraints/fact_run.json | b8816c24d0d82c31… | b8816c24d0d82c31… | ✅ |
| gonglu_62_cost_flow_incident/fact_run.json | 3098756ad4278dc9… | 3098756ad4278dc9… | ✅ |
| golden_fact_run_report.json | e97fe7a406cf2d04… | e97fe7a406cf2d04… | ✅ |

输入（旧仓只读）：golden registry + reviewed annotation sidecar + core_only_v1 策略 + 20 页黄金证据 + 基准报告记录的两个 diagram sidecar（phase25f_structural_reviewed / phase25f_remaining_audited）。

## B. 证据内容寻址 ID 零漂移（identity._canonical/_digest 七族重导）

- 页面 20，EvidenceUnit 1501，SourceConflict 86
- 重导 fingerprint / evidence_id / revision_id / document_id / page_id / conflict_id 与存量比对：7 个不一致；**迁移漂移 0 个**（判定法：对每个不一致，用旧仓 identity.py 原实现（逐字节搬运件，只读加载）对同一持久化输入重导——新旧实现结果相等 ⇒ 非迁移漂移，属旧数据谱系记录）

- 旧数据谱系登记（7 例，全部为 revision_id，fingerprint/evidence/document/page/conflict 五族 100% 零漂移）：fusion 后处理单元（table_row×5、三通道 paragraph×2）的 revision 构建期输入未随终态页面持久化，新旧实现对持久化输入的重导结果逐字节一致，与存量差异为旧仓既有事实，A 线字节全等证明其被原样消费：
  - gonglu_24_table_formula/page_0003.json:ev:ac140fc6c830e91e20566a2e（table_row，channels=layout,native_text）
  - gonglu_24_table_formula/page_0008.json:ev:d182dc81298341e02f3bffad（table_row，channels=layout,native_text）
  - gonglu_62_cost_flow_incident/page_0004.json:ev:e550e9c21f00dab70ba7ac97（paragraph，channels=native_text,vision_structure,visual_ocr）
  - gonglu_62_cost_flow_incident/page_0004.json:ev:dc2a8c461b5108746546f45f（paragraph，channels=native_text,vision_structure,visual_ocr）
  - gonglu_62_cost_flow_incident/page_0005.json:ev:1d5be6eb0dc3eacdb0c9b615（table_row，channels=layout,native_text）
  - gonglu_62_cost_flow_incident/page_0005.json:ev:5df756c56d8232d1123aae98（table_row，channels=layout,native_text）
  - gonglu_62_cost_flow_incident/page_0005.json:ev:03b2f7213fd1da520e1b2b47（table_row，channels=layout,native_text）

## C. cache_key / stable_config_hash 零漂移

- 配置谱系重建：按 run_evidence_v2 --golden 构造配方重建引擎级 config_hash，与 unit provenance 存量精确相等：**1499/1501**（覆盖冻结法定哈希 stable_config_hash 与 EvidenceEngineConfig/profile/provider 全配置面）；例外 2 例与 B 线谱系登记同源（另一次引擎运行铸造的 pinned cost-typo 视觉复核单元，provenance 保留铸造期 config_hash，非漂移）
- make_cache_key 新旧实现同输入平价（40 个页级键输入：native=pymupdf_rawdict + ocr）：**40/40**
- 说明：旧仓 data/evidence_v2/cache 为单页评审与 vision 缓存（清点 §二-1），不含黄金 run 页级条目，「命中录制缓存文件」不构成判据；vision 裁片键含 hint 级嵌套哈希，其输入不在页面持久化面内。

## D. Step2 语义层·录制学习黄金重放（validate_learning_fixture）

- draft（gonglu_phase2r0_draft）：valid=True，pending=41（旧测试口径=41），errors=0 → ✅
- V25 reviewed（gonglu_phase2r12_v25_reviewed × r12 语义 sidecar）：valid=True，errors=0 → ✅

## 备注

- 游离目录 `data/semantic_llm_v11101_...run1`（清点 §四-2 提醒）：属 Step4 人工评审基准，不在 Step2 重放输入面内，留旧仓原位即可，本验收已核对其存在、未迁移无影响。
- request_id 侧：Step2 请求身份由 canonical_step2_sha256（contracts/hashing 逐字节搬运件）派生，其字节行为已由 A 线（fact 链产物哈希全等）与 D 线（录制 fixture 锚定复验零误差）间接钉死；Step3 级请求 identity 重放依赖 phase 运行器（按清单留旧仓），不在新仓重放面内。
