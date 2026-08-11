"""准入聚合判定骨架：OfflineGate / AdmissionReadiness（新代码形态，基元来自 common.gates）。

聚合体属桶特有类型故留桶内（distill 设计 §2）；旧 phase 的期级 gate 实例
（phase2r84_offline_admission_gate_v1 等）是运行机器证物，按提炼规则留旧仓。
本骨架供新仓自建准入运行使用：GateCheck 基元与 valid==all(checks) and not
failure_codes 定式继承自已冻结的 common.gates.GateBase；封印走 common.hashing
（新产出域，铁律 bc-b8b9aca6）。
"""
from __future__ import annotations

from typing import Literal, Mapping

from pydantic import Field, model_validator

from ...common.gates import GateBase, GateCheck, failed_check_ids
from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel
from .dispositions import count_dispositions

OFFLINE_GATE_VERSION = "examcard_review_offline_gate_v1"
READINESS_VERSION = "examcard_review_admission_readiness_v1"


class ReviewOfflineGate(GateBase):
    """准入离线门禁：计数投影 + 定式校验（GateBase 提供 valid/checks/failure_codes/自证）。"""

    schema_version: Literal["examcard_review_offline_gate_v1"] = OFFLINE_GATE_VERSION
    queue_entry_count: int = Field(ge=0, default=0)
    decision_count: int = Field(ge=0, default=0)
    reviewed_candidate_count: int = Field(ge=0, default=0)
    manual_review_count: int = Field(ge=0, default=0)
    blocked_count: int = Field(ge=0, default=0)
    not_applicable_count: int = Field(ge=0, default=0)


class ReviewAdmissionReadiness(StrictModel):
    schema_version: Literal["examcard_review_admission_readiness_v1"] = READINESS_VERSION
    valid: bool
    fixture_ready: bool
    reviewed_candidate_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    browser_demo_decision_count: int = Field(ge=0)
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    readiness_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_readiness(self) -> "ReviewAdmissionReadiness":
        expected_valid = all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected_valid:
            raise ValueError("readiness validity mismatch")
        if self.fixture_ready != (self.valid and self.reviewed_candidate_count > 0):
            raise ValueError("fixture readiness mismatch")
        if self.readiness_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "readiness_sha256"})):
            raise ValueError("readiness hash mismatch")
        return self


def build_gate_checks(flags: Mapping[str, bool], details: Mapping[str, str] | None = None) -> tuple[GateCheck, ...]:
    lookup = details or {}
    return tuple(
        GateCheck(check_id=check_id, passed=passed, detail=lookup.get(check_id, "PASS" if passed else "FAIL"))
        for check_id, passed in flags.items()
    )


def build_review_gate(
    flags: Mapping[str, bool],
    *,
    dispositions: Mapping[str, str] | None = None,
    queue_entry_count: int = 0,
    decision_count: int = 0,
    details: Mapping[str, str] | None = None,
) -> ReviewOfflineGate:
    checks = build_gate_checks(flags, details)
    failures = failed_check_ids(checks)
    counts = count_dispositions((dispositions or {}).values())
    return hash_model(
        ReviewOfflineGate,
        {
            "valid": not failures,
            "checks": checks,
            "failure_codes": failures,
            "queue_entry_count": queue_entry_count,
            "decision_count": decision_count,
            "reviewed_candidate_count": counts["reviewed_candidate"],
            "manual_review_count": counts["manual_review"],
            "blocked_count": counts["blocked"],
            "not_applicable_count": counts["not_applicable"],
        },
        "gate_sha256",
    )


def build_admission_readiness(
    gate: ReviewOfflineGate,
    *,
    browser_demo_decision_count: int = 0,
    details: Mapping[str, str] | None = None,
) -> ReviewAdmissionReadiness:
    flags = {"offline_gate_valid": gate.valid}
    checks = build_gate_checks(flags, details)
    failures = failed_check_ids(checks)
    valid = not failures
    return hash_model(
        ReviewAdmissionReadiness,
        {
            "valid": valid,
            "fixture_ready": valid and gate.reviewed_candidate_count > 0,
            "reviewed_candidate_count": gate.reviewed_candidate_count,
            "manual_review_count": gate.manual_review_count,
            "blocked_count": gate.blocked_count,
            "not_applicable_count": gate.not_applicable_count,
            "decision_count": gate.decision_count,
            "browser_demo_decision_count": browser_demo_decision_count,
            "checks": checks,
            "failure_codes": failures,
        },
        "readiness_sha256",
    )
