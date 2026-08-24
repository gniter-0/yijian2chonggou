"""被拒模型响应的安全取证（拆分层 1）。

响应原文一律不落盘：只留请求身份、内容哈希与规范化后的本地错误类别——足够事后对账，
又不让原始模型文本变成运行期产物。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ..materials.schema import StrictModel

from .llm_support import _SHA256_PATTERN


class Step2SchemaErrorLocation(StrictModel):
    """Sanitized Pydantic location without any rejected input value."""

    path: tuple[str | int, ...] = Field(min_length=1)
    error_type: str = Field(min_length=1, max_length=96)


class Step2RejectedResponseForensics(StrictModel):
    """Safe diagnostics for a model response rejected after it reached this process.

    The original response body is deliberately never persisted. The request
    identity, content hash, and normalized local error class are enough to
    correlate a future review without making raw model text a runtime artifact.
    """

    request_id: str = Field(pattern=r"^step2[ab]-request:[0-9a-f]{64}$")
    raw_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    rejection_code: str = Field(pattern=r"^[a-z0-9_]+$")
    error_types: tuple[str, ...] = ()
    schema_error_locations: tuple[Step2SchemaErrorLocation, ...] = ()
    schema_error_count: int = Field(default=0, ge=0)
    missing_field_count: int = Field(default=0, ge=0)
    extra_field_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_forensics(self) -> "Step2RejectedResponseForensics":
        if len(self.error_types) != len(set(self.error_types)):
            raise ValueError("rejected-response forensic error types must be unique")
        locations = [(location.path, location.error_type) for location in self.schema_error_locations]
        if len(locations) != len(set(locations)):
            raise ValueError("rejected-response forensic schema locations must be unique")
        if self.schema_error_count != len(self.schema_error_locations):
            raise ValueError("rejected-response forensic schema error count must match locations")
        if self.missing_field_count != sum(location.error_type == "missing" for location in self.schema_error_locations):
            raise ValueError("rejected-response forensic missing count must match locations")
        if self.extra_field_count != sum(location.error_type == "extra_forbidden" for location in self.schema_error_locations):
            raise ValueError("rejected-response forensic extra-field count must match locations")
        return self
