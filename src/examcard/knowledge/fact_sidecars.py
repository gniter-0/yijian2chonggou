"""输入侧车与页面快照的强校验（拆分层 1）。

评审侧车与处置侧车成对校验，裁片清单路径必须仓根相对并逐哈希核对——
这条契约是 exams/ 布局与 conftest 的 repo_tmp_path 同根的由来。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..evidence.golden import GoldenSample, GoldenSampleRegistry
from ..evidence.schema import EvidenceKind, EvidenceUnit, PageEvidence

from .diagram.sidecar import DiagramReviewSidecar
from .diagram.disposition import DiagramDispositionSidecar
from .schema import DiagramDispositionTrace

from .fact_types import DiagramDispositionBinding
from .fact_support import ROOT, _read_json


def _load_selected_pages(*, sample: GoldenSample, evidence_root: Path) -> tuple[PageEvidence, ...]:
    pages: list[PageEvidence] = []
    for page_num in sample.selected_pages:
        path = evidence_root / sample.sample_id / "pages" / f"page_{page_num:04d}.json"
        if not path.is_file():
            raise ValueError(f"{sample.sample_id}: missing evidence snapshot {path}")
        page = PageEvidence.model_validate(_read_json(path))
        if page.page_num != page_num:
            raise ValueError(f"{sample.sample_id}: snapshot page mismatch for {page_num}")
        if any(unit.locator.document_sha256 != sample.pdf_sha256 for unit in page.evidence_units):
            raise ValueError(f"{sample.sample_id}: evidence source hash mismatch on page {page_num}")
        pages.append(page)
    return tuple(pages)


def _validate_diagram_review_sidecar(
    *,
    sidecar: DiagramReviewSidecar,
    registry: GoldenSampleRegistry,
    evidence_root: Path,
) -> None:
    if sidecar.registry_id != registry.registry_id:
        raise ValueError("diagram review sidecar registry does not match golden registry")
    if sidecar.material_profile_id != registry.material_profile_id:
        raise ValueError("diagram review sidecar material profile does not match golden registry")
    samples = {sample.sample_id: sample for sample in registry.samples}
    units_by_sample: dict[str, dict[str, EvidenceUnit]] = {}
    for entry in sidecar.entries:
        sample = samples.get(entry.sample_id)
        if sample is None or entry.pdf_sha256 != sample.pdf_sha256:
            raise ValueError(f"diagram review references an unknown or mismatched sample: {entry.review_id}")
        units = units_by_sample.get(entry.sample_id)
        if units is None:
            pages = _load_selected_pages(sample=sample, evidence_root=evidence_root)
            units = {unit.evidence_id: unit for page in pages for unit in page.evidence_units}
            units_by_sample[entry.sample_id] = units
        figure = units.get(entry.figure_evidence_id)
        if figure is None or figure.kind != EvidenceKind.FIGURE:
            raise ValueError(f"diagram review must bind a selected figure evidence unit: {entry.review_id}")
        if figure.locator.page_num != entry.page_num or figure.provenance.crop_path != entry.figure_crop_path:
            raise ValueError(f"diagram review figure binding mismatch: {entry.review_id}")
        crop_root = (evidence_root / entry.sample_id).resolve()
        crop_path = (crop_root / entry.figure_crop_path).resolve()
        if not crop_path.is_relative_to(crop_root) or not crop_path.is_file():
            raise ValueError(f"diagram review crop is missing or outside sample root: {entry.review_id}")
        if hashlib.sha256(crop_path.read_bytes()).hexdigest() != entry.figure_crop_sha256:
            raise ValueError(f"diagram review crop hash mismatch: {entry.review_id}")
        audit = entry.audit
        if audit is not None and audit.review_asset is not None:
            manifest_ref = audit.review_crop_manifest
            if manifest_ref is None:
                raise ValueError(f"diagram review high-resolution audit manifest is missing: {entry.review_id}")
            manifest_relative_path = Path(manifest_ref.manifest_path)
            if manifest_relative_path.is_absolute():
                raise ValueError(f"diagram review high-resolution manifest path must be repository-relative: {entry.review_id}")
            manifest_path = (ROOT / manifest_relative_path).resolve()
            if not manifest_path.is_relative_to(ROOT) or not manifest_path.is_file():
                raise ValueError(f"diagram review high-resolution manifest is missing or outside the repository: {entry.review_id}")
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_ref.manifest_sha256:
                raise ValueError(f"diagram review high-resolution manifest hash does not match: {entry.review_id}")
            manifest = _read_json(manifest_path)
            raw_entries = manifest.get("entries")
            if not isinstance(raw_entries, list):
                raise ValueError(f"diagram review high-resolution manifest entries must be a list: {entry.review_id}")
            manifest_entry = next(
                (item for item in raw_entries if isinstance(item, dict) and item.get("review_id") == entry.review_id),
                None,
            )
            if manifest_entry is None:
                raise ValueError(f"diagram review high-resolution manifest does not bind review: {entry.review_id}")
            asset = audit.review_asset
            expected_binding = {
                "sample_id": entry.sample_id,
                "pdf_sha256": entry.pdf_sha256,
                "page_num": entry.page_num,
                "figure_evidence_id": entry.figure_evidence_id,
                "original_crop_path": entry.figure_crop_path,
                "original_crop_sha256": entry.figure_crop_sha256,
                "review_crop_path": asset.review_crop_path,
                "review_crop_sha256": asset.review_crop_sha256,
                "render_dpi": asset.render_dpi,
                "margin_pt": asset.margin_pt,
                "figure_bbox": asset.figure_bbox.model_dump(mode="json"),
            }
            if any(manifest_entry.get(name) != value for name, value in expected_binding.items()):
                raise ValueError(f"diagram review high-resolution binding disagrees with manifest: {entry.review_id}")
            review_crop_relative_path = Path(asset.review_crop_path)
            if review_crop_relative_path.is_absolute():
                raise ValueError(f"diagram review high-resolution crop path must be repository-relative: {entry.review_id}")
            review_crop_path = (ROOT / review_crop_relative_path).resolve()
            if not review_crop_path.is_relative_to(ROOT) or not review_crop_path.is_file():
                raise ValueError(f"diagram review high-resolution crop is missing or outside the repository: {entry.review_id}")
            if hashlib.sha256(review_crop_path.read_bytes()).hexdigest() != asset.review_crop_sha256:
                raise ValueError(f"diagram review high-resolution crop hash does not match: {entry.review_id}")
        if any(candidate.evidence_id not in units for candidate in entry.candidate_nodes):
            raise ValueError(f"diagram review candidate does not belong to selected evidence: {entry.review_id}")
        if any(
            evidence_id not in units
            for node in entry.nodes
            for evidence_id in node.evidence_ids
        ):
                raise ValueError(f"diagram review node anchor does not belong to selected evidence: {entry.review_id}")


def _validate_diagram_disposition_sidecar(
    *,
    sidecar: DiagramDispositionSidecar,
    diagram_sidecar: DiagramReviewSidecar,
    diagram_sidecar_sha256: str,
    registry: GoldenSampleRegistry,
) -> dict[tuple[str, str], DiagramDispositionBinding]:
    if sidecar.registry_id != registry.registry_id:
        raise ValueError("diagram disposition sidecar registry does not match golden registry")
    if sidecar.material_profile_id != registry.material_profile_id:
        raise ValueError("diagram disposition sidecar material profile does not match golden registry")
    if sidecar.source_diagram_review_sidecar_id != diagram_sidecar.sidecar_id:
        raise ValueError("diagram disposition sidecar source ID does not match diagram review sidecar")
    if sidecar.source_diagram_review_sidecar_sha256 != diagram_sidecar_sha256:
        raise ValueError("diagram disposition sidecar source hash does not match diagram review sidecar")
    if sidecar.review_crop_manifest is None:
        raise ValueError("diagram disposition sidecar requires a high-resolution review crop manifest")

    manifest_relative_path = Path(sidecar.review_crop_manifest.manifest_path)
    if manifest_relative_path.is_absolute():
        raise ValueError("diagram disposition review crop manifest path must be repository-relative")
    manifest_path = (ROOT / manifest_relative_path).resolve()
    if not manifest_path.is_relative_to(ROOT) or not manifest_path.is_file():
        raise ValueError("diagram disposition review crop manifest is missing or outside the repository")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if manifest_sha256 != sidecar.review_crop_manifest.manifest_sha256:
        raise ValueError("diagram disposition review crop manifest hash does not match")
    manifest = _read_json(manifest_path)
    if manifest.get("source_sidecar_id") != diagram_sidecar.sidecar_id:
        raise ValueError("diagram disposition review crop manifest source ID does not match diagram review sidecar")
    if manifest.get("source_sidecar_sha256") != diagram_sidecar_sha256:
        raise ValueError("diagram disposition review crop manifest source hash does not match diagram review sidecar")
    if manifest.get("registry_id") != registry.registry_id or manifest.get("material_profile_id") != registry.material_profile_id:
        raise ValueError("diagram disposition review crop manifest registry binding does not match")
    raw_manifest_entries = manifest.get("entries")
    if not isinstance(raw_manifest_entries, list):
        raise ValueError("diagram disposition review crop manifest entries must be a list")
    manifest_entries = {
        str(item.get("review_id")): item
        for item in raw_manifest_entries
        if isinstance(item, dict) and isinstance(item.get("review_id"), str)
    }
    if len(manifest_entries) != len(raw_manifest_entries):
        raise ValueError("diagram disposition review crop manifest review IDs must be unique")

    drafts = {entry.review_id: entry for entry in diagram_sidecar.entries if entry.state.value == "draft"}
    entries = {entry.review_id: entry for entry in sidecar.entries}
    if set(entries) != set(drafts):
        raise ValueError("diagram disposition sidecar must classify exactly every draft diagram review")
    if set(manifest_entries) != set(drafts):
        raise ValueError("diagram disposition review crop manifest must cover exactly every draft diagram review")
    bindings: dict[tuple[str, str], DiagramDispositionBinding] = {}
    for review_id, disposition in entries.items():
        source = drafts[review_id]
        if (
            disposition.sample_id != source.sample_id
            or disposition.pdf_sha256 != source.pdf_sha256
            or disposition.page_num != source.page_num
            or disposition.figure_evidence_id != source.figure_evidence_id
            or disposition.figure_crop_path != source.figure_crop_path
            or disposition.figure_crop_sha256 != source.figure_crop_sha256
        ):
            raise ValueError(f"diagram disposition binding disagrees with draft diagram review: {review_id}")
        asset = disposition.review_asset
        if asset is None:
            raise ValueError(f"diagram disposition entry lacks a high-resolution review crop: {review_id}")
        manifest_entry = manifest_entries[review_id]
        expected_binding = {
            "sample_id": disposition.sample_id,
            "pdf_sha256": disposition.pdf_sha256,
            "page_num": disposition.page_num,
            "figure_evidence_id": disposition.figure_evidence_id,
            "original_crop_path": disposition.figure_crop_path,
            "original_crop_sha256": disposition.figure_crop_sha256,
            "review_crop_path": asset.review_crop_path,
            "review_crop_sha256": asset.review_crop_sha256,
            "render_dpi": asset.render_dpi,
            "margin_pt": asset.margin_pt,
            "figure_bbox": asset.figure_bbox.model_dump(mode="json"),
        }
        if any(manifest_entry.get(name) != value for name, value in expected_binding.items()):
            raise ValueError(f"diagram disposition high-resolution review binding disagrees with manifest: {review_id}")
        review_crop_relative_path = Path(asset.review_crop_path)
        if review_crop_relative_path.is_absolute():
            raise ValueError(f"diagram disposition review crop path must be repository-relative: {review_id}")
        review_crop_path = (ROOT / review_crop_relative_path).resolve()
        if not review_crop_path.is_relative_to(ROOT) or not review_crop_path.is_file():
            raise ValueError(f"diagram disposition review crop is missing or outside the repository: {review_id}")
        if hashlib.sha256(review_crop_path.read_bytes()).hexdigest() != asset.review_crop_sha256:
            raise ValueError(f"diagram disposition review crop hash does not match: {review_id}")
        bindings[(disposition.sample_id, disposition.figure_evidence_id)] = DiagramDispositionBinding(
            entry=disposition,
            trace=DiagramDispositionTrace(
                disposition_id=disposition.disposition_id,
                disposition=disposition.disposition.value,
                source_diagram_review_sidecar_id=sidecar.source_diagram_review_sidecar_id,
                source_diagram_review_sidecar_sha256=sidecar.source_diagram_review_sidecar_sha256,
                original_crop_path=disposition.figure_crop_path,
                original_crop_sha256=disposition.figure_crop_sha256,
                review_crop_manifest_path=sidecar.review_crop_manifest.manifest_path,
                review_crop_manifest_sha256=sidecar.review_crop_manifest.manifest_sha256,
                review_crop_path=asset.review_crop_path,
                review_crop_sha256=asset.review_crop_sha256,
                review_crop_render_dpi=asset.render_dpi,
                review_crop_bbox=asset.figure_bbox,
            ),
        )
    return bindings
