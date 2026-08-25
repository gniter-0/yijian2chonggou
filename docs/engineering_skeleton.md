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

- **requires-python：`>=3.12`（P0-1 二次上调；历程 `>=3.10` → `>=3.11` → `>=3.12`）**。3.11 的依据是 semantic_contracts 使用 StrEnum（3.11+）；再上调一档的依据是 `compilers/step3_card.py:204` 用了 PEP 701 嵌套 f-string（f-string 内层复用同种引号，`f"…{self.model_dump(mode="json", …)}…"`），该写法在 3.11 上是 SyntaxError——声明 `>=3.11` 等于放行一个「装得上、一 import 就崩」的组合。开发解释器与旧仓 venv 实测均为 3.12.10。旧仓若将来以依赖方式引用本引擎，需同步上调自身下限。
- **开发解释器：`.python-version` = 3.12**。对齐 yijian2 实际 venv（3.12.10）；uv sync 自动取用，新仓 venv/CI 均用 3.12。
- **运行依赖 9 项**：PyMuPDF、openai、httpx、Pillow、PyYAML、pydantic、rapidocr-onnxruntime、opencv-python<5、numpy。下限与 yijian2 `requirements.txt` 逐项一致，上限由 `uv.lock` 固定。
- **删掉的 3 项零 import 声明（12 → 9，理由入 pyproject 注释）**：`flask`（webui 是纯函数静态 HTML 渲染器，测试反而 AST 断言禁止 import flask）、`matplotlib`（`utils/table_render` 未落地，全仓零 import；conftest 的 T7 `MPLBACKEND=Agg` 是 setdefault 空守卫，保留）、`python-dotenv`（T6 规定测试不自动加载 `.env`）。声明零 import 的依赖不是无害的：它进锁文件、进 CI 安装面、进供应链审计面，还让读者误判包内真实形态。将来真落 flask 路由或 table_render 渲染分支，`uv add` 加回并同步本节。
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
    fact_*.py            fact_engine（67KB）在此拆分：门面留原名 + 12 个同级兄弟
                         （层序与验收口径见 docs/prework/fact_engine_split_blueprint.md）
    llm_*.py             llm（73KB）在此拆分：门面留原名 + 7 个同级兄弟
                         （层序与验收口径见 docs/prework/llm_split_blueprint.md）
    adapter_v1.py state_machine.py identity.py
    contracts/          semantic_contracts（380KB）在此拆分 + v25_generic_contracts + v25_teaching_gate_layers
      v25_*.py           v25_generic_contracts（101KB）在此拆分：门面留原名 + 15 个同级兄弟
                         （层序与验收口径见 docs/prework/v25_generic_contracts_split_blueprint.md）
    continuation/       core / integration / review / repair_sidecar
    diagram/            sidecar / disposition
    planner/            step2b bridge
  compilers/            Step3/4 编译器（manifest 4.1，4 个 phase 提炼；脚本本体留旧仓）
  gates/
    review/             人工准入门禁（manifest 4.3，27 个 phase 提炼）
    release/            发布就绪门禁（manifest 4.4，4 个 phase 提炼）
  pipeline/             四步流水线编排（出网走顶层 providers）
    live_*.py            live_run（86KB）在此拆分：门面留原名 + 14 个同级兄弟
                         （层序与验收口径见 docs/prework/live_run_split_blueprint.md）
  providers/            供应商兼容层，顶层包（manifest 4.2，18 个 phase 提炼；桶内 chat/image/relay/transport 结构自主）
  webui/                审阅工作台（纯函数渲染静态单文件 HTML；无 flask，无 HTTP 层）
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

- gates 只承载判定逻辑，webui 只承载 Web 层——旧仓 workbench phase 里"flask 路由 + 判定"混写的部分，迁移时按此拆开。**新仓现状**：webui 里连 HTTP 层都还没有，`review_workbench.render_review_workbench` 是纯函数产静态单文件 HTML（回环 `python -m http.server --directory <服务根>` 即可检视），flask 已从运行依赖里删除；这条职责线仍然有效，将来加路由时落点仍是 webui。
- **providers 落点（终裁·已决）**：升顶层 `src/examcard/providers/`。理由：providers 是 knowledge.llm / compilers / pipeline 三方共用的传输基础设施，且被 compilers 以回调注入而非反向 import，落 pipeline 之下会制造 `compilers → pipeline` 回边。`pipeline/providers/` 已移除。
- **共享底座（终裁·已决，裁决链以本条为最终口径）**：裁决历程 bc-97be0861（增设 common）→ bc-75c1f096（撤 common 统一 utils）→ **总指挥终裁：恢复双包并存**——`common/`（冻结底座，仅 hashing+gates 两件，签名按 providers 稿 §6.1/§9.1 冻结，增删/变更报总指挥）与 `utils/`（非底座杂项工具）各司其职，bc-75c1f096 中「不设 common、统一进 utils」条款撤销，终裁广播 **bc-888c6031【布局终裁·替代此前所有布局公告】** 已发布为全组最终口径。治理边界：冻结底座定式 → common；其余跨包杂项（writeback/lineage/日志等，不冻结）→ utils；两包均为叶包。一级子包据此锁定 **10 包**。
- **common 模块集扩容（已裁决·扩容终态，终裁广播 bc-9d82a196）**：初始冻结面为 hashing+gates 两件；经 compilers 设计者举证 io（atomic_write_json 等，5+ 份重复）/freeze（FrozenArtifact+verify，7 份）/junit（JUnitReceipt+parse，4 份）在历史 phase 反复重实现且四桶均需，总指挥批准全部上提，并增设 schema.py（strict_schema(model)），终版模块集 **{hashing, gates, io, freeze, junit, schema} 六件**。约束：只收通用（freeze 不含各 phase 路径 glob 集、不依赖 git）；hashing/gates 签名按 providers 稿 §6.1/§9.1，其余落地即冻；再变更需新终裁广播。包数不变仍 10 包。
- **requires-python 上调（已裁决·R1，其后经 P0-1 再上调）**：`>=3.10` → `>=3.11` → **`>=3.12`**。3.11 依据 semantic_contracts 使用 StrEnum；3.12 依据 compilers/step3_card.py 的 PEP 701 嵌套 f-string。uv.lock 均已按新下限重锁，冒烟复验通过。详见 §2。
- **compilers ⇄ gates.review 依赖环拆解（已裁决·A 案，经 gates-review 转达登记）**：跨桶纯数据契约下沉 `knowledge/contracts/admission_fixtures.py`；其法定哈希搬运件一并下沉为 `knowledge/contracts/admission_legacy_hash.py`（若搬运件留在 gates，下沉模型的自哈希 validator 会形成 knowledge→gates 向上回边；admission_ 前缀与 Step2 法定哈希冻结区模块隔离防混）。一级子包与冒烟不受影响；搬运件遵守冻结哈希铁律（bc-b8b9aca6，逐字节原样）。
- **阶段边界（总指挥令）**：P1 骨架落盘到此为止；阶段 2 的代码搬运须待用户确认后启动，骨架侧不得先行迁移任何模块。
- 历史台阶（v19/v23/v24）与 62 个 archive phase、289 个 phase 伴生工具、55 个 V1 生产工具：**一律不进新仓**（manifest 第三、4.5、五-2 节）。
- **金样资产镜像（阶段 2·n5 登记，总指挥补录）**：随迁测试所需的黄金语料闭包（≈120MB：`data/`（evidence_v2/diagram_review_v2/facts_v2/semantic_llm_v2 相关 run 目录 + shadow_v2 第 24 章）、`configs/v2/`、`scripts/tests/fixtures/v2/`、`schemas/v2/` 35 个快照、`docs/v2/baselines/`、5 个金样源 PDF、`input_pdf_公路/` 金样章）按旧仓相对布局**镜像于新仓根**，对用例只读（T5）。落仓根而非 tests/golden 是仓库相对路径契约所迫：`fact_engine` 以仓根锚定 crop-manifest 校验链（其 ROOT 推导已修正为 parents[3]，src 布局下唯一自锚点；cm21 拆分后锚点实体在 knowledge/fact_support.py，门面按原名再导出），conftest 的 repo_tmp_path 亦须同根。

## 4. 测试隔离规范（T1–T8）

新仓把旧仓零散的 ad-hoc skip / 手工 sys.path 收敛为强制条款。全局装置实现在 `tests/conftest.py`，标记注册在 `pyproject.toml`。

| 条款 | 规则 | 机制 |
|---|---|---|
| **T1 发现范围** | 只从 `tests/` 收集用例；`migration/`、`docs/`、包内代码永不被当作测试 | `testpaths = ["tests"]`（继承旧仓教训：脚本式 test_*.py 顶层 sys.exit 干扰收集） |
| **T2 live 隔离** | 真实外呼（LLM/生图/外网）用例必须标 `@pytest.mark.live`；默认套件自动剔除，显式 `uv run pytest -m live` 才跑 | `addopts = -m "not live"`；CLI 传 `-m` 可覆盖 |
| **T3 写盘收口** | 用例写盘只允许两处：pytest `tmp_path`；必须仓库相对路径契约时用 `repo_tmp_path`（`.test-tmp/` 下独立子目录，用后即删）。禁止写仓库树其他位置。**并且：每一次 pytest 调用都必须显式带 `--basetemp .test-tmp/<名字>`**——见下方「T3 附加条款」 | conftest `repo_tmp_path`；`.test-tmp/` 已入 .gitignore。单一稳定父目录规避 Windows 下建删顶层目录与 pytest 扫描的竞态 |
| **T4 默认禁网** | 未标 live 的用例发起非回环外联 → 立即 RuntimeError（拦截在拨号前，不产生流量）；回环（localhost/127.*/::1/AF_UNIX）放行以支持本地静态服务（如检视 webui 产物用的 `python -m http.server`）等 | conftest autouse 守卫 monkeypatch `socket.socket.connect` |
| **T5 黄金样本只读** | `tests/golden/`（learning_fixture、step2b_card_golden 落点）与 `tests/fixtures/`（table_continuation_request_fixture 落点）对用例只读；更新必须走显式重生成脚本，禁止用例内静默改写 | 约定 + 评审；重生成脚本随迁移任务补充 |
| **T6 密钥与环境** | 测试不自动加载 `.env`；live 用例所需密钥显式从环境变量读取，缺失时 skip 并说明。`.env*` 不入库 | .gitignore；live 用例模板见下 |
| **T7 无头渲染** | matplotlib 强制 Agg 后端，测试不弹 GUI、不依赖显示环境 | conftest `MPLBACKEND=Agg`（`setdefault`）。matplotlib 自 §2 起已不在运行依赖，本条当下是**空守卫**：保留是为了 `utils/table_render` 落地那天零改动生效 |
| **T8 严格标记** | 未注册的 marker 直接报错，防拼错静默失效 | `--strict-markers`；新增 marker 必须先在 pyproject 注册 |

### 4.1 T3 附加条款：`--basetemp` 是强制项，不是排障技巧

**规则**：本仓所有 pytest 调用——本地、CI、文档示例、脚本里拼的命令行——都必须显式带
`--basetemp .test-tmp/<名字>`。`<名字>` 取本次跑法的短名（`full` / `cov` / `ci` / `p0`……），
一次跑法一个子目录，事后可按名取证。

**为什么强制**：pytest 缺省把 basetemp 建在 `%TEMP%\pytest-of-<用户名>`，本机该目录 ACL
已损（病因见 §5.1）——普通用户读写即拒，于是裸跑会刷出上百条 `tmp_path` fixture 初始化
ERROR（20260822 实测 `604 passed / 9 skipped / 110 errors`），整轮判定作废。这是环境缺陷、
不是代码缺陷，但它**每次都发作**，所以「先裸跑、报错了再加参数」的口径等于每轮先浪费一次。
显式 basetemp 另有三处收益：产物落进 T3 收口区（`.test-tmp/` 已 gitignore，不脏工作树）、
位置可预期可取证、本地与 CI 逐字一致（CI 用的就是 `--basetemp .test-tmp/ci`）。

**违规形态**：`uv run pytest`、`python -m pytest tests/xxx.py` 这类不带 basetemp 的调用一律
算违规——即便它在别人机器上偶然能过。新写文档、脚本、CI 步骤时照抄 §5 的命令即可。

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
uv sync                                                   # 建 .venv、按锁安装、editable 装入 examcard
uv run pytest --basetemp .test-tmp/full                   # 默认套件（离线、密闭）
uv run pytest --basetemp .test-tmp/live -m live           # 仅 live 用例
uv run pytest --basetemp .test-tmp/fast -m "not slow"     # 剔除慢用例
uv run pytest --basetemp .test-tmp/cov --cov --cov-report=term-missing --cov-fail-under=75   # 覆盖率门槛（CI 同口径）
uv add <pkg>@latest && uv sync                            # 升级依赖（锁文件随之更新，一并提交）
```

`--basetemp` 每条都带，不是可选项——见 §4.1。

**旧仓语料位置（`EXAMCARD_LEGACY_ROOT`）**：依赖 yijian2 金标语料的用例，与 `migration/` 下的
实跑/对照脚本，共用同一条解析顺序——`EXAMCARD_LEGACY_ROOT` 显式指定优先，其次取本仓同级 `../yijian2`；
显式指错即抛错，决不静默回落到同级猜测（那会把「我指错了」悄悄变成「语料不在本机」）。唯一
实现在 `src/examcard/utils/legacy_root.py`，仓内任何 `.py` 都不许再钉机器本地绝对路径——
钉了就等于让别人（含 CI）只能以「找不到文件」的形式失败，而错误里出现的还是他从未写过的那条
路径。封印见 `tests/test_legacy_root_resolution_seal.py`。语料不在本机时相关模块整体跳过，
并在 pytest 摘要里响亮报数；`EXAMCARD_REQUIRE_LEGACY_CORPUS=1` 把这种缺席升级为收集期失败
（给 CI 用，防止 92 个用例无声蒸发）。

### 5.1 本机 pytest 环境附注（Windows ACL 损伤与修复）

裸 `uv run pytest` 若出现大批 `tmp_path` fixture 初始化 ERROR（本机曾实测 84 例、全部非代码缺陷——20260812 未来就绪审计定性），病因是 `%TEMP%\pytest-of-<用户名>` 与仓内 `.pytest_cache/` 曾被管理员权限运行创建，普通用户对其 ACL 拒绝访问、无法删除/改名/重置。

- **修复（管理员 PowerShell，于仓根执行一行）**：

```powershell
takeown /f $env:TEMP\pytest-of-$env:USERNAME /r /d y; icacls $env:TEMP\pytest-of-$env:USERNAME /reset /t /c; rd -r -fo $env:TEMP\pytest-of-$env:USERNAME; takeown /f .pytest_cache /r /d y; icacls .pytest_cache /reset /t /c; rd -r -fo .pytest_cache
```

- **现行强制口径（无需管理员，§4.1 已升为硬规则）**：`uv run pytest --basetemp .test-tmp/<名字>`——写盘收进 T3 收口区（`.test-tmp/`），首次验证时是 477 passed / 1 skipped 全绿基线（41s）。它不再是「修复前的临时替代」：ACL 修没修都照带，本地与 CI 一个口径。
- **补充实测（20260822）**：不带 basetemp 裸跑仍然复现，且随用例增长而放大——`604 passed / 9 skipped / 110 errors`（33.7s；110 条全是 `tmp_path` fixture 初始化失败，无一条源自被测代码）。同一棵树带 `--basetemp .test-tmp/<名字>` 则 0 error。这说明这条参数必须写死在文档与 CI 里，而不是留给人临场想起。
- 出处与证据：`docs/prework/P3_未来就绪审计与优化路线图_20260812.md` §1.1（两处损伤同一病因；`.pytest_cache/` 侧仅缓存告警不影响判定）。

## 6. 迁移执行约定（给后续 P 任务）

0. **两条铁律（总指挥令，组公告 bc-b8b9aca6）**：
   - **冻结哈希铁律**：所有旧契约的法定哈希/指纹实现（Step2 的 canonical_step2_sha256/_canonicalize/fingerprint_*、evidence 的 identity._canonical/_digest 内容寻址 ID、cache.stable_config_hash 及其它一切既有指纹）**逐字节原样随包搬运**，禁止折叠进 common/hashing 底座或改调新实现；common 底座仅供四桶新代码。合流仅允许在黄金重放逐字节平价通过后以别名化方式进行，且不属阶段 2 范围。一字节规范化差异（浮点 round / frozenset 排序 / separators / ensure_ascii）即作废全部 ID/缓存键/golden。
   - **产出路径纪律**：yijian2 旧仓全程只读；任何产出（文档/脚本/扫描结果）只写本仓，设计类进 `docs/design/` 或 `docs/prework/`。旧仓位置一律经 `EXAMCARD_LEGACY_ROOT` 解析（缺省取同级 `../yijian2`），见 §5。
1. import 改写：`knowledge_v2.X` / `scripts.knowledge_v2.X` → 按 manifest 去向列映射到 `examcard.knowledge.*` 等；`evidence_v2.*` → `examcard.evidence.*`；`materials_v2.*` → `examcard.materials.*`。
2. 每迁一批模块：同批迁其"随核心迁移"的测试（manifest 第六节 76 个），并使其满足 T1–T8（写盘改 tmp、外呼标 live）。
3. 骨架冒烟 `tests/test_skeleton.py` 必须始终绿：它锁定子包齐全、可导入、T3/T4 装置生效。
4. 一级子包新增/更名属破坏性变更：须先改本文与 `tests/test_skeleton.py` 的 `EXPECTED_SUBPACKAGES`，再动代码。
