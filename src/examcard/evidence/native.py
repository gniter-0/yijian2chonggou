"""PyMuPDF rawdict extraction with character coordinates kept intact."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

import fitz

from .engine_types import (
    NativeChar,
    NativeImage,
    NativeLine,
    NativePage,
    NativeSpan,
    NativeTable,
    NativeTableCell,
)


def _rect_tuple(rect: fitz.Rect, page_rect: fitz.Rect) -> tuple[float, float, float, float]:
    return (
        round(max(page_rect.x0, rect.x0), 4),
        round(max(page_rect.y0, rect.y0), 4),
        round(min(page_rect.x1, rect.x1), 4),
        round(min(page_rect.y1, rect.y1), 4),
    )


def _union_rect(rects: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    values = list(rects)
    if not values:
        return (0.0, 0.0, 0.1, 0.1)
    return (
        min(item[0] for item in values),
        min(item[1] for item in values),
        max(item[2] for item in values),
        max(item[3] for item in values),
    )


def _extract_tables(page: fitz.Page) -> tuple[NativeTable, ...]:
    try:
        detected = page.find_tables().tables
    except (RuntimeError, ValueError):
        return ()
    tables: list[NativeTable] = []
    for table_index, table in enumerate(detected):
        extracted = table.extract()
        cells: list[NativeTableCell] = []
        seen: dict[tuple[float, float, float, float], list[tuple[int, int]]] = {}
        raw_cells: list[tuple[int, int, tuple[float, float, float, float] | None, str]] = []
        for row_index, row in enumerate(table.rows):
            values = extracted[row_index] if row_index < len(extracted) else []
            for column_index, cell_rect in enumerate(row.cells):
                # 显式注解 + 显式解包成 4 元组：fitz 无存根，extract() 的格值与 Rect 迭代
                # 都是 Any，生成器进 tuple() 只能推出 tuple[float, ...]，与下面容器声明的
                # 定长 4 元组对不上。Rect 恒为 4 个分量，解包不改变行为。
                text: str = values[column_index] if column_index < len(values) and values[column_index] else ""
                bbox: tuple[float, float, float, float] | None = None
                if cell_rect:
                    x0, y0, x1, y1 = (round(float(value), 4) for value in cell_rect)
                    bbox = (x0, y0, x1, y1)
                raw_cells.append((row_index, column_index, bbox, text))
                if bbox is not None:
                    seen.setdefault(bbox, []).append((row_index, column_index))
        for row_index, column_index, bbox, text in raw_cells:
            positions = seen.get(bbox, []) if bbox is not None else []
            row_span = len({position[0] for position in positions}) or 1
            col_span = len({position[1] for position in positions}) or 1
            cells.append(
                NativeTableCell(
                    row=row_index,
                    column=column_index,
                    text=text.strip(),
                    bbox=bbox,
                    row_span=row_span,
                    col_span=col_span,
                )
            )
        tables.append(
            NativeTable(
                table_index=table_index,
                bbox=tuple(round(float(value), 4) for value in table.bbox),
                row_count=table.row_count,
                column_count=table.col_count,
                cells=tuple(cells),
            )
        )
    return tuple(tables)


def extract_native_page(page: fitz.Page, *, page_num: int) -> NativePage:
    """Extract unmodified native text, style, char boxes, images, tables and drawings."""
    page_rect = page.rect
    rawdict = page.get_text("rawdict", sort=False)
    lines: list[NativeLine] = []
    for block_index, block in enumerate(rawdict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, raw_line in enumerate(block.get("lines", [])):
            spans: list[NativeSpan] = []
            for raw_span in raw_line.get("spans", []):
                chars = tuple(
                    NativeChar(char=item.get("c", ""), bbox=_rect_tuple(fitz.Rect(item["bbox"]), page_rect))
                    for item in raw_span.get("chars", [])
                )
                text = "".join(item.char for item in chars)
                if not text:
                    continue
                spans.append(
                    NativeSpan(
                        text=text,
                        bbox=_rect_tuple(fitz.Rect(raw_span["bbox"]), page_rect),
                        font=str(raw_span.get("font") or ""),
                        size=float(raw_span.get("size") or 0),
                        flags=int(raw_span.get("flags") or 0),
                        color=int(raw_span.get("color") or 0),
                        chars=chars,
                    )
                )
            if not spans:
                continue
            text = "".join(span.text for span in spans)
            if not text.strip():
                continue
            lines.append(
                NativeLine(
                    block_index=block_index,
                    line_index=line_index,
                    bbox=_union_rect(span.bbox for span in spans),
                    text=text,
                    spans=tuple(spans),
                    writing_mode=int(raw_line.get("wmode") or 0),
                )
            )

    images: list[NativeImage] = []
    for image_index, info in enumerate(page.get_image_info(xrefs=True, hashes=True)):
        bbox = info.get("bbox")
        if not bbox:
            continue
        digest = info.get("digest")
        images.append(
            NativeImage(
                image_index=image_index,
                bbox=_rect_tuple(fitz.Rect(bbox), page_rect),
                xref=info.get("xref"),
                digest=digest.hex() if isinstance(digest, bytes) else None,
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
            )
        )

    clusters = tuple(_rect_tuple(rect, page_rect) for rect in page.cluster_drawings())
    return NativePage(
        page_num=page_num,
        width=round(page_rect.width, 4),
        height=round(page_rect.height, 4),
        rotation=page.rotation,
        lines=tuple(lines),
        images=tuple(images),
        tables=_extract_tables(page),
        drawing_clusters=clusters,
        rawdict_block_count=len(rawdict.get("blocks", [])),
    )


def page_render_hash(page: fitz.Page, *, dpi: int = 72) -> str:
    """A reproducible low-cost page render hash for lineage and cache diagnostics."""
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    return hashlib.sha256(pixmap.samples).hexdigest()
