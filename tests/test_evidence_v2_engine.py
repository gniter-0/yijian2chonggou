"""Offline tests for the V2 Step 1 Evidence Engine."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.engine_types import NativeLine, NativePage, OCRLine, OCRPage, VisualReviewHint
from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from examcard.evidence.layout import classify_native_line, equivalent_text, heading_level, normalized_compare_text
from examcard.evidence.schema import EvidenceKind, SourceQuality
from examcard.evidence.vision import GoldenFixtureVisionVerifier, VisionVerification
from examcard.materials.catalog import load_material_profile


class FakeOCRProvider:
    name = "fake_ocr"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def audit(self, page: pymupdf.Page, *, page_num: int, render_dpi: int) -> OCRPage:
        self.calls += 1
        if page_num == 1:
            lines = (
                OCRLine(bbox=(20, 5, 110, 22), polygon=((20, 5), (110, 5), (110, 22), (20, 22)), text="1. Heading", confidence=0.99),
                OCRLine(bbox=(20, 25, 100, 42), polygon=((20, 25), (100, 25), (100, 42), (20, 42)), text="Alpha", confidence=0.99),
                OCRLine(bbox=(20, 48, 130, 65), polygon=((20, 48), (130, 48), (130, 65), (20, 65)), text="value=1", confidence=0.99),
                OCRLine(bbox=(65, 150, 190, 170), polygon=((65, 150), (190, 150), (190, 170), (65, 170)), text="Flow node", confidence=0.98),
            )
        else:
            lines = (
                OCRLine(bbox=(20, 25, 100, 42), polygon=((20, 25), (100, 25), (100, 42), (20, 42)), text="Beta", confidence=0.99),
                OCRLine(bbox=(20, 48, 130, 65), polygon=((20, 48), (130, 48), (130, 65), (20, 65)), text="typo", confidence=0.99),
            )
        return OCRPage(
            page_num=page_num,
            render_dpi=render_dpi,
            engine_name=self.name,
            engine_version=self.version,
            elapsed_seconds=0.01,
            lines=lines,
        )


class FakeVisionVerifier:
    name = "fake_vision"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, png_bytes: bytes) -> VisionVerification:
        self.calls += 1
        assert png_bytes.startswith(b"\x89PNG")
        return VisionVerification(visible_text="fixed", uncertain=False)


def test_typographic_equivalence_does_not_hide_formula_or_enumeration_loss():
    assert equivalent_text("费用（含税）", "费用(含税)")
    assert equivalent_text("P425~428 12.2", "12.2")
    assert normalized_compare_text("＞550～451") == ">550~451"
    assert not equivalent_text("总成本=Σ费用", "总成本=费用", strict_formula=True)
    assert not equivalent_text("① 人工费", "人工费")


def test_visual_review_bbox_ignores_rawdict_footer_order():
    native = NativePage(
        page_num=1,
        width=300,
        height=300,
        lines=(
            NativeLine(block_index=0, line_index=0, bbox=(10, 280, 80, 290), text="footer", spans=()),
            NativeLine(block_index=1, line_index=0, bbox=(10, 30, 80, 40), text="tar", spans=()),
            NativeLine(block_index=1, line_index=1, bbox=(10, 48, 80, 58), text="get", spans=()),
        ),
        images=(),
        tables=(),
        rawdict_block_count=2,
    )
    bbox = EvidenceEngine._find_review_bbox(native, "target")
    assert bbox is not None
    assert bbox[1] < 30 and bbox[3] < 80


def test_course_advertising_is_preserved_as_noise_not_core_content():
    page = NativePage(
        page_num=1,
        width=300,
        height=300,
        lines=(),
        images=(),
        tables=(),
        rawdict_block_count=0,
    )
    line = NativeLine(
        block_index=0,
        line_index=0,
        bbox=(20, 120, 260, 135),
        text="绝密押题联系QQ/微信：3849178",
        spans=(),
    )
    kind, role, _, must_cover = classify_native_line(line, page=page, repeated_noise=frozenset())
    assert kind == EvidenceKind.WATERMARK
    assert role.value == "watermark"
    assert must_cover is False


def test_exercise_answer_is_preserved_for_policy_routing_not_core_content():
    page = NativePage(
        page_num=1,
        width=300,
        height=300,
        lines=(),
        images=(),
        tables=(),
        rawdict_block_count=0,
    )
    line = NativeLine(
        block_index=0,
        line_index=0,
        bbox=(20, 120, 160, 135),
        text="【答案】C",
        spans=(),
    )
    kind, role, _, must_cover = classify_native_line(line, page=page, repeated_noise=frozenset())
    assert kind == EvidenceKind.EXERCISE
    assert role.value == "exercise_answer"
    assert must_cover is True


def test_heading_level_preserves_numbered_outline_depth():
    assert heading_level("2. 公路项目施工成本计划") == 1
    assert heading_level("12.2.2 公路工程施工成本控制方法") == 3


def _make_pdf(path: Path) -> Path:
    image_path = path.with_suffix(".png")
    image = Image.new("RGB", (400, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 180, 65), outline="black", width=3)
    draw.rectangle((220, 20, 380, 65), outline="black", width=3)
    draw.line((180, 42, 220, 42), fill="black", width=3)
    draw.polygon(((210, 35), (220, 42), (210, 49)), fill="black")
    image.save(image_path)

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((20, 20), "1. Heading", fontsize=12)
    page.insert_text((20, 40), "Alpha", fontsize=12)
    page.insert_text((20, 63), "value=1", fontsize=12)
    page.insert_image(pymupdf.Rect(40, 120, 240, 200), filename=image_path)
    page = doc.new_page(width=300, height=300)
    page.insert_text((20, 40), "Beta", fontsize=12)
    page.insert_text((20, 63), "typo", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _engine(tmp_path: Path, provider: FakeOCRProvider, vision: FakeVisionVerifier | None = None) -> EvidenceEngine:
    profile = load_material_profile(ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml")
    return EvidenceEngine(
        profile=profile,
        cache_root=tmp_path / "cache",
        config=EvidenceEngineConfig(render_dpi=144),
        ocr_provider=provider,
        vision_verifier=vision,  # type: ignore[arg-type]
    )


def test_native_ocr_fusion_and_visual_only_figure_nodes(tmp_path):
    pdf = _make_pdf(tmp_path / "sample.pdf")
    provider = FakeOCRProvider()
    result = _engine(tmp_path, provider).process_pages(pdf, page_numbers=(1,), output_dir=tmp_path / "out")
    page = result.pages[0]

    assert any(unit.kind == EvidenceKind.FORMULA and unit.source_quality == SourceQuality.VERIFIED_AGREEMENT for unit in page.evidence_units)
    assert any(unit.kind == EvidenceKind.FIGURE and unit.source_quality == SourceQuality.VISUAL_ONLY for unit in page.evidence_units)
    diagram = next(unit for unit in page.evidence_units if unit.kind == EvidenceKind.DIAGRAM_NODE)
    heading = next(unit for unit in page.evidence_units if unit.kind == EvidenceKind.HEADING)
    alpha = next(unit for unit in page.evidence_units if unit.raw_text == "Alpha")
    assert diagram.raw_text == "Flow node"
    assert diagram.source_quality == SourceQuality.VISUAL_ONLY
    assert diagram.locator.bbox is not None
    assert alpha.parent_id == heading.evidence_id
    assert (tmp_path / "out" / "pages" / "page_0001.json").is_file()
    assert any((tmp_path / "out" / "crops").glob("*.png"))


def test_document_manifest_and_cache_are_complete_and_repeatable(tmp_path):
    pdf = _make_pdf(tmp_path / "sample.pdf")
    provider = FakeOCRProvider()
    engine = _engine(tmp_path, provider)
    manifest, report = engine.ingest_document(pdf, output_dir=tmp_path / "out")

    assert [page.page_num for page in manifest.pages] == [1, 2]
    assert manifest.source.page_count == 2
    assert report["page_count"] == 2
    first_call_count = provider.calls
    repeat = engine.process_pages(pdf, page_numbers=(1, 2))
    assert provider.calls == first_call_count
    assert repeat.report["cache_hits"] >= 4
    assert (tmp_path / "out" / "manifest.json").is_file()


def test_cache_schema_revision_invalidates_derived_evidence_cache(tmp_path):
    pdf = _make_pdf(tmp_path / "sample.pdf")
    provider = FakeOCRProvider()
    first = _engine(tmp_path, provider)
    first.process_pages(pdf, page_numbers=(1,))
    original_calls = provider.calls

    profile = load_material_profile(ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml")
    revised = EvidenceEngine(
        profile=profile,
        cache_root=tmp_path / "cache",
        config=EvidenceEngineConfig(render_dpi=144, cache_schema_version="evidence-engine-v3"),
        ocr_provider=provider,
    )
    revised.process_pages(pdf, page_numbers=(1,))
    assert provider.calls > original_calls


def test_visual_review_creates_conflict_without_rewriting_native_text(tmp_path):
    pdf = _make_pdf(tmp_path / "sample.pdf")
    provider = FakeOCRProvider()
    vision = FakeVisionVerifier()
    result = _engine(tmp_path, provider, vision).process_pages(
        pdf,
        page_numbers=(2,),
        output_dir=tmp_path / "out",
        review_hints=(VisualReviewHint(hint_id="typo_check", page_num=2, observed_text="typo"),),
    )
    page = result.pages[0]

    assert vision.calls == 1
    assert any(conflict.native_text == "typo" and conflict.visual_text == "fixed" for conflict in page.source_conflicts)
    native_unit = next(unit for unit in page.evidence_units if unit.raw_text == "typo")
    assert native_unit.canonical_text == "typo"
    assert native_unit.source_quality == SourceQuality.CONFLICT


def test_golden_fixture_vision_is_deterministic_and_accepts_only_png():
    verifier = GoldenFixtureVisionVerifier(visible_text="目标成本")
    assert verifier.verify(b"\x89PNGfixture").visible_text == "目标成本"
    try:
        verifier.verify(b"not-a-png")
    except ValueError as exc:
        assert "PNG" in str(exc)
    else:
        raise AssertionError("fixture verifier accepted a non-PNG payload")
