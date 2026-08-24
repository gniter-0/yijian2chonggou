"""Live 编排辅助库（P3 T2 落地基，T4b 通用全链驱动主体）。

T2 两件（任务书 `docs/prework/P3_优化施工方案_T1-T5_20260812.md` §2 第 3 条）：

- ``ResendPlan``：失败样本重发的身份管理。``manual_reissue_reason`` 参与 Step2
  请求 identity 摘要（`contracts/request_identity.py`），因此「重发」= 换新 reason
  后缀的**新身份**；本类把 pilot 实测口径（首发用 base reason 原文、重发 ``-rN``
  后缀、失败身份禁复用、轮数上限）抽为可复用对象。
- ``load_run_env()`` 与双通道取值函数：运行配置**默认读新仓根 `.env`**（gitignored；
  自 yijian2/.env 迁值，从此原仓零接触），支持显式传路径；文本/图像双通道取值，
  ``IMAGE_*`` 显式键优先、回落通用键。密钥值仅驻内存，调用方不得打印/落盘/入报告。

T4b 编排主体（设计稿 `docs/design/live_pipeline_driver_design.md`，含 R1/R2/R3 修订）：

- ``ExamPaths``：exams/<exam_id>/ 目录契约解析（runbook §1；registry_id 互锁）；
- ``LiveRunBudget``：双护栏预算账本（调用数 × 美元，charge-before-call，持久化续跑）；
- ``RunState``：run_state.json 状态机（断点续跑；损坏可从 stage 报告重建）；
- ``StageAdapter`` 注入协议：**编排层不 import migration**——具体 stage 业务（经
  实测的 pilot 驱动）由 CLI 侧注入；未注入的 stage 一律 fail-closed
  ``stage_adapter_unavailable``（对未来新考试即"资产链未就绪"的显式信号，
  对应设计稿 R1 的转换器开口纪律：宁可拒开，不得发明）；
- 检查点①（准入）/②（图像评审）：文件为契约（sheet 产出 → 退出码 2 暂停 →
  人工决定文件 → 严格摄取），webui 只是可选检视器；
- ``orchestrate()``：固定 DAG 排序 → 预检门禁 → 逐 stage → 报告物化（自证哈希）。

cm26 拆分后本文件只做再导出：实现搬到同级 live_* 兄弟模块（14 个，层序见
docs/prework/live_run_split_blueprint.md 第 3 节），导入路径
examcard.pipeline.live_run 一字不改。__all__ 连私有名一并列出——拆分前的 50 个顶层名
要 100% 兜住，门面不是趁搬家重新定义什么算公开的地方。
"""
from __future__ import annotations

from .live_support import (
    _REPO_ROOT,
    _REASON_MAX_LENGTH,
    _REASON_PATTERN,
    LIVE_STAGES,
    LIVE_STAGE_REPORT_VERSION,
    LIVE_RUN_REPORT_VERSION,
    DEFAULT_PRICING_RELATIVE,
    _RUN_LABEL_MAX,
    _STAGE_PREFLIGHT_KEYS,
    _utc_now_compact,
    _utc_now_iso,
    new_run_id,
)
from .live_budget import load_live_pricing, LiveBudgetExceeded, LiveRunBudget
from .live_paths import MissingUpstreamArtifact, ExamPaths
from .live_env import (
    default_env_path,
    load_run_env,
    _channel_value,
    text_base_url,
    text_api_key,
    image_base_url,
    image_api_key,
    image_channel_fallback,
)
from .live_resend import ResendRoundsExhausted, ResendPlan
from .live_preflight import evaluate_preflight_gate
from .live_reports import LiveStageReport, LiveRunReport, build_stage_report, build_live_run_report
from .live_stage_step3 import load_typed_fixture_compilation, run_step3_stage, load_step3_release_bypass
from .live_stage_step4 import run_step4_stage
from .live_checkpoint import (
    build_admission_checkpoint,
    ingest_admission_decisions,
    emit_image_review_sheet,
    ingest_image_review_sheet,
)
from .live_config import _STAGE_ORDER, LiveRunConfig, RunState
from .live_stage_samples import _cache_state, execute_stage_samples
from .live_rehearsal import budget_rehearsal_estimate, derive_stage_call_caps, materialize_live_run
from .live_orchestrate import _STATUS_TO_DETAIL, orchestrate


__all__ = [
    "DEFAULT_PRICING_RELATIVE",
    "ExamPaths",
    "LIVE_RUN_REPORT_VERSION",
    "LIVE_STAGES",
    "LIVE_STAGE_REPORT_VERSION",
    "LiveBudgetExceeded",
    "LiveRunBudget",
    "LiveRunConfig",
    "LiveRunReport",
    "LiveStageReport",
    "MissingUpstreamArtifact",
    "ResendPlan",
    "ResendRoundsExhausted",
    "RunState",
    "_REASON_MAX_LENGTH",
    "_REASON_PATTERN",
    "_REPO_ROOT",
    "_RUN_LABEL_MAX",
    "_STAGE_ORDER",
    "_STAGE_PREFLIGHT_KEYS",
    "_STATUS_TO_DETAIL",
    "_cache_state",
    "_channel_value",
    "_utc_now_compact",
    "_utc_now_iso",
    "budget_rehearsal_estimate",
    "build_admission_checkpoint",
    "build_live_run_report",
    "build_stage_report",
    "default_env_path",
    "derive_stage_call_caps",
    "emit_image_review_sheet",
    "evaluate_preflight_gate",
    "execute_stage_samples",
    "image_api_key",
    "image_base_url",
    "image_channel_fallback",
    "ingest_admission_decisions",
    "ingest_image_review_sheet",
    "load_live_pricing",
    "load_run_env",
    "load_step3_release_bypass",
    "load_typed_fixture_compilation",
    "materialize_live_run",
    "new_run_id",
    "orchestrate",
    "run_step3_stage",
    "run_step4_stage",
    "text_api_key",
    "text_base_url",
]
