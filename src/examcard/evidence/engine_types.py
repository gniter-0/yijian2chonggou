"""Internal, JSON-cacheable data structures for the Step 1 Evidence Engine."""
from __future__ import annotations

from pydantic import Field

from examcard.materials.schema import StrictModel


class NativeChar(StrictModel):
    char: str
    bbox: tuple[float, float, float, float]


class NativeSpan(StrictModel):
    text: str
    bbox: tuple[float, float, float, float]
    font: str
    size: float = Field(ge=0)
    flags: int = 0
    color: int = 0
    chars: tuple[NativeChar, ...] = ()


class NativeLine(StrictModel):
    block_index: int = Field(ge=0)
    line_index: int = Field(ge=0)
    bbox: tuple[float, float, float, float]
    text: str
    spans: tuple[NativeSpan, ...]
    writing_mode: int = 0


class NativeImage(StrictModel):
    image_index: int = Field(ge=0)
    bbox: tuple[float, float, float, float]
    xref: int | None = None
    digest: str | None = None
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class NativeTableCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: str = ""
    bbox: tuple[float, float, float, float] | None = None
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)


class NativeTable(StrictModel):
    table_index: int = Field(ge=0)
    bbox: tuple[float, float, float, float]
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    cells: tuple[NativeTableCell, ...]


class NativePage(StrictModel):
    page_num: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int = 0
    lines: tuple[NativeLine, ...]
    images: tuple[NativeImage, ...]
    tables: tuple[NativeTable, ...]
    drawing_clusters: tuple[tuple[float, float, float, float], ...] = ()
    rawdict_block_count: int = Field(ge=0)


class OCRLine(StrictModel):
    bbox: tuple[float, float, float, float]
    polygon: tuple[tuple[float, float], ...]
    text: str
    confidence: float = Field(ge=0, le=1)


class OCRPage(StrictModel):
    page_num: int = Field(ge=1)
    render_dpi: int = Field(ge=72)
    engine_name: str
    engine_version: str
    elapsed_seconds: float = Field(ge=0)
    lines: tuple[OCRLine, ...]


class VisualReviewHint(StrictModel):
    hint_id: str
    page_num: int = Field(ge=1)
    observed_text: str = Field(min_length=1)
    description: str = ""


class VisualReviewResult(StrictModel):
    hint_id: str
    bbox: tuple[float, float, float, float]
    native_text: str
    visual_text: str
    provider_name: str
    provider_version: str
    uncertain: bool = False
    crop_path: str | None = None
