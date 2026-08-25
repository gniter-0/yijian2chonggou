"""step2a 响应评定（拆分层 4）。

把模型返回逐条对着请求里的义务核：锚点要落在给定 span 内、数值 token 要属声明的类、
断言摘要要与原文对上。评定结果落 ``V25Step2AGate``，**失败不抛异常而是记进门禁**——
去留由上层预检决定，这条纪律拆分后仍在同一处。
"""
from __future__ import annotations

from ..schema import FactType

from . import normalized_compare_text

from .v25_step2a_prompt import _section_anchor_map
from .v25_step2a_request import (
    V25Step2AGate,
    V25Step2AModelFactProposal,
    V25Step2ARequest,
    V25Step2AResultPayload,
)


def assess_v25_step2a_response(
    *, request: V25Step2ARequest, response: V25Step2AResultPayload
) -> V25Step2AGate:
    """Fail closed on unknown, extra, incomplete, or heading-only source selection."""

    selectable = _section_anchor_map(request.sections)
    context_only = {
        span.anchor_span_id
        for section in request.sections
        for item in section.text_evidence
        if item.context_only
        for span in item.anchor_spans
    }
    failures: list[str] = []
    heading_only_failures = 0
    unsupported_diagram_fact_count = 0
    selected_by_proposal: list[tuple[V25Step2AModelFactProposal, set[str]]] = []
    for index, proposal in enumerate(response.proposals):
        selected = set(proposal.primary_anchor_span_ids)
        unknown = selected - set(selectable)
        if unknown:
            failures.append(f"proposal_{index}:unknown_anchor")
        if selected and selected <= context_only:
            failures.append(f"proposal_{index}:heading_only")
            heading_only_failures += 1
        if proposal.fact_type == FactType.DIAGRAM_RELATION:
            failures.append(f"proposal_{index}:diagram_requires_semantic_v2")
            unsupported_diagram_fact_count += 1
        selected_by_proposal.append((proposal, selected))

    complete = 0
    for obligation in request.coverage_obligations:
        required = set(obligation.required_primary_anchor_span_ids)
        same_type = [
            selected
            for proposal, selected in selected_by_proposal
            if proposal.fact_type == obligation.expected_fact_type
        ]
        exact = [selected for selected in same_type if selected == required]
        supersets = [selected for selected in same_type if required < selected]
        subsets = [selected for selected in same_type if selected < required]
        if len(exact) == 1:
            complete += 1
        elif len(exact) > 1:
            failures.append(f"{obligation.obligation_id}:duplicate_obligation_fact")
        elif supersets:
            failures.append(f"{obligation.obligation_id}:extra_anchor")
        elif subsets:
            failures.append(f"{obligation.obligation_id}:incomplete_anchor")
        else:
            failures.append(f"{obligation.obligation_id}:missing_fact")
    for atomic in request.atomic_source_obligations:
        required = set(atomic.required_primary_anchor_span_ids)
        atomic_candidates = [
            proposal
            for proposal, selected in selected_by_proposal
            if proposal.fact_type == atomic.expected_fact_type
        ]
        atomic_exact = [item for item in atomic_candidates if set(item.primary_anchor_span_ids) == required]
        atomic_supersets = [item for item in atomic_candidates if required < set(item.primary_anchor_span_ids)]
        atomic_subsets = [item for item in atomic_candidates if set(item.primary_anchor_span_ids) < required]
        if len(atomic_exact) == 1:
            assertion = normalized_compare_text(atomic_exact[0].assertion)
            expected_assertion = normalized_compare_text(atomic.required_source_assertion)
            if expected_assertion not in assertion:
                failures.append(f"{atomic.obligation_id}:assertion_incomplete")
            elif any(
                normalized_compare_text(expression.value) not in assertion
                for expression in atomic.numeric_expressions
            ):
                failures.append(f"{atomic.obligation_id}:numeric_expression_missing")
            elif any(
                operator.value not in atomic_exact[0].assertion for operator in atomic.required_condition_operators
            ):
                failures.append(f"{atomic.obligation_id}:condition_operator_missing")
            else:
                complete += 1
        elif len(atomic_exact) > 1:
            failures.append(f"{atomic.obligation_id}:duplicate_obligation_fact")
        elif atomic_supersets:
            failures.append(f"{atomic.obligation_id}:extra_anchor")
        elif atomic_subsets:
            failures.append(f"{atomic.obligation_id}:incomplete_anchor")
        else:
            failures.append(f"{atomic.obligation_id}:missing_fact")
    for text_obligation in request.text_source_obligations:
        required = set(text_obligation.required_primary_anchor_span_ids)
        text_candidates = [
            proposal
            for proposal, selected in selected_by_proposal
            if proposal.fact_type == text_obligation.expected_fact_type
        ]
        text_exact = [item for item in text_candidates if set(item.primary_anchor_span_ids) == required]
        text_supersets = [item for item in text_candidates if required < set(item.primary_anchor_span_ids)]
        text_subsets = [item for item in text_candidates if set(item.primary_anchor_span_ids) < required]
        if len(text_exact) == 1:
            assertion = normalized_compare_text(text_exact[0].assertion)
            expected_assertion = normalized_compare_text(text_obligation.model_assertion)
            if expected_assertion not in assertion:
                failures.append(f"{text_obligation.obligation_id}:assertion_incomplete")
            elif any(
                normalized_compare_text(expression.value) not in assertion
                for expression in text_obligation.numeric_expressions
            ):
                failures.append(f"{text_obligation.obligation_id}:numeric_expression_missing")
            elif any(
                operator.value not in text_exact[0].assertion
                for operator in text_obligation.required_condition_operators
            ):
                failures.append(f"{text_obligation.obligation_id}:condition_operator_missing")
            else:
                complete += 1
        elif len(text_exact) > 1:
            failures.append(f"{text_obligation.obligation_id}:duplicate_obligation_fact")
        elif text_supersets:
            failures.append(f"{text_obligation.obligation_id}:extra_anchor")
        elif text_subsets:
            failures.append(f"{text_obligation.obligation_id}:incomplete_anchor")
        else:
            failures.append(f"{text_obligation.obligation_id}:missing_fact")
    for list_obligation in request.list_source_obligations:
        required = set(list_obligation.required_primary_anchor_span_ids)
        list_candidates = [
            proposal
            for proposal, selected in selected_by_proposal
            if proposal.fact_type == list_obligation.expected_fact_type
        ]
        list_exact = [item for item in list_candidates if set(item.primary_anchor_span_ids) == required]
        list_supersets = [item for item in list_candidates if required < set(item.primary_anchor_span_ids)]
        list_subsets = [item for item in list_candidates if set(item.primary_anchor_span_ids) < required]
        if len(list_exact) == 1:
            assertion = normalized_compare_text(list_exact[0].assertion)
            expected_assertion = normalized_compare_text(list_obligation.model_assertion)
            if expected_assertion not in assertion:
                failures.append(f"{list_obligation.obligation_id}:assertion_incomplete")
            elif tuple(list_exact[0].primary_anchor_span_ids) != list_obligation.required_primary_anchor_span_ids:
                failures.append(f"{list_obligation.obligation_id}:anchor_order_mismatch")
            elif list_obligation.continuation_state == "local_only" and any(
                marker in list_exact[0].assertion.lower()
                for marker in ("跨页", "续页", "cross-page", "continuation")
            ):
                failures.append(f"{list_obligation.obligation_id}:unreviewed_cross_page_claim")
            else:
                complete += 1
        elif len(list_exact) > 1:
            failures.append(f"{list_obligation.obligation_id}:duplicate_obligation_fact")
        elif list_supersets:
            failures.append(f"{list_obligation.obligation_id}:extra_anchor")
        elif list_subsets:
            failures.append(f"{list_obligation.obligation_id}:incomplete_anchor")
        else:
            failures.append(f"{list_obligation.obligation_id}:missing_fact")
    obligation_count = (
        len(request.coverage_obligations)
        + len(request.atomic_source_obligations)
        + len(request.text_source_obligations)
        + len(request.list_source_obligations)
    )
    valid = not failures and complete == obligation_count
    return V25Step2AGate(
        request_id=request.identity.request_id,
        valid=valid,
        failure_codes=tuple(sorted(set(failures))),
        proposal_count=len(response.proposals),
        obligation_count=obligation_count,
        complete_obligation_count=complete,
        source_anchor_linked=all(
            all(anchor_id in selectable for anchor_id in proposal.primary_anchor_span_ids)
            for proposal in response.proposals
        ),
        heading_only_failures=heading_only_failures,
        unsupported_diagram_fact_count=unsupported_diagram_fact_count,
        basis=(
            "V25 checks source-local anchor ownership, exact structural and complete-atomic source coverage, and "
            "heading-only/diagram fail-closed behavior without consulting Learning Golden targets."
        ),
    )
