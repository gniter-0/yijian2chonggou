"""运行报告与物化骨架（r10x 家族最结构化形态的泛化；期实例留旧仓）。

- ReviewMachineReport / ReviewAcceptanceReport：新代码形态（examcard_review_*），
  封印走 common.hashing；JUnit 回执直接消费 common.junit.parse_junit_receipt；
- persist_review_artifacts：原子物化 + common.freeze 冻结快照（消费 common/io+freeze，
  bc-9d82a196 口径——冻结机器不再桶内自持）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ...common.freeze import FreezeSnapshot, build_freeze
from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...common.io import atomic_write_json
from ...common.junit import JUnitReceipt, parse_junit_receipt
from ...materials.schema import StrictModel

MACHINE_REPORT_VERSION = "examcard_review_machine_report_v1"
ACCEPTANCE_REPORT_VERSION = "examcard_review_acceptance_report_v1"


class ReviewMachineReport(StrictModel):
    schema_version: Literal["examcard_review_machine_report_v1"] = MACHINE_REPORT_VERSION
    run_id: str = Field(min_length=3)
    gate_sha256: str = Field(pattern=SHA256_HEX)
    readiness_sha256: str = Field(pattern=SHA256_HEX)
    reviewed_candidate_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    stop_reason: str = Field(min_length=1)
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "ReviewMachineReport":
        if self.report_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"})):
            raise ValueError("review machine report hash mismatch")
        return self


class ReviewAcceptanceReport(StrictModel):
    schema_version: Literal["examcard_review_acceptance_report_v1"] = ACCEPTANCE_REPORT_VERSION
    run_id: str = Field(min_length=3)
    accepted: bool
    gate_valid: bool
    all_tests_passed: bool
    receipts: tuple[JUnitReceipt, JUnitReceipt]
    stop_reason: str = Field(min_length=1)
    acceptance_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_acceptance(self) -> "ReviewAcceptanceReport":
        labels = tuple(item.label for item in self.receipts)
        if labels != ("targeted", "full"):
            raise ValueError("acceptance receipts must be the targeted+full pair")
        if self.all_tests_passed != all(item.passed for item in self.receipts):
            raise ValueError("acceptance test flag mismatch")
        if self.accepted != (self.gate_valid and self.all_tests_passed):
            raise ValueError("acceptance must reflect gate and test outcomes")
        if self.acceptance_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "acceptance_sha256"})):
            raise ValueError("review acceptance report hash mismatch")
        return self


def build_machine_report(
    *,
    run_id: str,
    gate_sha256: str,
    readiness_sha256: str,
    disposition_counts: Mapping[str, int],
    stop_reason: str,
) -> ReviewMachineReport:
    return hash_model(
        ReviewMachineReport,
        {
            "run_id": run_id,
            "gate_sha256": gate_sha256,
            "readiness_sha256": readiness_sha256,
            "reviewed_candidate_count": disposition_counts.get("reviewed_candidate", 0),
            "manual_review_count": disposition_counts.get("manual_review", 0),
            "blocked_count": disposition_counts.get("blocked", 0),
            "not_applicable_count": disposition_counts.get("not_applicable", 0),
            "stop_reason": stop_reason,
        },
        "report_sha256",
    )


def build_acceptance_report(
    *,
    run_id: str,
    gate_valid: bool,
    targeted_junit: Path,
    full_junit: Path,
    stop_reason: str,
) -> ReviewAcceptanceReport:
    receipts = (parse_junit_receipt("targeted", targeted_junit), parse_junit_receipt("full", full_junit))
    all_tests_passed = all(item.passed for item in receipts)
    return hash_model(
        ReviewAcceptanceReport,
        {
            "run_id": run_id,
            "accepted": gate_valid and all_tests_passed,
            "gate_valid": gate_valid,
            "all_tests_passed": all_tests_passed,
            "receipts": receipts,
            "stop_reason": stop_reason,
        },
        "acceptance_sha256",
    )


def persist_review_artifacts(output_dir: Path, artifacts: Mapping[str, Any]) -> FreezeSnapshot:
    """物化骨架：逐件原子写盘（JSON 文件名→对象），再对全部产物建冻结快照。"""
    if not artifacts:
        raise ValueError("nothing to persist")
    output = Path(output_dir)
    for name, value in artifacts.items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"artifact name must be a plain relative file name: {name}")
        atomic_write_json(output / name, value)
    return build_freeze(output, [(name, "run_artifact") for name in artifacts])
