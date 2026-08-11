"""四步流水线离线编排器（extract → evidence → facts → cards，provider 注入式）。

新代码节点（阶段 2 plan-7de967ee）：本编排器是通用四步引擎骨架——
- **provider 注入式**：任何外呼客户端经 `PipelineContext.providers` 注入，编排器自身
  决不拨号（默认禁网套件下可全程运行）；四步的具体绑定（materials/evidence/
  knowledge/compilers 的入口适配）随 65 章跑批节点交付，本模块不 import 业务包，
  避免与迁移中的内部接口耦合。
- **fail-closed**：任一步骤异常即终止，其后步骤记 skipped，报告 completed=False；
  决不静默部分成功。
- **步骤输出契约**：每步输出必须可 canonical 序列化（pydantic 模型/映射/序列/标量），
  其 canonical 哈希入 step 结果，作为跨步骤链绑定证据。
- 封印走 common.hashing、物化走 common.io、冻结快照走 common.freeze（新产出域）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import Field, model_validator

from ..common.freeze import FreezeSnapshot, build_freeze
from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..common.io import append_jsonl, atomic_write_json
from ..materials.schema import StrictModel

STEP_RESULT_VERSION = "examcard_pipeline_step_result_v1"
RUN_REPORT_VERSION = "examcard_pipeline_run_report_v1"

StepName = Literal["extract", "evidence", "facts", "cards"]
PIPELINE_STEPS: tuple[StepName, ...] = ("extract", "evidence", "facts", "cards")

StepCallable = Callable[["PipelineContext", Any], Any]


@dataclass(frozen=True)
class PipelineContext:
    """一次编排运行的注入上下文；providers 携带全部外呼客户端（离线跑传 fake）。"""

    run_id: str
    output_dir: Path
    providers: Mapping[str, Any] = field(default_factory=dict)
    offline: bool = True


@dataclass(frozen=True)
class PipelineSteps:
    extract: StepCallable
    evidence: StepCallable
    facts: StepCallable
    cards: StepCallable

    def runner(self, step: str) -> StepCallable:
        if step not in PIPELINE_STEPS:
            raise ValueError(f"unknown pipeline step: {step}")
        return getattr(self, step)


class PipelineStepResult(StrictModel):
    schema_version: Literal["examcard_pipeline_step_result_v1"] = STEP_RESULT_VERSION
    step: Literal["extract", "evidence", "facts", "cards"]
    status: Literal["completed", "failed", "skipped"]
    detail: str = Field(min_length=1)
    output_sha256: str | None = Field(default=None, pattern=SHA256_HEX)
    result_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_result(self) -> "PipelineStepResult":
        if (self.status == "completed") != (self.output_sha256 is not None):
            raise ValueError("completed step requires an output hash; failed/skipped must not carry one")
        if self.result_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "result_sha256"})):
            raise ValueError("pipeline step result hash mismatch")
        return self


class PipelineRunReport(StrictModel):
    schema_version: Literal["examcard_pipeline_run_report_v1"] = RUN_REPORT_VERSION
    run_id: str = Field(min_length=3)
    steps: tuple[PipelineStepResult, ...] = Field(min_length=4, max_length=4)
    completed: bool
    network_calls: Literal[0] = 0
    stop_reason: str = Field(min_length=1)
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "PipelineRunReport":
        if tuple(item.step for item in self.steps) != PIPELINE_STEPS:
            raise ValueError("pipeline steps must be extract, evidence, facts, cards in order")
        if self.completed != all(item.status == "completed" for item in self.steps):
            raise ValueError("pipeline completion flag mismatch")
        seen_failure = False
        for item in self.steps:
            if seen_failure and item.status != "skipped":
                raise ValueError("steps after a failure must be skipped")
            if item.status == "failed":
                seen_failure = True
        if self.report_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"})):
            raise ValueError("pipeline run report hash mismatch")
        return self


def _step_result(step: str, status: str, detail: str, output_sha256: str | None) -> PipelineStepResult:
    return hash_model(
        PipelineStepResult,
        {"step": step, "status": status, "detail": detail, "output_sha256": output_sha256},
        "result_sha256",
    )


def run_offline(
    context: PipelineContext,
    steps: PipelineSteps,
    initial_input: Any = None,
) -> tuple[PipelineRunReport, dict[str, Any]]:
    """顺序执行四步并物化报告/日志/冻结快照；返回 (报告, 各步输出)。"""
    if not context.offline:
        raise ValueError("this orchestrator node only supports offline mode")
    journal_path = context.output_dir / "journal.jsonl"
    results: list[PipelineStepResult] = []
    outputs: dict[str, Any] = {}
    current: Any = initial_input
    stop_reason = "completed"
    failed = False
    for sequence, step in enumerate(PIPELINE_STEPS, start=1):
        if failed:
            result = _step_result(step, "skipped", "skipped after upstream failure", None)
        else:
            try:
                current = steps.runner(step)(context, current)
                output_sha256 = canonical_hash(current)
                outputs[step] = current
                result = _step_result(step, "completed", "completed", output_sha256)
            except Exception as error:  # fail-closed：任何步骤异常终止全链
                failed = True
                first_line = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
                stop_reason = f"{step} failed: {first_line[:200]}"
                result = _step_result(step, "failed", first_line[:200] or "failed", None)
        results.append(result)
        append_jsonl(journal_path, {"sequence": sequence, "step": step, "status": result.status, "detail": result.detail})
    report = hash_model(
        PipelineRunReport,
        {"run_id": context.run_id, "steps": tuple(results), "completed": not failed, "stop_reason": stop_reason},
        "report_sha256",
    )
    atomic_write_json(context.output_dir / "pipeline-report.json", report)
    freeze = build_freeze(context.output_dir, [("pipeline-report.json", "report"), ("journal.jsonl", "journal")])
    atomic_write_json(context.output_dir / "pipeline-freeze.json", freeze)
    return report, outputs


def load_pipeline_freeze(output_dir: Path) -> FreezeSnapshot:
    return FreezeSnapshot.model_validate_json((Path(output_dir) / "pipeline-freeze.json").read_text(encoding="utf-8"))
