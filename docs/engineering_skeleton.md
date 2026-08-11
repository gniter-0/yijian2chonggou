# exam-card-engine 工程骨架规范（P1）

生效日期：2026-08-11。本文是新仓的工程基线：pyproject 决策、目录职责与依赖方向、测试隔离规范（T1–T8）。
模块级迁移去向以 `docs/extraction_manifest.md` 为准，本文只定「骨架与规则」。

## 1. 定位与命名

| 项 | 取值 | 理由 |
|---|---|---|
| 仓库名 | `exam-card-engine` | 与 yijian2 平级的新仓 |
| 发行名 = 导入名 | `examcard` | 一词、无连字符，`import examcard` 与包名一致，免 build 映射配置 |
| 布局 | `src/examcard/` | src 布局强制「装了才能 import」，杜绝旧仓 `sys.path.insert(scripts)` 一类路径黑魔法 |
| 包管理 | uv（`uv.lock` 入库） | 与 yijian2 现状一致 |
| 构建后端 | `uv_build` | uv 原生后端，默认约定即 `src/<name>`，零配置 |
| 包模式 | 可安装包（editable，`uv sync` 自动装） | 与 yijian2 的关键差异：旧仓 `[tool.uv] package = false`，测试靠 sys.path 拼；新仓测试/脚本一律 `import examcard` |

## 2. Python 与依赖基线

- **requires-python：`>=3.11`（总指挥裁决上调，原 >=3.10）**。依据：semantic_contracts 已使用 StrEnum（3.11+ 特性），>=3.10 实际不可运行；旧仓 venv 实测运行 3.12.10。旧仓若将来以依赖方式引用本引擎，需同步上调自身下限。
- **开发解释器：`.python-version` = 3.12**。对齐 yijian2 实际 venv（3.12.10）；uv sync 自动取用，新仓 venv/CI 均用 3.12。
- **运行依赖 12 项**，下限与 yijian2 `requirements.txt` 逐项一致（PyMuPDF、openai、httpx、Pillow、PyYAML、python-dotenv、flask、pydantic、matplotlib、rapidocr-onnxruntime、opencv-python<5、numpy）。上限由 `uv.lock` 固定。
- **与旧仓的两处刻意差异**：
  1. `pytest` 移出运行依赖，进 `[dependency-groups] dev`（uv sync 默认安装，构建产物不再携带测试框架）；
  2. 不迁移 `export_v2_schemas.py` 的手工注册模式——新仓 schema 导出改为自动发现 BaseModel 子类（manifest 五-2 节的既定决策）。
- 升级依赖：`uv add <pkg>@latest`；同步：`uv sync`。禁止手改 `uv.lock`。

## 3. 目录职责与依赖方向

```
src/examcard/
  materials/            素材接入。原 scripts/materials_v2 整包（3 文件），仅改 import
  evidence/             类型化证据。原 scripts/evidence_v2 整包（17 文件），仅改 import
  knowledge/            语义知识内核（manifest 第一、二节的 19 核心模块 + V25 现役契约）
    schema.py fact_engine.py llm.py projection.py quality.py
    adapter_v1.py state_machine.py identity.py
    contracts/          semantic_contracts（380KB）在此拆分 + v25_generic_contracts + v25_teaching_gate_layers
    continuation/       core / integration / review / repair_sidecar
    diagram/            sidecar / disposition
    planner/            step2b bridge
  compilers/            Step3/4 编译器（manifest 4.1，4 个 phase 提炼；脚本本体留旧仓）
  gates/
    review/             人工准入门禁（manifest 4.3，27 个 phase 提炼）
    release/            发布就绪门禁（manifest 4.4，4 个 phase 提炼）
  pipeline/             四步流水线编排（出网走顶层 providers）
  providers/            供应商兼容层，顶层包（manifest 4.2，18 个 phase 提炼；桶内 chat/image/relay/transport 结构自主）
  webui/                flask 审阅工作台，仅 HTTP/模板/静态层
  common/               四 distill 桶共用冻结底座（六模块：hashing/gates/io/freeze/junit/schema，签名冻结）
  utils/                非底座跨包杂项工具（writeback/lineage/日志/table_render 等，不冻结）
tests/                  见第 4 节
docs/                   本文 + extraction_manifest.md
migration/              抽包一次性辅助脚本，不属于包，不参与测试发现
```

**依赖方向（只许向下，禁止回边）：**

```
业务层单向：webui → pipeline → { compilers, gates } → knowledge → evidence → materials
传输基础设施：providers 可被任意业务包 import（knowledge.llm / compilers / pipeline 三方共用）
底座（总指挥终裁冻结）：common 与 utils 均为叶包（不 import 任何内部包）；
providers 只可 import common + utils；业务包可 import providers + common + utils
```

- gates 只承载判定逻辑，webui 只承载 Web 层——旧仓 workbench phase 里"flask 路由 + 判定"混写的部分，迁移时按此拆开。
- **providers 落点（终裁·已决）**：升顶层 `src/examcard/providers/`。理由：providers 是 knowledge.llm / compilers / pipeline 三方共用的传输基础设施，且被 compilers 以回调注入而非反向 import，落 pipeline 之下会制造 `compilers → pipeline` 回边。`pipeline/providers/` 已移除。
- **共享底座（终裁·已决，裁决链以本条为最终口径）**：裁决历程 bc-97be0861（增设 common）→ bc-75c1f096（撤 common 统一 utils）→ **总指挥终裁：恢复双包并存**——`common/`（冻结底座，仅 hashing+gates 两件，签名按 providers 稿 §6.1/§9.1 冻结，增删/变更报总指挥）与 `utils/`（非底座杂项工具）各司其职，bc-75c1f096 中「不设 common、统一进 utils」条款撤销，终裁广播 **bc-888c6031【布局终裁·替代此前所有布局公告】** 已发布为全组最终口径。治理边界：冻结底座定式 → common；其余跨包杂项（writeback/lineage/日志等，不冻结）→ utils；两包均为叶包。一级子包据此锁定 **10 包**。
- **common 模块集扩容（已裁决·扩容终态，终裁广播 bc-9d82a196）**：初始冻结面为 hashing+gates 两件；经 compilers 设计者举证 io（atomic_write_json 等，5+ 份重复）/freeze（FrozenArtifact+verify，7 份）/junit（JUnitReceipt+parse，4 份）在历史 phase 反复重实现且四桶均需，总指挥批准全部上提，并增设 schema.py（strict_schema(model)），终版模块集 **{hashing, gates, io, freeze, junit, schema} 六件**。约束：只收通用（freeze 不含各 phase 路径 glob 集、不依赖 git）；hashing/gates 签名按 providers 稿 §6.1/§9.1，其余落地即冻；再变更需新终裁广播。包数不变仍 10 包。
- **requires-python 上调（已裁决·R1）**：`>=3.10` → `>=3.11`。依据 semantic_contracts 使用 StrEnum（3.11+）；uv.lock 已按新下限重锁，冒烟复验通过。详见 §2。
- **compilers ⇄ gates.review 依赖环拆解（已裁决·A 案，经 gates-review 转达登记）**：跨桶纯数据契约下沉 `knowledge/contracts/admission_fixtures.py`；其法定哈希搬运件一并下沉为 `knowledge/contracts/admission_legacy_hash.py`（若搬运件留在 gates，下沉模型的自哈希 validator 会形成 knowledge→gates 向上回边；admission_ 前缀与 Step2 法定哈希冻结区模块隔离防混）。一级子包与冒烟不受影响；搬运件遵守冻结哈希铁律（bc-b8b9aca6，逐字节原样）。
- **阶段边界（总指挥令）**：P1 骨架落盘到此为止；阶段 2 的代码搬运须待用户确认后启动，骨架侧不得先行迁移任何模块。
- 历史台阶（v19/v23/v24）与 62 个 archive phase、289 个 phase 伴生工具、55 个 V1 生产工具：**一律不进新仓**（manifest 第三、4.5、五-2 节）。
- **金样资产镜像（阶段 2·n5 登记，总指挥补录）**：随迁测试所需的黄金语料闭包（≈120MB：`data/`（evidence_v2/diagram_review_v2/facts_v2/semantic_llm_v2 相关 run 目录 + shadow_v2 第 24 章）、`configs/v2/`、`scripts/tests/fixtures/v2/`、`schemas/v2/` 35 个快照、`docs/v2/baselines/`、5 个金样源 PDF、`input_pdf_公路/` 金样章）按旧仓相对布局**镜像于新仓根**，对用例只读（T5）。落仓根而非 tests/golden 是仓库相对路径契约所迫：`fact_engine` 以仓根锚定 crop-manifest 校验链（其 ROOT 推导已修正为 parents[3]，src 布局下唯一自锚点），conftest 的 repo_tmp_path 亦须同根。

## 4. 测试隔离规范（T1–T8）

新仓把旧仓零散的 ad-hoc skip / 手工 sys.path 收敛为强制条款。全局装置实现在 `tests/conftest.py`，标记注册在 `pyproject.toml`。

| 条款 | 规则 | 机制 |
|---|---|---|
| **T1 发现范围** | 只从 `tests/` 收集用例；`migration/`、`docs/`、包内代码永不被当作测试 | `testpaths = ["tests"]`（继承旧仓教训：脚本式 test_*.py 顶层 sys.exit 干扰收集） |
| **T2 live 隔离** | 真实外呼（LLM/生图/外网）用例必须标 `@pytest.mark.live`；默认套件自动剔除，显式 `uv run pytest -m live` 才跑 | `addopts = -m "not live"`；CLI 传 `-m` 可覆盖 |
| **T3 写盘收口** | 用例写盘只允许两处：pytest `tmp_path`；必须仓库相对路径契约时用 `repo_tmp_path`（`.test-tmp/` 下独立子目录，用后即删）。禁止写仓库树其他位置 | conftest `repo_tmp_path`；`.test-tmp/` 已入 .gitignore。单一稳定父目录规避 Windows 下建删顶层目录与 pytest 扫描的竞态 |
| **T4 默认禁网** | 未标 live 的用例发起非回环外联 → 立即 RuntimeError（拦截在拨号前，不产生流量）；回环（localhost/127.*/::1/AF_UNIX）放行以支持本地 flask 等 | conftest autouse 守卫 monkeypatch `socket.socket.connect` |
| **T5 黄金样本只读** | `tests/golden/`（learning_fixture、step2b_card_golden 落点）与 `tests/fixtures/`（table_continuation_request_fixture 落点）对用例只读；更新必须走显式重生成脚本，禁止用例内静默改写 | 约定 + 评审；重生成脚本随迁移任务补充 |
| **T6 密钥与环境** | 测试不自动加载 `.env`；live 用例所需密钥显式从环境变量读取，缺失时 skip 并说明。`.env*` 不入库 | .gitignore；live 用例模板见下 |
| **T7 无头渲染** | matplotlib 强制 Agg 后端，测试不弹 GUI、不依赖显示环境 | conftest `MPLBACKEND=Agg` |
| **T8 严格标记** | 未注册的 marker 直接报错，防拼错静默失效 | `--strict-markers`；新增 marker 必须先在 pyproject 注册 |

live 用例模板：

```python
import os
import pytest

pytestmark = pytest.mark.live

@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("EXAMCARD_API_KEY") or os.environ.get("API_KEY")
    if not key:
        pytest.skip("未配置 EXAMCARD_API_KEY/API_KEY，跳过 live 用例")
    return key
```

测试组织约定：`tests/` 为常规包（含 `__init__.py`），装置模块用 `tests.golden.*` / `tests.fixtures.*` 绝对导入；用例文件按被测子包命名（如 `test_knowledge_contracts_*.py`），不再沿用旧仓 phase 编号命名。

## 5. 日常命令

```bash
uv sync                          # 建 .venv、按锁安装、editable 装入 examcard
uv run pytest                    # 默认套件（离线、密闭）
uv run pytest -m live            # 仅 live 用例
uv run pytest -m "not slow"      # 剔除慢用例
uv add <pkg>@latest && uv sync   # 升级依赖（锁文件随之更新，一并提交）
```

## 6. 迁移执行约定（给后续 P 任务）

0. **两条铁律（总指挥令，组公告 bc-b8b9aca6）**：
   - **冻结哈希铁律**：所有旧契约的法定哈希/指纹实现（Step2 的 canonical_step2_sha256/_canonicalize/fingerprint_*、evidence 的 identity._canonical/_digest 内容寻址 ID、cache.stable_config_hash 及其它一切既有指纹）**逐字节原样随包搬运**，禁止折叠进 common/hashing 底座或改调新实现；common 底座仅供四桶新代码。合流仅允许在黄金重放逐字节平价通过后以别名化方式进行，且不属阶段 2 范围。一字节规范化差异（浮点 round / frozenset 排序 / separators / ensure_ascii）即作废全部 ID/缓存键/golden。
   - **产出路径纪律**：yijian2 旧仓全程只读；任何产出（文档/脚本/扫描结果）只写本仓，设计类进 `docs/design/` 或 `docs/prework/`。
1. import 改写：`knowledge_v2.X` / `scripts.knowledge_v2.X` → 按 manifest 去向列映射到 `examcard.knowledge.*` 等；`evidence_v2.*` → `examcard.evidence.*`；`materials_v2.*` → `examcard.materials.*`。
2. 每迁一批模块：同批迁其"随核心迁移"的测试（manifest 第六节 76 个），并使其满足 T1–T8（写盘改 tmp、外呼标 live）。
3. 骨架冒烟 `tests/test_skeleton.py` 必须始终绿：它锁定子包齐全、可导入、T3/T4 装置生效。
4. 一级子包新增/更名属破坏性变更：须先改本文与 `tests/test_skeleton.py` 的 `EXPECTED_SUBPACKAGES`，再动代码。
