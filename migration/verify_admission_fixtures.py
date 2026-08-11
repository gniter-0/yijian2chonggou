"""Acceptance for the admission fixture family: replay real frozen artifacts.

Every moved model keeps its self-hash validator, so validating the legacy
production artifacts with the NEW models re-derives and enforces every persisted
hash (fixture/candidate/contract/element/decision hashes). Old-vs-new dumps are
compared for full-shape parity, and the legacy canonical_hash variants
(r84/r810/r9+r10) are proven output-equivalent on the real payloads.

The r10 family has no persisted run in this working copy, so its r10x self-hash
protocol (exclude schema_version + hash field) is exercised by building a
decision/card/fixture through the LEGACY module helpers and validating the
result with the NEW models.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

OLD_ROOT = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(OLD_ROOT / "scripts"))

import knowledge_v2.phase2r810_r89_reviewed_admission_authorization as old_r810  # noqa: E402
import knowledge_v2.phase2r9_generic_step3_card_compiler as old_r9  # noqa: E402
import knowledge_v2.phase2r98_generic_step3_deterministic_release_closure as old_r98  # noqa: E402
import knowledge_v2.phase2r10_generic_step3_human_review_admission as old_r10  # noqa: E402
import knowledge_v2.phase2r102_production_step3_human_decision_session as old_r102  # noqa: E402
import knowledge_v2.phase2r103_step3_production_raw_decision_ingestion as old_r103  # noqa: E402
import knowledge_v2.phase2r104_step3_explicit_production_authorization as old_r104  # noqa: E402
import knowledge_v2.phase2r2242_step3_reviewed_release_merge as old_r2242  # noqa: E402
import knowledge_v2.phase2r319_step4_card4_reviewed_image_fixture as old_r319  # noqa: E402

import examcard.knowledge.contracts.admission_fixtures as af  # noqa: E402
import examcard.knowledge.contracts.admission_legacy_hash as alh  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("PASS " if condition else "FAIL ") + label + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# 1. R8.10 reviewed typed-evidence fixture (whole r84 family + r810 fixture).
r810_path = OLD_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1/reviewed-typed-evidence-fixture.json"
r810_data = json.loads(r810_path.read_text(encoding="utf-8"))
new_fixture = af.ReviewedTypedEvidenceFixture.model_validate(r810_data)
old_fixture = old_r810.ReviewedTypedEvidenceFixture.model_validate(r810_data)
check("r810 fixture: NEW validators accept the frozen artifact", True)
check(
    "r810 fixture: old/new dumps identical",
    new_fixture.model_dump(mode="json") == old_fixture.model_dump(mode="json"),
)
check(
    "canonical_hash equivalence on r810 payload (r84-bytes vs r810/r9/r10 variants)",
    alh.canonical_hash(r810_data)
    == old_r810.canonical_hash(r810_data)
    == old_r9.canonical_hash(r810_data)
    == old_r10.canonical_hash(r810_data),
)

# 2. R9.8 deterministic release bundle -> every DeterministicDraftCard
#    (validates Step3CardContract + FakeArtifact self-hashes and contract ids).
r98_path = OLD_ROOT / "data/semantic_llm_v2/phase2r98_generic_step3_deterministic_release_closure_run1/deterministic-release-bundle.json"
r98_data = json.loads(r98_path.read_text(encoding="utf-8"))
new_cards = [af.DeterministicDraftCard.model_validate(item) for item in r98_data["cards"]]
old_bundle = old_r98.DeterministicReleaseBundle.model_validate(r98_data)
check(f"r98 bundle: NEW validators accept all {len(new_cards)} deterministic draft cards", True)
check(
    "r98 cards: old/new dumps identical",
    [item.model_dump(mode='json') for item in new_cards]
    == [item.model_dump(mode='json') for item in old_bundle.cards],
)

new_release_bundle = af.DeterministicReleaseBundle.model_validate(r98_data)
check(
    "r98 release bundle: NEW validators accept the frozen artifact (release_sha256 recomputed)",
    new_release_bundle.model_dump(mode="json") == old_bundle.model_dump(mode="json"),
)

draft_bundle_payload = {
    "source_fixture_sha256": r98_data["source_fixture_sha256"],
    "artifacts": tuple(item["artifact"] for item in r98_data["cards"]),
    "artifact_count": len(r98_data["cards"]),
}
old_draft_bundle = old_r9.DraftBundle.model_validate(draft_bundle_payload)
new_draft_bundle = af.DraftBundle.model_validate(draft_bundle_payload)
check(
    "r9 draft bundle: old/new dumps identical on real FakeArtifacts",
    new_draft_bundle.model_dump(mode="json") == old_draft_bundle.model_dump(mode="json"),
)
check(
    "canonical_json lineage equivalence on r98 payload (r84 vs r9x vs r10x bytes)",
    alh.canonical_json(r98_data)
    == alh.canonical_json_r9x(r98_data)
    == alh.canonical_json_r10x(r98_data)
    == old_r9.canonical_json(r98_data),
)

# 3. r10x self-hash protocol via legacy-built decision/card/fixture.
sample_card = r98_data["cards"][0]
decision_payload = {
    "decision_id": "step3-human-decision:" + "a" * 24,
    "queue_item_id": "step3-review-queue:" + "b" * 24,
    "queue_sha256": "c" * 64,
    "contract_id": sample_card["contract"]["contract_id"],
    "source_candidate_id": sample_card["contract"]["source_candidate_id"],
    "fact_ids": tuple(sample_card["contract"]["fact_ids"]),
    "evidence_ids": ("ev-1",),
    "anchor_ids": ("anchor-1",),
    "source_texts": tuple(item["text"] for item in sample_card["contract"]["display_elements"]),
    "typed_contract_sha256": sample_card["contract"]["source_typed_contract_sha256"],
    "provenance_sha256": "d" * 64,
    "presentation": sample_card["contract"]["presentation"],
    "geometry_sha256": "e" * 64,
    "disposition": "reviewed_candidate",
    "reviewer": "parity_probe",
    "reason": "hash parity probe",
    "source_text_confirmed": True,
    "fact_card_confirmed": True,
    "evidence_anchor_confirmed": True,
    "typed_contract_confirmed": True,
    "provenance_confirmed": True,
    "presentation_confirmed": True,
    "geometry_confirmed": True,
    "explicit_human_authorization": True,
}
old_decision = old_r10._hash_model(old_r10.Step3HumanDecision, decision_payload, "decision_sha256")
new_decision = af.Step3HumanDecision.model_validate(old_decision.model_dump(mode="json"))
check("r10 decision: legacy-hashed payload accepted by NEW model", True)

reviewed_card_payload = {
    "queue_item_id": decision_payload["queue_item_id"],
    "contract": sample_card["contract"],
    "artifact": sample_card,
    "decision": old_decision.model_dump(mode="json"),
}
old_reviewed_card = old_r10._hash_model(old_r10.ReviewedStep3Card, reviewed_card_payload, "reviewed_card_sha256")
new_reviewed_card = af.ReviewedStep3Card.model_validate(old_reviewed_card.model_dump(mode="json"))
check("r10 reviewed card: legacy-hashed payload accepted by NEW model", True)

fixture_payload = {
    "source_fixture_sha256": "f" * 64,
    "deterministic_release_sha256": r98_data["release_sha256"],
    "decision_manifest_sha256": "9" * 64,
    "cards": (old_reviewed_card.model_dump(mode="json"),),
    "reviewed_card_count": 1,
    "fixture_authorized": True,
}
old_r10_fixture = old_r10._hash_model(old_r10.ReviewedStep3Fixture, fixture_payload, "fixture_sha256")
new_r10_fixture = af.ReviewedStep3Fixture.model_validate(old_r10_fixture.model_dump(mode="json"))
check("r10 fixture: legacy-hashed payload accepted by NEW model", True)
check(
    "r10 family: old/new dumps identical",
    new_r10_fixture.model_dump(mode="json") == old_r10_fixture.model_dump(mode="json"),
)

# 3b. R10.3 empty refusal-form fixture (renamed ReviewedStep3FixtureR103).
r103_payload = {
    "source_raw_draft_sha256": "a" * 64,
    "source_queue_sha256": "b" * 64,
    "source_typed_evidence_sha256": "c" * 64,
}
old_r103_fixture = old_r103._hash_model(old_r103.ReviewedStep3Fixture, r103_payload, "fixture_sha256")
new_r103_fixture = af.ReviewedStep3FixtureR103.model_validate(old_r103_fixture.model_dump(mode="json"))
check(
    "r103 empty fixture: legacy-hashed payload accepted by NEW renamed model",
    new_r103_fixture.model_dump(mode="json") == old_r103_fixture.model_dump(mode="json"),
)

# 4. R2.4.2 unified reviewed release fixture (r2242 trio + embedded r22/r2241 models).
r2242_path = OLD_ROOT / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
r2242_data = yaml.safe_load(r2242_path.read_text(encoding="utf-8"))
new_unified = af.Step3UnifiedReviewedReleaseFixture.model_validate(r2242_data)
old_unified = old_r2242.Step3UnifiedReviewedReleaseFixture.model_validate(r2242_data)
check("r2242 unified fixture: NEW validators accept the release YAML", True)
check(
    "r2242 unified fixture: old/new dumps identical",
    new_unified.model_dump(mode="json") == old_unified.model_dump(mode="json"),
)

# 5. R3.19 pilot handoff manifest (class renamed Step4PilotHandoffRefresh, hash-safe).
r319_path = OLD_ROOT / "data/semantic_llm_v2/phase2r319_step4_card4_reviewed_image_fixture_run1/step4-pilot-handoff-refresh.json"
r319_data = json.loads(r319_path.read_text(encoding="utf-8"))
new_handoff = af.Step4PilotHandoffRefresh.model_validate(r319_data)
old_handoff = old_r319.Step4PilotHandoffRefreshR319.model_validate(r319_data)
check("r319 handoff: NEW validators accept the frozen manifest", True)
check(
    "r319 handoff: old/new dumps identical",
    new_handoff.model_dump(mode="json") == old_handoff.model_dump(mode="json"),
)

# 5b. R10.4 run-evidence family via legacy-built payloads (r10x lineage hashes).
frozen_artifact_payload = {"path": "data/example.json", "role": "r103_run", "bytes": 128, "sha256": "1" * 64}
old_frozen = old_r104.FrozenArtifact.model_validate(frozen_artifact_payload)
new_frozen = af.FrozenArtifact.model_validate(frozen_artifact_payload)
check("r104 frozen artifact: old/new dumps identical", new_frozen.model_dump(mode="json") == old_frozen.model_dump(mode="json"))

freeze_payload = {
    "artifacts": (old_frozen.model_dump(mode="json"),),
    "artifact_count": 1,
    "aggregate_sha256": old_r102.canonical_hash((old_frozen,)),
}
old_freeze = old_r104._hash_model(old_r104.PriorFreeze, freeze_payload, "freeze_sha256")
new_freeze = af.PriorFreeze.model_validate(old_freeze.model_dump(mode="json"))
check("r104 prior freeze: legacy-hashed payload accepted by NEW model", True)

gate_check = old_r104.GateCheck.model_validate({"check_id": "frozen_inputs_valid", "passed": True, "detail": "probe"})
gate_payload = {
    "valid": True,
    "frozen_inputs_valid": True,
    "raw_manifest_valid": True,
    "prior_acceptance_valid": True,
    "source_chain_valid": True,
    "explicit_authorization_valid": True,
    "exactly_once_valid": True,
    "reviewed_fixture_valid": True,
    "strict_schema_valid": True,
    "regressions_fail_closed": True,
    "checks": (gate_check.model_dump(mode="json"),),
}
old_gate = old_r104._hash_model(old_r104.OfflineGate, gate_payload, "gate_sha256")
new_gate = af.OfflineGate.model_validate(old_gate.model_dump(mode="json"))
check("r104 offline gate: legacy-hashed payload accepted by NEW model", True)

receipt = old_r104.JUnitReceipt.model_validate(
    {"label": "targeted", "sha256": "2" * 64, "tests": 10, "failures": 0, "errors": 0, "skipped": 0}
)
receipt_full = old_r104.JUnitReceipt.model_validate({**receipt.model_dump(mode="json"), "label": "full"})
acceptance_payload = {
    "accepted": True,
    "report_sha256": "3" * 64,
    "receipts": (receipt.model_dump(mode="json"), receipt_full.model_dump(mode="json")),
}
old_acceptance = old_r104._hash_model(old_r104.AcceptanceReport, acceptance_payload, "acceptance_sha256")
new_acceptance = af.AcceptanceReport.model_validate(old_acceptance.model_dump(mode="json"))
check("r104 acceptance report: legacy-hashed payload accepted by NEW model", True)
check(
    "r104 family: old/new dumps identical",
    (new_freeze.model_dump(mode="json"), new_gate.model_dump(mode="json"), new_acceptance.model_dump(mode="json"))
    == (old_freeze.model_dump(mode="json"), old_gate.model_dump(mode="json"), old_acceptance.model_dump(mode="json")),
)

# 6. JSON-schema parity for the headline admission models. The renamed handoff
#    class keeps every field/constraint; only schema titles reflect class names,
#    so titles are normalized before comparison for that pair.
def normalized_schema(model, rename: dict[str, str] | None = None) -> str:
    text = json.dumps(model.model_json_schema(), ensure_ascii=False, sort_keys=True)
    for before, after in (rename or {}).items():
        text = text.replace(before, after)
    return text


SCHEMA_PAIRS = [
    (old_r810.ReviewedTypedEvidenceFixture, af.ReviewedTypedEvidenceFixture, None),
    (old_r9.Step3CardContract, af.Step3CardContract, None),
    (old_r98.DeterministicDraftCard, af.DeterministicDraftCard, None),
    (old_r10.ReviewedStep3Fixture, af.ReviewedStep3Fixture, None),
    (
        old_r103.ReviewedStep3Fixture,
        af.ReviewedStep3FixtureR103,
        {"ReviewedStep3Fixture": "ReviewedStep3FixtureR103"},
    ),
    (old_r104.OfflineGate, af.OfflineGate, None),
    (old_r104.AcceptanceReport, af.AcceptanceReport, None),
    (old_r104.PriorFreeze, af.PriorFreeze, None),
    (old_r2242.Step3UnifiedReviewedReleaseFixture, af.Step3UnifiedReviewedReleaseFixture, None),
    (
        old_r319.Step4PilotHandoffRefreshR319,
        af.Step4PilotHandoffRefresh,
        {"Step4PilotHandoffRefreshR319": "Step4PilotHandoffRefresh"},
    ),
]
for old_model, new_model, rename in SCHEMA_PAIRS:
    check(
        f"schema parity: {new_model.__name__}",
        normalized_schema(old_model, rename) == normalized_schema(new_model),
    )

if FAILURES:
    print(f"\nADMISSION FIXTURE PARITY FAILED: {len(FAILURES)} failures")
    raise SystemExit(1)
print("\nADMISSION FIXTURE PARITY OK")
