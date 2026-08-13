"""通用全链 live 驱动 CLI 薄壳（T4b；设计 docs/design/live_pipeline_driver_design.md）。

薄壳只做四件事（设计 §1）：解析参数 → 装配 ``LiveRunConfig`` 与 providers 注入面
→ 调 ``examcard.pipeline.live_run.orchestrate()`` → 把报告映射为退出码与控制台
摘要。零业务逻辑；全部编排在库内（可离线测试）。

退出码（设计 §2）：0=请求 stages 全部完成；2=正常暂停在人工检查点（打印待办与
恢复命令，不是错误）；1=失败（预检不达标/预算耗尽/门禁失败/样本硬失败，§9）。

已知边界（T4b 落地口径，逐条见设计对应节）：
- step2a/2b 的样本上下文构建现阶段绑定 V25 reviewed 资产族（gonglu 黄金五章镜像
  彩排即此道）；任意新考试的 E1 全泛化依赖其 reviewed 学习 fixture/侧车齐备。
- step3 echo 模式需显式 ``--step3-typed-fixture``（「准入放行→typed fixture」
  转换器缺席已 need_input 上报，设计 §7.1 已知开口）；主彩排走
  ``--step3-mode release-bypass``。
- 质量门禁登记钩子（gate_evaluator）在库内已留位；pilot 驱动仍是质量测量主道。

运行示例（黄金五章伪新考试彩排，两段式）：
  uv run python migration/run_exam_pipeline.py --exam gonglu_golden5_rehearsal \
    --stages step1,step2a,step2b --allow-live --budget-usd 1.5 --run-label rehearsal-a \
    --step3-release-fixture scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml
  # →人工检查点①→
  uv run python migration/run_exam_pipeline.py --exam gonglu_golden5_rehearsal \
    --stages step3,step4 --allow-live --budget-usd 1.5 --run-label rehearsal-b \
    --step3-mode release-bypass --prompt-variant v2 \
    --step3-release-fixture scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml \
    --step4-handoff <r319 handoff json>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))
sys.path.insert(0, str(NEW_ROOT / "migration"))

from examcard.pipeline.live_run import (  # noqa: E402
    DEFAULT_PRICING_RELATIVE,
    ExamPaths,
    LiveRunConfig,
    load_live_pricing,
    load_run_env,
    orchestrate,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exam", required=True)
    parser.add_argument("--stages", required=True, help="comma separated subset of step1,step2a,step2b,step3,step4")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--run-label", type=str, default="")
    parser.add_argument("--only-samples", type=str, default=None)
    parser.add_argument("--channel", choices=("first", "last"), default="last")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--step3-contract", choices=("v1", "v2"), default="v2")
    parser.add_argument("--step3-mode", choices=("echo", "release-bypass"), default="echo")
    parser.add_argument("--step3-typed-fixture", type=Path, default=None)
    parser.add_argument("--step3-release-fixture", type=Path, default=None)
    parser.add_argument("--step4-handoff", type=Path, default=None)
    parser.add_argument(
        "--step4-source",
        choices=("pilot-release", "echo-batch"),
        default="pilot-release",
        help="step4 bundle source (P8 bridge): pilot-release = r2242/r319 lane byte-unchanged; "
        "echo-batch = echo-batch-release/1.0 + issued handoff via compilers.step4_echo_bridge",
    )
    parser.add_argument("--echo-release", type=Path, default=None, help="echo-batch release fixture json (--step4-source echo-batch)")
    parser.add_argument("--echo-handoff", type=Path, default=None, help="issued echo-batch handoff json (--step4-source echo-batch)")
    parser.add_argument("--prompt-variant", choices=("v1", "v2"), default="v2")
    parser.add_argument("--diagram-review-sidecar", type=Path, default=None)
    parser.add_argument("--diagram-disposition-sidecar", type=Path, default=None)
    parser.add_argument(
        "--seed-evidence-from",
        type=Path,
        default=None,
        help="seed step1 evidence from an existing identity-equivalent evidence root instead of a fresh no-vision "
        "rebuild (rehearsal lane: the reviewed conflict sidecar anchors vision-arbitrated evidence, which a "
        "no-vision rebuild cannot reproduce)",
    )
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--resume", type=str, default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")
    return args


def _resolve_channel(env_path: Path, channel: str) -> dict:
    import relay_preflight

    env_config = relay_preflight.parse_env_channels(env_path.read_text(encoding="utf-8", errors="replace"))
    resolved = relay_preflight.resolve_channel(env_config, channel=channel, base_url=None)
    resolved = dict(resolved)
    resolved["api_key"] = resolved.get("api_key") or env_config["globals"].get("API_KEY") or ""
    resolved["globals"] = env_config["globals"]
    return resolved


def _load_preflight(args: argparse.Namespace, env_path: Path, stages: tuple[str, ...]) -> dict | None:
    """开跑预检（§8.2）：文本通道全量 + step4 时图像通道 p4；--skip-preflight 复用当日报告。"""

    import relay_preflight

    label = relay_preflight.channel_label(_resolve_channel(env_path, args.channel)["base_url"])
    cached = NEW_ROOT / "data" / "relay_preflight" / label / "relay_preflight_report.json"
    if args.skip_preflight:
        # 双通道当日缓存复用（原执行人 WIP 重做，总指挥核准）：文本通道缓存不含图像端点
        # 结论（其 P4 对文本中继探询 IMAGE_MODEL 恒 fail），step4 在场时必须并入图像通道
        # 当日缓存的 step4 结论，否则 --skip-preflight + step4 恒被 preflight_step4_unmet 拒开。
        def _cached_today(path: Path, what: str) -> dict:
            if not path.is_file():
                raise SystemExit(f"--skip-preflight requires a cached {what} report: {path}")
            if datetime.fromtimestamp(path.stat().st_mtime, UTC).date() != datetime.now(UTC).date():
                raise SystemExit(f"--skip-preflight cached {what} report is stale (not today): {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        report = _cached_today(cached, f"text-channel ({label})")
        if "step4" in stages:
            env_values = relay_preflight.parse_env_channels(env_path.read_text(encoding="utf-8", errors="replace"))
            image_channel = env_values.get("image_channel")
            if image_channel is None:
                raise SystemExit("--skip-preflight with step4 requires explicit IMAGE_API_* keys in .env")
            image_label = relay_preflight.channel_label(image_channel["base_url"])
            image_cached = NEW_ROOT / "data" / "relay_preflight" / image_label / "relay_preflight_report.json"
            image_report = _cached_today(image_cached, f"image-channel ({image_label})")
            report["requirement_conclusions"]["step4"] = image_report["requirement_conclusions"]["step4"]
        # 缓存复用零新增外呼：清零 network_calls，避免把历史预检调用重复计入本 run 账本。
        report["network_calls"] = 0
        return report
    report = relay_preflight.run_preflight(channel=args.channel, env_file=env_path, skip={"p3"})
    if "step4" in stages:
        image_report = relay_preflight.run_preflight(
            channel="image", env_file=env_path, skip={"p1", "p2", "p3"}, with_image=False
        )
        report["requirement_conclusions"]["step4"] = image_report["requirement_conclusions"]["step4"]
        report["network_calls"] = int(report.get("network_calls", 0)) + int(image_report.get("network_calls", 0))
    return report


def main() -> int:
    args = _parse_args()
    config = LiveRunConfig(
        exam_id=args.exam,
        stages=tuple(token.strip() for token in args.stages.split(",") if token.strip()),
        mode="live" if args.allow_live else "dry_run",
        run_label=args.run_label or ("dry-run" if args.dry_run else ""),
        budget_usd=args.budget_usd,
        only_samples=tuple(token.strip() for token in args.only_samples.split(",")) if args.only_samples else None,
        channel=args.channel,
        env_file=args.env_file,
        step3_contract=args.step3_contract,
        step3_mode=args.step3_mode,
        step3_typed_fixture=args.step3_typed_fixture,
        step3_release_fixture=args.step3_release_fixture,
        step4_handoff=args.step4_handoff,
        prompt_variant=args.prompt_variant,
        skip_preflight=args.skip_preflight,
        resume_run_id=args.resume,
        max_attempts=args.max_attempts,
        repo_root=NEW_ROOT,
    )
    paths = ExamPaths.resolve(NEW_ROOT, config.exam_id)
    env_path = args.env_file if args.env_file else NEW_ROOT / ".env"
    env = load_run_env(env_path)
    pricing = load_live_pricing(NEW_ROOT / DEFAULT_PRICING_RELATIVE)

    from examcard.evidence.golden import load_golden_registry

    registry = load_golden_registry(paths.registry_path)
    sample_ids = tuple(sample.sample_id for sample in registry.samples)

    providers: dict[str, object] = {"pricing": pricing, "sample_ids": sample_ids}
    providers["image_model"] = env.get("IMAGE_MODEL") or ""

    # ---- step1 / 事实底座（纯离线闭包） -------------------------------------
    def step1_runner() -> dict:
        # 断点续跑（§5.1 产物即完成标记）：evidence 产物已在即跳过，不重算不覆盖。
        if paths.evidence_root.is_dir() and any(paths.evidence_root.iterdir()):
            return {"ok": True, "detail": "evidence products already present (resume skip)"}
        if args.seed_evidence_from is not None:
            import shutil

            seed = args.seed_evidence_from
            if not seed.is_dir():
                return {"ok": False, "detail": f"seed evidence root does not exist: {seed}"}
            paths.evidence_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(seed, paths.evidence_root)
            return {"ok": True, "detail": f"evidence seeded from identity-equivalent root: {seed}"}
        from examcard.evidence.golden_runner import run_golden_registry
        from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
        from examcard.materials.catalog import load_material_profile

        profile = load_material_profile(paths.material_profile_path)
        engine = EvidenceEngine(
            profile=profile,
            cache_root=paths.exam_root / "data" / "evidence_v2" / "cache",
            config=EvidenceEngineConfig(render_dpi=200),
            vision_verifier=None,
            cache_enabled=True,
        )
        wanted = set(config.only_samples or ()) or None
        selected = {s for s in sample_ids if wanted is None or any(token in s for token in wanted)} if wanted else None
        report = run_golden_registry(
            workspace_root=NEW_ROOT,
            registry=registry,
            profile=profile,
            engine=engine,
            output_root=paths.evidence_root,
            **({"sample_ids": selected} if selected else {}),
        )
        ok = bool(getattr(report, "all_samples_passed", True))
        return {"ok": ok, "detail": f"golden evidence run: {getattr(report, 'sample_count', len(sample_ids))} samples"}

    def facts_runner() -> dict:
        from examcard.knowledge.fact_engine import run_golden_fact_pipeline

        def _probe_sidecar(explicit: Path | None, family: str) -> Path | None:
            """按 registry 命名空间探测唯一侧车（runbook §5 命名约定）；显式参数优先。"""
            if explicit:
                return explicit
            candidates = sorted((NEW_ROOT / "configs" / "v2" / family).glob(f"{paths.registry_id}.*.yaml"))
            return candidates[0] if len(candidates) == 1 else None

        kwargs: dict[str, object] = {}
        review_sidecar = _probe_sidecar(args.diagram_review_sidecar, "diagram_review_sidecars")
        disposition_sidecar = _probe_sidecar(args.diagram_disposition_sidecar, "diagram_disposition_sidecars")
        if review_sidecar:
            kwargs["diagram_review_sidecar_path"] = review_sidecar
        if disposition_sidecar:
            kwargs["diagram_disposition_sidecar_path"] = disposition_sidecar
        return dict(
            run_golden_fact_pipeline(
                registry_path=paths.registry_path,
                sidecar_path=paths.annotation_sidecar_path,
                content_policy_path=paths.content_policy_path,
                evidence_root=paths.evidence_root,
                output_root=paths.facts_root,
                **kwargs,
            )
        )

    providers["step1_runner"] = step1_runner
    providers["facts_runner"] = facts_runner

    # ---- live 通道与 step2/step3/step4 注入面 --------------------------------
    if config.mode == "live":
        import httpx

        text_channel = _resolve_channel(env_path, args.channel)
        base_url = text_channel["base_url"]
        api_key = text_channel["api_key"]
        text_model = env.get("TEXT_MODEL") or ""
        user_agent = env.get("OPENAI_USER_AGENT")
        if not text_model:
            raise SystemExit(".env is missing TEXT_MODEL")
        providers["preflight_report"] = None if config.skip_preflight and False else _load_preflight(args, env_path, config.stages)

        from examcard.knowledge.llm import OpenAICompatibleStep2Transport
        from examcard.knowledge.reinforcement import STEP2A_REINFORCEMENT_V1, STEP2A_REINFORCEMENT_V1_ID

        transport = OpenAICompatibleStep2Transport(
            base_url=base_url,
            api_key=api_key,
            timeout=600.0,
            default_headers={"User-Agent": user_agent} if user_agent else None,
            stream=True,
            system_suffix=STEP2A_REINFORCEMENT_V1,
        )
        providers["transport"] = transport
        providers["reinforcement_id"] = STEP2A_REINFORCEMENT_V1_ID

        def context_builder(*, sample_ids: tuple[str, ...], reason: str):
            from examcard.knowledge.contracts.phase2r14_v25_live import build_live_sample_contexts

            return build_live_sample_contexts(
                repository_root=NEW_ROOT,
                model=text_model,
                relay_base_url=base_url,
                manual_reissue_reason=reason,
                sample_ids=sample_ids,
            )

        def step2_probe(stage: str) -> dict:
            from examcard.knowledge.contracts.phase2r14_v25_live import _strict_probe

            return dict(_strict_probe(transport=transport, model=text_model, relay_base_url=base_url, stage=stage))

        providers["context_builder"] = context_builder
        providers["step2_probe"] = step2_probe

        from openai import OpenAI

        oa = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=600,
            max_retries=0,
            default_headers={"User-Agent": user_agent} if user_agent else None,
        )

        def chat_strict(*, messages, response_schema, schema_name, max_tokens) -> dict:
            started = time.perf_counter()
            content = ""
            http_status: int | None = 200
            error_type: str | None = None
            try:
                stream = oa.chat.completions.create(
                    model=text_model,
                    messages=list(messages),
                    max_tokens=max_tokens,
                    temperature=0,
                    stream=True,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": schema_name, "strict": True, "schema": response_schema},
                    },
                )
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
            except Exception as error:  # noqa: BLE001
                error_type = type(error).__name__
                http_status = getattr(error, "status_code", None)
            return {
                "content": content,
                "http_status": http_status,
                "error_type": error_type,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }

        def step3_probe() -> dict:
            probe_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
            result = chat_strict(
                messages=[{"role": "user", "content": 'Return exactly this JSON object with no extra fields or prose: {"ok": true}'}],
                response_schema=probe_schema,
                schema_name="step3_probe",
                max_tokens=64,
            )
            ok = False
            try:
                ok = json.loads(result["content"]).get("ok") is True
            except (ValueError, TypeError):
                ok = False
            return {**result, "outcome": "success" if ok else "failed"}

        providers["chat_strict"] = chat_strict
        providers["step3_probe"] = step3_probe

        # step4 图像通道（显式 IMAGE_* 键优先；providers 五字段投影在库内）。
        image_base = (env.get("IMAGE_API_BASE_URL") or env.get("API_BASE_URL") or "").rstrip("/")
        image_key = env.get("IMAGE_API_KEY") or env.get("API_KEY") or ""
        image_model = str(providers["image_model"])
        headers = {"Authorization": f"Bearer {image_key}", "Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        image_client = httpx.Client(timeout=240.0, follow_redirects=False, headers=headers)

        def image_caller(payload) -> tuple[int, bytes]:
            response = image_client.post(f"{image_base}/images/generations", json=dict(payload))
            return response.status_code, response.content

        def models_probe() -> dict:
            response = image_client.get(f"{image_base}/models")
            advertised: list[str] = []
            try:
                advertised = [str(item["id"]) for item in response.json().get("data", ()) if isinstance(item, dict)]
            except ValueError:
                pass
            return {"http_status": response.status_code, "model_advertised": image_model in advertised}

        providers["image_caller"] = image_caller
        providers["models_probe"] = models_probe

    # ---- step3/step4 静态注入（prompt 构建器与卡数口径，dry-run 也需要） ------
    import run_pilot_gonglu5_step4 as step4_pilot

    if args.prompt_variant == "v2":
        providers["prompt_builder"] = step4_pilot.source_prompt_v2
    else:
        providers["prompt_builder"] = lambda card: step4_pilot.source_prompt(
            card.render_presentation, tuple(item.visible_text for item in card.visible_texts)
        )
    providers["drift_classifier"] = step4_pilot.classify_benign_envelope_drift

    if "step3" in config.stages and config.step3_mode == "echo" and config.step3_typed_fixture:
        from examcard.pipeline.live_run import load_typed_fixture_compilation

        providers["step3_card_count"] = len(load_typed_fixture_compilation(config.step3_typed_fixture).contracts)
    if config.step3_release_fixture and config.step3_release_fixture.is_file():
        import yaml

        release_payload = yaml.safe_load(config.step3_release_fixture.read_text(encoding="utf-8"))
        providers["step4_card_count"] = len(release_payload.get("cards", ())) or 16

    # ---- step4 装载接缝（P8 补链：echo 批新版本号道；pilot 路径零改动） ----------
    if args.step4_source == "echo-batch":
        if not (args.echo_release and args.echo_release.is_file()):
            raise SystemExit("--step4-source echo-batch requires an existing --echo-release fixture json")
        if not (args.echo_handoff and args.echo_handoff.is_file()):
            raise SystemExit("--step4-source echo-batch requires an existing --echo-handoff json (issued by the review ingest gate)")
        from examcard.compilers.step4_echo_bridge import load_step4_bundle_source

        providers["step4_bundle_loader"] = lambda fixture_path, handoff_path: load_step4_bundle_source(
            source="echo-batch", release_fixture_path=fixture_path, handoff_path=handoff_path
        )
        providers["step4_release_fixture"] = args.echo_release
        providers["step4_handoff"] = args.echo_handoff
        echo_release_payload = json.loads(args.echo_release.read_text(encoding="utf-8"))
        providers["step4_card_count"] = len(echo_release_payload.get("cards", ())) or 1

    report, exit_code = orchestrate(config, providers)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "mode": report.mode,
                "stages": {item.stage: item.status for item in report.stages},
                "stop_reason": report.stop_reason,
                "network_calls": report.network_calls,
                "usd_estimate": report.usd_estimate,
                "exit_code": exit_code,
            },
            ensure_ascii=False,
        )
    )
    if exit_code == 2:
        print("PAUSED at a human checkpoint. Fill the decision file, then resume with: --resume " + report.run_id)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
