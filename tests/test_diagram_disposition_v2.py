"""Contract tests for Phase 2.5E manual visual disposition routing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

from examcard.knowledge.diagram.disposition import (
    DiagramDisposition,
    is_released_disposition,
    load_diagram_disposition_sidecar,
)
from examcard.knowledge.fact_engine import run_golden_fact_pipeline
from examcard.knowledge.schema import FactType, GroundingStatus, Renderability

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



REGISTRY_PATH = ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
EVIDENCE_ROOT = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"
ANNOTATION_SIDECAR = ROOT / "scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml"
POLICY_PATH = ROOT / "configs/v2/content_policies/core_only_v1.yaml"
DIAGRAM_REVIEW_SIDECAR = ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.highres_controlled_vision_reviewed.yaml"
DISPOSITION_SIDECAR = ROOT / "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25e1_audited.yaml"


def _run(tmp_path: Path, *, disposition_sidecar: Path = DISPOSITION_SIDECAR) -> dict[str, object]:
    return run_golden_fact_pipeline(
        registry_path=REGISTRY_PATH,
        sidecar_path=ANNOTATION_SIDECAR,
        content_policy_path=POLICY_PATH,
        evidence_root=EVIDENCE_ROOT,
        output_root=tmp_path / "facts",
        diagram_review_sidecar_path=DIAGRAM_REVIEW_SIDECAR,
        diagram_disposition_sidecar_path=disposition_sidecar,
    )


def test_manual_disposition_releases_only_photo_examples_and_keeps_structural_diagrams_blocked(tmp_path: Path):
    sidecar = load_diagram_disposition_sidecar(DISPOSITION_SIDECAR)

    assert len(sidecar.entries) == 19
    assert sum(entry.disposition == DiagramDisposition.STRUCTURAL_DIAGRAM for entry in sidecar.entries) == 13
    assert sum(is_released_disposition(entry.disposition) for entry in sidecar.entries) == 6
    assert all(entry.audit.crop_sha256 == entry.figure_crop_sha256 for entry in sidecar.entries)
    assert sidecar.review_crop_manifest is not None
    assert all(entry.review_asset is not None and entry.review_asset.render_dpi == 600 for entry in sidecar.entries)

    result = _run(tmp_path)
    assert result["diagram_reviewed_entries"] == 4
    assert result["diagram_disposition_entries"] == 19
    assert result["diagram_disposition_released_entries"] == 6
    assert result["quality_gates_passed"] is False

    expected_blockers = {
        "gonglu_01_text_list": 4,
        "gonglu_24_table_formula": 4,
        "gonglu_41_diagram_mixed": 4,
        "gonglu_55_standard_constraints": 1,
        "gonglu_62_cost_flow_incident": 0,
    }
    assert {
        sample["sample_id"]: sample["quality_gate"]["unrenderable_diagrams"]
        for sample in result["samples"]
    } == expected_blockers

    visual_facts = [
        fact
        for sample in result["samples"]
        for fact in sample["facts"]
        if fact["diagram_disposition_id"] is not None
    ]
    visual_cards = [
        card
        for sample in result["samples"]
        for card in sample["cards"]
        if card["diagram_disposition_id"] is not None
    ]
    assert len(visual_facts) == len(visual_cards) == 6
    assert all(fact["fact_type"] == FactType.VISUAL_REFERENCE.value for fact in visual_facts)
    assert all(fact["renderability"] == Renderability.PUBLISHABLE.value for fact in visual_facts)
    assert all(card["grounding_status"] == GroundingStatus.MANUAL_VERIFIED.value for card in visual_cards)
    assert all(card["diagram_type"] == DiagramDisposition.PHOTO_EXAMPLE.value for card in visual_cards)
    assert all(card["content_segments"][0]["renderer"] == "visual_reference" for card in visual_cards)
    assert all(card["kp_type"] == "visual_reference" for card in visual_cards)
    assert all(fact["diagram_disposition_trace"]["review_crop_render_dpi"] == 600 for fact in visual_facts)
    assert all(card["diagram_disposition_trace"]["review_crop_sha256"] for card in visual_cards)


def test_disposition_sidecar_rejects_a_source_review_hash_mismatch(tmp_path: Path):
    payload = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["source_diagram_review_sidecar_sha256"] = "0" * 64
    bad_sidecar = tmp_path / "bad_disposition.yaml"
    bad_sidecar.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="source hash"):
        _run(tmp_path, disposition_sidecar=bad_sidecar)


def test_disposition_sidecar_rejects_missing_or_mismatched_highres_audit_assets(tmp_path: Path, repo_tmp_path: Path):
    payload = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)

    missing_manifest = dict(payload)
    missing_manifest.pop("review_crop_manifest")
    missing_manifest_path = tmp_path / "missing_manifest.yaml"
    missing_manifest_path.write_text(yaml.safe_dump(missing_manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="review crop manifest"):
        _run(tmp_path, disposition_sidecar=missing_manifest_path)

    bad_manifest_hash = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(bad_manifest_hash, dict)
    bad_manifest_hash["review_crop_manifest"]["manifest_sha256"] = "0" * 64
    bad_manifest_hash_path = tmp_path / "bad_manifest_hash.yaml"
    bad_manifest_hash_path.write_text(yaml.safe_dump(bad_manifest_hash, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest hash"):
        _run(tmp_path, disposition_sidecar=bad_manifest_hash_path)

    bad_asset = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(bad_asset, dict)
    bad_asset["entries"][0]["review_asset"]["render_dpi"] = 300
    bad_asset_path = tmp_path / "bad_asset.yaml"
    bad_asset_path.write_text(yaml.safe_dump(bad_asset, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="high-resolution review binding"):
        _run(tmp_path, disposition_sidecar=bad_asset_path)

    bad_crop_hash = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(bad_crop_hash, dict)
    review_id = bad_crop_hash["entries"][0]["review_id"]
    bad_crop_hash["entries"][0]["review_asset"]["review_crop_sha256"] = "0" * 64
    manifest_source = ROOT / bad_crop_hash["review_crop_manifest"]["manifest_path"]
    manifest_payload = json.loads(manifest_source.read_text(encoding="utf-8"))
    next(item for item in manifest_payload["entries"] if item["review_id"] == review_id)["review_crop_sha256"] = "0" * 64
    manifest_path = repo_tmp_path / "manifest_with_bad_crop_hash.json"
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
    bad_crop_hash["review_crop_manifest"] = {
        "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    bad_crop_hash_path = tmp_path / "bad_crop_hash.yaml"
    bad_crop_hash_path.write_text(yaml.safe_dump(bad_crop_hash, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="review crop hash does not match"):
        _run(tmp_path, disposition_sidecar=bad_crop_hash_path)


def test_disposition_sidecar_rejects_blank_titles_and_non_deterministic_ids(tmp_path: Path):
    blank_title = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(blank_title, dict)
    next(entry for entry in blank_title["entries"] if entry["disposition"] == "photo_example")["title"] = "  \t "
    blank_title_path = tmp_path / "blank_title.yaml"
    blank_title_path.write_text(yaml.safe_dump(blank_title, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="title must not be blank"):
        load_diagram_disposition_sidecar(blank_title_path)

    bad_id = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(bad_id, dict)
    bad_id["entries"][0]["disposition_id"] = "visual:" + "0" * 24
    bad_id_path = tmp_path / "bad_id.yaml"
    bad_id_path.write_text(yaml.safe_dump(bad_id, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="ID must match"):
        load_diagram_disposition_sidecar(bad_id_path)


def test_non_kp_visual_is_audited_in_coverage_without_creating_fact_or_card(tmp_path: Path):
    payload = yaml.safe_load(DISPOSITION_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    entry = next(item for item in payload["entries"] if item["disposition"] == "photo_example")
    entry["disposition"] = "non_kp_visual"
    non_kp_path = tmp_path / "non_kp.yaml"
    non_kp_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = _run(tmp_path, disposition_sidecar=non_kp_path)
    sample = next(item for item in result["samples"] if item["sample_id"] == entry["sample_id"])
    figure_id = entry["figure_evidence_id"]
    assert not any(figure_id in fact["primary_evidence_ids"] for fact in sample["facts"])
    assert not any(figure_id in [ref["evidence_id"] for ref in card["evidence_refs"]] for card in sample["cards"])
    coverage = next(record for record in sample["coverage"]["records"] if record["evidence_id"] == figure_id)
    assert coverage["disposition"] == "excluded_by_audit"
    assert entry["disposition_id"] in coverage["reason"]
    assert sample["quality_gate"]["blockers"] == ["unrenderable_diagrams"]
