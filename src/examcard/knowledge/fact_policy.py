"""内容策略裁决（拆分层 1）：噪声角色匹配与 PolicyDecision 生成。"""
from __future__ import annotations

import hashlib
import re

from ..evidence.schema import EvidenceUnit
from ..materials.schema import (
    NOISE_ROLES,
    ContentPolicy,
    CoverageRequirement,
    PolicyDecision,
    PolicyDisposition,
    SemanticRole,
)


def _policy_decision_id(*, policy: ContentPolicy, evidence_id: str) -> str:
    digest = hashlib.sha256(f"{policy.policy_id}:{policy.revision}:{evidence_id}".encode("utf-8")).hexdigest()[:24]
    return f"pdec:{digest}"


def _matches_known_noise(*, unit: EvidenceUnit, policy: ContentPolicy) -> bool:
    texts = (unit.raw_text, unit.canonical_text)
    return any(re.search(pattern, text) for pattern in policy.known_noise_patterns for text in texts)


def decide_policy(*, unit: EvidenceUnit, policy: ContentPolicy) -> PolicyDecision:
    """Project one evidence unit to exactly one policy disposition."""

    role = unit.semantic_role
    if _matches_known_noise(unit=unit, policy=policy):
        role = SemanticRole.WATERMARK
        disposition = PolicyDisposition.IGNORED_NOISE
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "命中当前 Content Policy 的已知资料水印规则；原始 evidence 保留并记录该非破坏性分流。"
    elif role in NOISE_ROLES:
        disposition = PolicyDisposition.IGNORED_NOISE
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "页眉页脚、水印、页码或装饰只保留为证据，不进入产品内容。"
    elif role in policy.included_roles:
        disposition = PolicyDisposition.INCLUDED
        requirement = CoverageRequirement.REQUIRED if unit.must_cover_by_source else CoverageRequirement.OPTIONAL
        reason = "该语义角色由当前 Content Policy 消费。"
    elif role in policy.optional_roles:
        disposition = PolicyDisposition.INCLUDED
        requirement = CoverageRequirement.OPTIONAL
        reason = "该语义角色可作为结构或辅助信息被当前 Content Policy 消费。"
    elif role in policy.excluded_roles:
        disposition = PolicyDisposition.EXCLUDED_BY_POLICY
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "证据完整保留，但当前 Content Policy 不允许其进入核心知识卡。"
    elif role in policy.manual_review_roles:
        disposition = PolicyDisposition.MANUAL_REVIEW
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "当前 Content Policy 要求先人工决定该未知语义角色。"
    else:
        disposition = PolicyDisposition.MANUAL_REVIEW
        requirement = CoverageRequirement.NOT_REQUIRED
        reason = "语义角色未被当前 Content Policy 覆盖，不能静默丢弃。"
    return PolicyDecision(
        evidence_id=unit.evidence_id,
        semantic_role=role,
        disposition=disposition,
        coverage_requirement=requirement,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        reason=reason,
    )
