"""shadow 执线接线：v1_loader/v2_runner 真实实现 + 视觉通道 + 调用预算熔断 + 断点续跑。

- v1 侧：只读 yijian2 的 structured_<科目> 章 JSON（`NN 章名.json` 前缀索引），
  指标沿 `shadow.v1_chapter_metrics` 老口径；
- v2 侧：examcard.evidence 引擎（native+OCR+fusion，全程本地），vision 复核通道注入式——
  离线/dry-run 用 GoldenFixtureVisionVerifier（零外呼），实跑用 OpenAIVisionVerifier
  （端点/密钥自 yijian2\\.env 只读加载，密钥只进内存决不落盘/入日志）；
- 预算熔断：VisionCallBudget 持久化计数（common.io 原子写），到达硬上限（默认 400 次，
  依 65 章估算 ≈286 次 + 余量）当场抛 VisionBudgetExceeded，fail-closed 不降级；
- 断点续跑：章目录 chapter-report.json 为完成标记，重跑自动跳过已完成章；
  预算计数随文件恢复，跨进程续跑不重置。
产物目录：<output_root>/<章前缀>/（evidence/、chapter-report.json）+ 根部 vision-budget.json。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping

from ..common.io import atomic_write_json, read_json
from ..evidence.engine_types import VisualReviewHint
from ..evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from ..evidence.vision import GoldenFixtureVisionVerifier, OpenAIVisionVerifier
from ..materials.schema import MaterialProfile
from .shadow import classify_text_conflict, v1_chapter_metrics

DEFAULT_VISION_HARD_CAP = 400
_CHAPTER_FILE_PREFIX = re.compile(r"^(\d{2})\b")

V1_METRIC_KEYS = ("knowledge_point_count", "content_kp_count", "exercise_kp_count")

# 两遍式产物名（A 案，裁决 bc-658b8def）：
# - CENSUS_REPORT_NAME 是第一遍（零 API 普查）的完成标记与 hints 确定性输入；
# - chapter-report.json 是第二遍（vision 复核）的完成标记。
CENSUS_REPORT_NAME = "census.json"


class VisionBudgetExceeded(RuntimeError):
    """视觉调用到达硬上限：整跑熔断，决不静默降级继续。"""


class VisionCallBudget:
    """持久化调用计数器（原子写盘，跨进程断点续跑不重置）。"""

    def __init__(self, state_path: Path, hard_cap: int = DEFAULT_VISION_HARD_CAP) -> None:
        if hard_cap < 1:
            raise ValueError("vision hard cap must be positive")
        self.state_path = Path(state_path)
        self.hard_cap = hard_cap
        self.calls = 0
        if self.state_path.is_file():
            state = read_json(self.state_path)
            if int(state.get("hard_cap", hard_cap)) != hard_cap:
                raise ValueError("resumed vision budget hard cap mismatch")
            self.calls = int(state["calls"])
        else:
            self._persist()

    def _persist(self) -> None:
        atomic_write_json(self.state_path, {"calls": self.calls, "hard_cap": self.hard_cap})

    @property
    def remaining(self) -> int:
        return self.hard_cap - self.calls

    def charge(self, amount: int = 1) -> None:
        if amount < 1:
            raise ValueError("charge amount must be positive")
        if self.calls + amount > self.hard_cap:
            raise VisionBudgetExceeded(
                f"vision call budget exhausted: {self.calls}+{amount} would exceed hard cap {self.hard_cap}"
            )
        self.calls += amount
        self._persist()


class BudgetedVisionVerifier:
    """包住任意 verifier：每次 verify 前先扣预算；失败计数；决不吞异常。"""

    def __init__(self, inner: Any, budget: VisionCallBudget) -> None:
        self._inner = inner
        self._budget = budget
        self.name = f"budgeted:{inner.name}"
        self.version = inner.version
        self.calls = 0
        self.failures = 0

    def set_review_context(self, **kwargs: Any) -> None:
        setter = getattr(self._inner, "set_review_context", None)
        if callable(setter):
            setter(**kwargs)

    def verify(self, png_bytes: bytes) -> Any:
        self._budget.charge(1)
        self.calls += 1
        try:
            return self._inner.verify(png_bytes)
        except Exception:
            self.failures += 1
            raise


def load_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（只读；值仅驻内存，调用方不得记录）。"""
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def make_mock_vision_verifier(visible_text: str = "dry-run 复核占位文本") -> GoldenFixtureVisionVerifier:
    """零外呼 mock 通道（dry-run 唯一允许的通道）。"""
    return GoldenFixtureVisionVerifier(visible_text=visible_text)


def make_live_vision_verifier(env_path: Path, *, model_key: str = "TEXT_MODEL") -> OpenAIVisionVerifier:
    """真实通道：端点/密钥/模型自 .env 只读；缺键即拒（fail-closed，不猜默认端点）。"""
    values = load_env_file(env_path)
    missing = [key for key in ("API_BASE_URL", "API_KEY", model_key) if not values.get(key)]
    if missing:
        raise ValueError(f".env is missing required keys for live vision channel: {missing}")
    return OpenAIVisionVerifier(base_url=values["API_BASE_URL"], api_key=values["API_KEY"], model=values[model_key])


def _index_by_prefix(directory: Path, suffix: str) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted(directory.glob(f"*{suffix}")):
        match = _CHAPTER_FILE_PREFIX.match(path.name)
        if match:
            index.setdefault(match.group(1), path)
    return index


def make_v1_structured_loader(structured_dir: Path, repository_root: Path) -> Callable[[str], dict[str, Any]]:
    """v1_loader：structured_<科目> 只读 → 老口径章指标（仅可比键，供 diff）。"""
    index = _index_by_prefix(Path(structured_dir), ".json")
    if not index:
        raise ValueError(f"no structured chapter files under {structured_dir}")

    def loader(prefix: str) -> dict[str, Any]:
        path = index.get(prefix)
        if path is None:
            raise KeyError(f"structured chapter not found for prefix {prefix}")
        metrics = v1_chapter_metrics(path, repository_root)
        return {key: metrics[key] for key in V1_METRIC_KEYS}

    return loader


def make_gonglu_profile() -> MaterialProfile:
    """公路 shadow 用最小画像（字段口径对齐 configs/v2 模板）。"""
    return MaterialProfile.model_validate(
        {
            "profile_id": "gonglu_shadow_v2",
            "revision": 1,
            "display_name": "公路工程管理与实务（shadow 对拍）",
            "collection_id": "yijian/2026/gonglu",
            "exam_family": "一级建造师",
            "subject_id": "gonglu",
            "subject_name": "公路工程管理与实务",
            "source_kind": "textbook",
            "edition": "2026",
            "jurisdiction": "CN",
            "content_policy_id": "core_only_v1",
            "content_policy_revision": 1,
            "layout_profile_id": "default_v1",
            "taxonomy_profile_id": "default_v1",
            "validation_profile_id": "default_v1",
            "card_profile_id": "default_v1",
        }
    )


def _default_engine_factory(
    *,
    profile: MaterialProfile,
    cache_root: Path,
    vision_verifier: Any | None,
) -> EvidenceEngine:
    return EvidenceEngine(
        profile=profile,
        cache_root=cache_root,
        config=EvidenceEngineConfig(),
        vision_verifier=vision_verifier,
    )


def make_v2_evidence_runner(
    pdf_dir: Path,
    output_root: Path,
    *,
    vision_verifier: Any | None,
    budget: VisionCallBudget,
    profile: MaterialProfile | None = None,
    engine_factory: Callable[..., Any] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """v2_runner：evidence 引擎逐章运行，产物入 <output_root>/<章>/，含断点续跑。

    - 完成标记 = 章目录 chapter-report.json；重跑读取标记直接返回既有指标，零重算；
    - vision 通道包 BudgetedVisionVerifier（预算熔断先于任何外呼）；
    - engine_factory 注入点供测试替换（默认真实 EvidenceEngine）。
    """
    pdf_index = _index_by_prefix(Path(pdf_dir), ".pdf")
    if not pdf_index:
        raise ValueError(f"no chapter PDFs under {pdf_dir}")
    bound_profile = profile or make_gonglu_profile()
    factory = engine_factory or _default_engine_factory

    def runner(prefix: str) -> dict[str, Any]:
        chapter_dir = Path(output_root) / prefix
        report_path = chapter_dir / "chapter-report.json"
        if report_path.is_file():
            return dict(read_json(report_path)["metrics"])
        pdf_path = pdf_index.get(prefix)
        if pdf_path is None:
            raise KeyError(f"chapter PDF not found for prefix {prefix}")
        verifier = BudgetedVisionVerifier(vision_verifier, budget) if vision_verifier is not None else None
        engine = factory(profile=bound_profile, cache_root=chapter_dir / "cache", vision_verifier=verifier)
        manifest, report = engine.ingest_document(
            pdf_path,
            source_relative_path=pdf_path.name,
            output_dir=chapter_dir / "evidence",
        )
        conflict_count = sum(len(getattr(page, "source_conflicts", ()) or ()) for page in manifest.pages)
        metrics = {
            "page_count": len(manifest.pages),
            "evidence_unit_count": sum(len(page.evidence_units) for page in manifest.pages),
            "source_conflict_count": conflict_count,
            "vision_calls_used": verifier.calls if verifier is not None else 0,
        }
        atomic_write_json(
            report_path,
            {
                "chapter_prefix": prefix,
                "pdf": pdf_path.name,
                "document_sha256": manifest.source.sha256,
                "manifest_id": manifest.manifest_id,
                "metrics": metrics,
                "vision_budget_after": {"calls": budget.calls, "hard_cap": budget.hard_cap},
                "quality_report_keys": sorted(report)[:20],
            },
        )
        return metrics

    return runner


def _conflict_records(manifest: Any) -> list[dict[str, Any]]:
    """从 manifest 逐页抽取 source_conflict → 分诊严重度（shadow.classify_text_conflict）。

    分诊只对同时有 native/visual 文本的冲突判级；缺 native 文本者无法定位复核区域，
    记 severity=unlocatable 并排除出高危 hint（第二遍无从画裁片）。
    """
    records: list[dict[str, Any]] = []
    for page in getattr(manifest, "pages", ()) or ():
        for conflict in getattr(page, "source_conflicts", ()) or ():
            native = getattr(conflict, "native_text", None) or ""
            visual = getattr(conflict, "visual_text", None) or ""
            page_num = conflict.locator.page_num
            if not native:
                severity = "unlocatable"
            else:
                severity = classify_text_conflict(native, visual)
            records.append(
                {
                    "conflict_id": getattr(conflict, "conflict_id", f"conflict:{page_num}:{len(records)}"),
                    "page_num": page_num,
                    "native_text": native,
                    "visual_text": visual,
                    "severity": severity,
                }
            )
    return records


def high_risk_review_hints(records: list[dict[str, Any]]) -> tuple[VisualReviewHint, ...]:
    """把普查里 severity==high_risk 的冲突转成 VisualReviewHint（去重 + 稳定 id）。

    去重键 (page_num, native_text)：同页同文本的高危冲突只需一次视觉复核。
    """
    hints: list[VisualReviewHint] = []
    seen: set[tuple[int, str]] = set()
    for record in records:
        if record["severity"] != "high_risk":
            continue
        key = (record["page_num"], record["native_text"])
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            VisualReviewHint(
                hint_id=f"triage_{record['page_num']:04d}_{len(hints):03d}",
                page_num=record["page_num"],
                observed_text=record["native_text"],
                description="shadow high-risk conflict triage",
            )
        )
    return tuple(hints)


def make_census_runner(
    pdf_dir: Path,
    output_root: Path,
    *,
    profile: MaterialProfile | None = None,
    engine_factory: Callable[..., Any] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """第一遍（零 API 普查）：evidence 引擎逐章跑 native+OCR+fusion，无 vision 通道、无 hints。

    产物 <output_root>/<章>/census.json（完成标记 = 该文件）：全部 source_conflict + 分诊severity
    + high_risk 计数。断点续跑：census.json 在即跳过。**决不打任何 vision/外呼**。
    """
    pdf_index = _index_by_prefix(Path(pdf_dir), ".pdf")
    if not pdf_index:
        raise ValueError(f"no chapter PDFs under {pdf_dir}")
    bound_profile = profile or make_gonglu_profile()
    factory = engine_factory or _default_engine_factory

    def runner(prefix: str) -> dict[str, Any]:
        chapter_dir = Path(output_root) / prefix
        census_path = chapter_dir / CENSUS_REPORT_NAME
        if census_path.is_file():
            return dict(read_json(census_path))
        pdf_path = pdf_index.get(prefix)
        if pdf_path is None:
            raise KeyError(f"chapter PDF not found for prefix {prefix}")
        engine = factory(profile=bound_profile, cache_root=chapter_dir / "cache", vision_verifier=None)
        manifest, report = engine.ingest_document(
            pdf_path,
            source_relative_path=pdf_path.name,
            output_dir=chapter_dir / "evidence",
        )
        records = _conflict_records(manifest)
        high_risk = sum(1 for record in records if record["severity"] == "high_risk")
        payload = {
            "chapter_prefix": prefix,
            "pdf": pdf_path.name,
            "document_sha256": manifest.source.sha256,
            "manifest_id": manifest.manifest_id,
            "pass": "census",
            "page_count": len(manifest.pages),
            "evidence_unit_count": sum(len(page.evidence_units) for page in manifest.pages),
            "source_conflict_count": len(records),
            "high_risk_conflict_count": high_risk,
            "high_risk_hint_count": len(high_risk_review_hints(records)),
            "conflicts": records,
        }
        atomic_write_json(census_path, payload)
        return payload

    return runner


def make_vision_review_runner(
    pdf_dir: Path,
    output_root: Path,
    *,
    vision_verifier: Any,
    budget: VisionCallBudget,
    census_locator: Callable[[str], Path] | None = None,
    profile: MaterialProfile | None = None,
    engine_factory: Callable[..., Any] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """第二遍（vision 复核）：读第一遍 census → 仅对 high_risk 区域打真实 vision。

    - census_locator(prefix) → census.json 路径（缺省取本 output_root/<章>/census.json；
      收口方可注入以跨路读取其它分段的普查产物）；
    - vision 通道包 BudgetedVisionVerifier（预算熔断先于任何外呼）；
    - 完成标记 = <output_root>/<章>/chapter-report.json；断点续跑：存在即跳过。
    """
    pdf_index = _index_by_prefix(Path(pdf_dir), ".pdf")
    if not pdf_index:
        raise ValueError(f"no chapter PDFs under {pdf_dir}")
    bound_profile = profile or make_gonglu_profile()
    factory = engine_factory or _default_engine_factory

    def _census_path(prefix: str) -> Path:
        if census_locator is not None:
            return Path(census_locator(prefix))
        return Path(output_root) / prefix / CENSUS_REPORT_NAME

    def runner(prefix: str) -> dict[str, Any]:
        chapter_dir = Path(output_root) / prefix
        report_path = chapter_dir / "chapter-report.json"
        if report_path.is_file():
            return dict(read_json(report_path)["metrics"])
        census_path = _census_path(prefix)
        if not census_path.is_file():
            raise KeyError(f"census not found for prefix {prefix}: {census_path}")
        census = read_json(census_path)
        hints = high_risk_review_hints(list(census["conflicts"]))
        pdf_path = pdf_index.get(prefix)
        if pdf_path is None:
            raise KeyError(f"chapter PDF not found for prefix {prefix}")
        verifier = BudgetedVisionVerifier(vision_verifier, budget)
        engine = factory(profile=bound_profile, cache_root=chapter_dir / "cache", vision_verifier=verifier)
        manifest, report = engine.ingest_document(
            pdf_path,
            source_relative_path=pdf_path.name,
            output_dir=chapter_dir / "evidence",
            review_hints=hints,
        )
        conflict_count = sum(len(getattr(page, "source_conflicts", ()) or ()) for page in manifest.pages)
        metrics = {
            "page_count": len(manifest.pages),
            "evidence_unit_count": sum(len(page.evidence_units) for page in manifest.pages),
            "source_conflict_count": conflict_count,
            "high_risk_hint_count": len(hints),
            "vision_calls_used": verifier.calls,
            "vision_failures": verifier.failures,
        }
        atomic_write_json(
            report_path,
            {
                "chapter_prefix": prefix,
                "pdf": pdf_path.name,
                "document_sha256": manifest.source.sha256,
                "manifest_id": manifest.manifest_id,
                "pass": "vision_review",
                "metrics": metrics,
                "vision_budget_after": {"calls": budget.calls, "hard_cap": budget.hard_cap},
            },
        )
        return metrics

    return runner


def dry_run_chapter(
    prefix: str,
    *,
    pdf_dir: Path,
    structured_dir: Path,
    legacy_root: Path,
    output_root: Path,
    hard_cap: int = DEFAULT_VISION_HARD_CAP,
) -> dict[str, Any]:
    """单章零 API dry-run：vision 通道固定 mock；返回接线验证摘要。"""
    output_root = Path(output_root)
    budget = VisionCallBudget(output_root / "vision-budget.json", hard_cap=hard_cap)
    v1_loader = make_v1_structured_loader(structured_dir, legacy_root)
    v2_runner = make_v2_evidence_runner(
        pdf_dir,
        output_root,
        vision_verifier=make_mock_vision_verifier(),
        budget=budget,
    )
    v1_metrics = v1_loader(prefix)
    v2_metrics = v2_runner(prefix)
    return {
        "chapter_prefix": prefix,
        "v1_metrics": v1_metrics,
        "v2_metrics": v2_metrics,
        "vision_budget": {"calls": budget.calls, "hard_cap": budget.hard_cap, "remaining": budget.remaining},
        "chapter_dir": str(Path(output_root) / prefix),
    }
