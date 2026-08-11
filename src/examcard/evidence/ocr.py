"""Local OCR providers. The default provider keeps OCR independent from native PDF text."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from .engine_types import OCRLine, OCRPage


class OCRProvider(Protocol):
    name: str
    version: str

    def audit(self, page: fitz.Page, *, page_num: int, render_dpi: int) -> OCRPage: ...


def _package_version(package: str, fallback: str = "unknown") -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return fallback


class RapidOCRProvider:
    name = "rapidocr_onnxruntime"

    def __init__(self) -> None:
        self.version = _package_version("rapidocr-onnxruntime")
        self._engine: RapidOCR | None = None

    @property
    def engine(self) -> RapidOCR:
        if self._engine is None:
            self._engine = RapidOCR()
        return self._engine

    def audit(self, page: fitz.Page, *, page_num: int, render_dpi: int) -> OCRPage:
        pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        result, elapsed = self.engine(image)
        scale = render_dpi / 72.0
        lines: list[OCRLine] = []
        for item in result or []:
            polygon, text, confidence = item
            points = tuple(
                (round(float(point[0]) / scale + page.rect.x0, 4), round(float(point[1]) / scale + page.rect.y0, 4))
                for point in polygon
            )
            if not points or not str(text).strip():
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            lines.append(
                OCRLine(
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                    polygon=points,
                    text=str(text).strip(),
                    confidence=max(0.0, min(1.0, float(confidence))),
                )
            )
        elapsed_seconds = sum(float(value) for value in elapsed) if isinstance(elapsed, (list, tuple)) else float(elapsed or 0)
        return OCRPage(
            page_num=page_num,
            render_dpi=render_dpi,
            engine_name=self.name,
            engine_version=self.version,
            elapsed_seconds=elapsed_seconds,
            lines=tuple(lines),
        )
