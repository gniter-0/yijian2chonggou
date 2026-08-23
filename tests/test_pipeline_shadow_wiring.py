"""pipeline/shadow_wiring：预算熔断、断点续跑、v1/v2 接线（全部离线，tmp_path 收口）。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examcard.evidence.vision import PROBE_IDEMPOTENCY_CALL_COUNT
from examcard.pipeline.shadow_wiring import (
    BudgetedVisionVerifier,
    VisionBudgetExceeded,
    VisionCallBudget,
    _conflict_records,
    dry_run_chapter,
    high_risk_review_hints,
    load_env_file,
    make_census_runner,
    make_live_vision_verifier,
    make_mock_vision_verifier,
    make_v1_structured_loader,
    make_v2_evidence_runner,
    make_vision_review_runner,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (64).to_bytes(4, "big") + (48).to_bytes(4, "big")


def _write_structured(directory: Path, prefix: str = "01") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{prefix} 第1章（1）测试章节.json"
    target.write_text(
        json.dumps(
            {
                "knowledge_points": [
                    {"id": "kp_1", "kp_type": "content"},
                    {"id": "kp_2_q1", "kp_type": "content"},
                    {"id": "kp_3", "kp_type": "exercise"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


class _FakeEngine:
    """替换 EvidenceEngine 的测试假引擎：记录调用并产出最小 manifest/report。"""

    invocations = 0

    def __init__(self, *, vision_verifier: Any | None) -> None:
        self.vision_verifier = vision_verifier

    def ingest_document(self, pdf_path: Path, *, source_relative_path: str, output_dir: Path) -> tuple[Any, dict]:
        type(self).invocations += 1
        if self.vision_verifier is not None:
            self.vision_verifier.verify(PNG_BYTES)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "manifest.json").write_text("{}", encoding="utf-8")
        page = SimpleNamespace(evidence_units=[1, 2, 3], source_conflicts=[1])
        manifest = SimpleNamespace(
            pages=(page,),
            source=SimpleNamespace(sha256="a" * 64),
            manifest_id="manifest:test:0001",
        )
        return manifest, {"elapsed_seconds": 0.1}


def _fake_factory(*, profile: Any, cache_root: Path, vision_verifier: Any | None) -> _FakeEngine:
    return _FakeEngine(vision_verifier=vision_verifier)


def test_vision_budget_persists_and_trips_hard_cap(tmp_path: Path) -> None:
    budget = VisionCallBudget(tmp_path / "vision-budget.json", hard_cap=3)
    budget.charge(2)
    resumed = VisionCallBudget(tmp_path / "vision-budget.json", hard_cap=3)
    assert resumed.calls == 2 and resumed.remaining == 1
    resumed.charge(1)
    with pytest.raises(VisionBudgetExceeded, match="hard cap 3"):
        resumed.charge(1)
    with pytest.raises(ValueError, match="hard cap mismatch"):
        VisionCallBudget(tmp_path / "vision-budget.json", hard_cap=5)


def test_budgeted_verifier_charges_before_calling(tmp_path: Path) -> None:
    budget = VisionCallBudget(tmp_path / "b.json", hard_cap=1)
    wrapped = BudgetedVisionVerifier(make_mock_vision_verifier("文本"), budget)
    result = wrapped.verify(PNG_BYTES)
    assert result.visible_text == "文本"
    assert wrapped.calls == 1 and budget.calls == 1
    with pytest.raises(VisionBudgetExceeded):
        wrapped.verify(PNG_BYTES)
    assert wrapped.calls == 1  # 熔断发生在外呼之前，不计为一次调用


class _RecordingProber:
    """记录调用的假 verifier：只观察预算记账，不产生任何外呼。"""

    name = "recording_prober"
    version = "test-v1"

    def __init__(self) -> None:
        self.probe_keys: list[str] = []

    def verify(self, png_bytes: bytes) -> Any:
        raise AssertionError("本测试不应触达 verify")

    def probe_idempotency(self, png_bytes: bytes, *, idempotency_key: str) -> str:
        self.probe_keys.append(idempotency_key)
        return "probed"


def test_budgeted_probe_idempotency_charges_every_call_it_makes(tmp_path: Path) -> None:
    """幂等探测一次发两呼，就必须扣两次——按 verify 的一次一扣记会让账面短记一半。"""
    budget = VisionCallBudget(tmp_path / "probe.json", hard_cap=4)
    inner = _RecordingProber()
    wrapped = BudgetedVisionVerifier(inner, budget)

    assert wrapped.probe_idempotency(PNG_BYTES, idempotency_key="k-1") == "probed"

    assert inner.probe_keys == ["k-1"]
    assert PROBE_IDEMPOTENCY_CALL_COUNT == 2
    assert budget.calls == PROBE_IDEMPOTENCY_CALL_COUNT
    assert wrapped.calls == PROBE_IDEMPOTENCY_CALL_COUNT


def test_budgeted_probe_idempotency_refuses_when_only_one_call_remains(tmp_path: Path) -> None:
    """余量只剩 1 时当场熔断：两呼探测不许先发一呼、再发现额度不够。"""
    budget = VisionCallBudget(tmp_path / "probe.json", hard_cap=3)
    budget.charge(2)
    inner = _RecordingProber()
    wrapped = BudgetedVisionVerifier(inner, budget)

    with pytest.raises(VisionBudgetExceeded, match="hard cap 3"):
        wrapped.probe_idempotency(PNG_BYTES, idempotency_key="k-2")

    assert inner.probe_keys == []
    assert budget.calls == 2 and wrapped.calls == 0


def test_budgeted_probe_idempotency_fails_loud_without_charging(tmp_path: Path) -> None:
    """内层没有探测能力时响亮失败，且不许先把额度扣掉。"""
    budget = VisionCallBudget(tmp_path / "probe.json", hard_cap=4)
    wrapped = BudgetedVisionVerifier(make_mock_vision_verifier("文本"), budget)

    with pytest.raises(AttributeError, match="probe_idempotency"):
        wrapped.probe_idempotency(PNG_BYTES, idempotency_key="k-3")

    assert budget.calls == 0 and wrapped.calls == 0


def test_load_env_and_live_verifier_fail_closed(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# 注释\nAPI_BASE_URL=https://example.invalid/v1\nAPI_KEY='secret'\n", encoding="utf-8")
    values = load_env_file(env)
    assert values["API_BASE_URL"] == "https://example.invalid/v1"
    assert values["API_KEY"] == "secret"
    with pytest.raises(ValueError, match="TEXT_MODEL"):
        make_live_vision_verifier(env)


def test_v1_loader_reads_legacy_metrics(tmp_path: Path) -> None:
    structured = tmp_path / "structured"
    _write_structured(structured)
    loader = make_v1_structured_loader(structured, tmp_path)
    metrics = loader("01")
    assert metrics == {"knowledge_point_count": 3, "content_kp_count": 1, "exercise_kp_count": 2}
    with pytest.raises(KeyError):
        loader("99")
    with pytest.raises(ValueError, match="no structured chapter files"):
        make_v1_structured_loader(tmp_path / "empty", tmp_path)


def _pdf_dir(tmp_path: Path) -> Path:
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    (pdf_dir / "01 第1章（1）测试章节.pdf").write_bytes(b"%PDF-fake")
    return pdf_dir


def test_v2_runner_runs_then_resumes_without_recompute(tmp_path: Path) -> None:
    _FakeEngine.invocations = 0
    output_root = tmp_path / "out"
    budget = VisionCallBudget(output_root / "vision-budget.json", hard_cap=10)
    runner = make_v2_evidence_runner(
        _pdf_dir(tmp_path),
        output_root,
        vision_verifier=make_mock_vision_verifier(),
        budget=budget,
        engine_factory=_fake_factory,
    )
    metrics = runner("01")
    assert metrics == {"page_count": 1, "evidence_unit_count": 3, "source_conflict_count": 1, "vision_calls_used": 1}
    assert _FakeEngine.invocations == 1
    assert (output_root / "01" / "chapter-report.json").is_file()
    resumed_runner = make_v2_evidence_runner(
        _pdf_dir(tmp_path),
        output_root,
        vision_verifier=make_mock_vision_verifier(),
        budget=VisionCallBudget(output_root / "vision-budget.json", hard_cap=10),
        engine_factory=_fake_factory,
    )
    assert resumed_runner("01") == metrics
    assert _FakeEngine.invocations == 1  # 断点续跑：未重算


def test_v2_runner_budget_breaker_stops_run(tmp_path: Path) -> None:
    _FakeEngine.invocations = 0
    output_root = tmp_path / "out"
    budget = VisionCallBudget(output_root / "vision-budget.json", hard_cap=1)
    budget.charge(1)
    runner = make_v2_evidence_runner(
        _pdf_dir(tmp_path),
        output_root,
        vision_verifier=make_mock_vision_verifier(),
        budget=budget,
        engine_factory=_fake_factory,
    )
    with pytest.raises(VisionBudgetExceeded):
        runner("01")
    assert not (output_root / "01" / "chapter-report.json").exists()  # 熔断章不得留完成标记


def test_v2_runner_unknown_prefix_fails_closed(tmp_path: Path) -> None:
    runner = make_v2_evidence_runner(
        _pdf_dir(tmp_path),
        tmp_path / "out",
        vision_verifier=None,
        budget=VisionCallBudget(tmp_path / "out" / "vision-budget.json"),
        engine_factory=_fake_factory,
    )
    with pytest.raises(KeyError):
        runner("42")


def _conflict(page_num: int, native: str, visual: str, cid: str) -> Any:
    return SimpleNamespace(
        conflict_id=cid,
        locator=SimpleNamespace(page_num=page_num),
        native_text=native,
        visual_text=visual,
    )


class _ConflictEngine:
    """两遍式测试假引擎：产 native/visual 冲突；记录收到的 review_hints。"""

    last_hints: tuple = ()

    def __init__(self, *, vision_verifier: Any | None) -> None:
        self.vision_verifier = vision_verifier

    def ingest_document(self, pdf_path: Path, *, source_relative_path: str, output_dir: Path, review_hints: tuple = ()) -> tuple[Any, dict]:
        type(self).last_hints = review_hints
        # 每个 hint 触发一次 vision（模拟真实通道对高危区逐个复核）。
        if self.vision_verifier is not None:
            for _ in review_hints:
                self.vision_verifier.verify(PNG_BYTES)
        output_dir.mkdir(parents=True, exist_ok=True)
        page = SimpleNamespace(
            evidence_units=[1, 2, 3, 4],
            source_conflicts=(
                _conflict(1, "不得大于 300mm", "不得大于 800mm", "conflict:1:0"),  # high_risk（数字+义务措辞+真差异）
                _conflict(1, "路基施工", "路基施工 ", "conflict:1:1"),  # typography_only（仅尾空格）
                _conflict(2, "示意图甲", "示意图乙", "conflict:2:0"),  # normal（真差异、无高危词元）
                _conflict(2, None, "仅视觉", "conflict:2:1"),  # unlocatable（无 native）
            ),
        )
        manifest = SimpleNamespace(pages=(page,), source=SimpleNamespace(sha256="b" * 64), manifest_id="manifest:conflict:1")
        return manifest, {"elapsed_seconds": 0.1}


def _conflict_factory(*, profile: Any, cache_root: Path, vision_verifier: Any | None) -> _ConflictEngine:
    return _ConflictEngine(vision_verifier=vision_verifier)


def test_triage_selects_only_high_risk_conflicts() -> None:
    records = [
        {"page_num": 1, "native_text": "不得大于 300mm", "visual_text": "不得大于 300mm", "severity": "high_risk"},
        {"page_num": 1, "native_text": "不得大于 300mm", "visual_text": "x", "severity": "high_risk"},  # 同页同文本→去重
        {"page_num": 2, "native_text": "示意图", "visual_text": "示意图", "severity": "normal"},
    ]
    hints = high_risk_review_hints(records)
    assert len(hints) == 1
    assert hints[0].page_num == 1 and hints[0].observed_text == "不得大于 300mm"


def test_census_runner_writes_census_and_counts_high_risk(tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner = make_census_runner(_pdf_dir(tmp_path), out, engine_factory=_conflict_factory)
    payload = runner("01")
    assert payload["pass"] == "census"
    assert payload["source_conflict_count"] == 4
    assert payload["high_risk_conflict_count"] == 1
    assert payload["high_risk_hint_count"] == 1
    severities = {r["severity"] for r in payload["conflicts"]}
    assert severities == {"high_risk", "typography_only", "normal", "unlocatable"}
    assert (out / "01" / "census.json").is_file()
    assert not (out / "01" / "chapter-report.json").exists()  # 普查不写第二遍标记


def test_census_runner_resumes_without_recompute(tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner = make_census_runner(_pdf_dir(tmp_path), out, engine_factory=_conflict_factory)
    first = runner("01")
    (out / "01" / "sentinel").write_text("x", encoding="utf-8")  # 若重算会覆盖目录内容
    second = runner("01")
    assert second["manifest_id"] == first["manifest_id"]
    assert (out / "01" / "sentinel").is_file()


def test_vision_review_runner_targets_only_high_risk(tmp_path: Path) -> None:
    out = tmp_path / "out"
    make_census_runner(_pdf_dir(tmp_path), out, engine_factory=_conflict_factory)("01")
    budget = VisionCallBudget(out / "vision-budget.json", hard_cap=10)
    runner = make_vision_review_runner(
        _pdf_dir(tmp_path), out,
        vision_verifier=make_mock_vision_verifier("复核"),
        budget=budget,
        engine_factory=_conflict_factory,
    )
    metrics = runner("01")
    assert metrics["high_risk_hint_count"] == 1
    assert metrics["vision_calls_used"] == 1  # 仅高危区打 vision
    assert budget.calls == 1
    assert _ConflictEngine.last_hints and _ConflictEngine.last_hints[0].observed_text == "不得大于 300mm"
    assert (out / "01" / "chapter-report.json").is_file()
    # 断点续跑：第二遍标记在即跳过，预算不再增。
    resumed = make_vision_review_runner(
        _pdf_dir(tmp_path), out,
        vision_verifier=make_mock_vision_verifier("复核"),
        budget=VisionCallBudget(out / "vision-budget.json", hard_cap=10),
        engine_factory=_conflict_factory,
    )
    assert resumed("01") == metrics


def test_vision_review_runner_requires_census(tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner = make_vision_review_runner(
        _pdf_dir(tmp_path), out,
        vision_verifier=make_mock_vision_verifier(),
        budget=VisionCallBudget(out / "vision-budget.json", hard_cap=10),
        engine_factory=_conflict_factory,
    )
    with pytest.raises(KeyError, match="census not found"):
        runner("01")


def test_vision_review_runner_budget_breaker_leaves_no_marker(tmp_path: Path) -> None:
    out = tmp_path / "out"
    make_census_runner(_pdf_dir(tmp_path), out, engine_factory=_conflict_factory)("01")
    budget = VisionCallBudget(out / "vision-budget.json", hard_cap=1)
    budget.charge(1)
    runner = make_vision_review_runner(
        _pdf_dir(tmp_path), out,
        vision_verifier=make_mock_vision_verifier(),
        budget=budget,
        engine_factory=_conflict_factory,
    )
    with pytest.raises(VisionBudgetExceeded):
        runner("01")
    assert not (out / "01" / "chapter-report.json").exists()


def test_conflict_records_marks_unlocatable_without_native() -> None:
    manifest = SimpleNamespace(
        pages=(SimpleNamespace(source_conflicts=(_conflict(3, None, "仅视觉", "c:3:0"),)),)
    )
    records = _conflict_records(manifest)
    assert records[0]["severity"] == "unlocatable"
    assert high_risk_review_hints(records) == ()


def test_dry_run_chapter_wires_both_sides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    structured = tmp_path / "structured"
    _write_structured(structured)
    import examcard.pipeline.shadow_wiring as wiring

    monkeypatch.setattr(wiring, "_default_engine_factory", _fake_factory)
    summary = dry_run_chapter(
        "01",
        pdf_dir=_pdf_dir(tmp_path),
        structured_dir=structured,
        legacy_root=tmp_path,
        output_root=tmp_path / "shadow_out",
        hard_cap=5,
    )
    assert summary["v1_metrics"]["knowledge_point_count"] == 3
    assert summary["v2_metrics"]["page_count"] == 1
    assert summary["vision_budget"] == {"calls": 1, "hard_cap": 5, "remaining": 4}
    assert (tmp_path / "shadow_out" / "01" / "chapter-report.json").is_file()
