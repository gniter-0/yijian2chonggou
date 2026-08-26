"""cm35 切分产物：r104 离线闸 / 冻结 / 验收契约家族（层 5）。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_legacy_hash import canonical_hash_r10x
from .admission_fixtures_constants import (HASH, R104_ACCEPTANCE_VERSION, R104_FREEZE_VERSION, R104_GATE_CHECK_VERSION, R104_GATE_VERSION, R104_PHASE)


class FrozenArtifact(StrictModel):
    schema_version: Literal["phase2r104_prior_freeze_v1"] = R104_FREEZE_VERSION
    path: str = Field(min_length=1)
    role: Literal["r102_run", "r103_run", "code", "schema", "report", "source_queue"]
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=HASH)


class PriorFreeze(StrictModel):
    schema_version: Literal["phase2r104_prior_freeze_v1"] = R104_FREEZE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=HASH)
    r102_inputs_unchanged: Literal[True] = True
    r103_inputs_unchanged: Literal[True] = True
    freeze_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_freeze(self) -> "PriorFreeze":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("R10.4 frozen artifact count mismatch")
        if len({item.path for item in self.artifacts}) != self.artifact_count:
            raise ValueError("R10.4 frozen artifact paths must be unique")
        if self.aggregate_sha256 != canonical_hash_r10x(self.artifacts):
            raise ValueError("R10.4 frozen artifact aggregate mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "freeze_sha256"}))
        if self.freeze_sha256 != expected:
            raise ValueError("R10.4 freeze hash mismatch")
        return self


class GateCheck(StrictModel):
    schema_version: Literal["phase2r104_gate_check_v1"] = R104_GATE_CHECK_VERSION
    check_id: str = Field(min_length=3)
    passed: bool
    detail: str = Field(min_length=3)


class OfflineGate(StrictModel):
    schema_version: Literal["phase2r104_offline_gate_v1"] = R104_GATE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    valid: bool
    frozen_inputs_valid: bool
    raw_manifest_valid: bool
    prior_acceptance_valid: bool
    source_chain_valid: bool
    explicit_authorization_valid: bool
    exactly_once_valid: bool
    reviewed_fixture_valid: bool
    strict_schema_valid: bool
    regressions_fail_closed: bool
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    reviewed_fixture_created: Literal[True] = True
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        expected_valid = all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected_valid:
            raise ValueError("R10.4 offline Gate validity mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("R10.4 offline Gate hash mismatch")
        return self


class JUnitReceipt(StrictModel):
    schema_version: Literal["phase2r104_acceptance_report_v1"] = R104_ACCEPTANCE_VERSION
    label: Literal["targeted", "full"]
    sha256: str = Field(pattern=HASH)
    tests: int = Field(ge=1)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AcceptanceReport(StrictModel):
    schema_version: Literal["phase2r104_acceptance_report_v1"] = R104_ACCEPTANCE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    accepted: bool
    report_sha256: str = Field(pattern=HASH)
    receipts: tuple[JUnitReceipt, JUnitReceipt]
    reviewed_fixture_authorized: Literal[True] = True
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    acceptance_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_acceptance(self) -> "AcceptanceReport":
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "acceptance_sha256"}))
        if self.acceptance_sha256 != expected:
            raise ValueError("R10.4 acceptance hash mismatch")
        return self
