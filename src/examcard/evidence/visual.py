"""Figure crops and conservative diagram connector detection."""
from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np
from pydantic import Field

from examcard.materials.schema import StrictModel

from .engine_types import OCRLine


class DetectedDiagramEdge(StrictModel):
    bbox: tuple[float, float, float, float]
    source_text: str | None = None
    target_text: str | None = None
    direction: str = "unresolved"
    confidence: float = Field(ge=0, le=1)
    method: str


def save_page_crop(page: fitz.Page, bbox: tuple[float, float, float, float], path: Path, *, dpi: int = 250) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=dpi, clip=fitz.Rect(bbox), alpha=False).save(path)


def _rectangles(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if width < 80 or height < 20 or width / height < 1.6 or area < 700:
            continue
        approximation = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approximation) <= 8:
            boxes.append((x, y, width, height))
    return sorted(set(boxes), key=lambda item: (item[1], item[0]))


def _contains(box: tuple[int, int, int, int], x: float, y: float) -> bool:
    return box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]


def _ink_ratio(binary: np.ndarray, left: int, top: int, right: int, bottom: int) -> float:
    region = binary[max(0, top) : max(0, bottom), max(0, left) : max(0, right)]
    return float(np.mean(region > 0)) if region.size else 0.0


def _node_texts(
    boxes: list[tuple[int, int, int, int]],
    ocr_lines: tuple[OCRLine, ...],
    *,
    figure_bbox: tuple[float, float, float, float],
    scale: float,
) -> dict[tuple[int, int, int, int], str]:
    assigned: dict[tuple[int, int, int, int], list[str]] = {box: [] for box in boxes}
    for line in ocr_lines:
        center_x = ((line.bbox[0] + line.bbox[2]) / 2 - figure_bbox[0]) * scale
        center_y = ((line.bbox[1] + line.bbox[3]) / 2 - figure_bbox[1]) * scale
        matching = next((box for box in boxes if _contains(box, center_x, center_y)), None)
        if matching is not None:
            assigned[matching].append(line.text)
    return {box: "".join(texts).strip() for box, texts in assigned.items() if texts}


def _page_bbox(box: tuple[int, int, int, int], *, figure_bbox: tuple[float, float, float, float], scale: float) -> tuple[float, float, float, float]:
    return (
        round(figure_bbox[0] + box[0] / scale, 4),
        round(figure_bbox[1] + box[1] / scale, 4),
        round(figure_bbox[0] + (box[0] + box[2]) / scale, 4),
        round(figure_bbox[1] + (box[1] + box[3]) / scale, 4),
    )


def detect_diagram_edges(
    page: fitz.Page,
    *,
    figure_bbox: tuple[float, float, float, float],
    ocr_lines: tuple[OCRLine, ...],
    render_dpi: int = 300,
) -> tuple[DetectedDiagramEdge, ...]:
    """Emit only connectors seen between detected node rectangles.

    The edge direction is intentionally unresolved unless the local arrowhead density
    distinguishes one endpoint. Step 2 must not treat unresolved direction as a fact.
    """
    pixmap = page.get_pixmap(dpi=render_dpi, clip=fitz.Rect(figure_bbox), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    boxes = _rectangles(image)
    if len(boxes) < 2:
        return ()
    scale = render_dpi / 72.0
    texts = _node_texts(boxes, ocr_lines, figure_bbox=figure_bbox, scale=scale)
    edges: list[DetectedDiagramEdge] = []
    seen_pairs: set[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = set()
    for first in boxes:
        first_center_x = first[0] + first[2] / 2
        first_center_y = first[1] + first[3] / 2
        vertical_candidates = [
            second
            for second in boxes
            if second[1] >= first[1] + first[3]
            and abs((second[0] + second[2] / 2) - first_center_x) <= min(first[2], second[2]) * 0.18
        ]
        if vertical_candidates:
            second = min(vertical_candidates, key=lambda item: item[1])
            gap_top, gap_bottom = first[1] + first[3], second[1]
            corridor = max(8, min(first[2], second[2]) // 12)
            ink = _ink_ratio(binary, int(first_center_x - corridor), gap_top, int(first_center_x + corridor), gap_bottom)
            if ink >= 0.025:
                key = (first, second)
                seen_pairs.add(key)
                top_density = _ink_ratio(binary, int(first_center_x - corridor), gap_top, int(first_center_x + corridor), gap_top + 18)
                bottom_density = _ink_ratio(binary, int(first_center_x - corridor), gap_bottom - 18, int(first_center_x + corridor), gap_bottom)
                direction = "down" if bottom_density > top_density * 1.08 else "unresolved"
                source, target = (texts.get(first), texts.get(second)) if direction == "down" else (None, None)
                edges.append(
                    DetectedDiagramEdge(
                        bbox=(
                            round(figure_bbox[0] + (first_center_x - corridor) / scale, 4),
                            round(figure_bbox[1] + gap_top / scale, 4),
                            round(figure_bbox[0] + (first_center_x + corridor) / scale, 4),
                            round(figure_bbox[1] + gap_bottom / scale, 4),
                        ),
                        source_text=source,
                        target_text=target,
                        direction=direction,
                        confidence=0.78 if direction != "unresolved" else 0.62,
                        method="rectangle_connector_vertical",
                    )
                )
        horizontal_candidates = [
            second
            for second in boxes
            if second[0] >= first[0] + first[2]
            and abs((second[1] + second[3] / 2) - first_center_y) <= min(first[3], second[3]) * 0.45
        ]
        if horizontal_candidates:
            second = min(horizontal_candidates, key=lambda item: item[0])
            gap_left, gap_right = first[0] + first[2], second[0]
            corridor = max(8, min(first[3], second[3]) // 3)
            ink = _ink_ratio(binary, gap_left, int(first_center_y - corridor), gap_right, int(first_center_y + corridor))
            if ink >= 0.018:
                key = (first, second)
                if key in seen_pairs:
                    continue
                left_density = _ink_ratio(binary, gap_left, int(first_center_y - corridor), gap_left + 18, int(first_center_y + corridor))
                right_density = _ink_ratio(binary, gap_right - 18, int(first_center_y - corridor), gap_right, int(first_center_y + corridor))
                direction = "left" if left_density > right_density * 1.08 else "right" if right_density > left_density * 1.08 else "unresolved"
                if direction == "left":
                    source, target = texts.get(second), texts.get(first)
                elif direction == "right":
                    source, target = texts.get(first), texts.get(second)
                else:
                    source, target = None, None
                edges.append(
                    DetectedDiagramEdge(
                        bbox=(
                            round(figure_bbox[0] + gap_left / scale, 4),
                            round(figure_bbox[1] + (first_center_y - corridor) / scale, 4),
                            round(figure_bbox[0] + gap_right / scale, 4),
                            round(figure_bbox[1] + (first_center_y + corridor) / scale, 4),
                        ),
                        source_text=source,
                        target_text=target,
                        direction=direction,
                        confidence=0.76 if direction != "unresolved" else 0.6,
                        method="rectangle_connector_horizontal",
                    )
                )
    return tuple(edges)
