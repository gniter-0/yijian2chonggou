"""stage / run 两级报告模型与构建器（拆分层 1）。

谱系对齐离线的 PipelineStepResult/PipelineRunReport，但**另起版本号、不复用离线钉扎**：
live 报告的字段集与自证哈希各走一套。全模块也是本家族唯一的 pydantic 落点，
imports 封印会断言两个模型 ``__pydantic_complete__`` 为真。
"""
from __future__ import annotations

from typing import Literal, Sequence
from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..materials.schema import StrictModel

from .live_support import LIVE_RUN_REPORT_VERSION, LIVE_STAGE_REPORT_VERSION


# ---------------------------------------------------------------------------
# 报告模型（谱系对齐 PipelineStepResult/PipelineRunReport；新版本号，不复用离线钉扎）
# ---------------------------------------------------------------------------


class LiveStageReport(StrictModel):
    schema_version: Literal["examcard_live_stage_report_v1"] = LIVE_STAGE_REPORT_VERSION
    stage: str = Field(min_length=1)
    status: Literal["completed", "completed_with_failures", "failed", "skipped", "waiting_human", "pending"]
    detail: str = Field(min_length=1)
    samples_total: int = Field(ge=0, default=0)
    samples_succeeded: int = Field(ge=0, default=0)
    samples_failed: int = Field(ge=0, default=0)
    network_calls: int = Field(ge=0, default=0)
    result_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_result(self) -> "LiveStageReport":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("live stage report hash mismatch")
        return self


class LiveRunReport(StrictModel):
    schema_version: Literal["examcard_live_run_report_v1"] = LIVE_RUN_REPORT_VERSION
    run_id: str = Field(min_length=3)
    exam_id: str = Field(min_length=1)
    mode: Literal["dry_run", "live"]
    stages: tuple[LiveStageReport, ...] = Field(min_length=1)
    completed: bool
    stop_reason: str = Field(min_length=1)
    network_calls: int = Field(ge=0)
    usd_estimate: float = Field(ge=0)
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "LiveRunReport":
        seen_failure = False
        for item in self.stages:
            if seen_failure and item.status not in {"skipped", "pending"}:
                raise ValueError("stages after a failure must be skipped")
            if item.status == "failed":
                seen_failure = True
        if self.completed != (self.stop_reason == "completed"):
            raise ValueError("live run completion flag mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"}))
        if self.report_sha256 != expected:
            raise ValueError("live run report hash mismatch")
        return self


def build_stage_report(stage: str, status: str, detail: str, **counts: int) -> LiveStageReport:
    payload = {"stage": stage, "status": status, "detail": detail, **counts}
    return hash_model(LiveStageReport, payload, "result_sha256")


def build_live_run_report(
    *,
    run_id: str,
    exam_id: str,
    mode: str,
    stages: Sequence[LiveStageReport],
    stop_reason: str,
    network_calls: int,
    usd_estimate: float,
) -> LiveRunReport:
    payload = {
        "run_id": run_id,
        "exam_id": exam_id,
        "mode": mode,
        "stages": tuple(stages),
        "completed": stop_reason == "completed",
        "stop_reason": stop_reason,
        "network_calls": network_calls,
        "usd_estimate": round(usd_estimate, 6),
    }
    return hash_model(LiveRunReport, payload, "report_sha256")
