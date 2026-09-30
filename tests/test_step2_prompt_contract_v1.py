"""Offline tests for the v10 Step 2A model-output prompt contract."""
from __future__ import annotations

from examcard.knowledge.contracts import (
    PREVIOUS_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    Step2Stage,
    default_step2_prompt_template,
)


def _v10_prompt() -> str:
    return default_step2_prompt_template(
        Step2Stage.FACT_PLANNING,
        request_version=STEP2A_REQUEST_VERSION,
    )


def test_v10_prompt_names_the_current_proposal_fields() -> None:
    prompt = _v10_prompt()

    for field in (
        "proposal_key",
        "primary_anchor_span_ids",
        "source_section_ids",
        "numeric_claims",
        "anchor_span_id",
    ):
        assert field in prompt

    assert '"proposals"' in prompt
    assert '"proposal_key"' in prompt


def test_v10_prompt_requires_schema_compatible_proposal_keys() -> None:
    prompt = _v10_prompt()

    assert "^proposal:[a-z0-9][a-z0-9._-]*$" in prompt
    assert "lowercase ASCII letters" in prompt
    assert "digits" in prompt
    assert "dot" in prompt
    assert "underscore" in prompt
    assert "hyphen" in prompt
    assert "Uppercase letters" in prompt
    assert "whitespace" in prompt
    assert "Chinese characters" in prompt
    assert "slash" in prompt
    assert "backslash" in prompt
    assert "emoji" in prompt
    assert "all other punctuation" in prompt
    for example in (
        "proposal:composition-001",
        "proposal:table-composition-001",
        "proposal:roadbed-threshold-001",
        "proposal:Roadbed Threshold",
        "proposal:路基工程规则",
        "proposal:table/composition",
        "proposal:Table_2020",
    ):
        assert example in prompt
    assert "Before returning JSON, verify that every proposal_key satisfies this exact pattern" in prompt


def test_v10_prompt_requires_page_independent_content_independent_keys() -> None:
    prompt = _v10_prompt()

    assert "proposal_key is page-independent and content-independent" in prompt
    assert "must not depend on page number, source text, title, section name" in prompt
    assert "Chinese phrases, assertion, label, or table title" in prompt
    assert "Do not derive proposal_key directly from assertion, label, or table title" in prompt
    assert "Use a stable ASCII slug for every page and proposal" in prompt
    assert "if any key does not match the pattern, regenerate only its stable slug/key" in prompt
    assert "without changing the proposal content" in prompt
    for invalid in (
        "proposal:道路工程",
        "proposal:Road-001",
        "proposal:page-2/section",
        "proposal:table 001",
    ):
        assert invalid in prompt
    for valid in (
        "proposal:roadbed-001",
        "proposal:table-composition-001",
        "proposal:threshold-45-001",
    ):
        assert valid in prompt


def test_v10_prompt_forbids_legacy_replacements_and_anchor_alias() -> None:
    prompt = _v10_prompt()

    assert "Do not return legacy knowledge_points, content_anchors, or source_pages fields" in prompt
    assert "Do not use anchor_span_ids as a replacement for primary_anchor_span_ids" in prompt


def test_v10_prompt_matches_the_existing_numeric_claim_contract() -> None:
    prompt = _v10_prompt()

    assert "numeric_claims.value is a JSON string" in prompt
    assert "do not emit a JSON number" in prompt
    assert "do not add a separate unit field" in prompt
    assert '"value":"30kg"' in prompt


def test_v10_prompt_requires_complete_table_and_atomic_grounding() -> None:
    prompt = _v10_prompt()

    assert 'fact_type="composition"' in prompt
    assert "complete table grid" in prompt
    assert "all table cell anchors" in prompt
    assert "A composition Fact never substitutes for atomic table Facts" in prompt
    assert "threshold, condition, rule, or exception" in prompt
    assert "Every atomic table Fact must preserve its required assertion" in prompt


def test_v10_prompt_requires_exact_numeric_grounding_and_preserves_exclusions() -> None:
    prompt = _v10_prompt()

    assert "Every source-backed quantitative expression in assertion requires one" in prompt
    assert "anchor_span_id must point to a selected Evidence anchor containing the exact value" in prompt
    assert "value must exactly copy the complete source expression" in prompt
    assert 'example {"value":"2025年"}' in prompt
    assert "never calculate, derive, or invent numbers" in prompt
    assert "Section numbers, list indexes, identifiers, material grades, and reference codes are not quantitative" in prompt


def test_v10_prompt_forbids_partial_table_and_anchor_loss() -> None:
    prompt = _v10_prompt()

    assert "never use only one row or a subset of cells to represent the complete table" in prompt
    assert "Do not summarize multiple Evidence items while dropping anchors" in prompt
    assert "Invalid example (placeholder IDs only)" in prompt
    assert '"span:<one-row-only>"' in prompt


def test_v10_prompt_requires_one_to_one_numeric_token_binding() -> None:
    prompt = _v10_prompt()

    assert "Assertion quantitative token binding" in prompt
    assert "Every quantitative expression recognized in assertion" in prompt
    assert "numeric_claim.value must equal the exact complete source expression" in prompt
    assert "numeric_claim.anchor_span_id must point to a selected Evidence anchor" in prompt
    assert "The binding is one-to-one: one quantitative token equals one numeric_claim" in prompt
    assert "Never omit a claim, merge multiple numbers into one claim, rewrite a number" in prompt
    assert "add a model-derived unit, or add a suffix not present in source" in prompt


def test_v10_prompt_splits_multi_token_numeric_claims() -> None:
    prompt = _v10_prompt()

    assert "Single-token numeric binding" in prompt
    assert "numeric_claim.value must correspond to a single quantitative token" in prompt
    assert "One numeric_claim must not cover multiple quantitative candidates" in prompt
    assert "multiple table cells, or multiple anchor spans" in prompt
    assert "emit one independent numeric_claim for each value" in prompt
    assert "each claim must use its own anchor_span_id" in prompt
    assert "bound to the corresponding selected anchor" in prompt
    assert "preserve that source expression exactly" in prompt
    assert "Never concatenate multiple numbers" in prompt
    assert "combine a year with a quantity" in prompt
    assert "create a multi-cell summary expression" in prompt
    assert "use one value to represent multiple source cells" in prompt


def test_v10_composition_numeric_claims_are_cell_granular() -> None:
    prompt = _v10_prompt()

    assert "Composition-specific numeric claim granularity" in prompt
    assert 'for fact_type="composition"' in prompt
    assert "every quantitative table-cell value must produce its own independent numeric_claim" in prompt
    assert "one numeric_claim = one source quantitative cell = one anchor_span_id" in prompt
    assert "A composition Fact's completeness is expressed jointly" in prompt
    assert "its individual numeric_claims, and its selected anchors" in prompt
    assert "never by a small set of summary numeric_claims" in prompt
    assert "A summary numeric claim must not cover multiple table cells" in prompt
    assert "multiple quantitative candidates, a year/value pair" in prompt
    assert "or an aggregation across years" in prompt
    assert "A composition proposal may contain multiple numeric_claims" in prompt
    assert "keep every source expression and anchor independent" in prompt


def test_v10_multi_token_numeric_rules_are_not_added_to_other_versions() -> None:
    prompt = _v10_prompt()
    previous_prompt = default_step2_prompt_template(
        Step2Stage.FACT_PLANNING,
        request_version=PREVIOUS_STEP2A_REQUEST_VERSION,
    )

    for fragment in (
        "Single-token numeric binding",
        "One numeric_claim must not cover multiple quantitative candidates",
        "emit one independent numeric_claim for each value",
        "create a multi-cell summary expression",
        "Composition-specific numeric claim granularity",
        "never by a small set of summary numeric_claims",
    ):
        assert fragment in prompt
        assert fragment not in previous_prompt


def test_v10_prompt_covers_numeric_examples_and_list_index_exclusions() -> None:
    prompt = _v10_prompt()

    assert 'bare-number source "数量为45"' in prompt
    assert 'assertion "数量为45"' in prompt
    assert '{"value":"45","anchor_span_id":"span:<quantity-anchor>"}' in prompt
    assert 'Unit-bearing source "设计年限为45年"' in prompt
    assert 'assertion "设计年限为45年"' in prompt
    assert '{"value":"45年","anchor_span_id":"span:<design-life-anchor>"}' in prompt
    assert 'source "设计年限为45年"; assertion "设计年限为45年"; numeric_claim {"value":"45",' not in prompt
    assert '45 maps to value "45"' in prompt
    assert '45年 maps to value "45年"' in prompt
    assert '45 题 maps to value "45 题"' in prompt
    assert 'whitespace-equivalent normalized form "45题"' in prompt
    assert "Do not strip a tokenizer-recognized source unit" in prompt
    assert "The unit-bearing example must not be reduced to the bare value" in prompt
    assert 'The list-index forms "2020、", "2021、", and "2022、" are not numeric_claims' not in prompt
    assert '"2020、"' in prompt
    assert '"2021、"' in prompt
    assert '"2022、"' not in prompt
    assert "Exclude a number from numeric_claims only when it semantically functions as a structural list index" in prompt
    assert "punctuation alone does not determine this classification" in prompt
    assert '"1、施工准备"' in prompt
    assert '"（2）测量"' in prompt
    assert '"第3条"' in prompt
    assert "Table-value punctuation rule" in prompt
    assert "do not append a Chinese enumeration comma, list marker, or sorting marker" in prompt
    assert "\"30、\"" in prompt
    assert "\"45题、\"" in prompt
    assert "never write \"2020、45题\" or \"2021、42\"" in prompt
    assert "ambiguous because the local tokenizer classifies them as LIST_INDEX" in prompt
    assert "Keep each cell value as its own source expression" in prompt
    assert "年份 2020 对应数量 45题" in prompt
    assert "年份 2021 对应数量 42" in prompt
    assert "preflight every composition assertion" in prompt
    assert "rewrite the prose separator while preserving the source value and cell boundary" in prompt
    assert "preserve each cell boundary" in prompt
    assert "Never join two independent table cells with a colon" in prompt
    assert "Do not merge a year header and a quantity cell into one quantitative expression" in prompt
    assert '"年份 2018 对应数量 20；年份 2019 对应数量 30；年份 2020 对应数量 45题。"' in prompt
    assert "Each quantitative value must receive its own numeric_claim" in prompt
    assert 'Never use a composite value such as {"value":"2018:20"}' in prompt
    assert "every number that the current tokenizer recognizes as quantitative_value requires its own numeric_claim" in prompt
    assert "including a year copied from a table header" in prompt
    assert "Bind each header-year claim independently to that header cell anchor" in prompt
    assert 'assertion "年份 2018 对应数量 20；年份 2019 对应数量 30。"' in prompt
    assert '{"value":"2018","anchor_span_id":"<2018 header cell>"}' in prompt
    assert '{"value":"20","anchor_span_id":"<20 value cell>"}' in prompt
    assert "Never omit the claim for 2018 merely because it is a header year" in prompt
    assert "If a year is only a table header, do not create a numeric_claim" not in prompt
    assert '"2018:20；2019:30；2020:45题；2021:42"' not in prompt
    assert "put assertion separators into numeric_claim.value" in prompt
