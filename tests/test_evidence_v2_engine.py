"""Offline tests for the V2 Step 1 Evidence Engine."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.engine_types import NativeLine, NativePage, NativeSpan, OCRLine, OCRPage, VisualReviewHint
from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from collections import Counter

from examcard.evidence.layout import (
    OverlayMask,
    classify_native_line,
    classify_overlay_fragment,
    equivalent_text,
    heading_level,
    is_overlay_line,
    normalized_compare_text,
    strip_overlay_lines,
)
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
        config=EvidenceEngineConfig(
            render_dpi=144,
            cache_schema_version=EvidenceEngineConfig().cache_schema_version + "-revised",
        ),
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


def _line(text: str, *, direction=(1.0, 0.0), writing_mode=0, alpha=255) -> NativeLine:
    span = NativeSpan(text=text, bbox=(20, 120, 260, 135), font="f", size=12, alpha=alpha)
    return NativeLine(
        block_index=0,
        line_index=0,
        bbox=(20, 120, 260, 135),
        text=text,
        spans=(span,),
        writing_mode=writing_mode,
        direction=direction,
    )


def test_overlay_line_is_decided_by_geometry_not_wording():
    page = NativePage(page_num=1, width=300, height=300, lines=(), images=(), tables=(), rawdict_block_count=0)
    diagonal = _line("示例文字 ABC 2026", direction=(0.7071, -0.7071))
    assert is_overlay_line(diagonal)
    kind, role, tags, must_cover = classify_native_line(diagonal, page=page, repeated_noise=frozenset())
    assert (kind, role.value, must_cover) == (EvidenceKind.WATERMARK, "watermark", False)
    assert "overlay_text" in tags
    assert is_overlay_line(_line("半透明文字", alpha=122))
    # Horizontal body copy, vertical writing and axis-aligned rotated labels stay content.
    assert not is_overlay_line(_line("路基施工准备"))
    assert not is_overlay_line(_line("竖排", direction=(0.0, 1.0), writing_mode=1))
    assert not is_overlay_line(_line("表头", direction=(0.0, -1.0)))


def test_overlay_fragment_needs_overlay_glyph_coverage_and_overlay_text():
    mask = OverlayMask(glyph_boxes=((100, 100, 140, 140), (140, 100, 180, 140)), texts=("精准押题联系微信3849178",))
    on_glyphs = (105, 105, 175, 135)
    assert classify_overlay_fragment("联系微信", bbox=on_glyphs, mask=mask) == "overlay"
    assert classify_overlay_fragment("49178", bbox=on_glyphs, mask=mask) == "overlay"
    # Body text fused with the watermark is tagged, never dropped.
    assert classify_overlay_fragment("供系", bbox=on_glyphs, mask=mask) == "overlap"
    # Body text under the watermark shares no characters with it.
    assert classify_overlay_fragment("土的主要性能参数", bbox=on_glyphs, mask=mask) is None
    # The same words away from the overlay glyphs are not touched.
    assert classify_overlay_fragment("联系微信", bbox=(10, 10, 60, 30), mask=mask) is None
    # A single shared character is not enough to call a fragment watermark.
    assert classify_overlay_fragment("9", bbox=on_glyphs, mask=mask) == "overlap"
    assert classify_overlay_fragment("联系微信", bbox=on_glyphs, mask=OverlayMask(glyph_boxes=(), texts=())) is None


class OverlayOCRProvider:
    """OCR that re-reads part of the rotated overlay and two labels inside the figure."""

    name = "fake_overlay_ocr"
    version = "1"

    def audit(self, page: pymupdf.Page, *, page_num: int, render_dpi: int) -> OCRPage:
        glyphs = [
            char
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", [])
            if abs(line["dir"][1]) > 0.05 and abs(line["dir"][0]) > 0.05
            for span in line["spans"]
            for char in span["chars"]
        ]
        piece = glyphs[-5:]  # "49178"
        box = (
            min(char["bbox"][0] for char in piece),
            min(char["bbox"][1] for char in piece),
            max(char["bbox"][2] for char in piece),
            max(char["bbox"][3] for char in piece),
        )

        def ocr(bbox, text):
            x0, y0, x1, y1 = bbox
            return OCRLine(bbox=bbox, polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)), text=text, confidence=0.95)

        return OCRPage(
            page_num=page_num,
            render_dpi=render_dpi,
            engine_name=self.name,
            engine_version=self.version,
            elapsed_seconds=0.01,
            lines=(
                ocr(box, "".join(char["c"] for char in piece)),
                ocr((60, 130, 100, 145), "嗨学"),
                ocr((150, 160, 180, 175), "75"),
            ),
        )


def test_ocr_reread_of_overlay_and_figure_noise_never_become_core(tmp_path):
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (400, 160), "white").save(image_path)
    pdf = tmp_path / "overlay.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((20, 40), "Body text", fontsize=12)
    page.insert_image(pymupdf.Rect(40, 120, 240, 200), filename=image_path)
    pivot = pymupdf.Point(40, 290)
    page.insert_text(pivot, "CALL 3849178", fontsize=24, morph=(pivot, pymupdf.Matrix(45)))
    doc.save(pdf)
    doc.close()

    result = _engine(tmp_path, OverlayOCRProvider()).process_pages(pdf, page_numbers=(1,))
    units = result.pages[0].evidence_units
    rotated = next(unit for unit in units if unit.raw_text == "CALL3849178")  # rawdict chars carry no spaces
    assert rotated.kind == EvidenceKind.WATERMARK and "overlay_text" in rotated.risk_tags
    reread = next(unit for unit in units if unit.raw_text == "49178")
    assert reread.kind == EvidenceKind.WATERMARK
    assert reread.semantic_role.value == "watermark" and reread.must_cover_by_source is False
    brand = next(unit for unit in units if unit.raw_text == "嗨学")
    assert brand.kind == EvidenceKind.WATERMARK and brand.parent_id is None
    label = next(unit for unit in units if unit.raw_text == "75")
    assert label.kind == EvidenceKind.DIAGRAM_NODE and label.semantic_role.value == "core"
    body = next(unit for unit in units if unit.raw_text == "Bodytext")
    assert body.semantic_role.value == "core"


def test_table_cells_drop_only_overlay_glyph_lines_drawn_inside_the_cell():
    # "联" and "系" glyphs of a diagonal watermark sit inside two different cells.
    mask = OverlayMask(
        glyph_boxes=((12, 12, 18, 18), (52, 12, 58, 18)),
        texts=("唯一联系微信3849178",),
        glyph_chars=("系", "联"),
    )
    cell_a, cell_b = (10, 10, 40, 30), (50, 10, 80, 30)
    assert strip_overlay_lines("系\n2021", bbox=cell_a, mask=mask) == ("2021", ("系",), False)
    assert strip_overlay_lines("联\n45 题", bbox=cell_b, mask=mask) == ("45 题", ("联",), False)
    # The glyph is not inside this cell, so identical body characters stay.
    assert strip_overlay_lines("联\n45 题", bbox=cell_a, mask=mask) == ("联\n45 题", (), False)
    # A body line that merely shares the character is kept and tagged.
    assert strip_overlay_lines("联系人", bbox=cell_b, mask=mask) == ("联系人", (), True)
    # A cell is never emptied.
    assert strip_overlay_lines("系", bbox=cell_a, mask=mask) == ("系", (), True)
    assert strip_overlay_lines("系\n2021", bbox=None, mask=mask) == ("系\n2021", (), False)


def test_table_cell_tokens_are_removed_only_beyond_the_body_glyph_count():
    mask = OverlayMask(
        glyph_boxes=((30, 12, 36, 18), (20, 22, 26, 28)),
        texts=("唯一联系微信3849178",),
        glyph_chars=("8", "7"),
    )
    cell = (10, 10, 60, 30)
    body = Counter("防护工程设置与施工")
    # The trailing "8" and the "7" line are watermark: the body has neither.
    assert strip_overlay_lines("防护工程设置与施工 8\n7", bbox=cell, mask=mask, body=body) == (
        "防护工程设置与施工",
        ("8", "7"),
        False,
    )
    # A body "8" in the same cell keeps one "8"; only the surplus is watermark.
    assert strip_overlay_lines("压实度 8 8", bbox=cell, mask=mask, body=Counter("压实度8")) == ("压实度 8", ("8",), False)
    assert strip_overlay_lines("压实度 8", bbox=cell, mask=mask, body=Counter("压实度8")) == ("压实度 8", (), True)


class NoOCRProvider:
    name = "fake_no_ocr"
    version = "1"

    def audit(self, page: pymupdf.Page, *, page_num: int, render_dpi: int) -> OCRPage:
        return OCRPage(page_num=page_num, render_dpi=render_dpi, engine_name=self.name, engine_version=self.version, elapsed_seconds=0.0, lines=())


def test_watermark_crossing_a_table_is_removed_from_cell_text_only(tmp_path):
    pdf = tmp_path / "table.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    for x0, x1 in ((20, 140), (140, 280)):
        for y0, y1 in ((40, 120), (120, 200)):
            page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=1)
    page.insert_text((30, 70), "Item", fontsize=12)
    page.insert_text((150, 70), "Value 8", fontsize=12)
    page.insert_text((30, 150), "Other", fontsize=12)
    page.insert_text((150, 150), "Text", fontsize=12)
    pivot = pymupdf.Point(160, 115)
    page.insert_text(pivot, "79", fontsize=14, morph=(pivot, pymupdf.Matrix(45)))
    doc.save(pdf)
    doc.close()

    result = _engine(tmp_path, NoOCRProvider()).process_pages(pdf, page_numbers=(1,))
    cells = [unit for unit in result.pages[0].evidence_units if unit.kind == EvidenceKind.TABLE_CELL]
    assert cells, "the bordered table must be detected"
    texts = [unit.raw_text for unit in cells]
    assert all("7" not in text and "9" not in text for text in texts)
    cleaned = [unit for unit in cells if unit.metadata.get("overlay_removed")]
    assert cleaned and set("".join(cleaned[0].metadata["overlay_removed"])) <= {"7", "9"}
    # The body digit 8 shares no glyph with the watermark and survives.
    assert any(normalized_compare_text(text) == "Value8" for text in texts)
