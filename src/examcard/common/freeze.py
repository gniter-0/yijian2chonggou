"""通用冻结审计：FrozenArtifact + build/verify_freeze + 快照 diff。

批复面（compilers 稿 §2.2）；规范源 r810（_dedupe_paths + 多来源合并）、
r102/r103（role 标注）。章程红线（bc-9d82a196）：不含各 phase 路径 glob 集、
不依赖 git；路径一律仓库相对（posix），大小写不敏感去重（Windows 口径，末者胜）。

旧 phase 的冻结记录（PriorFreeze/R10Freeze/R97Freeze/FreezeAudit 族）不迁移、
不由本模块重验（compilers 稿 §2.1）；本模块只服务四桶新代码的新冻结需求。
签名落地即冻。
"""
from __future__ import annotations

from pathlib import Path
from typing import Final, Iterable, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from .hashing import SHA256_HEX, canonical_hash, file_hash
from .io import resolve_within
from .schema import StrictModel

FROZEN_ARTIFACT_VERSION: Final = "examcard_common_frozen_artifact_v1"
FREEZE_SNAPSHOT_VERSION: Final = "examcard_common_freeze_snapshot_v1"


class FrozenArtifact(StrictModel):
    schema_version: Literal["examcard_common_frozen_artifact_v1"] = FROZEN_ARTIFACT_VERSION
    path: StrictStr = Field(min_length=1)
    role: StrictStr = Field(default="artifact", min_length=1)
    bytes: StrictInt = Field(gt=0)
    sha256: StrictStr = Field(pattern=SHA256_HEX)


class FreezeSnapshot(StrictModel):
    schema_version: Literal["examcard_common_freeze_snapshot_v1"] = FREEZE_SNAPSHOT_VERSION
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    artifact_count: StrictInt = Field(gt=0)
    aggregate_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "FreezeSnapshot":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("freeze artifact count mismatch")
        if len({item.path for item in self.artifacts}) != self.artifact_count:
            raise ValueError("frozen paths must be unique")
        if self.aggregate_sha256 != canonical_hash(self.artifacts):
            raise ValueError("freeze aggregate hash mismatch")
        return self


def freeze_artifact(root: Path, relative_path: str | Path, role: str = "artifact") -> FrozenArtifact:
    if Path(relative_path).is_absolute():
        raise ValueError(f"freeze paths must be repository-relative: {relative_path}")
    target = resolve_within(root, relative_path)
    return FrozenArtifact(
        path=Path(relative_path).as_posix(),
        role=role,
        bytes=target.stat().st_size,
        sha256=file_hash(target),
    )


def _dedupe_entries(entries: Iterable[tuple[str | Path, str]]) -> tuple[tuple[str, str], ...]:
    unique: dict[str, tuple[str, str]] = {}
    for path_value, role in entries:
        text = Path(path_value).as_posix()
        unique[text.lower()] = (text, role)
    return tuple(unique[key] for key in sorted(unique))


def build_freeze(root: Path, entries: Iterable[str | Path | tuple[str | Path, str]]) -> FreezeSnapshot:
    normalized = tuple(item if isinstance(item, tuple) else (item, "artifact") for item in entries)
    deduped = _dedupe_entries(normalized)
    if not deduped:
        raise ValueError("freeze requires at least one artifact path")
    artifacts = tuple(freeze_artifact(root, path_text, role) for path_text, role in deduped)
    return FreezeSnapshot(
        artifacts=artifacts,
        artifact_count=len(artifacts),
        aggregate_sha256=canonical_hash(artifacts),
    )


def diff_freeze(root: Path, snapshot: FreezeSnapshot) -> tuple[str, ...]:
    """快照 diff：返回内容漂移/丢失/越界的冻结路径（保持快照顺序）。"""
    changed: list[str] = []
    for artifact in snapshot.artifacts:
        try:
            target = resolve_within(root, artifact.path)
        except ValueError:
            changed.append(artifact.path)
            continue
        if not target.is_file() or target.stat().st_size != artifact.bytes or file_hash(target) != artifact.sha256:
            changed.append(artifact.path)
    return tuple(changed)


def verify_freeze(root: Path, snapshot: FreezeSnapshot) -> bool:
    return not diff_freeze(root, snapshot)
