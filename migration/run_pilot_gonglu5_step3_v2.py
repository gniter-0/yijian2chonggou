"""step3 v2 单卡回显 pilot（逐卡 live，独立驱动）。

背景（T3，docs/prework/P3_优化施工方案_T1-T5_20260812.md §4 与 §8-1）：
step3 v1 契约的「三代表卡一次性整体回显」在两中继全部证伪（chatgpt2api 空数组
逃逸；2chat 网关掐断/上游 APIError）。v2 契约把颗粒降为单卡一发（输出 ~1-1.5K
tokens，step2b 同量级 5/5 全绿先例），本驱动逐卡打满全部契约卡。

【语料口径勘误（本驱动实测确认，报总指挥归档）】方案与任务书写的「r810 →
compile_reviewed_fixture → 16 张契约」两半互斥：r810 reviewed typed-evidence
fixture（唯一存世的 typed-evidence 语料，多科目 shizheng/ganghang/tongxin）
实际编译 **6** 张契约——v1 驱动 dry-run 报告 compiled_contract_count=6 同证。
「黄金五章 16 卡」属 r2242 step4 发布语料（Step3MergedReviewedReleaseCard，
V1 学习卡形状：canonical_title/image_prompt/expected_texts…均在 v2 禁字段表），
**不含 typed layout/source_binding，结构上不可喂 v2 回显**。故本驱动按 N 卡
通用：预算 = 探针 + 2N；验收线 N==16 时 14（方案 §4-5 口径），否则 N 全通过，
可用 --min-pass 显式覆盖。

链路（全新仓部件；输入链与 v1 驱动 run_pilot_gonglu5_step3.py 同源）：
- r810 reviewed typed-evidence fixture（老仓只读）→ ``compile_reviewed_fixture``
  → N 张 Step3CardContract 离线工件；
- 逐卡组合 contracts[i] + model_input.cards[i]（保序一一对应）→
  ``build_single_card_model_input`` → strict 业务请求（RESULT_SCHEMA_V2，
  draft_cards minItems=1/maxItems=1）；
- 响应门禁：SSE 流式聚合最小信封 → ``make_single_card_inspector``（内容关权威）；
  非流式路径另过 providers.inspect_chat_envelope（信封关：profile 失败降级
  allowlist 复检）；
- 纪律：1 次 {"ok"} 探针先行；预算硬上限 = 探针 + 每卡 2 发；失败卡以**新身份**
  重发一次（identity 掺 attempt + manual_reissue_reason，禁复用失败身份；补强
  system 在传输层注入、不进 identity——与 step2 pilot 实测口径一致，
  reinforcement_id 单独入 journal 审计）；
- 密钥读 .env（默认新仓 exam-card-engine/.env，缺席回落 yijian2/.env，均只读）；
  产物落 --storage-root，老仓零写入。

运行（exam-card-engine 仓根）：
  uv run python migration/run_pilot_gonglu5_step3_v2.py --dry-run --storage-root data/pilot_gonglu5/step3v2_dryrun
  uv run python migration/run_pilot_gonglu5_step3_v2.py --allow-live --storage-root data/pilot_gonglu5/step3v2_live
  uv run python migration/run_pilot_gonglu5_step3_v2.py --allow-live --limit 1 --storage-root data/pilot_gonglu5/step3v2_smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.compilers.step3_card import compile_reviewed_fixture  # noqa: E402
from examcard.compilers.step3_response_v2 import (  # noqa: E402
    RESULT_SCHEMA_V2,
    build_single_card_model_input,
    build_single_card_prompt,
    make_single_card_inspector,
    prompt_v2_sha256,
    result_schema_v2_sha256,
)
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture  # noqa: E402
from examcard.providers import (  # noqa: E402
    build_chat_allowlist,
    build_chat_profile,
    inspect_chat_envelope,
)
from examcard.pipeline.shadow_wiring import load_env_file  # noqa: E402

R810_FIXTURE = OLD_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1/reviewed-typed-evidence-fixture.json"
REPORT_NAME = "pilot_gonglu5_step3_v2_report.json"
MAX_ATTEMPTS_PER_CARD = 2
# 方案 §4-5 验收口径：16 卡 ≥14/16（对齐 step4 首发成功率口径）；其余卡数（r810
# 现实为 6 卡，或 --limit 冒烟）默认要求全通过，--min-pass 可显式覆盖。
ACCEPTANCE_MIN_PASS_FULL16 = 14
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
# 自带补强 system（独立驱动不依赖 T2 并行轨的 reinforcement 模块）：针对两个实测
# 已知失败模式——空数组逃逸、input 版本号错当 result 版本号回显。传输层注入，
# 不进请求 identity（与 step2 pilot 实测口径一致），reinforcement_id 入 journal。
REINFORCEMENT_SYSTEM_V2 = (
    "You must return draft_cards with exactly 1 item that echoes the supplied card verbatim from "
    "MODEL_INPUT_JSON. An empty draft_cards array is invalid. Echo contract_id, source_candidate_id, "
    "fact_ids, presentation, source_binding, visible_elements and layout byte-for-byte without any change. "
    "The top-level schema_version of your reply must be exactly the value required by the response json "
    "schema; do not copy the schema_version value from MODEL_INPUT_JSON."
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _resolve_env_file(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    new_env = NEW_ROOT / ".env"
    if new_env.is_file():
        return new_env
    return OLD_ROOT / ".env"


def _request_identity(model_input_sha256: str, model_input_id: str, attempt: int, reissue_reason: str | None) -> str:
    # 失败卡新身份重发：attempt 与 manual_reissue_reason 进 seed，禁复用失败身份。
    seed = {
        "pilot": "gonglu5_step3_v2",
        "model_input_id": model_input_id,
        "model_input_sha256": model_input_sha256,
        "prompt_sha256": prompt_v2_sha256(),
        "response_schema_sha256": result_schema_v2_sha256(),
        "attempt": attempt,
        "manual_reissue_reason": reissue_reason,
    }
    return f"step3-v2-live-request:{_sha256_json(seed)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SSE streaming business calls (default on; defeats gateway first-byte timeout)",
    )
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=None, help="run config (.env); default exam-card-engine/.env, fallback yijian2/.env")
    parser.add_argument("--limit", type=int, default=0, help="only first N cards (0 = all); live smoke helper")
    parser.add_argument("--only-contracts", type=str, default=None, help="comma separated contract_id list; reissue helper")
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--min-pass", type=int, default=None, help="acceptance threshold override (default: 14 when 16 cards else all)")
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")

    env_path = _resolve_env_file(args.env_file)
    env = load_env_file(env_path)
    missing = [key for key in ("API_BASE_URL", "TEXT_MODEL") if not env.get(key)]
    if args.allow_live and not env.get("API_KEY"):
        missing.append("API_KEY")
    if missing:
        raise SystemExit(f"{env_path} is missing required keys: {missing}")
    base_url = env["API_BASE_URL"].rstrip("/")
    model = env["TEXT_MODEL"]

    storage = Path(args.storage_root).resolve()
    storage.mkdir(parents=True, exist_ok=True)

    fixture_bytes = R810_FIXTURE.read_bytes()
    fixture = ReviewedTypedEvidenceFixture.model_validate_json(fixture_bytes.decode("utf-8"))
    compilation = compile_reviewed_fixture(fixture, source_fixture_sha256=_sha256_bytes(fixture_bytes))

    prompt = build_single_card_prompt()
    # contracts[i] 与 model_input.cards[i] 保序一一对应（pilot 已验证）；
    # build_single_card_model_input 内建配对守卫（presentation/typed 哈希互证）。
    plans: list[dict[str, object]] = []
    for index, (contract, projected) in enumerate(zip(compilation.contracts, compilation.model_input.cards, strict=True), start=1):
        model_input = build_single_card_model_input(contract, projected)
        user_content = (
            prompt.prompt_text
            + "\n\nMODEL_INPUT_JSON:\n"
            + json.dumps(model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        plans.append(
            {
                "index": index,
                "contract_id": contract.contract_id,
                "presentation": contract.presentation,
                "model_input": model_input,
                "model_input_sha256": _sha256_json(model_input.model_dump(mode="json")),
                "user_content": user_content,
            }
        )

    if args.only_contracts:
        wanted = {item.strip() for item in args.only_contracts.split(",") if item.strip()}
        plans = [plan for plan in plans if plan["contract_id"] in wanted]
        missing_ids = wanted - {plan["contract_id"] for plan in plans}
        if missing_ids:
            raise SystemExit(f"--only-contracts has unknown contract ids: {sorted(missing_ids)}")
    if args.limit > 0:
        plans = plans[: args.limit]
    if not plans:
        raise SystemExit("no cards selected")

    card_count = len(plans)
    acceptance_min_pass = args.min_pass if args.min_pass is not None else (
        ACCEPTANCE_MIN_PASS_FULL16 if card_count == 16 else card_count
    )
    if not 0 < acceptance_min_pass <= card_count:
        raise SystemExit(f"--min-pass must be within 1..{card_count}")
    network_call_budget = 1 + MAX_ATTEMPTS_PER_CARD * card_count

    report: dict[str, object] = {
        "pilot": "gonglu5_step3_v2",
        "started_at_utc": _utc_now(),
        "mode": "live" if args.allow_live else "dry_run",
        "model": model,
        "env_file": str(env_path),
        "transport": "sse" if args.stream else "plain",
        "source_fixture_sha256": compilation.source_fixture_sha256,
        "compiled_contract_count": len(compilation.contracts),
        "offline_gate_valid": compilation.gate.valid,
        "card_count": card_count,
        "acceptance_min_pass": acceptance_min_pass,
        "prompt_sha256": prompt_v2_sha256(),
        "response_schema_sha256": result_schema_v2_sha256(),
        "reinforcement_id": _sha256_json(REINFORCEMENT_SYSTEM_V2)[:16],
        "max_tokens": args.max_tokens,
        "network_call_budget": network_call_budget,
        "probe": None,
        "cards": [],
        "passed_cards": 0,
        "accepted": False,
        "stop_reason": None,
    }

    def card_dir(plan: dict[str, object]) -> Path:
        contract_short = str(plan["contract_id"]).rsplit(":", maxsplit=1)[-1]
        return storage / "cards" / f"{plan['index']:02d}_{contract_short}"

    requests_manifest = []
    for plan in plans:
        model_input = plan["model_input"]
        first_request_id = _request_identity(str(plan["model_input_sha256"]), model_input.model_input_id, 1, None)
        _atomic_write_json(
            card_dir(plan) / "model-input.json",
            {
                "model_input": model_input.model_dump(mode="json"),
                "user_content_utf8_bytes": len(str(plan["user_content"]).encode("utf-8")),
                "first_request_id": first_request_id,
            },
        )
        requests_manifest.append(
            {
                "index": plan["index"],
                "contract_id": plan["contract_id"],
                "presentation": plan["presentation"],
                "model_input_id": model_input.model_input_id,
                "request_id": first_request_id,
                "user_content_utf8_bytes": len(str(plan["user_content"]).encode("utf-8")),
            }
        )
    _atomic_write_json(
        storage / "requests-manifest.json",
        {
            "prompt_text": prompt.prompt_text,
            "prompt_sha256": prompt_v2_sha256(),
            "response_schema_sha256": result_schema_v2_sha256(),
            "reinforcement_system": REINFORCEMENT_SYSTEM_V2,
            "requests": requests_manifest,
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
                    "passed_cards": report["passed_cards"],
                    "card_count": report["card_count"],
                    "accepted": report["accepted"],
                    "stop_reason": report["stop_reason"],
                },
                ensure_ascii=False,
            )
        )
        return exit_code

    if not args.allow_live:
        report.update(
            {
                "network_calls": 0,
                "passed_cards": 0,
                "accepted": compilation.gate.valid and len(requests_manifest) == card_count,
                "stop_reason": "dry_run_complete",
            }
        )
        return finish(0)

    headers = {"Authorization": f"Bearer {env['API_KEY']}", "Content-Type": "application/json"}
    if env.get("OPENAI_USER_AGENT"):
        headers["User-Agent"] = env["OPENAI_USER_AGENT"]
    client = httpx.Client(timeout=300.0, follow_redirects=False, headers=headers)
    oa = None
    if args.stream:
        from openai import OpenAI

        oa = OpenAI(
            base_url=base_url,
            api_key=env["API_KEY"],
            timeout=600,
            max_retries=0,
            default_headers={"User-Agent": env["OPENAI_USER_AGENT"]} if env.get("OPENAI_USER_AGENT") else None,
        )
    network_calls = 0
    budget_exhausted = False
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
                "json_schema": {"name": "step3_v2_probe", "strict": True, "schema": PROBE_SCHEMA},
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

        def run_attempt(plan: dict[str, object], attempt: int, request_id: str) -> dict[str, object]:
            nonlocal network_calls
            model_input = plan["model_input"]
            inner = make_single_card_inspector(model_input)
            messages = [
                {"role": "system", "content": REINFORCEMENT_SYSTEM_V2},
                {"role": "user", "content": str(plan["user_content"])},
            ]
            business_payload = {
                "model": model,
                "messages": messages,
                "max_tokens": args.max_tokens,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "step3_v2_single_card_result", "strict": True, "schema": RESULT_SCHEMA_V2},
                },
            }
            attempt_started = time.perf_counter()
            entry: dict[str, object] = {"attempt": attempt, "request_id": request_id}
            directory = card_dir(plan)
            if args.stream:
                outcome = "failed"
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
                except Exception as error:  # noqa: BLE001 - live 网关异常形态多，记录后判负
                    error_type = type(error).__name__
                    http_status = getattr(error, "status_code", None)
                network_calls += 1
                minimal_envelope = json.dumps(
                    {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
                    ensure_ascii=False,
                ).encode("utf-8")
                (directory / f"attempt-{attempt}.response.raw.json").write_bytes(minimal_envelope)
                gate = inner(minimal_envelope) if content else None
                if gate is not None:
                    _atomic_write_json(directory / f"attempt-{attempt}.gate.json", gate.model_dump(mode="json"))
                outcome = "success" if gate is not None and gate.valid else "failed"
                entry.update(
                    {
                        "transport": "sse",
                        "http_status": http_status,
                        "latency_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                        "response_bytes": len(content.encode("utf-8")),
                        "error_type": error_type,
                        "gate_valid": bool(gate and gate.valid),
                        "gate_failures": list(gate.failure_codes) if gate is not None else ["inner-not-reached"],
                        "outcome": outcome,
                    }
                )
                return entry
            response = client.post(f"{base_url}/chat/completions", json=business_payload)
            network_calls += 1
            (directory / f"attempt-{attempt}.response.raw.json").write_bytes(response.content)
            profile_inspection = inspect_chat_envelope(
                response.content, inner_inspector=inner, profile=build_chat_profile(expected_model=None)
            )
            allowlist_inspection = None
            if not profile_inspection.gate.valid:
                allowlist_inspection = inspect_chat_envelope(
                    response.content, inner_inspector=inner, allowlist=build_chat_allowlist()
                )
            effective = allowlist_inspection or profile_inspection
            inner_gate = effective.inner_gate
            if inner_gate is not None:
                _atomic_write_json(directory / f"attempt-{attempt}.gate.json", inner_gate.model_dump(mode="json"))
            outcome = (
                "success"
                if response.status_code == 200 and effective.gate.valid and inner_gate is not None and inner_gate.valid
                else "failed"
            )
            entry.update(
                {
                    "transport": "plain",
                    "http_status": response.status_code,
                    "latency_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                    "response_bytes": len(response.content),
                    "profile_gate_valid": profile_inspection.gate.valid,
                    "allowlist_gate_valid": allowlist_inspection.gate.valid if allowlist_inspection else None,
                    "gate_valid": bool(inner_gate and inner_gate.valid),
                    "gate_failures": list(inner_gate.failure_codes) if inner_gate is not None else ["inner-not-reached"],
                    "outcome": outcome,
                }
            )
            return entry

        for plan in plans:
            card_report: dict[str, object] = {
                "index": plan["index"],
                "contract_id": plan["contract_id"],
                "presentation": plan["presentation"],
                "model_input_id": plan["model_input"].model_input_id,
                "attempts": [],
                "outcome": "not-attempted",
            }
            report["cards"].append(card_report)
            for attempt in range(1, MAX_ATTEMPTS_PER_CARD + 1):
                if network_calls >= network_call_budget:
                    budget_exhausted = True
                    break
                reissue_reason = None if attempt == 1 else f"manual_reissue_step3v2_r{attempt}"
                request_id = _request_identity(
                    str(plan["model_input_sha256"]), plan["model_input"].model_input_id, attempt, reissue_reason
                )
                entry = run_attempt(plan, attempt, request_id)
                card_report["attempts"].append(entry)
                if entry["outcome"] == "success":
                    card_report["outcome"] = "success"
                    break
                card_report["outcome"] = "failed"
            if budget_exhausted:
                break
    finally:
        client.close()

    passed = sum(1 for card in report["cards"] if card["outcome"] == "success")
    accepted = passed >= acceptance_min_pass and not budget_exhausted
    report.update(
        {
            "network_calls": network_calls,
            "passed_cards": passed,
            "accepted": accepted,
            "stop_reason": "budget_exhausted" if budget_exhausted else ("completed" if accepted else "completed_with_failures"),
        }
    )
    return finish(0 if accepted else 1)


if __name__ == "__main__":
    raise SystemExit(main())
