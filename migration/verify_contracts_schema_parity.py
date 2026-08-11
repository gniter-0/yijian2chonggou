"""Byte-level JSON Schema parity for the 28 semantic_contracts models (task acceptance).

Reuses the legacy scripts/tools/export_v2_schemas.py mechanism verbatim
(model.model_json_schema() serialized with ensure_ascii=False, indent=2, trailing
newline, one file per registry name) on BOTH sides inside one interpreter, then
compares the exported bytes. Outputs land under .test-tmp/ in this repo only;
the legacy repo stays read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OLD_SCRIPTS = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts")
sys.path.insert(0, str(OLD_SCRIPTS))

import knowledge_v2.semantic_contracts as old_sc  # noqa: E402
import examcard.knowledge.contracts as new_sc  # noqa: E402

# Exactly the semantic_contracts entries of the legacy export registry
# (scripts/tools/export_v2_schemas.py MODELS), same names, same order.
CONTRACT_MODELS = [
    "evidence_anchor_span",
    "continuation_record",
    "continuation_binding",
    "step2a_request",
    "step2a_semantic_projection",
    "step2a_response",
    "step2a_result_payload",
    "step2a_v13_result_payload",
    "step2a_v14_result_payload",
    "step2a_v15_result_payload",
    "step2a_v16_result_payload",
    "step2a_v17_result_payload",
    "step2a_v18_result_payload",
    "step2a_v19_result_payload",
    "step2a_v20_result_payload",
    "step2a_v21_result_payload",
    "step2a_v22_result_payload",
    "step2a_v23_result_payload",
    "step2a_table_structural_index_occurrence_audit",
    "step2a_complete_table_structural_projection_audit",
    "step2a_coverage_obligation_input",
    "step2a_coverage_obligation_context_input",
    "step2b_request",
    "step2b_response",
    "step2b_result_payload",
    "table_atomic_subfact_requirement",
    "table_continuation_request_input",
    "reviewed_canonical_title_projection",
]

CLASS_BY_NAME = {
    "evidence_anchor_span": "EvidenceAnchorSpan",
    "continuation_record": "ContinuationRecord",
    "continuation_binding": "ContinuationBinding",
    "step2a_request": "Step2ARequest",
    "step2a_semantic_projection": "Step2ASemanticProjection",
    "step2a_response": "Step2AResponse",
    "step2a_result_payload": "Step2AResultPayload",
    "step2a_v13_result_payload": "Step2AV13ResultPayload",
    "step2a_v14_result_payload": "Step2AV14ResultPayload",
    "step2a_v15_result_payload": "Step2AV15ResultPayload",
    "step2a_v16_result_payload": "Step2AV16ResultPayload",
    "step2a_v17_result_payload": "Step2AV17ResultPayload",
    "step2a_v18_result_payload": "Step2AV18ResultPayload",
    "step2a_v19_result_payload": "Step2AV19ResultPayload",
    "step2a_v20_result_payload": "Step2AV20ResultPayload",
    "step2a_v21_result_payload": "Step2AV21ResultPayload",
    "step2a_v22_result_payload": "Step2AV22ResultPayload",
    "step2a_v23_result_payload": "Step2AV23ResultPayload",
    "step2a_table_structural_index_occurrence_audit": "Step2ATableStructuralIndexOccurrenceAudit",
    "step2a_complete_table_structural_projection_audit": "Step2ACompleteTableStructuralProjectionAudit",
    "step2a_coverage_obligation_input": "Step2ACoverageObligationInput",
    "step2a_coverage_obligation_context_input": "Step2ACoverageObligationContextInput",
    "step2b_request": "Step2BRequest",
    "step2b_response": "Step2BResponse",
    "step2b_result_payload": "Step2BResultPayload",
    "table_atomic_subfact_requirement": "TableAtomicSubfactRequirement",
    "table_continuation_request_input": "TableContinuationRequestInput",
    "reviewed_canonical_title_projection": "ReviewedCanonicalTitleProjection",
}


def export(module, output_dir: Path) -> dict[str, bytes]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: dict[str, bytes] = {}
    for name in CONTRACT_MODELS:
        model = getattr(module, CLASS_BY_NAME[name])
        payload = (
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        (output_dir / f"{name}.schema.json").write_bytes(payload)
        exported[name] = payload
    return exported


def committed_blob(name: str) -> bytes:
    """Committed baseline bytes via git blob (read-only; working tree may be CRLF)."""
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(OLD_SCRIPTS.parent), "show", f"HEAD:schemas/v2/{name}.schema.json"],
        capture_output=True,
        check=True,
    )
    return result.stdout


def main() -> None:
    base = Path(__file__).resolve().parents[1] / ".test-tmp" / "contracts-schema-parity"
    old_exports = export(old_sc, base / "old")
    new_exports = export(new_sc, base / "new")
    mismatches = [name for name in CONTRACT_MODELS if old_exports[name] != new_exports[name]]
    if mismatches:
        print(f"SCHEMA PARITY FAILED for {len(mismatches)} / {len(CONTRACT_MODELS)} models: {mismatches}")
        for name in mismatches[:3]:
            old_text = old_exports[name].decode("utf-8").splitlines()
            new_text = new_exports[name].decode("utf-8").splitlines()
            for line_no, (old_line, new_line) in enumerate(zip(old_text, new_text), 1):
                if old_line != new_line:
                    print(f"  {name}: first diff at line {line_no}\n    OLD: {old_line}\n    NEW: {new_line}")
                    break
        raise SystemExit(1)
    baseline_mismatches = [name for name in CONTRACT_MODELS if committed_blob(name) != new_exports[name]]
    if baseline_mismatches:
        print(f"COMMITTED BASELINE PARITY FAILED for {len(baseline_mismatches)} models: {baseline_mismatches}")
        raise SystemExit(1)
    total_bytes = sum(len(value) for value in old_exports.values())
    print(f"SCHEMA PARITY OK: {len(CONTRACT_MODELS)} models byte-identical ({total_bytes} bytes per side)")
    print(f"COMMITTED BASELINE OK: all {len(CONTRACT_MODELS)} exports byte-identical to HEAD git blobs of schemas/v2")
    print(f"artifacts: {base}")


if __name__ == "__main__":
    main()
