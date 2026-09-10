"""step2a/step2b 样本执行（拆分层 2；设计 §5）。

cache-state 命中即跳过、传输/响应失败样本走 ResendPlan 重发；语义门禁失败则
fail-closed 且不重发。transport 一律注入，本模块不建真客户端。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Sequence

from ..common.hashing import sha256_bytes
from ..common.io import read_json
from ..providers.base import sanitized_error_summary

from .live_budget import LiveRunBudget
from .live_resend import ResendPlan


# ---------------------------------------------------------------------------
# step2a/step2b 样本执行（§5：cache-state 跳过 + 失败重发 + 门禁登记不熔断）
# ---------------------------------------------------------------------------


def _cache_document(storage: Path, cache_key: str) -> dict[str, Any] | None:
    cache_path = storage / "cache" / f"{cache_key.replace(':', '_')}.json"
    if not cache_path.is_file():
        return None
    try:
        payload = read_json(cache_path)
    except (OSError, UnicodeError, TypeError, ValueError):
        return {"state": "invalid_cache"}
    return payload if isinstance(payload, dict) else {"state": "invalid_cache"}


def _cache_state(storage: Path, cache_key: str) -> str | None:
    """Compatibility projection for callers that only need the cache state."""

    cache = _cache_document(storage, cache_key)
    return str(cache.get("state")) if cache is not None else None


def _load_cached_success_record(storage: Path, cache: Mapping[str, Any]) -> dict[str, Any]:
    request_id = cache.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("cached success record binding is missing")
    record_path = storage / "records" / f"{request_id.replace(':', '_')}.json"
    if not record_path.is_file():
        raise ValueError("cached success record is missing")
    raw = record_path.read_bytes()
    expected_hash = cache.get("record_sha256")
    if expected_hash is not None and expected_hash != sha256_bytes(raw):
        raise ValueError("cached success record binding does not match")
    record = read_json(record_path)
    if not isinstance(record, dict) or record.get("state") != "success":
        raise ValueError("cached success record is invalid")
    return record


def execute_stage_samples(
    *,
    stage: Literal["step2a", "step2b"],
    sample_ids: Sequence[str],
    context_builder: Callable[..., Sequence[Any]],
    transport: Any,
    storage: Path,
    budget: LiveRunBudget,
    resend: ResendPlan,
    stage_executor: Callable[..., dict[str, Any]] | None = None,
    probe: Callable[[str], dict[str, Any]] | None = None,
    gate_evaluator: Callable[[str, Any, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
) -> dict[str, Any]:
    """step2a/step2b 共用样本执行器。

    - 断点续跑：同 (样本, reason) 身份的 cache `state=success` 直接复用跳过；
      `failed/unknown_outcome` 落入重发道（ResendPlan 新 reason → 新身份），
      identity 拒绝复用语义由 `_execute_stage` 的三件存在性检查兜底（§5.1）。
    - 探针：stage 前置 strict 探针连败（重发 1 次的现行口径）→ 整 stage fail-closed，
      不进样本重发道；传输/响应失败进入样本重发道，重发轮耗尽记 failed。
    - 语义门禁：gate_evaluator 返回的 `valid` 必须严格为 True；失败或异常均
      fail-closed，且不会进入 ResendPlan 重发道。
    """

    # 兜底执行器只在调用方没传时按需导入。原先写成 `import ... as stage_executor` 直接覆盖
    # 形参，mypy 不把这种重绑当作收窄，调用点报「"None" not callable」，而且原地还压着一条
    # 抑制注释。改成另起一个不可空的局部名，两处一起干净。
    if stage_executor is None:
        from ..knowledge.contracts.phase2r14_v25_live import _execute_stage

        executor: Callable[..., dict[str, Any]] = _execute_stage
    else:
        executor = stage_executor

    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {"stage": stage, "probe": None, "samples": [], "status": "completed"}
    if probe is not None:
        probe_results = []
        for _ in range(2):  # 现行口径：探针失败重发一次
            budget.charge(stage, calls=1, usd_estimate=probe_price)
            result = probe(stage)
            probe_results.append(result)
            if result.get("outcome") == "success":
                break
        outcome["probe"] = probe_results
        if probe_results[-1].get("outcome") != "success":
            outcome["status"] = "failed"
            outcome["stop_reason"] = f"{stage}_probe_failed"
            return outcome

    failed_samples = 0
    for sample_id in sample_ids:
        entry: dict[str, Any] = {"sample_id": sample_id, "attempts": [], "outcome": "failed"}
        outcome["samples"].append(entry)
        while True:
            if not resend.can_issue(sample_id):
                break
            reason = resend.next_reason(sample_id)
            contexts = context_builder(sample_ids=(sample_id,), reason=reason)
            context = contexts[0]
            request = context.step2a_request if stage == "step2a" else context.step2b_request
            cache_key = request.identity.cache_key
            attempt_entry: dict[str, Any] = {"reason": reason, "request_id": request.identity.request_id}
            cache = _cache_document(storage, cache_key)
            cached = str(cache.get("state")) if cache is not None else None
            if cached == "invalid_cache" and gate_evaluator is not None:
                attempt_entry["state"] = "invalid_cache"
                attempt_entry["reused_cache"] = True
                entry["attempts"].append(attempt_entry)
                entry["gates"] = {"gate_error": "cached success cache is invalid"}
                entry["semantic_gate_valid"] = False
                entry["outcome"] = "failed"
                break
            if cached == "success":
                attempt_entry["state"] = "success"
                attempt_entry["reused_cache"] = True
                entry["attempts"].append(attempt_entry)
                if gate_evaluator is None:
                    entry["outcome"] = "success"
                else:
                    try:
                        record = _load_cached_success_record(storage, cache or {})
                        gates = dict(gate_evaluator(stage, context, record))
                        entry["gates"] = gates
                        entry["semantic_gate_valid"] = gates.get("valid") is True
                        entry["outcome"] = "success" if entry["semantic_gate_valid"] else "failed"
                    except Exception as error:  # semantic/cache validation must fail closed
                        entry["gates"] = {"gate_error": sanitized_error_summary(error)}
                        entry["semantic_gate_valid"] = False
                        entry["outcome"] = "failed"
                break
            if cached is not None:
                attempt_entry["state"] = cached
                attempt_entry["reused_cache"] = True
                entry["attempts"].append(attempt_entry)
                continue  # 已有失败身份：换下一轮新身份，不重放
            budget.charge(stage, calls=1, usd_estimate=price_per_call)
            record = executor(storage_root=storage, transport=transport, request=request, stage=stage)
            attempt_entry["state"] = record.get("state")
            attempt_entry["http_status"] = record.get("http_status")
            attempt_entry["latency_ms"] = record.get("latency_ms")
            entry["attempts"].append(attempt_entry)
            if record.get("state") == "success":
                if gate_evaluator is not None:
                    try:
                        gates = dict(gate_evaluator(stage, context, record))
                        entry["gates"] = gates
                        entry["semantic_gate_valid"] = gates.get("valid") is True
                        entry["outcome"] = "success" if entry["semantic_gate_valid"] else "failed"
                    except Exception as error:  # semantic gate errors fail closed
                        entry["gates"] = {"gate_error": sanitized_error_summary(error)}
                        entry["semantic_gate_valid"] = False
                        entry["outcome"] = "failed"
                else:
                    entry["outcome"] = "success"
                break
        if entry["outcome"] != "success":
            failed_samples += 1
    if failed_samples:
        outcome["status"] = "completed_with_failures"
        outcome["failed_samples"] = failed_samples
    return outcome
