"""Diagnostic probe for the real Step 2 OpenAI-compatible transport.

This module is intentionally not part of the production pipeline.  It sends
two tiny synthetic requests through the existing transport class, one streamed
and one non-streamed, and reports only safe capability metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from examcard.knowledge.contracts.enums import Step2DecoderConfig, Step2ResponseFormat, Step2Stage  # noqa: E402
from examcard.knowledge.contracts.hashing import canonical_step2_sha256  # noqa: E402
from examcard.knowledge.contracts.request_identity import make_step2_request_identity  # noqa: E402
from examcard.knowledge.contracts.responses import step2a_result_payload_model  # noqa: E402
from examcard.knowledge.contracts.versions import TABLE_CONTINUATION_STEP2A_REQUEST_VERSION  # noqa: E402
from examcard.knowledge.llm_transport import OpenAICompatibleStep2Transport  # noqa: E402

sys.path.insert(0, str(ROOT / "migration"))
from relay_preflight import parse_env_channels, resolve_channel  # noqa: E402


def _safe_failure(exc: BaseException, *, boundary: str) -> dict[str, object]:
    """Return exception type and boundary only; never provider text."""

    return {
        "provider_call_succeeded": False,
        "failure_boundary": boundary,
        "failure_type": type(exc).__name__,
    }


def _synthetic_payload() -> dict[str, object]:
    return {
        "sample_id": "step2-compat-probe",
        "material_profile_id": "probe-material-v1",
        "heading_only_allowed_anchor_span_ids": [],
        "continuation_records": [],
        "sections": [
            {
                "section_id": "section:probe",
                "page_num": 1,
                "text_evidence": [
                    {
                        "kind": "paragraph",
                        "context_only": False,
                        "anchor_spans": [
                            {
                                "anchor_span_id": "span:probe",
                                "text": "Synthetic probe evidence: 2 + 2 = 4.",
                            }
                        ],
                    }
                ],
                "tables": [],
                "controlled_diagrams": [],
            }
        ],
    }


def _probe_once(
    *,
    base_url: str,
    api_key: str,
    model: str,
    stream: bool,
    system_suffix: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    payload = _synthetic_payload()
    request_version = TABLE_CONTINUATION_STEP2A_REQUEST_VERSION
    schema = step2a_result_payload_model(request_version).model_json_schema()
    decoder = Step2DecoderConfig(
        max_tokens=1400,
        temperature=0,
        response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT,
    )
    identity = make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model=model,
        payload_sha256=canonical_step2_sha256(payload),
        relay_base_url=base_url,
        response_schema=schema,
        decoder=decoder,
    )
    transport = OpenAICompatibleStep2Transport(
        base_url=base_url,
        api_key=api_key,
        timeout=120.0,
        default_headers={"User-Agent": user_agent} if user_agent else None,
        stream=stream,
        system_suffix=system_suffix,
    )
    try:
        completion = transport.complete_payload(
            identity=identity,
            model_payload=payload,
            response_schema=schema,
            prompt="Return only the required JSON object for this compatibility probe.",
            use_idempotency_key=False,
        )
    except Exception as exc:
        return {
            "stream": stream,
            "http_status": None,
            "http_status_present": False,
            "completion_received": False,
            "provider_request_id_present": False,
            "strict_schema_valid": False,
            **_safe_failure(exc, boundary="provider call before TransportCompletion"),
        }

    result: dict[str, object] = {
        "stream": stream,
        "http_status": completion.http_status,
        "http_status_present": completion.http_status is not None,
        "completion_received": True,
        "provider_request_id_present": completion.provider_request_id is not None,
        "finish_reason": completion.finish_reason,
        "finish_reason_present": completion.finish_reason is not None,
    }
    try:
        decoded = json.loads(completion.content)
        step2a_result_payload_model(request_version).model_validate(decoded)
    except Exception as exc:
        result.update(
            {
                "strict_schema_valid": False,
                "failure_boundary": "local JSON/schema validation after completion",
                "failure_type": type(exc).__name__,
            }
        )
    else:
        result.update(
            {
                "strict_schema_valid": True,
                "failure_boundary": None,
                "failure_type": None,
            }
        )
    return result


def run_probe(*, channel: str = "last", env_path: Path | None = None) -> dict[str, Any]:
    if os.environ.get("REAL_NETWORK") != "1":
        return {
            "status": "blocked",
            "failure_boundary": "REAL_NETWORK must equal 1",
        }
    config_path = env_path or ROOT / ".env"
    if not config_path.is_file():
        return {"status": "blocked", "failure_boundary": "runtime configuration file is missing"}
    config = parse_env_channels(config_path.read_text(encoding="utf-8"))
    try:
        selected = resolve_channel(config, channel=channel, base_url=None)
    except Exception:
        return {"status": "blocked", "failure_boundary": "text provider channel is unavailable"}
    model = str(config.get("globals", {}).get("TEXT_MODEL", "")).strip()
    base_url = str(selected.get("base_url", "")).strip()
    api_key = str(selected.get("api_key", "")).strip()
    if not model or not base_url or not api_key:
        return {"status": "blocked", "failure_boundary": "required provider configuration is missing"}

    globals_ = config.get("globals", {})
    common = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "system_suffix": str(globals_["STEP2A_REINFORCEMENT_V1"])
        if globals_.get("STEP2A_REINFORCEMENT_V1")
        else None,
        "user_agent": str(globals_["OPENAI_USER_AGENT"]) if globals_.get("OPENAI_USER_AGENT") else None,
    }
    streaming = _probe_once(stream=True, **common)
    non_streaming = _probe_once(stream=False, **common)
    return {
        "status": "completed",
        "channel": channel,
        "model": model,
        "streaming": streaming,
        "non_streaming": non_streaming,
        "provider_supports_streaming": bool(streaming.get("completion_received")),
        "provider_supports_strict_schema_streaming": bool(streaming.get("strict_schema_valid")),
        "provider_supports_strict_schema_non_streaming": bool(non_streaming.get("strict_schema_valid")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Step 2 provider compatibility without persisting responses.")
    parser.add_argument("--channel", choices=("first", "last"), default="last")
    args = parser.parse_args()
    print(json.dumps(run_probe(channel=args.channel), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
