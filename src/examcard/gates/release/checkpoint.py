"""Multi-subject release checkpoint auditors (distilled from phase2r6).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r6_generic_multi_subject_release_checkpoint.py`
（distill:release-gate，设计依据 docs/design/p1_compilers_release_gate_design.md §5.3）。

- 提炼物：域中性源码审计 `audit_domain_neutrality`（marker 集参数化）、严格 schema
  审计 `audit_strict_schemas`（additionalProperties=false + 全 required + 根含
  schema_version）、工件摘要 `digest_artifact` 与 checkpoint manifest 数据模型族。
  这两个审计器可直接服务新仓自身的发布门禁（配合 schema 自动发现导出）。
- r6 法定哈希副本 `canonical_json/canonical_hash/file_hash` 依铁律 bc-b8b9aca6
  随模块逐字节原样。
- 旧仓留守：`GENERIC_SOURCE_FILES/GENERIC_DOCUMENT_FILES/FORBIDDEN_GENERIC_MARKERS`
  常量表、`_build_regressions`/`build_phase2r6_checkpoint`（依赖 archive 范围
  phase2r5 的合成重编译，r5 不迁移）、`MANIFEST_PATH`/`materialize_phase2r6`。
  本模块的 `DEFAULT_*` 参数缺省值保留旧仓语义供历史产物装载校验。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel

__all__ = [
    "ArtifactDigest",
    "CheckpointManifest",
    "GateCheck",
    "GenericSourceAudit",
    "Phase2R6Report",
    "RegressionResult",
    "ReleaseGate",
    "SchemaAudit",
    "audit_domain_neutrality",
    "audit_strict_schemas",
    "canonical_hash",
    "digest_artifact",
]

PHASE = "2R.6"
SCHEMA_VERSION = "phase2r6_generic_multi_subject_release_checkpoint_v1"
ARTIFACT_VERSION = "phase2r6_checkpoint_artifact_v1"
SCHEMA_AUDIT_VERSION = "phase2r6_schema_audit_v1"
SOURCE_AUDIT_VERSION = "phase2r6_generic_source_audit_v1"
REGRESSION_VERSION = "phase2r6_regression_result_v1"
CHECK_VERSION = "phase2r6_gate_check_v1"
GATE_VERSION = "phase2r6_release_gate_v1"
REPORT_VERSION = "phase2r6_report_v1"

_HASH = r"^[0-9a-f]{64}$"
_ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"

# 旧仓 `_source_audit` 内嵌的两组判据标记；参数缺省沿用（历史产物装载校验语义不变）。
DEFAULT_SUBJECT_PATH_MARKERS = ("data/structured_", "data/extracted_")
DEFAULT_LEGACY_PHASE_PREFIX = "phase2r5"


def canonical_json(value: Any) -> bytes:
    # r6 法定哈希副本，逐字节原样（铁律 bc-b8b9aca6）
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactDigest(StrictModel):
    schema_version: Literal[ARTIFACT_VERSION] = ARTIFACT_VERSION
    path: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=_HASH)


class GenericSourceAudit(StrictModel):
    schema_version: Literal[SOURCE_AUDIT_VERSION] = SOURCE_AUDIT_VERSION
    path: str = Field(min_length=1)
    markers: tuple[str, ...] = ()
    domain_neutral: bool
    no_subject_paths: bool
    no_legacy_phase_imports: bool


class SchemaAudit(StrictModel):
    schema_version: Literal[SCHEMA_AUDIT_VERSION] = SCHEMA_AUDIT_VERSION
    path: str = Field(min_length=1)
    object_count: int = Field(ge=1)
    closed_object_count: int = Field(ge=0)
    fully_required_object_count: int = Field(ge=0)
    root_schema_version_required: bool
    valid: bool


class RegressionResult(StrictModel):
    schema_version: Literal[REGRESSION_VERSION] = REGRESSION_VERSION
    case_id: str = Field(pattern=_ID)
    expected: Literal["fail_closed"] = "fail_closed"
    caught: bool
    error_type: str | None = None


class GateCheck(StrictModel):
    schema_version: Literal[CHECK_VERSION] = CHECK_VERSION
    check_id: str = Field(pattern=_ID)
    passed: bool
    detail: str = Field(min_length=1)


class ReleaseGate(StrictModel):
    schema_version: Literal[GATE_VERSION] = GATE_VERSION
    valid: bool
    generic_multi_subject_ready: bool
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    cards_created: Literal[0] = 0
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_gate(self) -> "ReleaseGate":
        expected = all(item.passed for item in self.checks)
        if self.valid != expected or self.generic_multi_subject_ready != expected:
            raise ValueError("R6 readiness must equal the complete Gate result")
        if self.valid and self.failure_codes:
            raise ValueError("a valid Gate cannot contain failure codes")
        return self


class CheckpointManifest(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    checkpoint_id: str = Field(pattern=_ID)
    frozen_phase_range: Literal["Phase 2R.5-2R.5.7"] = "Phase 2R.5-2R.5.7"
    generic_sources: tuple[ArtifactDigest, ...] = Field(min_length=1)
    generic_documents: tuple[ArtifactDigest, ...] = Field(min_length=1)
    frozen_artifacts: tuple[ArtifactDigest, ...] = Field(min_length=1)
    source_audits: tuple[GenericSourceAudit, ...] = Field(min_length=1)
    schema_audits: tuple[SchemaAudit, ...] = Field(min_length=1)
    regressions: tuple[RegressionResult, ...] = Field(min_length=1)
    synthetic_domain_count: Literal[4] = 4
    synthetic_card_count: Literal[20] = 20
    gate: ReleaseGate
    manifest_sha256: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_manifest(self) -> "CheckpointManifest":
        if self.manifest_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"manifest_sha256"})):
            raise ValueError("R6 checkpoint manifest hash mismatch")
        if len(self.source_audits) != len(self.generic_sources):
            raise ValueError("every generic source must have one source audit")
        if not all(item.caught for item in self.regressions):
            raise ValueError("every R6 regression must be fail-closed")
        return self


class Phase2R6Report(StrictModel):
    schema_version: Literal[REPORT_VERSION] = REPORT_VERSION
    phase: Literal[PHASE] = PHASE
    disposition: Literal["accepted_generic_multi_subject_release_checkpoint"] = "accepted_generic_multi_subject_release_checkpoint"
    checkpoint_id: str = Field(pattern=_ID)
    manifest_sha256: str = Field(pattern=_HASH)
    generic_source_count: int = Field(ge=1)
    generic_document_count: int = Field(ge=1)
    frozen_artifact_count: int = Field(ge=1)
    schema_count: int = Field(ge=1)
    synthetic_domain_count: Literal[4] = 4
    synthetic_card_count: Literal[20] = 20
    regression_count: int = Field(ge=1)
    gate: ReleaseGate
    network_calls: Literal[0] = 0
    cards_created: Literal[0] = 0
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def digest_artifact(root: Path, path: Path) -> ArtifactDigest:
    return ArtifactDigest(path=_relative(path, root), bytes=path.stat().st_size, sha256=file_hash(path))


def audit_domain_neutrality(
    root: Path,
    paths: Sequence[Path],
    *,
    forbidden_markers: Iterable[str],
    subject_path_markers: tuple[str, ...] = DEFAULT_SUBJECT_PATH_MARKERS,
    legacy_phase_prefix: str = DEFAULT_LEGACY_PHASE_PREFIX,
) -> tuple[GenericSourceAudit, ...]:
    """域中性审计（原 `_source_audit`；marker 集参数化，逻辑不变）。

    新仓 CI 用法：`forbidden_markers` 传新仓自己的科目隔离清单，对将要发布的
    通用源码集做「无科目标记 / 无科目数据路径 / 无遗留 phase import」三重断言。
    """
    markers_tuple = tuple(forbidden_markers)
    audits: list[GenericSourceAudit] = []
    for path in paths:
        text = path.read_text(encoding="utf-8").casefold()
        markers = tuple(marker for marker in markers_tuple if marker.casefold() in text)
        audits.append(
            GenericSourceAudit(
                path=_relative(path, root),
                markers=markers,
                domain_neutral=not markers,
                no_subject_paths=not any(marker in markers for marker in subject_path_markers),
                no_legacy_phase_imports=not any(marker.startswith(legacy_phase_prefix) for marker in markers),
            )
        )
    return tuple(audits)


def _walk_objects(value: object):
    if isinstance(value, dict):
        if value.get("type") == "object":
            yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def audit_strict_schemas(root: Path, paths: Sequence[Path]) -> tuple[SchemaAudit, ...]:
    """严格 schema 审计（原 `_schema_audits`，逻辑不变）。

    对导出的 JSON Schema 逐一断言：所有 object 节点 `additionalProperties:false`
    且 properties 全 required，根节点必含 `schema_version`。直接适配新仓
    「自动发现 BaseModel 导出」产物的发布门禁。
    """
    audits: list[SchemaAudit] = []
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        objects = tuple(_walk_objects(schema))
        closed = sum(item.get("additionalProperties") is False for item in objects)
        required = sum(set(item.get("required", ())) == set(item.get("properties", {})) for item in objects)
        root_required = "schema_version" in schema.get("required", ())
        valid = bool(objects) and closed == len(objects) and required == len(objects) and root_required
        audits.append(
            SchemaAudit(
                path=_relative(path, root),
                object_count=len(objects),
                closed_object_count=closed,
                fully_required_object_count=required,
                root_schema_version_required=root_required,
                valid=valid,
            )
        )
    return tuple(audits)
