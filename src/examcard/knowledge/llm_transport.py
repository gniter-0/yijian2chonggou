"""会真联网的 Step 2 transport 层（拆分层 1）。

OpenAI 兼容缝的入参钉在 SDK 自己的 TypedDict 上（``chat.completions.create()`` 按
``stream`` 布尔重载，喂 ``dict[str, Any]`` 匹配不上任何重载）；响应格式只认
json_object 与 json_schema 两种。``CallableStep2Transport`` 是离线替身，不建真客户端。
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject, ResponseFormatJSONSchema
from pydantic import Field

from ..materials.schema import StrictModel

from .contracts import Step2ARequest, Step2BRequest, Step2RequestIdentity, Step2ResponseFormat

from .llm_support import _sanitized_error_message, _sha256


# openai SDK 的 ``chat.completions.create()`` 按 ``stream`` 布尔重载，且 ``messages`` /
# ``response_format`` 都要求 TypedDict 精确形状：喂 ``list[dict[str, Any]]`` 与
# ``dict[str, Any]`` 一律匹配不上任何重载（call-overload）。本别名把 transport 缝的入参
# 钉到 SDK 自己的类型上——本仓只用 json_object 与 json_schema 两种响应格式，因此这里是
# 两member 的闭合联合，而不是 SDK 那个还含 text 的宽联合。
_ChatResponseFormatParam = ResponseFormatJSONObject | ResponseFormatJSONSchema


class TransportCompletion(StrictModel):
    content: str
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    headers: dict[str, str] = {}
    finish_reason: str | None = None


class Step2Transport(Protocol):
    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion: ...


class OpenAICompatibleStep2Transport:
    """Small transport wrapper with SDK retries disabled for auditable outcomes.

    Production-recipe options (P3 T2, both default-off so the existing behavior
    stays byte-identical):

    - ``stream=True`` aggregates an SSE response into the same
      ``TransportCompletion`` shape (defeats gateway first-byte/idle cutoffs on
      long generations; semantics ported verbatim from the pilot streaming
      transport).
    - ``system_suffix`` is appended to the end of the system message at message
      construction time (transport-layer reinforcement injection; the frozen
      prompt template body is never modified).

    Identity discipline: neither option participates in request-identity
    derivation (identities are computed upstream from the unmodified prompt
    template). Drivers must journal both (``streaming_transport`` /
    ``reinforcement_id``) in their run reports for auditability.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float,
        default_headers: Mapping[str, str] | None = None,
        stream: bool = False,
        system_suffix: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.network_call_count = 0
        self.stream = stream
        self.system_suffix = system_suffix
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            default_headers=default_headers or None,
            max_retries=0,
        )

    @staticmethod
    def _provider_request_id(headers: Mapping[str, str]) -> str | None:
        return headers.get("x-request-id") or headers.get("request-id")

    @staticmethod
    def _normalized_headers(raw_headers: Any) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}

    @staticmethod
    def _raw_http_status(raw: Any) -> int | None:
        """Read the SDK raw-response status without inferring it from success."""

        for candidate in (
            getattr(raw, "status_code", None),
            getattr(getattr(raw, "http_response", None), "status_code", None),
            getattr(getattr(raw, "_response", None), "status_code", None),
        ):
            if isinstance(candidate, int) and 100 <= candidate <= 599:
                return candidate
        return None

    @staticmethod
    def _response_format_for_mode(
        *,
        mode: Step2ResponseFormat,
        response_schema: Mapping[str, Any],
        schema_name: str,
    ) -> _ChatResponseFormatParam:
        """Map the typed Step 2 mode to one provider wire shape.

        The decoder mode is selected before request identity construction.  The
        transport only maps that already-bound mode to the provider's typed
        request parameter; it never retries or changes the mode implicitly.
        """

        if mode == Step2ResponseFormat.JSON_SCHEMA_STRICT:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(response_schema),
                },
            }
        if mode == Step2ResponseFormat.JSON_OBJECT:
            return {"type": "json_object"}
        raise ValueError("unsupported Step 2 response format")

    def _raw_create(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        max_tokens: int,
        temperature: float,
        response_format: _ChatResponseFormatParam,
        idempotency_key: str | None,
    ) -> TransportCompletion:
        self.network_call_count += 1
        if self.stream:
            return self._raw_create_streaming(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                idempotency_key=idempotency_key,
            )
        raw = self._client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        headers = self._normalized_headers(raw.headers)
        response = raw.parse()
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        return TransportCompletion(
            content=content,
            provider_request_id=self._provider_request_id(headers),
            http_status=self._raw_http_status(raw),
            headers=headers,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    def _raw_create_streaming(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        max_tokens: int,
        temperature: float,
        response_format: _ChatResponseFormatParam,
        idempotency_key: str | None,
    ) -> TransportCompletion:
        """SSE aggregation: join deltas into a TransportCompletion equivalent to the non-streaming shape.

        Ported verbatim from the pilot ``StreamingStep2Transport`` (probes and
        business calls share this single ``_raw_create`` seam). Besides the wire
        transfer mode nothing about the request changes (model, decoder
        parameters and response_format stay identical). The last chunk's
        ``finish_reason`` is preserved so a mid-stream cutoff (aggregate without
        ``finish_reason="stop"``) remains distinguishable in audits.
        """

        raw = self._client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            stream=True,
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        headers = self._normalized_headers(raw.headers)
        stream = raw.parse()
        parts: list[str] = []
        finish_reason: str | None = None
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or ()
                if choices:
                    delta = getattr(choices[0].delta, "content", None)
                    if delta:
                        parts.append(delta)
                    chunk_finish = getattr(choices[0], "finish_reason", None)
                    if chunk_finish is not None:
                        finish_reason = str(chunk_finish)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return TransportCompletion(
            content="".join(parts).strip(),
            provider_request_id=self._provider_request_id(headers),
            http_status=self._raw_http_status(raw),
            headers=headers,
            finish_reason=finish_reason,
        )

    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        return self.complete_payload(
            identity=request.identity,
            model_payload=self.model_payload(request),
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=use_idempotency_key,
        )

    def complete_payload(
        self,
        *,
        identity: Step2RequestIdentity,
        model_payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        """Submit a versioned custom contract without widening the V1 path.

        V2 has a few deliberately isolated contracts whose model input is not a
        ``Step2ARequest`` or ``Step2BRequest``.  They still need the same
        no-retry OpenAI-compatible transport, status telemetry and idempotency
        behavior.  Callers remain responsible for their local response Gate.
        """

        decoder = identity.decoder
        response_format = self._response_format_for_mode(
            mode=decoder.response_format,
            response_schema=response_schema,
            schema_name=f"{identity.stage.value}_result",
        )
        messages = self._messages_for_model_payload(
            prompt=self._system_prompt_with_suffix(prompt), model_payload=model_payload
        )
        return self._raw_create(
            model=identity.model,
            messages=messages,
            max_tokens=decoder.max_tokens,
            temperature=decoder.temperature,
            response_format=response_format,
            idempotency_key=identity.idempotency_key if use_idempotency_key else None,
        )

    @staticmethod
    def model_payload(request: Step2ARequest | Step2BRequest) -> dict[str, Any]:
        """Project audit-rich requests into the smallest model-visible shape."""

        if isinstance(request, Step2BRequest):
            return request.model_dump(mode="json", exclude={"identity"})
        spans_by_parent: dict[str, list[dict[str, str]]] = {}
        for projection in request.source_projections:
            for span in projection.anchor_spans:
                spans_by_parent.setdefault(span.parent_evidence_id, []).append(
                    {"anchor_span_id": span.anchor_span_id, "text": span.canonical_text}
                )

        sections: list[dict[str, Any]] = []
        for section in request.sections:
            diagrams = []
            for diagram in section.controlled_diagrams:
                diagrams.append(
                    {
                        "review_id": diagram.review_id,
                        "title": diagram.title,
                        "diagram_type": diagram.diagram_type,
                        "nodes": [
                            {
                                "node_id": node.node_id,
                                "label": node.label,
                                "anchor_span_ids": [
                                    span["anchor_span_id"]
                                    for evidence_id in node.evidence_ids
                                    for span in spans_by_parent.get(evidence_id, ())
                                ],
                            }
                            for node in diagram.nodes
                        ],
                        "edges": [
                            {
                                "source_node_id": edge.source_node_id,
                                "target_node_id": edge.target_node_id,
                                "relation": edge.relation,
                                "basis": edge.basis,
                                "confidence": edge.confidence,
                                "anchor_span_ids": [
                                    span["anchor_span_id"]
                                    for evidence_id in edge.evidence_ids
                                    for span in spans_by_parent.get(evidence_id, ())
                                ],
                            }
                            for edge in diagram.edges
                        ],
                    }
                )
            sections.append(
                {
                    "section_id": section.section_id,
                    "page_num": section.page_num,
                    "text_evidence": [
                        {
                            "kind": item.kind.value,
                            "context_only": item.context_only,
                            "anchor_spans": spans_by_parent[item.evidence_id],
                        }
                        for item in section.text_evidence
                    ],
                    "tables": [
                        {
                            "table_evidence_id": table.table_evidence_id,
                            "row_count": table.row_count,
                            "column_count": table.column_count,
                            "requires_complete_composition": True,
                            "complete_cell_anchor_span_ids": [
                                span["anchor_span_id"]
                                for row in table.rows
                                for cell in row.cells
                                for span in spans_by_parent[cell.evidence_id]
                            ],
                            "atomic_subfact_requirements": [
                                {
                                    "requirement_id": requirement.requirement_id,
                                    "expected_fact_type": requirement.expected_fact_type.value,
                                    "required_anchor_span_id": requirement.anchor_span_id,
                                    "required_assertion": requirement.required_assertion,
                                }
                                for requirement in table.atomic_subfact_requirements
                            ],
                            "rows": [
                                {
                                    "row_index": row.row_index,
                                    "cells": [
                                        {
                                            "row_index": cell.row_index,
                                            "column_index": cell.column_index,
                                            "row_span": cell.row_span,
                                            "col_span": cell.col_span,
                                            "anchor_spans": spans_by_parent[cell.evidence_id],
                                        }
                                        for cell in row.cells
                                    ],
                                }
                                for row in table.rows
                            ],
                        }
                        for table in section.tables
                    ],
                    "controlled_diagrams": diagrams,
                }
            )
        # 逐步补键的 JSON 载荷：不注解的话 mypy 会按首个字面量把值类型收成
        # Sequence[Collection[str]]，后面几处补键（reviewed_continuation_sidecar 等）本来
        # 合法的赋值全报 assignment。本函数的返回声明就是 dict[str, Any]，这里如实写同一
        # 个类型即可。
        payload: dict[str, Any] = {
            "sample_id": request.sample_id,
            "material_profile_id": request.material_profile_id,
            "heading_only_allowed_anchor_span_ids": list(request.heading_only_allowed_anchor_span_ids),
            "continuation_records": [
                {
                    "continuation_id": record.continuation_id,
                    "state": record.state.value,
                    "anchor_span_ids": list(record.anchor_span_ids),
                    "required_adjacent_page_num": record.required_adjacent_page_num,
                }
                for projection in request.source_projections
                for record in projection.continuation_records
            ],
            "sections": sections,
        }
        if request.continuation_bindings:
            payload["continuation_bindings"] = [
                {
                    "binding_id": binding.binding_id,
                    "continuation_id": binding.continuation_id,
                    "state": binding.state.value,
                    "source_section_id": binding.source_section_id,
                    "source_anchor_span_ids": list(binding.source_anchor_span_ids),
                    "adjacent_page_num": binding.adjacent_page_num,
                    "adjacent_section_id": binding.adjacent_section_id,
                    "closure_anchor_span_ids": list(binding.closure_anchor_span_ids),
                    "closure_basis": binding.closure_basis,
                    "failure_code": binding.failure_code.value if binding.failure_code is not None else None,
                }
                for binding in request.continuation_bindings
            ]
        if request.continuation_review_sidecar is not None:
            payload["reviewed_continuation_sidecar"] = {
                "sidecar_id": request.continuation_review_sidecar.sidecar_id,
                "sidecar_sha256": request.continuation_review_sidecar_sha256,
                "marker_dispositions": [
                    {
                        "continuation_id": item.continuation_id,
                        "disposition": item.disposition.value,
                        "anchor_span_ids": list(item.anchor_span_ids),
                    }
                    for item in request.continuation_review_sidecar.marker_reviews
                ],
                "closure_review_ids": [item.review_id for item in request.continuation_review_sidecar.closure_reviews],
            }
        if request.coverage_obligation_context_input is not None:
            context_input = request.coverage_obligation_context_input
            payload["coverage_obligation_context_input"] = {
                "input_version": context_input.input_version,
                "input_id": context_input.input_id,
                "contexts": [
                    {
                        "context_id": context.context_id,
                        "obligation_id": context.obligation_id,
                        "kind": context.kind.value,
                        "expected_fact_type": context.expected_fact_type.value,
                        "required_primary_anchor_span_ids": list(context.required_primary_anchor_span_ids),
                        "requires_all_items": context.requires_all_items,
                        "table_evidence_id": context.table_evidence_id,
                        "table_row_count": context.table_row_count,
                        "table_column_count": context.table_column_count,
                        "required_source_assertion": context.required_source_assertion,
                        "anchor_contexts": [item.model_dump(mode="json") for item in context.anchor_contexts],
                    }
                    for context in context_input.contexts
                ],
            }
        elif request.coverage_obligation_input is not None:
            payload["coverage_obligation_input"] = {
                "input_version": request.coverage_obligation_input.input_version,
                "input_id": request.coverage_obligation_input.input_id,
                "obligations": [
                    {
                        "obligation_id": item.obligation_id,
                        "kind": item.kind.value,
                        "expected_fact_type": item.expected_fact_type.value,
                        "required_primary_anchor_span_ids": list(item.required_primary_anchor_span_ids),
                        "requires_all_items": item.requires_all_items,
                    }
                    for item in request.coverage_obligation_input.obligations
                ],
            }
        return payload

    def _system_prompt_with_suffix(self, prompt: str) -> str:
        """Append the optional reinforcement suffix at message-construction time.

        The suffix is applied after the identity/prompt fingerprint has been
        verified upstream, so request identities keep binding the unmodified
        template (pilot-calibrated discipline: reinforcement is injected at the
        transport layer and never enters identity derivation).
        """

        return prompt if self.system_suffix is None else prompt + self.system_suffix

    @staticmethod
    def _request_messages(
        *, request: Step2ARequest | Step2BRequest, prompt: str
    ) -> list[ChatCompletionMessageParam]:
        return OpenAICompatibleStep2Transport._messages_for_model_payload(
            prompt=prompt,
            model_payload=OpenAICompatibleStep2Transport.model_payload(request),
        )

    @staticmethod
    def _messages_for_model_payload(
        *, prompt: str, model_payload: Mapping[str, Any]
    ) -> list[ChatCompletionMessageParam]:
        return [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Return only the required JSON object for this immutable request data:\n"
                + json.dumps(dict(model_payload), ensure_ascii=False),
            },
        ]

    def probe_response_format(self, *, model: str) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
        try:
            completion = self._raw_create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": "Return {\"ok\": true}."},
                ],
                max_tokens=64,
                temperature=0,
                response_format={"type": "json_schema", "json_schema": {"name": "step2_probe", "strict": True, "schema": schema}},
                idempotency_key=None,
            )
            parsed = json.loads(completion.content)
            return {
                "supported": parsed == {"ok": True},
                "provider_request_id": completion.provider_request_id,
                "raw_response_sha256": _sha256(completion.content),
                "error": None,
            }
        except Exception as exc:  # Capability probes must report incompatibility, not hide it.
            return {"supported": False, "provider_request_id": None, "raw_response_sha256": None, "error": _sanitized_error_message(exc)}

    def probe_json_schema(
        self,
        *,
        model: str,
        schema_name: str,
        response_schema: Mapping[str, Any],
        user_content: str,
        max_tokens: int = 400,
    ) -> TransportCompletion:
        """Perform one no-retry strict-schema probe for a concrete V2 contract."""
        return self._raw_create(
            model=model,
            messages=[
                {"role": "system", "content": "Return only strict JSON conforming to the supplied response schema."},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": dict(response_schema)},
            },
            idempotency_key=None,
        )

    def probe_idempotency(self, *, model: str, idempotency_key: str) -> dict[str, Any]:
        response_format: _ChatResponseFormatParam = {"type": "json_object"}
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Return {\"probe\": true}."},
        ]
        records: list[dict[str, Any]] = []
        try:
            for _ in range(2):
                completion = self._raw_create(
                    model=model,
                    messages=messages,
                    max_tokens=64,
                    temperature=0,
                    response_format=response_format,
                    idempotency_key=idempotency_key,
                )
                replay_value = next(
                    (
                        completion.headers.get(name)
                        for name in ("idempotency-replayed", "idempotent-replayed", "x-idempotency-replayed")
                        if completion.headers.get(name) is not None
                    ),
                    None,
                )
                records.append(
                    {
                        "provider_request_id": completion.provider_request_id,
                        "idempotency_replayed": replay_value is not None and replay_value.lower() in {"1", "true", "yes", "replayed"},
                        "response_header_evidence": {
                            name: value
                            for name, value in completion.headers.items()
                            if name in {"idempotency-replayed", "idempotent-replayed", "x-idempotency-replayed", "x-request-id"}
                        },
                    }
                )
            return {"supported": records[1]["idempotency_replayed"] is True, "records": records, "error": None}
        except Exception as exc:
            return {"supported": False, "records": records, "error": _sanitized_error_message(exc)}


class CallableStep2Transport:
    """Test-only transport adapter; no network implementation is hidden here."""

    def __init__(self, callback: Callable[..., TransportCompletion]) -> None:
        self._callback = callback

    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        return self._callback(
            request=request,
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=use_idempotency_key,
        )
