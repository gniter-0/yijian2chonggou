"""Release-readiness audits (distilled from phase2r15 + phase2r152, data-driven merge).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r15_release_readiness.py` 与
`phase2r152_release_readiness_refresh.py`（distill:release-gate，设计依据
docs/design/p1_compilers_release_gate_design.md §5.1）。

- r15 与 r152 合并为一个数据驱动模块：「刷新」不再是新文件，而是换参数重跑同一套
  审计（r152 旧仓即直接 import r15 的 `_audit_sample/_audit_stage_transport/
  _aggregate_gate`）。
- 本模块本质是**对持久化运行的审计器**：读盘是其语义，不强行纯化；但所有被读路径
  均由 `SampleSpec` 或调用方参数携带。五金样本 `SAMPLE_SPECS` 常量表、
  `_PREVIOUS_BUNDLE`/`_REMEDIATION_*` 钉死路径、`_snapshot_frozen_inputs` 的具体
  路径集与 `run_phase2r15_*`/`run_phase2r152_*` 入口全部留旧仓。
- r15/r152 法定哈希副本 `_sha256_bytes` 依铁律 bc-b8b9aca6 随模块逐字节原样。
- 参数化（设计 §5.1）：`build_release_readiness_report(root, specs, freeze_run_roots)`
  由调用方传样本表与冻结 run 目录集；`audit_remediation_document(root, document,
  *, expectations, forbidden_request_ids, report_path)` 把 r152 的钉死值
  （network_calls==4、obligation 42、projections 11、card 4、样本名 gonglu_24）
  打包成 `RemediationExpectations` 由旧仓侧作为常量传入。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel

__all__ = [
    "Phase2R15BridgeAudit",
    "Phase2R15CardDisposition",
    "Phase2R15FactDisposition",
    "Phase2R15FreezeAudit",
    "Phase2R15FrozenArtifact",
    "Phase2R15ReleaseReadinessReport",
    "Phase2R15SampleAudit",
    "Phase2R15TransportAudit",
    "Phase2R152FreezeAudit",
    "Phase2R152FrozenArtifact",
    "Phase2R152PreviousBundleAudit",
    "Phase2R152ReleaseReadinessReport",
    "Phase2R152RemediationAudit",
    "RemediationExpectations",
    "SampleSpec",
    "aggregate_gate",
    "audit_remediation_document",
    "audit_sample",
    "audit_stage_transport",
    "build_freeze_audit_from_snapshots",
    "build_release_readiness_report",
]

PHASE_R15 = "2R.1.5"
SCHEMA_VERSION_R15 = "phase2r15_five_golden_release_readiness_v1"
PHASE_R152 = "2R.1.5.2"
SCHEMA_VERSION_R152 = "phase2r152_five_golden_release_readiness_refresh_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ALLOWED_FACT_DISPOSITIONS = frozenset({"projected", "out_of_scope", "duplicate_subfact", "manual_review"})
_ALLOWED_STAGE2A_PROPOSAL_FIELDS = frozenset({"fact_type", "label", "assertion", "primary_anchor_span_ids"})
_ALLOWED_STAGE2B_PROPOSAL_FIELDS = frozenset({"title", "primary_fact_ids", "presentation", "split_reason"})
_FORBIDDEN_MODEL_INPUT_KEYS = frozenset(
    {"canonical_title", "card_key", "review_state", "presentation", "obligation_id", "grouping_obligations"}
)


@dataclass(frozen=True)
class SampleSpec:
    """五金样本 spec 形态（原 Phase2R15SampleSpec；具体常量表留旧仓/调用方）。"""

    sample_id: str
    step2a_report: str
    step2b_report: str
    expected_obligation_count: int
    expected_projected_fact_count: int
    expected_card_count: int
    expected_diagram_nodes: int | None = None
    expected_diagram_edges: int | None = None


@dataclass(frozen=True)
class RemediationExpectations:
    """r152 补救记录审计的期望值参数对象（旧仓钉死值由调用方传入）。"""

    phase: str
    sample_id: str
    network_calls: int
    expected_obligation_count: int
    expected_projection_count: int
    expected_grouping_obligation_count: int
    expected_projected_card_count: int


class Phase2R15FrozenArtifact(StrictModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class Phase2R15FreezeAudit(StrictModel):
    valid: bool
    artifact_count: int = Field(ge=1)
    aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifacts: tuple[Phase2R15FrozenArtifact, ...]
    mismatch_paths: tuple[str, ...] = ()
    historical_hash_drift_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_freeze(self) -> "Phase2R15FreezeAudit":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("freeze artifact count must equal emitted artifact records")
        if len({item.path for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("freeze artifact paths must be unique")
        if self.valid != (not self.mismatch_paths):
            raise ValueError("freeze validity must match its mismatch paths")
        return self


class Phase2R15TransportAudit(StrictModel):
    stage: str
    request_id: str = Field(min_length=1)
    request_version: str = Field(min_length=1)
    model_input_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    cache_key: str = Field(min_length=1)
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    report_path: str
    request_path: str
    record_path: str
    cache_path: str
    journal_path: str
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    record_sha256: str = Field(pattern=_SHA256_PATTERN)
    cache_sha256: str = Field(pattern=_SHA256_PATTERN)
    journal_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema_valid: bool
    telemetry_valid: bool
    no_unknown_response_fields: bool
    valid: bool
    failure_codes: tuple[str, ...] = ()


class Phase2R15FactDisposition(StrictModel):
    proposal_index: int = Field(ge=0)
    fact_type: str = Field(min_length=1)
    model_label_sha256: str = Field(pattern=_SHA256_PATTERN)
    disposition: str
    canonical_title: str | None = None
    fact_key: str | None = None
    primary_anchor_span_ids: tuple[str, ...]
    primary_evidence_ids: tuple[str, ...]
    source_manifest_sha256s: tuple[str, ...]
    source_table_ids: tuple[str, ...]
    basis_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_disposition(self) -> "Phase2R15FactDisposition":
        if self.disposition not in _ALLOWED_FACT_DISPOSITIONS:
            raise ValueError("unknown Phase 2R.1.5 Fact disposition")
        if not self.primary_evidence_ids or not self.source_manifest_sha256s:
            raise ValueError("Fact dispositions require immutable evidence and source manifests")
        if self.disposition == "projected" and (not self.canonical_title or not self.fact_key):
            raise ValueError("projected Fact requires canonical title and fact key")
        return self


class Phase2R15CardDisposition(StrictModel):
    card_index: int = Field(ge=0)
    model_title: str = Field(min_length=1)
    presentation: str = Field(min_length=1)
    primary_fact_ids: tuple[str, ...] = Field(min_length=1)
    disposition: str

    @model_validator(mode="after")
    def validate_card(self) -> "Phase2R15CardDisposition":
        if self.disposition not in {"projected", "blocked", "manual_review"}:
            raise ValueError("unknown Phase 2R.1.5 Card disposition")
        return self


class Phase2R15BridgeAudit(StrictModel):
    valid: bool
    projected_fact_count: int = Field(ge=0)
    bridge_fact_count: int = Field(ge=0)
    card_count: int = Field(ge=0)
    exactly_once: bool
    source_grouping_valid: bool
    complete_table_atomic_separation_valid: bool
    model_input_has_forbidden_fields: bool
    stale_provenance: bool
    evidence_only_or_watermark_leak: bool
    unknown_response_fields: bool
    failure_codes: tuple[str, ...] = ()


class Phase2R15SampleAudit(StrictModel):
    sample_id: str
    step2a: Phase2R15TransportAudit
    step2b: Phase2R15TransportAudit
    source_obligation_count: int = Field(ge=0)
    source_obligation_valid: bool
    canonical_projection_valid: bool
    teaching_gate_valid: bool
    learning_golden_gate_valid: bool
    table_list_diagram_bridge_valid: bool
    diagram_node_count: int = Field(ge=0)
    diagram_edge_count: int = Field(ge=0)
    source_mutations: int = Field(ge=0)
    fact_dispositions: tuple[Phase2R15FactDisposition, ...]
    card_dispositions: tuple[Phase2R15CardDisposition, ...]
    bridge: Phase2R15BridgeAudit
    release_state: str
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_sample(self) -> "Phase2R15SampleAudit":
        if self.release_state not in {"ready_draft", "blocked", "manual_review"}:
            raise ValueError("unknown sample release state")
        if self.source_mutations != 0:
            raise ValueError("release audit cannot accept source mutations")
        if self.release_state == "ready_draft" and not (
            self.step2a.valid
            and self.step2b.valid
            and self.source_obligation_valid
            and self.canonical_projection_valid
            and self.teaching_gate_valid
            and self.learning_golden_gate_valid
            and self.table_list_diagram_bridge_valid
            and self.bridge.valid
        ):
            raise ValueError("ready draft samples require every local audit gate")
        return self


class Phase2R15ReleaseReadinessReport(StrictModel):
    schema_version: str = SCHEMA_VERSION_R15
    phase: str = PHASE_R15
    mode: str = "offline_read_only"
    freeze: Phase2R15FreezeAudit
    samples: tuple[Phase2R15SampleAudit, ...]
    release_ready: bool
    release_blockers: tuple[str, ...]
    unique_request_identity_valid: bool
    no_unknown_fields_valid: bool
    no_fabricated_anchor_valid: bool
    no_cross_source_merge_valid: bool
    no_evidence_only_or_watermark_leak_valid: bool
    network_calls: int = 0
    source_mutations: int = 0
    accepted: bool
    next_required_action: str

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R15ReleaseReadinessReport":
        if len(self.samples) != 5 or len({item.sample_id for item in self.samples}) != 5:
            raise ValueError("Phase 2R.1.5 requires exactly five unique samples")
        if self.network_calls != 0 or self.source_mutations != 0:
            raise ValueError("Phase 2R.1.5 must remain read-only and offline")
        if self.release_ready and self.release_blockers:
            raise ValueError("release-ready bundle cannot retain blockers")
        if self.release_ready and not all(item.release_state == "ready_draft" for item in self.samples):
            raise ValueError("release-ready bundle requires every sample to be ready_draft")
        expected_accepted = self.freeze.valid and all(
            (
                item.step2a.valid
                and item.step2b.valid
                and item.bridge.valid
                and item.source_mutations == 0
            )
            for item in self.samples
        ) and all(
            (
                self.unique_request_identity_valid,
                self.no_unknown_fields_valid,
                self.no_fabricated_anchor_valid,
                self.no_cross_source_merge_valid,
                self.no_evidence_only_or_watermark_leak_valid,
            )
        )
        if self.accepted != expected_accepted:
            raise ValueError("aggregate acceptance must reflect every read-only forensic audit")
        return self


class Phase2R152FrozenArtifact(StrictModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class Phase2R152FreezeAudit(StrictModel):
    valid: bool
    inputs_unchanged: bool
    artifact_count: int = Field(ge=1)
    aggregate_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifacts: tuple[Phase2R152FrozenArtifact, ...]
    changed_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_freeze(self) -> "Phase2R152FreezeAudit":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("freeze artifact count must equal emitted artifact records")
        if len({item.path for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("freeze artifact paths must be unique")
        if self.valid != (self.inputs_unchanged and not self.changed_paths):
            raise ValueError("freeze validity must reflect unchanged inputs")
        return self


class Phase2R152PreviousBundleAudit(StrictModel):
    report_path: str
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    accepted: bool
    release_ready: bool
    historical_gonglu24_state: str
    valid: bool
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_previous(self) -> "Phase2R152PreviousBundleAudit":
        if self.valid != (not self.failure_codes):
            raise ValueError("previous bundle validity must match its failure codes")
        return self


class Phase2R152RemediationAudit(StrictModel):
    report_path: str
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    accepted: bool
    frozen_inputs_unchanged: bool
    network_calls: int = Field(ge=0)
    source_mutations: int = Field(ge=0)
    step2a: Phase2R15TransportAudit
    step2b: Phase2R15TransportAudit
    source_obligation_count: int = Field(ge=0)
    projected_fact_count: int = Field(ge=0)
    expected_card_count: int = Field(ge=0)
    valid: bool
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_remediation(self) -> "Phase2R152RemediationAudit":
        if self.valid != (not self.failure_codes):
            raise ValueError("remediation validity must match its failure codes")
        return self


class Phase2R152ReleaseReadinessReport(StrictModel):
    schema_version: str = SCHEMA_VERSION_R152
    phase: str = PHASE_R152
    mode: str = "offline_read_only_refresh"
    previous_bundle: Phase2R152PreviousBundleAudit
    remediation: Phase2R152RemediationAudit
    freeze: Phase2R152FreezeAudit
    samples: tuple[Phase2R15SampleAudit, ...]
    release_ready: bool
    release_blockers: tuple[str, ...]
    unique_request_identity_valid: bool
    no_unknown_fields_valid: bool
    no_fabricated_anchor_valid: bool
    no_cross_source_merge_valid: bool
    no_evidence_only_or_watermark_leak_valid: bool
    network_calls: int = 0
    source_mutations: int = 0
    review_promotion: str = "disabled; every real result remains draft"
    accepted: bool
    next_required_action: str

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R152ReleaseReadinessReport":
        if len(self.samples) != 5 or len({item.sample_id for item in self.samples}) != 5:
            raise ValueError("Phase 2R.1.5.2 requires exactly five unique samples")
        if self.network_calls != 0 or self.source_mutations != 0:
            raise ValueError("Phase 2R.1.5.2 must remain read-only and offline")
        if self.release_ready and (self.release_blockers or not all(item.release_state == "ready_draft" for item in self.samples)):
            raise ValueError("release-ready refresh requires five ready-draft samples and no blockers")
        expected_accepted = (
            self.previous_bundle.valid
            and self.remediation.valid
            and self.freeze.valid
            and all(item.step2a.valid and item.step2b.valid and item.bridge.valid for item in self.samples)
            and all(
                (
                    self.unique_request_identity_valid,
                    self.no_unknown_fields_valid,
                    self.no_fabricated_anchor_valid,
                    self.no_cross_source_merge_valid,
                    self.no_evidence_only_or_watermark_leak_valid,
                )
            )
        )
        if self.accepted != expected_accepted:
            raise ValueError("aggregate acceptance must reflect every refresh audit")
        return self


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _relative(root: Path, value: str | Path) -> str:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"historical artifact escapes repository root: {path}") from error


def _resolve(root: Path, value: str | Path) -> Path:
    return root / _relative(root, value)


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected object for {field}")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected non-empty string for {field}")
    return value


def _int(value: object, *, field: str) -> int:
    # 报表里该位缺失时（None）原先直接 int(None) 抛 TypeError，信息不含字段名；
    # 这里与 _string/_mapping 对齐成 fail-closed 的 ValueError。
    if not isinstance(value, (int, str)):
        raise ValueError(f"expected integer for {field}")
    return int(value)


def _strings(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"expected non-empty-string list for {field}")
    return tuple(value)


def _collect_forbidden_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        found: set[str] = set(value).intersection(_FORBIDDEN_MODEL_INPUT_KEYS)
        for nested in value.values():
            found.update(_collect_forbidden_keys(nested))
        return found
    if isinstance(value, list):
        found = set()
        for nested in value:
            found.update(_collect_forbidden_keys(nested))
        return found
    return set()


def _expected_response_fields(stage: str) -> frozenset[str]:
    if stage == "step2a":
        return _ALLOWED_STAGE2A_PROPOSAL_FIELDS
    if stage == "step2b":
        return _ALLOWED_STAGE2B_PROPOSAL_FIELDS
    raise ValueError(f"unsupported stage: {stage}")


def audit_stage_transport(
    *, root: Path, report: Mapping[str, Any], report_path: str, stage: str
) -> Phase2R15TransportAudit:
    """传输审计（原 `_audit_stage_transport`，逻辑逐条一致）。"""
    stage_record = _mapping(report.get(stage), field=f"{stage} report record")
    required_paths = {name: _string(stage_record.get(name), field=f"{stage}.{name}") for name in ("request_path", "record_path", "cache_path", "journal_path")}
    failure_codes: list[str] = []
    paths = {name: _resolve(root, value) for name, value in required_paths.items()}
    for name, path in paths.items():
        if not path.is_file():
            failure_codes.append(f"missing_{stage}_{name}")
    report_file = _resolve(root, report_path)
    if not report_file.is_file():
        raise ValueError(f"missing selected report: {report_path}")
    request = record = cache = None
    journal_events: list[dict[str, Any]] = []
    if not failure_codes:
        request_document = _read_json(paths["request_path"])
        request = _mapping(request_document.get("request"), field=f"{stage} request")
        record = _read_json(paths["record_path"])
        cache = _read_json(paths["cache_path"])
        for line in paths["journal_path"].read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    failure_codes.append(f"invalid_{stage}_journal_event")
                    continue
                journal_events.append(item)
    identity = _mapping(request.get("identity"), field=f"{stage} identity") if request is not None else {}
    request_id = _string(identity.get("request_id"), field=f"{stage} request id") if identity else "missing"
    response_sha256 = stage_record.get("response_sha256")
    if not isinstance(response_sha256, str) or len(response_sha256) != 64:
        failure_codes.append(f"missing_{stage}_response_sha256")
        response_sha256 = None
    http_status = stage_record.get("http_status")
    provider_request_id = stage_record.get("provider_request_id")
    if stage_record.get("state") != "success" or http_status != 200 or not isinstance(provider_request_id, str) or not provider_request_id:
        failure_codes.append(f"{stage}_transport_not_success_200")
    if stage_record.get("schema_valid") is not True:
        failure_codes.append(f"{stage}_schema_not_valid")
    if stage_record.get("automatic_retry_enabled") is not False:
        failure_codes.append(f"{stage}_automatic_retry_not_disabled")
    if request is not None and record is not None and cache is not None:
        if request.get("sample_id") != report.get("sample_id"):
            failure_codes.append(f"{stage}_sample_mismatch")
        if identity.get("stage") != stage or record.get("stage") != stage or stage_record.get("stage") != stage:
            failure_codes.append(f"{stage}_stage_mismatch")
        if record.get("request_id") != request_id or stage_record.get("request_id") != request_id or cache.get("request_id") != request_id:
            failure_codes.append(f"{stage}_request_id_mismatch")
        if record.get("response_sha256") != response_sha256:
            failure_codes.append(f"{stage}_response_hash_mismatch")
        if cache.get("record_sha256") != _sha256_bytes(paths["record_path"].read_bytes()):
            failure_codes.append(f"{stage}_cache_record_hash_mismatch")
        response = record.get("response")
        proposals = response.get("proposals") if isinstance(response, Mapping) else None
        expected_fields = _expected_response_fields(stage)
        if not isinstance(proposals, list) or not proposals or any(
            not isinstance(proposal, Mapping) or set(proposal) != expected_fields for proposal in proposals
        ):
            failure_codes.append(f"{stage}_unknown_or_invalid_response_fields")
        started = [event for event in journal_events if event.get("event_type") in {"request_started", "probe_started"} and event.get("request_id") == request_id]
        terminal = [event for event in journal_events if event.get("event_type") == "terminal" and event.get("request_id") == request_id]
        if not started or len(terminal) != 1:
            failure_codes.append(f"{stage}_journal_missing_terminal_pair")
        elif (
            terminal[0].get("outcome") != "success"
            or terminal[0].get("http_status") != 200
            or terminal[0].get("provider_request_id") != provider_request_id
            or terminal[0].get("response_sha256") != response_sha256
        ):
            failure_codes.append(f"{stage}_journal_terminal_mismatch")
    report_sha256 = _sha256_bytes(report_file.read_bytes())
    artifact_hashes = {
        name: _sha256_bytes(path.read_bytes()) if path.is_file() else "0" * 64 for name, path in paths.items()
    }
    telemetry_valid = not any(code.startswith(stage) or code.startswith("missing_" + stage) for code in failure_codes)
    unknown_response = f"{stage}_unknown_or_invalid_response_fields" in failure_codes
    return Phase2R15TransportAudit(
        stage=stage,
        request_id=request_id,
        request_version=str(identity.get("request_version") or "missing"),
        model_input_version=str(request.get("model_input_version") if request else "missing"),
        prompt_version=str(request.get("prompt_version") if request else "missing"),
        response_schema_sha256=str(identity.get("response_schema_sha256") or "0" * 64),
        cache_key=str(identity.get("cache_key") or "missing"),
        http_status=http_status if isinstance(http_status, int) else None,
        provider_request_id=provider_request_id if isinstance(provider_request_id, str) else None,
        latency_ms=float(stage_record["latency_ms"]) if isinstance(stage_record.get("latency_ms"), (int, float)) else None,
        response_sha256=response_sha256,
        report_path=_relative(root, report_path),
        request_path=_relative(root, required_paths["request_path"]),
        record_path=_relative(root, required_paths["record_path"]),
        cache_path=_relative(root, required_paths["cache_path"]),
        journal_path=_relative(root, required_paths["journal_path"]),
        report_sha256=report_sha256,
        request_sha256=artifact_hashes["request_path"],
        record_sha256=artifact_hashes["record_path"],
        cache_sha256=artifact_hashes["cache_path"],
        journal_sha256=artifact_hashes["journal_path"],
        schema_valid=stage_record.get("schema_valid") is True,
        telemetry_valid=telemetry_valid,
        no_unknown_response_fields=not unknown_response,
        valid=not failure_codes,
        failure_codes=tuple(sorted(set(failure_codes))),
    )


def _projection_fact_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    projection = _mapping(report.get("canonical_projection"), field="canonical projection")
    values = projection.get("projections")
    if not isinstance(values, list):
        raise ValueError("canonical projection has no projections")
    result: list[str] = []
    for item in values:
        fact_summary = _mapping(_mapping(item, field="projection item").get("fact_summary"), field="projection fact summary")
        result.append(_string(fact_summary.get("fact_id"), field="projection fact id"))
    return tuple(result)


def _fact_dispositions(report: Mapping[str, Any]) -> tuple[Phase2R15FactDisposition, ...]:
    projection = _mapping(report.get("canonical_projection"), field="canonical projection")
    audits = projection.get("proposal_audits")
    if not isinstance(audits, list):
        raise ValueError("canonical projection has no proposal audits")
    result: list[Phase2R15FactDisposition] = []
    for audit in audits:
        item = _mapping(audit, field="proposal audit")
        result.append(
            Phase2R15FactDisposition(
                proposal_index=_int(item.get("proposal_index"), field="proposal index"),
                fact_type=_string(item.get("fact_type"), field="proposal fact type"),
                model_label_sha256=_string(item.get("model_label_sha256"), field="proposal label hash"),
                disposition=_string(item.get("disposition"), field="proposal disposition"),
                canonical_title=item.get("canonical_title") if isinstance(item.get("canonical_title"), str) else None,
                fact_key=item.get("fact_key") if isinstance(item.get("fact_key"), str) else None,
                primary_anchor_span_ids=_strings(item.get("primary_anchor_span_ids"), field="proposal anchors"),
                primary_evidence_ids=_strings(item.get("primary_evidence_ids"), field="proposal evidence"),
                source_manifest_sha256s=_strings(item.get("source_manifest_sha256s"), field="proposal manifests"),
                source_table_ids=tuple(item.get("source_table_ids") or ()),
                basis_codes=_strings(item.get("basis_codes"), field="proposal basis codes"),
            )
        )
    projected_fact_keys = {item.fact_key for item in result if item.disposition == "projected" and item.fact_key}
    projections = projection.get("projections")
    if not isinstance(projections, list):
        raise ValueError("canonical projection has no projections")
    next_index = max((item.proposal_index for item in result), default=-1) + 1
    for projection_item in projections:
        item = _mapping(projection_item, field="canonical projection item")
        fact_key = _string(item.get("fact_key"), field="canonical projection fact key")
        if fact_key in projected_fact_keys:
            continue
        summary = _mapping(item.get("fact_summary"), field="canonical projection fact summary")
        diagram = summary.get("semantic_diagram_bridge")
        if not isinstance(diagram, Mapping):
            raise ValueError("projection Fact without a matching model proposal requires a semantic_v2 bridge")
        source_manifest = _string(diagram.get("source_manifest_sha256"), field="semantic diagram source manifest")
        result.append(
            Phase2R15FactDisposition(
                proposal_index=next_index,
                fact_type=_string(summary.get("fact_type"), field="semantic projection fact type"),
                model_label_sha256=_sha256_bytes(
                    ("local-semantic-v2:" + _string(summary.get("fact_id"), field="semantic projection fact id")).encode("utf-8")
                ),
                disposition="projected",
                canonical_title=_string(summary.get("canonical_title"), field="semantic projection canonical title"),
                fact_key=fact_key,
                primary_anchor_span_ids=tuple(summary.get("primary_anchor_span_ids") or ()),
                primary_evidence_ids=_strings(summary.get("primary_evidence_ids"), field="semantic projection figure evidence"),
                source_manifest_sha256s=(source_manifest,),
                source_table_ids=tuple(summary.get("source_table_ids") or ()),
                basis_codes=("local_semantic_v2_projection",),
            )
        )
        next_index += 1
    return tuple(result)


def _gate_valid(report: Mapping[str, Any], *names: str) -> bool:
    for name in names:
        value = report.get(name)
        if isinstance(value, Mapping):
            return value.get("valid") is True or value.get("publishable") is True
    return False


def _audit_v25b_bridge_from_document(
    *,
    step2a_report: Mapping[str, Any],
    step2b_report: Mapping[str, Any],
    step2a: Phase2R15TransportAudit,
    step2b: Phase2R15TransportAudit,
    request_document: Mapping[str, Any] | None,
    failure_codes: list[str] | None = None,
) -> tuple[Phase2R15BridgeAudit, tuple[Phase2R15CardDisposition, ...]]:
    codes = list(failure_codes or ())
    if request_document is None:
        codes.append("missing_step2b_request_document")
        request_document = {}
    request = request_document.get("request")
    model_input = request_document.get("model_input")
    if not isinstance(request, Mapping) or not isinstance(model_input, Mapping):
        codes.append("invalid_step2b_request_document")
        request = {}
        model_input = {}
    projected_ids = _projection_fact_ids(step2a_report)
    bridge_facts = request.get("facts")
    if not isinstance(bridge_facts, list):
        codes.append("missing_step2b_bridge_facts")
        bridge_facts = []
    bridge_ids: list[str] = []
    for fact in bridge_facts:
        if not isinstance(fact, Mapping) or not isinstance(fact.get("fact_summary"), Mapping):
            codes.append("invalid_step2b_bridge_fact")
            continue
        bridge_ids.append(str(fact["fact_summary"].get("fact_id") or ""))
    if len(bridge_ids) != len(set(bridge_ids)) or set(bridge_ids) != set(projected_ids):
        codes.append("step2b_projected_fact_exactly_once_failed")
    if request.get("frozen_step2a_request_id") != step2a.request_id or request.get("frozen_step2a_response_sha256") != step2a.response_sha256:
        codes.append("step2b_stale_or_mismatched_step2a_provenance")
    forbidden = _collect_forbidden_keys(model_input)
    if forbidden:
        codes.append("step2b_model_input_forbidden_fields:" + ",".join(sorted(forbidden)))
    model_facts = model_input.get("facts")
    model_fact_ids = {str(item.get("fact_id") or "") for item in model_facts} if isinstance(model_facts, list) and all(isinstance(item, Mapping) for item in model_facts) else set()
    if model_fact_ids != set(projected_ids):
        codes.append("step2b_model_input_fact_set_mismatch")
    for fact in model_facts if isinstance(model_facts, list) else ():
        if not isinstance(fact, Mapping):
            continue
        diagram = fact.get("semantic_diagram_bridge")
        anchors = fact.get("primary_anchor_span_ids")
        if isinstance(diagram, Mapping) and anchors:
            codes.append("semantic_diagram_has_fabricated_text_anchor")
        if isinstance(diagram, Mapping) and (
            not diagram.get("figure_evidence_id") or not diagram.get("semantic_sidecar_sha256") or not diagram.get("source_manifest_sha256")
        ):
            codes.append("semantic_diagram_bridge_incomplete")
    cards_raw = _mapping(step2b_report.get("step2b"), field="step2b report record").get("response", {}).get("proposals", [])
    cards: list[Phase2R15CardDisposition] = []
    response_ids: list[str] = []
    unknown_response_fields = False
    if not isinstance(cards_raw, list):
        codes.append("missing_step2b_response_cards")
        cards_raw = []
    for index, card in enumerate(cards_raw):
        if not isinstance(card, Mapping) or set(card) != _ALLOWED_STAGE2B_PROPOSAL_FIELDS:
            unknown_response_fields = True
            continue
        fact_ids = _strings(card.get("primary_fact_ids"), field="card primary facts")
        response_ids.extend(fact_ids)
        cards.append(
            Phase2R15CardDisposition(
                card_index=index,
                model_title=_string(card.get("title"), field="card title"),
                presentation=_string(card.get("presentation"), field="card presentation"),
                primary_fact_ids=fact_ids,
                disposition="projected",
            )
        )
    if unknown_response_fields:
        codes.append("step2b_unknown_response_fields")
    if len(response_ids) != len(set(response_ids)) or set(response_ids) != set(projected_ids):
        codes.append("step2b_response_fact_exactly_once_failed")
    grouping_gate = step2b_report.get("v25b_grouping_gate") or step2b_report.get("grouping_gate")
    title_gate = step2b_report.get("v25b_card_facing_title_gate") or step2b_report.get("card_facing_title_gate")
    grouping_valid = isinstance(grouping_gate, Mapping) and grouping_gate.get("valid") is True and grouping_gate.get("exactly_once") is True
    source_grouping_valid = _gate_valid(step2b_report, "source_grouping_gate", "source_projection_gate") or (
        grouping_valid and _gate_valid(step2b_report, "v25b_model_input_audit", "model_input_audit")
    )
    separation_valid = (
        _gate_valid(step2b_report, "complete_table_atomic_separation_gate")
        if "complete_table_atomic_separation_gate" in step2b_report
        else True
    )
    if not grouping_valid:
        codes.append("step2b_grouping_gate_failed")
    if not source_grouping_valid:
        codes.append("step2b_source_grouping_gate_failed")
    if not separation_valid:
        codes.append("step2b_complete_table_atomic_separation_failed")
    if not (isinstance(title_gate, Mapping) and title_gate.get("valid") is True):
        codes.append("step2b_card_facing_title_gate_failed")
    leakage = int(step2a_report.get("unresolved_watermark_count") or 0) != 0
    if leakage:
        codes.append("unresolved_watermark_leak")
    bridge = Phase2R15BridgeAudit(
        valid=not codes,
        projected_fact_count=len(projected_ids),
        bridge_fact_count=len(bridge_ids),
        card_count=len(cards),
        exactly_once=(len(response_ids) == len(set(response_ids)) and set(response_ids) == set(projected_ids)),
        source_grouping_valid=source_grouping_valid,
        complete_table_atomic_separation_valid=separation_valid,
        model_input_has_forbidden_fields=bool(forbidden),
        stale_provenance="step2b_stale_or_mismatched_step2a_provenance" in codes,
        evidence_only_or_watermark_leak=leakage or any(code.startswith("semantic_diagram") for code in codes),
        unknown_response_fields=unknown_response_fields,
        failure_codes=tuple(sorted(set(codes))),
    )
    return bridge, tuple(cards)


def _freeze_paths(root: Path, reports: tuple[Mapping[str, Any], ...], *, run_roots: Sequence[str]) -> Phase2R15FreezeAudit:
    """冻结审计（原 `_freeze_paths`；钉死 run 目录集参数化为 `run_roots`）。"""
    paths: dict[str, str] = {}
    mismatch_paths: set[str] = set()
    historical_hash_drift_paths: set[str] = set()
    for relative_root in sorted(set(run_roots)):
        directory = root / relative_root
        if not directory.is_dir():
            continue
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            paths[path.relative_to(root).as_posix()] = _sha256_bytes(path.read_bytes())
    for report in reports:
        for key, value in report.items():
            if not key.startswith("frozen_") or not key.endswith("sha256s") or not isinstance(value, Mapping):
                continue
            for relative, expected in value.items():
                if not isinstance(relative, str) or not isinstance(expected, str):
                    mismatch_paths.add(str(relative))
                    continue
                path = root / relative
                if not path.is_file():
                    mismatch_paths.add(relative)
                    continue
                actual = _sha256_bytes(path.read_bytes())
                paths[relative] = actual
                if actual != expected:
                    historical_hash_drift_paths.add(relative)
    artifacts = tuple(
        Phase2R15FrozenArtifact(path=relative, bytes=(root / relative).stat().st_size, sha256=digest)
        for relative, digest in sorted(paths.items())
    )
    aggregate = _sha256_bytes("\n".join(f"{item.path}:{item.sha256}" for item in artifacts).encode("utf-8"))
    return Phase2R15FreezeAudit(
        valid=not mismatch_paths,
        artifact_count=len(artifacts),
        aggregate_sha256=aggregate,
        artifacts=artifacts,
        mismatch_paths=tuple(sorted(mismatch_paths)),
        historical_hash_drift_paths=tuple(sorted(historical_hash_drift_paths)),
    )


def audit_sample(*, root: Path, spec: SampleSpec) -> Phase2R15SampleAudit:
    """单样本审计（原 `_audit_sample`，逻辑逐条一致；被读路径全部由 spec 携带）。"""
    step2a_path = _resolve(root, spec.step2a_report)
    step2b_path = _resolve(root, spec.step2b_report)
    step2a_report = _read_json(step2a_path)
    step2b_report = _read_json(step2b_path)
    if step2a_report.get("sample_id") != spec.sample_id or step2b_report.get("sample_id") != spec.sample_id:
        raise ValueError(f"sample id mismatch for {spec.sample_id}")
    step2a = audit_stage_transport(root=root, report=step2a_report, report_path=spec.step2a_report, stage="step2a")
    step2b = audit_stage_transport(root=root, report=step2b_report, report_path=spec.step2b_report, stage="step2b")
    request_document = _read_json(_resolve(root, step2b.request_path))
    bridge, cards = _audit_v25b_bridge_from_document(
        step2a_report=step2a_report,
        step2b_report=step2b_report,
        step2a=step2a,
        step2b=step2b,
        request_document=request_document,
    )
    dispositions = _fact_dispositions(step2a_report)
    projection = _mapping(step2a_report.get("canonical_projection"), field="canonical projection")
    obligation_gate = step2a_report.get("step2a_gate") or step2a_report.get("source_contract_gate")
    source_obligation_count = int(obligation_gate.get("obligation_count") or 0) if isinstance(obligation_gate, Mapping) else 0
    source_obligation_valid = isinstance(obligation_gate, Mapping) and obligation_gate.get("valid") is True and source_obligation_count == spec.expected_obligation_count
    canonical_projection_valid = projection.get("valid") is True and len(_projection_fact_ids(step2a_report)) == spec.expected_projected_fact_count
    teaching_gate_valid = _gate_valid(step2a_report, "canonical_teaching_gate", "teaching_gate")
    learning_golden_gate_valid = _gate_valid(step2a_report, "learning_golden_gate")
    diagram = step2a_report.get("diagram_bridge_audit")
    diagram_node_count = int(diagram.get("node_count") or 0) if isinstance(diagram, Mapping) else 0
    diagram_edge_count = int(diagram.get("edge_count") or 0) if isinstance(diagram, Mapping) else 0
    expected_diagram_valid = (
        spec.expected_diagram_nodes is None
        or (isinstance(diagram, Mapping) and diagram.get("valid") is True and diagram_node_count == spec.expected_diagram_nodes and diagram_edge_count == spec.expected_diagram_edges and int(diagram.get("primary_anchor_span_count") or 0) == 0)
    )
    table_list_diagram_bridge_valid = expected_diagram_valid and all(
        _gate_valid(step2a_report, name)
        for name in ("table_provenance_gate", "table_and_continuation_gate")
        if name in step2a_report
    )
    failure_codes: list[str] = []
    if not step2a_report.get("accepted"):
        failure_codes.append("persisted_step2a_report_not_accepted")
    if not source_obligation_valid:
        failure_codes.append("source_coverage_obligation_gate_failed")
    if not canonical_projection_valid:
        failure_codes.append("canonical_projection_gate_failed")
    if not teaching_gate_valid:
        failure_codes.append("teaching_gate_failed")
    if not learning_golden_gate_valid:
        failure_codes.append("learning_golden_gate_failed")
    if not table_list_diagram_bridge_valid:
        failure_codes.append("table_list_or_diagram_bridge_failed")
    if bridge.card_count != spec.expected_card_count:
        failure_codes.append("card_count_mismatch")
    if bridge.failure_codes:
        failure_codes.extend(bridge.failure_codes)
    if step2a.failure_codes:
        failure_codes.extend(step2a.failure_codes)
    if step2b.failure_codes:
        failure_codes.extend(step2b.failure_codes)
    full_valid = not failure_codes
    cards = tuple(
        card.model_copy(update={"disposition": "projected" if full_valid else "blocked"}) for card in cards
    )
    return Phase2R15SampleAudit(
        sample_id=spec.sample_id,
        step2a=step2a,
        step2b=step2b,
        source_obligation_count=source_obligation_count,
        source_obligation_valid=source_obligation_valid,
        canonical_projection_valid=canonical_projection_valid,
        teaching_gate_valid=teaching_gate_valid,
        learning_golden_gate_valid=learning_golden_gate_valid,
        table_list_diagram_bridge_valid=table_list_diagram_bridge_valid,
        diagram_node_count=diagram_node_count,
        diagram_edge_count=diagram_edge_count,
        source_mutations=max(int(step2a_report.get("source_mutations") or 0), int(step2b_report.get("source_mutations") or 0)),
        fact_dispositions=dispositions,
        card_dispositions=cards,
        bridge=bridge,
        release_state="ready_draft" if full_valid else "blocked",
        failure_codes=tuple(sorted(set(failure_codes))),
    )


def aggregate_gate(samples: tuple[Phase2R15SampleAudit, ...]) -> tuple[bool, bool, bool, bool, bool, tuple[str, ...]]:
    """聚合门禁（原 `_aggregate_gate`，逻辑逐条一致）。"""
    ids = [stage.request_id for sample in samples for stage in (sample.step2a, sample.step2b)]
    unique_request_identity_valid = len(ids) == len(set(ids))
    no_unknown_fields_valid = all(
        stage.no_unknown_response_fields and not sample.bridge.unknown_response_fields
        for sample in samples
        for stage in (sample.step2a, sample.step2b)
    )
    no_fabricated_anchor_valid = all(
        not any(code.startswith("semantic_diagram_has_fabricated") for code in sample.bridge.failure_codes)
        for sample in samples
    )
    no_cross_source_merge_valid = all(sample.bridge.source_grouping_valid and sample.bridge.complete_table_atomic_separation_valid for sample in samples)
    no_evidence_only_or_watermark_leak_valid = all(not sample.bridge.evidence_only_or_watermark_leak for sample in samples)
    blockers: list[str] = []
    if not unique_request_identity_valid:
        blockers.append("duplicate_or_reused_request_identity")
    if not no_unknown_fields_valid:
        blockers.append("unknown_model_response_field")
    if not no_fabricated_anchor_valid:
        blockers.append("fabricated_semantic_diagram_anchor")
    if not no_cross_source_merge_valid:
        blockers.append("cross_source_grouping_or_table_merge")
    if not no_evidence_only_or_watermark_leak_valid:
        blockers.append("evidence_only_diagram_or_watermark_leak")
    for sample in samples:
        if sample.release_state != "ready_draft":
            blockers.append(f"{sample.sample_id}:" + ",".join(sample.failure_codes))
    return (
        unique_request_identity_valid,
        no_unknown_fields_valid,
        no_fabricated_anchor_valid,
        no_cross_source_merge_valid,
        no_evidence_only_or_watermark_leak_valid,
        tuple(sorted(dict.fromkeys(blockers))),
    )


def build_release_readiness_report(
    *,
    repository_root: Path,
    specs: Sequence[SampleSpec],
    freeze_run_roots: Sequence[str] = (),
) -> Phase2R15ReleaseReadinessReport:
    """构建就绪审计束（原 `build_phase2r15_release_readiness_report`；SAMPLE_SPECS
    与冻结 run 目录集由调用方传入）。不创建、不改变任何历史记录。"""
    root = Path(repository_root).resolve()
    specs = tuple(specs)
    reports = tuple(_read_json(_resolve(root, path)) for spec in specs for path in {spec.step2a_report, spec.step2b_report})
    freeze_before = _freeze_paths(root, reports, run_roots=freeze_run_roots)
    samples = tuple(audit_sample(root=root, spec=spec) for spec in specs)
    freeze_after = _freeze_paths(root, reports, run_roots=freeze_run_roots)
    freeze = freeze_before.model_copy(update={"valid": freeze_before.valid and freeze_before == freeze_after})
    unique, unknown, anchors, source_merge, leakage, blockers = aggregate_gate(samples)
    release_ready = freeze.valid and not blockers
    accepted = freeze.valid and all(sample.step2a.valid and sample.step2b.valid and sample.bridge.valid for sample in samples) and all((unique, unknown, anchors, source_merge, leakage))
    return Phase2R15ReleaseReadinessReport(
        freeze=freeze,
        samples=samples,
        release_ready=release_ready,
        release_blockers=blockers,
        unique_request_identity_valid=unique,
        no_unknown_fields_valid=unknown,
        no_fabricated_anchor_valid=anchors,
        no_cross_source_merge_valid=source_merge,
        no_evidence_only_or_watermark_leak_valid=leakage,
        accepted=accepted,
        next_required_action=(
            "separately authorize a fresh gonglu_24_table_formula V25 Step 2A remediation; do not retry any historical identity"
            if not release_ready
            else "manual release decision remains required because every real result is draft"
        ),
    )


def build_freeze_audit_from_snapshots(*, root: Path, before: Mapping[str, str], after: Mapping[str, str]) -> Phase2R152FreezeAudit:
    """前后快照对比冻结审计（原 r152 `_freeze_audit`；快照来源由调用方提供）。"""
    changed = tuple(sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key)))
    artifacts = tuple(
        Phase2R152FrozenArtifact(path=path, bytes=(root / path).stat().st_size, sha256=digest)
        for path, digest in sorted(after.items())
    )
    aggregate = _sha256_bytes("\n".join(f"{item.path}:{item.sha256}" for item in artifacts).encode("utf-8"))
    return Phase2R152FreezeAudit(
        valid=not changed,
        inputs_unchanged=not changed,
        artifact_count=len(artifacts),
        aggregate_sha256=aggregate,
        artifacts=artifacts,
        changed_paths=changed,
    )


def audit_remediation_document(
    *,
    root: Path,
    document: Mapping[str, Any],
    expectations: RemediationExpectations,
    forbidden_request_ids: set[str],
    report_path: str,
) -> Phase2R152RemediationAudit:
    """补救记录审计（原 r152 `_audit_remediation_document`；钉死值参数化）。

    机制通用：禁用历史请求身份、逐门禁校验、网络调用预算；旧仓钉死值
    （phase=2R.1.5.1、sample=gonglu_24_table_formula、network_calls=4、
    obligation 42、projections 11、grouping 4、card 4）打包为 `expectations`
    由旧仓侧常量传入。
    """
    report_file = _resolve(root, report_path)
    codes: list[str] = []
    if document.get("phase") != expectations.phase:
        codes.append("remediation_phase_mismatch")
    if document.get("sample_id") != expectations.sample_id:
        codes.append("remediation_sample_mismatch")
    if document.get("accepted") is not True:
        codes.append("remediation_not_accepted")
    if document.get("frozen_inputs_unchanged") is not True:
        codes.append("remediation_frozen_input_drift")
    if document.get("network_calls") != expectations.network_calls:
        codes.append("remediation_network_call_budget_mismatch")
    if int(document.get("source_mutations") or 0) != 0:
        codes.append("remediation_source_mutation_detected")
    step2a = audit_stage_transport(root=root, report=document, report_path=report_path, stage="step2a")
    step2b = audit_stage_transport(root=root, report=document, report_path=report_path, stage="step2b")
    reported_ids = {
        str(stage.get("request_id"))
        for stage in (document.get("step2a"), document.get("step2b"))
        if isinstance(stage, Mapping) and isinstance(stage.get("request_id"), str)
    }
    if (
        reported_ids & forbidden_request_ids
        or step2a.request_id in forbidden_request_ids
        or step2b.request_id in forbidden_request_ids
        or step2a.request_id == step2b.request_id
        or len(reported_ids) != 2
    ):
        codes.append("remediation_reuses_historical_or_cross_stage_identity")
    source_gate = document.get("source_contract_gate")
    if not isinstance(source_gate, Mapping) or source_gate.get("valid") is not True or source_gate.get("obligation_count") != expectations.expected_obligation_count:
        codes.append("remediation_source_obligation_gate_failed")
    projection = document.get("canonical_projection")
    projections = projection.get("projections") if isinstance(projection, Mapping) else None
    if not isinstance(projection, Mapping) or projection.get("valid") is not True or not isinstance(projections, list) or len(projections) != expectations.expected_projection_count:
        codes.append("remediation_canonical_projection_failed")
    for gate_name in ("atomic_contract_gate", "table_and_continuation_gate", "canonical_teaching_gate", "learning_golden_gate"):
        gate = document.get(gate_name)
        if not isinstance(gate, Mapping) or gate.get("valid") is not True:
            codes.append(f"remediation_{gate_name}_failed")
    grouping = document.get("v25b_grouping_gate")
    input_audit = document.get("v25b_model_input_audit")
    separation = document.get("complete_table_atomic_separation_gate")
    title_gate = document.get("v25b_card_facing_title_gate")
    if not isinstance(grouping, Mapping) or not (
        grouping.get("valid") is True and grouping.get("exactly_once") is True and grouping.get("exact_obligation_count") == expectations.expected_grouping_obligation_count
    ):
        codes.append("remediation_v25b_grouping_gate_failed")
    if not isinstance(input_audit, Mapping) or input_audit.get("valid") is not True or input_audit.get("forbidden_model_input_keys") not in ([], ()):
        codes.append("remediation_v25b_model_input_gate_failed")
    if not isinstance(separation, Mapping) or separation.get("valid") is not True:
        codes.append("remediation_complete_table_atomic_separation_failed")
    if not isinstance(title_gate, Mapping) or title_gate.get("valid") is not True or title_gate.get("projected_card_count") != expectations.expected_projected_card_count:
        codes.append("remediation_card_title_gate_failed")
    codes.extend(step2a.failure_codes)
    codes.extend(step2b.failure_codes)
    return Phase2R152RemediationAudit(
        report_path=_relative(root, report_path),
        report_sha256=_sha256_bytes(report_file.read_bytes()),
        accepted=document.get("accepted") is True,
        frozen_inputs_unchanged=document.get("frozen_inputs_unchanged") is True,
        network_calls=int(document.get("network_calls") or 0),
        source_mutations=int(document.get("source_mutations") or 0),
        step2a=step2a,
        step2b=step2b,
        source_obligation_count=int(source_gate.get("obligation_count") or 0) if isinstance(source_gate, Mapping) else 0,
        projected_fact_count=len(projections) if isinstance(projections, list) else 0,
        expected_card_count=int(grouping.get("exact_obligation_count") or 0) if isinstance(grouping, Mapping) else 0,
        valid=not codes,
        failure_codes=tuple(sorted(set(codes))),
    )
