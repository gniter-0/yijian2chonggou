"""总编排（拆分层 3；设计 §10.1）。

固定 DAG 排序 → 预检门禁 → 逐 stage 执行 → 检查点暂停/续跑 → 报告物化。本模块运行期引用
12 个兄弟，是家族里唯一的汇聚点：层序只许指向它，不许从它反向被引用。没被它碰到的只有
live_env——.env 取值由 CLI 侧读出后作为参数传进来，家族内部不自己去读运行配置。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .live_support import new_run_id
from .live_budget import LiveRunBudget
from .live_paths import ExamPaths
from .live_resend import ResendPlan
from .live_preflight import evaluate_preflight_gate
from .live_reports import LiveRunReport, LiveStageReport, build_live_run_report, build_stage_report
from .live_stage_step3 import load_step3_release_bypass, load_typed_fixture_compilation, run_step3_stage
from .live_stage_step4 import Step4ImageIntegrityError, prepare_step4_request_bindings, run_step4_stage
from .live_checkpoint import (
    build_admission_checkpoint,
    build_admission_checkpoint_v2,
    emit_echo_step3_review_checkpoint_v2,
    image_review_emission_identity,
    emit_image_review_sheet,
    ingest_admission_checkpoint_v2,
    ingest_admission_decisions,
    ingest_echo_step3_review_checkpoint_v2,
    ingest_image_review_sheet,
    validate_ingested_image_review,
)
from ..common.hashing import canonical_hash
from .live_config import LiveRunConfig, RunState, RunStateBindingError, build_run_resume_contract
from .live_stage_samples import execute_stage_samples
from .live_rehearsal import budget_rehearsal_estimate, derive_stage_call_caps, materialize_live_run


# ---------------------------------------------------------------------------
# 总编排（§10.1 orchestrate：DAG 排序 → 逐 stage → 检查点暂停/续跑 → 报告物化）
# ---------------------------------------------------------------------------

_STATUS_TO_DETAIL = {
    "completed": "completed",
    "completed_with_failures": "completed with sample-level failures (resend rounds exhausted)",
}


def orchestrate(config: LiveRunConfig, providers: Mapping[str, Any]) -> tuple[LiveRunReport, int]:
    """总编排。退出码语义（§2）：0=全部完成；2=正常暂停在人工检查点；1=失败。

    ``providers`` 注入面（CLI 装配；离线测试注入伪件）：
    sample_ids / pricing / preflight_report / context_builder / transport /
    stage_executor / step2_probe / gate_evaluator / step1_runner / facts_runner /
    admission_queue_builder / workbench_renderer / candidates_by_id /
    decision_builder / chat_strict / step3_probe / step3_card_count /
    prompt_builder / image_caller / models_probe / drift_classifier /
    step4_card_count / image_base_url / image_model。
    """

    paths = ExamPaths.resolve(config.repo_root, config.exam_id)
    run_id = config.resume_run_id or new_run_id(config.run_label)
    run_root = paths.live_runs_root / run_id
    if config.resume_run_id is not None:
        if not (run_root / "run_state.json").is_file():
            raise RunStateBindingError(
                "resume target is missing run_state.json; start a fresh run without --resume"
            )
    else:
        run_root.mkdir(parents=True, exist_ok=True)

    pricing: Mapping[str, float] = providers.get("pricing") or {}
    sample_ids: tuple[str, ...] = tuple(providers.get("sample_ids") or ())
    if config.only_samples:
        sample_ids = tuple(s for s in sample_ids if any(token in s for token in config.only_samples))
    step3_cards = int(providers.get("step3_card_count") or 0)
    step4_cards = int(providers.get("step4_card_count") or 0)
    same_run_admission = (
        config.mode == "live"
        and "step2b" in config.stages
        and "step3" in config.stages
        and config.step3_contract == "v3"
    )
    same_run_echo = same_run_admission and config.step3_mode == "echo" and "step4" in config.stages
    if same_run_echo:
        # The human review may release fewer cards, but the immutable run cap
        # must cover the full source-derived Step3 cardinality.  The release
        # itself remains the authority for the actual Step4 card count.
        step4_cards = step3_cards
    caps = derive_stage_call_caps(
        stages=config.stages,
        sample_count=max(len(sample_ids), 1),
        step3_card_count=max(step3_cards, 1),
        step4_card_count=max(step4_cards, 1),
        max_attempts=config.max_attempts,
    )
    image_base_url = str(providers.get("image_base_url", ""))
    image_model = str(providers.get("image_model", ""))
    step4_handoff = config.step4_handoff or _optional_path(providers.get("step4_handoff"))
    step4_release_fixture = _optional_path(providers.get("step4_release_fixture"))
    run_config = build_run_resume_contract(
        config,
        resolved_sample_ids=sample_ids,
        step3_card_count=step3_cards,
        step4_card_count=step4_cards,
        stage_call_caps=caps,
        image_base_url=image_base_url,
        image_model=image_model,
        step4_handoff=None if same_run_echo else step4_handoff,
        step4_release_fixture=None if same_run_echo else step4_release_fixture if config.step3_release_fixture is None else None,
        admission_contract="v2" if same_run_admission else None,
        step3_typed_fixture_origin="admission_checkpoint" if same_run_admission else None,
    )
    state = RunState.load_or_create(
        run_root / "run_state.json",
        run_id=run_id,
        exam_id=config.exam_id,
        stages=config.stages,
        run_config=run_config,
        run_config_sha256=canonical_hash(run_config),
    )
    stage_reports: list[LiveStageReport] = []
    extras: dict[str, Any] = {"run_root": str(run_root), "stages_requested": list(config.stages)}
    usd_cap = config.budget_usd if config.budget_usd is not None else 1.0
    budget = LiveRunBudget(run_root / "budget.json", usd_cap=usd_cap, stage_call_caps=caps)

    def same_run_v2_bundle_loader(*args: Any) -> tuple[Any, Any]:
        loader = providers.get("step4_bundle_loader_v2")
        if not callable(loader):
            raise ValueError("same-run Step4 requires the V2 echo bundle loader")
        loaded = loader(*args)
        if not isinstance(loaded, tuple) or len(loaded) != 2:
            raise ValueError("same-run Step4 V2 bundle loader returned an invalid result")
        from ..compilers.step4_echo_bridge_v2 import EchoStep4BundleV2, EchoStep4InputViewV2

        if not isinstance(loaded[0], EchoStep4BundleV2) or not isinstance(loaded[1], EchoStep4InputViewV2):
            raise ValueError("same-run Step4 bundle loader is not V2")
        return loaded

    def resolve_step4_source() -> tuple[Path, Path, Any | None]:
        if not same_run_echo:
            return (
                config.step3_release_fixture or Path(str(providers.get("step4_release_fixture", ""))),
                config.step4_handoff or Path(str(providers.get("step4_handoff", ""))),
                providers.get("step4_bundle_loader"),
            )
        ticket = state.checkpoint("echo_review")
        if ticket.get("status") != "ingested":
            raise ValueError("same-run Step4 review authority is not ingested")
        verified = ingest_echo_step3_review_checkpoint_v2(
            run_root=run_root,
            ticket=ticket,
            expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
            source_echo_run_id=run_id,
        )
        if verified.get("status") != "ingested":
            raise ValueError(str(verified.get("detail", "same-run Echo review revalidation failed")))
        release_path = Path(str(verified["release_path"]))
        handoff_path = Path(str(verified["handoff_path"]))
        # Execute the injected callable once at the boundary so a pilot/V1
        # loader can never be selected accidentally by Step4 internals.
        same_run_v2_bundle_loader(release_path, handoff_path)
        return release_path, handoff_path, same_run_v2_bundle_loader

    def current_step4_bindings() -> dict[int, dict[str, Any]]:
        release_path, handoff_path, bundle_loader = resolve_step4_source()
        return prepare_step4_request_bindings(
            release_fixture_path=release_path,
            handoff_path=handoff_path,
            prompt_builder=providers["prompt_builder"],
            image_model=image_model,
            bundle_loader=bundle_loader,
        )

    def finish(stop_reason: str, exit_code: int) -> tuple[LiveRunReport, int]:
        report = build_live_run_report(
            run_id=run_id,
            exam_id=config.exam_id,
            mode=config.mode,
            stages=stage_reports or [build_stage_report("step1", "pending", "no stage executed")],
            stop_reason=stop_reason,
            network_calls=budget.total_calls,
            usd_estimate=budget.spent_usd,
        )
        extras["budget"] = budget.snapshot()
        materialize_live_run(run_root=run_root, report=report, extras=extras)
        return report, exit_code

    # 前置产物检查（§9 missing_upstream_artifact：指名缺件与应跑 stage）。
    for stage in config.stages:
        missing = paths.missing_for_stage(stage, planned_stages=config.stages)
        if missing:
            stage_reports.append(build_stage_report(stage, "failed", f"missing upstream artifacts: {'; '.join(missing)}"))
            state.set_stage(stage, "failed", missing=missing)
            return finish("missing_upstream_artifact", 1)

    # dry-run 预算预演（§6-4）。
    if config.mode == "dry_run":
        extras["budget_rehearsal"] = budget_rehearsal_estimate(
            stages=config.stages,
            sample_count=len(sample_ids),
            step3_card_count=step3_cards,
            step4_card_count=step4_cards,
            pricing=pricing,
            max_attempts=config.max_attempts,
        )

    # 开跑预检（§8.2；live 且未显式跳过；预检自身计入账本）。
    if config.mode == "live" and not config.skip_preflight:
        preflight_report = providers.get("preflight_report")
        if preflight_report is None:
            return finish("preflight_step2_unmet" if "step2a" in config.stages else "preflight_step3_unmet", 1)
        extras["preflight"] = {
            "report_path": preflight_report.get("report_path"),
            "network_calls": preflight_report.get("network_calls", 0),
        }
        calls = int(preflight_report.get("network_calls", 0) or 0)
        if calls:
            budget.charge("preflight", calls=calls, usd_estimate=calls * pricing.get("preflight_call", 0.0))
        verdict = evaluate_preflight_gate(preflight_report, config.stages)
        extras["preflight_verdict"] = verdict
        if not verdict["allowed"]:
            return finish(str(verdict["stop_reason"]), 1)

    failed = False
    for stage in config.stages:
        if failed:
            stage_reports.append(build_stage_report(stage, "skipped", "skipped after upstream failure"))
            state.set_stage(stage, "skipped")
            continue
        if state.stage_status(stage) == "completed":
            if stage in {"step2a", "step2b"} and config.mode == "live":
                stage_state = state.payload.get("stage_states", {}).get(stage, {})
                if stage_state.get("semantic_gate_validated") is not True:
                    stage_reports.append(
                        build_stage_report(stage, "failed", "step2 semantic validation proof is missing; start a fresh run")
                    )
                    state.set_stage(stage, "failed", stop_reason="step2_semantic_validation_missing")
                    failed = True
                    continue
            stage_reports.append(build_stage_report(stage, "completed", "already completed in this run (resume skip)"))
            # ◆检查点② resume 对账（彩排收口发现的盲区）：step4 已 completed 时本分支直接
            # continue，而检查点②摄取位于 step4 分支内部（暂停时 stage 先记 completed 再退 2），
            # 故 resume 永远走不到摄取——检查点①因摄取位于后继 stage（step3）入口而幸免。
            # 就地对账，与 step4 分支内检查点块逐字同义（ingested/refused/仍缺三分支）。
            if stage == "step4" and config.mode == "live":
                if same_run_echo:
                    try:
                        resolve_step4_source()
                    except Exception as error:
                        state.set_checkpoint("echo_review", "refused", detail=str(error))
                        stage_reports.append(build_stage_report("echo_review_checkpoint", "failed", str(error)))
                        return finish("gate_refused_echo_review", 1)
                ticket = state.checkpoint("image_review")
                sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
                bindings = None
                if ticket.get("status") == "ingested" or sheet_path.is_file():
                    try:
                        bindings = current_step4_bindings()
                    except Exception as error:
                        state.set_checkpoint("image_review", "refused", detail=str(error))
                        stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(error)))
                        return finish("gate_refused_admission", 1)
                if ticket.get("status") == "ingested":
                    try:
                        validate_ingested_image_review(
                            run_root=run_root,
                            current_request_bindings=bindings or {},
                            expected_authority=ticket,
                        )
                    except (Step4ImageIntegrityError, KeyError, OSError, TypeError, ValueError) as error:
                        state.set_checkpoint("image_review", "refused", detail=str(error))
                        stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(error)))
                        return finish("gate_refused_admission", 1)
                else:
                    sheet_ingest = ingest_image_review_sheet(
                        run_root=run_root,
                        current_request_bindings=bindings,
                        expected_source_identity=ticket if ticket.get("status") == "waiting_human" else None,
                    )
                    if sheet_ingest["status"] == "ingested":
                        state.set_checkpoint(
                            "image_review",
                            "ingested",
                            **{key: value for key, value in sheet_ingest.items() if key != "status"},
                        )
                        stage_reports.append(build_stage_report("image_review_checkpoint", "completed", f"image review ingested; approved={sheet_ingest.get('approved_count', 0)}"))
                    elif sheet_ingest["status"] == "refused":
                        state.set_checkpoint("image_review", "refused", detail=str(sheet_ingest["detail"]))
                        stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(sheet_ingest["detail"])))
                        return finish("gate_refused_admission", 1)
                    else:
                        stage_reports.append(build_stage_report("image_review_checkpoint", "waiting_human", str(sheet_ingest.get("detail", "waiting for human dispositions"))))
                        return finish("waiting_human_image_review", 2)
            continue
        state.set_stage(stage, "running")

        if stage == "step1":
            runner = providers.get("step1_runner")
            if runner is None:
                stage_reports.append(build_stage_report(stage, "failed", "no step1 runner injected"))
                state.set_stage(stage, "failed")
                failed = True
                continue
            result = dict(runner())
            status = "completed" if result.get("ok") is True else "failed"
            stage_reports.append(build_stage_report(stage, status, str(result.get("detail", "golden evidence run"))))
            state.set_stage(stage, status)
            failed = failed or status == "failed"
            continue

        if stage in {"step2a", "step2b"}:
            if stage == "step2a":
                facts_runner = providers.get("facts_runner")
                if facts_runner is not None:
                    summary = dict(facts_runner())
                    extras["facts_base"] = {
                        "quality_gates_passed": bool(summary.get("quality_gates_passed")),
                        "machine_assertions_passed": bool(summary.get("machine_assertions_passed")),
                    }
                    if not (summary.get("quality_gates_passed") and summary.get("machine_assertions_passed")):
                        stage_reports.append(build_stage_report(stage, "failed", "facts base quality gates failed (step2a precondition)"))
                        state.set_stage(stage, "failed")
                        failed = True
                        continue
            if config.mode == "dry_run":
                stage_reports.append(
                    build_stage_report(stage, "completed", f"dry-run planned {len(sample_ids)} samples", samples_total=len(sample_ids))
                )
                state.set_stage(stage, "completed")
                continue
            resend = ResendPlan(base_reason=config.base_reason(stage), max_attempts=config.max_attempts)
            outcome = execute_stage_samples(
                stage=stage,  # type: ignore[arg-type]
                sample_ids=sample_ids,
                context_builder=providers["context_builder"],
                transport=providers.get("transport"),
                storage=run_root / stage,
                budget=budget,
                resend=resend,
                stage_executor=providers.get("stage_executor"),
                probe=providers.get("step2_probe"),
                gate_evaluator=providers.get("gate_evaluator"),
                price_per_call=pricing.get(f"{stage}_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
            )
            extras[stage] = outcome
            if outcome["status"] == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "stage failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            succeeded = sum(1 for item in outcome["samples"] if item["outcome"] == "success")
            stage_reports.append(
                build_stage_report(
                    stage,
                    outcome["status"],
                    _STATUS_TO_DETAIL[outcome["status"]],
                    samples_total=len(outcome["samples"]),
                    samples_succeeded=succeeded,
                    samples_failed=len(outcome["samples"]) - succeeded,
                )
            )
            if outcome["status"] == "completed" and config.mode == "live":
                semantic_validated = providers.get("gate_evaluator") is not None and all(
                    item.get("semantic_gate_valid") is True for item in outcome["samples"]
                )
                state.set_stage(
                    stage,
                    outcome["status"],
                    semantic_gate_validated=semantic_validated,
                    **(
                        {"semantic_gate_policy": "phase2r14-v25-semantic-gate-v1"}
                        if semantic_validated
                        else {}
                    ),
                )
            else:
                state.set_stage(stage, outcome["status"])
            if outcome["status"] == "completed_with_failures":
                failed = True  # 重发轮数耗尽仍失败 → 下游 skipped（§5.2）
                extras["stop_reason_hint"] = "completed_with_failures"
            # ◆检查点①：step2b 完成后产出物料并暂停（§7.1）。
            if stage == "step2b" and not failed and config.mode == "live":
                if same_run_admission:
                    try:
                        checkpoint_builder = providers.get("admission_checkpoint_builder_v2")
                        if callable(checkpoint_builder):
                            ticket_info = checkpoint_builder(run_root=run_root, outcome=outcome)
                        else:
                            projection_builder = providers.get("admission_projection_builder")
                            if not callable(projection_builder):
                                raise ValueError("V2 admission projection builder is not injected")
                            projection = projection_builder(run_root=run_root, outcome=outcome)
                            ticket_info = build_admission_checkpoint_v2(
                                run_root=run_root,
                                projection=projection,
                                page_artifacts=providers.get("admission_page_artifacts"),
                                workbench_renderer=providers.get("workbench_renderer"),
                                source_provenance=providers.get("admission_source_provenance"),
                                expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
                            )
                        if not isinstance(ticket_info, Mapping) or ticket_info.get("status") != "waiting_human":
                            raise ValueError("V2 admission checkpoint builder returned an invalid ticket")
                        state.set_checkpoint(
                            "admission",
                            "waiting_human",
                            **{key: value for key, value in ticket_info.items() if key != "status"},
                        )
                        stage_reports.append(
                            build_stage_report(
                                "admission_checkpoint",
                                "waiting_human",
                                "V2 materials emitted; waiting for explicit human authority",
                            )
                        )
                        return finish("waiting_human_admission", 2)
                    except Exception as error:
                        state.set_checkpoint("admission", "refused", detail=str(error))
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", str(error)))
                        return finish("gate_refused_admission", 1)
                ticket = state.checkpoint("admission")
                if ticket.get("status") != "ingested":
                    queue_builder = providers.get("admission_queue_builder")
                    if queue_builder is not None:
                        ticket_info = build_admission_checkpoint(
                            run_root=run_root,
                            queue_items=queue_builder(),
                            workbench_renderer=providers.get("workbench_renderer"),
                        )
                        state.set_checkpoint("admission", "waiting_human", **{k: v for k, v in ticket_info.items() if k != "status"})
                        stage_reports.append(build_stage_report("admission_checkpoint", "waiting_human", "materials emitted; waiting for human admission decisions"))
                        return finish("waiting_human_admission", 2)
            continue

        if stage == "step3":
            admission_fixture_path: Path | None = None
            # 检查点①门禁：同一 run 内跑过 step2b 才要求准入放行（bypass 语料自带 reviewed 身份）。
            if same_run_admission:
                ticket = state.checkpoint("admission")
                admission_source_validator = providers.get("admission_source_validator")
                if not callable(admission_source_validator):
                    detail = "V2 admission source validator is required for same-run resume"
                    state.set_checkpoint("admission", "refused", detail=detail)
                    stage_reports.append(build_stage_report("admission_checkpoint", "failed", detail))
                    return finish("gate_refused_admission", 1)
                if ticket.get("status") == "waiting_human":
                    ingest = ingest_admission_checkpoint_v2(
                        run_root=run_root,
                        expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
                        expected_queue_file_sha256=ticket.get("queue_file_sha256"),
                        source_validator=admission_source_validator,
                    )
                    if ingest["status"] == "waiting_human":
                        stage_reports.append(build_stage_report("admission_checkpoint", "waiting_human", str(ingest["detail"])))
                        return finish("waiting_human_admission", 2)
                    if ingest["status"] == "refused":
                        state.set_checkpoint("admission", "refused", detail=str(ingest.get("detail", "V2 admission refused")))
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", str(ingest.get("detail", "V2 admission refused"))))
                        return finish("gate_refused_admission", 1)
                    state.set_checkpoint(
                        "admission",
                        "ingested",
                        released_count=ingest.get("released_count", 0),
                        fixture_path=ingest.get("fixture_path"),
                        fixture_sha256=ingest.get("fixture_sha256"),
                        fixture_file_sha256=ingest.get("fixture_file_sha256"),
                        expected_step3_count=ingest.get("authority_identity", {}).get("expected_step3_count"),
                        authority_identity=ingest.get("authority_identity"),
                    )
                    stage_reports.append(
                        build_stage_report(
                            "admission_checkpoint",
                            "completed",
                            f"V2 admission ingested; released={ingest.get('released_count', 0)}",
                        )
                    )
                elif ticket.get("status") == "ingested":
                    expected_authority = ticket.get("authority_identity")
                    if not isinstance(expected_authority, Mapping):
                        state.set_checkpoint("admission", "tampered", detail="V2 authority identity is missing from the resume ticket")
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", "V2 authority identity is missing from the resume ticket"))
                        return finish("gate_refused_admission", 1)
                    ingest = ingest_admission_checkpoint_v2(
                        run_root=run_root,
                        verify_only=True,
                        expected_authority=expected_authority,
                        expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
                        expected_queue_file_sha256=ticket.get("queue_file_sha256"),
                        source_validator=admission_source_validator,
                    )
                    if ingest["status"] != "ingested":
                        state.set_checkpoint("admission", "tampered", detail=str(ingest.get("detail", "V2 admission verification failed")))
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", str(ingest.get("detail", "V2 admission verification failed"))))
                        return finish("gate_refused_admission", 1)
                    admission_fixture_path = Path(str(ingest["fixture_path"]))
                else:
                    state.set_checkpoint("admission", "refused", detail="V2 admission checkpoint is not authorized")
                    stage_reports.append(build_stage_report("admission_checkpoint", "failed", "V2 admission checkpoint is not authorized"))
                    return finish("gate_refused_admission", 1)
                if admission_fixture_path is None:
                    ticket = state.checkpoint("admission")
                    admission_fixture_path = Path(str(ticket.get("fixture_path", "")))
                if not admission_fixture_path.is_file():
                    state.set_checkpoint("admission", "tampered", detail="persisted V2 typed fixture is missing")
                    stage_reports.append(build_stage_report("admission_checkpoint", "failed", "persisted V2 typed fixture is missing"))
                    return finish("gate_refused_admission", 1)
            elif "step2b" in config.stages and config.mode == "live":
                ticket = state.checkpoint("admission")
                if ticket.get("status") == "waiting_human":
                    ingest = ingest_admission_decisions(
                        run_root=run_root,
                        candidates_by_id=providers.get("candidates_by_id") or {},
                        expected_queue_sha256=str(ticket.get("queue_sha256", "")),
                        decision_builder=providers.get("decision_builder") or (lambda raw, candidate: raw),
                    )
                    if ingest["status"] == "waiting_human":
                        stage_reports.append(build_stage_report("admission_checkpoint", "waiting_human", str(ingest["detail"])))
                        return finish("waiting_human_admission", 2)
                    if ingest["status"] == "refused":
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", str(ingest["detail"])))
                        state.set_checkpoint("admission", "refused")
                        return finish("gate_refused_admission", 1)
                    state.set_checkpoint("admission", "ingested", released_count=ingest.get("released_count", 0))
                    stage_reports.append(build_stage_report("admission_checkpoint", "completed", f"admission ingested; released={ingest.get('released_count', 0)}"))
            if config.step3_mode == "release-bypass":
                info = load_step3_release_bypass(release_fixture_path=config.step3_release_fixture)  # type: ignore[arg-type]
                extras["step3"] = info
                stage_reports.append(
                    build_stage_report(stage, "completed", f"release-bypass: {info['card_count']} reviewed release cards validated")
                )
                state.set_stage(stage, "completed")
                continue
            compilation = load_typed_fixture_compilation(
                admission_fixture_path or config.step3_typed_fixture, contract_version=config.step3_contract  # type: ignore[arg-type]
            )
            if config.mode == "dry_run":
                count = len(compilation.contracts)
                stage_reports.append(build_stage_report(stage, "completed", f"dry-run compiled {count} contracts (echo lane)", samples_total=count))
                state.set_stage(stage, "completed")
                continue
            outcome = run_step3_stage(
                compilation=compilation,
                contract_version=config.step3_contract,
                chat_strict=providers["chat_strict"],
                storage=run_root / "step3",
                budget=budget,
                base_reason=config.base_reason("step3"),
                max_attempts=config.max_attempts,
                price_per_call=pricing.get("step3_card_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
                probe=providers.get("step3_probe"),
            )
            extras["step3"] = {key: value for key, value in outcome.items() if key != "cards"}
            extras["step3_cards"] = outcome.get("cards", [])
            if outcome["status"] == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "step3 failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            stage_reports.append(
                build_stage_report(
                    stage,
                    outcome["status"],
                    f"echo {outcome.get('passed_cards', 0)}/{outcome.get('card_count', 0)} cards passed",
                    samples_total=int(outcome.get("card_count", 0)),
                    samples_succeeded=int(outcome.get("passed_cards", 0)),
                    samples_failed=int(outcome.get("card_count", 0)) - int(outcome.get("passed_cards", 0)),
                )
            )
            state.set_stage(stage, outcome["status"])
            if outcome["status"] == "completed_with_failures":
                failed = True
            elif same_run_echo:
                try:
                    admission_ticket = state.checkpoint("admission")
                    fixture_value = admission_ticket.get("fixture_path")
                    fixture_path = Path(fixture_value).resolve() if isinstance(fixture_value, str) else None
                    if fixture_path is None or not fixture_path.is_file():
                        raise ValueError("same-run Echo review requires the persisted Admission V2 fixture")
                    try:
                        fixture_path.relative_to(run_root.resolve())
                    except ValueError as error:
                        raise ValueError("same-run Echo review fixture is outside the current run") from error
                    ticket_info = emit_echo_step3_review_checkpoint_v2(
                        run_root=run_root,
                        typed_fixture_path=fixture_path,
                        expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
                        source_echo_run_id=run_id,
                    )
                    state.set_checkpoint(
                        "echo_review",
                        "waiting_human",
                        **{key: value for key, value in ticket_info.items() if key != "status"},
                    )
                    stage_reports.append(
                        build_stage_report(
                            "echo_review_checkpoint",
                            "waiting_human",
                            "V2 Step3 review sheet emitted; waiting for explicit human review",
                        )
                    )
                    return finish("waiting_human_echo_review", 2)
                except Exception as error:
                    state.set_checkpoint("echo_review", "refused", detail=str(error))
                    stage_reports.append(build_stage_report("echo_review_checkpoint", "failed", str(error)))
                    return finish("gate_refused_echo_review", 1)
            continue

        if stage == "step4":
            if same_run_echo:
                ticket = state.checkpoint("echo_review")
                if ticket.get("status") == "waiting_human":
                    ingest = ingest_echo_step3_review_checkpoint_v2(
                        run_root=run_root,
                        ticket=ticket,
                        expected_step3_count=int(run_config.get("step3_card_count", step3_cards)),
                        source_echo_run_id=run_id,
                    )
                    if ingest["status"] == "waiting_human":
                        stage_reports.append(
                            build_stage_report(
                                "echo_review_checkpoint",
                                "waiting_human",
                                str(ingest.get("detail", "waiting for V2 Step3 review")),
                            )
                        )
                        return finish("waiting_human_echo_review", 2)
                    if ingest["status"] == "refused":
                        detail = str(ingest.get("detail", "same-run Echo review was refused"))
                        state.set_checkpoint("echo_review", "refused", detail=detail)
                        stage_reports.append(build_stage_report("echo_review_checkpoint", "failed", detail))
                        return finish("gate_refused_echo_review", 1)
                    state.set_checkpoint(
                        "echo_review",
                        "ingested",
                        **{key: value for key, value in ingest.items() if key != "status"},
                    )
                    stage_reports.append(
                        build_stage_report(
                            "echo_review_checkpoint",
                            "completed",
                            str(ingest.get("detail", "same-run Echo review ingested")),
                        )
                    )
                elif ticket.get("status") != "ingested":
                    detail = "same-run Echo review checkpoint is not authorized"
                    state.set_checkpoint("echo_review", "refused", detail=detail)
                    stage_reports.append(build_stage_report("echo_review_checkpoint", "failed", detail))
                    return finish("gate_refused_echo_review", 1)
                try:
                    release_fixture_path, handoff_path, bundle_loader = resolve_step4_source()
                except Exception as error:
                    state.set_checkpoint("echo_review", "refused", detail=str(error))
                    stage_reports.append(build_stage_report("echo_review_checkpoint", "failed", str(error)))
                    return finish("gate_refused_echo_review", 1)
            else:
                release_fixture_path, handoff_path, bundle_loader = resolve_step4_source()
            outcome = run_step4_stage(
                release_fixture_path=release_fixture_path,
                handoff_path=handoff_path,
                prompt_builder=providers["prompt_builder"],
                image_caller=providers.get("image_caller") or (lambda payload: (0, b"")),
                models_probe=providers.get("models_probe") if config.mode == "live" else None,
                image_model=image_model,
                storage=run_root / "step4",
                budget=budget,
                drift_classifier=providers.get("drift_classifier"),
                price_per_call=pricing.get("step4_image_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
                dry_run=config.mode == "dry_run",
                max_attempts=config.max_attempts,
                bundle_loader=bundle_loader,
            )
            extras["step4"] = {key: value for key, value in outcome.items() if key != "cards"}
            extras["step4_cards"] = outcome.get("cards", [])
            if outcome.get("status") == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "step4 failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            stage_reports.append(
                build_stage_report(
                    stage,
                    str(outcome["status"]),
                    str(outcome.get("detail", f"images {outcome.get('succeeded_cards', 0)}/{len(outcome.get('cards', []))}")),
                    samples_total=len(outcome.get("cards", [])) or int(outcome.get("card_count", 0)),
                    samples_succeeded=int(outcome.get("succeeded_cards", 0)),
                )
            )
            state.set_stage(stage, str(outcome["status"]))
            if outcome["status"] == "completed_with_failures":
                failed = True
                continue
            # ◆检查点②：出图完成产出底稿并暂停（§7.2）。
            if config.mode == "live":
                ticket = state.checkpoint("image_review")
                if ticket.get("status") == "ingested":
                    try:
                        validate_ingested_image_review(
                            run_root=run_root,
                            current_request_bindings={
                                int(card["card_index"]): {
                                    "card_index": card["card_index"],
                                    "source_card_id": card["source_card_id"],
                                    "sample_id": card["sample_id"],
                                    "render_presentation": card["render_presentation"],
                                    "http_payload_sha256": card["http_payload_sha256"],
                                }
                                for card in outcome.get("cards", [])
                            },
                            expected_authority=ticket,
                        )
                    except (Step4ImageIntegrityError, KeyError, OSError, TypeError, ValueError) as error:
                        state.set_checkpoint("image_review", "refused", detail=str(error))
                        stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(error)))
                        return finish("gate_refused_admission", 1)
                    continue
                current_bindings = {
                    int(card["card_index"]): {
                        "card_index": card["card_index"],
                        "source_card_id": card["source_card_id"],
                        "sample_id": card["sample_id"],
                        "render_presentation": card["render_presentation"],
                        "http_payload_sha256": card["http_payload_sha256"],
                    }
                    for card in outcome.get("cards", [])
                    if card.get("outcome") == "success"
                }
                sheet_ingest = ingest_image_review_sheet(
                    run_root=run_root,
                    current_request_bindings=current_bindings,
                    expected_source_identity=ticket if ticket.get("status") == "waiting_human" else None,
                )
                if sheet_ingest["status"] == "ingested":
                    state.set_checkpoint(
                        "image_review",
                        "ingested",
                        **{key: value for key, value in sheet_ingest.items() if key != "status"},
                    )
                    stage_reports.append(build_stage_report("image_review_checkpoint", "completed", f"image review ingested; approved={sheet_ingest.get('approved_count', 0)}"))
                    continue
                if sheet_ingest["status"] == "refused":
                    state.set_checkpoint("image_review", "refused", detail=str(sheet_ingest["detail"]))
                    stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(sheet_ingest["detail"])))
                    return finish("gate_refused_admission", 1)
                try:
                    sheet_path = emit_image_review_sheet(run_root=run_root, step4_outcome=outcome, storage=run_root / "step4")
                except (Step4ImageIntegrityError, KeyError, OSError, TypeError, ValueError) as error:
                    state.set_checkpoint("image_review", "refused", detail=str(error))
                    stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(error)))
                    return finish("gate_refused_admission", 1)
                emission_identity = image_review_emission_identity(
                    run_root=run_root,
                    sheet_path=sheet_path,
                    current_request_bindings=current_bindings,
                )
                state.set_checkpoint(
                    "image_review",
                    "waiting_human",
                    **emission_identity,
                )
                stage_reports.append(build_stage_report("image_review_checkpoint", "waiting_human", "image review sheet emitted; waiting for human dispositions"))
                return finish("waiting_human_image_review", 2)
            continue

    if failed:
        hint = extras.get("stop_reason_hint", "completed_with_failures")
        return finish(str(hint), 1)
    return finish("completed", 0)


def _optional_path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value)
