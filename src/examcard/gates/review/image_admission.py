"""Step 4 图片人工目检准入（规范源 phase2r11101，生产形态逐字面量保真）。

状态机：SourceImageBinding（冻结绑定）→ 逐张 HumanReviewDecision（approved 必须
显式授权，非 approved 不得携带授权）→ ProductionAuthorizationManifest（三呈现
恰一次覆盖）→ admit_images 严格摄取（approved→ReviewedImageCard；rejected/
manual_review→PostReviewItem；全量 AdmissionAudit 汇总为会话审计）。

本家族属 r9/r10x 哈希谱系：自证哈希投影排除 {schema_version, 封印字段}，
一律绑 admission_legacy_hash 的 r10x 实现（铁律 bc-b8b9aca6）。
形状钉扎（1024x1536、三呈现、恰三决策）是生产数据格式的一部分，随模型保真；
上游 R11.10 冻结哈希常量与 run 路径按提炼规则留旧仓。
"""
from __future__ import annotations

from typing import Final, Literal, Mapping, Sequence

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ...knowledge.contracts.admission_legacy_hash import canonical_hash_r10x, hash_model_r10x
from ...materials.schema import StrictModel

BINDING_VERSION: Final = "phase2r11101_source_image_binding_v1"
DECISION_VERSION: Final = "phase2r11101_human_review_decision_v1"
MANIFEST_VERSION: Final = "phase2r11101_production_authorization_manifest_v1"
FIXTURE_VERSION: Final = "phase2r11101_reviewed_image_fixture_v1"
QUEUE_VERSION: Final = "phase2r11101_post_review_queue_v1"
AUDIT_VERSION: Final = "phase2r11101_admission_audit_v1"

HASH = r"^[0-9a-f]{64}$"
CARD_ID = r"^step3-card-contract:[0-9a-f]{24}$"
IMAGE_PATH = r"^images/step4-card-[0-9a-f]{24}\.png$"
IMAGE_WIDTH: Final = 1024
IMAGE_HEIGHT: Final = 1536
IMAGE_PRESENTATIONS: tuple[str, ...] = ("table", "ordered_list", "formula")


class SourceImageBinding(StrictModel):
    schema_version: Literal["phase2r11101_source_image_binding_v1"] = BINDING_VERSION
    source_card_id: StrictStr = Field(pattern=CARD_ID)
    presentation: Literal["table", "ordered_list", "formula"]
    source_texts: tuple[StrictStr, ...] = Field(min_length=1)
    fact_ids: tuple[StrictStr, ...] = Field(min_length=1)
    evidence_ids: tuple[StrictStr, ...] = Field(min_length=1)
    anchor_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_page_ref_ids: tuple[StrictStr, ...] = Field(min_length=1)
    request_sha256: StrictStr = Field(pattern=HASH)
    image_record_sha256: StrictStr = Field(pattern=HASH)
    image_path: StrictStr = Field(pattern=IMAGE_PATH)
    image_sha256: StrictStr = Field(pattern=HASH)
    image_width: Literal[1024] = IMAGE_WIDTH
    image_height: Literal[1536] = IMAGE_HEIGHT
    image_gate_sha256: StrictStr = Field(pattern=HASH)
    source_contract_sha256: StrictStr = Field(pattern=HASH)
    typed_contract_sha256: StrictStr = Field(pattern=HASH)
    provenance_sha256: StrictStr = Field(pattern=HASH)
    geometry_sha256: StrictStr = Field(pattern=HASH)
    source_text_sha256: StrictStr = Field(pattern=HASH)
    binding_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_binding(self) -> "SourceImageBinding":
        if self.source_text_sha256 != canonical_hash_r10x(self.source_texts):
            raise ValueError("source text hash mismatch")
        if self.binding_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "binding_sha256"})):
            raise ValueError("source image binding hash mismatch")
        return self


class HumanReviewDecision(StrictModel):
    schema_version: Literal["phase2r11101_human_review_decision_v1"] = DECISION_VERSION
    decision_id: StrictStr = Field(pattern=r"^phase2r11101-human-decision:[0-9a-f]{24}$")
    input_freeze_sha256: StrictStr = Field(pattern=HASH)
    source_card_id: StrictStr = Field(pattern=CARD_ID)
    presentation: Literal["table", "ordered_list", "formula"]
    disposition: Literal["approved", "rejected", "manual_review"]
    explicit_human_authorization: StrictBool
    reviewer_id: StrictStr = Field(min_length=1)
    decision_source: Literal["explicit_user_message"] = "explicit_user_message"
    review_basis: StrictStr = Field(min_length=3)
    source_texts: tuple[StrictStr, ...] = Field(min_length=1)
    fact_ids: tuple[StrictStr, ...] = Field(min_length=1)
    evidence_ids: tuple[StrictStr, ...] = Field(min_length=1)
    anchor_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_page_ref_ids: tuple[StrictStr, ...] = Field(min_length=1)
    request_sha256: StrictStr = Field(pattern=HASH)
    image_record_sha256: StrictStr = Field(pattern=HASH)
    image_sha256: StrictStr = Field(pattern=HASH)
    image_width: Literal[1024] = IMAGE_WIDTH
    image_height: Literal[1536] = IMAGE_HEIGHT
    image_gate_sha256: StrictStr = Field(pattern=HASH)
    source_contract_sha256: StrictStr = Field(pattern=HASH)
    typed_contract_sha256: StrictStr = Field(pattern=HASH)
    provenance_sha256: StrictStr = Field(pattern=HASH)
    geometry_sha256: StrictStr = Field(pattern=HASH)
    decision_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_decision(self) -> "HumanReviewDecision":
        if self.disposition == "approved" and not self.explicit_human_authorization:
            raise ValueError("approved image requires explicit human authorization")
        if self.disposition != "approved" and self.explicit_human_authorization:
            raise ValueError("non-approved image cannot carry authorization")
        if self.decision_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "decision_sha256"})):
            raise ValueError("human review decision hash mismatch")
        return self


class ProductionAuthorizationManifest(StrictModel):
    schema_version: Literal["phase2r11101_production_authorization_manifest_v1"] = MANIFEST_VERSION
    manifest_id: StrictStr = Field(pattern=r"^phase2r11101-human-review-manifest:[0-9a-f]{24}$")
    input_freeze_sha256: StrictStr = Field(pattern=HASH)
    explicit_user_attestation: Literal[True] = True
    decision_source: Literal["explicit_user_message"] = "explicit_user_message"
    decisions: tuple[HumanReviewDecision, ...] = Field(min_length=3, max_length=3)
    decision_count: Literal[3] = 3
    manifest_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ProductionAuthorizationManifest":
        if self.input_freeze_sha256 != self.decisions[0].input_freeze_sha256:
            raise ValueError("authorization manifest crossed input freeze")
        if len({item.source_card_id for item in self.decisions}) != self.decision_count:
            raise ValueError("authorization decisions must be exactly once")
        if any(item.input_freeze_sha256 != self.input_freeze_sha256 or item.decision_source != self.decision_source for item in self.decisions):
            raise ValueError("authorization decision source binding mismatch")
        if self.manifest_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "manifest_sha256"})):
            raise ValueError("authorization manifest hash mismatch")
        return self


class ReviewedImageCard(StrictModel):
    schema_version: Literal["phase2r11101_reviewed_image_fixture_v1"] = FIXTURE_VERSION
    source_card_id: StrictStr = Field(pattern=CARD_ID)
    presentation: Literal["table", "ordered_list", "formula"]
    source_texts: tuple[StrictStr, ...] = Field(min_length=1)
    fact_ids: tuple[StrictStr, ...] = Field(min_length=1)
    evidence_ids: tuple[StrictStr, ...] = Field(min_length=1)
    anchor_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_page_ref_ids: tuple[StrictStr, ...] = Field(min_length=1)
    request_sha256: StrictStr = Field(pattern=HASH)
    image_record_sha256: StrictStr = Field(pattern=HASH)
    image_path: StrictStr = Field(pattern=IMAGE_PATH)
    image_sha256: StrictStr = Field(pattern=HASH)
    image_width: Literal[1024] = IMAGE_WIDTH
    image_height: Literal[1536] = IMAGE_HEIGHT
    image_gate_sha256: StrictStr = Field(pattern=HASH)
    source_contract_sha256: StrictStr = Field(pattern=HASH)
    typed_contract_sha256: StrictStr = Field(pattern=HASH)
    provenance_sha256: StrictStr = Field(pattern=HASH)
    geometry_sha256: StrictStr = Field(pattern=HASH)
    decision_sha256: StrictStr = Field(pattern=HASH)
    reviewed_state: Literal["reviewed_image"] = "reviewed_image"
    reviewed_card_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_card(self) -> "ReviewedImageCard":
        if self.reviewed_card_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "reviewed_card_sha256"})):
            raise ValueError("reviewed image Card hash mismatch")
        return self


class ReviewedImageFixture(StrictModel):
    schema_version: Literal["phase2r11101_reviewed_image_fixture_v1"] = FIXTURE_VERSION
    input_freeze_sha256: StrictStr = Field(pattern=HASH)
    authorization_manifest_sha256: StrictStr = Field(pattern=HASH)
    cards: tuple[ReviewedImageCard, ...] = Field(min_length=1, max_length=3)
    reviewed_card_count: StrictInt = Field(gt=0, le=3)
    fixture_authorized: Literal[True] = True
    batch_generation_authorized: Literal[False] = False
    fixture_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedImageFixture":
        if self.reviewed_card_count != len(self.cards) or len({item.source_card_id for item in self.cards}) != len(self.cards):
            raise ValueError("reviewed image fixture cards are not exactly once")
        if self.fixture_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"})):
            raise ValueError("reviewed image fixture hash mismatch")
        return self


class PostReviewItem(StrictModel):
    schema_version: Literal["phase2r11101_post_review_queue_v1"] = QUEUE_VERSION
    source_card_id: StrictStr = Field(pattern=CARD_ID)
    presentation: Literal["table", "ordered_list", "formula"]
    disposition: Literal["rejected", "manual_review"]
    reason: StrictStr = Field(min_length=1)
    source_contract_sha256: StrictStr = Field(pattern=HASH)
    image_record_sha256: StrictStr = Field(pattern=HASH)
    item_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_item(self) -> "PostReviewItem":
        if self.item_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "item_sha256"})):
            raise ValueError("post-review item hash mismatch")
        return self


class AdmissionAudit(StrictModel):
    schema_version: Literal["phase2r11101_admission_audit_v1"] = AUDIT_VERSION
    input_freeze_sha256: StrictStr = Field(pattern=HASH)
    authorization_manifest_sha256: StrictStr = Field(pattern=HASH)
    source_card_id: StrictStr = Field(pattern=CARD_ID)
    presentation: Literal["table", "ordered_list", "formula"]
    disposition: Literal["approved", "rejected", "manual_review"]
    explicit_human_authorization: StrictBool
    admitted: StrictBool
    reason: StrictStr = Field(min_length=1)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    audit_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_audit(self) -> "AdmissionAudit":
        if self.admitted != (self.disposition == "approved" and self.explicit_human_authorization):
            raise ValueError("admission audit authorization mismatch")
        if self.audit_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "audit_sha256"})):
            raise ValueError("admission audit hash mismatch")
        return self


class AdmissionAuditSession(StrictModel):
    schema_version: Literal["phase2r11101_admission_audit_v1"] = AUDIT_VERSION
    input_freeze_sha256: StrictStr = Field(pattern=HASH)
    authorization_manifest_sha256: StrictStr = Field(pattern=HASH)
    audits: tuple[AdmissionAudit, ...] = Field(min_length=3, max_length=3)
    admitted_count: StrictInt = Field(ge=0, le=3)
    manual_review_count: StrictInt = Field(ge=0, le=3)
    rejected_count: StrictInt = Field(ge=0, le=3)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    audit_sha256: StrictStr = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_session(self) -> "AdmissionAuditSession":
        if len(self.audits) != self.admitted_count + self.manual_review_count + self.rejected_count:
            raise ValueError("admission audit disposition counts mismatch")
        if self.audit_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "audit_sha256"})):
            raise ValueError("admission session audit hash mismatch")
        return self


_BOUND_FIELDS = (
    "source_texts", "fact_ids", "evidence_ids", "anchor_ids", "source_page_ref_ids",
    "request_sha256", "image_record_sha256", "image_sha256", "image_width", "image_height",
    "image_gate_sha256", "source_contract_sha256", "typed_contract_sha256",
    "provenance_sha256", "geometry_sha256",
)


def validate_image_decision(decision: HumanReviewDecision, binding: SourceImageBinding, freeze_sha256: str) -> None:
    """决策必须逐字段绑定冻结的图像绑定（r11101 `_validate_decision` 语义）。"""
    if decision.input_freeze_sha256 != freeze_sha256 or decision.source_card_id != binding.source_card_id or decision.presentation != binding.presentation:
        raise ValueError("human decision crossed source binding")
    for field in _BOUND_FIELDS:
        if getattr(decision, field) != getattr(binding, field):
            raise ValueError(f"human decision {field} mismatch")
    if not decision.reviewer_id or decision.decision_source != "explicit_user_message":
        raise ValueError("human decision reviewer/source is not authorized")


def image_binding_decision(
    binding: SourceImageBinding,
    freeze_sha256: str,
    disposition: str,
    reviewer_id: str,
    review_basis: str | None = None,
) -> HumanReviewDecision:
    if disposition not in ("approved", "rejected", "manual_review"):
        raise ValueError(f"unknown human disposition: {disposition}")
    payload = {
        "decision_id": f"phase2r11101-human-decision:{canonical_hash_r10x((binding.source_card_id, freeze_sha256, disposition, reviewer_id))[:24]}",
        "input_freeze_sha256": freeze_sha256,
        "source_card_id": binding.source_card_id,
        "presentation": binding.presentation,
        "disposition": disposition,
        "explicit_human_authorization": disposition == "approved",
        "reviewer_id": reviewer_id,
        "review_basis": review_basis or f"explicit human visual review: {disposition}",
        "source_texts": binding.source_texts,
        "fact_ids": binding.fact_ids,
        "evidence_ids": binding.evidence_ids,
        "anchor_ids": binding.anchor_ids,
        "source_page_ref_ids": binding.source_page_ref_ids,
        "request_sha256": binding.request_sha256,
        "image_record_sha256": binding.image_record_sha256,
        "image_sha256": binding.image_sha256,
        "image_gate_sha256": binding.image_gate_sha256,
        "source_contract_sha256": binding.source_contract_sha256,
        "typed_contract_sha256": binding.typed_contract_sha256,
        "provenance_sha256": binding.provenance_sha256,
        "geometry_sha256": binding.geometry_sha256,
    }
    return hash_model_r10x(HumanReviewDecision, payload, "decision_sha256")


def build_image_manifest(
    bindings: Sequence[SourceImageBinding],
    freeze_sha256: str,
    dispositions: Mapping[str, str],
    reviewer_id: str,
) -> ProductionAuthorizationManifest:
    """三呈现恰一次覆盖：dispositions 键集必须等于绑定的呈现集合（r11101 build_manifest 语义）。"""
    if {binding.presentation for binding in bindings} != set(IMAGE_PRESENTATIONS) or len(bindings) != 3:
        raise ValueError("image admission requires exactly the three frozen presentations")
    if set(dispositions) != set(IMAGE_PRESENTATIONS):
        raise ValueError("human review must explicitly cover table, ordered_list and formula")
    decisions = tuple(
        image_binding_decision(binding, freeze_sha256, dispositions[binding.presentation], reviewer_id)
        for binding in bindings
    )
    payload = {
        "manifest_id": f"phase2r11101-human-review-manifest:{canonical_hash_r10x(tuple(item.decision_id for item in decisions))[:24]}",
        "input_freeze_sha256": freeze_sha256,
        "decisions": decisions,
        "decision_count": 3,
    }
    return hash_model_r10x(ProductionAuthorizationManifest, payload, "manifest_sha256")


def _reviewed_card(binding: SourceImageBinding, decision: HumanReviewDecision) -> ReviewedImageCard:
    """r11101 `_reviewed_card` 定式（字段清单与文案逐字保真，黄金重放对齐）。"""
    if decision.disposition != "approved" or not decision.explicit_human_authorization:
        raise ValueError("only explicitly approved images may enter reviewed fixture")
    return hash_model_r10x(
        ReviewedImageCard,
        {
            "source_card_id": binding.source_card_id, "presentation": binding.presentation,
            "source_texts": binding.source_texts, "fact_ids": binding.fact_ids, "evidence_ids": binding.evidence_ids,
            "anchor_ids": binding.anchor_ids, "source_page_ref_ids": binding.source_page_ref_ids,
            "request_sha256": binding.request_sha256, "image_record_sha256": binding.image_record_sha256,
            "image_path": binding.image_path, "image_sha256": binding.image_sha256,
            "image_width": binding.image_width, "image_height": binding.image_height,
            "image_gate_sha256": binding.image_gate_sha256, "source_contract_sha256": binding.source_contract_sha256,
            "typed_contract_sha256": binding.typed_contract_sha256, "provenance_sha256": binding.provenance_sha256,
            "geometry_sha256": binding.geometry_sha256, "decision_sha256": decision.decision_sha256,
        },
        "reviewed_card_sha256",
    )


def _post_review_item(binding: SourceImageBinding, decision: HumanReviewDecision) -> PostReviewItem:
    return hash_model_r10x(
        PostReviewItem,
        {
            "source_card_id": binding.source_card_id,
            "presentation": binding.presentation,
            "disposition": "rejected" if decision.disposition == "rejected" else "manual_review",
            "reason": "explicit user rejection" if decision.disposition == "rejected" else "no approval for strict admission",
            "source_contract_sha256": binding.source_contract_sha256,
            "image_record_sha256": binding.image_record_sha256,
        },
        "item_sha256",
    )


def admit_images(
    manifest: ProductionAuthorizationManifest,
    bindings: Sequence[SourceImageBinding],
    freeze_sha256: str,
) -> tuple[ReviewedImageFixture | None, tuple[PostReviewItem, ...], AdmissionAuditSession]:
    """严格摄取状态机（r11101 `build_admission` 定式）：approved→reviewed 卡；
    rejected/manual_review→post-review 队列；全量审计。按冻结绑定顺序迭代并要求
    决策对绑定恰一次覆盖；零 approved 时返回 None fixture（不产空 fixture）。
    """
    if manifest.input_freeze_sha256 != freeze_sha256:
        raise ValueError("authorization manifest does not bind input freeze")
    if {item.source_card_id for item in manifest.decisions} != {item.source_card_id for item in bindings}:
        raise ValueError("authorization manifest must cover every source image exactly once")
    by_card = {item.source_card_id: item for item in manifest.decisions}
    cards: list[ReviewedImageCard] = []
    queue: list[PostReviewItem] = []
    audits: list[AdmissionAudit] = []
    for binding in bindings:
        decision = by_card[binding.source_card_id]
        validate_image_decision(decision, binding, freeze_sha256)
        admitted = decision.disposition == "approved" and decision.explicit_human_authorization
        if admitted:
            cards.append(_reviewed_card(binding, decision))
        else:
            queue.append(_post_review_item(binding, decision))
        audits.append(
            hash_model_r10x(
                AdmissionAudit,
                {
                    "input_freeze_sha256": freeze_sha256,
                    "authorization_manifest_sha256": manifest.manifest_sha256,
                    "source_card_id": binding.source_card_id,
                    "presentation": binding.presentation,
                    "disposition": decision.disposition,
                    "explicit_human_authorization": decision.explicit_human_authorization,
                    "admitted": admitted,
                    "reason": "explicitly admitted" if admitted else "kept outside reviewed fixture",
                    "network_calls": 0,
                    "model_calls": 0,
                    "relay_calls": 0,
                    "image_api_calls": 0,
                },
                "audit_sha256",
            )
        )
    fixture = None
    if cards:
        fixture = hash_model_r10x(
            ReviewedImageFixture,
            {
                "input_freeze_sha256": freeze_sha256,
                "authorization_manifest_sha256": manifest.manifest_sha256,
                "cards": tuple(cards),
                "reviewed_card_count": len(cards),
                "fixture_authorized": True,
                "batch_generation_authorized": False,
            },
            "fixture_sha256",
        )
    session = hash_model_r10x(
        AdmissionAuditSession,
        {
            "input_freeze_sha256": freeze_sha256,
            "authorization_manifest_sha256": manifest.manifest_sha256,
            "audits": tuple(audits),
            "admitted_count": len(cards),
            "manual_review_count": sum(item.disposition == "manual_review" for item in manifest.decisions),
            "rejected_count": sum(item.disposition == "rejected" for item in manifest.decisions),
            "network_calls": 0,
            "model_calls": 0,
            "relay_calls": 0,
            "image_api_calls": 0,
        },
        "audit_sha256",
    )
    return fixture, tuple(queue), session
