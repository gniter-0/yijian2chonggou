"""Fuse native spans, OCR observations, layout structures and visual review results."""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import fitz

from examcard.materials.schema import SemanticRole

from .engine_types import NativeLine, NativePage, OCRLine, OCRPage, VisualReviewResult
from .identity import make_conflict_id, make_page_id
from .layout import (
    bbox_intersects,
    bbox_iou,
    classify_native_line,
    classify_visual_text,
    equivalent_text,
    heading_level,
    normalized_compare_text,
)
from .schema import (
    BBox,
    EvidenceKind,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceConflict,
    SourceLocator,
    SourceQuality,
)
from .units import build_evidence_unit
from .visual import detect_diagram_edges, save_page_crop


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _center_in(bbox: tuple[float, float, float, float], container: tuple[float, float, float, float]) -> bool:
    x, y = _center(bbox)
    return container[0] <= x <= container[2] and container[1] <= y <= container[3]


def _union_bbox(values: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    items = list(values)
    return (
        min(item[0] for item in items),
        min(item[1] for item in items),
        max(item[2] for item in items),
        max(item[3] for item in items),
    )


def _native_ocr_alignment(native_lines: tuple[NativeLine, ...], ocr_lines: tuple[OCRLine, ...]) -> tuple[dict[int, int], set[int]]:
    assignments: dict[int, int] = {}
    used: set[int] = set()
    for native_index, native in enumerate(native_lines):
        native_text = normalized_compare_text(native.text)
        if not native_text:
            continue
        best: tuple[float, int] | None = None
        native_center = _center(native.bbox)
        native_height = max(1.0, native.bbox[3] - native.bbox[1])
        for ocr_index, ocr in enumerate(ocr_lines):
            if ocr_index in used:
                continue
            ocr_center = _center(ocr.bbox)
            vertical_delta = abs(native_center[1] - ocr_center[1])
            if vertical_delta > max(native_height * 1.8, 12):
                continue
            similarity = SequenceMatcher(None, native_text, normalized_compare_text(ocr.text)).ratio()
            spatial = bbox_iou(native.bbox, ocr.bbox)
            x_delta = abs(native_center[0] - ocr_center[0]) / max(1.0, native.bbox[2] - native.bbox[0])
            score = similarity * 0.7 + spatial * 0.2 + max(0.0, 1 - x_delta) * 0.1
            if best is None or score > best[0]:
                best = (score, ocr_index)
        if best and best[0] >= 0.58:
            assignments[native_index] = best[1]
            used.add(best[1])
    return assignments, used


def _crop_path(
    *,
    page: fitz.Page,
    bbox: tuple[float, float, float, float],
    output_dir: Path | None,
    relative_dir: str,
    stem: str,
) -> str | None:
    if output_dir is None:
        return None
    relative = Path(relative_dir) / f"p{page.number + 1:04d}_{stem}.png"
    save_page_crop(page, bbox, output_dir / relative)
    return relative.as_posix()


def _source_conflict(
    *,
    document_sha256: str,
    page_num: int,
    bbox: tuple[float, float, float, float],
    native_text: str,
    visual_text: str,
) -> SourceConflict:
    locator = SourceLocator(document_sha256=document_sha256, page_num=page_num, bbox=BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]))
    return SourceConflict(
        conflict_id=make_conflict_id(locator=locator, native_text=native_text, visual_text=visual_text),
        locator=locator,
        native_text=native_text,
        visual_text=visual_text,
    )


def fuse_page(
    *,
    page: fitz.Page,
    native: NativePage,
    ocr: OCRPage,
    document_sha256: str,
    config_hash: str,
    engine_versions: dict[str, str],
    repeated_noise: frozenset[str],
    output_dir: Path | None = None,
    visual_reviews: tuple[VisualReviewResult, ...] = (),
    minimum_ocr_confidence: float = 0.55,
    heading_stack: list[tuple[int, str]] | None = None,
) -> PageEvidence:
    """Build a single schema-valid page from all independent source channels."""
    document_id = "doc:"  # only used to construct a deterministic page ID below
    from .identity import make_document_id

    document_id = make_document_id(document_sha256)
    units = []
    conflicts: list[SourceConflict] = []
    order = 0
    outline = heading_stack if heading_stack is not None else []

    # Figures are emitted before OCR nodes so diagram evidence can preserve parent linkage.
    figure_parents: list[tuple[tuple[float, float, float, float], str, str | None]] = []
    page_area = native.width * native.height
    for image in native.images:
        area_ratio = ((image.bbox[2] - image.bbox[0]) * (image.bbox[3] - image.bbox[1])) / page_area
        image_text = f"image:{image.digest or image.image_index}"
        if area_ratio >= 0.9:
            kind, role, risk, must_cover = EvidenceKind.WATERMARK, SemanticRole.WATERMARK, {"background_image"}, False
        elif area_ratio >= 0.012:
            kind, role, risk, must_cover = EvidenceKind.FIGURE, SemanticRole.CORE, {"figure", "visual_only"}, True
        else:
            continue
        crop_path = _crop_path(
            page=page,
            bbox=image.bbox,
            output_dir=output_dir,
            relative_dir="crops",
            stem=f"image_{image.image_index:03d}",
        )
        unit = build_evidence_unit(
            document_sha256=document_sha256,
            page_num=native.page_num,
            kind=kind,
            bbox=image.bbox,
            raw_text="",
            canonical_text=image_text,
            semantic_role=role,
            source_quality=SourceQuality.VISUAL_ONLY,
            confidence=0.92 if kind == EvidenceKind.FIGURE else 0.7,
            order=order,
            source_channels={SourceChannel.VISION_STRUCTURE},
            engine_versions=engine_versions,
            config_hash=config_hash,
            region_id=f"image_{image.image_index}",
            crop_path=crop_path,
            risk_tags=risk,
            must_cover_by_source=must_cover,
            metadata={"xref": image.xref, "digest": image.digest, "width": image.width, "height": image.height, "area_ratio": round(area_ratio, 4)},
        )
        units.append(unit)
        order += 1
        if kind == EvidenceKind.FIGURE:
            figure_parents.append((image.bbox, unit.evidence_id, crop_path))

    alignment, used_ocr = _native_ocr_alignment(native.lines, ocr.lines)
    for native_index, line in enumerate(native.lines):
        kind, role, risk_tags, must_cover = classify_native_line(line, page=native, repeated_noise=repeated_noise)
        level = heading_level(line.text) if kind == EvidenceKind.HEADING else None
        if level is not None:
            while outline and outline[-1][0] >= level:
                outline.pop()
        active_parent = outline[-1][1] if outline else None
        matched = ocr.lines[alignment[native_index]] if native_index in alignment else None
        source_channels = {SourceChannel.NATIVE_TEXT}
        metadata: dict[str, object] = {
            "spans": [span.model_dump(mode="json") for span in line.spans],
            "writing_mode": line.writing_mode,
        }
        if matched is None:
            quality = SourceQuality.NATIVE_ONLY
            confidence = 0.94
        else:
            source_channels.add(SourceChannel.VISUAL_OCR)
            metadata["ocr_text"] = matched.text
            metadata["ocr_confidence"] = matched.confidence
            same_text = equivalent_text(
                line.text,
                matched.text,
                strict_formula=kind == EvidenceKind.FORMULA,
            )
            ratio = SequenceMatcher(None, normalized_compare_text(line.text), normalized_compare_text(matched.text)).ratio()
            if role in {
                SemanticRole.HEADER_FOOTER,
                SemanticRole.WATERMARK,
                SemanticRole.PAGE_NUMBER,
                SemanticRole.DECORATIVE,
            }:
                # Noise remains evidence, but OCR disagreement must not inflate
                # content-conflict queues intended for downstream publication gates.
                source_channels.discard(SourceChannel.VISUAL_OCR)
                quality = SourceQuality.NATIVE_ONLY
                confidence = 0.9
            elif same_text:
                quality = SourceQuality.VERIFIED_AGREEMENT
                confidence = min(0.99, 0.85 + matched.confidence * 0.14)
            elif ratio >= 0.72:
                quality = SourceQuality.CONFLICT
                confidence = min(0.85, matched.confidence)
                conflicts.append(
                    _source_conflict(
                        document_sha256=document_sha256,
                        page_num=native.page_num,
                        bbox=_union_bbox((line.bbox, matched.bbox)),
                        native_text=line.text,
                        visual_text=matched.text,
                    )
                )
            else:
                quality = SourceQuality.NATIVE_ONLY
                confidence = 0.84
                used_ocr.discard(alignment[native_index])
        crop_path = None
        if kind == EvidenceKind.FORMULA:
            crop_path = _crop_path(
                page=page,
                bbox=line.bbox,
                output_dir=output_dir,
                relative_dir="crops",
                stem=f"formula_{native_index:03d}",
            )
        unit = build_evidence_unit(
            document_sha256=document_sha256,
            page_num=native.page_num,
            kind=kind,
            bbox=line.bbox,
            raw_text=line.text,
            canonical_text=line.text,
            semantic_role=role,
            source_quality=quality,
            confidence=confidence,
            order=order,
            source_channels=source_channels,
            engine_versions=engine_versions,
            config_hash=config_hash,
            parent_id=active_parent,
            risk_tags=set(risk_tags),
            must_cover_by_source=must_cover,
            native_block_index=line.block_index,
            native_line_index=line.line_index,
            crop_path=crop_path,
            metadata=metadata,
        )
        units.append(unit)
        if level is not None:
            outline.append((level, unit.evidence_id))
        order += 1

    # Preserve table geometry as a tree instead of relying on paragraph reconstruction later.
    for table in native.tables:
        table_crop = _crop_path(
            page=page,
            bbox=table.bbox,
            output_dir=output_dir,
            relative_dir="crops",
            stem=f"table_{table.table_index:03d}",
        )
        table_unit = build_evidence_unit(
            document_sha256=document_sha256,
            page_num=native.page_num,
            kind=EvidenceKind.TABLE,
            bbox=table.bbox,
            raw_text="\n".join(cell.text for cell in table.cells if cell.text),
            canonical_text="\n".join(cell.text for cell in table.cells if cell.text),
            semantic_role=SemanticRole.CORE,
            source_quality=SourceQuality.NATIVE_ONLY,
            confidence=0.92,
            order=order,
            source_channels={SourceChannel.NATIVE_TEXT, SourceChannel.LAYOUT},
            engine_versions=engine_versions,
            config_hash=config_hash,
            region_id=f"table_{table.table_index}",
            crop_path=table_crop,
            risk_tags={"table"},
            must_cover_by_source=True,
            metadata={"rows": table.row_count, "columns": table.column_count},
        )
        units.append(table_unit)
        order += 1
        for row_index in range(table.row_count):
            row_cells = [cell for cell in table.cells if cell.row == row_index and cell.bbox is not None]
            if not row_cells:
                continue
            row_bbox = _union_bbox(cell.bbox for cell in row_cells if cell.bbox is not None)
            row_unit = build_evidence_unit(
                document_sha256=document_sha256,
                page_num=native.page_num,
                kind=EvidenceKind.TABLE_ROW,
                bbox=row_bbox,
                raw_text=" | ".join(cell.text for cell in row_cells),
                canonical_text=" | ".join(cell.text for cell in row_cells),
                semantic_role=SemanticRole.CORE,
                source_quality=SourceQuality.NATIVE_ONLY,
                confidence=0.9,
                order=order,
                source_channels={SourceChannel.NATIVE_TEXT, SourceChannel.LAYOUT},
                engine_versions=engine_versions,
                config_hash=config_hash,
                parent_id=table_unit.evidence_id,
                region_id=f"table_{table.table_index}_row_{row_index}",
                risk_tags={"table"},
                metadata={"row": row_index},
            )
            units.append(row_unit)
            order += 1
            for cell in row_cells:
                cell_unit = build_evidence_unit(
                    document_sha256=document_sha256,
                    page_num=native.page_num,
                    kind=EvidenceKind.TABLE_CELL,
                    bbox=cell.bbox,
                    raw_text=cell.text,
                    canonical_text=cell.text,
                    semantic_role=SemanticRole.CORE,
                    source_quality=SourceQuality.NATIVE_ONLY,
                    confidence=0.9,
                    order=order,
                    source_channels={SourceChannel.NATIVE_TEXT, SourceChannel.LAYOUT},
                    engine_versions=engine_versions,
                    config_hash=config_hash,
                    parent_id=row_unit.evidence_id,
                    region_id=f"table_{table.table_index}_r{cell.row}_c{cell.column}",
                    risk_tags={"table"},
                    must_cover_by_source=bool(cell.text),
                    metadata={"row": cell.row, "column": cell.column, "row_span": cell.row_span, "col_span": cell.col_span},
                )
                units.append(cell_unit)
                order += 1

    figure_ocr: dict[str, list[OCRLine]] = {figure_id: [] for _, figure_id, _ in figure_parents}
    for ocr_index, ocr_line in enumerate(ocr.lines):
        if ocr_index in used_ocr or ocr_line.confidence < minimum_ocr_confidence:
            continue
        figure = next((item for item in figure_parents if _center_in(ocr_line.bbox, item[0])), None)
        if figure is not None:
            figure_bbox, parent_id, crop_path = figure
            figure_ocr[parent_id].append(ocr_line)
            kind, role, risk_tags, must_cover = (
                EvidenceKind.DIAGRAM_NODE,
                SemanticRole.CORE,
                {"diagram_node", "visual_only"},
                True,
            )
            parent = parent_id
            region_id = f"diagram_{parent_id[-8:]}"
            provenance_crop = crop_path
        else:
            kind, role, risk_tags, must_cover = classify_visual_text(ocr_line.text, bbox=ocr_line.bbox, page=native)
            parent = None
            region_id = None
            provenance_crop = None
            if "=" in ocr_line.text or "＝" in ocr_line.text:
                kind, risk_tags, must_cover = EvidenceKind.FORMULA, set(risk_tags) | {"formula", "numeric"}, True
        unit = build_evidence_unit(
            document_sha256=document_sha256,
            page_num=native.page_num,
            kind=kind,
            bbox=ocr_line.bbox,
            raw_text=ocr_line.text,
            canonical_text=ocr_line.text,
            semantic_role=role,
            source_quality=SourceQuality.VISUAL_ONLY,
            confidence=ocr_line.confidence,
            order=order,
            source_channels={SourceChannel.VISUAL_OCR},
            engine_versions=engine_versions,
            config_hash=config_hash,
            parent_id=parent,
            region_id=region_id,
            crop_path=provenance_crop,
            risk_tags=set(risk_tags),
            must_cover_by_source=must_cover,
            metadata={"ocr_polygon": ocr_line.polygon, "ocr_confidence": ocr_line.confidence},
        )
        units.append(unit)
        order += 1

    for figure_bbox, parent_id, crop_path in figure_parents:
        edges = detect_diagram_edges(page, figure_bbox=figure_bbox, ocr_lines=tuple(figure_ocr[parent_id]))
        for edge_index, edge in enumerate(edges):
            raw_text = f"{edge.source_text} -> {edge.target_text}" if edge.source_text and edge.target_text else ""
            edge_unit = build_evidence_unit(
                document_sha256=document_sha256,
                page_num=native.page_num,
                kind=EvidenceKind.DIAGRAM_EDGE,
                bbox=edge.bbox,
                raw_text=raw_text,
                canonical_text=raw_text,
                semantic_role=SemanticRole.CORE,
                source_quality=SourceQuality.VISUAL_ONLY,
                confidence=edge.confidence,
                order=order,
                source_channels={SourceChannel.VISION_STRUCTURE},
                engine_versions=engine_versions,
                config_hash=config_hash,
                parent_id=parent_id,
                region_id=f"diagram_edge_{parent_id[-8:]}_{edge_index}",
                crop_path=crop_path,
                risk_tags={"diagram_edge", "direction_unresolved"} if edge.direction == "unresolved" else {"diagram_edge"},
                must_cover_by_source=True,
                metadata=edge.model_dump(mode="json"),
            )
            units.append(edge_unit)
            order += 1

    # High-risk Vision review never rewrites native evidence. It adds a conflict and marks
    # overlapping units as conflict for downstream blocking/review.
    review_regions: list[tuple[float, float, float, float]] = []
    for review in visual_reviews:
        if normalized_compare_text(review.native_text) in normalized_compare_text(review.visual_text):
            continue
        review_regions.append(review.bbox)
        conflicts.append(
            _source_conflict(
                document_sha256=document_sha256,
                page_num=native.page_num,
                bbox=review.bbox,
                native_text=review.native_text,
                visual_text=review.visual_text,
            )
        )
    if review_regions:
        rewritten = []
        for unit in units:
            unit_bbox = unit.locator.bbox
            if unit.semantic_role in {
                SemanticRole.HEADER_FOOTER,
                SemanticRole.WATERMARK,
                SemanticRole.PAGE_NUMBER,
                SemanticRole.DECORATIVE,
            } or unit_bbox is None or not any(
                bbox_intersects((unit_bbox.x0, unit_bbox.y0, unit_bbox.x1, unit_bbox.y1), region)
                for region in review_regions
            ):
                rewritten.append(unit)
                continue
            provenance = unit.provenance.model_copy(
                update={
                    "source_channels": unit.provenance.source_channels | {SourceChannel.VISION_STRUCTURE},
                    "engine_versions": {**unit.provenance.engine_versions, "vision": "review"},
                }
            )
            rewritten.append(
                unit.model_copy(
                    update={
                        "source_quality": SourceQuality.CONFLICT,
                        "provenance": provenance,
                        "risk_tags": unit.risk_tags | {"vision_conflict"},
                    }
                )
            )
        units = rewritten

    native_chars = sum(len(normalized_compare_text(line.text)) for line in native.lines)
    unmatched_visual_chars = sum(
        len(normalized_compare_text(line.text))
        for index, line in enumerate(ocr.lines)
        if index not in used_ocr and line.confidence >= minimum_ocr_confidence
    )
    total_chars = native_chars + unmatched_visual_chars
    quality = PageQuality(
        native_text_coverage=native_chars / total_chars if total_chars else 1.0,
        ocr_alignment_rate=len(used_ocr) / len(ocr.lines) if ocr.lines else 1.0,
        unresolved_regions=0,
        conflict_count=len(conflicts),
    )
    return PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=native.page_num),
        page_num=native.page_num,
        width=native.width,
        height=native.height,
        rotation=native.rotation,
        evidence_units=tuple(units),
        source_conflicts=tuple(conflicts),
        quality=quality,
    )
