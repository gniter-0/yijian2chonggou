"""step2b 离线替身与离线日志（拆分层 4）。

``build_v25b_source_fake_response`` 是全家唯一被**反射**取到的名字：
``phase2r14_v25_live`` 里有一处 ``__import__('...v25_generic_contracts', fromlist=[...])``
取它（该文件另有同目标的顶层静态 import，那处反射是历史冗余）。反射取到的是**门面**模块
对象，所以这个名字必须始终从门面再导出得到——cm27 已把这条隐形边登记进
``tests/test_knowledge_contracts_layering.py`` 的 ``DYNAMIC_SIBLING_IMPORTS`` 并断言属性可解析。
"""
from __future__ import annotations

from . import Step2Stage

from .v25_versions import V25B_STEP2B_JOURNAL_VERSION
from .v25_support import _label_from_source
from .v25_step2a_request import V25JournalRecord
from .v25_step2b_models import V25BModelCardProposal, V25BStep2BRequest, V25BStep2BResultPayload


def build_v25b_source_fake_response(*, request: V25BStep2BRequest) -> V25BStep2BResultPayload:
    facts = {item.fact_id: item for item in request.facts}
    proposals = []
    for obligation in request.grouping_obligations:
        members = tuple(facts[item] for item in obligation.member_fact_ids)
        title = _label_from_source(" ".join(item.assertion_summary for item in members))
        proposals.append(
            V25BModelCardProposal(
                title=title,
                primary_fact_ids=obligation.member_fact_ids,
                presentation=obligation.required_presentation,
                split_reason="Source-derived grouping obligation keeps the owned Fact family together.",
            )
        )
    return V25BStep2BResultPayload(proposals=tuple(proposals))


def make_v25b_offline_journal(*, request: V25BStep2BRequest) -> V25JournalRecord:
    return V25JournalRecord(
        journal_version=V25B_STEP2B_JOURNAL_VERSION,
        request_id=request.identity.request_id,
        stage=Step2Stage.CARD_PLANNING,
        event_cursor=0,
        event_type="offline_contract_preflight",
        payload_sha256=request.identity.payload_sha256,
        response_schema_sha256=request.identity.response_schema_sha256,
        network_calls=0,
        state="offline_contract_preflight",
    )
