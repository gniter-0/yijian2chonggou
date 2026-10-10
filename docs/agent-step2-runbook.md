# Agent 文件通道 Step2 手册（agent-as-model）

- 日期：2026-10-09；分支：`feat/agent-step2-transport`
- 入口：`migration/run_agent_step2_v1.py`；传输层：`src/examcard/knowledge/agent_file_transport.py`
- 一句话：由编码 Agent（或人工）代替 Step2A 模型，按页写 JSON 响应文件；文件经由**未改动的** `Step2OpenAIAdapter` 和全部下游门禁（锚点、数值/条件推导、grounding、卡片编译），最后 dry-run 出卡。

## 0. 边界与诚信规则

| 规则 | 落实方式 |
|---|---|
| 不冒充供应商模型 | 模型标签必须以 `agent-file:` 开头（默认 `agent-file:pdf-page-review`），它参与 `request_id` 计算，因此 agent 结果永远不会和真实模型共用 request_id/缓存 |
| 响应只能用于写它时对应的那个请求 | 信封里的 `request_id`、`model`、`prompt_sha256`、`response_schema_sha256`、`projection_sha256` 必须与实时请求逐项一致，否则 `binding_mismatch` |
| 不是审核、不是批准 | `author` 只是自述；运行摘要里固定 `human_approved: false` |
| 无网络 | 传输层 `network_call_count == 0`；completion 带 `x-examcard-transport: agent_file_v1`，`provider_request_id = agent-file:<信封sha256前32位>` |
| 不等待、不重试 | 文件缺失或绑定不符时直接报错。Adapter 会把这类错误写成失败的终态记录，所以**必须先 `check` 全绿再 `run`** |
| 只到 dry-run | `run` 调用 `run_exam_project_v1(dry_run=True)`，不出图、不调用图像供应商 |

## 1. 流程

```text
export ──▶ Agent 逐页写 responses/*.response.json ──▶ check（可反复）──▶ run
```

所有命令都在仓库根目录执行（脚本会自动以 `PYTHONHASHSEED=0` 重新执行自身，原因见 §6）：

```bash
.venv/Scripts/python.exe migration/run_agent_step2_v1.py export --pdf "<pdf>" --run-root pilot_runs/<日期>-agent-step2-<名称>
.venv/Scripts/python.exe migration/run_agent_step2_v1.py check  --pdf "<pdf>" --run-root <同上>
.venv/Scripts/python.exe migration/run_agent_step2_v1.py run    --pdf "<pdf>" --run-root <同上>
```

| 参数 | 说明 |
|---|---|
| `--model agent-file:<label>` | 改模型标签；改了以后所有 request_id 都会变，需要重新 export |
| `--refreeze` | 仅 export：重新做证据摄取并覆盖冻结的 manifest。**这会改变 span/section/request id**，已有响应全部作废 |
| `--no-page-images` | 仅 export：不渲染 `page.png` |
| `--source-relative-path` / `--sample-id` | 与 API 流水线保持一致时使用 |

退出码：`0` 成功；`1` 有页面未通过或 runner 失败；`2` 用法错误（无冻结 manifest、manifest 属于别的 PDF、模型标签不合法等）。

## 2. 运行目录结构

```text
<run-root>/
  evidence-cache/                     # Evidence 引擎缓存
  agent_step2/
    evidence_manifest.json            # 冻结的证据（Evidence 带时间戳，重新摄取会改变所有 id）
    evidence_report.json
    queue.json                        # 每页请求、响应路径、孤儿响应列表
    requests/p000N/                   # 每页一个工作包，见 §3
    responses/<request_id>.response.json
    last_check.json / last_run.json   # 每页状态、response_set_sha256、runner 失败码
  step2/<response_set_sha256[:16]>/   # 仅 run：持久 adapter 存储，响应一改就换目录，旧缓存不会遮蔽新响应
  result/cards.json facts.json image_requests.json   # 仅 run 成功后
```

`check` 只在临时目录里写，不留任何持久记录。

## 3. 工作包（requests/p000N/）

| 文件 | 用途 |
|---|---|
| `model_input.json` | 供应商会收到的完整 model-visible 载荷（逐字节同源） |
| `prompt.md` | 系统提示词（与 API 流水线相同的模板，未改动） |
| `response_schema.json` | 严格 JSON Schema（当前请求版本 `step2a_contract_v10_table_atomic_continuation`） |
| `request_identity.json` | 绑定字段 |
| `worksheet.md` | 按 section 列出 span id 与文本，写响应时查 id 用 |
| `page.png` | 页面图像，用于核对表格结构、看出 OCR 漏掉的内容 |
| `response_template.json` | 绑定字段已预填的信封模板；`author.agent_label` 是占位符，不改会被判 `invalid_envelope` |

## 4. 响应信封

```json
{
  "schema_version": "examcard_agent_step2_response_v1",
  "request_id": "step2a-request:<64hex>",
  "model": "agent-file:pdf-page-review",
  "prompt_sha256": "…", "response_schema_sha256": "…", "projection_sha256": "…",
  "author": {"agent_label": "<谁写的>", "page_image_reviewed": true, "notes": "<取舍说明>"},
  "payload": {"proposals": [ … 与 response_schema.json 一致 … ]}
}
```

信封是 StrictModel：多出任何字段（例如 `approved_by`）都会被拒绝。`payload` 至少要有 1 条 proposal（空数组在契约里算 `invalid_response`）。

## 5. `check` 每页状态

| 状态 | 含义 | 处理 |
|---|---|---|
| `missing` | 没有响应文件 | 写响应 |
| `invalid_envelope` | 信封 JSON/Schema 不合法，或 agent_label 仍是占位符 | 看 `detail` |
| `binding_mismatch` | 响应写给的是别的请求（通常是重新冻结或改了模型标签） | 按新的工作包重写；旧文件挪到 `superseded_responses/` |
| `rejected` | Adapter 契约门禁拒绝 | 看 `detail` / `forensics`，见 §7 |
| `accepted` | 该页通过 Step2A | — |

所有页面 `accepted` 后，`check` 还会在临时目录里把 runner 完整 dry-run 一遍。失败时打印 `runner failed - <code>`，`last_check.json.runner_failure` 里有阶段和源码位置（不含异常正文）。

## 6. 已知限制

1. **PYTHONHASHSEED 固定**：Evidence 的 `frozenset` 字段按哈希种子顺序序列化，表格页的 request_id 每个进程都不一样。CLI 目前以 `PYTHONHASHSEED=0` 重新执行自身作为临时措施；根本修复在 `fix/evidence-frozenset-order` 分支（合并后改为只给漂移警告）。**API 流水线的缓存/续跑同样受影响。**
2. **水印字符**：表格单元格的证据文本里混有“联、题、押、精、信、系、微、准”等水印残字，会原样进入卡片文本。过滤属于后续工作。
3. **只到 dry-run**：图像生成不在本通道范围内。

## 7. 写 proposal 的经验（doc01 试跑总结）

**契约（Step2A adapter）**
- 一个 proposal 的 secondary span 不能和 primary span 来自同一个父 Evidence（同一单元格、同一段落）。
- 选中的 span 只要含条件算子（不得、必须、不应、不小于、不大于、方可、及以上、以下、以内、不超过、至少、≤ ≥ < >），assertion 里就必须原样出现。
- 数值写法与原文 token 一致：`2 个`、`2%`、`4`；每个 numeric claim 绑定到被选中的 span。
- 表格原子规则：`required_assertion` 必须是 assertion 的子串，必需锚点放 primary，类型只能 `threshold`/`condition`。
- 组成（composition）proposal 必须选全该表所有单元格 span，`completeness_mode` 用 `full_table_row`。

**Grounding（`evidence_to_fact_v1`）**
- proposal 的 `fact_type`/`completeness_mode` 必须等于按**全部所选证据（含 secondary）**推导出的类型：
  图示 → diagram；公式或含“=” → formula；任一表格单元 → composition；全部是列表项 → enumeration/all_items；含约束词 → exception；含数字 → threshold；否则 definition。
- 例外（本分支新增）：全部由表格证据组成的 `threshold`/`condition` 原子 proposal 保留自己的类型。一个单元格最多 1 个组成 Fact 主属，可以有多个原子 Fact 主属（一个单元格里可能有 ①② 两条规则）。其他任何主属重叠仍然报错。
- 因此：表格单元格不能再拆成 definition/enumeration；表头单元格放 secondary；列表 proposal 不要带段落引导句（“包括以下内容：”），否则推导结果不是 enumeration。
- `proposal_key` 在**整个文档**内唯一（grounding 按文档一次性检查），建议加页前缀：`proposal:p3-table-threshold-001`。

**卡片编译（Step4 桥接）**
- 本分支修正：含换行、引号、反斜杠的文本以前会被误判为 “expected visible text is missing from the prompt”（prompt 内是 JSON 转义形式）。现在按 JSON 转义形式查找；单行文本的 prompt 和哈希不变。

## 8. 排错速查

| runner 失败码 | 阶段 | 常见原因 |
|---|---|---|
| `knowledge_extraction_failed`（stage=grounding） | `ground_step2a_proposals` | 类型/模式与推导不符、主属重叠、`proposal_key` 跨页重复 |
| `card_compilation_failed` | Step4 桥接 | 渲染器不一致、卡片不可发布；换行问题已修 |

逐条定位：参考 `.test-tmp/agent-s2-scratch/dbg6.py`（逐条累加调用 `ground_step2a_proposals`）和 `dbg9.py`（逐张卡编译）。

## 9. 试跑记录：doc01

- PDF：`input_pdf_公路/01 第1章（1）路基施工准备.pdf`（6 页，sha256 `ace9041ad220…`）
- 运行目录：`pilot_runs/20261009-agent-step2-doc01/`
- 结果：6/6 页 accepted → 30 条 Fact → 30 张卡（按页 2/5/10/9/3/1），`run` 退出码 0，`durable: true`，`human_approved: false`
- 响应的取舍说明写在各响应的 `author.notes` 里（例如 p6 例题按 definition 记录，题干不在证据中）。
