# 新考试接入手册（Runbook · 以一级造价 / 消防工程师为例）

- 日期：2026-08-12；适用仓：exam-card-engine（阶段 2 基线之后）
- 更新：2026-08-13（T5 文档沉淀）——§8 扩写 live 生产配方四件套 / 两中继能力边界矩阵 / `relay_preflight` 前置检查 / `.env` 双通道键约定；并入漂移核查 N1–N6 修订（依据 `docs/prework/P3_runbook漂移核查_20260812.md`，该核查对本手册 §2–§8 做过 41 项逐符号实测：0 硬漂移）
- 方法论来源：旧仓《Step1_Step2_V2完整重构方案》§16（黄金样本）、§其他（红线），本仓 `configs/v2/` 既有模板与公路（gonglu）全套实例
- 铁律：**引擎代码零改动接入新考试——引擎只认 Material Profile**；不允许为单一考试修改 Evidence/Fact/Card 核心字段（方案红线）；一建公路的阈值/断言不复用为新考试的无条件真值（方案 §16.1）

## 0. 引擎能力面（接入前须知）

| 能力 | 入口（新仓） | 网络 |
|---|---|---|
| Step1 证据引擎（PDF→页面证据+冲突+缓存） | `examcard.evidence.ingest.EvidenceEngine` / `examcard.evidence.golden_runner.run_golden_registry` | 离线（vision 复核可选外呼） |
| Step2 事实链（证据→Fact/Card+质量门禁） | `examcard.knowledge.fact_engine.run_golden_fact_pipeline` | 纯离线、字节可复现 |
| Step2 语义链（V25 现役契约+黄金复核） | `examcard.knowledge.contracts.*`（phase2 契约为 r11/r12/r13/r14/r142 五模块闭包，非连续席位） | 离线复验；live 重放需 provider |
| Step3/Step4 编译与供应商兼容 | `examcard.compilers.*` + `examcard.providers.*` | live 需中继（chat/image 信封画像见 providers 设计稿） |
| 字节级验收安全网 | 仿 `migration/golden_five_replay.py` | 纯离线 |

冷克隆语义（T5）：`data/`、`input_pdf_*/`、`docs/v2/baselines/` 为 gitignore 大资产，缺席时资产依赖测试整组优雅 skip——新考试资产同样遵循此约定。

## 1. exams/ 工作区目录约定（代码与数据分仓）

新考试的一切资料/产物放 `exams/<exam_id>/`（仓根下），引擎源码树（src/）零改动：

```
exam-card-engine/
├── src/…                          # 引擎（不因考试而改）
├── configs/v2/
│   ├── material_profiles/<exam_id>.yaml      # ★唯一必须新增的“接入点”
│   ├── content_policies/                     # 复用 core_only_v1 / question_bank_v1，或新增
│   └── *_sidecars/                           # 人工评审侧车（按 registry 命名空间新增文件）
├── exams/
│   └── zaojia_2026/               # 例：一级造价 2026（xiaofang_2026 同构）
│       ├── input_pdf/             # 源 PDF（gitignore）
│       ├── data/evidence_v2/      # Step1 产物+缓存（gitignore）
│       ├── data/facts_v2/         # Step2 产物（gitignore）
│       └── fixtures/
│           ├── golden_samples/<exam_id>_phase0.yaml       # 黄金注册表（入库）
│           └── golden_annotations/<exam_id>_phase0_v1.yaml # 人工标注侧车（入库）
└── .gitignore                     # 追加 /exams/*/input_pdf/ 与 /exams/*/data/
```

约束（源自 fact_engine 的仓根锚定契约）：**一切持久化引用路径必须是仓根相对路径**（如 diagram review 的 crop manifest）；exams/ 在仓根下即天然满足。注册表/侧车（小体量、人工签字物）入库，PDF 与运行产物不入库。

## 2. 第一步：Material Profile（唯一硬接入点）

复制 `configs/v2/material_profiles/exam_profile.template.yaml`，逐字段填写。造价示例：

```yaml
schema_version: "2.0"
profile_id: zaojia_jishu_2026          # 命名：<考试>_<科目>_<版次>
revision: 1
display_name: 2026 一级造价工程师 技术与计量（土建）
collection_id: zaojia/2026/jishu_tujian
exam_family: 一级造价工程师
exam_level: 一级
subject_id: jishu_tujian
subject_name: 建设工程技术与计量（土木建筑工程）
source_kind: textbook                  # 枚举：textbook/lecture_note/regulation/standard/question_bank/past_exam/slide_deck/other
source_language: zh-CN
edition: "2026"
jurisdiction: CN
effective_window: {}                   # 法规/标准类科目必须填 effective_from/standard_id（消防的规范卷尤其）
content_policy_id: core_only_v1        # 首接建议复用；题库另用 question_bank_v1
content_policy_revision: 2
layout_profile_id: textbook_cn_v1      # 版式不同于公路讲义（haixue_textbook_cn_v1）时新起 id
taxonomy_profile_id: zaojia_jishu_v1
validation_profile_id: cost_engineering_cn_v1
card_profile_id: knowledge_diagram_v1
dedup_scope: within_collection         # 红线：跨 collection 决不自动合并
```

消防差异点：`exam_family: 一级注册消防工程师`；规范类分册 `source_kind: regulation` 且 `effective_window.standard_id` 必填（如 GB 50016 版次）；`validation_profile_id: fire_protection_cn_v1`。

校验：`uv run python -c "from pathlib import Path; from examcard.materials.catalog import load_material_profile; print(load_material_profile(Path('configs/v2/material_profiles/zaojia_jishu_2026.yaml')).profile_id)"`——`MaterialProfile` 模型（`materials/schema.py`）会强校验字段与枚举。

> 模板同步注记（漂移核查 N6，T5 已落修 20260813）：`exam_profile.template.yaml` 的 `content_policy_revision` 已同步为现行 `core_only_v1.yaml` 的 `revision: 2`（与本节示例一致）。今后 content policy 改版时，模板与本节示例须一并同步；沿用其他 policy 时以该 policy 文件内的 `revision` 实值为准。

> 模板陈旧项提醒（漂移核查 N6）：`exam_profile.template.yaml` 里 `content_policy_revision: 1` 可能落后于现行 policy 文件（`core_only_v1.yaml` 现为 `revision: 2`，本节示例已用 2）——复制模板后须对照所选 content policy 文件核对 revision 再提交。

## 3. 第二步：黄金样本（方案 §16.1 方法论，逐考试重建）

**规模基准**：5 份 PDF、约 20 页（公路 phase0 口径）。选样必须覆盖方案 §16.1 清单：普通正文、多级枚举、跨页列表、表格及合并单元格、公式/单位/上下限、流程图和思维导图、原生文本缺失、native/OCR 冲突、水印页眉页脚、例题与正文混排、同标题不同条件的相似知识点。

考试特异性选样建议（在通用清单之上）：
- **造价**：费用构成计算表（多层合计行）、指标单位链（元/m²、工日）、计量规则条文枚举、造价流程图（招投标/结算流程）——对应公路的 `gonglu_24_table_formula`/`gonglu_62_cost_flow_incident` 形态；
- **消防**：设施参数表（喷淋/消火栓间距上下限）、规范条文强制性枚举（"应/宜/严禁"语义）、系统原理图（供水/联动）、防火分区示意——对应 `gonglu_41_diagram_mixed`/`gonglu_55_standard_constraints` 形态。

**注册表**（`exams/<exam_id>/fixtures/golden_samples/<exam_id>_phase0.yaml`，结构照抄 `scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml`）：顶层三字段照抄样例——`schema_version: "2.0"`、`registry_id: <exam_id>_phase0_v1`（**与 §4 的 output_root / §6 的 evidence_root 路径互锁**，引擎侧一律用 `registry.registry_id` 拼路径、勿手写字面量）、`material_profile_id` **必须等于**第一步的 `profile_id`（`run_golden_registry` 与 `run_golden_fact_pipeline` 两处强校验，不符直接拒绝——漂移核查 N1）。每样本记录 `sample_id / pdf_relative_path（仓根相对）/ pdf_sha256 / selected_pages / focus / assertions / annotation_status / reviewer / reviewed_at_utc`。断言写"已知事实"（expected_evidence_kinds + expected_matches 的 text/kind/semantic_role/must_cover），如消防：`text: 喷头与端墙的距离不应大于…, kind: table_cell, must_cover: true`。

**状态机（门禁，不可绕过）**：`selected`（选定范围）→ `seeded`（写好断言未签字）→ `reviewed`（人工逐页标注签字）。全部 `reviewed` 才有 `ready_for_phase1=true`；Step2 事实链入口会拒绝未 reviewed 的注册表（`run_golden_fact_pipeline` 第一道校验）。

## 4. 第三步：Step1 证据运行

新仓等价调用（旧仓 `run_evidence_v2.py --golden` 的库内形态；建议存为 `exams/<exam_id>/tools/run_evidence.py`）：

```python
from pathlib import Path
from examcard.evidence.golden import load_golden_registry
from examcard.evidence.golden_runner import run_golden_registry
from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from examcard.materials.catalog import load_material_profile

ROOT = Path(__file__).resolve().parents[3]          # 仓根
EXAM = ROOT / "exams/zaojia_2026"
profile = load_material_profile(ROOT / "configs/v2/material_profiles/zaojia_jishu_2026.yaml")
engine = EvidenceEngine(
    profile=profile,
    cache_root=EXAM / "data/evidence_v2/cache",
    config=EvidenceEngineConfig(render_dpi=200),
    vision_verifier=None,                            # 首跑建议 no-vision；vision 复核见下
    cache_enabled=True,
)
registry = load_golden_registry(EXAM / "fixtures/golden_samples/zaojia_2026_phase0.yaml")
report = run_golden_registry(
    workspace_root=ROOT, registry=registry, profile=profile, engine=engine,
    output_root=EXAM / "data/evidence_v2/golden" / registry.registry_id,
)
```

要点：`pdf_relative_path` 相对仓根解析（PDF 放 `exams/<exam_id>/input_pdf/` 并在注册表写全相对路径）；缓存键内含 profile id/revision 与引擎配置（改配置自动失效）；vision 复核（native/OCR 冲突仲裁）在新考试没有 pinned fixture——要么 no-vision 先出证据、冲突走人工，要么配 `OpenAIVisionVerifier`（真外呼，走 T6 环境变量纪律）。

## 5. 第四步：人工评审侧车（configs/v2 九族，按需逐族补）

| 侧车目录 | 作用 | 何时需要 |
|---|---|---|
| `golden_annotations/`（exams/ 下） | 黄金页人工标注/冲突裁决（Step2 必需） | 必需 |
| `diagram_review_sidecars/` | 图示结构人工复核（节点/边/裁片） | 有流程图/原理图样本 |
| `diagram_disposition_sidecars/` | 剩余 draft 图示的处置（photo_example 等） | 同上 |
| `table_repair_sidecars/` | 表格结构人工修复 | 合并单元格/断表 |
| `table_continuation_sidecars/` | 跨页表续接 | 跨页表格 |
| `continuation_review_sidecars/` | 续接人工复核 | 同上 |
| `card_grouping_review_sidecars/` | 卡片编组复核（V25 链） | 进入 Step2B 后 |
| `watermark_review_sidecars/` | 水印判定复核 | 水印教材 |
| `content_policies/` | 收录策略 | 复用或新增 |

命名带上考试命名空间（`zaojia_2026_phase0_v1.*.yaml`）。diagram review 的高清裁片 manifest 路径必须仓根相对（fact_engine 逐哈希校验其存在与一致）。

## 6. 第五步：Step2 事实链（纯离线、字节可复现）

```python
from examcard.knowledge.fact_engine import run_golden_fact_pipeline

summary = run_golden_fact_pipeline(
    registry_path=EXAM / "fixtures/golden_samples/zaojia_2026_phase0.yaml",
    sidecar_path=EXAM / "fixtures/golden_annotations/zaojia_2026_phase0_v1.yaml",
    content_policy_path=ROOT / "configs/v2/content_policies/core_only_v1.yaml",
    evidence_root=EXAM / "data/evidence_v2/golden/zaojia_2026_phase0_v1",
    output_root=EXAM / "data/facts_v2/golden/zaojia_2026_phase0_v1/core_only_v1",
    diagram_review_sidecar_path=…,       # 有图示样本时必传（否则图示 KP 不能 verified）
    diagram_disposition_sidecar_path=…,
)
assert summary["quality_gates_passed"] and summary["machine_assertions_passed"]
```

产物：每样本 `fact_run.json` + 总 `golden_fact_run_report.json`（内容寻址 fact:/card:/ev: ID，零墙钟、逐字节可复现）。质量门禁含 must-cover 覆盖率 100%（方案验收口径）。

## 7. 第六步：验收安全网（每考试自建基准）

1. 事实链跑绿后，把 N+1 个产物文件（N 个 `<sample_id>/fact_run.json` + 1 个 `golden_fact_run_report.json`；黄金五章口径即 6 个——漂移核查 N2）的 SHA256 记录为该考试基准（仿 `migration/golden_five_replay.py` 的 A 线写一个 `exams/<exam_id>/tools/replay_check.py`）；
2. 学习黄金（Step2 语义链）：照 `scripts/tests/fixtures/v2/learning_golden/` 形态建 draft 学习 fixture，`examcard.knowledge.learning_fixture.validate_learning_fixture` 复验锚定（D 线形态）。注意该函数收**已加载对象**不收路径（漂移核查 N3）：`validate_learning_fixture(fixture=load_learning_golden_fixture(fixture_path), registry=load_golden_registry(registry_path), evidence_root=…, diagram_sidecar_path=…)`；
3. 新考试的回归测试放 `tests/`（带 T5 金样镜像守护：资产缺席 skipif——既有 17 个金样守护文件 = 16 个 `_GOLDEN_MIRROR_REQUIRED` 文件级守护 + 1 个 v1 适配器样本守护，另有 1 个旧仓语料回放守护不计其内，漂移核查 N4），断言注册表口径的已知事实。

## 8. Step2 语义链 / Step3 / Step4（live 阶段）

- V25 语义链复用 `examcard.knowledge.contracts`（现役契约 + 黄金复核闭包；phase2 契约为 r11/r12/r13/r14/r142 **五模块**：`phase2r11_generic_five_golden` / `phase2r12_v25_golden_review` / `phase2r13_v25_reconciliation` / `phase2r14_v25_live` / `phase2r142_v25_projection`——端点缩写「r11–142」易误读为连续席位，漂移核查 N5）；新考试的 V25 黄金复核需要自己的 reviewed 学习 fixture 与图示语义 sidecar（对照 r12 的 `_TARGET_*` 常量族形态）。
- live 外呼一律走 providers 层契约：chat 走 `inspect_chat_envelope`（画像/白名单双模式）、图像走 `StrictImageAdapter` + 公网五字段投影（内部字段决不进 HTTP 体——r116 教训）；预算化、禁自动重试、失败身份不复用（providers 设计稿 12 不变式）。
- step3 自动出卡走 **v2 单卡回显契约**（`compilers/step3_response_v2.py`，T3 落地；r810 六卡 live 实测 6/6 首发全过——`P3_step3v2实测_20260812.md`）；v1 三卡整体回显在两中继三路径已证伪，仅存量重放使用。
- 测试纪律：live 用例标 `@pytest.mark.live`（默认剔除，T4 禁网守卫）；密钥走环境变量（T6，`.env` 不入库）；本机 pytest 环境修复指引见 `engineering_skeleton.md` §5 附注。

### 8.1 生产配方四件套（step2a/step2b live 定稿口径，20260812 实测）

**配方一行**：`2chat + gpt-5.6-sol + 补强指令 + SSE 流式 + 失败章自动重发`。四件套与引擎入口：

| 件 | 引擎入口（新仓实件） | 要点 |
|---|---|---|
| 模型 | `.env` `TEXT_MODEL=gpt-5.6-sol` | 5.5→5.6-sol 对比复测：噪声减半、断言整洁度与聚合率显著提升（深评判级 C→B-，补强后 A-/B+） |
| 补强指令 | `knowledge/reinforcement.py` 的 `STEP2A_REINFORCEMENT_V1`，经 `OpenAICompatibleStep2Transport(system_suffix=…)` 注入 | 五条硬性要求（考情元数据禁产/禁拆行/忽略水印散字/diagram 必产/宁缺毋滥）；**传输层注入，V25 冻结模板零改动、不进请求 identity**；运行报告须登记 `reinforcement_id`（常量 `STEP2A_REINFORCEMENT_V1_ID`） |
| SSE 流式 | `OpenAICompatibleStep2Transport(stream=True)` | SSE 聚合 delta → 与非流式同形 `TransportCompletion`（保留 provider id / status / `finish_reason` 审计字段，中途掐断可辨）；把网关掐断条件从「总时长 ~125s」改善为「首字节」；同样不进 identity，journal 登记 `streaming_transport` |
| 失败重发 | `pipeline/live_run.py` 的 `ResendPlan(base_reason=…, max_attempts=3)` | 首发 attempt=1 用 base reason 原文；重发派生 `<base>-rN` **新身份**（`manual_reissue_reason` 参与 identity 摘要）；每（样本,轮次）只签发一次、失败身份结构上禁复用；轮数耗尽抛 `ResendRoundsExhausted`，升级处置而非硬重试 |

数值口径（`P2_pilot_step2a质量深评_20260812.md`）：r5 补强轮考情/碎片/水印三类噪声**全部归零**、三个 diagram 基准目标首次全部命中；62 章流式 r4 重发一次通过（step2a 79.6s / step2b 8.8s / 噪声 0/0/0，与非流式补强轮同水位）；全量跑按**首发 ~60%、重发累计 >95%** 预留重发预算与时长。

### 8.2 两中继能力边界矩阵（T1 `relay_preflight` 实测 + pilot 口径，20260812/13）

| 能力 | 2chat（文本生产通道） | chatgpt2api-new（127.0.0.1:3100，图像通道） |
|---|---|---|
| 请求体积 | 无上限区间（实测未触顶） | **[40,70)KB 上限**——step2a ≥300KB 需求不满足 |
| strict json_schema | **强制解码**（T1 实测确认） | 不强制（空数组逃逸史）——step3 不可走 |
| 首字节窗口 | 非流式 ~125s 被网关掐断；**流式可穿越**（182s/244s 存活实证） | —（体积先不达标） |
| 图像端点 | 未配（图像不走此通道） | 可用（16/16 出图 + 修复轨 6 卡复拍）；**20260813 信封漂移**：`data[0]` 新增 `width/height` 键触发严格门禁 FAIL，处置=良性剥离后复门禁、逐卡审计留痕 |
| 结论 | step2a / step2b / step3(v2 单卡) 绿灯 | 仅 step4 图像通道；跑前必须 `--channel image` 预检 |

### 8.3 `relay_preflight` 前置检查（接入新中继 / 换模型 / 图像通道变更时必跑）

```bash
uv run python migration/relay_preflight.py --dry-run --channel last                    # 零外呼自检（打印探针装配）
uv run python migration/relay_preflight.py --allow-live --channel last --deep         # 文本通道全测（2chat）
uv run python migration/relay_preflight.py --allow-live --channel image --with-image  # 图像通道（IMAGE_API_* 显式键）
```

- 库调用（T4b 驱动同款）：`run_preflight(dry_run=…, channel=…) -> report`，机器判定 `report["requirement_conclusions"][{step2a,step3_v2,step4}]["verdict_code"] ∈ {pass, likely_pass, fail, unknown}`；报告落 `report["report_path"]`（`data/relay_preflight/<label>/relay_preflight_report.json`，dry-run 不覆写 live 报告）；配置错误抛 `SystemExit`（带中文说明），预算熔断不抛出、落 `report["stop_reason"]`。
- 需求画像（`REQUIREMENT_PROFILE`）：step2a 请求体 ≥300KB；step3_v2 = strict 强制 +（首字节窗口 ≥150s 或流式可续）；step4 = 图像端点可用。
- 费用：默认口径 ≤12 次小调用 ≈$0.1；`--deep --with-image` 全开 ≈$0.3-0.5。

### 8.4 `.env` 双通道键约定（新规：**运行配置在新仓，原仓 yijian2 零接触**）

| 通道 | 键 | 取值语义 |
|---|---|---|
| 文本 | `API_BASE_URL` / `API_KEY` / `TEXT_MODEL` | 新仓 `.env` 通用键**只留一对**（=文本通道；现值 2chat + gpt-5.6-sol） |
| 图像 | `IMAGE_API_BASE_URL` / `IMAGE_API_KEY` / `IMAGE_MODEL` | **显式键优先**；显式键缺席回落文本通道同源，且驱动报告必须登记 `image_channel_fallback: true`（图像误走文本中继是已踩过的坑） |
| 可选 | `OPENAI_USER_AGENT` | 存在则入 default_headers（现行 pilot 口径） |

- 读取入口：`pipeline/live_run.py` 的 `load_run_env(env_file=None)`——缺省读新仓根 `exam-card-engine/.env`（gitignore 覆盖，T6 密钥不入库）；`--env-file` 可显式指定（含迁移过渡期指回旧仓路径的用法）。配套双通道取值函数：`text_base_url / text_api_key / image_base_url / image_api_key / image_channel_fallback`。
- 密钥纪律：值仅驻内存——不打印、不落盘、不入报告与日志。
- 教训登记：图像显式键曾被 `.env` 后续编辑覆盖丢失（`P3_未来就绪审计与优化路线图_20260812.md` §1.2）——step4 出图前用 §8.3 预检确认通道形态，勿凭记忆。

## 9. 红线清单（违者返工）

1. 不修改 Evidence/Fact/Card 核心 schema 字段来适配单一考试（允许：新增 profile/术语表/版式插件/黄金样本）。
2. 一建公路阈值与断言不得直接复用为新考试真值——每考试重建版式黄金样本。
3. `ready_for_phase1` 门禁不得用参数绕过；未 reviewed 不进 Step2。
4. 跨 collection 决不自动合并（`dedup_scope` 红线）。
5. 冻结哈希铁律：决不改动法定哈希实现（identity/_canonical/_digest、canonical_step2_sha256、stable_config_hash）；新考试 ID 稳定性依赖于此。
6. yijian2 旧仓只读；新考试资产只进本仓 `exams/` 约定位置。

## 10. 接入完成定义（DoD）

- [ ] Material Profile 过 `load_material_profile` 校验，profile_id/collection_id 命名合规
- [ ] 黄金注册表 5 PDF/约 20 页、覆盖 §16.1 清单 + 考试特异形态，全样本 `reviewed`
- [ ] Step1 证据跑绿（seeded assertions 全过），冲突有人工裁决路径
- [ ] Step2 事实链 `quality_gates_passed=true`、must-cover 100%
- [ ] 产物 SHA256 基准落档 + 重放脚本可复跑字节全等
- [ ] 新增回归测试入 `tests/`（带 T5 守护），冷克隆不破坏（skip 而非 fail）
- [ ] 全仓 `uv run pytest` 保持全绿
