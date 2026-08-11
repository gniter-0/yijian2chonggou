"""供应商信封画像与严格适配器（envelope_profiles）。

覆盖 18 个 distill:providers 模块提炼的全部信封兼容场景
（docs/design/providers_distillation_design.md §2/§5）：

- chat 宽松白名单适配器：phase2r94（ALLOWED_METADATA、核心元数据全有或全无、
  精确形状比对——解析后重序列化必须与原文等值，任何隐式修复=拒绝）；
- chat 来源审计画像：phase2r96（每层精确键集、finish_reason="stop"、
  TRANSPORT_ONLY_PATHS 传输字段决不进结果门禁）；
- 图像信封画像 + 严格适配器：phase2r112/phase2r114/phase2r118
  （b64-only、strict base64、PNG 签名/可解码/尺寸精确、revised_prompt 精确、
  usage 两级 details 求和一致、非 200 一律失败、错误信封决不白名单）；
- HTTP 422 公网请求投影：phase2r116（公网体=恰好五公有字段，内部绑定进 sidecar，
  投影决不改源文本——prompt 哈希证明）。

全部契约对象自证哈希（不变式 #11）；画像键集由 validator 钉死（不变式 #4 的新仓形态：
键集来自旧仓来源审计的冻结产物，经 distilled_from 溯源，不允许运行期扩白名单）。
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Literal, Mapping

from PIL import Image, UnidentifiedImageError
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from examcard.common.gates import GateBase
from examcard.common.hashing import SHA256_HEX, canonical_hash, canonical_json, hash_model, sha256_bytes, sha256_text

from .base import InnerGate, StrictModel, build_checks, merge_failure_codes

__all__ = (
    "CHAT_ALLOWED_METADATA",
    "CHAT_CORE_METADATA",
    "CHAT_ALLOWLIST_VERSION",
    "CHAT_PROFILE_VERSION",
    "CHAT_GATE_VERSION",
    "ChatUsage",
    "ChatEnvelopeAllowlist",
    "ChatEnvelopeProfile",
    "ChatEnvelopeGate",
    "ChatEnvelopeInspection",
    "build_chat_allowlist",
    "build_chat_profile",
    "inspect_chat_envelope",
    "IMAGE_TOP_LEVEL_KEYS",
    "IMAGE_DATA_ITEM_KEYS",
    "IMAGE_USAGE_KEYS",
    "IMAGE_PROFILE_VERSION",
    "IMAGE_GATE_VERSION",
    "ImageEnvelopeProfile",
    "ImageEnvelopeGate",
    "StrictImageAdapter",
    "build_image_profile",
    "inspect_image_envelope",
    "IMAGE_HTTP_PUBLIC_FIELDS",
    "IMAGE_REQUEST_PROJECTION_VERSION",
    "PublicImageRequest",
    "ImageRequestProjection",
    "project_image_request",
)


# ===========================================================================
# Step3 chat 信封（r94 白名单 / r96 画像）
# ===========================================================================

CHAT_ALLOWED_METADATA = ("id", "object", "created", "model", "system_fingerprint", "service_tier", "usage")
CHAT_CORE_METADATA = ("id", "object", "created", "model")

CHAT_PROFILE_TOP_LEVEL_KEYS = ("id", "object", "created", "model", "choices", "usage")
CHAT_PROFILE_CHOICE_KEYS = ("index", "message", "finish_reason")
CHAT_PROFILE_MESSAGE_KEYS = ("role", "content")
CHAT_PROFILE_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_tokens_details",
    "completion_tokens_details",
)
CHAT_PROFILE_PROMPT_DETAIL_KEYS = ("text_tokens", "image_tokens", "cached_tokens")
CHAT_PROFILE_COMPLETION_DETAIL_KEYS = ("text_tokens", "image_tokens", "reasoning_tokens")
CHAT_TRANSPORT_ONLY_PATHS = (
    "id",
    "object",
    "created",
    "model",
    "choices[0].finish_reason",
    "usage",
    "usage.prompt_tokens_details",
    "usage.completion_tokens_details",
)

CHAT_ALLOWLIST_VERSION = "examcard_chat_envelope_allowlist_v1"
CHAT_PROFILE_VERSION = "examcard_chat_envelope_profile_v1"
CHAT_GATE_VERSION = "examcard_chat_envelope_gate_v1"


class ChatUsage(StrictModel):
    """distilled_from: phase2r94 ProviderUsage（total==prompt+completion 定式）。"""

    prompt_tokens: StrictInt = Field(ge=0)
    completion_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _validate_total(self) -> "ChatUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("provider usage total does not match token components")
        return self


class ChatEnvelopeAllowlist(StrictModel):
    """宽松白名单适配器契约（distilled_from: phase2r94_provider_envelope_adapter_v1）。

    适用场景：中继源码不可得、无法出画像时的降级形态（设计稿 §2.1 演进结论）。
    """

    schema_version: Literal["examcard_chat_envelope_allowlist_v1"] = CHAT_ALLOWLIST_VERSION
    allowed_metadata: tuple[StrictStr, ...] = CHAT_ALLOWED_METADATA
    core_metadata: tuple[StrictStr, ...] = CHAT_CORE_METADATA
    strict_choice_keys: tuple[StrictStr, ...] = ("index", "message")
    strict_message_keys: tuple[StrictStr, ...] = ("role", "content")
    normalization_rule: Literal["validate-allowlist-then-forward-choices-without-repair"] = (
        "validate-allowlist-then-forward-choices-without-repair"
    )
    unknown_fields_allowed: Literal[False] = False
    content_inference_allowed: Literal[False] = False
    automatic_field_whitelist_allowed: Literal[False] = False
    distilled_from: tuple[StrictStr, ...] = (
        "phase2r94_provider_envelope_adapter_v1",
        "phase2r94_provider_envelope_v1",
    )
    contract_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "ChatEnvelopeAllowlist":
        if self.allowed_metadata != CHAT_ALLOWED_METADATA or self.core_metadata != CHAT_CORE_METADATA:
            raise ValueError("chat allowlist metadata set drifted")
        if self.strict_choice_keys != ("index", "message") or self.strict_message_keys != ("role", "content"):
            raise ValueError("chat allowlist inner envelope keys drifted")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "contract_sha256"}))
        if self.contract_sha256 != expected:
            raise ValueError("chat allowlist contract hash mismatch")
        return self


def build_chat_allowlist() -> ChatEnvelopeAllowlist:
    return hash_model(ChatEnvelopeAllowlist, {}, "contract_sha256")


class ChatEnvelopeProfile(StrictModel):
    """来源审计画像（distilled_from: phase2r96_provider_envelope_profile_v1）。

    键集字段为冻结数据：值来自旧仓 r96 对中继 serializer 源码的逐行取证产物；
    新仓不重跑取证，经 distilled_from 溯源（旧哈希是原样复制的不透明字符串，
    决不用新实现重算——冻结哈希铁律，设计稿 §6.5）。
    """

    schema_version: Literal["examcard_chat_envelope_profile_v1"] = CHAT_PROFILE_VERSION
    top_level_keys: tuple[StrictStr, ...] = CHAT_PROFILE_TOP_LEVEL_KEYS
    choice_keys: tuple[StrictStr, ...] = CHAT_PROFILE_CHOICE_KEYS
    message_keys: tuple[StrictStr, ...] = CHAT_PROFILE_MESSAGE_KEYS
    usage_keys: tuple[StrictStr, ...] = CHAT_PROFILE_USAGE_KEYS
    prompt_detail_keys: tuple[StrictStr, ...] = CHAT_PROFILE_PROMPT_DETAIL_KEYS
    completion_detail_keys: tuple[StrictStr, ...] = CHAT_PROFILE_COMPLETION_DETAIL_KEYS
    finish_reason: Literal["stop"] = "stop"
    envelope_id_pattern: StrictStr = r"^chatcmpl-[a-f0-9]{32}$"
    expected_model: StrictStr | None = None
    transport_only_paths: tuple[StrictStr, ...] = CHAT_TRANSPORT_ONLY_PATHS
    normalization_rule: Literal[
        "validate-source-evidenced-transport-fields-then-forward-exact-choice-message-content"
    ] = "validate-source-evidenced-transport-fields-then-forward-exact-choice-message-content"
    unknown_fields_allowed: Literal[False] = False
    content_inference_allowed: Literal[False] = False
    raw_response_reconstruction_allowed: Literal[False] = False
    automatic_field_whitelist_allowed: Literal[False] = False
    distilled_from: tuple[StrictStr, ...] = (
        "phase2r96_provider_envelope_profile_v1",
        "phase2r96_relay_serializer_audit_v1",
    )
    profile_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "ChatEnvelopeProfile":
        if (
            self.top_level_keys != CHAT_PROFILE_TOP_LEVEL_KEYS
            or self.choice_keys != CHAT_PROFILE_CHOICE_KEYS
            or self.message_keys != CHAT_PROFILE_MESSAGE_KEYS
            or self.usage_keys != CHAT_PROFILE_USAGE_KEYS
            or self.prompt_detail_keys != CHAT_PROFILE_PROMPT_DETAIL_KEYS
            or self.completion_detail_keys != CHAT_PROFILE_COMPLETION_DETAIL_KEYS
            or self.transport_only_paths != CHAT_TRANSPORT_ONLY_PATHS
        ):
            raise ValueError("chat profile field allow-list drifted")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "profile_sha256"}))
        if self.profile_sha256 != expected:
            raise ValueError("chat profile hash mismatch")
        return self


def build_chat_profile(expected_model: str | None = None) -> ChatEnvelopeProfile:
    return hash_model(ChatEnvelopeProfile, {"expected_model": expected_model}, "profile_sha256")


class ChatEnvelopeGate(GateBase):
    schema_version: Literal["examcard_chat_envelope_gate_v1"] = CHAT_GATE_VERSION
    mode: Literal["allowlist", "profile"]
    transport_json_valid: StrictBool
    wrapper_shape_valid: StrictBool
    metadata_values_exact: StrictBool
    choices_content_exact: StrictBool
    usage_consistent: StrictBool
    inner_valid: StrictBool
    response_sha256: StrictStr = Field(pattern=SHA256_HEX)
    response_bytes: StrictInt = Field(ge=0)
    normalized_envelope_sha256: StrictStr | None = Field(default=None, pattern=SHA256_HEX)


@dataclass(frozen=True)
class ChatEnvelopeInspection:
    gate: ChatEnvelopeGate
    normalized_envelope: bytes | None
    inner_gate: InnerGate | None


_CHAT_CHECK_DETAILS = {
    "transport_json_valid": "provider body is one UTF-8 JSON object",
    "wrapper_shape_valid": "wrapper keys satisfy the allowlist/profile exactly",
    "metadata_values_exact": "transport metadata values survive parse round-trip unchanged",
    "choices_content_exact": "choice, message and content survive parse round-trip unchanged",
    "usage_consistent": "usage totals match token components",
    "inner_valid": "normalized choices pass the injected inner result gate",
}


def _round_trip_equal(raw_value: Any, parsed_value: Any) -> bool:
    """精确形状比对（不变式 #2）：解析值与原始值必须逐字段等值，任何强转/剥空格=拒绝。"""
    return json.loads(canonical_json(parsed_value).decode("utf-8")) == json.loads(
        canonical_json(raw_value).decode("utf-8")
    ) and raw_value == parsed_value


def _validate_chat_allowlist(raw: dict[str, Any], values: dict[str, bool], failures: list[str]) -> bytes | None:
    metadata = {key: value for key, value in raw.items() if key != "choices"}
    if set(raw).difference({"choices", *CHAT_ALLOWED_METADATA}):
        failures.append("wrapper-unknown-top-level")
        return None
    if "choices" not in raw:
        failures.append("wrapper-missing-choices")
        return None
    values["wrapper_shape_valid"] = True

    core_present = [key for key in CHAT_CORE_METADATA if key in metadata]
    if core_present and len(core_present) != len(CHAT_CORE_METADATA):
        failures.append("core-metadata-incomplete")
        return None
    optional_present = [key for key in ("system_fingerprint", "service_tier", "usage") if key in metadata]
    if optional_present and len(core_present) != len(CHAT_CORE_METADATA):
        failures.append("optional-metadata-without-core")
        return None
    for key in ("id", "model", "system_fingerprint", "service_tier"):
        if key in metadata and (not isinstance(metadata[key], str) or not metadata[key]):
            failures.append(f"metadata-{key}-invalid")
            return None
    if "object" in metadata and metadata["object"] != "chat.completion":
        failures.append("metadata-object-invalid")
        return None
    if "created" in metadata and (isinstance(metadata["created"], bool) or not isinstance(metadata["created"], int) or metadata["created"] < 0):
        failures.append("metadata-created-invalid")
        return None
    values["metadata_values_exact"] = True

    if "usage" in metadata:
        try:
            usage = ChatUsage.model_validate(metadata["usage"])
        except Exception:
            failures.append("usage-invalid")
            return None
        if not _round_trip_equal(metadata["usage"], usage.model_dump(mode="json")):
            failures.append("usage-value-transformed")
            return None
    values["usage_consistent"] = True

    choices = raw["choices"]
    if not _chat_choices_exact(choices, require_finish_reason=False):
        failures.append("choices-value-transformed")
        return None
    values["choices_content_exact"] = True
    return canonical_json({"choices": choices})


def _chat_choices_exact(choices: Any, *, require_finish_reason: bool) -> bool:
    """choices 精确形状：恰一项；键集精确；role=assistant；content 非空 str 且无隐式变换。"""
    if not isinstance(choices, list) or len(choices) != 1:
        return False
    choice = choices[0]
    expected_keys = {"index", "message", "finish_reason"} if require_finish_reason else {"index", "message"}
    if not isinstance(choice, dict) or set(choice) != expected_keys:
        return False
    if choice["index"] != 0 or isinstance(choice["index"], bool):
        return False
    if require_finish_reason and choice["finish_reason"] != "stop":
        return False
    message = choice["message"]
    if not isinstance(message, dict) or set(message) != {"role", "content"}:
        return False
    if message["role"] != "assistant":
        return False
    content = message["content"]
    if not isinstance(content, str) or not content:
        return False
    if content != content.strip():
        # StrictModel 会剥边缘空白——那是隐式修复，按不变式 #2 直接拒绝。
        return False
    return True


def _validate_chat_profile(
    raw: dict[str, Any], profile: ChatEnvelopeProfile, values: dict[str, bool], failures: list[str]
) -> bytes | None:
    import re as _re

    if set(raw) != set(profile.top_level_keys):
        failures.append("wrapper-keys-not-exact")
        return None
    values["wrapper_shape_valid"] = True

    if not isinstance(raw["id"], str) or not _re.match(profile.envelope_id_pattern, raw["id"]):
        failures.append("envelope-id-pattern-mismatch")
        return None
    if raw["object"] != "chat.completion":
        failures.append("metadata-object-invalid")
        return None
    if isinstance(raw["created"], bool) or not isinstance(raw["created"], int) or raw["created"] < 0:
        failures.append("metadata-created-invalid")
        return None
    if not isinstance(raw["model"], str) or not raw["model"]:
        failures.append("metadata-model-invalid")
        return None
    if profile.expected_model is not None and raw["model"] != profile.expected_model:
        failures.append("response-model-mismatch")
        return None
    values["metadata_values_exact"] = True

    usage = raw["usage"]
    if not isinstance(usage, dict) or set(usage) != set(profile.usage_keys):
        failures.append("usage-keys-not-exact")
        return None
    for detail_key, expected_keys in (
        ("prompt_tokens_details", profile.prompt_detail_keys),
        ("completion_tokens_details", profile.completion_detail_keys),
    ):
        detail = usage[detail_key]
        if not isinstance(detail, dict) or set(detail) != set(expected_keys):
            failures.append(f"usage-{detail_key}-not-exact")
            return None
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in detail.values()):
            failures.append(f"usage-{detail_key}-invalid")
            return None
    counters = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counters.values()):
        failures.append("usage-invalid")
        return None
    if counters["total_tokens"] != counters["prompt_tokens"] + counters["completion_tokens"]:
        failures.append("usage-total-inconsistent")
        return None
    values["usage_consistent"] = True

    if not _chat_choices_exact(raw["choices"], require_finish_reason=True):
        failures.append("choices-value-transformed")
        return None
    values["choices_content_exact"] = True

    choice = raw["choices"][0]
    # 转发前剥掉 transport-only 的 finish_reason（r96 归一化规则；content 原文未触碰）。
    return canonical_json(
        {"choices": [{"index": choice["index"], "message": {"role": choice["message"]["role"], "content": choice["message"]["content"]}}]}
    )


def inspect_chat_envelope(
    body: bytes,
    *,
    inner_inspector: Callable[[bytes], InnerGate],
    profile: ChatEnvelopeProfile | None = None,
    allowlist: ChatEnvelopeAllowlist | None = None,
) -> ChatEnvelopeInspection:
    """两级门禁（不变式 #1）：信封关（本层）→ 内容关（inner_inspector 回调，compilers 注入）。

    有画像走画像（首选）；无画像走白名单降级；两者必须恰好提供其一。
    """
    if (profile is None) == (allowlist is None):
        raise ValueError("provide exactly one of profile or allowlist")
    mode: Literal["allowlist", "profile"] = "profile" if profile is not None else "allowlist"

    values = {key: False for key in _CHAT_CHECK_DETAILS}
    failures: list[str] = []
    normalized: bytes | None = None
    inner_gate: InnerGate | None = None

    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raw = None
        failures.append("wrapper-json-invalid")
    if isinstance(raw, dict):
        values["transport_json_valid"] = True
        if profile is not None:
            normalized = _validate_chat_profile(raw, profile, values, failures)
        else:
            normalized = _validate_chat_allowlist(raw, values, failures)
    elif raw is not None:
        failures.append("wrapper-not-object")

    if normalized is not None:
        inner_gate = inner_inspector(normalized)
        values["inner_valid"] = bool(inner_gate.valid)
        if not inner_gate.valid:
            failures.extend(f"inner-{code}" for code in inner_gate.failure_codes)

    failure_codes = merge_failure_codes(values, failures)
    gate = hash_model(
        ChatEnvelopeGate,
        {
            "mode": mode,
            **values,
            "valid": not failure_codes,
            "checks": build_checks(values, _CHAT_CHECK_DETAILS),
            "failure_codes": failure_codes,
            "response_sha256": sha256_bytes(body),
            "response_bytes": len(body),
            "normalized_envelope_sha256": sha256_bytes(normalized) if normalized is not None else None,
        },
        "gate_sha256",
    )
    return ChatEnvelopeInspection(gate, normalized if gate.valid else None, inner_gate)


# ===========================================================================
# Step4 图像信封（r112 画像 + r114 部署钉扎 + r118 严格适配器）
# ===========================================================================

IMAGE_TOP_LEVEL_KEYS = ("created", "data", "usage")
IMAGE_DATA_ITEM_KEYS = ("b64_json", "revised_prompt")
IMAGE_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "input_tokens_details", "output_tokens_details")
IMAGE_INPUT_DETAIL_KEYS = ("text_tokens", "image_tokens", "cached_tokens")
IMAGE_OUTPUT_DETAIL_KEYS = ("text_tokens", "image_tokens", "reasoning_tokens")

IMAGE_PROFILE_VERSION = "examcard_image_envelope_profile_v1"
IMAGE_GATE_VERSION = "examcard_image_envelope_gate_v1"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ImageEnvelopeProfile(StrictModel):
    """图像信封画像（distilled_from: r112 profile/adapter、r114 deployment、r118 error adapter）。

    模型名/尺寸等钉扎值是画像数据而非代码字面量（设计稿 §4.2 迁移规则）。
    """

    schema_version: Literal["examcard_image_envelope_profile_v1"] = IMAGE_PROFILE_VERSION
    endpoint: StrictStr = "/v1/images/generations"
    model: StrictStr = "gpt-image-2"
    image_width: StrictInt = Field(default=1536, ge=1)
    image_height: StrictInt = Field(default=1024, ge=1)
    request_n: Literal[1] = 1
    response_format: Literal["b64_json"] = "b64_json"
    top_level_keys: tuple[StrictStr, ...] = IMAGE_TOP_LEVEL_KEYS
    data_item_keys: tuple[StrictStr, ...] = IMAGE_DATA_ITEM_KEYS
    usage_keys: tuple[StrictStr, ...] = IMAGE_USAGE_KEYS
    input_detail_keys: tuple[StrictStr, ...] = IMAGE_INPUT_DETAIL_KEYS
    output_detail_keys: tuple[StrictStr, ...] = IMAGE_OUTPUT_DETAIL_KEYS
    transport_only_metadata_paths: tuple[StrictStr, ...] = ("created", "usage")
    url_response_allowed: Literal[False] = False
    non_200_is_failure: Literal[True] = True
    error_envelope_success_whitelisted: Literal[False] = False
    response_body_transformed: Literal[False] = False
    revised_prompt_exact: Literal[True] = True
    unknown_fields_allowed: Literal[False] = False
    automatic_field_whitelist_allowed: Literal[False] = False
    distilled_from: tuple[StrictStr, ...] = (
        "phase2r112_provider_image_envelope_profile_v1",
        "phase2r112_provider_image_adapter_contract_v1",
        "phase2r114_active_image_relay_deployment_profile_v1",
        "phase2r118_provider_image_error_adapter_profile_v1",
    )
    profile_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "ImageEnvelopeProfile":
        if (
            self.top_level_keys != IMAGE_TOP_LEVEL_KEYS
            or self.data_item_keys != IMAGE_DATA_ITEM_KEYS
            or self.usage_keys != IMAGE_USAGE_KEYS
            or self.input_detail_keys != IMAGE_INPUT_DETAIL_KEYS
            or self.output_detail_keys != IMAGE_OUTPUT_DETAIL_KEYS
        ):
            raise ValueError("image profile field allow-list drifted")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "profile_sha256"}))
        if self.profile_sha256 != expected:
            raise ValueError("image profile hash mismatch")
        return self


def build_image_profile(
    *, model: str = "gpt-image-2", image_width: int = 1536, image_height: int = 1024
) -> ImageEnvelopeProfile:
    return hash_model(
        ImageEnvelopeProfile,
        {"model": model, "image_width": image_width, "image_height": image_height},
        "profile_sha256",
    )


class StrictImageAdapter:
    """非变换成功解析器（distilled_from: phase2r118 StrictImageAdapter）。

    非 200 一律失败（不变式 #3）；成功路径逐层精确校验；决不变换响应。
    """

    def __init__(self, profile: ImageEnvelopeProfile) -> None:
        self._profile = profile

    @staticmethod
    def _keys(value: object, expected: tuple[str, ...], location: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or tuple(sorted(str(key) for key in value)) != tuple(sorted(expected)):
            raise ValueError(f"{location} keys are not exact")
        return value

    @staticmethod
    def _int(value: object, location: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{location} is not a non-negative integer")
        return value

    def validate_success_document(self, status: int, document: object, prompt: str) -> Mapping[str, Any]:
        profile = self._profile
        if status != 200:
            raise ValueError("non-200 image response is failure evidence")
        envelope = self._keys(document, tuple(profile.top_level_keys), "response")
        self._int(envelope["created"], "response.created")
        data = envelope["data"]
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("response.data must contain exactly one item")
        item = self._keys(data[0], tuple(profile.data_item_keys), "response.data[0]")
        value = item["b64_json"]
        if not isinstance(value, str) or not value:
            raise ValueError("response.data[0].b64_json is not text")
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("response.data[0].b64_json is not strict base64") from exc
        try:
            if not raw.startswith(_PNG_SIGNATURE):
                raise ValueError("response image is not PNG")
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.size != (profile.image_width, profile.image_height):
                    raise ValueError(
                        f"response PNG dimensions are not {profile.image_width}x{profile.image_height}"
                    )
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("response"):
                raise
            raise ValueError("response PNG is invalid") from exc
        revised = item["revised_prompt"]
        if not isinstance(revised, str) or revised != prompt:
            raise ValueError("revised_prompt is not exact")
        usage = self._keys(envelope["usage"], tuple(profile.usage_keys), "response.usage")
        input_details = self._keys(usage["input_tokens_details"], tuple(profile.input_detail_keys), "input token details")
        output_details = self._keys(usage["output_tokens_details"], tuple(profile.output_detail_keys), "output token details")
        input_tokens = self._int(usage["input_tokens"], "input_tokens")
        output_tokens = self._int(usage["output_tokens"], "output_tokens")
        total_tokens = self._int(usage["total_tokens"], "total_tokens")
        input_sum = sum(self._int(input_details[key], key) for key in profile.input_detail_keys)
        output_sum = sum(self._int(output_details[key], key) for key in profile.output_detail_keys)
        if input_tokens != input_sum or output_tokens != output_sum or total_tokens != input_tokens + output_tokens:
            raise ValueError("usage totals are inconsistent")
        return document  # type: ignore[return-value]

    def validate_success_body(self, status: int, body: bytes, prompt: str) -> Mapping[str, Any]:
        if status != 200:
            raise ValueError("non-200 image response is failure evidence")
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("image response is not valid JSON") from exc
        return self.validate_success_document(status, document, prompt)


class ImageEnvelopeGate(GateBase):
    schema_version: Literal["examcard_image_envelope_gate_v1"] = IMAGE_GATE_VERSION
    http_status_success: StrictBool
    transport_json_valid: StrictBool
    wrapper_exact: StrictBool
    data_item_exact: StrictBool
    b64_strict: StrictBool
    png_valid: StrictBool
    png_dimensions_exact: StrictBool
    revised_prompt_exact: StrictBool
    usage_sums_consistent: StrictBool
    response_sha256: StrictStr = Field(pattern=SHA256_HEX)
    response_bytes: StrictInt = Field(ge=0)
    image_sha256: StrictStr | None = Field(default=None, pattern=SHA256_HEX)
    image_width: StrictInt | None = Field(default=None, ge=1)
    image_height: StrictInt | None = Field(default=None, ge=1)


_IMAGE_CHECK_DETAILS = {
    "http_status_success": "HTTP status is 200 (every non-200 or error envelope is terminal failure)",
    "transport_json_valid": "provider body is one UTF-8 JSON object",
    "wrapper_exact": "top-level keys are exactly the profiled set",
    "data_item_exact": "data carries exactly one item with the profiled keys",
    "b64_strict": "image payload is strict base64 (b64-only, URL responses rejected)",
    "png_valid": "decoded payload is a verifiable PNG",
    "png_dimensions_exact": "PNG dimensions match the deployment profile exactly",
    "revised_prompt_exact": "revised_prompt equals the request prompt exactly",
    "usage_sums_consistent": "usage totals and per-detail sums are consistent",
}

_IMAGE_FAILURE_TO_CHECK = (
    ("non-200 image response", "http_status_success"),
    ("image response is not valid JSON", "transport_json_valid"),
    ("response keys are not exact", "wrapper_exact"),
    ("response.created", "wrapper_exact"),
    ("response.data must contain exactly one item", "data_item_exact"),
    ("response.data[0] keys are not exact", "data_item_exact"),
    ("response.data[0].b64_json is not text", "b64_strict"),
    ("response.data[0].b64_json is not strict base64", "b64_strict"),
    ("response image is not PNG", "png_valid"),
    ("response PNG is invalid", "png_valid"),
    ("response PNG dimensions are not", "png_dimensions_exact"),
    ("revised_prompt is not exact", "revised_prompt_exact"),
    ("response.usage keys are not exact", "usage_sums_consistent"),
    ("input token details keys are not exact", "usage_sums_consistent"),
    ("output token details keys are not exact", "usage_sums_consistent"),
    ("usage totals are inconsistent", "usage_sums_consistent"),
    ("input_tokens", "usage_sums_consistent"),
    ("output_tokens", "usage_sums_consistent"),
    ("total_tokens", "usage_sums_consistent"),
    ("text_tokens", "usage_sums_consistent"),
    ("image_tokens", "usage_sums_consistent"),
    ("cached_tokens", "usage_sums_consistent"),
    ("reasoning_tokens", "usage_sums_consistent"),
)


def inspect_image_envelope(
    status: int, body: bytes, prompt: str, profile: ImageEnvelopeProfile
) -> ImageEnvelopeGate:
    """图像信封门禁：包装严格适配器为具名检查位 + fail-closed 失败码（r112 定式）。"""
    values = {key: False for key in _IMAGE_CHECK_DETAILS}
    failures: list[str] = []
    image_sha256: str | None = None
    image_size: tuple[int, int] | None = None

    adapter = StrictImageAdapter(profile)
    try:
        document = adapter.validate_success_body(status, body, prompt)
    except ValueError as error:
        message = str(error)
        matched = False
        for prefix, check_id in _IMAGE_FAILURE_TO_CHECK:
            if message.startswith(prefix):
                # 该检查位之前的全部检查按序视为通过（适配器逐层推进，先失败先停）。
                _mark_passed_until(values, check_id)
                failures.append(message)
                matched = True
                break
        if not matched:
            failures.append(message)
    else:
        for key in values:
            values[key] = True
        raw = base64.b64decode(document["data"][0]["b64_json"], validate=True)
        image_sha256 = sha256_bytes(raw)
        with Image.open(BytesIO(raw)) as image:
            image_size = image.size

    failure_codes = merge_failure_codes(values, failures)
    return hash_model(
        ImageEnvelopeGate,
        {
            **values,
            "valid": not failure_codes,
            "checks": build_checks(values, _IMAGE_CHECK_DETAILS),
            "failure_codes": failure_codes,
            "response_sha256": sha256_bytes(body),
            "response_bytes": len(body),
            "image_sha256": image_sha256,
            "image_width": image_size[0] if image_size else None,
            "image_height": image_size[1] if image_size else None,
        },
        "gate_sha256",
    )


def _mark_passed_until(values: dict[str, bool], failed_check: str) -> None:
    for key in values:
        if key == failed_check:
            return
        values[key] = True


# ===========================================================================
# Step4 HTTP 请求公网投影（r116：422 根因整改）
# ===========================================================================

IMAGE_HTTP_PUBLIC_FIELDS = ("model", "n", "prompt", "response_format", "size")
IMAGE_REQUEST_PROJECTION_VERSION = "examcard_image_request_projection_v1"

# distilled_from: phase2r116 INTERNAL_FIELDS——历史上真实泄漏过或高危的内部绑定字段。
KNOWN_INTERNAL_FIELDS = (
    "schema_version",
    "source_card_id",
    "source_candidate_id",
    "request_id",
    "business_identity",
    "source_contract_sha256",
    "typed_contract_sha256",
    "provenance_sha256",
    "geometry_sha256",
    "fact_ids",
    "evidence_ids",
    "anchor_ids",
)


class PublicImageRequest(StrictModel):
    """公网 HTTP 体（恰好五公有字段；extra=forbid 使内部字段根本装不进来）。"""

    model: StrictStr = Field(min_length=1)
    n: Literal[1] = 1
    prompt: StrictStr = Field(min_length=1)
    response_format: Literal["b64_json"] = "b64_json"
    size: StrictStr = Field(pattern=r"^\d{3,4}x\d{3,4}$")


class ImageRequestProjection(StrictModel):
    """公网投影 + sidecar 绑定证明（distilled_from: phase2r116 HttpRequestProjection）。

    不变式 #9：内部字段决不进公网体；投影决不改源文本（prompt 哈希证明）。
    """

    schema_version: Literal["examcard_image_request_projection_v1"] = IMAGE_REQUEST_PROJECTION_VERSION
    http_request: PublicImageRequest
    http_payload_utf8_bytes: StrictInt = Field(gt=0)
    http_payload_sha256: StrictStr = Field(pattern=SHA256_HEX)
    source_prompt_sha256: StrictStr = Field(pattern=SHA256_HEX)
    original_payload_sha256: StrictStr = Field(pattern=SHA256_HEX)
    sidecar_field_names: tuple[StrictStr, ...] = ()
    sidecar_sha256: StrictStr = Field(pattern=SHA256_HEX)
    internal_fields_leaked: Literal[False] = False
    distilled_from: tuple[StrictStr, ...] = (
        "phase2r116_provider_image_request_compatibility_profile_v1",
        "phase2r116_http_request_projection_v1",
    )
    projection_sha256: StrictStr = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate(self) -> "ImageRequestProjection":
        request_keys = tuple(sorted(self.http_request.model_dump(mode="json")))
        if request_keys != tuple(sorted(IMAGE_HTTP_PUBLIC_FIELDS)):
            raise ValueError("HTTP request contains non-public fields")
        body = canonical_json(self.http_request)
        if len(body) != self.http_payload_utf8_bytes or sha256_bytes(body) != self.http_payload_sha256:
            raise ValueError("HTTP projection payload hash mismatch")
        if self.source_prompt_sha256 != sha256_text(self.http_request.prompt):
            raise ValueError("HTTP projection prompt drifted from source")
        if set(self.sidecar_field_names) & set(IMAGE_HTTP_PUBLIC_FIELDS):
            raise ValueError("sidecar and public HTTP field sets overlap")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "projection_sha256"}))
        if self.projection_sha256 != expected:
            raise ValueError("HTTP projection hash mismatch")
        return self


def project_image_request(
    payload: Mapping[str, Any], profile: ImageEnvelopeProfile
) -> tuple[ImageRequestProjection, dict[str, Any]]:
    """把混入内部字段的请求负载投影为（公网体, sidecar）二元组。

    公网体=恰好五公有字段且逐项匹配部署画像钉扎；其余一切字段隔离进 sidecar
    （含 r116 历史泄漏字段 schema_version 等）；prompt 原文决不改动。
    """
    missing = [field for field in IMAGE_HTTP_PUBLIC_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"public HTTP fields missing from payload: {missing}")
    public = PublicImageRequest.model_validate({field: payload[field] for field in IMAGE_HTTP_PUBLIC_FIELDS})
    if public.prompt != payload["prompt"]:
        raise ValueError("HTTP projection altered source prompt text")
    if public.model != profile.model:
        raise ValueError("request model does not match the deployment profile")
    if public.size != f"{profile.image_width}x{profile.image_height}":
        raise ValueError("request size does not match the deployment profile")
    sidecar = {key: payload[key] for key in payload if key not in IMAGE_HTTP_PUBLIC_FIELDS}
    body = canonical_json(public)
    projection = hash_model(
        ImageRequestProjection,
        {
            "http_request": public,
            "http_payload_utf8_bytes": len(body),
            "http_payload_sha256": sha256_bytes(body),
            "source_prompt_sha256": sha256_text(public.prompt),
            "original_payload_sha256": canonical_hash(payload),
            "sidecar_field_names": tuple(sorted(sidecar)),
            "sidecar_sha256": canonical_hash(sidecar),
        },
        "projection_sha256",
    )
    return projection, sidecar
