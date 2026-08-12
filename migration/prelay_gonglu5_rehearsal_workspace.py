"""T4b 前置：exams/gonglu_golden5_rehearsal/ 伪新考试工作区预铺（幂等，可复跑）。

依据：docs/design/live_pipeline_driver_design.md §3（exams/ 目录契约）+ §4（各 stage
输入面）+ §10.1（registry_id/material_profile_id 互锁纪律）+ §11（live 彩排取材双道）。

预铺物（「复制不挪动」——既有 reviewed 资产原位零改动，本脚本只读原件）：
- configs/v2/material_profiles/gonglu_golden5_rehearsal.yaml     接入点（唯一必须新增）
- exams/gonglu_golden5_rehearsal/input_pdf/                      黄金五章源 PDF ×5（gitignore 面，本脚本复制）
- exams/gonglu_golden5_rehearsal/data/{evidence_v2,facts_v2}/    运行产物骨架（gitignore 面）
- exams/gonglu_golden5_rehearsal/fixtures/golden_samples/gonglu_golden5_rehearsal_phase0.yaml
- exams/gonglu_golden5_rehearsal/fixtures/golden_annotations/gonglu_golden5_rehearsal_phase0_v1.yaml
- configs/v2/diagram_review_sidecars/gonglu_golden5_rehearsal_phase0_v1.phase25f_structural_reviewed.yaml
- configs/v2/diagram_disposition_sidecars/gonglu_golden5_rehearsal_phase0_v1.phase25f_remaining_audited.yaml
- data/diagram_review_v2/gonglu_golden5_rehearsal_phase0_v1/phase25f_remaining_highres_crop_manifest.json
 （data/ 面，gitignore；本脚本按确定性字节替换生成——异机复跑得到逐字节相同产物）

【身份纪律·为什么 profile_id 保留 yijian_gonglu_2026】EvidenceEngine.config_hash 把
{profile_id, revision} 编进证据身份（src/examcard/evidence/ingest.py），全部 reviewed
侧车（标注冲突锚、diagram 图锚、节点 evidence_ids）引用的 ev: ID 都以 config_hash 为前提。
换 profile_id 会使黄金五章全部人工评审资产失锚（等价于重做人工评审）。故「新考试」的身份
由 exams/ 工作区 + registry_id=gonglu_golden5_rehearsal_phase0_v1 承担；profile 文件按
目录契约以 <exam_id>.yaml 命名，内容逐字段取自 yijian_gonglu_2026（profile_id/revision 原值）。

哈希互锁链（生成顺序固定）：
  ① 评审侧车副本（换 sidecar_id/registry_id）→ sha256(①)
  ② 高清裁剪清单副本（换 registry 绑定 + source_sidecar_id/sha256=①）→ sha256(②)
  ③ 处置侧车副本（换 sidecar_id/registry_id + source=① + manifest=②）

验证（零网络）：profile/registry/annotations 装载 + 源 PDF 哈希 + run_golden_fact_pipeline
全链强校验（借既有 golden 证据 data/evidence_v2/golden/gonglu_phase0_v1——身份同源，
彩排时 step1 会在 exams/.../data/evidence_v2/golden/gonglu_golden5_rehearsal_phase0_v1/
重新生成同身份证据）+ r2242 16 发布卡装载 + r810 六卡 typed 语料编译。

运行（exam-card-engine 仓根）：
  uv run python migration/prelay_gonglu5_rehearsal_workspace.py
  uv run python migration/prelay_gonglu5_rehearsal_workspace.py --skip-fact-check   # 仅铺件+轻校验
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))

EXAM_ID = "gonglu_golden5_rehearsal"
REGISTRY_ID = f"{EXAM_ID}_phase0_v1"
EXAM_ROOT = NEW_ROOT / "exams" / EXAM_ID

SRC_PDF_DIR = NEW_ROOT / "input_pdf_公路"
SRC_PROFILE = NEW_ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml"
SRC_REGISTRY = NEW_ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
SRC_ANNOTATIONS = NEW_ROOT / "scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml"
SRC_DIAGRAM_REVIEW = NEW_ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml"
SRC_DIAGRAM_DISPOSITION = (
    NEW_ROOT / "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25f_remaining_audited.yaml"
)
SRC_CROP_MANIFEST = NEW_ROOT / "data/diagram_review_v2/gonglu_phase0_v1/phase25f_remaining_highres_crop_manifest.json"

DST_PROFILE = NEW_ROOT / f"configs/v2/material_profiles/{EXAM_ID}.yaml"
DST_PDF_DIR = EXAM_ROOT / "input_pdf"
DST_REGISTRY = EXAM_ROOT / f"fixtures/golden_samples/{EXAM_ID}_phase0.yaml"
DST_ANNOTATIONS = EXAM_ROOT / f"fixtures/golden_annotations/{REGISTRY_ID}.yaml"
DST_DIAGRAM_REVIEW = (
    NEW_ROOT / f"configs/v2/diagram_review_sidecars/{REGISTRY_ID}.phase25f_structural_reviewed.yaml"
)
DST_DIAGRAM_DISPOSITION = (
    NEW_ROOT / f"configs/v2/diagram_disposition_sidecars/{REGISTRY_ID}.phase25f_remaining_audited.yaml"
)
DST_CROP_MANIFEST = (
    NEW_ROOT / f"data/diagram_review_v2/{REGISTRY_ID}/phase25f_remaining_highres_crop_manifest.json"
)

POLICY = NEW_ROOT / "configs/v2/content_policies/core_only_v1.yaml"
VALIDATION_EVIDENCE_ROOT = NEW_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"
FACT_CHECK_OUT = NEW_ROOT / ".test-tmp" / "prelay_gonglu5_rehearsal" / "facts_v2"

R2242_FIXTURE_NEW = NEW_ROOT / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
R2242_FIXTURE_OLD = OLD_ROOT / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
R810_FIXTURE = OLD_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1/reviewed-typed-evidence-fixture.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def replace_exact(blob: bytes, old: bytes, new: bytes, *, count: int, label: str) -> bytes:
    found = blob.count(old)
    if found != count:
        raise SystemExit(f"[prelay] {label}: expected {count} occurrence(s) of {old!r}, found {found}")
    return blob.replace(old, new)


def write_bytes(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def utf8(text: str) -> bytes:
    return text.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-fact-check", action="store_true", help="skip the full fact-pipeline validation run")
    args = parser.parse_args()

    verdict: dict[str, object] = {"exam_id": EXAM_ID, "registry_id": REGISTRY_ID, "steps": []}
    steps: list[dict[str, object]] = verdict["steps"]  # type: ignore[assignment]

    # ── 1. PDF 复制（gitignore 面；哈希守恒） ────────────────────────────────
    import yaml

    registry_raw = yaml.safe_load(SRC_REGISTRY.read_text(encoding="utf-8"))
    copied = []
    for sample in registry_raw["samples"]:
        source = NEW_ROOT / sample["pdf_relative_path"]
        if not source.is_file():
            raise SystemExit(f"[prelay] source PDF missing: {source}")
        blob = source.read_bytes()
        if sha256_bytes(blob) != sample["pdf_sha256"]:
            raise SystemExit(f"[prelay] source PDF hash mismatch: {source}")
        target = DST_PDF_DIR / source.name
        if not (target.is_file() and sha256_bytes(target.read_bytes()) == sample["pdf_sha256"]):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        copied.append(source.name)
    steps.append({"step": "copy_pdfs", "count": len(copied)})

    # ── 2. 运行产物骨架（gitignore 面） ─────────────────────────────────────
    for skeleton in (EXAM_ROOT / "data/evidence_v2", EXAM_ROOT / "data/facts_v2"):
        skeleton.mkdir(parents=True, exist_ok=True)
    steps.append({"step": "data_skeleton", "paths": ["data/evidence_v2", "data/facts_v2"]})

    # ── 3. material profile 接入点（内容=yijian_gonglu_2026 逐字段，见 docstring 纪律） ──
    profile_body = SRC_PROFILE.read_text(encoding="utf-8")
    profile_header = (
        f"# {EXAM_ID} —— T4b 彩排「伪新考试」接入点（预铺件，migration/prelay_gonglu5_rehearsal_workspace.py 生成）\n"
        "# 来源：configs/v2/material_profiles/yijian_gonglu_2026.yaml 逐字段复制派生（20260813）。\n"
        "# 纪律：profile_id/revision 保留原值——EvidenceEngine.config_hash 把二者编进 ev: 证据身份，\n"
        "# 全部 reviewed 侧车锚点以此为前提；改名=黄金五章人工评审资产整体失锚（重做评审级代价）。\n"
        "# 「新考试」身份由 exams/gonglu_golden5_rehearsal/ 工作区 + registry_id 承担（设计稿 §10.1）。\n"
        f"# 详见 docs/prework/P3_T4b前置_伪新考试工作区预铺_20260813.md。\n"
    )
    write_bytes(DST_PROFILE, utf8(profile_header + profile_body))
    steps.append({"step": "material_profile", "path": str(DST_PROFILE.relative_to(NEW_ROOT))})

    # ── 4. 黄金注册表副本（registry_id + pdf 路径前缀；material_profile_id 保留） ──
    registry_blob = SRC_REGISTRY.read_bytes()
    registry_blob = replace_exact(
        registry_blob,
        b"registry_id: gonglu_phase0_v1",
        utf8(f"registry_id: {REGISTRY_ID}"),
        count=1,
        label="registry.registry_id",
    )
    registry_blob = replace_exact(
        registry_blob,
        utf8("pdf_relative_path: input_pdf_公路/"),
        utf8(f"pdf_relative_path: exams/{EXAM_ID}/input_pdf/"),
        count=5,
        label="registry.pdf_relative_path",
    )
    registry_header = (
        f"# {EXAM_ID} 黄金注册表（预铺件）：源=scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml，\n"
        f"# 仅改 registry_id 与 pdf_relative_path 前缀；样本/断言/评审元数据逐字节保留。\n"
    )
    write_bytes(DST_REGISTRY, utf8(registry_header) + registry_blob)
    steps.append({"step": "golden_registry", "path": str(DST_REGISTRY.relative_to(NEW_ROOT))})

    # ── 5. 人工标注侧车副本（sidecar_id/registry_id；正文逐字节保留） ────────────
    annotations_blob = SRC_ANNOTATIONS.read_bytes()
    annotations_blob = replace_exact(
        annotations_blob,
        b"sidecar_id: gonglu_phase0_v1.reviewed",
        utf8(f"sidecar_id: {REGISTRY_ID}.reviewed"),
        count=1,
        label="annotations.sidecar_id",
    )
    annotations_blob = replace_exact(
        annotations_blob,
        b"registry_id: gonglu_phase0_v1",
        utf8(f"registry_id: {REGISTRY_ID}"),
        count=1,
        label="annotations.registry_id",
    )
    annotations_header = (
        f"# {EXAM_ID} 人工标注侧车（预铺件）：源=scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml，\n"
        f"# 仅改 sidecar_id/registry_id；页级评审、冲突锚、config_hash 逐字节保留（profile 身份未变故依然成立）。\n"
    )
    write_bytes(DST_ANNOTATIONS, utf8(annotations_header) + annotations_blob)
    steps.append({"step": "golden_annotations", "path": str(DST_ANNOTATIONS.relative_to(NEW_ROOT))})

    # ── 6. diagram 评审侧车副本 → 哈希链① ──────────────────────────────────
    review_blob = SRC_DIAGRAM_REVIEW.read_bytes()
    review_blob = replace_exact(
        review_blob,
        b"sidecar_id: gonglu_phase0_v1.diagram_review_phase25f_structural_reviewed",
        utf8(f"sidecar_id: {REGISTRY_ID}.diagram_review_phase25f_structural_reviewed"),
        count=1,
        label="diagram_review.sidecar_id",
    )
    review_blob = replace_exact(
        review_blob,
        b"registry_id: gonglu_phase0_v1",
        utf8(f"registry_id: {REGISTRY_ID}"),
        count=1,
        label="diagram_review.registry_id",
    )
    review_header = (
        f"# {EXAM_ID} diagram 评审侧车（预铺件）：源=configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml，\n"
        f"# 仅改 sidecar_id/registry_id；条目（ev: 锚、crop 哈希、phase25e1 高清清单引用）逐字节保留。\n"
    )
    review_bytes = utf8(review_header) + review_blob
    write_bytes(DST_DIAGRAM_REVIEW, review_bytes)
    review_sha256 = sha256_bytes(review_bytes)
    steps.append(
        {"step": "diagram_review_sidecar", "path": str(DST_DIAGRAM_REVIEW.relative_to(NEW_ROOT)), "sha256": review_sha256}
    )

    # ── 7. 高清裁剪清单副本 → 哈希链②（data/ 面，确定性字节替换） ─────────────
    disposition_src = SRC_DIAGRAM_DISPOSITION.read_text(encoding="utf-8")
    old_review_sha = re.search(r"source_diagram_review_sidecar_sha256: ([0-9a-f]{64})", disposition_src)
    old_manifest_sha = re.search(r"manifest_sha256: ([0-9a-f]{64})", disposition_src)
    if old_review_sha is None or old_manifest_sha is None:
        raise SystemExit("[prelay] disposition source lost its hash fields; re-check upstream")
    manifest_blob = SRC_CROP_MANIFEST.read_bytes()
    manifest_blob = replace_exact(
        manifest_blob,
        b'"registry_id": "gonglu_phase0_v1"',
        utf8(f'"registry_id": "{REGISTRY_ID}"'),
        count=1,
        label="crop_manifest.registry_id",
    )
    manifest_blob = replace_exact(
        manifest_blob,
        b'"source_sidecar_id": "gonglu_phase0_v1.diagram_review_phase25f_structural_reviewed"',
        utf8(f'"source_sidecar_id": "{REGISTRY_ID}.diagram_review_phase25f_structural_reviewed"'),
        count=1,
        label="crop_manifest.source_sidecar_id",
    )
    manifest_blob = replace_exact(
        manifest_blob,
        utf8(f'"source_sidecar_sha256": "{old_review_sha.group(1)}"'),
        utf8(f'"source_sidecar_sha256": "{review_sha256}"'),
        count=1,
        label="crop_manifest.source_sidecar_sha256",
    )
    write_bytes(DST_CROP_MANIFEST, manifest_blob)
    manifest_sha256 = sha256_bytes(manifest_blob)
    steps.append(
        {"step": "crop_manifest", "path": str(DST_CROP_MANIFEST.relative_to(NEW_ROOT)), "sha256": manifest_sha256}
    )

    # ── 8. diagram 处置侧车副本 → 哈希链③ ─────────────────────────────────
    disposition_blob = SRC_DIAGRAM_DISPOSITION.read_bytes()
    disposition_blob = replace_exact(
        disposition_blob,
        b"sidecar_id: gonglu_phase0_v1.phase25f_remaining_visual_disposition",
        utf8(f"sidecar_id: {REGISTRY_ID}.phase25f_remaining_visual_disposition"),
        count=1,
        label="disposition.sidecar_id",
    )
    disposition_blob = replace_exact(
        disposition_blob,
        b"registry_id: gonglu_phase0_v1",
        utf8(f"registry_id: {REGISTRY_ID}"),
        count=1,
        label="disposition.registry_id",
    )
    disposition_blob = replace_exact(
        disposition_blob,
        b"source_diagram_review_sidecar_id: gonglu_phase0_v1.diagram_review_phase25f_structural_reviewed",
        utf8(f"source_diagram_review_sidecar_id: {REGISTRY_ID}.diagram_review_phase25f_structural_reviewed"),
        count=1,
        label="disposition.source_id",
    )
    disposition_blob = replace_exact(
        disposition_blob,
        utf8(f"source_diagram_review_sidecar_sha256: {old_review_sha.group(1)}"),
        utf8(f"source_diagram_review_sidecar_sha256: {review_sha256}"),
        count=1,
        label="disposition.source_sha256",
    )
    disposition_blob = replace_exact(
        disposition_blob,
        b"manifest_path: data/diagram_review_v2/gonglu_phase0_v1/phase25f_remaining_highres_crop_manifest.json",
        utf8(f"manifest_path: data/diagram_review_v2/{REGISTRY_ID}/phase25f_remaining_highres_crop_manifest.json"),
        count=1,
        label="disposition.manifest_path",
    )
    disposition_blob = replace_exact(
        disposition_blob,
        utf8(f"manifest_sha256: {old_manifest_sha.group(1)}"),
        utf8(f"manifest_sha256: {manifest_sha256}"),
        count=1,
        label="disposition.manifest_sha256",
    )
    disposition_header = (
        f"# {EXAM_ID} diagram 处置侧车（预铺件）：源=configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25f_remaining_audited.yaml，\n"
        f"# 改 sidecar_id/registry_id/source_*（指向本命名空间评审侧车副本）/review_crop_manifest（指向本命名空间清单副本）；\n"
        f"# 处置条目与高清 crop 引用逐字节保留（crop PNG 复用 gonglu_phase0_v1 原位资产，只读）。\n"
        f"# 注意：manifest 为 data/ 面（不入库），异机先跑 uv run python migration/prelay_gonglu5_rehearsal_workspace.py 重建。\n"
    )
    write_bytes(DST_DIAGRAM_DISPOSITION, utf8(disposition_header) + disposition_blob)
    steps.append(
        {
            "step": "diagram_disposition_sidecar",
            "path": str(DST_DIAGRAM_DISPOSITION.relative_to(NEW_ROOT)),
            "review_sha256": review_sha256,
            "manifest_sha256": manifest_sha256,
        }
    )

    # ── 9. 验证：装载 + 互锁 + 全链事实底座 + 彩排双道语料 ─────────────────────
    from examcard.evidence.annotations import load_golden_review_sidecar
    from examcard.evidence.golden import load_golden_registry, verify_golden_sources
    from examcard.materials.schema import MaterialProfile

    profile = MaterialProfile.model_validate(yaml.safe_load(DST_PROFILE.read_text(encoding="utf-8")))
    registry = load_golden_registry(DST_REGISTRY)
    if registry.registry_id != REGISTRY_ID:
        raise SystemExit("[prelay] adapted registry_id mismatch")
    if registry.material_profile_id != profile.profile_id:
        raise SystemExit("[prelay] registry.material_profile_id must equal profile.profile_id (§10.1)")
    if not registry.ready_for_phase1:
        raise SystemExit("[prelay] adapted registry lost reviewed status")
    source_errors = verify_golden_sources(registry, NEW_ROOT)
    if source_errors:
        raise SystemExit(f"[prelay] golden source verification failed: {source_errors}")
    annotations = load_golden_review_sidecar(DST_ANNOTATIONS)
    if annotations.registry_id != REGISTRY_ID or annotations.state.value != "reviewed":
        raise SystemExit("[prelay] adapted annotations sidecar binding broken")
    if annotations.material_profile_id != registry.material_profile_id:
        raise SystemExit("[prelay] annotations material profile mismatch")
    steps.append({"step": "verify_loaders", "profile_id": profile.profile_id, "samples": len(registry.samples)})

    if not args.skip_fact_check:
        from examcard.knowledge.fact_engine import run_golden_fact_pipeline

        FACT_CHECK_OUT.mkdir(parents=True, exist_ok=True)
        summary = run_golden_fact_pipeline(
            registry_path=DST_REGISTRY,
            sidecar_path=DST_ANNOTATIONS,
            content_policy_path=POLICY,
            evidence_root=VALIDATION_EVIDENCE_ROOT,
            output_root=FACT_CHECK_OUT,
            diagram_review_sidecar_path=DST_DIAGRAM_REVIEW,
            diagram_disposition_sidecar_path=DST_DIAGRAM_DISPOSITION,
        )
        gates = {
            "quality_gates_passed": summary.get("quality_gates_passed"),
            "machine_assertions_passed": summary.get("machine_assertions_passed"),
        }
        if gates["quality_gates_passed"] is not True or gates["machine_assertions_passed"] is not True:
            steps.append({"step": "fact_pipeline", "outcome": "failed", **gates})
            verdict["accepted"] = False
            print(json.dumps(verdict, ensure_ascii=False, indent=2))
            return 1
        steps.append(
            {
                "step": "fact_pipeline",
                "outcome": "passed",
                **gates,
                "registry_id": summary.get("registry_id"),
                "diagram_review_sidecar_id": summary.get("diagram_review_sidecar_id"),
                "output_root": str(FACT_CHECK_OUT.relative_to(NEW_ROOT)),
            }
        )

    # r2242 16 发布卡（release-bypass 道）
    from examcard.knowledge.contracts.admission_fixtures import (
        ReviewedTypedEvidenceFixture,
        Step3UnifiedReviewedReleaseFixture,
    )

    r2242_bytes = R2242_FIXTURE_NEW.read_bytes()
    r2242 = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(r2242_bytes.decode("utf-8")))
    r2242_cards = len(r2242.cards)
    r2242_parity_with_old = R2242_FIXTURE_OLD.is_file() and sha256_bytes(R2242_FIXTURE_OLD.read_bytes()) == sha256_bytes(r2242_bytes)
    steps.append(
        {
            "step": "r2242_release_fixture",
            "path": str(R2242_FIXTURE_NEW.relative_to(NEW_ROOT)),
            "cards": r2242_cards,
            "byte_parity_with_old_repo": r2242_parity_with_old,
        }
    )

    # r810 六卡 typed 语料（echo 演示道，老仓只读）
    from examcard.compilers.step3_card import compile_reviewed_fixture

    r810_bytes = R810_FIXTURE.read_bytes()
    r810 = ReviewedTypedEvidenceFixture.model_validate_json(r810_bytes.decode("utf-8"))
    compilation = compile_reviewed_fixture(r810, source_fixture_sha256=sha256_bytes(r810_bytes))
    steps.append(
        {
            "step": "r810_typed_fixture",
            "path": str(R810_FIXTURE),
            "contracts": len(compilation.contracts),
            "offline_gate_valid": compilation.gate.valid,
        }
    )
    if len(compilation.contracts) != 6 or not compilation.gate.valid:
        verdict["accepted"] = False
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 1
    if r2242_cards != 16:
        verdict["accepted"] = False
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 1

    verdict["accepted"] = True
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
