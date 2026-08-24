"""step3 echo 阶段与双取材模式（拆分层 1；设计 §4）。

v2 单卡缺省 / v1 三卡存量两条取材路径，另加 release-bypass：三者都只从磁盘契约取材，
缺件即 ``MissingUpstreamArtifact``，不就地合成替代物。
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from ..common.hashing import canonical_hash
from ..common.io import atomic_write_json

from .live_budget import LiveRunBudget
from .live_paths import MissingUpstreamArtifact


# ---------------------------------------------------------------------------
# step3：echo（v2 单卡缺省 / v1 三卡存量）与 release-bypass 双取材模式（§4）
# ---------------------------------------------------------------------------


def load_typed_fixture_compilation(typed_fixture_path: Path) -> Any:
    """echo 模式取材：显式 reviewed typed-evidence fixture → 离线编译。

    「检查点①放行产物 → typed fixture」的自动转换器在新仓缺席（设计 §7.1 已知
    开口，T4b 首项验证点已核实并 need_input 上报）；在总指挥裁决补搬运之前，
    echo 模式仅接受显式给定的既有 fixture（如 r810），缺席即 fail-closed。
    """

    from ..compilers.step3_card import compile_reviewed_fixture
    from ..knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

    path = Path(typed_fixture_path)
    if not path.is_file():
        raise MissingUpstreamArtifact(
            f"step3 echo mode needs a reviewed typed-evidence fixture: {path} — pass --step3-typed-fixture "
            "(the admitted-step2→typed-fixture converter is absent from this repo; use --step3-mode release-bypass otherwise)"
        )
    fixture_bytes = path.read_bytes()
    import hashlib as _hashlib

    fixture = ReviewedTypedEvidenceFixture.model_validate_json(fixture_bytes.decode("utf-8"))
    return compile_reviewed_fixture(fixture, source_fixture_sha256=_hashlib.sha256(fixture_bytes).hexdigest())


def run_step3_stage(
    *,
    compilation: Any,
    contract_version: Literal["v1", "v2"],
    chat_strict: Callable[..., Mapping[str, Any]],
    storage: Path,
    budget: LiveRunBudget,
    base_reason: str,
    max_attempts: int = 2,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
    probe: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """step3 echo 逐卡循环（吸收 T3 独立驱动结构；N 卡通用，勿写死卡数）。

    ``chat_strict(messages=…, response_schema=…, schema_name=…, max_tokens=…)``
    由 CLI 注入（SSE 流式聚合最小信封字节返回 ``{"content": str, "http_status",
    "error_type", "latency_ms"}``）；离线测试注入确定性回显伪 transport。
    重发四不变式沿 T3 落地口径：attempt 进 identity seed、补强/流式不进。
    """

    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {"stage": "step3", "mode": "echo", "contract": contract_version, "cards": [], "probe": None}
    if probe is not None:
        budget.charge("step3", calls=1, usd_estimate=probe_price)
        probe_result = dict(probe())
        outcome["probe"] = probe_result
        if probe_result.get("outcome") != "success":
            outcome["status"] = "failed"
            outcome["stop_reason"] = "step3_probe_failed"
            return outcome

    if contract_version == "v2":
        from ..compilers.step3_response_v2 import (
            RESULT_SCHEMA_V2,
            build_single_card_model_input,
            build_single_card_prompt,
            make_single_card_inspector,
        )

        prompt = build_single_card_prompt()
        reinforcement = (
            "You must return draft_cards with exactly 1 item that echoes the supplied card verbatim from "
            "MODEL_INPUT_JSON. An empty draft_cards array is invalid. The top-level schema_version of your "
            "reply must be exactly the value required by the response json schema; do not copy the "
            "schema_version value from MODEL_INPUT_JSON."
        )
        pairs = list(zip(compilation.contracts, compilation.model_input.cards, strict=True))
        passed = 0
        for index, (contract, projected) in enumerate(pairs, start=1):
            model_input = build_single_card_model_input(contract, projected)
            inspector = make_single_card_inspector(model_input)
            user_content = (
                prompt.prompt_text
                + "\n\nMODEL_INPUT_JSON:\n"
                + json.dumps(model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            card_dir = storage / "cards" / f"{index:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
            atomic_write_json(
                card_dir / "model-input.json",
                {"model_input": model_input.model_dump(mode="json"), "user_content_utf8_bytes": len(user_content.encode("utf-8"))},
            )
            card_report: dict[str, Any] = {
                "index": index,
                "contract_id": contract.contract_id,
                "presentation": contract.presentation,
                "attempts": [],
                "outcome": "failed",
            }
            outcome["cards"].append(card_report)
            for attempt in range(1, max_attempts + 1):
                reissue = None if attempt == 1 else f"{base_reason}-r{attempt}"
                seed = {
                    "driver": "run_exam_pipeline.step3.v2",
                    "model_input_id": model_input.model_input_id,
                    "attempt": attempt,
                    "manual_reissue_reason": reissue,
                }
                request_id = f"step3-v2-live-request:{canonical_hash(seed)}"
                budget.charge("step3", calls=1, usd_estimate=price_per_call)
                result = dict(
                    chat_strict(
                        messages=[
                            {"role": "system", "content": reinforcement},
                            {"role": "user", "content": user_content},
                        ],
                        response_schema=RESULT_SCHEMA_V2,
                        schema_name="step3_v2_single_card_result",
                        max_tokens=6000,
                    )
                )
                content = str(result.get("content") or "")
                envelope = json.dumps(
                    {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
                    ensure_ascii=False,
                ).encode("utf-8")
                (card_dir / f"attempt-{attempt}.response.raw.json").parent.mkdir(parents=True, exist_ok=True)
                (card_dir / f"attempt-{attempt}.response.raw.json").write_bytes(envelope)
                gate = inspector(envelope) if content else None
                if gate is not None:
                    atomic_write_json(card_dir / f"attempt-{attempt}.gate.json", gate.model_dump(mode="json"))
                attempt_entry = {
                    "attempt": attempt,
                    "request_id": request_id,
                    "http_status": result.get("http_status"),
                    "latency_ms": result.get("latency_ms"),
                    "error_type": result.get("error_type"),
                    "gate_valid": bool(gate and gate.valid),
                    "gate_failures": list(gate.failure_codes) if gate is not None else ["inner-not-reached"],
                }
                card_report["attempts"].append(attempt_entry)
                if gate is not None and gate.valid:
                    card_report["outcome"] = "success"
                    passed += 1
                    break
        outcome["passed_cards"] = passed
        outcome["card_count"] = len(pairs)
        outcome["status"] = "completed" if passed == len(pairs) else "completed_with_failures"
        return outcome

    # v1 三卡存量重放通道（仅存量重放使用；两中继证伪史见 runbook §8）。
    from types import SimpleNamespace

    from ..compilers.step3_response import (
        ResponseDocument,
        build_prompt,
        build_response_model_input,
        make_inner_inspector,
    )

    representatives = []
    for presentation in ("table", "ordered_list", "formula"):
        pair = next(
            (
                (contract, projected)
                for contract, projected in zip(compilation.contracts, compilation.model_input.cards, strict=True)
                if contract.presentation == presentation
            ),
            None,
        )
        if pair is None:
            raise MissingUpstreamArtifact(f"v1 step3 contract needs a {presentation} card in the fixture")
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
    # 这三个名字在本函数上半段已经绑给单卡道（build_single_card_model_input /
    # build_single_card_prompt 那一族），mypy 认死首次绑定的类型，多卡道这三行本来合法的赋值
    # 各报一条 assignment，还连带把 make_inner_inspector 的实参类型也判错。按形状 A 的正解给
    # 第二次绑定改名，行为不变。
    response_model_input = build_response_model_input(representatives, source_fixture_sha256=compilation.source_fixture_sha256)
    response_inspector = make_inner_inspector(response_model_input)
    response_prompt = build_prompt()
    user_content = (
        response_prompt.prompt_text
        + "\n\nMODEL_INPUT_JSON:\n"
        + json.dumps(response_model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    budget.charge("step3", calls=1, usd_estimate=price_per_call)
    result = dict(
        chat_strict(
            messages=[{"role": "user", "content": user_content}],
            response_schema=ResponseDocument.model_json_schema(),
            schema_name="step3_result",
            max_tokens=8000,
        )
    )
    content = str(result.get("content") or "")
    envelope = json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}, ensure_ascii=False
    ).encode("utf-8")
    (storage / "v1-response.raw.json").write_bytes(envelope)
    response_gate = response_inspector(envelope) if content else None
    valid = bool(response_gate and response_gate.valid)
    outcome.update(
        {
            "card_count": 3,
            "passed_cards": 3 if valid else 0,
            "gate_failures": list(response_gate.failure_codes) if response_gate is not None else ["inner-not-reached"],
            "status": "completed" if valid else "completed_with_failures",
        }
    )
    return outcome


def load_step3_release_bypass(*, release_fixture_path: Path) -> dict[str, Any]:
    """release-bypass 取材校验（方案 v1.2 勘误道）：既有 reviewed 发布 fixture 直进 step4。"""

    import hashlib as _hashlib

    import yaml

    from ..knowledge.contracts.admission_fixtures import Step3UnifiedReviewedReleaseFixture

    path = Path(release_fixture_path)
    if not path.is_file():
        raise MissingUpstreamArtifact(f"release-bypass fixture is missing: {path}")
    fixture_bytes = path.read_bytes()
    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
    return {
        "mode": "release-bypass",
        "fixture_path": str(path),
        "fixture_sha256": _hashlib.sha256(fixture_bytes).hexdigest(),
        "card_count": len(fixture.cards),
    }
