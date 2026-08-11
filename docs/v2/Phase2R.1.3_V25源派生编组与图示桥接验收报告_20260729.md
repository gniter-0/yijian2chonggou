# Phase 2R.1.3 V25 source-derived Card grouping / semantic-diagram bridge reconciliation

Date: 2026-07-29
Mode: offline deterministic fake-LLM and reviewed source artifacts only. No relay or real Step 2A/2B request was sent.

## Scope And Protection

- Read-only inputs: V25 reviewed fixture, original V2 Evidence, the V25 `semantic_v2` diagram sidecar, reviewed table/continuation sidecars, and Content Policy.
- No V1, raw V2 Evidence, historical reviewed sidecar, image, or frozen V23/V24 record was modified.
- The report records `network_calls=0`, `real_step2a_requests=0`, `real_step2b_requests=0`, `source_mutations=0`, and `frozen_inputs_unchanged=true`.

## Delivered

- Two independent reviewed table-repair sidecars:
  - `gonglu_24_table_formula` page 8: closes the source 5x3 arch-removal grid using only reviewed merged-cell spans. `formwork.arch-removal` now has a policy-approved source anchor.
  - `gonglu_41_diagram_mixed` page 2: binds the three-column title span of the source 6x3 tunnel-span table. `tunnel.span-classification` now has a policy-approved source anchor.
- Five persisted semantic-diagram bridge audits preserve the reviewed figure Evidence, node/edge Evidence, basis, confidence, source manifest, and semantic sidecar hash. Four have a reviewed `diagram_relation` Fact and enter V25B FactSummary; one is explicitly `evidence_only`. The page-1 formwork load-layout figure has no reviewed Fact target, so it remains evidence-only and no synthetic Fact was created.
- Six source-family grouping reviews contain Fact IDs/keys, raw Evidence IDs, anchor spans, manifests, and source basis only. They contain no Learning Golden Card key, title, or presentation field. V25B derives the required grouping/presentation from each source-family kind.
- Three spatial watermark candidates are independently reviewed and marked `excluded_as_watermark`; unreviewed candidates remain fail-closed.

## Five-Sample Results

| Sample | FactSummary | Diagram Fact bridge | Source families | Card drift | Unresolved watermark | Teaching Gate |
|---|---:|---:|---:|---:|---:|---|
| `gonglu_01_text_list` | 3 | 1 | 0 | 0 | 0 | publishable |
| `gonglu_24_table_formula` | 11 | 0 | 2 | 0 | 0 | publishable |
| `gonglu_41_diagram_mixed` | 4 | 1 | 1 | 0 | 0 | publishable |
| `gonglu_55_standard_constraints` | 5 | 1 | 2 | 0 | 0 | publishable |
| `gonglu_62_cost_flow_incident` | 5 | 1 | 1 | 0 | 0 | publishable |

All five V25 Step 2A source fake Gates and all five V25B grouping Gates pass. Every reviewed Fact is assigned exactly once, each reviewed Card grouping aligns, and table/continuation provenance remains intact.

The persisted bridge audit separates the source facts from the source diagrams: `5` reviewed `semantic_v2` diagrams, `4` V25B FactSummary bridges, and `1` evidence-only bridge. The evidence-only item is fail-closed and cannot enter Step 2B until a separate human review establishes a Fact target.

## Verification

```powershell
$env:PYTHONPATH='scripts'
.\.venv\Scripts\python.exe -m pytest scripts/tests/test_phase2r13_v25_reconciliation.py scripts/tests/test_phase2r12_v25_golden_review.py scripts/tests/test_phase2r11_generic_contracts.py scripts/tests/test_semantic_projection_v2.py -q --basetemp .test-tmp-phase2r13-targeted-final
.\.venv\Scripts\python.exe scripts/tools/run_phase2r13_v25_reconciliation_offline.py --output docs/v2/artifacts/phase2r13_v25_reconciliation_offline_report_20260729.json --generated-at-utc 2026-07-29T18:00:00Z
.\.venv\Scripts\python.exe scripts/tools/export_v2_schemas.py --output-dir schemas/v2
.\.venv\Scripts\python.exe -m pytest -q --basetemp .test-tmp-phase2r13-full-final --junitxml docs/v2/artifacts/phase2r13_full_pytest_20260729.xml
```

Targeted regression: `20 passed`. Schema export: passed. Full pytest: `1334 passed, 6 xfailed`, exit code `0`, in `84.36s`; the JUnit result is `docs/v2/artifacts/phase2r13_full_pytest_20260729.xml`. The sole warning is pytest's pre-existing inability to write `.pytest_cache` (`WinError 5`); it does not affect test execution or generated artifacts.

## Conclusion

Phase 2R.1.3 is accepted for offline readiness. It closes the prior V25B projection, semantic-diagram, source-grouping, and watermark blockers without leaking Learning Golden Card data into model input.

The next phase, only with explicit approval, is a bounded real V25/V25B smoke. It must use new request identities, preserve draft-only results, stop on any timeout/5xx/unknown outcome/schema/Gate failure, and must not enter Step 3/4 or a 10/65-chapter run.
