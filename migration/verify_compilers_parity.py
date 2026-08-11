"""compilers/gates-release 双跑对照（一次性迁移验证工具，设计 §6.2 DoD）。

对 yijian2 只读（sys.dont_write_bytecode 防 __pycache__ 写入）；任何产出仅打印
stdout。逐目标比较新仓模块输出与旧仓已持久化 run 产物的 canonical JSON 字节。

用法：uv run python migration/verify_compilers_parity.py [--old-root PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

DEFAULT_OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")

RESULTS: list[tuple[str, bool, str]] = []


def record(target: str, ok: bool, detail: str) -> None:
    RESULTS.append((target, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {target}: {detail}")


def canonical_bytes(value) -> bytes:
    from examcard.knowledge.contracts.admission_legacy_hash import canonical_json_r10x

    return canonical_json_r10x(value)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(target: str, new_value, old_document) -> None:
    new_bytes = canonical_bytes(new_value)
    old_bytes = canonical_bytes(old_document)
    if new_bytes == old_bytes:
        record(target, True, f"byte-identical ({len(new_bytes)} bytes)")
    else:
        digest_new = hashlib.sha256(new_bytes).hexdigest()[:16]
        digest_old = hashlib.sha256(old_bytes).hexdigest()[:16]
        record(target, False, f"mismatch new={digest_new} old={digest_old} (len {len(new_bytes)} vs {len(old_bytes)})")


def verify_step3_card(old_root: Path):
    from examcard.compilers import step3_card as new_mod
    from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

    fixture_path = old_root / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1/reviewed-typed-evidence-fixture.json"
    run = old_root / "data/semantic_llm_v2/phase2r9_generic_step3_card_compiler_run1"
    fixture = ReviewedTypedEvidenceFixture.model_validate(load_json(fixture_path))
    compilation = new_mod.compile_reviewed_fixture(fixture, source_fixture_sha256=file_sha(fixture_path))
    old_bundle = load_json(run / "step3-card-contract-bundle.json")
    compare("step3_card.contracts", [item.model_dump(mode="json") for item in compilation.contracts], old_bundle["contracts"])
    compare("step3_card.model_input", compilation.model_input.model_dump(mode="json"), load_json(run / "step3-model-input.json"))
    compare("step3_card.prompt", compilation.prompt.model_dump(mode="json"), load_json(run / "step3-prompt-contract.json"))
    compare("step3_card.request", compilation.request.model_dump(mode="json"), load_json(run / "step3-offline-request.json"))
    compare("step3_card.draft", compilation.draft.model_dump(mode="json"), load_json(run / "step3-deterministic-draft.json"))
    compare("step3_card.batch", compilation.batch.model_dump(mode="json"), load_json(run / "step3-batch-manifest.json"))
    compare("step3_card.gate", compilation.gate.model_dump(mode="json"), load_json(run / "offline-gate.json"))
    compare("step3_card.regressions", [item.model_dump(mode="json") for item in compilation.regressions], load_json(run / "regression-matrix.json"))
    compare("step3_card.readiness", compilation.readiness.model_dump(mode="json"), load_json(run / "readiness.json"))
    return compilation, fixture_path


def verify_step3_response(old_root: Path):
    from examcard.compilers import step3_response as new_mod

    run = old_root / "data/semantic_llm_v2/phase2r92_generic_step3_response_contract_remediation_run2"
    old_input = load_json(run / "model-input.json")
    model_input = new_mod.ResponseModelInput.model_validate(old_input)
    fake = new_mod.build_fake_result(model_input)
    body = new_mod.encode_response(fake)
    old_response = load_json(run / "deterministic-fake-response.json")
    if json.loads(body.decode("utf-8")) == old_response:
        record("step3_response.fake_response", True, f"byte-equivalent envelope ({len(body)} bytes)")
    else:
        record("step3_response.fake_response", False, "envelope mismatch")
    compare("step3_response.fake_result", fake.model_dump(mode="json"), load_json(run / "deterministic-fake-result.json"))
    inspection = new_mod.inspect_response(model_input, body)
    compare("step3_response.fake_gate", inspection.gate.model_dump(mode="json"), load_json(run / "deterministic-fake-gate.json"))
    cases = new_mod.build_regression_cases(model_input)
    old_regression = load_json(run / "regression-matrix.json")
    mismatches = []
    for name, case_body in cases.items():
        entry = old_regression.get(name)
        if entry is None:
            mismatches.append(f"{name}: missing in old run")
            continue
        digest = hashlib.sha256(case_body).hexdigest()
        gate = new_mod.inspect_response(model_input, case_body).gate
        if digest != entry.get("response_sha256"):
            mismatches.append(f"{name}: body hash mismatch")
        elif gate.valid != entry.get("valid") or gate.gate_sha256 != entry.get("gate_sha256"):
            mismatches.append(f"{name}: gate mismatch")
    record(
        "step3_response.regressions",
        not mismatches,
        f"{len(cases)} cases replayed" if not mismatches else "; ".join(mismatches[:4]),
    )
    prompt = new_mod.build_prompt()
    compare("step3_response.prompt", prompt.model_dump(mode="json"), load_json(run / "prompt.json"))
    compare("step3_response.result_schema", new_mod.strict_schema(new_mod.ResponseDocument), load_json(run / "response-schema.json"))
    compare("step3_response.envelope_schema", new_mod.strict_schema(new_mod.ResponseEnvelope), load_json(run / "response-envelope-schema.json"))


def verify_step4_contract(old_root: Path):
    import yaml

    from examcard.compilers import step4_contract as new_mod
    from examcard.knowledge.contracts.admission_fixtures import (
        Step3UnifiedReviewedReleaseFixture,
        Step4PilotHandoffRefresh,
        UNIFIED_FIXTURE,
    )

    run = old_root / "data/semantic_llm_v2/phase2r4_step4_generic_contract_compiler_run1"
    fixture_path = old_root / Path(UNIFIED_FIXTURE)
    handoff_path = old_root / "data/semantic_llm_v2/phase2r319_step4_card4_reviewed_image_fixture_run1/step4-pilot-handoff-refresh.json"
    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_path.read_text(encoding="utf-8")))
    handoff = Step4PilotHandoffRefresh.model_validate(load_json(handoff_path))
    bundle, model_input, request, artifacts, batch = new_mod.derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=file_sha(fixture_path),
        handoff_sha256=file_sha(handoff_path),
    )
    compare("step4_contract.bundle", bundle.model_dump(mode="json"), load_json(run / "step4-generic-contract-bundle.json"))
    compare("step4_contract.model_input", model_input.model_dump(mode="json"), load_json(run / "step4-generic-model-input.json"))
    compare("step4_contract.request", request.model_dump(mode="json"), load_json(run / "step4-generic-request.json"))
    compare("step4_contract.fake_artifacts", artifacts.model_dump(mode="json"), load_json(run / "step4-generic-fake-artifact-manifest.json"))
    compare("step4_contract.batch", batch.model_dump(mode="json"), load_json(run / "step4-generic-batch-manifest.json"))
    gate = new_mod.assess_offline_gate(
        fixture=fixture,
        handoff=handoff,
        unified_fixture_sha256=file_sha(fixture_path),
        handoff_sha256=file_sha(handoff_path),
        bundle=bundle,
        model_input=model_input,
        request=request,
        artifacts=artifacts,
        batch=batch,
        freeze_valid=True,
    )
    compare("step4_contract.gate", gate.model_dump(mode="json"), load_json(run / "step4-generic-offline-gate.json"))
    regressions = new_mod.build_regression_matrix(
        fixture=fixture,
        handoff=handoff,
        unified_fixture_sha256=file_sha(fixture_path),
        handoff_sha256=file_sha(handoff_path),
        bundle=bundle,
        model_input=model_input,
        request=request,
        artifacts=artifacts,
        batch=batch,
    )
    compare("step4_contract.regressions", [item.model_dump(mode="json") for item in regressions], load_json(run / "step4-generic-regression-matrix.json"))


def verify_step4_image(old_root: Path):
    from examcard.compilers import step4_image as new_mod
    from examcard.knowledge.contracts.admission_fixtures import ReviewedStep3Fixture

    run = old_root / "data/semantic_llm_v2/phase2r11_generic_step4_source_derived_image_compiler_run1"
    fixture_path = old_root / "data/semantic_llm_v2/phase2r104_step3_explicit_production_authorization_run1/reviewed-step3-fixture.json"
    fixture = ReviewedStep3Fixture.model_validate(load_json(fixture_path))
    batch = new_mod.compile_image_batch(fixture.cards, fixture_sha256=file_sha(fixture_path))
    compare("step4_image.source_cards", [item.model_dump(mode="json") for item in batch.source_cards], load_json(run / "source-cards.json"))
    compare("step4_image.model_input", batch.model_input.model_dump(mode="json"), load_json(run / "model-input.json"))
    compare("step4_image.prompt", batch.prompt.model_dump(mode="json"), load_json(run / "prompt.json"))
    compare("step4_image.request", batch.request.model_dump(mode="json"), load_json(run / "request.json"))
    compare("step4_image.fake_response", batch.response.model_dump(mode="json"), load_json(run / "fake-response.json"))
    compare("step4_image.cache", batch.cache.model_dump(mode="json"), load_json(run / "cache.json"))
    compare("step4_image.journal", batch.journal.model_dump(mode="json"), load_json(run / "journal.json"))
    compare("step4_image.regressions", [item.model_dump(mode="json") for item in batch.regressions], load_json(run / "regression-matrix.json"))
    compare("step4_image.gate", batch.gate.model_dump(mode="json"), load_json(run / "offline-gate.json"))
    compare("step4_image.report", batch.report.model_dump(mode="json"), load_json(run / "report.json"))


def verify_closure(old_root: Path, compilation, fixture_path: Path):
    from examcard.gates.release import closure as new_mod

    run = old_root / "data/semantic_llm_v2/phase2r98_generic_step3_deterministic_release_closure_run1"
    label = fixture_path.resolve().relative_to(old_root.resolve()).as_posix()
    release = new_mod.build_deterministic_release(compilation, source_fixture_path_label=label)
    compare("closure.bundle", release.bundle.model_dump(mode="json"), load_json(run / "deterministic-release-bundle.json"))
    compare("closure.authority", release.authority.model_dump(mode="json"), load_json(run / "authority-policy.json"))
    compare("closure.gate", release.gate.model_dump(mode="json"), load_json(run / "offline-gate.json"))
    compare("closure.regressions", [item.model_dump(mode="json") for item in release.regressions], load_json(run / "regression-matrix.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", type=Path, default=DEFAULT_OLD_ROOT)
    args = parser.parse_args()
    old_root = args.old_root.resolve()
    if not old_root.is_dir():
        print(f"old root not found: {old_root}")
        return 2
    compilation = None
    fixture_path = None
    for name, runner in (
        ("step3_card", verify_step3_card),
        ("step3_response", verify_step3_response),
        ("step4_contract", verify_step4_contract),
        ("step4_image", verify_step4_image),
    ):
        try:
            result = runner(old_root)
            if name == "step3_card" and result is not None:
                compilation, fixture_path = result
        except Exception as exc:  # noqa: BLE001 - 汇总报告
            record(name, False, f"exception: {type(exc).__name__}: {exc}")
    if compilation is not None and fixture_path is not None:
        try:
            verify_closure(old_root, compilation, fixture_path)
        except Exception as exc:  # noqa: BLE001
            record("closure", False, f"exception: {type(exc).__name__}: {exc}")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n== parity summary: {passed}/{len(RESULTS)} targets byte-identical ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
