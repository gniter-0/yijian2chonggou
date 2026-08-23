"""公路黄金五章 V25/V25B live pilot · Step2 段（step2a+step2b 全五章真实跑）。

背景（pilot 决策 20260812，用户已批 ≈$5 全链）：本脚本是全链 pilot 的第一段，
校准 V2 语义链的真实单价、时延与一次通过率；step3/step4 段随后另行驱动。

方案要点：
- 资产：老仓 yijian2 只读（r12/r13 reviewed 侧车全家桶 + 黄金 registry + 内容策略）；
- 引擎：全复用 examcard.knowledge.contracts.phase2r14_v25_live 的装配/执行/门禁部件，
  样本面从 r14 的 2 个扩到黄金五章（build_live_sample_contexts(sample_ids=…)）；
- 纪律：两枚 strict 探针先行（中继波动实测存在——502/截断 JSON——每枚允许重发 1 次，
  重发是新的探测请求不是同身份自动重试）；正式样本调用失败登记后继续下一样本
  （单点波动不熔断整场，失败样本可另行补跑）；黄金门禁失败只登记不熔断——
  pilot 的目的是测质量落点，不是验收；预算硬上限 14 次（≤4 探针 + 5×step2a + 5×step2b）；
  老仓零写入；运行配置默认读新仓根 .env（T2 迁移，live_run.load_run_env；
  --env-file 可显式指回旧路径），密钥仅驻内存不落盘不入日志。
- 生产配方（T2 产品化后）：--reinforce/--stream 直接走引擎可选参
  （OpenAICompatibleStep2Transport 的 system_suffix / stream），私有 transport
  子类已删；补强文本常量在 examcard.knowledge.reinforcement（pilot 文本一字不改），
  reinforcement_id 入运行报告审计。

运行（exam-card-engine 仓根）：
  uv run python migration/run_pilot_gonglu5_step2.py --dry-run --storage-root data/pilot_gonglu5/step2_dryrun
  uv run python migration/run_pilot_gonglu5_step2.py --allow-live --manual-reissue-reason pilot5-step2-20260812 --storage-root data/pilot_gonglu5/step2_live
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.knowledge.contracts.phase2r14_v25_live import (  # noqa: E402
    _atomic_write_json,
    _card_golden_gate,
    _execute_stage,
    _learning_fact_gate,
    _stage_request_payload,
    _strict_probe,
    _teaching_gate,
    assess_v25_response_learning_golden,
    build_live_sample_contexts,
    freeze_phase2r13_inputs,
)
from examcard.knowledge.contracts import fingerprint_step2_relay  # noqa: E402
from examcard.knowledge.contracts.v25_generic_contracts import (  # noqa: E402
    V25BStep2BResultPayload,
    V25Step2AResultPayload,
    assess_v25_step2a_response,
    assess_v25b_step2b_response,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport  # noqa: E402
from examcard.knowledge.reinforcement import (  # noqa: E402
    STEP2A_REINFORCEMENT_V1,
    STEP2A_REINFORCEMENT_V1_ID,
)
from examcard.pipeline.live_run import load_run_env  # noqa: E402
from examcard.providers.base import sanitize_error_text  # noqa: E402

PILOT_SAMPLE_IDS = (
    "gonglu_01_text_list",
    "gonglu_24_table_formula",
    "gonglu_41_diagram_mixed",
    "gonglu_55_standard_constraints",
    "gonglu_62_cost_flow_incident",
)
NETWORK_CALL_BUDGET = 14
PROBE_ATTEMPTS = 2
REPORT_NAME = "pilot_gonglu5_step2_report.json"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _stage_size_probe(request: object, stage: str) -> dict[str, int]:
    model_payload, prompt, response_schema = _stage_request_payload(request, stage)
    return {
        "payload_utf8_bytes": _json_bytes(model_payload),
        "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "response_schema_utf8_bytes": _json_bytes(response_schema),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true", help="required acknowledgement before relay calls")
    parser.add_argument("--dry-run", action="store_true", help="build requests and sizes only; zero network calls")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--manual-reissue-reason", default=None)
    parser.add_argument("--reinforce", action="store_true", help="append anti-noise reinforcement to the system prompt")
    parser.add_argument("--stream", action="store_true", help="use SSE streaming transport (defeats gateway idle timeout)")
    parser.add_argument("--only-samples", default=None, help="comma-separated sample id substrings (retry lane)")
    parser.add_argument("--env-file", type=Path, default=None, help="run config (.env); default exam-card-engine/.env")
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")
    if args.allow_live and not args.manual_reissue_reason:
        parser.error("--manual-reissue-reason is required for live request identities")
    if args.allow_live and (
        len(args.manual_reissue_reason) > 96
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", args.manual_reissue_reason) is None
    ):
        parser.error("--manual-reissue-reason must be an ASCII audit identifier of at most 96 characters")
    reason = args.manual_reissue_reason or "pilot5-step2-dry-run"

    env = load_run_env(args.env_file)
    missing = [key for key in ("API_BASE_URL", "TEXT_MODEL") if not env.get(key)]
    if args.allow_live and not env.get("API_KEY"):
        missing.append("API_KEY")
    if missing:
        raise SystemExit(f"run env file is missing required keys: {missing}")
    base_url = env["API_BASE_URL"]
    model = env["TEXT_MODEL"]

    storage = Path(args.storage_root).resolve()
    if storage.exists() and any(storage.iterdir()):
        raise SystemExit(f"storage root must be new or empty: {storage}")
    storage.mkdir(parents=True, exist_ok=True)

    selected_ids = PILOT_SAMPLE_IDS
    if args.only_samples:
        needles = [item.strip() for item in args.only_samples.split(",") if item.strip()]
        selected_ids = tuple(sid for sid in PILOT_SAMPLE_IDS if any(needle in sid for needle in needles))
        if not selected_ids:
            raise SystemExit(f"--only-samples matched nothing: {args.only_samples}")

    frozen_before = freeze_phase2r13_inputs(repository_root=OLD_ROOT)
    contexts = build_live_sample_contexts(
        repository_root=OLD_ROOT,
        model=model,
        relay_base_url=base_url,
        manual_reissue_reason=reason,
        sample_ids=selected_ids,
    )

    report: dict[str, object] = {
        "pilot": "gonglu5_step2",
        "started_at_utc": _utc_now(),
        "mode": "live" if args.allow_live else "dry_run",
        "model": model,
        "relay_fingerprint_sha256": fingerprint_step2_relay(base_url),
        "network_call_budget": NETWORK_CALL_BUDGET,
        "manual_reissue_reason": reason,
        "sample_ids": list(selected_ids),
        "probes": {"step2a": None, "step2b": None},
        "samples": [],
        "gate_failures": [],
        "review_promotion": "disabled; all live Step 2A and Step 2B output remains draft",
        "accepted": False,
        "stop_reason": None,
    }

    def finish(exit_code: int) -> int:
        report["finished_at_utc"] = _utc_now()
        report["frozen_phase2r13_unchanged"] = freeze_phase2r13_inputs(repository_root=OLD_ROOT) == frozen_before
        calls = [
            stage_record
            for sample in report["samples"]
            for stage_record in (sample.get("step2a"), sample.get("step2b"))
            if stage_record is not None
        ]
        report["calibration"] = {
            "stage_calls": len(calls),
            "successful_stage_calls": sum(record["state"] == "success" for record in calls),
            "total_latency_ms": round(sum(record["latency_ms"] for record in calls), 2),
            "total_payload_utf8_bytes": sum(record["payload_utf8_bytes"] for record in calls),
            "total_response_utf8_bytes": sum(
                _json_bytes(record["response"]) for record in calls if record.get("response") is not None
            ),
        }
        _atomic_write_json(storage / REPORT_NAME, report)
        print(
            json.dumps(
                {
                    "report": str(storage / REPORT_NAME),
                    "mode": report["mode"],
                    "network_calls": report.get("network_calls", 0),
                    "accepted": report["accepted"],
                    "stop_reason": report["stop_reason"],
                    "gate_failures": len(report["gate_failures"]),
                },
                ensure_ascii=False,
            )
        )
        return exit_code

    if not args.allow_live:
        for context in contexts:
            report["samples"].append(
                {
                    "sample_id": context.sample_id,
                    "step2a_sizes": _stage_size_probe(context.step2a_request, "step2a"),
                    "step2b_sizes": _stage_size_probe(context.step2b_request, "step2b"),
                    "reviewed_fact_count": len(context.fixture_sample.facts),
                    "reviewed_card_count": len(context.fixture_sample.cards),
                    "learning_fact_gate": _learning_fact_gate(context),
                }
            )
        report.update({"network_calls": 0, "accepted": True, "stop_reason": "dry_run_complete"})
        return finish(0)

    # 生产配方走引擎可选参（T2 产品化）：补强/流式均为传输层选项，不进请求 identity；
    # 两者连同 reinforcement_id 入运行报告审计。
    transport = OpenAICompatibleStep2Transport(
        base_url=base_url,
        api_key=env["API_KEY"],
        timeout=600 if args.stream else 240,
        default_headers={"User-Agent": env["OPENAI_USER_AGENT"]} if env.get("OPENAI_USER_AGENT") else None,
        stream=args.stream,
        system_suffix=STEP2A_REINFORCEMENT_V1 if args.reinforce else None,
    )
    report["reinforced_system_prompt"] = args.reinforce
    report["streaming_transport"] = args.stream
    report["reinforcement_id"] = STEP2A_REINFORCEMENT_V1_ID if args.reinforce else None

    report["probe_attempts"] = {"step2a": [], "step2b": []}
    for stage in ("step2a", "step2b"):
        succeeded = False
        for _ in range(PROBE_ATTEMPTS):
            probe = _strict_probe(transport=transport, model=model, relay_base_url=base_url, stage=stage)
            report["probe_attempts"][stage].append(probe)
            report["probes"][stage] = probe
            if probe["outcome"] == "success":
                succeeded = True
                break
        if not succeeded:
            report.update({"network_calls": transport.network_call_count, "stop_reason": f"{stage}_schema_probe_failed"})
            return finish(1)

    sample_failures: list[dict[str, str]] = []
    for context in contexts:
        if transport.network_call_count + 2 > NETWORK_CALL_BUDGET:
            report.update({"network_calls": transport.network_call_count, "stop_reason": "network_call_budget_reached"})
            return finish(1)
        step2a = _execute_stage(storage_root=storage, transport=transport, request=context.step2a_request, stage="step2a")
        sample_report: dict[str, object] = {"sample_id": context.sample_id, "step2a": step2a, "step2b": None}
        report["samples"].append(sample_report)
        if step2a["state"] != "success":
            sample_failures.append({"sample_id": context.sample_id, "stage": "step2a", "state": step2a["state"]})
        else:
            # 门禁评估对模型行为 fail-safe：劣质模型可能编造不存在的锚点 ID（56terra 实测），
            # 评估器索引崩溃时登记为评估错误并继续，不熔断整场。
            response_a = V25Step2AResultPayload.model_validate(step2a["response"])
            gates: dict[str, object] = {}
            results: list[tuple[str, bool]] = []
            try:
                source_gate = assess_v25_step2a_response(request=context.step2a_request, response=response_a)
                gates["source_contract"] = source_gate.model_dump(mode="json")
                results.append(("source_contract", source_gate.valid))
            except Exception as error:
                gates["source_contract"] = {"evaluation_error": type(error).__name__, "detail": sanitize_error_text(str(error))}
                results.append(("source_contract_evaluation_error", False))
            try:
                teaching_gate = _teaching_gate(context, response_a)
                gates["teaching"] = teaching_gate.model_dump(mode="json")
                results.append(("teaching_publishable", teaching_gate.publishable))
            except Exception as error:
                gates["teaching"] = {"evaluation_error": type(error).__name__, "detail": sanitize_error_text(str(error))}
                results.append(("teaching_evaluation_error", False))
            learning_fact = _learning_fact_gate(context)
            gates["learning_fixture"] = learning_fact
            results.append(("learning_fixture", learning_fact["valid"]))
            try:
                learning_golden = assess_v25_response_learning_golden(context=context, response=response_a)
                gates["learning_golden"] = learning_golden
                results.append(("learning_golden", learning_golden["valid"]))
            except Exception as error:
                gates["learning_golden"] = {"evaluation_error": type(error).__name__, "detail": sanitize_error_text(str(error))}
                results.append(("learning_golden_evaluation_error", False))
            sample_report["step2a_gates"] = gates
            for gate_name, ok in results:
                if not ok:
                    report["gate_failures"].append({"sample_id": context.sample_id, "stage": "step2a", "gate": gate_name})

        step2b = _execute_stage(storage_root=storage, transport=transport, request=context.step2b_request, stage="step2b")
        sample_report["step2b"] = step2b
        if step2b["state"] != "success":
            sample_failures.append({"sample_id": context.sample_id, "stage": "step2b", "state": step2b["state"]})
            continue
        response_b = V25BStep2BResultPayload.model_validate(step2b["response"])
        grouping_gate = assess_v25b_step2b_response(request=context.step2b_request, response=response_b)
        card_golden = _card_golden_gate(context, response_b)
        compositions = {fact.fact_id for fact in context.step2b_request.facts if fact.fact_type.value == "composition"}
        composition_cards = [
            set(card.primary_fact_ids) for card in response_b.proposals if set(card.primary_fact_ids) & compositions
        ]
        atomic_separation = all(len(card & compositions) == 1 for card in composition_cards)
        sample_report["step2b_gates"] = {
            "source_grouping": grouping_gate.model_dump(mode="json"),
            "learning_card": card_golden,
            "complete_table_cards_separate_from_atomic_rules": atomic_separation,
        }
        for gate_name, ok in (
            ("source_grouping", grouping_gate.valid),
            ("learning_card", card_golden["valid"]),
            ("atomic_separation", atomic_separation),
        ):
            if not ok:
                report["gate_failures"].append({"sample_id": context.sample_id, "stage": "step2b", "gate": gate_name})

    report.update(
        {
            "network_calls": transport.network_call_count,
            "sample_failures": sample_failures,
            "accepted": transport.network_call_count <= NETWORK_CALL_BUDGET and not sample_failures,
            "stop_reason": "completed" if not sample_failures else "completed_with_failures",
        }
    )
    return finish(0 if report["accepted"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
