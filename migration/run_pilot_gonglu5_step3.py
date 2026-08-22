"""公路黄金五章 step3 strict 响应 pilot（三代表卡 live，r92/r97 冻结口径）。

背景（pilot 决策 20260812）：step3 的 v1 冻结契约钉扎三代表卡（table/ordered_list/
formula 固定序，`ResponseModelInput` 模型自身校验；放宽卡数属 v2 新版本号范畴），
故本段按老仓 r97 real pilot 同口径在新仓部件上重放：校准当前中继/模型下 step3
strict json_schema 响应的可用性与单价。

链路（全新仓部件）：
- r810 reviewed typed-evidence fixture（老仓只读）→ `compile_reviewed_fixture`
  → Step3CardContract 全套离线工件；
- 三代表卡投影 `build_response_model_input` → strict 业务请求（ResponseDocument
  json_schema）；
- 响应双级门禁：providers.inspect_chat_envelope（信封关：r96 画像，失败降级
  allowlist 复检）→ make_inner_inspector（内容关：逐字段保全校验）。
- 纪律：1 次 {"ok"} 形状小探针先行；业务 1 次；预算硬上限 4；密钥自
  yijian2\\.env 只读；产物落 storage root，老仓零写入。

运行（exam-card-engine 仓根）：
  uv run python migration/run_pilot_gonglu5_step3.py --dry-run --storage-root data/pilot_gonglu5/step3_dryrun
  uv run python migration/run_pilot_gonglu5_step3.py --allow-live --storage-root data/pilot_gonglu5/step3_live
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.compilers.step3_card import compile_reviewed_fixture  # noqa: E402
from examcard.compilers.step3_response import (  # noqa: E402
    ResponseDocument,
    build_prompt,
    build_response_model_input,
    make_inner_inspector,
)
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture  # noqa: E402
from examcard.providers import (  # noqa: E402
    build_chat_allowlist,
    build_chat_profile,
    inspect_chat_envelope,
)
from examcard.pipeline.shadow_wiring import load_env_file  # noqa: E402

R810_FIXTURE = OLD_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1/reviewed-typed-evidence-fixture.json"
REPRESENTATIVE_PRESENTATIONS = ("table", "ordered_list", "formula")
NETWORK_CALL_BUDGET = 4
REPORT_NAME = "pilot_gonglu5_step3_report.json"
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stream", action="store_true", help="SSE streaming business call (defeats gateway first-byte timeout)")
    parser.add_argument("--storage-root", type=Path, required=True)
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")

    env = load_env_file(OLD_ROOT / ".env")
    missing = [key for key in ("API_BASE_URL", "TEXT_MODEL") if not env.get(key)]
    if args.allow_live and not env.get("API_KEY"):
        missing.append("API_KEY")
    if missing:
        raise SystemExit(f"yijian2/.env is missing required keys: {missing}")
    base_url = env["API_BASE_URL"].rstrip("/")
    model = env["TEXT_MODEL"]

    storage = Path(args.storage_root).resolve()
    storage.mkdir(parents=True, exist_ok=True)

    fixture_bytes = R810_FIXTURE.read_bytes()
    fixture = ReviewedTypedEvidenceFixture.model_validate_json(fixture_bytes.decode("utf-8"))
    compilation = compile_reviewed_fixture(fixture, source_fixture_sha256=_sha256_bytes(fixture_bytes))

    # build_response_model_input 需要 contract 身份 + model 投影两面字段；
    # compilation.contracts 与 compilation.model_input.cards 是保序一一投影，按位组合。
    representatives = []
    for presentation in REPRESENTATIVE_PRESENTATIONS:
        pair = next(
            (
                (contract, projected)
                for contract, projected in zip(compilation.contracts, compilation.model_input.cards)
                if contract.presentation == presentation
            ),
            None,
        )
        if pair is None:
            raise SystemExit(f"no compiled card for representative presentation: {presentation}")
        contract, projected = pair
        representatives.append(
            SimpleNamespace(
                contract_id=contract.contract_id,
                source_candidate_id=contract.source_candidate_id,
                fact_ids=contract.fact_ids,
                presentation=contract.presentation,
                source_binding=projected.source_binding,
                visible_elements=projected.visible_elements,
                layout=projected.layout,
                geometry=projected.geometry,
            )
        )
    model_input = build_response_model_input(representatives, source_fixture_sha256=compilation.source_fixture_sha256)
    prompt = build_prompt()
    result_schema = ResponseDocument.model_json_schema()
    user_content = (
        prompt.prompt_text
        + "\n\nMODEL_INPUT_JSON:\n"
        + json.dumps(model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

    report: dict[str, object] = {
        "pilot": "gonglu5_step3",
        "started_at_utc": _utc_now(),
        "mode": "live" if args.allow_live else "dry_run",
        "model": model,
        "source_fixture_sha256": compilation.source_fixture_sha256,
        "compiled_contract_count": len(compilation.contracts),
        "offline_gate_valid": compilation.gate.valid,
        "representative_cards": [
            {"contract_id": card.contract_id, "presentation": card.presentation} for card in representatives
        ],
        "model_input_id": model_input.model_input_id,
        "prompt_sha256": _sha256_bytes(prompt.prompt_text.encode("utf-8")),
        "user_content_utf8_bytes": len(user_content.encode("utf-8")),
        "network_call_budget": NETWORK_CALL_BUDGET,
        "probe": None,
        "business": None,
        "accepted": False,
        "stop_reason": None,
    }
    _atomic_write_json(
        storage / "request-manifest.json",
        {
            "model_input": model_input.model_dump(mode="json"),
            "prompt_text": prompt.prompt_text,
            "result_schema_sha256": _sha256_bytes(
                json.dumps(result_schema, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ),
            "user_content_utf8_bytes": len(user_content.encode("utf-8")),
        },
    )

    def finish(exit_code: int) -> int:
        report["finished_at_utc"] = _utc_now()
        _atomic_write_json(storage / REPORT_NAME, report)
        print(
            json.dumps(
                {
                    "report": str(storage / REPORT_NAME),
                    "mode": report["mode"],
                    "network_calls": report.get("network_calls", 0),
                    "accepted": report["accepted"],
                    "stop_reason": report["stop_reason"],
                },
                ensure_ascii=False,
            )
        )
        return exit_code

    if not args.allow_live:
        report.update({"network_calls": 0, "accepted": compilation.gate.valid, "stop_reason": "dry_run_complete"})
        return finish(0)

    headers = {"Authorization": f"Bearer {env['API_KEY']}", "Content-Type": "application/json"}
    if env.get("OPENAI_USER_AGENT"):
        headers["User-Agent"] = env["OPENAI_USER_AGENT"]
    client = httpx.Client(timeout=300.0, follow_redirects=False, headers=headers)
    network_calls = 0
    try:
        started = time.perf_counter()
        probe_payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Return exactly this JSON object with no extra fields or prose: {\"ok\": true}",
                }
            ],
            "max_tokens": 64,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "step3_probe", "strict": True, "schema": PROBE_SCHEMA},
            },
        }
        probe_response = client.post(f"{base_url}/chat/completions", json=probe_payload)
        network_calls += 1
        probe_ok = False
        try:
            probe_document = probe_response.json()
            probe_ok = (
                probe_response.status_code == 200
                and json.loads(probe_document["choices"][0]["message"]["content"]).get("ok") is True
            )
        except (ValueError, KeyError, IndexError, TypeError):
            probe_ok = False
        report["probe"] = {
            "http_status": probe_response.status_code,
            "outcome": "success" if probe_ok else "failed",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if not probe_ok:
            report.update({"network_calls": network_calls, "stop_reason": "chat_schema_probe_failed"})
            return finish(1)

        # 诊断性强化（不动冻结的 PromptContract 本体，只加传输层 system 消息）：
        # 首轮实测模型返回空 draft_cards（strict minItems 未被中继强制），
        # 用显式回显指令区分「偶发偷懒」与「稳定不遵从」。
        messages = [
            {
                "role": "system",
                "content": (
                    "You must return draft_cards with exactly 3 items, echoing all three supplied cards "
                    "verbatim from MODEL_INPUT_JSON in source order. An empty draft_cards array is invalid. "
                    "schema_version must be exactly the value required by the response schema."
                ),
            },
            {"role": "user", "content": user_content},
        ]
        business_payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 8000,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "step3_result", "strict": True, "schema": result_schema},
            },
        }
        started = time.perf_counter()
        inner = make_inner_inspector(model_input)
        if args.stream:
            # SSE 流式：聚合 delta 后重建最小信封（choices-only，即 r96 剥 transport-only
            # 投影的形状）喂内容门禁；流式下无单一 JSON body，信封关记 n/a。
            from openai import OpenAI

            oa = OpenAI(
                base_url=base_url,
                api_key=env["API_KEY"],
                timeout=600,
                max_retries=0,
                default_headers={"User-Agent": env["OPENAI_USER_AGENT"]} if env.get("OPENAI_USER_AGENT") else None,
            )
            http_status: int | None = 200
            error_type: str | None = None
            content = ""
            try:
                stream = oa.chat.completions.create(stream=True, **business_payload)
                parts: list[str] = []
                try:
                    for chunk in stream:
                        choices = getattr(chunk, "choices", None) or ()
                        if choices:
                            delta = getattr(choices[0].delta, "content", None)
                            if delta:
                                parts.append(delta)
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()
                content = "".join(parts).strip()
            except Exception as error:
                error_type = type(error).__name__
                http_status = getattr(error, "status_code", None)
            network_calls += 1
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            minimal_envelope = json.dumps(
                {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            (storage / "business-response.raw.json").write_bytes(minimal_envelope)
            inner_gate = inner(minimal_envelope) if content else None
            report["business"] = {
                "transport": "sse",
                "http_status": http_status,
                "latency_ms": latency_ms,
                "response_bytes": len(content.encode("utf-8")),
                "error_type": error_type,
                "profile_gate_valid": None,
                "allowlist_gate_valid": None,
                "inner_gate_valid": bool(inner_gate and inner_gate.valid),
                "inner_gate_failures": list(inner_gate.failure_codes) if inner_gate else ["inner-not-reached"],
                "outcome": "success" if inner_gate and inner_gate.valid else "failed",
            }
        else:
            response = client.post(f"{base_url}/chat/completions", json=business_payload)
            network_calls += 1
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            (storage / "business-response.raw.json").write_bytes(response.content)

            profile_inspection = inspect_chat_envelope(
                response.content, inner_inspector=inner, profile=build_chat_profile(expected_model=None)
            )
            allowlist_inspection = None
            if not profile_inspection.gate.valid:
                allowlist_inspection = inspect_chat_envelope(
                    response.content, inner_inspector=inner, allowlist=build_chat_allowlist()
                )
            effective = allowlist_inspection or profile_inspection
            report["business"] = {
                "transport": "plain",
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "response_bytes": len(response.content),
                "profile_gate_valid": profile_inspection.gate.valid,
                "profile_gate_failures": list(profile_inspection.gate.failure_codes),
                "allowlist_gate_valid": allowlist_inspection.gate.valid if allowlist_inspection else None,
                "allowlist_gate_failures": list(allowlist_inspection.gate.failure_codes) if allowlist_inspection else None,
                "inner_gate_valid": bool(effective.inner_gate and effective.inner_gate.valid),
                "inner_gate_failures": list(effective.inner_gate.failure_codes)
                if effective.inner_gate
                else ["inner-not-reached"],
                "outcome": "success"
                if response.status_code == 200 and effective.gate.valid and effective.inner_gate and effective.inner_gate.valid
                else "failed",
            }
    finally:
        client.close()

    accepted = report["business"] is not None and report["business"]["outcome"] == "success"
    report.update(
        {
            "network_calls": network_calls,
            "accepted": accepted,
            "stop_reason": "completed" if accepted else "completed_with_failures",
        }
    )
    return finish(0 if accepted else 1)


if __name__ == "__main__":
    raise SystemExit(main())
