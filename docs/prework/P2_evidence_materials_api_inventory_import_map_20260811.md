# P2 · evidence_v2 / materials_v2 API 面清点与 import 改写映射表

- 日期：2026-08-11
- 范围：`yijian2/scripts/evidence_v2`（17 个文件）与 `yijian2/scripts/materials_v2`（3 个文件：`__init__.py`、`schema.py`、`catalog.py`；任务书所述"2 个文件"指两个实体模块，不含 `__init__.py`）
- 目标：V2 引擎抽包到平级新仓 `C:\Users\liushaocheng\Desktop\getinfo\exam-card-engine`，src 布局：
  - `evidence_v2` → `examcard.evidence`
  - `materials_v2` → `examcard.materials`
  - `knowledge_v2` 核心 → `examcard.knowledge`（本文档不清点其 API，只涉及其作为消费方的改写）

## 0. 结论摘要（TL;DR）

1. **今天的导入机制**：两包均以**顶层绝对包**形式被导入（`from evidence_v2.schema import ...`、`from materials_v2.schema import ...`），依赖各入口文件把 `scripts/` 插入 `sys.path`（`sys.path.insert(0, str(SCRIPTS_DIR))`）。**不存在** `scripts.evidence_v2` 前缀形态、**不存在**从包根 `from evidence_v2 import X` 的用法（所有消费方都直接 import 子模块）。
2. **依赖方向单向、可干净抽包**：`evidence_v2 → materials_v2`（10 个文件），`materials_v2` 是叶子（零内部依赖）。无反向依赖、无循环。
3. **消费方规模**：yijian2 内共 **186 个消费文件 / 294 行 import 语句**待改写（knowledge_v2 138、tools 25、tests 22、scripts 根 1 = `run_evidence_v2.py`）。root 散置 `*.py` 与 `conftest.py` 零命中。
4. **改写规则纯机械**（3 条正则即可覆盖 100%），仅 1 处裸 `import evidence_v2.vision as vision_module`（`scripts/tests/test_vision_idempotency.py:11`）需要第 3 条规则。
5. **排除项（勿改写）**：`data/evidence_v2/` 是**数据输出目录**，不是模块引用（`run_v2_shadow.py:592`、`run_fact_v2.py:36`、`run_evidence_v2.py:51-52` 的默认参数路径）。全仓唯一的字符串形态命中就是它。
6. **新仓 Python 版本**：两包使用 `enum.StrEnum`（3.11+ 才有）。yijian2 的 pyproject 写 `requires-python >= 3.10`（实际 venv 是 3.12.10）；**新仓应声明 `requires-python >= 3.11`**。

---

## 1. API 面清点 · materials_v2 → examcard.materials

依赖：`schema.py` 仅 stdlib + pydantic；`catalog.py` 仅 stdlib + PyYAML + `.schema`。叶子包，零仓内依赖。

### 1.1 `materials_v2/schema.py` → `examcard/materials/schema.py`

| 公开符号 | 类型 | 说明 | 外部消费文件数 |
|---|---|---|---|
| `V2_SCHEMA_VERSION` | 常量 `"2.0"` | 全 V2 契约版本号 | 19 |
| `StrictModel` | pydantic 基类 | `extra="forbid", frozen=True, str_strip_whitespace=True`；**全仓事实上的契约基类** | **141** |
| `SemanticRole` | StrEnum | core/worked_example/exercise_*/exam_tag/heading/caption/header_footer/watermark/page_number/decorative/unknown | 9 |
| `NOISE_ROLES` | frozenset 常量 | 4 个噪声角色集合 | 1 |
| `PolicyDisposition` | StrEnum | included/excluded_by_policy/archived/manual_review/ignored_noise | 5 |
| `CoverageRequirement` | StrEnum | required/optional/not_required | 2 |
| `PolicyDecisionState` | StrEnum | unclassified/auto_classified/needs_manual_review/manually_resolved | 2 |
| `PresentationTransformAction` | StrEnum | remove_matches/truncate_from_match | 1 |
| `PresentationTransformRule` | StrictModel | 渲染期非破坏转换规则（校验 pattern 可编译） | 0（间接经 ContentPolicy） |
| `SourceKind` | StrEnum | textbook/lecture_note/regulation/standard/question_bank/past_exam/slide_deck/other | 1 |
| `DedupScope` | StrEnum | within_document/within_collection/cross_collection_candidate_only | 1 |
| `EffectiveWindow` | StrictModel | effective_from/to + standard_id | 1 |
| `ContentPolicy` | StrictModel | 内容策略（included/excluded/optional/manual_review 角色集互斥校验） | 7 |
| `MaterialProfile` | StrictModel | 教材档案（profile_id/revision/collection/policy/layout/taxonomy/validation/card 各 profile 引用） | 4 |
| `PolicyDecision` | StrictModel | 单条 evidence 的策略裁决（噪声/收录/覆盖率约束校验） | 5 |

### 1.2 `materials_v2/catalog.py` → `examcard/materials/catalog.py`

| 公开符号 | 类型 | 说明 | 外部消费文件数 |
|---|---|---|---|
| `load_content_policy(path) -> ContentPolicy` | 函数 | YAML → ContentPolicy | **35** |
| `load_material_profile(path) -> MaterialProfile` | 函数 | YAML → MaterialProfile | 4 |

### 1.3 `materials_v2/__init__.py` → `examcard/materials/__init__.py`

`__all__` 再导出 8 个符号：`ContentPolicy, CoverageRequirement, MaterialProfile, PolicyDecision, PolicyDisposition, SemanticRole, load_content_policy, load_material_profile`。
**事实**：扫描显示 **0 个消费方从包根导入**——所有人都直接走 `materials_v2.schema` / `materials_v2.catalog` 子模块路径（含 `__init__` 未再导出的 `StrictModel`/`V2_SCHEMA_VERSION` 这两个最高频符号）。**因此子模块路径才是必须保持稳定的 API 面**，`__init__` 再导出表照搬即可。

---

## 2. API 面清点 · evidence_v2 → examcard.evidence

### 2.1 模块分层与包内依赖（全部为相对导入 `from .x import y`，搬迁后无需改动）

```
schema.py ──────► materials_v2.schema          （契约层）
identity.py ────► .schema                      （ID/指纹/缓存键）
cache.py        （无仓内依赖）                  （文件缓存）
engine_types.py ► materials_v2.schema          （引擎内部可缓存结构）
native.py ──────► .engine_types                （PyMuPDF rawdict 提取）
ocr.py ─────────► .engine_types                （RapidOCR 提供方）
vision.py       （无仓内依赖，依赖 openai）     （视觉复核）
layout.py ──────► .engine_types .schema + materials_v2.schema
units.py ───────► .identity .schema + materials_v2.schema
visual.py ──────► .engine_types + materials_v2.schema （图形裁剪/连线检测）
quality.py ─────► .schema                      （质量汇总）
fusion.py ──────► .engine_types .identity .layout .schema .units .visual + materials_v2.schema
annotations.py ─► .schema + materials_v2.schema（golden 复核 sidecar）
golden.py ──────► .schema + materials_v2.schema（golden 注册表）
golden_runner.py► .engine_types .golden .ingest .layout + materials_v2.schema
ingest.py ──────► .cache .engine_types .fusion .identity .layout .native .ocr .quality .schema .units .vision + materials_v2.schema
__init__.py ────► .identity .ingest .schema
```

**注意**：`evidence_v2/__init__.py` 急切 import `.ingest`，链式拉起 `fitz/rapidocr/cv2/numpy/openai` 全部重依赖——今天**任何** `from evidence_v2.schema import X` 都会执行这条重链（Python 导入子模块必先执行父包 `__init__`）。消费方从不使用包根导出（0 命中），新仓如想让 schema-only 消费方轻量化，可安全瘦身 `__init__`；默认照搬保持行为一致。

### 2.2 各模块公开符号

| 模块（新路径 `examcard.evidence.*`） | 公开符号 | 外部消费文件数 |
|---|---|---|
| `schema` | 枚举 `EvidenceKind`(19 值) `SourceChannel`(5) `SourceQuality`(5)；模型 `BBox` `SourceLocator` `EvidenceProvenance` `SourceConflict` `EvidenceUnit` `PageQuality` `PageEvidence` `SourceDocument` `EvidenceLineage` `DocumentEvidenceManifest` | **59**（PageEvidence 45、EvidenceKind 26、BBox 12、EvidenceUnit 11、SourceQuality 4、DocumentEvidenceManifest 2、SourceChannel 2、EvidenceProvenance 1、SourceLocator 1） |
| `identity` | `normalize_evidence_text` `make_document_id` `make_source_locator_id` `make_evidence_fingerprint` `make_evidence_revision_id` `make_evidence_id` `make_page_id` `make_manifest_id` `make_conflict_id` `make_cache_key` | 1（tools/export_v2_schemas 类工具链；包内 units/fusion/ingest 重度使用） |
| `cache` | `stable_config_hash` `ArtifactCache` | 0 外部（包内 ingest 用） |
| `engine_types` | `NativeChar` `NativeSpan` `NativeLine` `NativeImage` `NativeTableCell` `NativeTable` `NativePage` `OCRLine` `OCRPage` `VisualReviewHint` `VisualReviewResult` | 2 |
| `native` | `extract_native_page` `page_render_hash` | 1 |
| `ocr` | `OCRProvider`(Protocol) `RapidOCRProvider` | 0 外部（包内 ingest 用） |
| `vision` | `VisionVerification` `IdempotencyProbeResult` `GoldenFixtureVisionVerifier` `OpenAIVisionVerifier` | 6（含唯一裸 import 形态） |
| `layout` | `normalized_compare_text` `equivalent_text` `bbox_area` `bbox_intersects` `bbox_iou` `expand_bbox` `detect_repeated_noise` `classify_native_line` `heading_level` `classify_visual_text` | 13（normalized_compare_text 13） |
| `units` | `utc_now` `build_evidence_unit` | 0 外部（包内用） |
| `visual` | `DetectedDiagramEdge` `save_page_crop` `detect_diagram_edges` | 0 外部（包内 fusion 用） |
| `fusion` | `fuse_page` | 0 外部（包内 ingest 用） |
| `quality` | `summarize_pages` | 0 外部（包内 ingest 用） |
| `annotations` | `ConflictClassification` `AnnotationState` `PageReviewAudit` `ConflictReviewAudit` `ConflictReviewDraft` `GoldenPageReviewDraft` `GoldenSampleReviewDraft` `GoldenReviewSidecar` `load_golden_review_sidecar` | 5 |
| `golden` | `AnnotationStatus` `GoldenAssertion` `ExpectedEvidenceMatch` `GoldenSample` `GoldenSampleRegistry` `load_golden_registry` `verify_golden_sources` | **23**（load_golden_registry 21） |
| `golden_runner` | `run_golden_registry` | 1（run_evidence_v2.py） |
| `ingest` | `EvidenceEngineConfig` `EvidenceRunResult`(dataclass) `EvidenceEngine` | 3 |
| `__init__` | 再导出：`DocumentEvidenceManifest` `EvidenceEngine` `EvidenceEngineConfig` `EvidenceUnit` `PageEvidence` + 8 个 `make_*` | 0（无人从包根导入） |

### 2.3 第三方依赖（新仓 pyproject 依据）

| 包 | 依赖 |
|---|---|
| `examcard.materials` | `pydantic>=2.7`、`PyYAML` |
| `examcard.evidence` | 上述 + `PyMuPDF`(fitz)、`numpy`、`opencv-python`(cv2, 仅 visual.py)、`rapidocr-onnxruntime`(仅 ocr.py)、`openai`(仅 vision.py，**模块级导入=硬依赖**) |

Python：**>= 3.11**（`enum.StrEnum`；yijian2 venv 实测 3.12.10）。

---

## 3. import 改写映射表

### 3.1 新仓内部改写（包自身文件，共 10 个文件 10 行）

`evidence_v2` 的包内相对导入（`from .x import y`）**全部原样保留**。唯一要改的是对 materials 的顶层绝对导入，统一改为新仓绝对路径（推荐；也可用相对 `..materials.schema`，不推荐——绝对路径便于全仓 grep）：

| # | 文件（新路径 `src/examcard/evidence/`） | 行 | 现状 | 改为 |
|---|---|---|---|---|
| 1 | `schema.py` | 9 | `from materials_v2.schema import PolicyDecision, SemanticRole, StrictModel, V2_SCHEMA_VERSION` | `from examcard.materials.schema import PolicyDecision, SemanticRole, StrictModel, V2_SCHEMA_VERSION` |
| 2 | `engine_types.py` | 6 | `from materials_v2.schema import StrictModel` | `from examcard.materials.schema import StrictModel` |
| 3 | `fusion.py` | 11 | `from materials_v2.schema import SemanticRole` | `from examcard.materials.schema import SemanticRole` |
| 4 | `layout.py` | 8 | `from materials_v2.schema import SemanticRole` | `from examcard.materials.schema import SemanticRole` |
| 5 | `units.py` | 7 | `from materials_v2.schema import SemanticRole` | `from examcard.materials.schema import SemanticRole` |
| 6 | `visual.py` | 11 | `from materials_v2.schema import StrictModel` | `from examcard.materials.schema import StrictModel` |
| 7 | `annotations.py` | 10 | `from materials_v2.schema import StrictModel, V2_SCHEMA_VERSION` | `from examcard.materials.schema import StrictModel, V2_SCHEMA_VERSION` |
| 8 | `golden.py` | 12 | `from materials_v2.schema import SemanticRole, StrictModel, V2_SCHEMA_VERSION` | `from examcard.materials.schema import SemanticRole, StrictModel, V2_SCHEMA_VERSION` |
| 9 | `golden_runner.py` | 9 | `from materials_v2.schema import MaterialProfile` | `from examcard.materials.schema import MaterialProfile` |
| 10 | `ingest.py` | 13 | `from materials_v2.schema import MaterialProfile, StrictModel, V2_SCHEMA_VERSION` | `from examcard.materials.schema import MaterialProfile, StrictModel, V2_SCHEMA_VERSION` |

`materials_v2` 3 个文件**零改动**（内部全相对导入、无仓内外部依赖），直接落位 `src/examcard/materials/`。

### 3.2 消费方改写规则（yijian2 侧 186 文件 / 294 行；机械正则，3 条全覆盖）

| # | 匹配（正则） | 替换 | 覆盖 |
|---|---|---|---|
| R1 | `^(\s*)from evidence_v2(\.[\w.]+)? import ` | `$1from examcard.evidence$2 import ` | 全部 evidence from-import（含 `# noqa` 尾注行，注释保留） |
| R2 | `^(\s*)from materials_v2(\.[\w.]+)? import ` | `$1from examcard.materials$2 import ` | 全部 materials from-import |
| R3 | `^(\s*)import evidence_v2\.([\w.]+)( as .+)?$` | `$1import examcard.evidence.$2$3` | 唯一 1 处：`scripts/tests/test_vision_idempotency.py:11`（`import evidence_v2.vision as vision_module`） |

**模块路径 → 新路径速查**（所有出现过的旧模块路径，一对一）：

| 旧模块路径 | 新模块路径 |
|---|---|
| `evidence_v2` （包根，仅测试 monkeypatch 场景可能出现） | `examcard.evidence` |
| `evidence_v2.schema` | `examcard.evidence.schema` |
| `evidence_v2.identity` | `examcard.evidence.identity` |
| `evidence_v2.cache` | `examcard.evidence.cache` |
| `evidence_v2.engine_types` | `examcard.evidence.engine_types` |
| `evidence_v2.native` | `examcard.evidence.native` |
| `evidence_v2.ocr` | `examcard.evidence.ocr` |
| `evidence_v2.vision` | `examcard.evidence.vision` |
| `evidence_v2.layout` | `examcard.evidence.layout` |
| `evidence_v2.units` | `examcard.evidence.units` |
| `evidence_v2.visual` | `examcard.evidence.visual` |
| `evidence_v2.fusion` | `examcard.evidence.fusion` |
| `evidence_v2.quality` | `examcard.evidence.quality` |
| `evidence_v2.annotations` | `examcard.evidence.annotations` |
| `evidence_v2.golden` | `examcard.evidence.golden` |
| `evidence_v2.golden_runner` | `examcard.evidence.golden_runner` |
| `evidence_v2.ingest` | `examcard.evidence.ingest` |
| `materials_v2` （包根，同上） | `examcard.materials` |
| `materials_v2.schema` | `examcard.materials.schema` |
| `materials_v2.catalog` | `examcard.materials.catalog` |

**消费方分布**（改写触达面）：`scripts/knowledge_v2` 138 文件、`scripts/tools` 25、`scripts/tests` 22、`scripts/run_evidence_v2.py` 1。按模块计：`materials_v2.schema` 151 文件、`materials_v2.catalog` 37、`evidence_v2.schema` 59、`evidence_v2.golden` 23、`evidence_v2.layout` 13、`evidence_v2.vision` 6、`evidence_v2.annotations` 5、`evidence_v2.ingest` 3、`evidence_v2.engine_types` 2、`evidence_v2.golden_runner/identity/native` 各 1。

### 3.3 排除项（改写时**必须跳过**，否则破坏数据路径）

| 位置 | 内容 | 性质 |
|---|---|---|
| `scripts/tools/run_v2_shadow.py:592` | `ROOT / "data" / "evidence_v2" / "golden" / ...` | 数据输出目录 |
| `scripts/run_fact_v2.py:36` | `default=ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"` | 数据输出目录 |
| `scripts/run_evidence_v2.py:51-52` | `--output-root` / `--cache-root` 默认 `ROOT / "data/evidence_v2"` | 数据输出目录 |
| `data/evidence_v2/**`（磁盘） | golden 运行产物、缓存 | 不动 |

AST 全扫描确认：除上述数据路径外，**全仓（scripts/ + conftest.py + 根目录 *.py）不存在任何字符串/importlib/monkeypatch 形态的模块名引用**；改写只发生在静态 import 语句上。

### 3.4 sys.path 引导与安装方式（改写的配套前提）

- 现状：47/186 个消费文件自带 `sys.path.insert(0, str(SCRIPTS_DIR))` 引导（其余靠被这些入口带起）。顶层包 `evidence_v2`/`materials_v2` 之所以可导入，完全依赖该 hack。
- 抽包后：`examcard` 应通过**安装**进入环境（yijian2 侧 `uv add --editable ../exam-card-engine` 或 pyproject `[tool.uv.sources] examcard = { path = "../exam-card-engine", editable = true }`）。
- `sys.path.insert` 行**不需要删**（它们还服务 `config`、`knowledge_v2` 等仍留在 scripts/ 下的顶层模块）；但改写完成后 scripts/ 下不得再有 `evidence_v2`、`materials_v2` 目录残留，否则旧包会**遮蔽**新包导致静默用旧代码（切换必须删目录或整体搬走，不留半截）。

### 3.5 改写后验证命令（P3 执行方复核用）

```bash
# 1) 旧形态零残留（预期无输出；data/evidence_v2 属数据路径不在匹配范围内）
rg -n "^\s*(from|import)\s+(evidence_v2|materials_v2)\b" scripts/ conftest.py

# 2) 新包可导入且指向新仓（预期打印 exam-card-engine 下的路径）
python -c "import examcard.evidence.schema, examcard.materials.schema as m; print(m.__file__)"

# 3) 契约回归（yijian2 侧现有测试入口）
python -m pytest scripts/tests/test_v2_contracts.py scripts/tests/test_evidence_v2_engine.py -q
```

## 4. 交接备注（给 P3 执行方 / 总指挥）

1. **建议硬切换而非留 shim**：294 行改写为纯机械正则，留 `scripts/evidence_v2` 兼容 shim 会造成双源与遮蔽风险（见 3.4）。
2. **knowledge_v2 消费行的归宿不影响本表**：无论某 knowledge 文件留在 yijian2 还是并入 `examcard.knowledge`，其对 evidence/materials 的 import 目标一律是本表新路径。
3. **新仓 pyproject 要点**：`requires-python >= 3.11`；依赖按 2.3 节分组（可将 `opencv-python`/`rapidocr-onnxruntime`/`openai` 设为 extras，如 `examcard[engine]`，纯契约消费方只装 pydantic+PyYAML——但注意 2.1 节 `__init__` 重链问题需一并处理，否则 extras 不成立）。
4. 本清点由 AST 全量扫描得出（scripts/ 836 个 py + conftest.py + 根目录散置 py），扫描口径：静态 import（含相对导入、裸 import、别名）+ 字符串模块引用；无解析错误。
5. **附件**：`docs/prework/P2_import_scan_result_20260811.json`（本文档同目录）—— 逐文件原始扫描结果（`import_forms`=每种 import 语句形态及其文件清单；`module_symbol_consumers`=模块×符号×消费文件；`dynamic_string_refs`=字符串引用命中）。P3 可直接把它当逐文件改写工单，免重扫。
