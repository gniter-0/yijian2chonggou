"""Typeset agent card images for the agent image lane (no model, no network).

Reads ``<run-root>/agent_images/queue.json`` written by
``migration/run_agent_step2_v1.py run`` and writes, for every packet whose PNG
is still missing, ``agent_images/responses/prompt-<sha256>.png``.

The image request asks for the supplied source-derived card to be rendered
exactly (``render_only_supplied_source_text``, ``do_not_add_labels_claims_or_values``).
This renderer therefore draws only the card title and the verbatim text of every
``source_element`` in source order - nothing is paraphrased, translated,
labelled or added:

* ``table`` cards place each cell at its source PDF bbox (from ``evidence_refs``),
  scaled into the canvas, so the source grid layout is preserved;
* every other card stacks its elements top to bottom; ``threshold`` elements get
  a shaded box, the rest are plain paragraphs.

The font size is chosen per card as the largest size at which everything fits.
Run ``run_agent_step2_v1.py images`` afterwards: the PNGs still pass the
unchanged image envelope gate (exact size, valid PNG) before the runner keeps them.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from PIL import Image, ImageDraw, ImageFont

__all__ = ["main", "render_card_image", "wrap_text"]

_FONT_CANDIDATES: Final = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)
_BOLD_CANDIDATES: Final = (Path("C:/Windows/Fonts/msyhbd.ttc"),)
_MARGIN: Final = 56
_TITLE_TOP: Final = 40
_INK: Final = (28, 32, 40)
_RULE: Final = (70, 90, 120)
_GRID: Final = (90, 100, 115)
_SHADE: Final = (232, 240, 252)
_HEADER_SHADE: Final = (242, 245, 249)
_MAX_TABLE_STRETCH: Final = 2.0
_MIN_SOURCE_LINE_SIZE: Final = 18


def _font_path(candidates: Sequence[Path], override: Path | None) -> Path:
    if override is not None:
        return override
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("render: no CJK font found; pass --font")


@dataclass(frozen=True, slots=True)
class _Fonts:
    regular: Path
    bold: Path

    def get(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.bold if bold else self.regular), size)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Character-level wrap (CJK has no spaces); explicit newlines are kept."""

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            if current and font.getlength(current + char) > width:
                lines.append(current)
                current = char
            else:
                current += char
        lines.append(current)
    return lines


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * 1.18)


def _draw_title(draw: ImageDraw.ImageDraw, title: str, fonts: _Fonts, width: int) -> int:
    for size in range(48, 23, -2):
        font = fonts.get(size, bold=True)
        lines = wrap_text(title, font, width - 2 * _MARGIN)
        if len(lines) <= 2:
            break
    y = _TITLE_TOP
    for line in lines:
        draw.text((_MARGIN, y), line, font=font, fill=_INK)
        y += _line_height(font)
    y += 10
    draw.line((_MARGIN, y, width - _MARGIN, y), fill=_RULE, width=3)
    return y + 24


def _bbox_of(element: dict[str, Any], refs: dict[str, dict[str, float]]) -> tuple[float, float, float, float] | None:
    boxes = [refs[eid] for eid in element.get("evidence_ids", []) if eid in refs]
    if not boxes:
        return None
    return (
        min(b["x0"] for b in boxes),
        min(b["y0"] for b in boxes),
        max(b["x1"] for b in boxes),
        max(b["y1"] for b in boxes),
    )


def _fit_font(text: str, fonts: _Fonts, box_w: float, box_h: float, start: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = start
    while True:
        font = fonts.get(size)
        lines = wrap_text(text, font, box_w)
        if len(lines) * _line_height(font) <= box_h or size <= 10:
            return font, lines
        size -= 1


def _render_table(
    draw: ImageDraw.ImageDraw, request: dict[str, Any], fonts: _Fonts, area: tuple[int, int, int, int]
) -> bool:
    refs = {ref["evidence_id"]: ref["bbox"] for ref in request.get("evidence_refs", [])}
    cells: dict[tuple[float, float, float, float], list[str]] = {}
    for element in request["source_elements"]:
        box = _bbox_of(element, refs)
        if box is None:
            return False
        cells.setdefault(box, []).append(element["text"])
    x0 = min(b[0] for b in cells)
    y0 = min(b[1] for b in cells)
    x1 = max(b[2] for b in cells)
    y1 = max(b[3] for b in cells)
    left, top, right, bottom = area
    fit_y = (bottom - top) / max(y1 - y0, 1e-6)
    scale_x = min((right - left) / max(x1 - x0, 1e-6), fit_y)
    # Wide, flat tables may stretch vertically (at most 2x) so their text is not tiny;
    # cell order, adjacency and merges are unchanged by an axis-aligned scale.
    scale_y = min(fit_y, scale_x * _MAX_TABLE_STRETCH)
    off_x = left + ((right - left) - (x1 - x0) * scale_x) / 2
    off_y = top + ((bottom - top) - (y1 - y0) * scale_y) / 2

    def tx(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (
            off_x + (box[0] - x0) * scale_x,
            off_y + (box[1] - y0) * scale_y,
            off_x + (box[2] - x0) * scale_x,
            off_y + (box[3] - y0) * scale_y,
        )

    # One shared font size for the whole grid: the largest size at which every cell fits.
    placed = [(tx(box), "\n".join(texts)) for box, texts in cells.items()]

    def fits(font: ImageFont.FreeTypeFont, *, keep_source_lines: bool) -> bool:
        for c, text in placed:
            lines = wrap_text(text, font, (c[2] - c[0]) - 14)
            if keep_source_lines and len(lines) != text.count("\n") + 1:
                return False
            if len(lines) * _line_height(font) > (c[3] - c[1]) - 8:
                return False
        return True

    # Prefer the largest size that keeps every source line break as-is (no orphan
    # characters); only below _MIN_SOURCE_LINE_SIZE fall back to extra wrapping.
    size = next(
        (n for n in range(48, _MIN_SOURCE_LINE_SIZE - 1, -1) if fits(fonts.get(n), keep_source_lines=True)),
        next((n for n in range(48, 10, -1) if fits(fonts.get(n), keep_source_lines=False)), 10),
    )
    font = fonts.get(size)
    top_row = min(c[1] for c, _ in placed)
    for c, text in placed:
        fill = _HEADER_SHADE if abs(c[1] - top_row) < 1 else None
        draw.rectangle(c, outline=_GRID, width=2, fill=fill)
        lines = wrap_text(text, font, (c[2] - c[0]) - 14)
        block_h = len(lines) * _line_height(font)
        y = c[1] + ((c[3] - c[1]) - block_h) / 2
        for line in lines:
            w = font.getlength(line)
            draw.text((c[0] + ((c[2] - c[0]) - w) / 2, y), line, font=font, fill=_INK)
            y += _line_height(font)
    return True


def _render_blocks(
    draw: ImageDraw.ImageDraw, request: dict[str, Any], fonts: _Fonts, area: tuple[int, int, int, int]
) -> None:
    left, top, right, bottom = area
    elements = request["source_elements"]
    pad = 18
    gap = 22
    for size in range(56, 9, -1):
        font = fonts.get(size)
        heights = []
        for element in elements:
            shaded = element.get("source_renderer") == "threshold"
            inner = (right - left) - (2 * pad if shaded else 0)
            lines = wrap_text(element["text"], font, inner)
            heights.append(len(lines) * _line_height(font) + (2 * pad if shaded else 0))
        if sum(heights) + gap * (len(elements) - 1) <= bottom - top:
            break
    y = float(top)
    for element in elements:
        shaded = element.get("source_renderer") == "threshold"
        inner = (right - left) - (2 * pad if shaded else 0)
        lines = wrap_text(element["text"], font, inner)
        height = len(lines) * _line_height(font) + (2 * pad if shaded else 0)
        if shaded:
            draw.rounded_rectangle((left, y, right, y + height), radius=14, fill=_SHADE, outline=_RULE, width=2)
        ty = y + (pad if shaded else 0)
        for line in lines:
            draw.text((left + (pad if shaded else 0), ty), line, font=font, fill=_INK)
            ty += _line_height(font)
        y += height + gap


def render_card_image(request: dict[str, Any], *, size: tuple[int, int], fonts: _Fonts) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    body_top = _draw_title(draw, request["title"], fonts, width)
    area = (_MARGIN, body_top, width - _MARGIN, height - _MARGIN)
    if request["presentation"] == "table" and _render_table(draw, request, fonts, area):
        return image
    _render_blocks(draw, request, fonts, area)
    return image


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Typeset agent card PNGs for the agent image lane.")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--font", type=Path, default=None, help="CJK font file (default: first system match)")
    parser.add_argument("--force", action="store_true", help="re-render PNGs that already exist")
    args = parser.parse_args(argv)
    image_root = Path(args.run_root).resolve() / "agent_images"
    queue_path = image_root / "queue.json"
    if not queue_path.is_file():
        print("render: no agent_images/queue.json; run `run_agent_step2_v1.py run` first", file=sys.stderr)
        return 2
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    width, height = (int(part) for part in str(queue["size"]).split("x"))
    regular = _font_path(_FONT_CANDIDATES, args.font)
    fonts = _Fonts(regular=regular, bold=_font_path(_BOLD_CANDIDATES, None) if args.font is None and any(p.is_file() for p in _BOLD_CANDIDATES) else regular)
    responses = image_root / "responses"
    responses.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in queue["requests"]:
        target = responses / entry["response_file"]
        if target.is_file() and not args.force:
            continue
        packet = json.loads((image_root / entry["packet"]).read_text(encoding="utf-8"))
        image = render_card_image(packet["request"], size=(width, height), fonts=fonts)
        image.save(target, format="PNG")
        written += 1
        print(f"rendered {entry['raw_id']} {entry['title']} -> {target.name}")
    print(f"render: wrote {written} PNG(s) into {responses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
