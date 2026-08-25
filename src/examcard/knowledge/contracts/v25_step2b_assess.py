"""step2b 响应评定（拆分层 4）。

逐条核对模型给出的卡片提案与归组义务是否对齐，结果落 ``V25BGroupingGate``；
与 step2a 侧同口径：登记失败，不就地熔断。
"""
from __future__ import annotations

from .v25_step2b_models import V25BGroupingGate, V25BStep2BRequest, V25BStep2BResultPayload


def assess_v25b_step2b_response(*, request: V25BStep2BRequest, response: V25BStep2BResultPayload) -> V25BGroupingGate:
    failures: list[str] = []
    card_sets = [set(card.primary_fact_ids) for card in response.proposals]
    exact = 0
    missing = 0
    split = 0
    merged = 0
    wrong_presentation = 0
    for obligation in request.grouping_obligations:
        required = set(obligation.member_fact_ids)
        matches = [index for index, fact_ids in enumerate(card_sets) if fact_ids == required]
        intersects = [index for index, fact_ids in enumerate(card_sets) if fact_ids & required]
        if len(matches) == 1:
            card = response.proposals[matches[0]]
            if card.presentation != obligation.required_presentation:
                wrong_presentation += 1
                failures.append(f"{obligation.obligation_id}:wrong_presentation")
            else:
                exact += 1
        elif not intersects:
            missing += 1
            failures.append(f"{obligation.obligation_id}:missing_card")
        elif len(intersects) > 1:
            split += 1
            failures.append(f"{obligation.obligation_id}:split_group")
        else:
            merged += 1
            failures.append(f"{obligation.obligation_id}:merged_group")
    known_fact_ids = {fact.fact_id for fact in request.facts}
    extra_cards = sum(bool(set(card.primary_fact_ids) - known_fact_ids) for card in response.proposals)
    if extra_cards:
        failures.append("response:unknown_fact")
    assigned = [fact_id for card in response.proposals for fact_id in card.primary_fact_ids]
    exactly_once = set(assigned) == known_fact_ids and len(assigned) == len(set(assigned))
    if not exactly_once:
        failures.append("response:not_exactly_once")
    valid = not failures and exact == len(request.grouping_obligations)
    return V25BGroupingGate(
        request_id=request.identity.request_id,
        valid=valid,
        failure_codes=tuple(sorted(set(failures))),
        exact_obligation_count=exact,
        missing_obligation_count=missing,
        split_obligation_count=split,
        merged_obligation_count=merged,
        wrong_presentation_count=wrong_presentation,
        extra_card_count=extra_cards,
        exactly_once=exactly_once,
        basis=(
            "V25B checks source-table ownership, complete-table separation, typed rule families, and exactly-once Fact "
            "assignment without consuming Card Golden targets."
        ),
    )
