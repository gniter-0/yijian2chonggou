"""common：四 distill 桶（providers/compilers/gates-review/release-gate）共用的冻结底座（总指挥终裁保留）。

模块集（终裁广播 bc-9d82a196 扩容后，共六件）：
- hashing.py：canonical_json / canonical_hash / hash_model 契约自证哈希；
- gates.py：GateCheck 与 fail-closed 回归矩阵定式；
- io.py：原子写等通用 IO 定式（各 phase 重复 5 份的归一）；
- freeze.py：冻结审计定式（重复 7 份归一；不含各 phase glob 集、不依赖 git）；
- junit.py：JUnit 回执定式（重复 4 份归一）；
- schema.py：strict_schema(model) 严格模式定式。
hashing/gates 签名按 docs/design/providers_distillation_design.md §6.1/§9.1 冻结；
其余模块签名落地即冻。模块增删、签名变更须报总指挥裁决。

红线：只收通用底座、不收任何桶特有类型；叶包——不得 import 任何 examcard 内部包。
冻结哈希铁律（组公告 bc-b8b9aca6）：本底座仅供四桶**新代码**使用；旧契约的一切法定哈希/指纹
实现（canonical_step2_sha256、evidence identity._canonical/_digest、stable_config_hash 等）
必须逐字节原样随包搬运，禁止折叠进本包或改调新实现——一字节规范化差异即作废全部 ID/缓存键/golden。
"""
from __future__ import annotations

from .freeze import (
    FREEZE_SNAPSHOT_VERSION,
    FROZEN_ARTIFACT_VERSION,
    FreezeSnapshot,
    FrozenArtifact,
    build_freeze,
    diff_freeze,
    freeze_artifact,
    verify_freeze,
)
from .gates import (
    FAIL_CLOSED_REGRESSION_VERSION,
    GATE_CHECK_VERSION,
    REJECTION_ERRORS,
    FailClosedRegression,
    GateBase,
    GateCheck,
    failed_check_ids,
    run_fail_closed,
)
from .hashing import (
    SHA256_HEX,
    canonical_hash,
    canonical_json,
    file_hash,
    hash_model,
    jsonable,
    sha256_bytes,
    sha256_text,
)
from .io import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_yaml,
    load_model,
    png_dimensions,
    read_json,
    relative,
    resolve,
    resolve_within,
    write_json,
    write_yaml,
)
from .junit import JUNIT_RECEIPT_VERSION, JUnitLabel, JUnitReceipt, junit_stats, parse_junit_receipt
from .schema import StrictModel, strict_schema

__all__ = (
    # schema
    "StrictModel",
    "strict_schema",
    # hashing
    "SHA256_HEX",
    "jsonable",
    "canonical_json",
    "canonical_hash",
    "sha256_bytes",
    "sha256_text",
    "file_hash",
    "hash_model",
    # io
    "atomic_write_bytes",
    "atomic_write_json",
    "write_json",
    "atomic_write_yaml",
    "write_yaml",
    "read_json",
    "load_model",
    "append_jsonl",
    "resolve",
    "relative",
    "resolve_within",
    "png_dimensions",
    # freeze
    "FROZEN_ARTIFACT_VERSION",
    "FREEZE_SNAPSHOT_VERSION",
    "FrozenArtifact",
    "FreezeSnapshot",
    "freeze_artifact",
    "build_freeze",
    "verify_freeze",
    "diff_freeze",
    # junit
    "JUNIT_RECEIPT_VERSION",
    "JUnitLabel",
    "JUnitReceipt",
    "junit_stats",
    "parse_junit_receipt",
    # gates
    "GATE_CHECK_VERSION",
    "FAIL_CLOSED_REGRESSION_VERSION",
    "REJECTION_ERRORS",
    "GateCheck",
    "GateBase",
    "FailClosedRegression",
    "failed_check_ids",
    "run_fail_closed",
)
