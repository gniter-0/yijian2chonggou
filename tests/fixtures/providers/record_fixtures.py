"""录制 providers 离线测试 fixture（确定性、零网络）。

运行方式（仅在需要重录时）：uv run python tests/fixtures/providers/record_fixtures.py
产物：本目录下 *.json——图像 fixture 内嵌真实 PNG 的 strict base64
（1536x1024 合规图 + 512x512 尺寸突变图），chat/capability fixture 为纯 JSON 形态。
数据全部取自 18 个 distill:providers phase 模块的回归矩阵形态
（r94/r96 信封变体、r112/r118 图像与 502、r116 422 请求、r2232 能力分类）。
"""
from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent

PROMPT = "Render exam card 2: four labelled roadbed layers, exact source text, no invented wording."
CHAT_CONTENT = '{"draft_cards":[{"presentation":"table"},{"presentation":"ordered_list"},{"presentation":"formula"}]}'
RELAY_ID = "chatcmpl-" + "a1b2c3d4e5f60718293a4b5c6d7e8f90"
CREATED = 1754870400
HEX64 = "f" * 64


def _png_b64(width: int, height: int, color: tuple[int, int, int]) -> str:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _write(name: str, payload: object) -> None:
    path = HERE / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {path.name}")


def _chat_choice(*, finish_reason: bool, content: str = CHAT_CONTENT) -> dict:
    choice: dict = {"index": 0, "message": {"role": "assistant", "content": content}}
    if finish_reason:
        choice["finish_reason"] = "stop"
    return choice


def _profile_usage() -> dict:
    return {
        "prompt_tokens": 1200,
        "completion_tokens": 340,
        "total_tokens": 1540,
        "prompt_tokens_details": {"text_tokens": 1100, "image_tokens": 0, "cached_tokens": 100},
        "completion_tokens_details": {"text_tokens": 340, "image_tokens": 0, "reasoning_tokens": 0},
    }


def record_chat() -> None:
    _write("chat_valid_minimal.json", {"choices": [_chat_choice(finish_reason=False)]})
    _write(
        "chat_valid_full_metadata.json",
        {
            "id": RELAY_ID,
            "object": "chat.completion",
            "created": CREATED,
            "model": "gpt-5.5",
            "system_fingerprint": "fp_44709d6fcb",
            "service_tier": "default",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540},
            "choices": [_chat_choice(finish_reason=False)],
        },
    )
    _write(
        "chat_unknown_top_level.json",
        {"choices": [_chat_choice(finish_reason=False)], "provider_note": "extra sibling"},
    )
    _write("chat_core_metadata_incomplete.json", {"id": RELAY_ID, "choices": [_chat_choice(finish_reason=False)]})
    _write(
        "chat_usage_mismatch.json",
        {
            "id": RELAY_ID,
            "object": "chat.completion",
            "created": CREATED,
            "model": "gpt-5.5",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1541},
            "choices": [_chat_choice(finish_reason=False)],
        },
    )
    _write(
        "chat_two_choices.json",
        {"choices": [_chat_choice(finish_reason=False), _chat_choice(finish_reason=False)]},
    )
    _write("chat_padded_content.json", {"choices": [_chat_choice(finish_reason=False, content=f"  {CHAT_CONTENT}  ")]})

    profile_valid = {
        "id": RELAY_ID,
        "object": "chat.completion",
        "created": CREATED,
        "model": "gpt-5.5",
        "choices": [_chat_choice(finish_reason=True)],
        "usage": _profile_usage(),
    }
    _write("chat_profile_valid.json", profile_valid)

    missing_details = json.loads(json.dumps(profile_valid))
    missing_details["usage"] = {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540}
    _write("chat_profile_missing_usage_details.json", missing_details)

    finish_length = json.loads(json.dumps(profile_valid))
    finish_length["choices"][0]["finish_reason"] = "length"
    _write("chat_profile_finish_reason_length.json", finish_length)

    extra_choice_key = json.loads(json.dumps(profile_valid))
    extra_choice_key["choices"][0]["logprobs"] = None
    _write("chat_profile_extra_choice_key.json", extra_choice_key)

    unknown_top = json.loads(json.dumps(profile_valid))
    unknown_top["system_fingerprint"] = "fp_44709d6fcb"
    _write("chat_profile_unknown_top_level.json", unknown_top)

    bad_id = json.loads(json.dumps(profile_valid))
    bad_id["id"] = "cmpl-legacy-000042"
    _write("chat_profile_bad_id.json", bad_id)


def _image_usage() -> dict:
    return {
        "input_tokens": 110,
        "output_tokens": 4160,
        "total_tokens": 4270,
        "input_tokens_details": {"text_tokens": 100, "image_tokens": 0, "cached_tokens": 10},
        "output_tokens_details": {"text_tokens": 0, "image_tokens": 4160, "reasoning_tokens": 0},
    }


def record_image() -> None:
    good_png = _png_b64(1536, 1024, (31, 63, 127))
    wrong_size_png = _png_b64(512, 512, (200, 40, 40))

    def envelope(b64: str, revised: str = PROMPT) -> dict:
        return {"created": CREATED, "data": [{"b64_json": b64, "revised_prompt": revised}], "usage": _image_usage()}

    _write("image_valid.json", {"status": 200, "prompt": PROMPT, "body": envelope(good_png)})
    _write(
        "image_url_response.json",
        {
            "status": 200,
            "prompt": PROMPT,
            "body": {
                "created": CREATED,
                "data": [{"url": "https://cdn.example/img.png", "revised_prompt": PROMPT}],
                "usage": _image_usage(),
            },
        },
    )
    two_items = envelope(good_png)
    two_items["data"] = two_items["data"] + [{"b64_json": good_png, "revised_prompt": PROMPT}]
    _write("image_two_items.json", {"status": 200, "prompt": PROMPT, "body": two_items})
    _write("image_bad_b64.json", {"status": 200, "prompt": PROMPT, "body": envelope("@@not/base64@@")})
    _write("image_wrong_size_png.json", {"status": 200, "prompt": PROMPT, "body": envelope(wrong_size_png)})
    non_png = base64.b64encode(b"GIF89a-not-a-png-payload").decode("ascii")
    _write("image_non_png_payload.json", {"status": 200, "prompt": PROMPT, "body": envelope(non_png)})
    usage_mismatch = envelope(good_png)
    usage_mismatch["usage"]["total_tokens"] = 9999
    _write("image_usage_mismatch.json", {"status": 200, "prompt": PROMPT, "body": usage_mismatch})
    _write(
        "image_revised_prompt_drift.json",
        {"status": 200, "prompt": PROMPT, "body": envelope(good_png, revised=PROMPT + " (enhanced)")},
    )
    extra_top = envelope(good_png)
    extra_top["id"] = "img-42"
    _write("image_extra_top_level.json", {"status": 200, "prompt": PROMPT, "body": extra_top})
    _write(
        "image_http_502.json",
        {
            "status": 502,
            "prompt": PROMPT,
            "body": {"error": {"message": "Bad gateway from upstream relay", "type": "server_error"}},
        },
    )


def record_request() -> None:
    _write(
        "request_422_regression.json",
        {
            # distilled_from: phase2r116 —— schema_version 等内部字段曾泄漏进公网体触发 422。
            "schema_version": "step4_image_request_v1",
            "model": "gpt-image-2",
            "n": 1,
            "prompt": PROMPT,
            "response_format": "b64_json",
            "size": "1536x1024",
            "source_card_id": "step3-card-contract:0123456789abcdef01234567",
            "source_candidate_id": "candidate-table-01",
            "request_id": "step4-request-0001",
            "business_identity": "step4-business-0001",
            "source_contract_sha256": HEX64,
            "typed_contract_sha256": HEX64,
            "provenance_sha256": HEX64,
            "geometry_sha256": HEX64,
            "fact_ids": ["fact-0001"],
            "evidence_ids": ["ev-0001"],
            "anchor_ids": ["anchor-0001"],
        },
    )


def record_capability() -> None:
    request_id = "req-2f5c1b7d9e"

    def probe(kind: str, outcome: str, **extra: object) -> dict:
        return {"kind": kind, "outcome": outcome, **extra}

    cases = [
        {
            "case_id": "all_green",
            "expected_classification": "none",
            "expected_ready": True,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "success", http_status=200, provider_request_id=request_id),
                probe("strict_schema", "success", http_status=200, provider_request_id=request_id),
            ],
        },
        {
            "case_id": "models_404_nonblocking",
            "expected_classification": "none",
            "expected_ready": True,
            "probes": [
                probe("models", "failed", http_status=404, sanitized_detail="no such route"),
                probe("chat_json", "success", http_status=200, provider_request_id=request_id),
                probe("strict_schema", "success", http_status=200, provider_request_id=request_id),
            ],
        },
        {
            "case_id": "authentication_failure",
            "expected_classification": "authentication_failure",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "failed", http_status=401, sanitized_detail="unauthorized"),
            ],
        },
        {
            "case_id": "authentication_disguised_404",
            "expected_classification": "authentication_disguised_404",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "failed", http_status=404, sanitized_detail="invalid api key for this route"),
            ],
        },
        {
            "case_id": "endpoint_not_found",
            "expected_classification": "endpoint_not_found",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "failed", http_status=404, sanitized_detail="no route matches"),
            ],
        },
        {
            "case_id": "model_not_found",
            "expected_classification": "model_not_found",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=False),
                probe("chat_json", "failed", http_status=400, sanitized_detail="model gpt-5.5 not found"),
            ],
        },
        {
            "case_id": "chat_completions_incompatible",
            "expected_classification": "chat_completions_incompatible",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "failed", http_status=400, sanitized_detail="response_format rejected"),
            ],
        },
        {
            "case_id": "strict_schema_unsupported",
            "expected_classification": "strict_json_schema_unsupported",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "success", http_status=200, provider_request_id=request_id),
                probe("strict_schema", "failed", http_status=400, sanitized_detail="json_schema mode unsupported"),
            ],
        },
        {
            "case_id": "provenance_missing",
            "expected_classification": "provider_provenance_missing",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "success", http_status=200),
            ],
        },
        {
            "case_id": "transport_unknown_outcome",
            "expected_classification": "transport_unknown_outcome",
            "expected_ready": False,
            "probes": [
                probe("models", "success", http_status=200, model_advertised=True),
                probe("chat_json", "unknown_outcome", sanitized_detail="socket closed mid-response"),
            ],
        },
    ]
    _write("capability_cases.json", cases)


def main() -> None:
    record_chat()
    record_image()
    record_request()
    record_capability()


if __name__ == "__main__":
    main()
