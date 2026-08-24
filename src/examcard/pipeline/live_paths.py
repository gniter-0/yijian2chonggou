"""exams/<exam_id>/ 目录契约解析（拆分层 0；runbook §1，registry_id 互锁）。

上游产物缺失一律抛 ``MissingUpstreamArtifact`` 而不是就地兜个空值：对新考试来说
「资产链未就绪」必须是显式信号——宁可拒开，不得发明（设计稿 R1）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# exams/ 目录契约解析（§3；漂移核查 N1 互锁纪律）
# ---------------------------------------------------------------------------


class MissingUpstreamArtifact(RuntimeError):
    """前置产物/资产缺席：消息必须指名缺哪件、该跑哪段（§9 missing_upstream_artifact）。"""


@dataclass(frozen=True)
class ExamPaths:
    """目录契约解析器：一切路径仓根相对派生；evidence/facts 一律从 registry_id 派生。"""

    repo_root: Path
    exam_id: str
    exam_root: Path
    input_pdf_dir: Path
    registry_path: Path
    annotation_sidecar_path: Path
    registry_id: str
    material_profile_id: str
    material_profile_path: Path
    content_policy_id: str
    content_policy_path: Path
    evidence_root: Path
    facts_root: Path
    live_runs_root: Path

    @classmethod
    def resolve(cls, repo_root: Path, exam_id: str) -> "ExamPaths":
        repo_root = Path(repo_root).resolve()
        exam_root = repo_root / "exams" / exam_id
        if not exam_root.is_dir():
            raise MissingUpstreamArtifact(
                f"exam workspace is missing: exams/{exam_id}/ — create it per runbook §1 (exams/ directory contract)"
            )
        registry_path = exam_root / "fixtures" / "golden_samples" / f"{exam_id}_phase0.yaml"
        if not registry_path.is_file():
            raise MissingUpstreamArtifact(
                f"golden registry is missing: {registry_path.relative_to(repo_root).as_posix()} — author it per runbook §3"
            )
        from ..evidence.golden import load_golden_registry
        from ..materials.catalog import load_material_profile

        registry = load_golden_registry(registry_path)
        # 互锁纪律（漂移核查 N1）：evidence/facts 路径一律 registry_id 派生，勿手写字面量。
        profile_path = repo_root / "configs" / "v2" / "material_profiles" / f"{registry.material_profile_id}.yaml"
        if not profile_path.is_file():
            raise MissingUpstreamArtifact(
                f"material profile is missing: {profile_path.relative_to(repo_root).as_posix()} — author it per runbook §2"
            )
        profile = load_material_profile(profile_path)
        if profile.profile_id != registry.material_profile_id:
            raise ValueError(
                "material profile interlock violated: registry.material_profile_id="
                f"{registry.material_profile_id!r} != profile.profile_id={profile.profile_id!r}"
            )
        annotation_path = exam_root / "fixtures" / "golden_annotations" / f"{registry.registry_id}.yaml"
        policy_path = repo_root / "configs" / "v2" / "content_policies" / f"{profile.content_policy_id}.yaml"
        if not policy_path.is_file():
            raise MissingUpstreamArtifact(
                f"content policy is missing: {policy_path.relative_to(repo_root).as_posix()} — reuse or author per runbook §2"
            )
        return cls(
            repo_root=repo_root,
            exam_id=exam_id,
            exam_root=exam_root,
            input_pdf_dir=exam_root / "input_pdf",
            registry_path=registry_path,
            annotation_sidecar_path=annotation_path,
            registry_id=registry.registry_id,
            material_profile_id=profile.profile_id,
            material_profile_path=profile_path,
            content_policy_id=profile.content_policy_id,
            content_policy_path=policy_path,
            evidence_root=exam_root / "data" / "evidence_v2" / "golden" / registry.registry_id,
            facts_root=exam_root / "data" / "facts_v2" / "golden" / registry.registry_id / profile.content_policy_id,
            live_runs_root=exam_root / "data" / "live_runs",
        )

    def missing_for_stage(self, stage: str, *, planned_stages: Sequence[str] = ()) -> list[str]:
        """`--stages` 子集缺前置产物时的指名清单（§9 missing_upstream_artifact）。

        本 run 计划内将由更早 stage 产出的产物不判缺（如 step1 与 step2a 同场时，
        evidence 产物按 satisfied-by-plan 处理）；人工签字物（注册表/标注侧车）
        没有产出者，恒检查。
        """

        missing: list[str] = []
        rel = lambda path: Path(path).relative_to(self.repo_root).as_posix()  # noqa: E731
        if stage == "step1":
            if not self.input_pdf_dir.is_dir() or not any(self.input_pdf_dir.iterdir()):
                missing.append(f"{rel(self.input_pdf_dir)} (source PDFs; runbook §1)")
        if stage in {"step2a", "step2b"}:
            if not self.evidence_root.is_dir() and "step1" not in planned_stages:
                missing.append(f"{rel(self.evidence_root)} (step1 evidence output; run --stages step1)")
            if not self.annotation_sidecar_path.is_file():
                missing.append(f"{rel(self.annotation_sidecar_path)} (golden annotation sidecar; runbook §3)")
        return missing
