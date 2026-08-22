"""通用 N 图批量授权清单 v2（T4b 新增；v1 image_admission.py 零改动）。

依据 docs/design/live_pipeline_driver_design.md §7.2：v1 的
``ProductionAuthorizationManifest`` 冻结为「恰 3 决策、三呈现各恰一次」
（phase2r11101 钉扎），通用全链驱动的检查点② 需要 N ≥1 决策、呈现分布自由的
新版本号清单模型。``decision_source: "explicit_user_message"`` 语义保留：
清单只能从人工回填的评审底稿构建，驱动没有任何自动晋升路径。

自证哈希走 ``common.hashing``（新产出域；与旧契约法定哈希域不掺混——铁律
bc-b8b9aca6）。
"""
from __future__ import annotations

from typing import Any, Final, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel

__all__ = (
    "BATCH_IMAGE_DECISION_VERSION",
    "BATCH_IMAGE_MANIFEST_VERSION",
    "BatchImageDecision",
    "BatchImageAuthorizationManifest",
    "build_batch_image_authorization",
)

BATCH_IMAGE_DECISION_VERSION: Final = "image-admission-decision/2.0"
BATCH_IMAGE_MANIFEST_VERSION: Final = "image-admission-manifest/2.0"

_ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"


class BatchImageDecision(StrictModel):
    schema_version: Literal["image-admission-decision/2.0"] = BATCH_IMAGE_DECISION_VERSION
    card_index: int = Field(ge=1)
    source_card_id: str = Field(pattern=_ID)
    render_presentation: str = Field(min_length=2)
    image_sha256: str = Field(pattern=SHA256_HEX)
    disposition: Literal["approved", "rejected", "manual_review"]
    reviewer: str = Field(min_length=1)
    basis: str = Field(min_length=3)
    decision_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_decision(self) -> "BatchImageDecision":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "decision_sha256"}))
        if self.decision_sha256 != expected:
            raise ValueError("batch image decision hash mismatch")
        return self


class BatchImageAuthorizationManifest(StrictModel):
    schema_version: Literal["image-admission-manifest/2.0"] = BATCH_IMAGE_MANIFEST_VERSION
    decision_source: Literal["explicit_user_message"] = "explicit_user_message"
    decisions: tuple[BatchImageDecision, ...] = Field(min_length=1)
    decision_count: int = Field(ge=1)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_manifest(self) -> "BatchImageAuthorizationManifest":
        if self.decision_count != len(self.decisions):
            raise ValueError("batch image manifest decision count mismatch")
        if len({item.card_index for item in self.decisions}) != self.decision_count:
            raise ValueError("batch image manifest card indexes must be unique")
        counts = {
            "approved": sum(1 for item in self.decisions if item.disposition == "approved"),
            "rejected": sum(1 for item in self.decisions if item.disposition == "rejected"),
            "manual_review": sum(1 for item in self.decisions if item.disposition == "manual_review"),
        }
        if (self.approved_count, self.rejected_count, self.manual_review_count) != (
            counts["approved"],
            counts["rejected"],
            counts["manual_review"],
        ):
            raise ValueError("batch image manifest disposition counts mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "manifest_sha256"}))
        if self.manifest_sha256 != expected:
            raise ValueError("batch image manifest hash mismatch")
        return self


def build_batch_image_authorization(rows: Sequence[Mapping[str, Any]]) -> BatchImageAuthorizationManifest:
    """从人工回填的评审底稿行构建授权清单（缺 disposition/reviewer/basis 即拒）。"""

    decisions = []
    for row in rows:
        payload = {
            "card_index": row["card_index"],
            "source_card_id": row["source_card_id"],
            "render_presentation": row.get("render_presentation") or row.get("presentation"),
            "image_sha256": row["image_sha256"],
            "disposition": row["disposition"],
            "reviewer": row["reviewer"],
            "basis": row["basis"],
        }
        decisions.append(hash_model(BatchImageDecision, payload, "decision_sha256"))
    manifest_payload = {
        "decisions": tuple(decisions),
        "decision_count": len(decisions),
        "approved_count": sum(1 for item in decisions if item.disposition == "approved"),
        "rejected_count": sum(1 for item in decisions if item.disposition == "rejected"),
        "manual_review_count": sum(1 for item in decisions if item.disposition == "manual_review"),
    }
    return hash_model(BatchImageAuthorizationManifest, manifest_payload, "manifest_sha256")
