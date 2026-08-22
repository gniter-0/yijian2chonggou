"""评审队列基元：队列项基类 + queue_sha256 计算与链绑定校验。

规范源 r83 SelectedReviewQueueItem + r10 Step3ReviewQueue（distill 设计 §2 规范源表）。
差异点按 §4 提炼规则参数化：schema_version / 身份前缀 / 呈现集合由具体期子类钉扎；
本模块只保留全家族共有的绑定核心与哈希链协议。

queue_sha256 是贯穿决策/manifest/审计/fixture 的链绑定标识（r84 `_queue_hash` 定式）：
canonical_hash([item.model_dump(mode="json") for item in items])，走 admission_legacy_hash
的 r8x 家族实现（旧语料回放路径；铁律 bc-b8b9aca6）。
"""
from __future__ import annotations

from typing import Any, Final, Iterable, Literal, Protocol, Sequence

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import HASH_PATTERN, ID_PATTERN, SourcePageBinding
from ...knowledge.contracts.admission_legacy_hash import canonical_hash
from ...materials.schema import StrictModel


class QueueItemLike(Protocol):
    """队列项的结构契约：把决策/审计/fixture 三处「鸭子类型化」写成可校验的形状。

    这三处判定既要吃 `ReviewQueueItemBase` 子类，也要吃 `AdmissionQueueItem`——两族
    字段一致却没有共同基类（各自独立钉扎 schema_version 字面量）。名义类型对不上，
    于是原先退回 `object`，代价是这些函数体内每一次字段访问都脱离类型检查（打错
    字段名要等运行时才炸）。这里只声明判定真正读取的字段，按结构约束。

    一律声明成只读 property：`StrictModel` 是 frozen 模型，pydantic 插件把字段暴露
    为只读属性；协议若写成可写变量，结构匹配会以「expected settable variable」失败。
    只读也更贴事实——判定方只读不写。
    """

    @property
    def queue_item_id(self) -> str: ...

    @property
    def subject_id(self) -> str: ...

    @property
    def record_id(self) -> str: ...

    @property
    def fact_id(self) -> str: ...

    @property
    def evidence_id(self) -> str: ...

    @property
    def presentation(self) -> Literal["diagram", "table", "ordered_list", "constraint", "formula"]: ...

    @property
    def source_manifest_sha256(self) -> str: ...

    @property
    def anchor_ids(self) -> tuple[str, ...]: ...

    @property
    def pages(self) -> tuple[SourcePageBinding, ...]: ...


def queue_sha256(items: Sequence[Any]) -> str:
    return canonical_hash([item.model_dump(mode="json") for item in items])


class ReviewQueueItemBase(StrictModel):
    """队列项内核：Fact/Evidence/呈现/锚点/源页绑定的公共核心。

    子类追加 schema_version 字面量、期身份字段与 entry 封印；本基类只校验
    「页绑定不得跨 source manifest」这一全家族不变量。
    """

    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_pages_within_manifest(self) -> "ReviewQueueItemBase":
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("queue item page crossed source manifest")
        return self


def validate_queue_chain(expected_queue_sha256: str, bound_objects: Iterable[Any]) -> None:
    """链绑定校验：所有携带 queue_sha256 的对象必须绑定同一队列哈希。"""
    for item in bound_objects:
        if item.queue_sha256 != expected_queue_sha256:
            raise ValueError("object crossed queue hash chain")


QUEUE_ITEM_VERSION: Final = "phase2r84_admission_queue_item_v1"
MANUAL_QUEUE_VERSION: Final = "phase2r84_manual_review_queue_v1"


class AdmissionQueueItem(StrictModel):
    """r84 严格摄取后的待审队列项（生产形态保真）：reviewed 不得滞留队列。"""

    schema_version: Literal["phase2r84_admission_queue_item_v1"] = QUEUE_ITEM_VERSION
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    source_decision_id: str | None = None
    decision_audit_id: str = Field(pattern=ID_PATTERN)
    typed_contract_authorized: Literal[False] = False
    reason: str = Field(min_length=3)
    entry_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_queue_item(self) -> "AdmissionQueueItem":
        if self.disposition == "reviewed_candidate":
            raise ValueError("reviewed candidates do not remain in admission queue")
        if self.entry_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"entry_sha256"})):
            raise ValueError("admission queue item hash mismatch")
        return self


class ManualReviewQueue(StrictModel):
    schema_version: Literal["phase2r84_manual_review_queue_v1"] = MANUAL_QUEUE_VERSION
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[AdmissionQueueItem, ...] = ()
    queue_count: int = Field(ge=0)
    queue_sha256_projection: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_queue(self) -> "ManualReviewQueue":
        if self.queue_count != len(self.candidates) or any(item.disposition != "manual_review" for item in self.candidates):
            raise ValueError("manual-review queue contains an unauthorized disposition")
        if self.queue_sha256_projection != canonical_hash([item.model_dump(mode="json") for item in self.candidates]):
            raise ValueError("manual-review projection hash mismatch")
        return self
