"""providers：LLM/生图供应商兼容层（顶层第 9 包，总指挥终裁锁定）。

从 18 个 provider phase 模块提炼（manifest 4.2；设计稿 docs/design/providers_distillation_design.md）：
envelope profile、请求/响应兼容修复、中继恢复等供应商侧共性逻辑，
是 knowledge / compilers / pipeline 三方共用的传输基础设施。

依赖方向（布局终裁 bc-888c6031）：providers 只可 import examcard.common / examcard.utils，
不得 import 任何业务包；业务包（materials/evidence/knowledge/compilers/gates/pipeline/webui）
可 import providers。

本落地批次（base + envelope_profiles）覆盖：422 请求形状 / 502 不透明错误 / b64-only /
能力探测 / 信封变体，全部离线、录制 fixture 驱动。传输编排（bounded/recovery）与
整改循环骨架（remediation）为后续批次。
"""
from __future__ import annotations

from .base import (
    CAPABILITY_ASSESSMENT_VERSION,
    CAPABILITY_PROBE_VERSION,
    OPAQUE_HTTP_FAILURE_VERSION,
    CapabilityProbeRecord,
    InnerGate,
    OpaqueHttpFailure,
    RelayCapabilityAssessment,
    RelayFailureClass,
    RelayProbeKind,
    RelayProbeOutcome,
    StrictModel,
    StubInnerGate,
    assess_relay_capability,
    build_opaque_failure,
    sanitize_error_text,
    sanitized_error_summary,
)
from .envelope_profiles import (
    CHAT_ALLOWED_METADATA,
    CHAT_ALLOWLIST_VERSION,
    CHAT_CORE_METADATA,
    CHAT_GATE_VERSION,
    CHAT_PROFILE_VERSION,
    IMAGE_DATA_ITEM_KEYS,
    IMAGE_GATE_VERSION,
    IMAGE_HTTP_PUBLIC_FIELDS,
    IMAGE_PROFILE_VERSION,
    IMAGE_REQUEST_PROJECTION_VERSION,
    IMAGE_TOP_LEVEL_KEYS,
    IMAGE_USAGE_KEYS,
    ChatEnvelopeAllowlist,
    ChatEnvelopeGate,
    ChatEnvelopeInspection,
    ChatEnvelopeProfile,
    ChatUsage,
    ImageEnvelopeGate,
    ImageEnvelopeProfile,
    ImageRequestProjection,
    PublicImageRequest,
    StrictImageAdapter,
    build_chat_allowlist,
    build_chat_profile,
    build_image_profile,
    inspect_chat_envelope,
    inspect_image_envelope,
    project_image_request,
)

__all__ = (
    # base
    "StrictModel",
    "InnerGate",
    "StubInnerGate",
    "OPAQUE_HTTP_FAILURE_VERSION",
    "OpaqueHttpFailure",
    "build_opaque_failure",
    "sanitize_error_text",
    "sanitized_error_summary",
    "RelayProbeKind",
    "RelayProbeOutcome",
    "RelayFailureClass",
    "CAPABILITY_PROBE_VERSION",
    "CAPABILITY_ASSESSMENT_VERSION",
    "CapabilityProbeRecord",
    "RelayCapabilityAssessment",
    "assess_relay_capability",
    # chat envelope
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
    # image envelope
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
    # request projection
    "IMAGE_HTTP_PUBLIC_FIELDS",
    "IMAGE_REQUEST_PROJECTION_VERSION",
    "PublicImageRequest",
    "ImageRequestProjection",
    "project_image_request",
)
