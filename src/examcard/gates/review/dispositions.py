"""四态处置与准入依据（distill_gates_review_design §2/§4，规范源 r84/r82/r810）。

设计公理「四态处置守恒」：reviewed_candidate / manual_review / blocked / not_applicable；
非 reviewed 状态在任何投影/合并/导出中原样保留、永不静默升格；浏览器界（browser_demo）
的 reviewed_candidate 一律降格为 manual_review 且 admission_basis 记为
browser_demo_not_authority。
"""
from __future__ import annotations

from typing import Iterable, Literal, Mapping

Disposition = Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
DISPOSITIONS: tuple[Disposition, ...] = ("reviewed_candidate", "manual_review", "blocked", "not_applicable")

Presentation = Literal["diagram", "table", "ordered_list", "constraint", "formula"]
PRESENTATIONS: tuple[Presentation, ...] = ("diagram", "table", "ordered_list", "constraint", "formula")

AdmissionBasis = Literal[
    "no_production_human_decision",
    "browser_demo_not_authority",
    "explicit_human_reviewed_candidate",
    "explicit_human_manual_review",
    "explicit_human_blocked",
    "explicit_human_not_applicable",
]
ADMISSION_BASES: tuple[str, ...] = (
    "no_production_human_decision",
    "browser_demo_not_authority",
    "explicit_human_reviewed_candidate",
    "explicit_human_manual_review",
    "explicit_human_blocked",
    "explicit_human_not_applicable",
)

# 图片目检（r11101 家族）的三态处置；approved 之外一律不产 reviewed 卡。
ImageDisposition = Literal["approved", "rejected", "manual_review"]
IMAGE_DISPOSITIONS: tuple[ImageDisposition, ...] = ("approved", "rejected", "manual_review")


def effective_disposition(input_disposition: str, *, browser_demo: bool) -> str:
    """浏览器界的 reviewed_candidate 降格 manual_review；其余原样（永不升格）。"""
    if input_disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {input_disposition}")
    if browser_demo and input_disposition == "reviewed_candidate":
        return "manual_review"
    return input_disposition


def derive_admission_basis(disposition: str | None, *, browser_demo: bool) -> str:
    """r84 `_audit_for` 的 admission_basis 推导定式。"""
    if disposition is None:
        return "no_production_human_decision"
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {disposition}")
    if browser_demo:
        return "browser_demo_not_authority"
    return f"explicit_human_{disposition}"


def count_dispositions(values: Iterable[str]) -> dict[str, int]:
    counts = {state: 0 for state in DISPOSITIONS}
    for value in values:
        if value not in counts:
            raise ValueError(f"unknown disposition: {value}")
        counts[value] += 1
    return counts


def assert_disposition_conserved(before: Mapping[str, str], after: Mapping[str, str]) -> None:
    """处置守恒断言：键集一致；非 reviewed 输入必须原样保留；升格一律拒绝。

    唯一合法迁移是 reviewed_candidate -> manual_review（浏览器界降格 /
    等待显式授权），其余任何迁移都视为静默升格或篡改。
    """
    if set(before) != set(after):
        raise ValueError("disposition conservation broken: item sets differ")
    for key, source in before.items():
        target = after[key]
        if source == target:
            continue
        if source == "reviewed_candidate" and target == "manual_review":
            continue
        raise ValueError(f"disposition conservation broken for {key}: {source} -> {target}")
