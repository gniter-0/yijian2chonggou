"""P5 Publication Authority V2 contract and frozen persistence tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.common.io import read_json
from examcard.gates.release.final_release_v2 import (
    FinalReleaseAuditV2,
    FinalReleaseBundleV2,
    FinalReleaseClosureReceiptV2,
)
from examcard.gates.release.publication_v2 import (
    PUBLICATION_SCOPE_V2,
    PublicationAuthorizationV2,
    build_publication_artifact_v2,
    build_publication_authorization_v2,
    build_publication_manifest_v2,
    derive_publication_v2,
)
from examcard.pipeline.live_checkpoint import (
    materialize_final_release_v2,
    materialize_publication_v2,
    validate_materialized_publication_v2,
)
from tests.test_gates_release_final_release_v2 import _materialized_p3_run


def _p4_run(tmp_path: Path, *, zero_release: bool = False) -> tuple[Path, dict[str, Any]]:
    run_root, p3_identity = _materialized_p3_run(tmp_path, zero_approved=zero_release)
    return run_root, materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)


def _p4_models(run_root: Path) -> tuple[Any, Any | None, Any, dict[str, Any]]:
    directory = run_root / "checkpoints" / "final_release"
    receipt = FinalReleaseClosureReceiptV2.model_validate(read_json(directory / "final-release-closure-v2.json"))
    bundle = (
        FinalReleaseBundleV2.model_validate(read_json(directory / "final-release-bundle-v2.json"))
        if receipt.release_bundle_present
        else None
    )
    audit = FinalReleaseAuditV2.model_validate(read_json(directory / "final-release-audit-v2.json"))
    return receipt, bundle, audit, {}


def _p4_identity(run_root: Path, materialized: dict[str, Any]) -> dict[str, Any]:
    # P4 persistence identity is returned directly by materialize_final_release_v2;
    # the state fixture is intentionally not required for pure P5 derivation.
    return dict(materialized)


def _auth(run_root: Path, p4: dict[str, Any], *, manifest_key: str) -> PublicationAuthorizationV2:
    return build_publication_authorization_v2(
        authorization_id="human-auth-1",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=str(p4["final_release_receipt_sha256"]),
        publication_manifest_sha256=manifest_key,
        authorized_by="release-operator",
        authorization_reason="approved for controlled publication package",
        authorized_at="2026-09-11T00:00:00Z",
    )


def test_publication_contract_is_deterministic_and_sorts_artifacts(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path)
    receipt, bundle, audit, _ = _p4_models(run_root)
    assert bundle is not None
    card_hash = bundle.release_cards[0].release_card_sha256
    first = build_publication_artifact_v2(
        artifact_id="card:1:provenance",
        relative_path="artifacts/card-0001/image-provenance.json",
        media_type="application/json",
        content=b"provenance",
        source_release_card_sha256=card_hash,
    )
    second = build_publication_artifact_v2(
        artifact_id="card:1:image",
        relative_path="artifacts/card-0001/image.png",
        media_type="image/png",
        content=b"image",
        source_release_card_sha256=card_hash,
    )
    manifest = build_publication_manifest_v2(
        final_release_receipt=receipt,
        final_release_bundle=bundle,
        final_release_audit=audit,
        p4_receipt_identity=_p4_identity(run_root, p4),
        artifacts=(first, second),
    )
    assert tuple(item.relative_path for item in manifest.artifacts) == tuple(
        sorted(item.relative_path for item in (first, second))
    )
    auth = _auth(run_root, p4, manifest_key=manifest.manifest_sha256)
    first_result = derive_publication_v2(
        final_release_receipt=receipt,
        final_release_bundle=bundle,
        final_release_audit=audit,
        p4_receipt_identity=p4,
        artifacts=(second, first),
        authorization=auth,
    )
    assert first_result.manifest == manifest
    assert first_result.receipt == derive_publication_v2(
        final_release_receipt=receipt,
        final_release_bundle=bundle,
        final_release_audit=audit,
        p4_receipt_identity=p4,
        artifacts=(second, first),
        authorization=auth,
    ).receipt


def test_publication_rejects_p4_closure_and_bundle_binding_drift(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path)
    receipt, bundle, audit, _ = _p4_models(run_root)
    assert bundle is not None
    artifact = build_publication_artifact_v2(
        artifact_id="card:1:image",
        relative_path="artifacts/card-0001/image.png",
        media_type="image/png",
        content=b"image",
        source_release_card_sha256=bundle.release_cards[0].release_card_sha256,
    )
    for field in ("final_release_receipt_sha256", "final_release_bundle_sha256"):
        bad = dict(p4)
        bad[field] = "0" * 64
        with pytest.raises(ValueError, match="P4"):
            build_publication_manifest_v2(
                final_release_receipt=receipt,
                final_release_bundle=bundle,
                final_release_audit=audit,
                p4_receipt_identity=bad,
                artifacts=(artifact,),
            )


def test_publication_requires_exact_authorization_binding(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path)
    receipt, bundle, audit, _ = _p4_models(run_root)
    assert bundle is not None
    artifact = build_publication_artifact_v2(
        artifact_id="card:1:image",
        relative_path="artifacts/card-0001/image.png",
        media_type="image/png",
        content=b"image",
        source_release_card_sha256=bundle.release_cards[0].release_card_sha256,
    )
    manifest = build_publication_manifest_v2(
        final_release_receipt=receipt,
        final_release_bundle=bundle,
        final_release_audit=audit,
        p4_receipt_identity=p4,
        artifacts=(artifact,),
    )
    auth = _auth(run_root, p4, manifest_key="0" * 64)
    with pytest.raises(ValueError, match="manifest"):
        derive_publication_v2(
            final_release_receipt=receipt,
            final_release_bundle=bundle,
            final_release_audit=audit,
            p4_receipt_identity=p4,
            artifacts=(artifact,),
            authorization=auth,
            publication_manifest=manifest,
        )


def test_publication_zero_release_has_no_package_artifacts(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path, zero_release=True)
    pending = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4)
    assert pending["publication_status"] == "waiting_human"
    assert pending["publication_artifact_count"] == 0
    manifest = json.loads(
        (run_root / "checkpoints" / "publication" / "publication-manifest-v2.json").read_text(encoding="utf-8")
    )
    auth = _auth(run_root, p4, manifest_key=manifest["manifest_sha256"])
    frozen = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4, authorization=auth)
    assert frozen["publication_artifact_count"] == 0
    assert not list((run_root / "checkpoints" / "publication" / "artifacts").rglob("*"))
    receipt = json.loads(
        (run_root / "checkpoints" / "publication" / "publication-receipt-v2.json").read_text(encoding="utf-8")
    )
    assert receipt["artifact_count"] == 0
    assert receipt["package_sha256"] == "0" * 64


def test_publication_persistence_is_receipt_last_and_recovers_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, p4 = _p4_run(tmp_path)
    pending = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4)
    directory = run_root / "checkpoints" / "publication"
    manifest_before = (directory / "publication-manifest-v2.json").read_bytes()
    artifact_path = directory / "artifacts" / "card-0001" / "image.png"
    artifact_path.unlink()
    writes: list[str] = []
    from examcard.pipeline import live_checkpoint

    original = live_checkpoint.atomic_write_json

    def record(path: Path, payload: Any) -> None:
        writes.append(path.name)
        original(path, payload)

    monkeypatch.setattr(live_checkpoint, "atomic_write_json", record)
    auth = _auth(run_root, p4, manifest_key=str(pending["publication_manifest_sha256"]))
    frozen = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4, authorization=auth)
    assert frozen["publication_status"] == "frozen"
    assert (directory / "publication-manifest-v2.json").read_bytes() == manifest_before
    assert writes[-1] == "publication-receipt-v2.json"
    assert writes == ["publication-authorization-v2.json", "publication-receipt-v2.json"]
    assert materialize_publication_v2(run_root=run_root, expected_p4_authority=p4) == frozen


def test_publication_conflicting_partial_and_frozen_tamper_fail_closed(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path)
    pending = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4)
    artifact_path = run_root / "checkpoints" / "publication" / "artifacts" / "card-0001" / "image.png"
    artifact_path.write_bytes(b"tampered")
    with pytest.raises(Exception, match="conflicts"):
        materialize_publication_v2(run_root=run_root, expected_p4_authority=p4)

    # Rebuild the partial copy, then complete the frozen authority.
    artifact_path.write_bytes((run_root / "step4" / "images" / "step4-card-01-one.png").read_bytes())
    auth = _auth(run_root, p4, manifest_key=str(pending["publication_manifest_sha256"]))
    frozen = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4, authorization=auth)
    expected = {**p4, **frozen}
    frozen_bytes = {
        path.name: path.read_bytes()
        for path in (run_root / "checkpoints" / "publication").glob("publication-*.json")
    }
    assert validate_materialized_publication_v2(run_root=run_root, expected_authority=expected) == frozen
    auth_path = run_root / "checkpoints" / "publication" / "publication-authorization-v2.json"
    auth_path.write_bytes(auth_path.read_bytes() + b"\n")
    with pytest.raises(Exception, match="drifted|conflicts|hash"):
        validate_materialized_publication_v2(run_root=run_root, expected_authority=expected)
    assert frozen_bytes["publication-manifest-v2.json"] == (
        run_root / "checkpoints" / "publication" / "publication-manifest-v2.json"
    ).read_bytes()


def test_publication_missing_child_after_receipt_refuses_without_recreation(tmp_path: Path) -> None:
    run_root, p4 = _p4_run(tmp_path)
    pending = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4)
    auth = _auth(run_root, p4, manifest_key=str(pending["publication_manifest_sha256"]))
    frozen = materialize_publication_v2(run_root=run_root, expected_p4_authority=p4, authorization=auth)
    expected = {**p4, **frozen}
    child = run_root / "checkpoints" / "publication" / "artifacts" / "card-0001" / "image.png"
    child.unlink()
    with pytest.raises(Exception, match="missing"):
        validate_materialized_publication_v2(run_root=run_root, expected_authority=expected)
    assert not child.exists()
