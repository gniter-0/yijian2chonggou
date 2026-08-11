"""Canonical evidence contracts for Step 1 V2."""

from .identity import (
    make_cache_key,
    make_conflict_id,
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_revision_id,
    make_manifest_id,
    make_page_id,
    make_source_locator_id,
)
from .ingest import EvidenceEngine, EvidenceEngineConfig
from .schema import DocumentEvidenceManifest, EvidenceUnit, PageEvidence

__all__ = [
    "DocumentEvidenceManifest",
    "EvidenceEngine",
    "EvidenceEngineConfig",
    "EvidenceUnit",
    "PageEvidence",
    "make_cache_key",
    "make_conflict_id",
    "make_document_id",
    "make_evidence_fingerprint",
    "make_evidence_revision_id",
    "make_manifest_id",
    "make_page_id",
    "make_source_locator_id",
]
