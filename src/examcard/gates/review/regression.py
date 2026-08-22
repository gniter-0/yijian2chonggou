"""准入回归矩阵：桶内 RegressionResult 形态 + 负向用例族骨架。

桶内记录形态取 r84（case_id / expected_failure_code / caught / detail，无封印——
矩阵整体随 gate 封印）；执行器语义取 r10x 家族 `_fail_closed`（拒绝异常集 =
ValueError/TypeError/KeyError，pydantic ValidationError 是 ValueError 子类天然覆盖）。
新建 CI 门禁类回归请直接用 common.gates.run_fail_closed（构造即断言定式）。
"""
from __future__ import annotations

from typing import Any, Callable, Final, Iterable, Literal, Mapping

from pydantic import Field

from ...materials.schema import StrictModel

REGRESSION_VERSION: Final = "phase2r84_regression_result_v1"

REJECTION_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError, KeyError)


class RegressionResult(StrictModel):
    schema_version: Literal["phase2r84_regression_result_v1"] = REGRESSION_VERSION
    case_id: str = Field(min_length=1)
    expected_failure_code: str = Field(min_length=1)
    caught: bool
    detail: str = Field(min_length=1)


def run_admission_regression(case_id: str, expected_failure_code: str, operation: Callable[[], Any]) -> RegressionResult:
    caught = False
    detail = "mutation was unexpectedly accepted"
    try:
        operation()
    except REJECTION_ERRORS as error:
        caught = True
        first_line = str(error).strip().splitlines()[0] if str(error).strip() else ""
        detail = first_line[:200] or "rejected"
    return RegressionResult(case_id=case_id, expected_failure_code=expected_failure_code, caught=caught, detail=detail)


def build_regression_matrix(cases: Mapping[str, tuple[str, Callable[[], Any]]]) -> tuple[RegressionResult, ...]:
    """负向用例族骨架：{case_id: (expected_failure_code, operation)} → 结果矩阵。"""
    return tuple(
        run_admission_regression(case_id, expected_code, operation)
        for case_id, (expected_code, operation) in cases.items()
    )


def all_regressions_caught(matrix: Iterable[RegressionResult]) -> bool:
    items = tuple(matrix)
    return bool(items) and all(item.caught for item in items)
