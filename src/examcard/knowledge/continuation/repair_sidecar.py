"""Reviewed, non-destructive table-grid repairs for Step 2A projections.

Evidence table text and native geometry remain immutable.  A reviewed repair
sidecar may correct only declared cell spans after binding every source table,
row, cell, bbox, crop, and source-manifest hash.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from ...evidence.schema import BBox, EvidenceKind, PageEvidence
from ...materials.schema import StrictModel, V2_SCHEMA_VERSION


TABLE_REPAIR_SIDECAR_VERSION = "table_repair_review_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _text_sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def make_table_repair_id(
    *,
    sample_id: str,
    page_num: int,
    table_evidence_id: str,
    source_manifest_sha256: str,
    crop_sha256: str,
) -> str:
    digest = _canonical_sha256(
        {
            "version": TABLE_REPAIR_SIDECAR_VERSION,
            "sample_id": sample_id,
            "page_num": page_num,
            "table_evidence_id": table_evidence_id,
            "source_manifest_sha256": source_manifest_sha256,
            "crop_sha256": crop_sha256,
        }
    )
    return f"table-repair:{digest[:24]}"


class TableRepairReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class TableRepairRowBinding(StrictModel):
    evidence_id: str
    row_index: int = Field(ge=0)


class TableRepairCellBinding(StrictModel):
    evidence_id: str
    row_evidence_id: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    source_row_span: int = Field(ge=1)
    source_col_span: int = Field(ge=1)
    reviewed_row_span: int = Field(ge=1)
    reviewed_col_span: int = Field(ge=1)
    canonical_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    bbox: BBox


class TableRepairReviewSidecar(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    sidecar_version: str = TABLE_REPAIR_SIDECAR_VERSION
    repair_id: str = Field(pattern=r"^table-repair:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    page_num: int = Field(ge=1)
    state: TableRepairReviewState
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=16)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_evidence_id: str
    table_bbox: BBox
    crop_path: str = Field(min_length=1)
    crop_sha256: str = Field(pattern=_SHA256_PATTERN)
    declared_row_count: int = Field(ge=1)
    declared_column_count: int = Field(ge=1)
    rows: tuple[TableRepairRowBinding, ...]
    cells: tuple[TableRepairCellBinding, ...]

    @model_validator(mode="after")
    def validate_sidecar(self) -> "TableRepairReviewSidecar":
        if self.sidecar_version != TABLE_REPAIR_SIDECAR_VERSION:
            raise ValueError("unsupported table repair sidecar version")
        expected_id = make_table_repair_id(
            sample_id=self.sample_id,
            page_num=self.page_num,
            table_evidence_id=self.table_evidence_id,
            source_manifest_sha256=self.source_manifest_sha256,
            crop_sha256=self.crop_sha256,
        )
        if self.repair_id != expected_id:
            raise ValueError("table repair ID does not match its immutable source binding")
        if self.state == TableRepairReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed table repairs require reviewer and reviewed_at_utc")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft table repairs cannot contain reviewer metadata")
        row_ids = [row.evidence_id for row in self.rows]
        row_indexes = [row.row_index for row in self.rows]
        if len(row_ids) != len(set(row_ids)) or len(row_indexes) != len(set(row_indexes)):
            raise ValueError("table repair rows must be unique")
        if set(row_indexes) != set(range(self.declared_row_count)):
            raise ValueError("table repair rows must exactly cover declared geometry")
        rows_by_id = {row.evidence_id: row.row_index for row in self.rows}
        cell_ids = [cell.evidence_id for cell in self.cells]
        if not self.cells or len(cell_ids) != len(set(cell_ids)):
            raise ValueError("table repair cells must be unique and non-empty")
        covered: set[tuple[int, int]] = set()
        for cell in self.cells:
            if rows_by_id.get(cell.row_evidence_id) != cell.row_index:
                raise ValueError("table repair cell must bind its source row")
            for row_index in range(cell.row_index, cell.row_index + cell.reviewed_row_span):
                for column_index in range(cell.column_index, cell.column_index + cell.reviewed_col_span):
                    if row_index >= self.declared_row_count or column_index >= self.declared_column_count:
                        raise ValueError("reviewed table repair span exceeds declared geometry")
                    coordinate = (row_index, column_index)
                    if coordinate in covered:
                        raise ValueError("reviewed table repair spans overlap")
                    covered.add(coordinate)
        expected = {
            (row_index, column_index)
            for row_index in range(self.declared_row_count)
            for column_index in range(self.declared_column_count)
        }
        if covered != expected:
            raise ValueError("reviewed table repair spans must exactly cover declared geometry")
        return self


def load_table_repair_sidecar(path: Path) -> TableRepairReviewSidecar:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return TableRepairReviewSidecar.model_validate(raw)


def verify_reviewed_table_repair(
    *,
    sidecar: TableRepairReviewSidecar,
    page: PageEvidence,
    source_manifest_path: Path,
    repository_root: Path,
) -> dict[str, tuple[int, int]]:
    """Return reviewed span overrides only after exact immutable-source checks."""
    if sidecar.state != TableRepairReviewState.REVIEWED:
        raise ValueError("only reviewed table repair sidecars may alter a semantic projection")
    source_manifest_path = Path(source_manifest_path)
    if sidecar.source_manifest_sha256 != _sha256_bytes(source_manifest_path.read_bytes()):
        raise ValueError("table repair source manifest hash does not match")
    if sidecar.page_num != page.page_num:
        raise ValueError("table repair page does not match the projected page")
    units = {unit.evidence_id: unit for unit in page.evidence_units}
    table = units.get(sidecar.table_evidence_id)
    if table is None or table.kind != EvidenceKind.TABLE:
        raise ValueError("table repair table Evidence is absent or has the wrong kind")
    if table.locator.bbox != sidecar.table_bbox:
        raise ValueError("table repair bbox does not match source table Evidence")
    if int(table.metadata.get("rows", 0)) != sidecar.declared_row_count or int(table.metadata.get("columns", 0)) != sidecar.declared_column_count:
        raise ValueError("table repair declared geometry does not match source table Evidence")
    expected_crop = (source_manifest_path.parent.parent / str(table.provenance.crop_path or "")).resolve()
    bound_crop = (Path(repository_root) / sidecar.crop_path).resolve()
    if not table.provenance.crop_path or expected_crop != bound_crop:
        raise ValueError("table repair crop path does not bind the source table crop")
    if not bound_crop.is_file() or _sha256_bytes(bound_crop.read_bytes()) != sidecar.crop_sha256:
        raise ValueError("table repair crop hash does not match")

    rows = [unit for unit in page.evidence_units if unit.kind == EvidenceKind.TABLE_ROW and unit.parent_id == table.evidence_id]
    rows_by_id = {row.evidence_id: row for row in rows}
    if set(rows_by_id) != {row.evidence_id for row in sidecar.rows}:
        raise ValueError("table repair row Evidence set does not exactly match source table")
    for binding in sidecar.rows:
        if int(rows_by_id[binding.evidence_id].metadata.get("row", -1)) != binding.row_index:
            raise ValueError("table repair row index does not match source Evidence")
    cells = [unit for row in rows for unit in page.evidence_units if unit.kind == EvidenceKind.TABLE_CELL and unit.parent_id == row.evidence_id]
    cells_by_id = {cell.evidence_id: cell for cell in cells}
    if set(cells_by_id) != {cell.evidence_id for cell in sidecar.cells}:
        raise ValueError("table repair cell Evidence set does not exactly match source table")
    for binding in sidecar.cells:
        cell = cells_by_id[binding.evidence_id]
        if cell.parent_id != binding.row_evidence_id:
            raise ValueError("table repair cell parent row does not match source Evidence")
        source_geometry = (
            int(cell.metadata.get("row", -1)),
            int(cell.metadata.get("column", -1)),
            int(cell.metadata.get("row_span", 1)),
            int(cell.metadata.get("col_span", 1)),
        )
        if source_geometry != (
            binding.row_index,
            binding.column_index,
            binding.source_row_span,
            binding.source_col_span,
        ):
            raise ValueError("table repair source cell geometry does not match immutable Evidence")
        if _text_sha256(cell.canonical_text) != binding.canonical_text_sha256 or cell.locator.bbox != binding.bbox:
            raise ValueError("table repair cell text hash or bbox does not match immutable Evidence")
    return {binding.evidence_id: (binding.reviewed_row_span, binding.reviewed_col_span) for binding in sidecar.cells}
