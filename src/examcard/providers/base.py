"""providers 桶本地底座（base）。

依赖方向（布局终裁 bc-888c6031）：providers 只可 import examcard.common / examcard.utils，
不得 import 任何业务包（含 materials）——因此本模块自带 `StrictModel` 基类，
model_config 与 `examcard.common.schema.StrictModel` / `examcard.materials.schema.StrictModel`
逐字段一致（extra="forbid", frozen=True, str_strip_whitespace=True），三处必须保持同步。

提炼来源（docs/design/providers_distillation_design.md §2/§3）：
- 能力诊断分类税与就绪判定：phase2r2232_step3_relay_diagnostic；
- 不透明失败关闭记录：phase2r117 502 证据 + phase2r118 remediation；
- 错误脱敏：phase2r95/phase2r2232 的 _sanitize_error/_redact 家族。

不变式（设计稿 §3）：#3 fail-closed、#8 响应体不落盘只存哈希、#10 诊断有序有界、
#11 契约自证哈希、#12 证据脱敏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from examcard.common.gates import GateCheck
from examcard.common.hashing import SHA256_HEX, canonical_hash, hash_model, sha256_bytes

__all__ = (
    "StrictModel",
    "InnerGate",
    "StubInnerGate",
    "build_checks",
    "OPAQUE_HTTP_FAILURE_VERSION",
    "OpaqueHttpFailure",
    "build_opaque_failure",
    "sanitize_error_text",
    "RelayProbeKind",
    "RelayProbeOutcome",
    "RelayFailureClass",
    "CAPABILITY_PROBE_VERSION",
    "CAPABILITY_ASSESSMENT_VERSION",
    "CapabilityProbeRecord",
    "RelayCapabilityAssessment",
    "assess_relay_capability",
)


class StrictModel(BaseModel):
    """providers 本地严格基类；config 与 common/materials 的 StrictModel 保持一致。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# 下游结果门禁回调（与 compilers 桶锁定的接口，设计稿 §6.3）
# ---------------------------------------------------------------------------


@runtime_checkable
class InnerGate(Protocol):
    """compilers 侧 ResponseGate 的最小 Protocol 面：{valid, failure_codes}。"""

    @property
    def valid(self) -> bool: ...

    @property
    def failure_codes(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class StubInnerGate:
    """测试/离线场景用的最小 InnerGate 实现。"""

    valid: bool
    failure_codes: tuple[str, ...] = ()


def build_checks(values: Mapping[str, bool], details: Mapping[str, str]) -> tuple[GateCheck, ...]:
    """按 r94/r96/r112 的定式把具名布尔位翻译成 GateCheck 元组（顺序=values 声明序）。"""
    unknown = set(values).symmetric_difference(details)
    if unknown:
        raise ValueError(f"gate check ids and details mismatch: {sorted(unknown)}")
    return tuple(
        GateCheck(check_id=key, passed=passed, detail=details[key]) for key, passed in values.items()
    )


def merge_failure_codes(values: Mapping[str, bool], extra: Sequence[str]) -> tuple[str, ...]:
    """失败码定式（r94）：先列未通过的具名位，再附加解析期失败码，保序去重。"""
    codes = [key for key, passed in values.items() if not passed]
    codes.extend(extra)
    return tuple(dict.fromkeys(codes))


# ---------------------------------------------------------------------------
# 不透明失败关闭（502 家族：r117 证据 + r118 remediation）
# ---------------------------------------------------------------------------

OPAQUE_HTTP_FAILURE_VERSION = "examcard_provider_opaque_http_failure_v1"

_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+\S+|sk-[A-Za-z0-9_-]{8,}|api[-_]?key\s*[:=]\s*\S+)")
_SANITIZED_LIMIT = 300


def sanitize_error_text(text: str) -> str:
    """错误脱敏（不变式 #12）：掩码疑似凭据并截断，决不透传原文全量。"""
    masked = _SECRET_PATTERN.sub("[REDACTED]", text)
    masked = " ".join(masked.split())
    return masked[:_SANITIZED_LIMIT]


class OpaqueHttpFailure(StrictModel):
    """非 2xx 供应商响应的失败关闭证据：只存哈希与脱敏摘要，决不保留响应体。

    distilled_from: phase2r117 sanitized 502 record / phase2r118 opaque_failure_required。
    """

    schema_version: Literal["examcard_provider_opaque_http_failure_v1"] = OPAQUE_HTTP_FAILURE_VERSION
    http_status: StrictInt = Field(ge=100, le=599)
    response_sha256: StrictStr = Field(pattern=SHA256_HEX)
    response_bytes: StrictInt = Field(ge=0)
    sanitized_detail: StrictStr = ""
    body_persisted: Literal[False] = False
    body_reconstruction_allowed: Literal[False] = False
    failure_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "OpaqueHttpFailure":
        if 200 <= self.http_status < 300:
            raise ValueError("opaque failure record cannot carry a 2xx status")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "failure_sha256"}))
        if self.failure_sha256 != expected:
            raise ValueError("opaque failure hash mismatch")
        return self


def build_opaque_failure(status: int, body: bytes, detail: str = "") -> OpaqueHttpFailure:
    return hash_model(
        OpaqueHttpFailure,
        {
            "http_status": status,
            "response_sha256": sha256_bytes(body),
            "response_bytes": len(body),
            "sanitized_detail": sanitize_error_text(detail),
        },
        "failure_sha256",
    )


# ---------------------------------------------------------------------------
# 中继能力诊断（r2232：固定顺序探针 + 失败分类税 + 就绪判定）
# ---------------------------------------------------------------------------

CAPABILITY_PROBE_VERSION = "examcard_relay_capability_probe_v1"
CAPABILITY_ASSESSMENT_VERSION = "examcard_relay_capability_assessment_v1"


class RelayProbeKind(StrEnum):
    MODELS = "models"
    CHAT_JSON = "chat_json"
    STRICT_SCHEMA = "strict_schema"


class RelayProbeOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class RelayFailureClass(StrEnum):
    """distilled_from: phase2r2232 RelayFailureClass（9 值原样保留）。"""

    NONE = "none"
    ENDPOINT_NOT_FOUND = "endpoint_not_found"
    MODEL_NOT_FOUND = "model_not_found"
    AUTHENTICATION_DISGUISED_404 = "authentication_disguised_404"
    AUTHENTICATION_FAILURE = "authentication_failure"
    CHAT_COMPLETIONS_INCOMPATIBLE = "chat_completions_incompatible"
    STRICT_JSON_SCHEMA_UNSUPPORTED = "strict_json_schema_unsupported"
    PROVIDER_PROVENANCE_MISSING = "provider_provenance_missing"
    TRANSPORT_UNKNOWN_OUTCOME = "transport_unknown_outcome"
    TRANSPORT_FAILURE = "transport_failure"


_AUTH_HINT = re.compile(r"(?i)(unauthorized|invalid[\s_-]*(api[\s_-]*key|token)|authentication|forbidden)")
_MODEL_HINT = re.compile(r"(?i)(model.{0,40}(not\s+found|does\s+not\s+exist)|unknown\s+model)")

_PROBE_SEQUENCE = (RelayProbeKind.MODELS, RelayProbeKind.CHAT_JSON, RelayProbeKind.STRICT_SCHEMA)


class CapabilityProbeRecord(StrictModel):
    """单个诊断探针的录制记录（离线消费：录制 fixture 即本模型的 JSON 形态）。

    不变式 #10：探针顺序 models → chat_json → strict_schema，strict 仅在 chat_json
    成功后执行；#8：只存响应哈希，不存响应体。
    """

    schema_version: Literal["examcard_relay_capability_probe_v1"] = CAPABILITY_PROBE_VERSION
    kind: RelayProbeKind
    outcome: RelayProbeOutcome
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    provider_request_id: StrictStr | None = None
    model_advertised: StrictBool | None = None
    response_sha256: StrictStr | None = Field(default=None, pattern=SHA256_HEX)
    sanitized_detail: StrictStr = ""

    @model_validator(mode="after")
    def _validate(self) -> "CapabilityProbeRecord":
        if self.sanitized_detail != sanitize_error_text(self.sanitized_detail):
            raise ValueError("probe detail is not sanitized")
        return self


class RelayCapabilityAssessment(StrictModel):
    """distilled_from: phase2r2232 RelayCapabilityAssessment（就绪判定原样保留）。"""

    schema_version: Literal["examcard_relay_capability_assessment_v1"] = CAPABILITY_ASSESSMENT_VERSION
    models_endpoint_available: StrictBool
    model_advertised: StrictBool | None
    chat_completions_available: StrictBool
    json_object_supported: StrictBool
    strict_json_schema_supported: StrictBool
    provider_provenance_complete: StrictBool
    primary_failure_classification: RelayFailureClass
    classification_basis: tuple[StrictStr, ...] = Field(min_length=1)
    failure_codes: tuple[StrictStr, ...] = ()
    relay_ready_for_fresh_business: StrictBool
    business_call_sent: Literal[False] = False
    assessment_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "RelayCapabilityAssessment":
        ready = (
            self.chat_completions_available
            and self.json_object_supported
            and self.strict_json_schema_supported
            and self.provider_provenance_complete
            and self.primary_failure_classification is RelayFailureClass.NONE
            and not self.failure_codes
        )
        if self.relay_ready_for_fresh_business != ready:
            raise ValueError("relay readiness must match capability gates")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "assessment_sha256"}))
        if self.assessment_sha256 != expected:
            raise ValueError("capability assessment hash mismatch")
        return self


def _probe_map(probes: Sequence[CapabilityProbeRecord]) -> dict[RelayProbeKind, CapabilityProbeRecord]:
    seen: dict[RelayProbeKind, CapabilityProbeRecord] = {}
    for probe in probes:
        if probe.kind in seen:
            raise ValueError(f"duplicate capability probe: {probe.kind}")
        seen[probe.kind] = probe
    extra = set(seen).difference(_PROBE_SEQUENCE)
    if extra:
        raise ValueError(f"unknown capability probes: {sorted(item.value for item in extra)}")
    ordered = [probe.kind for probe in probes]
    if ordered != [kind for kind in _PROBE_SEQUENCE if kind in seen]:
        raise ValueError("capability probes must follow models, chat_json, strict_schema order")
    if RelayProbeKind.STRICT_SCHEMA in seen:
        chat = seen.get(RelayProbeKind.CHAT_JSON)
        if chat is None or chat.outcome is not RelayProbeOutcome.SUCCESS:
            raise ValueError("strict_schema probe may only run after chat_json success")
    return seen


def _classify(probes: Mapping[RelayProbeKind, CapabilityProbeRecord]) -> tuple[RelayFailureClass, tuple[str, ...]]:
    """失败分类优先级（distilled_from r2232 _explicit_error_class + assess_relay_capabilities）。"""
    basis: list[str] = []
    for kind in _PROBE_SEQUENCE:
        probe = probes.get(kind)
        if probe is None:
            continue
        detail = probe.sanitized_detail
        if probe.outcome is RelayProbeOutcome.UNKNOWN_OUTCOME:
            basis.append(f"{kind.value}: outcome unknown")
            return RelayFailureClass.TRANSPORT_UNKNOWN_OUTCOME, tuple(basis)
        if probe.outcome is RelayProbeOutcome.SUCCESS:
            if probe.provider_request_id is None and kind is not RelayProbeKind.MODELS:
                basis.append(f"{kind.value}: success without provider request id")
                return RelayFailureClass.PROVIDER_PROVENANCE_MISSING, tuple(basis)
            continue
        basis.append(f"{kind.value}: http {probe.http_status} {detail}".rstrip())
        if probe.http_status in (401, 403):
            return RelayFailureClass.AUTHENTICATION_FAILURE, tuple(basis)
        if probe.http_status == 404 and _AUTH_HINT.search(detail):
            return RelayFailureClass.AUTHENTICATION_DISGUISED_404, tuple(basis)
        if _MODEL_HINT.search(detail):
            return RelayFailureClass.MODEL_NOT_FOUND, tuple(basis)
        if probe.http_status == 404:
            if kind is RelayProbeKind.MODELS:
                # models 端点缺失是非阻断发现（r2232 nonblocking finding），继续看 chat。
                continue
            return RelayFailureClass.ENDPOINT_NOT_FOUND, tuple(basis)
        if kind is RelayProbeKind.CHAT_JSON:
            return RelayFailureClass.CHAT_COMPLETIONS_INCOMPATIBLE, tuple(basis)
        if kind is RelayProbeKind.STRICT_SCHEMA:
            return RelayFailureClass.STRICT_JSON_SCHEMA_UNSUPPORTED, tuple(basis)
        return RelayFailureClass.TRANSPORT_FAILURE, tuple(basis)
    return RelayFailureClass.NONE, tuple(basis) if basis else ("all probes succeeded",)


def assess_relay_capability(probes: Sequence[CapabilityProbeRecord]) -> RelayCapabilityAssessment:
    """离线能力评估：消费录制探针记录，产出 fail-closed 就绪判定（零网络）。"""
    by_kind = _probe_map(probes)
    classification, basis = _classify(by_kind)

    models = by_kind.get(RelayProbeKind.MODELS)
    chat = by_kind.get(RelayProbeKind.CHAT_JSON)
    strict = by_kind.get(RelayProbeKind.STRICT_SCHEMA)

    chat_ok = chat is not None and chat.outcome is RelayProbeOutcome.SUCCESS
    strict_ok = strict is not None and strict.outcome is RelayProbeOutcome.SUCCESS
    provenance_ok = bool(
        chat_ok
        and chat is not None
        and chat.provider_request_id
        and (not strict_ok or (strict is not None and bool(strict.provider_request_id)))
    )
    failure_codes: tuple[str, ...] = ()
    if classification is not RelayFailureClass.NONE:
        failure_codes = (classification.value,)

    return hash_model(
        RelayCapabilityAssessment,
        {
            "models_endpoint_available": bool(models and models.outcome is RelayProbeOutcome.SUCCESS),
            "model_advertised": models.model_advertised if models is not None else None,
            "chat_completions_available": chat_ok,
            "json_object_supported": chat_ok,
            "strict_json_schema_supported": strict_ok,
            "provider_provenance_complete": provenance_ok,
            "primary_failure_classification": classification,
            "classification_basis": basis,
            "failure_codes": failure_codes,
            "relay_ready_for_fresh_business": (
                chat_ok and strict_ok and provenance_ok and classification is RelayFailureClass.NONE and not failure_codes
            ),
        },
        "assessment_sha256",
    )
