"""门禁与 fail-closed 回归定式。签名按 providers 稿 §6.1/§9.1 冻结。

- GateCheck：具名检查位（check_id + passed + detail）；
- GateBase：门禁基类，valid == all(checks.passed) and not failure_codes 定式，
  gate_sha256 自证（哈希投影排除 schema_version 与 gate_sha256 本身）；
  各桶子类化后追加自己的 schema_version 字面量与具名布尔位，用 hashing.hash_model 封印构建；
- FailClosedRegression：case_id + expected_accepted=False 定式（r116/r118 家族）——
  三个 Literal 字段使「突变被接受」的记录根本构造不出来，构造成功本身即断言拒绝；
- run_fail_closed：回归矩阵执行器；callback 未被拒绝时抛 ValueError（fail-closed）。

各桶的 FORBIDDEN_KEYS/FORBIDDEN_TEXT 常量集不合并、不入本模块（compilers 稿 §2.2）。
"""
from __future__ import annotations

from typing import Any, Callable, Final, Iterable, Literal

from pydantic import Field, StrictBool, StrictStr, model_validator

from .hashing import SHA256_HEX, canonical_hash
from .schema import StrictModel

GATE_CHECK_VERSION: Final = "examcard_common_gate_check_v1"
FAIL_CLOSED_REGRESSION_VERSION: Final = "examcard_common_fail_closed_regression_v1"

REJECTION_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError, KeyError)


class GateCheck(StrictModel):
    schema_version: Literal["examcard_common_gate_check_v1"] = GATE_CHECK_VERSION
    check_id: StrictStr = Field(min_length=3)
    passed: StrictBool
    detail: StrictStr = Field(min_length=1)


def failed_check_ids(checks: Iterable[GateCheck]) -> tuple[str, ...]:
    return tuple(item.check_id for item in checks if not item.passed)


class GateBase(StrictModel):
    valid: StrictBool
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[StrictStr, ...] = ()
    gate_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate_gate_invariant(self) -> "GateBase":
        expected = all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected:
            raise ValueError("gate validity mismatch")
        if self.gate_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"})):
            raise ValueError("gate hash mismatch")
        return self


class FailClosedRegression(StrictModel):
    schema_version: Literal["examcard_common_fail_closed_regression_v1"] = FAIL_CLOSED_REGRESSION_VERSION
    case_id: StrictStr = Field(min_length=1)
    expected_accepted: Literal[False] = False
    actual_accepted: Literal[False] = False
    passed: Literal[True] = True


def run_fail_closed(
    case_id: str,
    callback: Callable[[], Any],
    *,
    rejection_errors: tuple[type[Exception], ...] = REJECTION_ERRORS,
) -> FailClosedRegression:
    try:
        callback()
    except rejection_errors:
        return FailClosedRegression(case_id=case_id)
    raise ValueError(f"fail-closed regression accepted a forbidden mutation: {case_id}")
