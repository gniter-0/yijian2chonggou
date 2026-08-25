"""step2b 归组义务派生与请求构建（拆分层 4）。

``derive_v25b_grouping_obligations`` 是构建的前半段：按来源族把事实摘要归组、定呈现方式；
``build_v25b_step2b_request`` 紧接着装请求并自证。两者是同一条链，故同住。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from ..schema import FactType

from . import (
    CardPresentation,
    Step2DecoderConfig,
    Step2Stage,
    canonical_step2_sha256,
    make_step2_request_identity,
)

from .v25_versions import (
    V25B_STEP2B_MODEL_INPUT_VERSION,
    V25B_STEP2B_PROMPT_VERSION,
    V25B_STEP2B_REQUEST_VERSION,
)
from .v25_support import _OFFLINE_RELAY_BASE_URL, _v25_id
from .v25_enums import V25BGroupingKind, V25BSourceFamilyKind, _SOURCE_FAMILY_PRESENTATION
from .v25_step2b_prompt import _presentation_for_fact_type, v25b_step2b_prompt_template
from .v25_step2b_models import (
    V25BGroupingObligation,
    V25BSourceFamilyBinding,
    V25BStep2BRequest,
    V25BStep2BResultPayload,
    V25Step2BFactSummary,
)


def derive_v25b_grouping_obligations(
    *,
    facts: Iterable[V25Step2BFactSummary],
    source_family_bindings: Iterable[V25BSourceFamilyBinding] = (),
) -> tuple[V25BGroupingObligation, ...]:
    """Derive generic grouping from reviewed Facts and source-table provenance only."""

    frozen = tuple(facts)
    if not frozen:
        raise ValueError("V25B grouping requires at least one Fact")
    groups: list[tuple[V25BGroupingKind, tuple[V25Step2BFactSummary, ...], CardPresentation, str | None, str | None]] = []
    consumed: set[str] = set()
    by_id = {fact.fact_id: fact for fact in frozen}
    for binding in source_family_bindings:
        if any(fact_id not in by_id for fact_id in binding.member_fact_ids):
            raise ValueError("V25B source-family binding cannot reference an unknown Fact")
        if any(fact_id in consumed for fact_id in binding.member_fact_ids):
            raise ValueError("V25B source-family bindings cannot overlap")
        members = tuple(by_id[fact_id] for fact_id in binding.member_fact_ids)
        member_evidence_ids = {item for fact in members for item in fact.primary_evidence_ids}
        member_anchor_ids = {item for fact in members for item in fact.primary_anchor_span_ids}
        if not set(binding.source_evidence_ids) <= member_evidence_ids:
            raise ValueError("V25B source-family Evidence must be owned by its member Facts")
        if not set(binding.source_anchor_span_ids) <= member_anchor_ids:
            raise ValueError("V25B source-family anchors must be owned by its member Facts")
        kind, presentation = _SOURCE_FAMILY_PRESENTATION[binding.family_kind]
        table_ids = tuple(dict.fromkeys(table_id for fact in members for table_id in fact.source_table_ids))
        table_id = table_ids[0] if binding.family_kind == V25BSourceFamilyKind.TABLE_CLASSIFICATION and len(table_ids) == 1 else None
        topic_key: str | None = _v25_id("source-topic", {"family_id": binding.family_id, "source_manifest_sha256s": binding.source_manifest_sha256s})
        if kind == V25BGroupingKind.STANDALONE:
            table_id = None
            topic_key = None
        groups.append((kind, members, presentation, table_id, topic_key))
        consumed.update(binding.member_fact_ids)
    by_table: dict[str, list[V25Step2BFactSummary]] = defaultdict(list)
    for fact in frozen:
        if fact.fact_id not in consumed and len(fact.source_table_ids) == 1:
            by_table[fact.source_table_ids[0]].append(fact)
    for table_id, table_members in sorted(by_table.items()):
        topic_key = _v25_id("source-topic", {"source_table_id": table_id})
        compositions = [item for item in table_members if item.fact_type == FactType.COMPOSITION]
        for fact in compositions:
            groups.append((V25BGroupingKind.TABLE_COMPOSITION, (fact,), CardPresentation.TABLE, table_id, topic_key))
            consumed.add(fact.fact_id)
        conditions = tuple(
            item
            for item in table_members
            if item.fact_type in {FactType.CONDITION, FactType.EXCEPTION} and item.fact_id not in consumed
        )
        thresholds = tuple(
            item for item in table_members if item.fact_type == FactType.THRESHOLD and item.fact_id not in consumed
        )
        if conditions:
            rule_members = (*conditions, *thresholds)
            groups.append((V25BGroupingKind.CONSTRAINT_FAMILY, rule_members, CardPresentation.CONSTRAINT, table_id, topic_key))
            consumed.update(item.fact_id for item in rule_members)
        if thresholds:
            remaining = tuple(item for item in thresholds if item.fact_id not in consumed)
            if remaining:
                groups.append((V25BGroupingKind.THRESHOLD_FAMILY, remaining, CardPresentation.THRESHOLD, table_id, topic_key))
                consumed.update(item.fact_id for item in remaining)
    for fact in frozen:
        if fact.fact_id not in consumed:
            groups.append((V25BGroupingKind.STANDALONE, (fact,), _presentation_for_fact_type(fact.fact_type), None, None))
            consumed.add(fact.fact_id)
    obligations: list[V25BGroupingObligation] = []
    for kind, members, presentation, source_table_id, source_topic_key in groups:
        member_ids = tuple(item.fact_id for item in members)
        payload = {
            "grouping_kind": kind.value,
            "member_fact_ids": member_ids,
            "source_table_id": source_table_id,
            "source_topic_key": source_topic_key,
            "required_presentation": presentation.value,
        }
        obligations.append(V25BGroupingObligation(obligation_id=_v25_id("v25b-obligation", payload), **payload))
    return tuple(sorted(obligations, key=lambda item: item.obligation_id))


def build_v25b_step2b_request(
    *,
    sample_id: str,
    material_profile_id: str,
    facts: tuple[V25Step2BFactSummary, ...],
    source_family_bindings: tuple[V25BSourceFamilyBinding, ...] = (),
    model: str = "deterministic-fake-llm",
    relay_base_url: str = _OFFLINE_RELAY_BASE_URL,
) -> V25BStep2BRequest:
    obligations = derive_v25b_grouping_obligations(facts=facts, source_family_bindings=source_family_bindings)
    draft = {
        "sample_id": sample_id,
        "material_profile_id": material_profile_id,
        "model_input_version": V25B_STEP2B_MODEL_INPUT_VERSION,
        "prompt_version": V25B_STEP2B_PROMPT_VERSION,
        "facts": facts,
        "grouping_obligations": obligations,
        "source_family_bindings": source_family_bindings,
    }
    payload = {
        "stage": "step2b",
        "model_input_version": draft["model_input_version"],
        "prompt_version": draft["prompt_version"],
        "sample_id": sample_id,
        "facts": [
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type.value,
                "assertion_summary": fact.assertion_summary,
                "primary_evidence_ids": fact.primary_evidence_ids,
                "primary_anchor_span_ids": fact.primary_anchor_span_ids,
                "source_table_ids": fact.source_table_ids,
                "source_topic_key": fact.source_topic_key,
                "semantic_diagram_bridge": (
                    fact.semantic_diagram_bridge.model_dump(mode="json") if fact.semantic_diagram_bridge is not None else None
                ),
            }
            for fact in facts
        ],
        "grouping_context": [
            {
                "grouping_kind": obligation.grouping_kind.value,
                "member_fact_ids": obligation.member_fact_ids,
                "source_table_id": obligation.source_table_id,
                "source_topic_key": obligation.source_topic_key,
                "required_presentation": obligation.required_presentation.value,
            }
            for obligation in obligations
        ],
        "source_grouping_provenance": [
            {
                "family_kind": binding.family_kind.value,
                "member_fact_ids": binding.member_fact_ids,
                "source_evidence_ids": binding.source_evidence_ids,
                "source_anchor_span_ids": binding.source_anchor_span_ids,
                "source_manifest_sha256s": binding.source_manifest_sha256s,
            }
            for binding in source_family_bindings
        ],
    }
    identity = make_step2_request_identity(
        stage=Step2Stage.CARD_PLANNING,
        request_version=V25B_STEP2B_REQUEST_VERSION,
        model=model,
        payload_sha256=canonical_step2_sha256(payload),
        relay_base_url=relay_base_url,
        prompt_template=v25b_step2b_prompt_template(),
        response_schema=V25BStep2BResultPayload.model_json_schema(),
        decoder=Step2DecoderConfig(temperature=0, max_tokens=1800),
        manual_reissue_reason=None,
    )
    return V25BStep2BRequest(identity=identity, **draft)
