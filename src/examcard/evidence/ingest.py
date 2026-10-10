"""Step 1 V2 orchestration: PDF -> canonical page evidence / manifest."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from pydantic import Field

from examcard.materials.schema import MaterialProfile, StrictModel, V2_SCHEMA_VERSION

from .cache import ArtifactCache, stable_config_hash
from .engine_types import NativePage, OCRPage, VisualReviewHint, VisualReviewResult
from .fusion import fuse_page
from .identity import make_cache_key, make_document_id, make_manifest_id
from .layout import detect_repeated_noise, expand_bbox, normalized_compare_text
from .native import extract_native_page, page_render_hash
from .ocr import OCRProvider, RapidOCRProvider
from .quality import summarize_pages
from .schema import DocumentEvidenceManifest, EvidenceLineage, PageEvidence, SourceDocument
from .units import utc_now
from .vision import OpenAIVisionVerifier


class EvidenceEngineConfig(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    render_dpi: int = Field(default=200, ge=144, le=600)
    crop_dpi: int = Field(default=250, ge=144, le=600)
    minimum_ocr_confidence: float = Field(default=0.55, ge=0, le=1)
    # Bump whenever fusion or semantic classification changes so old extraction
    # artifacts and their derived evidence cannot silently retain stale roles.
    cache_schema_version: str = "evidence-engine-v3"
    enable_visual_review: bool = True


@dataclass(frozen=True)
class EvidenceRunResult:
    document_sha256: str
    page_render_hashes: dict[int, str]
    pages: tuple[PageEvidence, ...]
    report: dict[str, object]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class EvidenceEngine:
    """Isolated Step 1 engine. It has no dependency on Step 2 or V1 output paths."""

    def __init__(
        self,
        *,
        profile: MaterialProfile,
        cache_root: Path,
        config: EvidenceEngineConfig | None = None,
        ocr_provider: OCRProvider | None = None,
        vision_verifier: OpenAIVisionVerifier | None = None,
        cache_enabled: bool = True,
    ) -> None:
        self.profile = profile
        self.config = config or EvidenceEngineConfig()
        self.ocr_provider = ocr_provider or RapidOCRProvider()
        self.vision_verifier = vision_verifier
        self.cache = ArtifactCache(cache_root, enabled=cache_enabled)
        self.config_hash = stable_config_hash(
            {
                "config": self.config.model_dump(mode="json"),
                "profile": {"id": profile.profile_id, "revision": profile.revision},
                "ocr": {"name": self.ocr_provider.name, "version": self.ocr_provider.version},
                "vision": (
                    {"name": vision_verifier.name, "version": vision_verifier.version}
                    if vision_verifier is not None
                    else None
                ),
            }
        )

    def _native_page(self, page: pymupdf.Page, *, document_sha256: str, page_num: int) -> NativePage:
        key = make_cache_key(
            pdf_sha256=document_sha256,
            page_num=page_num,
            region_bbox=None,
            engine_name="pymupdf_rawdict",
            engine_version=pymupdf.VersionBind,
            config_hash=self.config_hash,
            artifact_schema_version=self.config.cache_schema_version,
        )
        cached = self.cache.get_model(key, NativePage)
        if cached is not None:
            return cached
        extracted = extract_native_page(page, page_num=page_num)
        self.cache.put_model(key, extracted)
        return extracted

    def _ocr_page(self, page: pymupdf.Page, *, document_sha256: str, page_num: int) -> OCRPage:
        key = make_cache_key(
            pdf_sha256=document_sha256,
            page_num=page_num,
            region_bbox=None,
            engine_name=self.ocr_provider.name,
            engine_version=self.ocr_provider.version,
            config_hash=self.config_hash,
            artifact_schema_version=self.config.cache_schema_version,
        )
        cached = self.cache.get_model(key, OCRPage)
        if cached is not None:
            return cached
        audited = self.ocr_provider.audit(page, page_num=page_num, render_dpi=self.config.render_dpi)
        self.cache.put_model(key, audited)
        return audited

    @staticmethod
    def _find_review_bbox(native: NativePage, observed_text: str) -> tuple[float, float, float, float] | None:
        observed = normalized_compare_text(observed_text)
        # rawdict may list footer blocks before body blocks. Review crops must follow
        # visual reading order rather than the PDF object's incidental order.
        lines = tuple(sorted(native.lines, key=lambda line: (line.bbox[1], line.bbox[0])))
        candidates: list[tuple[float, tuple[float, float, float, float]]] = []
        for start in range(len(lines)):
            combined = ""
            boxes: list[tuple[float, float, float, float]] = []
            for end in range(start, min(len(lines), start + 4)):
                combined += normalized_compare_text(lines[end].text)
                boxes.append(lines[end].bbox)
                if observed in combined:
                    bbox = (
                        min(box[0] for box in boxes),
                        min(box[1] for box in boxes),
                        max(box[2] for box in boxes),
                        max(box[3] for box in boxes),
                    )
                    candidates.append(((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), bbox))
        if not candidates:
            return None
        _, best = min(candidates, key=lambda item: item[0])
        return expand_bbox(best, margin=8, width=native.width, height=native.height)

    def _visual_reviews(
        self,
        *,
        page: pymupdf.Page,
        native: NativePage,
        document_sha256: str,
        hints: tuple[VisualReviewHint, ...],
        output_dir: Path | None,
    ) -> tuple[VisualReviewResult, ...]:
        if not hints or self.vision_verifier is None or not self.config.enable_visual_review:
            return ()
        results: list[VisualReviewResult] = []
        for hint in hints:
            bbox = self._find_review_bbox(native, hint.observed_text)
            if bbox is None:
                continue
            crop_path = None
            png_bytes = page.get_pixmap(dpi=self.config.crop_dpi, clip=pymupdf.Rect(bbox), alpha=False).tobytes("png")
            if output_dir is not None:
                relative = Path("crops") / f"p{native.page_num:04d}_review_{hint.hint_id}.png"
                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(png_bytes)
                crop_path = relative.as_posix()
            from .schema import BBox

            key = make_cache_key(
                pdf_sha256=document_sha256,
                page_num=native.page_num,
                region_bbox=BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                engine_name=self.vision_verifier.name,
                engine_version=self.vision_verifier.version,
                config_hash=stable_config_hash(
                    {
                        "engine": self.config_hash,
                        "review_locator_version": "2",
                        "hint": hint.hint_id,
                        "observed": hint.observed_text,
                    }
                ),
                artifact_schema_version=self.config.cache_schema_version,
            )
            cached = self.cache.get_model(key, VisualReviewResult)
            if cached is not None:
                results.append(cached.model_copy(update={"crop_path": crop_path}))
                continue
            set_context = getattr(self.vision_verifier, "set_review_context", None)
            if callable(set_context):
                set_context(
                    hint_id=hint.hint_id,
                    page_num=native.page_num,
                    observed_text=hint.observed_text,
                    crop_sha256=hashlib.sha256(png_bytes).hexdigest(),
                )
            verification = self.vision_verifier.verify(png_bytes)
            result = VisualReviewResult(
                hint_id=hint.hint_id,
                bbox=bbox,
                native_text=hint.observed_text,
                visual_text=verification.visible_text,
                provider_name=self.vision_verifier.name,
                provider_version=self.vision_verifier.version,
                uncertain=verification.uncertain,
                crop_path=crop_path,
            )
            self.cache.put_model(key, result)
            results.append(result)
        return tuple(results)

    def process_pages(
        self,
        pdf_path: Path,
        *,
        page_numbers: tuple[int, ...] | list[int] | None = None,
        output_dir: Path | None = None,
        review_hints: tuple[VisualReviewHint, ...] = (),
    ) -> EvidenceRunResult:
        started = time.perf_counter()
        pdf_path = pdf_path.resolve()
        document_sha256 = _sha256_file(pdf_path)
        hints_by_page: dict[int, list[VisualReviewHint]] = {}
        for hint in review_hints:
            hints_by_page.setdefault(hint.page_num, []).append(hint)
        with pymupdf.open(pdf_path) as document:
            requested = tuple(page_numbers or range(1, document.page_count + 1))
            if not requested:
                raise ValueError("at least one page is required")
            if len(requested) != len(set(requested)) or min(requested) < 1 or max(requested) > document.page_count:
                raise ValueError("page_numbers must be unique and within the source PDF")
            native_pages: dict[int, NativePage] = {}
            render_hashes: dict[int, str] = {}
            for page_num in requested:
                page = document[page_num - 1]
                native_pages[page_num] = self._native_page(page, document_sha256=document_sha256, page_num=page_num)
                render_hashes[page_num] = page_render_hash(page)
            repeated_noise = detect_repeated_noise(tuple(native_pages[number] for number in requested))
            pages: list[PageEvidence] = []
            engine_versions = {
                "pymupdf": pymupdf.VersionBind,
                "ocr": f"{self.ocr_provider.name}@{self.ocr_provider.version}",
            }
            if self.vision_verifier is not None:
                engine_versions["vision"] = f"{self.vision_verifier.name}@{self.vision_verifier.version}"
            heading_stack: list[tuple[int, str]] = []
            previous_page_num: int | None = None
            for page_num in requested:
                if previous_page_num is not None and page_num != previous_page_num + 1:
                    # Selected-page golden runs may skip chapter boundaries. Never infer a
                    # parent from an omitted page; full-document runs remain continuous.
                    heading_stack.clear()
                page = document[page_num - 1]
                native = native_pages[page_num]
                ocr = self._ocr_page(page, document_sha256=document_sha256, page_num=page_num)
                reviews = self._visual_reviews(
                    page=page,
                    native=native,
                    document_sha256=document_sha256,
                    hints=tuple(hints_by_page.get(page_num, [])),
                    output_dir=output_dir,
                )
                fused = fuse_page(
                    page=page,
                    native=native,
                    ocr=ocr,
                    document_sha256=document_sha256,
                    config_hash=self.config_hash,
                    engine_versions=engine_versions,
                    repeated_noise=repeated_noise,
                    output_dir=output_dir,
                    visual_reviews=reviews,
                    minimum_ocr_confidence=self.config.minimum_ocr_confidence,
                    heading_stack=heading_stack,
                )
                pages.append(fused)
                previous_page_num = page_num
                if output_dir is not None:
                    _write_json(output_dir / "pages" / f"page_{page_num:04d}.json", fused.model_dump(mode="json"))
        elapsed = time.perf_counter() - started
        report = summarize_pages(
            tuple(pages),
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            elapsed_seconds=elapsed,
        )
        report.update(
            {
                "schema_version": V2_SCHEMA_VERSION,
                "document_sha256": document_sha256,
                "profile_id": self.profile.profile_id,
                "profile_revision": self.profile.revision,
                "config_hash": self.config_hash,
                "selected_pages": list(requested),
                "page_render_hashes": {str(number): digest for number, digest in render_hashes.items()},
            }
        )
        return EvidenceRunResult(
            document_sha256=document_sha256,
            page_render_hashes=render_hashes,
            pages=tuple(pages),
            report=report,
        )

    def ingest_document(
        self,
        pdf_path: Path,
        *,
        source_relative_path: str | None = None,
        output_dir: Path | None = None,
        review_hints: tuple[VisualReviewHint, ...] = (),
    ) -> tuple[DocumentEvidenceManifest, dict[str, object]]:
        """Process a complete document and emit a manifest that satisfies the V2 schema."""
        with pymupdf.open(pdf_path) as document:
            page_numbers = tuple(range(1, document.page_count + 1))
            page_count = document.page_count
        result = self.process_pages(
            pdf_path,
            page_numbers=page_numbers,
            output_dir=output_dir,
            review_hints=review_hints,
        )
        document_id = make_document_id(result.document_sha256)
        manifest = DocumentEvidenceManifest(
            manifest_id=make_manifest_id(
                document_id=document_id,
                material_profile_id=self.profile.profile_id,
                material_profile_revision=self.profile.revision,
                config_hash=self.config_hash,
            ),
            document_id=document_id,
            material_profile_id=self.profile.profile_id,
            material_profile_revision=self.profile.revision,
            source=SourceDocument(
                relative_path=source_relative_path or pdf_path.name,
                sha256=result.document_sha256,
                byte_size=pdf_path.stat().st_size,
                page_count=page_count,
            ),
            pages=result.pages,
            lineage=EvidenceLineage(
                run_id=f"run:{stable_config_hash({'document': result.document_sha256, 'pages': page_numbers, 'config': self.config_hash})[:24]}",
                generated_at_utc=utc_now(),
                profile_id=self.profile.profile_id,
                profile_revision=self.profile.revision,
                config_hash=self.config_hash,
            ),
        )
        if output_dir is not None:
            _write_json(output_dir / "manifest.json", manifest.model_dump(mode="json"))
            _write_json(output_dir / "quality_report.json", result.report)
        return manifest, result.report
