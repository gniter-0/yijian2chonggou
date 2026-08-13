"""echo 批 step3 人工评审摄取门禁 + 授权签发（P8 补链施工 #3；规范 = echo_step4_bridge_design.md §3.1/§3.3）。

形态与 §7 检查点同构：**物料 → 人工回填决定 → 严格摄取 → 放行**；决定文件是唯一权威输入。
评审执行权归 B2 权威口径（总指挥 20260813 冻结令）；本模块只摄取与签发，不代行评审。
待审底稿的生成器在 `compilers/step4_echo_bridge.emit_echo_step3_review_sheet`（其消费
compile_reviewed_fixture——A 案依赖方向纪律禁止 gates↔compilers 边，编译面归 compilers）。

防伪造（B1 冒签事故后的组规）：每张 approved 卡必携 **b2_review 取证块**（非空对象）；
单纯 reviewer 字符串不再单独可信。取证块 canonical_hash 即 review_signoff_sha256，
随 release 卡谱系四哈希入库。

授权签发（§3.3）：`issue_echo_batch_handoff` 是 `EchoBatchStep4Handoff` 的**唯一签发口**——
只有摄取门禁放行（released 非空）后才可调用；把 r319「批产不授权」常量翻转为人工签发的
正向事实。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ...common.hashing import canonical_hash, hash_model
from ...knowledge.contracts.echo_release import (
    EchoBatchCardDisposition,
    EchoBatchReviewedReleaseFixture,
    EchoBatchStep4Handoff,
)

__all__ = (
    "ingest_echo_step3_review",
    "issue_echo_batch_handoff",
)


def ingest_echo_step3_review(*, sheet_path: Path) -> dict[str, Any]:
    """严格摄取：全卡有 disposition 才走；approved 行须携真实签名 + b2_review 取证块。

    返回：waiting_human（缺回填）/ refused（签名或取证块不合规，fail-closed）/
    released（approved 行集合，含 review_signoff_sha256——取证块 canonical_hash）。
    """

    sheet = json.loads(Path(sheet_path).read_text(encoding="utf-8"))
    cards = sheet.get("cards") or []
    if not cards:
        return {"status": "refused", "detail": "review sheet has no cards"}
    undecided = [card["card_seq"] for card in cards if card.get("disposition") is None]
    if undecided:
        return {"status": "waiting_human", "detail": f"cards without disposition: {undecided}"}
    released: list[dict[str, Any]] = []
    for card in cards:
        disposition = card["disposition"]
        if disposition not in {"approved", "rejected", "manual_review"}:
            return {"status": "refused", "detail": f"card {card['card_seq']} has an unknown disposition: {disposition}"}
        if disposition != "approved":
            continue
        reviewer = str(card.get("reviewer") or "")
        if not reviewer or reviewer.startswith("<"):
            return {"status": "refused", "detail": f"card {card['card_seq']} lacks a real reviewer signature"}
        # 冒签事故（20260813 检查点①实录）后的组规：approved 必携逐项取证块。
        evidence = card.get("b2_review")
        if not isinstance(evidence, dict) or not evidence:
            return {"status": "refused", "detail": f"card {card['card_seq']} lacks the b2_review evidence block (forgery guard)"}
        if not str(card.get("basis") or "").strip():
            return {"status": "refused", "detail": f"card {card['card_seq']} lacks a review basis"}
        released.append(
            {
                "card_seq": int(card["card_seq"]),
                "contract_id": str(card["contract_id"]),
                "reviewer": reviewer,
                "basis": str(card["basis"]),
                "review_signoff_sha256": canonical_hash(evidence),
            }
        )
    return {
        "status": "released" if released else "refused",
        "detail": f"released={len(released)}" if released else "no approved cards after review",
        "released": released,
        "sheet_sha256": hashlib.sha256(Path(sheet_path).read_bytes()).hexdigest(),
    }


def issue_echo_batch_handoff(
    *,
    release: EchoBatchReviewedReleaseFixture,
    authorized_by: str,
    authorization_evidence: Mapping[str, Any],
) -> EchoBatchStep4Handoff:
    """授权签发（唯一签发口）：release 卡集 → 全 pending 处置的新批 handoff。

    ``authorization_evidence``：签发人的 b2_review 取证块（非空），canonical_hash 入
    `authorization_evidence_sha256`。增量重渲批（携旧图 reviewed 引用）留待后续需要时
    扩展本函数——当前新批语义缺省全 pending（设计 §3.3）。
    """

    if not authorized_by.strip() or authorized_by.startswith("<"):
        raise ValueError("handoff issuance requires a real authorizer signature")
    if not isinstance(authorization_evidence, Mapping) or not authorization_evidence:
        raise ValueError("handoff issuance requires a non-empty b2_review evidence block")
    payload = {
        "batch_id": release.batch_id,
        "source_release_sha256": release.fixture_sha256,
        "card_count": release.card_count,
        "cards": tuple(
            EchoBatchCardDisposition(card_index=card.card_index, source_card_id=card.source_card_id, disposition="pending")
            for card in release.cards
        ),
        "reviewed_image_references": (),
        "authorized_by": authorized_by,
        "authorization_evidence_sha256": canonical_hash(dict(authorization_evidence)),
    }
    return hash_model(EchoBatchStep4Handoff, payload, "handoff_sha256")
