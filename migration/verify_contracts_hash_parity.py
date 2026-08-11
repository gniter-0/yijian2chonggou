"""Blueprint section 8-1 acceptance: byte-level hash parity, legacy vs split contracts.

Runs BOTH sides in one interpreter (the new repo venv) so the pydantic version is
identical and only the code move is under test:

1. source fidelity   - the legal hash implementations and the 30KB prompt template
                       must be byte-identical to the legacy sources (R7 / R2);
2. primitive parity  - canonical_step2_sha256 / fingerprint_* / every make_*_id and
                       proposal-key derivation agree on adversarial value corpora;
3. golden replay     - a real Step 2A request is built from the legacy golden page
                       through the LEGACY pipeline, then re-validated by the NEW
                       Step2ARequest (whose validators recompute and enforce every
                       nested self-hash), and identities are compared byte-for-byte;
4. step2b parity     - identical Step 2B requests built on both sides agree on
                       payload_sha256 / request_id / cache_key / idempotency_key.

The legacy repo is only read. Exit code 0 means parity holds.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

OLD_SCRIPTS = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts")
OLD_ROOT = OLD_SCRIPTS.parent
sys.path.insert(0, str(OLD_SCRIPTS))

import knowledge_v2.semantic_contracts as old_sc  # noqa: E402
from knowledge_v2.semantic_projection import build_step2a_semantic_projection  # noqa: E402
from materials_v2.catalog import load_content_policy  # noqa: E402
from evidence_v2.schema import PageEvidence  # noqa: E402

import examcard.knowledge.contracts as new_sc  # noqa: E402

FAILURES: list[str] = []


def check(label: str, old_value: object, new_value: object) -> None:
    if old_value != new_value:
        FAILURES.append(f"{label}: OLD={old_value!r} NEW={new_value!r}")


# ---------------------------------------------------------------------------
# 1. Source fidelity for the frozen implementations (R7 hashing, R2 prompt).
# ---------------------------------------------------------------------------
for name in (
    "_canonicalize",
    "canonical_step2_sha256",
    "_canonical_sha256",
    "fingerprint_step2_relay",
    "fingerprint_step2_prompt",
    "default_step2_prompt_template",
):
    old_fn = getattr(old_sc, name)
    new_fn = getattr(
        __import__("examcard.knowledge.contracts.hashing", fromlist=[name])
        if name != "default_step2_prompt_template"
        else __import__("examcard.knowledge.contracts.prompts", fromlist=[name]),
        name,
    )
    check(f"source[{name}]", inspect.getsource(old_fn), inspect.getsource(new_fn))

# ---------------------------------------------------------------------------
# 2. Primitive parity on adversarial corpora.
# ---------------------------------------------------------------------------


def corpus(module):
    return [
        {"b": 1, "a": [3, 2, {"z": None}], "中文": "≥65MPa，不得采用"},
        {"set": frozenset({"b", "a", "0"}), "nested": {"s": {3, 1, 2}}},
        [module.Step2Stage.FACT_PLANNING, module.Step2Stage.CARD_PLANNING],
        {"enum_map": {module.CardPresentation.TABLE: [module.NumericTokenClass.LIST_INDEX]}},
        {"float": 1.0, "int": 1, "bool": True, "none": None, "ratio": "1/400"},
        ("tuple", ["mixed", {"k": frozenset({"δ", "α"})}]),
    ]


for index, (old_value, new_value) in enumerate(zip(corpus(old_sc), corpus(new_sc))):
    check(
        f"canonical_step2_sha256[case {index}]",
        old_sc.canonical_step2_sha256(old_value),
        new_sc.canonical_step2_sha256(new_value),
    )

check(
    "fingerprint_step2_relay",
    old_sc.fingerprint_step2_relay("https://relay.example.com/v1/"),
    new_sc.fingerprint_step2_relay("https://relay.example.com/v1/"),
)

STAGE_VERSIONS = [
    ("FACT_PLANNING", None),
    ("FACT_PLANNING", "LEGACY_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "TABLE_CONTINUATION_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION"),
    ("FACT_PLANNING", "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION"),
    ("CARD_PLANNING", None),
]
for stage_name, version_name in STAGE_VERSIONS:
    old_prompt = old_sc.default_step2_prompt_template(
        getattr(old_sc.Step2Stage, stage_name),
        request_version=getattr(old_sc, version_name) if version_name else None,
    )
    new_prompt = new_sc.default_step2_prompt_template(
        getattr(new_sc.Step2Stage, stage_name),
        request_version=getattr(new_sc, version_name) if version_name else None,
    )
    check(f"prompt[{stage_name}/{version_name}]", old_prompt, new_prompt)
    check(
        f"prompt_fingerprint[{stage_name}/{version_name}]",
        old_sc.fingerprint_step2_prompt(old_prompt),
        new_sc.fingerprint_step2_prompt(new_prompt),
    )

SHA = "a" * 64


def id_calls(module):
    span_ref = None
    if hasattr(module, "NumericSpanReference"):
        span_ref = module.NumericSpanReference(start=2, end=9, sha256=SHA)
    return [
        (
            "make_evidence_anchor_span_id",
            dict(
                parent_evidence_id="ev:1",
                start=3,
                end=17,
                exact_text_sha256=SHA,
                content_policy_fingerprint_sha256=SHA,
                source_manifest_sha256=SHA,
            ),
        ),
        (
            "make_table_atomic_subfact_requirement_id",
            dict(
                table_evidence_id="t:1",
                cell_evidence_id="c:1",
                anchor_span_id="span:" + "b" * 24,
                kind=module.TableAtomicSubfactKind.THRESHOLD,
                required_assertion_sha256=SHA,
            ),
        ),
        (
            "make_continuation_record_id",
            dict(page_num=3, source_manifest_sha256=SHA, anchor_span_ids=("span:" + "b" * 24,)),
        ),
        (
            "make_continuation_binding_id",
            dict(
                continuation_id="continuation:" + "b" * 24,
                source_projection_id="step2a-projection:" + "b" * 24,
                source_manifest_sha256=SHA,
                source_page_num=3,
                source_section_id="section:" + "b" * 24,
                source_anchor_span_ids=("span:" + "b" * 24,),
                state=module.ContinuationBindingState.MANUAL_REVIEW,
                adjacent_projection_id=None,
                adjacent_manifest_sha256=None,
                adjacent_page_num=None,
                adjacent_section_id=None,
                closure_anchor_span_ids=(),
                closure_basis="No reviewed closure binding available.",
                failure_code=module.ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE,
            ),
        ),
        (
            "make_table_continuation_controlled_section_id",
            dict(page_num=4, source_manifest_sha256=SHA, binding_id="table-continuation:" + "b" * 24, evidence_ids=("e2", "e1")),
        ),
        ("make_table_continuation_request_input_id", dict(payload={"k": "v", "n": [1, 2]})),
        (
            "make_step2a_coverage_obligation_id",
            dict(
                kind=module.Step2ACoverageObligationKind.CLOSED_CONTINUATION,
                source_binding_id="continuation-binding:" + "b" * 24,
                table_evidence_id=None,
                atomic_requirement_id=None,
                expected_fact_type=_fact_type(module, "ENUMERATION"),
                required_primary_anchor_span_ids=("span:" + "b" * 24,),
                requires_all_items=True,
            ),
        ),
        ("make_step2a_coverage_obligation_input_id", dict(payload={"obligations": [1, 2]})),
        ("make_step2a_coverage_obligation_context_id", dict(payload={"ctx": True})),
        ("make_step2a_coverage_obligation_context_input_id", dict(payload={"ctx": [3]})),
        (
            "make_table_numeric_occurrence_id",
            dict(
                table_evidence_id="t:1",
                cell_evidence_id="c:1",
                page_num=2,
                row_index=1,
                column_index=0,
                row_span=1,
                col_span=2,
                anchor_span_id="span:" + "b" * 24,
                source_span=span_ref,
                source_manifest_sha256=SHA,
            ),
        ),
        (
            "make_table_structural_index_occurrence_id",
            dict(
                table_evidence_id="t:1",
                cell_evidence_id="c:1",
                page_num=2,
                row_index=1,
                column_index=0,
                row_span=1,
                col_span=1,
                anchor_span_id="span:" + "b" * 24,
                source_span=span_ref,
                source_manifest_sha256=SHA,
            ),
        ),
        (
            "make_table_structural_cell_occurrence_id",
            dict(
                table_evidence_id="t:1",
                cell_evidence_id="c:1",
                page_num=2,
                row_index=0,
                column_index=1,
                row_span=1,
                col_span=1,
                cell_text_sha256=SHA,
                anchor_span_ids=("span:" + "b" * 24,),
                source_manifest_sha256=SHA,
            ),
        ),
        (
            "make_table_grid_metadata_id",
            dict(
                table_evidence_id="t:1",
                page_nums=(2, 3),
                source_manifest_sha256s=(SHA,),
                row_count=4,
                column_count=3,
                cell_evidence_ids=("c:1", "c:2"),
                cell_anchor_span_ids=("span:" + "b" * 24,),
            ),
        ),
        (
            "make_complete_table_structural_projection_id",
            dict(
                proposal_key="proposal:" + "b" * 24,
                table_evidence_id="t:1",
                grid_metadata_id="table-grid-metadata:" + "b" * 24,
                cell_occurrence_ids=("table-structural-cell-occurrence:" + "b" * 24,),
                list_index_occurrence_ids=(),
            ),
        ),
    ]


def _fact_type(module, name):
    if module is old_sc:
        from knowledge_v2.schema import FactType as OldFactType

        return getattr(OldFactType, name)
    from examcard.knowledge.schema import FactType as NewFactType

    return getattr(NewFactType, name)


for (old_name, old_kwargs), (new_name, new_kwargs) in zip(id_calls(old_sc), id_calls(new_sc)):
    assert old_name == new_name
    old_result = getattr(old_sc, old_name)(**old_kwargs)
    new_result = getattr(new_sc, new_name)(**new_kwargs)
    check(f"id[{old_name}]", old_result, new_result)

for key_name in (
    "make_step2a_v13_proposal_key",
    "make_step2a_v14_proposal_key",
    "make_step2a_v15_proposal_key",
    "make_step2a_v16_proposal_key",
    "make_step2a_v17_proposal_key",
    "make_step2a_v18_proposal_key",
):
    kwargs = dict(assertion="吊环的拉应力应不大于65MPa", primary_anchor_span_ids=("span:" + "b" * 24,))
    if key_name in ("make_step2a_v13_proposal_key", "make_step2a_v14_proposal_key"):
        kwargs["secondary_anchor_span_ids"] = ("span:" + "c" * 24,)
    check(
        f"proposal_key[{key_name}]",
        getattr(old_sc, key_name)(fact_type=_fact_type(old_sc, "THRESHOLD"), **kwargs),
        getattr(new_sc, key_name)(fact_type=_fact_type(new_sc, "THRESHOLD"), **kwargs),
    )

# ---------------------------------------------------------------------------
# 3. Golden replay through the full request pipeline.
# ---------------------------------------------------------------------------
PAGE_PATH = OLD_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
POLICY_PATH = OLD_ROOT / "configs/v2/content_policies/core_only_v1.yaml"

page = PageEvidence.model_validate_json(PAGE_PATH.read_text(encoding="utf-8"))
built = build_step2a_semantic_projection(
    sample_id="gonglu_24_table_formula",
    page=page,
    source_manifest_path=PAGE_PATH,
    source_manifest_reference="data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json",
    content_policy=load_content_policy(POLICY_PATH),
)
old_request = old_sc.build_step2a_request(
    sample_id="gonglu_24_table_formula",
    material_profile_id="yijian_gonglu_2026",
    projection=built.projection,
    sections=(built.section,),
    model="deterministic-fake-v1",
    decoder=old_sc.Step2DecoderConfig(max_tokens=1000, response_format=old_sc.Step2ResponseFormat.JSON_SCHEMA_STRICT),
)
old_dump = old_request.model_dump(mode="json")
new_request = new_sc.Step2ARequest.model_validate(old_dump)  # every self-hash re-verified by NEW validators
for field in (
    "request_id",
    "cache_key",
    "idempotency_key",
    "payload_sha256",
    "prompt_sha256",
    "relay_fingerprint_sha256",
    "response_schema_sha256",
    "projection_id",
    "projection_sha256",
):
    check(f"golden identity[{field}]", getattr(old_request.identity, field), getattr(new_request.identity, field))
check("golden full dump", old_dump, new_request.model_dump(mode="json"))

new_rebuilt = new_sc.build_step2a_request(
    sample_id="gonglu_24_table_formula",
    material_profile_id="yijian_gonglu_2026",
    projection=new_sc.Step2ASemanticProjection.model_validate(built.projection.model_dump(mode="json")),
    sections=(new_sc.Step2ASectionInput.model_validate(built.section.model_dump(mode="json")),),
    model="deterministic-fake-v1",
    decoder=new_sc.Step2DecoderConfig(max_tokens=1000, response_format=new_sc.Step2ResponseFormat.JSON_SCHEMA_STRICT),
)
check("golden rebuilt request_id", old_request.identity.request_id, new_rebuilt.identity.request_id)
check("golden rebuilt cache_key", old_request.identity.cache_key, new_rebuilt.identity.cache_key)

# ---------------------------------------------------------------------------
# 4. Step 2B request parity on identical fact summaries.
# ---------------------------------------------------------------------------


def step2b_request(module):
    fact = module.FactSummaryInput(
        fact_id="fact:" + "d" * 24,
        fact_type=_fact_type(module, "CONDITION"),
        label="吊装拉应力规则",
        assertion_summary="吊环的拉应力应不大于65MPa",
        completeness_mode=_completeness(module, "ATOMIC"),
        must_cover=True,
    )
    return module.build_step2b_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        facts=(fact,),
        model="deterministic-fake-v1",
    )


def _completeness(module, name):
    if module is old_sc:
        from knowledge_v2.schema import CompletenessMode as OldMode

        return getattr(OldMode, name)
    from examcard.knowledge.schema import CompletenessMode as NewMode

    return getattr(NewMode, name)


old_2b = step2b_request(old_sc)
new_2b = step2b_request(new_sc)
for field in ("request_id", "cache_key", "idempotency_key", "payload_sha256", "prompt_sha256", "response_schema_sha256"):
    check(f"step2b identity[{field}]", getattr(old_2b.identity, field), getattr(new_2b.identity, field))

# ---------------------------------------------------------------------------
if FAILURES:
    print(f"HASH PARITY FAILED: {len(FAILURES)} mismatches")
    for failure in FAILURES[:40]:
        print(" -", failure[:400])
    raise SystemExit(1)
print("HASH PARITY OK: sources byte-identical, primitives/IDs/prompts/golden replay/step2b all equal")
