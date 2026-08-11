# providers 供应商兼容层提炼设计（P0）

- 日期：2026-08-11
- 范围：`docs/extraction_manifest.md` 第 4.2 节全部 18 个 `distill:providers` 模块（846KB，均位于旧仓 `yijian2/scripts/knowledge_v2/`）
- 性质：设计文档（阶段 2 实施的施工图）；本文不改动任何代码
- 原则（承接清单第四节）：**通用逻辑提炼进新仓 `src/examcard/providers/`，phase 脚本本体原样留旧仓**

---

## 1. 供应商兼容层是什么

V2 引擎经由本地中继仓 `chatgpt2api`（OpenAI 协议兼容网关）访问两类供应商端点：

| 通道 | 端点 | 消费方 | 响应形态 |
|---|---|---|---|
| Step3 文本 | `POST /v1/chat/completions`（非流式） | Step3 卡片编译器 | chat.completion 信封，assistant content 内嵌业务 JSON |
| Step4 图像 | `POST /v1/images/generations` | Step4 图像编译器 | `{created, data[1]{b64_json, revised_prompt}, usage}` 信封 |

供应商兼容层是引擎与这两个端点之间的全部边界逻辑：**请求投影、响应信封验证、能力探测、部署画像、失败分类、有界实弹传输纪律**。18 个 phase 模块记录了 18 天真实故障排除得出的这套边界——它们是本层唯一的需求来源，不允许在提炼时"顺手泛化"出模块中不存在的行为。

## 2. 18 个模块勘察结论

### 2.1 Step3 信封演进链（r94→r95→r96→r97）

| 模块 | 故障/动机 | 沉淀资产 |
|---|---|---|
| `phase2r94_..._provider_envelope_compatibility_remediation` | R9.3 实弹失败：供应商在 `choices` 旁附带元数据字段，严格信封直接拒绝 | **宽松白名单适配器**：顶层元数据 7 字段白名单（`id/object/created/model/system_fingerprint/service_tier/usage`）+ 核心四字段全有或全无 + `usage.total==prompt+completion` + **精确形状比对**（解析后重序列化必须与原文等值，任何隐式修复=拒绝）+ 只转发未触碰的 `choices` 进下游结果门禁 |
| `phase2r95_..._provider_envelope_real_pilot` | 验证 r94 适配器扛真实供应商 | **有界实弹传输纪律**：`capability_call_budget=1` + `business_call_budget=1`、`automatic_retry_enabled=False`、新鲜身份 nonce、成功记录必须有 provenance（provider_request_id + 响应哈希）、失败记录禁止保留已采信结果、响应体不落盘只存 sha256、错误脱敏 |
| `phase2r96_..._provider_envelope_profile_remediation` | r95 暴露 r94 白名单仍是"拍脑袋"：宽容集合无来源证据 | **来源审计画像**：对中继源码（`api/ai.py` + `services/protocol/openai_v1_chat_complete.py`）逐行取证（claim=文件+行号+语句+字段路径，git blob SHA1 钉扎 + 工作树 clean 校验），导出每一层的**精确键集**（top-level/choice/message/usage/两级 details）、`finish_reason="stop"`、`TRANSPORT_ONLY_PATHS`（只做传输校验、决不进结果门禁的字段路径）；`automatic_field_whitelist_allowed=False` 成为契约字段 |
| `phase2r97_..._provider_envelope_profile_real_pilot` | 画像实弹验收 | r95 传输纪律 + r96 画像门禁的组合复验；确认"画像来自源码审计"路线闭环 |

**演进结论**：宽白名单（r94）是应急产物，源码审计画像（r96）是终态。新仓两者都保留——画像是首选，白名单适配器降级用于"中继源码不可得"的供应商。

### 2.2 Step3 中继运维（r2231、r2232、r2241）

| 模块 | 故障/动机 | 沉淀资产 |
|---|---|---|
| `phase2r2231_step3_relay_recovery` | 中继故障致 Step3 失败后如何恢复 | **新鲜重发恢复模式**：失败身份决不复用，恢复=创建全新业务身份一次性重发（`one fresh source-owned reissue`），网络预算显式声明 |
| `phase2r2232_step3_relay_diagnostic` | 需要在不烧业务预算的前提下定位中继能力 | **能力诊断探针**：固定顺序 `models → chat_json → strict_schema`（strict 探针仅在 chat_json 成功后执行）、总预算 3 次、每探针独立新鲜身份、**失败分类税** `RelayFailureClass`（endpoint_not_found / model_not_found / authentication_disguised_404 / authentication_failure / chat_completions_incompatible / strict_json_schema_unsupported / provider_provenance_missing / transport_unknown_outcome / transport_failure）、`RelayCapabilityAssessment` 就绪判定（chat+json_object+strict_schema+provenance 全绿才 ready）、`business_step3_sent=False` 硬约束（诊断决不夹带业务） |
| `phase2r2241_step3_source_text_remediation` | 终审 held 卡片的源文本瑕疵离线修复 | **离线整改循环**（供应商相关度低）：候选修复必须锚定源文本投影、人工 reviewed fixture 定版。供应商层只取其"失败证据→离线修复→定版"的流程骨架，文本投影逻辑归 contracts/继承包 |

### 2.3 Step4 图像信封与 HTTP 边界（r101、r112、r114、r116、r118、r32）

| 模块 | 故障/动机 | 沉淀资产 |
|---|---|---|
| `phase2r101_relay_baseline_compatibility` | 历史 r33 fixture 与升级后中继并存，需锁双基线 | **双部署画像**：历史画像（容器名/挂载/b64-only 源码验证）与活动画像（源文件哈希集 + 配置的 model 别名声明）打包为 `RelayProfileBundle`，源码哈希基线钉扎防漂移。注：**phase2r33 本体按清单归档**，其 b64-only 经验经 r101 引用其 `RELAY_SOURCE_HASHES/RELAY_DEPLOYED_HASHES` 存续 |
| `phase2r112_..._provider_image_response_compatibility_remediation` | R11.1 实弹图像响应形状与假设不符 | **图像信封画像+适配器**：信封 `{created, data, usage}` 精确键集、`data` 恰一项 `{b64_json, revised_prompt}`、`url_response_allowed=False`、strict base64、PNG 签名+可解码+尺寸精确（1536x1024）、`revised_prompt` 与请求 prompt 精确相等、usage 两级 details 求和一致；同样由中继源码审计支撑 |
| `phase2r114_active_gpt_image2_relay_capability_remediation` | 中继是否真部署了 gpt-image-2 能力说不清 | **部署能力画像（fail-closed）**：上游 slug 映射（`gpt-image-2 → gpt-5-3`）、`advertised_now` 必须等于 `ready_web_image_account_count > 0`、部署态二值（`active` / `source_ready_account_missing`）、**脱敏配置审计**（决不落盘密钥与账号标识，`secrets_redacted=True` 为契约字段）、deterministic fake upstream 证据、fail-closed 回归 |
| `phase2r116_gpt_image2_http_422_request_compatibility_remediation` | 422 根因：内部字段 `schema_version` 泄漏进公网请求体；中继 `extra=forbid` 拒绝是**正确行为** | **公网请求投影**：HTTP 体=恰好五公有字段 `(model, n, prompt, response_format, size)`；全部内部绑定（card id、fact/evidence/anchor ids、各契约哈希……共 12+ 字段）进 **sidecar 投影对象**随证据链持久化；投影校验 prompt 哈希与源一致（投影决不改源文本）；`error_envelope_whitelisted=False`；修复方向是改自身请求而非改中继 |
| `phase2r118_gpt_image2_http_502_source_backed_relay_remediation` | 502 网关错误，响应体不可信 | **不透明失败关闭**：502 只留脱敏记录+响应哈希、决不重构供应商响应体、源码审计证明无中继缺陷可修（`no_relay_fix_admissible=True`）、`StrictImageAdapter`——非 200 一律失败、错误信封决不白名单、成功路径全部精确校验（键集/base64/PNG/尺寸/revised_prompt/usage 求和） |
| `phase2r32_step4_image_relay_recovery` | Card2 图像 smoke 失败后恢复 | Step4 版**一次性新鲜恢复**（与 r2231 同构：新身份、一次预算、传输配置审计复用） |

### 2.4 Step4 视觉整改循环（r34、r36、r311、r42、r151）

| 模块 | 故障 | 沉淀资产 |
|---|---|---|
| `phase2r34_..._card2_exact_label_remediation` | 图内标签数量/文案漂移 | 整改循环实例：失败证据冻结 → prompt 契约收紧（exact-four-label + 目标区域绑定）→ deterministic fake 渲染证据 → 离线门禁 |
| `phase2r36_..._card2_connector_topology_remediation` | 连接件拓扑错误 | 同上循环 + 拓扑边契约（`Step4Card2ExactLayoutEdge`）与回归矩阵 |
| `phase2r311_..._card6_table_border_remediation` | 表格边框缺失 | 同上循环 + 几何契约族（coordinate/bounding_box/grid_cell/border_segment） |
| `phase2r42_..._card14_formula_remediation` | 公式渲染丢源文本 | 同上循环 + 源文本保真校验（deterministic fake renderer 证明 exact source-text fidelity），显式"决不调用图像中继" |
| `phase2r151_gonglu24_remediation_live` | V25/V25B 单科目补救 | **有界多阶段 live 补救**：每阶段新鲜 strict probe + 新业务身份、immutable draft-only 遥测、**首败即停**（stop at the first transport, schema, or local Gate failure）、历史身份决不重试 |

**判定**：这 5 个模块的卡片特定 prompt/几何契约属于 compilers/contracts 桶的素材；**providers 层从中提炼的是"整改循环"流程骨架与"有界 live 补救"传输纪律**（见 §3 不变式 6/7 与 §4 的 `remediation.py`）。

## 3. 沉淀的不变式（本层设计公理）

18 天故障史收敛为 12 条，新仓实现必须逐条可追溯：

1. **两级门禁**：供应商响应先过信封关（键集/形状/传输元数据），再过内容关（业务结果门禁）；信封关决不解释业务语义。
2. **决不修复**：不 repair、不 drop、不 infer、不隐式转换。解析后重序列化与原文不等值即拒绝（r94 的 `choices-value-transformed` 判据）。
3. **fail-closed**：未知字段、非 200、错误信封、形状漂移、能力不足——一律终态失败；错误信封决不进成功白名单（r116/r118）。
4. **画像须有来源证据**：字段白名单/键集必须由中继源码逐行取证支撑（claim + git blob 钉扎 + 工作树 clean）；`automatic_field_whitelist_allowed=False` 是契约字段不是注释（r96/r112）。
5. **传输元数据与业务内容分离**：`TRANSPORT_ONLY_PATHS` 上的字段只做传输校验，决不进入结果门禁（r96）。
6. **实弹预算化**：能力探针与业务调用各自显式预算（典型 1+1），`automatic_retry_enabled=False` 为契约字段（r95/r97）。
7. **失败身份不复用**：恢复与补救=新鲜身份一次性重发；多阶段补救首败即停（r2231/r32/r151）。
8. **响应体不落盘**：实弹响应只存 sha256 + 长度 + 脱敏错误；成功必须留 provenance（provider_request_id + 哈希），失败决不保留已采信结果（r95 `BusinessRecord` 形状约束）。
9. **内部字段决不进公网体**：公网请求=最小公有字段投影，内部绑定进 sidecar；投影决不改源文本（prompt 哈希校验）（r116）。
10. **诊断有序有界**：`models → chat_json → strict_schema` 固定顺序、依赖前序成功、总预算封顶、决不夹带业务调用；失败必须落入枚举分类税（r2232）。
11. **契约自证**：每个契约对象自带 canonical hash 自校验，身份 id 由内容哈希派生（全部 18 模块通用的 `_hash_model` 模式）。
12. **证据脱敏**：密钥、账号标识决不进任何持久化证据；`secrets_redacted=True` 为契约字段（r114/r116 配置审计、r95/r2232 错误脱敏）。

## 4. 目标包设计：`src/examcard/providers/`

```
src/examcard/common/      # ★布局终裁（bc-888c6031 + 扩容终裁 bc-9d82a196）：冻结底座六件，四桶共用，
├── hashing.py            #   最小面 + 签名落地即冻（§6.1）；再变更需新终裁广播
├── gates.py              # hashing: canonical_json/canonical_hash/sha256_*/hash_model
├── io.py                 # gates: GateCheck、门禁基类（valid==all(checks) and not failure_codes 定式）、
├── freeze.py             #        回归矩阵框架（case_id + expected_accepted=False 的 fail-closed 定式）
├── junit.py              # io: atomic_write_json 等原子落盘；freeze: 通用 FrozenArtifact+verify
└── schema.py             #     （不含 phase 路径 glob、不依赖 git）；junit: JUnitReceipt+parse；
                          # schema: strict_schema(model)
                          # providers 消费其中 hashing/gates/io/freeze/schema 五件（junit 属验收机器，
                          # 本层新代码不需要）；非底座杂项归 utils/（writeback/lineage/日志等，不冻结）

src/examcard/providers/   # ★布局终裁：顶层包（10 包之一），import 前缀 examcard.providers.*
├── __init__.py           # 公共 API 导出
├── chat/
│   ├── envelope.py       # ProviderUsage、ProviderEnvelope（r94 宽白名单适配器模型）
│   ├── profile.py        # ChatEnvelopeProfile（r96 精确键集画像）、ProfileEnvelope/Choice/Message/
│   │                     # ProfileUsage/两级 TokenDetails、TRANSPORT_ONLY_PATHS
│   └── inspection.py     # inspect_chat_envelope(mode="allowlist"|"profile") → ChatEnvelopeGate
│                         # （r94 inspect_provider_envelope + r96 inspect_profile_envelope 合流，
│                         #   下游结果校验以回调注入，不反向依赖 compilers）
├── image/
│   ├── envelope.py       # ProviderImageEnvelope/Data/Usage/两级 TokenDetails（r112）
│   ├── adapter.py        # StrictImageAdapter（r118 终态：非200即失败 + 键集/base64/PNG/尺寸/
│   │                     #   revised_prompt/usage 求和全精确）+ ProviderImageErrorAdapterProfile
│   └── request.py        # PublicImageRequest（五公有字段投影）+ RequestSidecarBinding（内部绑定
│                         #   sidecar）+ build_http_projection（r116）
├── relay/
│   ├── source_audit.py   # RelaySourceArtifact/SourceClaim/RelaySourceAudit + git 取证工具
│   │                     #   （r96/r101/r112/r114/r116/r118 六处重复实现 → 唯一实现）
│   ├── config_audit.py   # SanitizedRelayConfigAudit（脱敏配置审计，r114/r116）
│   ├── deployment.py     # RelayDeploymentProfile（r114 能力广告 fail-closed 判定 +
│   │                     #   r101 双画像 bundle 模式）
│   └── diagnostic.py     # RelayFailureClass、RelayDiagnosticPlan、RelayCapabilityAssessment、
│                         #   探针执行器（r2232）
├── transport/
│   ├── identity.py       # FreshIdentity（nonce + 内容哈希派生 id；能力/业务/诊断三型）
│   ├── bounded.py        # BoundedTransport 协议 + HttpxTransport 实现 + CapabilityRecord/
│   │                     #   BusinessRecord（r95 形状约束原样保留）+ 预算执行器
│   ├── errors.py         # sanitize_error / redact（r95/r2232 脱敏与 provider_request_id 提取）
│   └── recovery.py       # 新鲜重发恢复（r2231/r32）+ 多阶段首败即停编排（r151）
└── remediation.py        # 整改循环骨架：FailureEvidence → 离线修复产物 → deterministic fake
                          #   证据 → fail-closed 回归 →（可选）有界 live 复验
                          #   （r34/r36/r311/r42/r2241/r151 的流程公因式；卡片特定契约不在此层；
                          #    失败证据/产物钉扎复用 common/freeze 通用原语）
```

### 4.1 关键 API 草图

```python
# chat/inspection.py
def inspect_chat_envelope(
    body: bytes,
    *,
    profile: ChatEnvelopeProfile | None = None,     # 有画像走画像门禁（首选）
    allowlist: ChatEnvelopeAllowlist | None = None,  # 无画像降级宽白名单
    inner_inspector: Callable[[bytes], InnerGate],   # 下游结果门禁回调（compilers 注入）
) -> ChatEnvelopeInspection: ...

# image/adapter.py
class StrictImageAdapter:
    def validate_success_body(self, status: int, body: bytes, prompt: str) -> Mapping[str, Any]:
        """非 200 一律 ValueError；成功路径逐层精确校验。决不变换响应。"""

# transport/bounded.py
class BoundedTransport(Protocol):
    def post_json(self, endpoint: str, payload: bytes) -> RawResponse: ...

def run_bounded_call(
    transport: BoundedTransport,
    identity: FreshIdentity,
    budget: CallBudget,            # capability=1, business=1, retry 恒禁
    persist: EvidenceSink,         # 只收 hash/长度/脱敏错误，拒收响应体
) -> CapabilityRecord | BusinessRecord: ...

# relay/diagnostic.py
def run_relay_diagnostic(
    transport: DiagnosticTransport,
    plan: RelayDiagnosticPlan,     # models→chat_json→strict_schema，预算 3
) -> RelayCapabilityAssessment: ...
```

### 4.2 从 phase 常量到新仓配置/画像数据的迁移规则

| phase 里的形态 | 新仓形态 |
|---|---|
| 硬编码中继路径 `Path(r"C:\...\chatgpt2api")` | `ProviderSettings.relay_root`（配置注入；审计函数收 `relay_root` 参数——r112/r114 已是参数化形态，沿用） |
| `Literal["gpt-image-2"] / "1536x1024" / "gpt-5-3"` 等钉扎字面量 | 保持**画像对象的数据字段**（这正是本层经验：能力钉在画像里，不散落代码）；新仓出厂附带从 r101/r112/r114/r116 冻结产物翻译的画像实例 |
| `phase2r94_provider_envelope_v1` 等版本串 | 中性命名 `examcard_chat_envelope_v1` 等；旧串保留于画像的 `distilled_from` 溯源字段 |
| `CHECKPOINT_COMMIT`、`R93_RUN` 等 phase 链冻结/复核机器 | **链路本体不迁移**（冻结-复核-验收-journal 的 phase 工作流由 git + tests/golden 承担等价职责）；其中的**通用原语**按扩容终裁 bc-9d82a196 收敛：`FrozenArtifact`+verify → `common/freeze`（去 phase 路径 glob、去 git 依赖）、`JUnitReceipt`+parse → `common/junit`、原子落盘 → `common/io`、`strict_schema` → `common/schema` |
| `FORBIDDEN_KEYS`（review_state/golden_title 等业务禁字段） | 迁入 `chat/envelope.py` 常量并在 inspection 中保留检查（它防的是业务字段从上游污染信封，属本层职责） |

## 5. 迁移映射表（18 模块逐条）

「提炼」= 逻辑改写进新仓目标文件；「留守」= phase 本体（冻结/journal/验收/runner/伴生 tools 与 93 个 phase 测试）原样留旧仓。

> **硬约束（来自 release-gate 桶对齐，2026-08-11）**：4.4 桶的 `phase2r98` 冻结审计按字节锁定了 `phase2r97*.py` 等文件（`R97_CODE_FILES`），`phase2r152` 同样锁定 `phase2r151*.py` 字节（`_REMEDIATION_OWNED_PATHS`）。因此对 **r96/r97/r151（及全部 18 模块）的提炼一律只读旧仓脚本，一个字节都不改**——否则击穿 r98/r152 已落盘的 freeze 校验。这与清单"distill 不搬本体"一致，此处升格为强制项。

| # | 模块 | 提炼物 → 新仓目标 |
|---|---|---|
| 1 | phase2r94_provider_envelope_compatibility | `ProviderUsage/ProviderEnvelope/ALLOWED_METADATA/FORBIDDEN_KEYS` → `chat/envelope.py`；`inspect_provider_envelope` 白名单分支 + 回归用例族 → `chat/inspection.py`、`gates.py` |
| 2 | phase2r95_provider_envelope_real_pilot | `RequestIdentity/CapabilityRecord/BusinessRecord/HttpxPilotTransport/_sanitize_error/_provider_request_id` → `transport/{identity,bounded,errors}.py` |
| 3 | phase2r96_provider_envelope_profile | `ProviderEnvelopeProfile/ProfileEnvelope 族/TOP_LEVEL~DETAIL_KEYS/TRANSPORT_ONLY_PATHS` → `chat/profile.py`；`RelaySerializerAudit/RelaySourceClaim/git 取证` → `relay/source_audit.py`；`inspect_profile_envelope` → `chat/inspection.py` |
| 4 | phase2r97_provider_envelope_profile_real_pilot | 画像+实弹组合编排 → `transport/bounded.py` 的用例参考（无独有新逻辑，验收其组合语义） |
| 5 | phase2r2241_source_text_remediation | 流程骨架 → `remediation.py`；文本投影细节归 contracts 桶（跨桶备注 §6） |
| 6 | phase2r2231_relay_recovery | 新鲜重发恢复 → `transport/recovery.py` |
| 7 | phase2r2232_relay_diagnostic | `RelayFailureClass/RelayDiagnosticPlan/RelayCapabilityAssessment/探针执行/_redact` → `relay/diagnostic.py`、`transport/errors.py` |
| 8 | phase2r101_relay_baseline_compatibility | 双画像 bundle 模式 + 源码哈希基线 → `relay/deployment.py`（历史 r33 fixture 数据本体留旧仓） |
| 9 | phase2r112_image_response_compatibility | `ProviderImage* 模型族/inspect_provider_image_envelope/图像源码审计` → `image/envelope.py`、`image/adapter.py`、`relay/source_audit.py` |
| 10 | phase2r114_relay_capability | `ActiveImageRelayDeploymentProfile/RelayConfigAudit/fail-closed 判定` → `relay/{deployment,config_audit}.py` |
| 11 | phase2r116_http_422_request_compatibility | `ProviderImageHttpRequest/HttpRequestProjection/build_http_projection/SourceBackedRootCause 模式` → `image/request.py` |
| 12 | phase2r118_http_502_source_backed_relay | `StrictImageAdapter/ProviderImageErrorAdapterProfile/不透明失败关闭` → `image/adapter.py` |
| 13 | phase2r32_image_relay_recovery | Step4 一次性恢复 → `transport/recovery.py`（与 r2231 合流） |
| 14 | phase2r34_card2_exact_label | 整改循环骨架公因式 → `remediation.py`；exact-label 契约归 compilers 桶（其稿 §4.3 已接收处置：卡片级实例留旧仓，通用形态由 r4 `GenericTokenBinding` 覆盖） |
| 15 | phase2r36_card2_connector_topology | 同上；拓扑契约归 compilers 桶（通用形态 `GenericSemanticEdge`；边框段/bbox 登记为其 v2 几何扩展素材） |
| 16 | phase2r311_card6_table_border | 同上；几何契约族归 compilers 桶（`GenericTableContract` + v2 几何扩展素材，见其稿 §4.3） |
| 17 | phase2r42_card14_formula | 同上；`决不调用图像中继`的离线整改约束进 `remediation.py` 文档化断言；公式 token 契约归 compilers 桶（`GenericTokenBinding`） |
| 18 | phase2r151_gonglu24_remediation_live | 多阶段首败即停 + 每阶段新鲜身份 → `transport/recovery.py` |

## 6. 跨桶接口（需总指挥/相邻桶对齐）

1. **公共底座归属——布局终裁（bc-888c6031）+ 模块集扩容终裁（bc-9d82a196，2026-08-11）**：新仓顶层 10 包；冻结底座落 **`src/examcard/common/`**，模块集为**六件 {hashing, gates, io, freeze, junit, schema}**（import 前缀 `examcard.common.*`），providers / compilers / gates-review / release-gate 四桶共用单一实现；**`utils/` 为非底座杂项包**（writeback/lineage/日志等，不冻结），与 common/ 并存，治理边界见骨架 `engineering_skeleton.md` §3。章程：① `common/` 各模块只收通用底座、不收任何桶特有类型；`freeze` 不含各 phase 路径 glob 集、不依赖 git；② 签名**落地即冻**，再变更需新终裁广播。**依赖方向规则**：common/utils 为叶包；providers 只可 import common+utils；其余业务包可 import 三者。providers 消费 hashing/gates/io/freeze/schema 五件（本层 18 个源模块中 `_write_json`/`_atomic_write_bytes` 重复 5+ 份、`FrozenArtifact` 重复 7 份、`strict_schema` 几乎每模块一份——正是扩容举证的重复源之一）。
2. **`StrictModel` 依赖**：全部契约模型继承 `materials_v2.schema.StrictModel`；骨架已冻结其落点 `examcard.materials.schema`（与清单第五节一致），providers 直接 import，不复制。
   > **骨架对齐（2026-08-11，见新仓 `docs/engineering_skeleton.md`）**：导入名 `examcard`、src 布局、Python >=3.11（追加裁决 R1：`semantic_contracts`/r2241 用 `StrEnum`）、pytest 隔离规范 T1–T8（live 标记默认剔除、默认禁网、写盘收口 `.test-tmp/`）已冻结。**providers 包层级已裁决（同修正公告）**：升顶层第 9 包 `src/examcard/providers/`；骨架目录由骨架桶同步修改并在 `engineering_skeleton.md` §3 登记。
3. **下游结果门禁回调——已锁定（与 compilers 桶双向确认，2026-08-11）**：compilers 侧提供 `examcard.compilers.step3_response.make_inner_inspector(model_input) -> Callable[[bytes], ResponseGate]`（r92 `inspect_response` 绑定 model_input 的闭包）；`ResponseGate` 含 `valid: bool + failure_codes: tuple[str,...]`（另有具名检查位/checks/gate_sha256），是本层 `InnerGate` Protocol `{valid, failure_codes}` 的超集——providers 侧自声明 Protocol，零反向 import。**转发字节形态**（源自 r94/r96 实测）：本层校验信封后转发的是重新规范化的最小信封 `canonical_json({"choices":[{"index":0,"message":{"role":"assistant","content":<未触碰原文>}}]})`（r96 在此步剥掉 transport-only 的 `finish_reason`），**不是**拆封后的裸 content——因此 `make_inner_inspector` 单入口即可，无需 document 级入口。
4. **`semantic_llm` 的 `TransportCompletion`**：r2231 消费它；清单将 `semantic_llm` 划入 core（→ `knowledge/llm.py`）。恢复编排以协议类型（Protocol）解耦，不直接依赖 llm.py 具体类。
5. **冻结哈希铁律（总指挥公告 bc-b8b9aca6，2026-08-11，经 bc-888c6031 确认继续有效）**：旧契约的法定哈希/指纹实现（含 r2231/r2232/r151 引用的 `canonical_step2_sha256`/`fingerprint_step2_relay` 等）**逐字节随其所属包搬运，禁止折叠进 `common/hashing` 或改调新实现**——哪怕一个字节的规范化差异（浮点 round/frozenset 排序/separators/ensure_ascii）都会作废全部旧 ID/缓存键/golden。providers 落点：① `common/hashing` 仅供本层**新代码**（新契约对象的自证哈希）使用；② 恢复/补救编排凡涉 Step2 指纹，一律调用随 contracts 包搬运的原实现；③ `distilled_from` 溯源字段携带的旧 phase 哈希值是**原样复制的不透明字符串**，决不用新实现重算复核（见 §7.2）。

## 7. 测试策略

1. **回归矩阵翻译**：r94/r96/r112/r114/r116/r118 各自内置的 fail-closed 回归用例族（缺字段/未知字段/形状变换/错误信封/坏 base64/坏 PNG/错尺寸/错 revised_prompt/usage 不一致……）逐条翻译为新仓 pytest 参数化用例——这是 18 天故障史的直接可执行形态，**一条不丢**。
2. **画像快照测试**：出厂画像实例（chat 画像、image 画像、部署画像）与旧仓冻结产物中的对应哈希字段比对（`distilled_from` 溯源）。**合规注**（冻结哈希铁律，§6.5）：比对是"新画像携带的旧哈希串 == 旧产物里的哈希串"的字符串相等断言，旧哈希决不用 `common/hashing` 重算；新画像自身的 `profile_sha256` 用新实现自证，两套哈希域互不掺混。
3. **传输纪律测试**：以 fake transport 验证预算耗尽、禁重试、失败记录不得含 result、成功记录必须含 provenance 等形状约束。
4. **不迁移**：93 个仅引 phase 脚本的旧测试按清单留旧仓。

## 8. 实施顺序（阶段 2 施工建议）

1. `examcard/common/` 六件底座就位（hashing/gates/io/freeze/junit/schema，扩容终裁 bc-9d82a196；与其他三桶共建，providers 只消费不重实现）
2. `chat/`（r94→r96 顺序：先白名单模型后画像，inspection 最后合流）
3. `image/`（envelope → adapter → request）
4. `relay/`（source_audit 先行——它被 chat/image 画像构建共同依赖）
5. `transport/`（identity → errors → bounded → recovery）
6. `remediation.py`（依赖前五步全部就位）
7. 回归矩阵翻译与画像快照测试（与 2–6 各步同交付）

每步验收：新仓 pytest 全绿 + 该步对应的不变式（§3 编号）在测试中有显式断言。

## 9. 开放问题

| # | 问题 | 建议 |
|---|---|---|
| 1 | ~~底座落点与 providers 包层级~~ | **均已布局终裁关闭**（bc-888c6031，替代此前全部布局公告）：顶层 10 包；冻结底座归 `examcard/common/`、非底座杂项归 `examcard/utils/`（两包并存）、providers 顶层 `examcard/providers/`；依赖方向 common/utils 叶包 ← providers ← 业务包（见 §6.1/§6.2） |
| 2 | 图像尺寸/模型名等画像数据未来多供应商化 | 本次**不做**多供应商抽象（YAGNI；18 模块只见过 chatgpt2api 一个中继），画像对象天然支持多实例，够用 |
| 3 | r2241 文本投影细节的归属 | 判定归 contracts；如 contracts 桶设计者另有安排，providers 侧只保留 remediation 骨架引用点 |
| 4 | 任务书提及 phase2r33 b64_closure | r33 在清单 4.2 不在列（属 4.5 archive）；其 b64-only 经验已由 r101 引用存续 + r112/r114 画像继承，无遗漏 |
