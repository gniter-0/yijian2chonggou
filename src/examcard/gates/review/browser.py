"""非权威浏览器界：raw 草稿信封 / 决策分类 / 进度快照（规范源 r87，次要 r102/r85/r82/r83）。

两界原则的结构化落地：浏览器输出永远携带 pending 占位哈希与 browser_demo 标记，
其形态在类型层面就无法通过权威模型（HumanDecision 的 decision_sha256 模式约束 +
browser_demo Literal[False]）；只有经 normalize_raw_draft 重绑定冻结候选、
由 CLI 严格摄取重算哈希后才可能进入权威链。

本模块的信封/进度模型为新代码形态（examcard_review_* 版本串），自证哈希按铁律
bc-b8b9aca6 使用 common 底座（新产出域）；classify_browser_decision 为 r82 定式。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel
from .dispositions import count_dispositions

RAW_DECISION_VERSION = "examcard_review_raw_decision_v1"
RAW_MANIFEST_VERSION = "examcard_review_raw_draft_manifest_v1"
PROGRESS_ENTRY_VERSION = "examcard_review_progress_entry_v1"
PROGRESS_VERSION = "examcard_review_progress_v1"

PENDING_DECISION_HASH = "browser-export-pending-cli-hash"
PENDING_MANIFEST_HASH = "raw-review-draft-pending-normalization"

_ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"


class RawReviewDecision(StrictModel):
    """浏览器导出的单条草稿决定：占位哈希 + browser_demo 恒真，结构上不可权威。"""

    schema_version: Literal["examcard_review_raw_decision_v1"] = RAW_DECISION_VERSION
    draft_id: str = Field(pattern=_ID)
    candidate_id: str = Field(pattern=_ID)
    queue_item_id: str = Field(pattern=_ID)
    queue_sha256: str = Field(pattern=SHA256_HEX)
    disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: bool = False
    anchor_confirmed: bool = False
    source_page_confirmed: bool = False
    image_hash_confirmed: bool = False
    typed_contract_confirmed: bool = False
    typed_contract_payload: dict[str, Any] | None = None
    recorded_at: str = Field(min_length=1)
    browser_demo: Literal[True] = True
    production_fixture_authorized: Literal[False] = False
    decision_sha256: Literal["browser-export-pending-cli-hash"] = PENDING_DECISION_HASH


class RawReviewDraftManifest(StrictModel):
    """raw 草稿信封（可续传）：pending 清单哈希；决不作为权威输入。"""

    schema_version: Literal["examcard_review_raw_draft_manifest_v1"] = RAW_MANIFEST_VERSION
    manifest_id: str = Field(pattern=_ID)
    queue_sha256: str = Field(pattern=SHA256_HEX)
    decisions: tuple[RawReviewDecision, ...] = ()
    decision_count: int = Field(ge=0)
    browser_demo: Literal[True] = True
    manifest_sha256: Literal["raw-review-draft-pending-normalization"] = PENDING_MANIFEST_HASH

    @model_validator(mode="after")
    def validate_raw_manifest(self) -> "RawReviewDraftManifest":
        if self.decision_count != len(self.decisions):
            raise ValueError("raw draft decision count mismatch")
        if len({item.draft_id for item in self.decisions}) != self.decision_count:
            raise ValueError("raw draft identities must be unique")
        if len({item.candidate_id for item in self.decisions}) != self.decision_count:
            raise ValueError("raw draft candidates must be unique")
        if any(item.queue_sha256 != self.queue_sha256 for item in self.decisions):
            raise ValueError("raw draft crossed queue hash")
        return self


class ProgressEntry(StrictModel):
    schema_version: Literal["examcard_review_progress_entry_v1"] = PROGRESS_ENTRY_VERSION
    candidate_id: str = Field(pattern=_ID)
    queue_item_id: str = Field(pattern=_ID)
    subject_id: str = Field(pattern=_ID)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    explicit_decision: bool
    draft_id: str | None = None
    reviewer: str | None = None
    entry_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_entry(self) -> "ProgressEntry":
        if self.explicit_decision != (self.draft_id is not None):
            raise ValueError("progress explicit decision mismatch")
        if self.entry_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "entry_sha256"})):
            raise ValueError("progress entry hash mismatch")
        return self


class Progress(StrictModel):
    schema_version: Literal["examcard_review_progress_v1"] = PROGRESS_VERSION
    queue_sha256: str = Field(pattern=SHA256_HEX)
    entries: tuple[ProgressEntry, ...] = ()
    candidate_count: int = Field(ge=0)
    reviewed_candidate_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    progress_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_progress(self) -> "Progress":
        if self.candidate_count != len(self.entries):
            raise ValueError("progress candidate count mismatch")
        counts = count_dispositions(item.disposition for item in self.entries)
        actual = (self.reviewed_candidate_count, self.manual_review_count, self.blocked_count, self.not_applicable_count)
        if actual != (counts["reviewed_candidate"], counts["manual_review"], counts["blocked"], counts["not_applicable"]):
            raise ValueError("progress disposition counts mismatch")
        if self.progress_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "progress_sha256"})):
            raise ValueError("progress hash mismatch")
        return self


def build_progress(queue_sha256: str, entries: Sequence[Mapping[str, Any]]) -> Progress:
    sealed = tuple(hash_model(ProgressEntry, dict(entry), "entry_sha256") for entry in entries)
    counts = count_dispositions(item.disposition for item in sealed)
    return hash_model(
        Progress,
        {
            "queue_sha256": queue_sha256,
            "entries": sealed,
            "candidate_count": len(sealed),
            "reviewed_candidate_count": counts["reviewed_candidate"],
            "manual_review_count": counts["manual_review"],
            "blocked_count": counts["blocked"],
            "not_applicable_count": counts["not_applicable"],
        },
        "progress_sha256",
    )


def classify_browser_decision(decision: Any) -> tuple[str, str]:
    """r82 定式：浏览器 reviewed_candidate 必须五确认齐备，且一律降格 manual_review。"""
    if decision.disposition == "reviewed_candidate":
        if not all(
            (
                decision.source_text_confirmed,
                decision.anchor_confirmed,
                decision.source_page_confirmed,
                decision.image_hash_confirmed,
                decision.typed_contract_confirmed,
            )
        ):
            raise ValueError("reviewed candidate decision requires all five confirmations")
        return "manual_review", "browser_demo_reviewed_candidate_blocked"
    return decision.disposition, {
        "manual_review": "explicit_manual_review_disposition",
        "blocked": "explicit_blocked_disposition",
        "not_applicable": "explicit_not_applicable_disposition",
    }[decision.disposition]


def normalize_raw_draft(
    raw: RawReviewDraftManifest,
    candidates_by_id: Mapping[str, Any],
    *,
    expected_queue_sha256: str,
    decision_builder: Callable[[RawReviewDecision, Any], Any],
) -> tuple[Any, ...]:
    """r87 定式骨架：raw 草稿逐条重绑定冻结候选并经 decision_builder 重算哈希。

    未知候选一律拒绝；队列哈希越界一律拒绝。builder 由具体期注入（如构建 r84
    HumanDecision），normalize 本身不发明权威身份。
    """
    if raw.queue_sha256 != expected_queue_sha256:
        raise ValueError("raw draft crossed expected queue hash")
    normalized: list[Any] = []
    for raw_decision in raw.decisions:
        candidate = candidates_by_id.get(raw_decision.candidate_id)
        if candidate is None:
            raise ValueError("raw draft references unknown candidate")
        normalized.append(decision_builder(raw_decision, candidate))
    return tuple(normalized)


def summarize_browser_input(decisions: Iterable[Any]) -> dict[str, int]:
    """r84 BrowserInputAudit 的计数投影：browser_demo 条目全部拒绝用于生产。"""
    items = tuple(decisions)
    browser_demo_count = sum(1 for item in items if getattr(item, "browser_demo", False))
    return {
        "decision_count": len(items),
        "browser_demo_count": browser_demo_count,
        "rejected_for_production_count": browser_demo_count,
        "production_authorized_count": 0,
    }
