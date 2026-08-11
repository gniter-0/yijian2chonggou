"""gates.release：发布就绪门禁（自 4 个 release-gate phase 提炼，见 manifest 4.4 节）。

提炼来源（脚本本体留旧仓；设计 docs/design/p1_compilers_release_gate_design.md §5）：

- ``readiness`` ← phase2r15 + phase2r152（数据驱动合并）：持久化运行的发布就绪审计
- ``closure`` ← phase2r98：Step3 确定性发布闭环（R9 编译器唯一权威）
- ``checkpoint`` ← phase2r6：域中性源码审计 + 严格 schema 审计
"""

from examcard.gates.release.checkpoint import audit_domain_neutrality, audit_strict_schemas
from examcard.gates.release.closure import (
    DeterministicRelease,
    ExternalCheck,
    build_deterministic_release,
    submit_non_authoritative_candidate,
)
from examcard.gates.release.readiness import (
    RemediationExpectations,
    SampleSpec,
    audit_remediation_document,
    audit_sample,
    build_release_readiness_report,
)

__all__ = [
    "DeterministicRelease",
    "ExternalCheck",
    "RemediationExpectations",
    "SampleSpec",
    "audit_domain_neutrality",
    "audit_remediation_document",
    "audit_sample",
    "audit_strict_schemas",
    "build_deterministic_release",
    "build_release_readiness_report",
    "submit_non_authoritative_candidate",
]
