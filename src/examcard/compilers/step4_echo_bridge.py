"""echo 批 → step4 装载桥（P8 补链施工 #4；规范 = echo_step4_bridge_design.md §3.4）。

判据（设计 §2）：`GenericCardContract` 之后的一切已被 b2 彩排实弹验证——本桥只负责把
echo 批**送到卡形消费面为止**：`run_step4_stage` 下游循环消费 `.cards[]` 的
card_index / source_card_id / sample_id / render_presentation 与 prompt 构建器的
duck 契约（visible_texts / diagram_node_ids / semantic_layout_edges / table_contracts），
`EchoBatchReleaseCard` 逐字段对齐（echo_release.py），无需触碰 pilot 任何模型。

`load_step4_bundle_source` 是装载接缝的唯一入口：pilot-release 分支调用现
`derive_step4_bundle`（零改动路径，r2242 彩排/回归照旧）；echo-batch 分支走本桥。

本模块同时承载 **release 构建器与评审底稿生成器**（二者消费 `compile_reviewed_fixture`）：
A 案依赖方向纪律禁止 knowledge→compilers 与 gates↔compilers 边，故凡需编译面的
桥件一律落本包；纯摄取/签发在 gates/review/echo_step3_review.py。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..materials.schema import StrictModel
from ..knowledge.contracts.echo_release import (
    EchoBatchReleaseCard,
    EchoBatchReviewedReleaseFixture,
    EchoBatchStep4Handoff,
    EchoReleaseSemanticEdge,
    EchoReleaseTableCell,
    EchoReleaseTableView,
    EchoReleaseVisibleText,
    load_echo_handoff,
    load_echo_release_fixture,
)

__all__ = (
    "EchoStep4Bundle",
    "EchoStep4InputView",
    "build_echo_release_fixture",
    "emit_echo_step3_review_sheet",
    "derive_step4_bundle_echo",
    "load_step4_bundle_source",
)

ECHO_REVIEW_SHEET_NAME = "echo_step3_review_sheet.json"


class EchoStep4Bundle(StrictModel):
    """run_step4_stage 消费面（`.cards`）；prior reviewed 跳过集合另行暴露。"""

    batch_id: str = Field(min_length=1)
    source_release_sha256: str = Field(pattern=SHA256_HEX)
    source_handoff_sha256: str = Field(pattern=SHA256_HEX)
    cards: tuple[EchoBatchReleaseCard, ...] = Field(min_length=1)
    prior_reviewed_indexes: tuple[int, ...] = ()
    bundle_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_bundle(self) -> "EchoStep4Bundle":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"bundle_sha256"}))
        if self.bundle_sha256 != expected:
            raise ValueError("echo step4 bundle hash mismatch")
        return self


class EchoStep4InputView(StrictModel):
    """run_step4_stage 报告元数据消费面（`.fact_count`）。"""

    fact_count: int = Field(ge=1)


def derive_step4_bundle_echo(
    release: EchoBatchReviewedReleaseFixture,
    handoff: EchoBatchStep4Handoff,
    *,
    release_sha256: str,
    handoff_sha256: str,
) -> tuple[EchoStep4Bundle, EchoStep4InputView]:
    """release + 已授权 handoff → step4 消费面（互证 fail-closed）。

    保留 pilot derive 的两语义（设计 §1 第三行）：卡数互证；prior_reviewed 跳过集合
    （增量重渲批携旧图 reviewed 引用时，run_step4_stage 的 PNG 断点语义会自然跳过——
    本桥同时把下标集合显式暴露供报告审计）。
    """

    if handoff.batch_id != release.batch_id:
        raise ValueError("echo handoff batch id does not match the release fixture")
    if handoff.source_release_sha256 != release.fixture_sha256:
        raise ValueError("echo handoff does not bind this release fixture (sha mismatch)")
    if handoff.card_count != release.card_count:
        raise ValueError("echo handoff card count does not match the release fixture")
    release_ids = {card.card_index: card.source_card_id for card in release.cards}
    for item in handoff.cards:
        if release_ids.get(item.card_index) != item.source_card_id:
            raise ValueError(f"echo handoff disposition disagrees with release card {item.card_index}")
    payload = {
        "batch_id": release.batch_id,
        "source_release_sha256": release_sha256,
        "source_handoff_sha256": handoff_sha256,
        "cards": tuple(release.cards),
        "prior_reviewed_indexes": tuple(
            sorted(item.card_index for item in handoff.cards if item.disposition == "reviewed")
        ),
    }
    encoded = {**payload, "cards": tuple(card.model_dump(mode="json") for card in payload["cards"])}
    bundle = EchoStep4Bundle.model_validate({**payload, "bundle_sha256": canonical_hash(encoded)})
    fact_ids = {fact_id for card in release.cards for fact_id in card.fact_ids}
    return bundle, EchoStep4InputView(fact_count=len(fact_ids))


def load_step4_bundle_source(
    *,
    source: Literal["pilot-release", "echo-batch"],
    release_fixture_path: Path,
    handoff_path: Path,
) -> tuple[Any, Any]:
    """装载接缝唯一入口（设计 §3.4）：返回 (bundle_view, input_view)。

    - ``pilot-release``：现 `derive_step4_bundle` 零改动路径（r319/r2242 形状照旧）。
    - ``echo-batch``：本桥新路径（新版本号家族）。
    """

    fixture_path = Path(release_fixture_path)
    handoff_file = Path(handoff_path)
    fixture_bytes = fixture_path.read_bytes()
    handoff_bytes = handoff_file.read_bytes()
    if source == "pilot-release":
        import yaml

        from ..knowledge.contracts.admission_fixtures import (
            Step3UnifiedReviewedReleaseFixture,
            Step4PilotHandoffRefresh,
        )
        from .step4_contract import derive_step4_bundle

        fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
        handoff = Step4PilotHandoffRefresh.model_validate_json(handoff_bytes.decode("utf-8"))
        bundle, model_input, _request, _artifacts, _batch = derive_step4_bundle(
            fixture,
            handoff,
            unified_fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
            handoff_sha256=hashlib.sha256(handoff_bytes).hexdigest(),
        )
        return bundle, model_input
    release = load_echo_release_fixture(fixture_path)
    handoff = load_echo_handoff(handoff_file)
    return derive_step4_bundle_echo(
        release,
        handoff,
        release_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        handoff_sha256=hashlib.sha256(handoff_bytes).hexdigest(),
    )


# ---------------------------------------------------------------------------
# 评审底稿生成器 + release 构建器（§3.1/§3.2 的编译消费面）
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _compile_contracts(typed_fixture_path: Path):
    from ..knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture
    from .step3_card import compile_reviewed_fixture

    typed_bytes = Path(typed_fixture_path).read_bytes()
    typed_sha = hashlib.sha256(typed_bytes).hexdigest()
    typed_fixture = ReviewedTypedEvidenceFixture.model_validate_json(typed_bytes.decode("utf-8"))
    compilation = compile_reviewed_fixture(typed_fixture, source_fixture_sha256=typed_sha)
    return typed_sha, compilation


def emit_echo_step3_review_sheet(
    *,
    echo_run_dir: Path,
    typed_fixture_path: Path,
    out_dir: Path,
) -> Path:
    """echo run 逐卡实录 + typed fixture 契约 → 待审底稿（disposition 空位待人工回填，§3.1）。"""

    typed_sha, compilation = _compile_contracts(typed_fixture_path)
    cards = []
    for sequence, contract in enumerate(compilation.contracts, start=1):
        record_dir = Path(echo_run_dir) / "step3" / "cards" / f"{sequence:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
        raw_files = sorted(record_dir.glob("attempt-*.response.raw.json"))
        gate_files = sorted(record_dir.glob("attempt-*.gate.json"))
        if not raw_files:
            raise ValueError(f"echo run lacks a response record for contract {contract.contract_id}: {record_dir}")
        last_gate = json.loads(gate_files[-1].read_text(encoding="utf-8")) if gate_files else None
        cards.append(
            {
                "card_seq": sequence,
                "contract_id": contract.contract_id,
                "presentation": contract.presentation,
                "sample_id": contract.display_elements[0].provenance.subject_id,
                "echo_record_sha256": hashlib.sha256(raw_files[-1].read_bytes()).hexdigest(),
                "echo_gate_valid": bool(last_gate.get("valid")) if last_gate is not None else None,
                "key_texts": [element.text for element in contract.display_elements[:3]],
                "disposition": None,
                "reviewer": None,
                "basis": None,
                "b2_review": None,
            }
        )
    sheet = {
        "schema": "echo-step3-review-sheet/1.0",
        "source_typed_fixture_path": Path(typed_fixture_path).as_posix(),
        "source_typed_fixture_sha256": typed_sha,
        "source_echo_run_dir": Path(echo_run_dir).as_posix(),
        "cards": cards,
    }
    out_path = Path(out_dir) / ECHO_REVIEW_SHEET_NAME
    _atomic_write_json(out_path, sheet)
    return out_path


def _project_visible_texts(contract: Any) -> tuple[EchoReleaseVisibleText, ...]:
    diagram_elements = {ref.element_id for ref in contract.layout.diagram_nodes}
    return tuple(
        EchoReleaseVisibleText(
            element_id=element.element_id,
            visible_text=element.text,
            fact_id=element.provenance.fact_id,
            source_order=element.source_order,
            semantic_node_id=element.element_id if element.element_id in diagram_elements else None,
        )
        for element in contract.display_elements
    )


def _project_table_views(contract: Any) -> tuple[EchoReleaseTableView, ...]:
    layout = contract.layout
    if contract.presentation != "table" or layout.table_rows is None or layout.table_columns is None:
        return ()
    text_by_element = {element.element_id: element.text for element in contract.display_elements}
    cells = tuple(
        EchoReleaseTableCell(
            row=ref.row - 1,  # typed 层 1 基 → prompt TABLE_GRID 0 基口径（设计 §3.2 映射表）
            column=ref.column - 1,
            row_span=ref.row_span,
            column_span=ref.column_span,
            text=text_by_element[ref.element_id],
        )
        for ref in layout.table_cells
    )
    return (EchoReleaseTableView(row_count=layout.table_rows, column_count=layout.table_columns, cells=cells),)


def _project_diagram(contract: Any) -> tuple[tuple[str, ...], tuple[EchoReleaseSemanticEdge, ...]]:
    if contract.presentation != "diagram":
        return (), ()
    node_ids = tuple(ref.element_id for ref in contract.layout.diagram_nodes)
    edges = tuple(
        EchoReleaseSemanticEdge(source_node_id=ref.source_node_id, target_node_id=ref.target_node_id)
        for ref in contract.layout.diagram_edges
    )
    return node_ids, edges


def build_echo_release_fixture(
    *,
    typed_fixture_path: Path,
    echo_run_dir: Path,
    review_sheet_path: Path,
    approved_rows: Sequence[Mapping[str, Any]],
    source_echo_run_id: str,
) -> EchoBatchReviewedReleaseFixture:
    """approved 评审行 + typed fixture + echo 逐卡实录 → release fixture（四哈希链内派生）。

    ``approved_rows``：摄取门禁（gates/review/echo_step3_review）产出的 released 行，
    每行含 contract_id / reviewer / review_signoff_sha256（取证块哈希，摄取侧算）。
    本构建器**不收任何外部内容哈希**：typed fixture / contract / echo record 三层哈希
    全部现场读盘派生（不变式 4，对齐 fe76237 转换器纪律）。
    """

    typed_sha, compilation = _compile_contracts(typed_fixture_path)
    # 内容级门禁必须全绿；复合 valid 刻意不要求——其还与入 r9 试点常量
    # batch_generation_authorized（「批产不授权」）：echo 道的批产授权正是本链
    # 由人工评审签发的 handoff（§3.3 授权语义翻转，P8 的核心矫正对象）。
    gate = compilation.gate
    content_checks = {
        "source_fixture_authorized": gate.source_fixture_authorized,
        "source_contracts_valid": gate.source_contracts_valid,
        "provenance_valid": gate.provenance_valid,
        "source_text_exactly_once": gate.source_text_exactly_once,
        "model_input_clean": gate.model_input_clean,
        "deterministic_draft_valid": gate.deterministic_draft_valid,
    }
    failed_checks = [name for name, passed in content_checks.items() if not passed]
    if failed_checks:
        raise ValueError(f"typed fixture content gate failed; refuse to build an echo release: {failed_checks}")
    contracts_by_id = {contract.contract_id: contract for contract in compilation.contracts}
    review_sheet_sha = hashlib.sha256(Path(review_sheet_path).read_bytes()).hexdigest()

    cards: list[EchoBatchReleaseCard] = []
    for index, row in enumerate(sorted(approved_rows, key=lambda item: int(item["card_seq"])), start=1):
        contract_id = str(row["contract_id"])
        contract = contracts_by_id.get(contract_id)
        if contract is None:
            raise ValueError(f"approved row references an unknown contract: {contract_id}")
        record_path = Path(echo_run_dir) / "step3" / "cards" / f"{int(row['card_seq']):02d}_{contract_id.rsplit(':', 1)[-1]}"
        raw_files = sorted(record_path.glob("attempt-*.response.raw.json"))
        if not raw_files:
            raise ValueError(f"echo run has no response record for contract {contract_id}: {record_path}")
        node_ids, edges = _project_diagram(contract)
        payload = {
            "card_index": index,
            "source_card_id": contract.contract_id,
            "source_candidate_id": contract.source_candidate_id,
            "sample_id": contract.display_elements[0].provenance.subject_id,
            "source_presentation": contract.presentation,
            "render_presentation": contract.presentation,
            "fact_ids": tuple(contract.fact_ids),
            "visible_texts": _project_visible_texts(contract),
            "diagram_node_ids": node_ids,
            "semantic_layout_edges": edges,
            "table_contracts": _project_table_views(contract),
            "source_typed_fixture_sha256": typed_sha,
            "source_contract_sha256": contract.contract_sha256,
            "echo_record_sha256": hashlib.sha256(raw_files[-1].read_bytes()).hexdigest(),
            "review_signoff_sha256": str(row["review_signoff_sha256"]),
            "reviewer": str(row["reviewer"]),
        }
        cards.append(hash_model(EchoBatchReleaseCard, payload, "card_sha256"))

    if not cards:
        raise ValueError("no approved cards; refuse to build an empty echo release")
    batch_seed = canonical_hash(
        {
            "typed": typed_sha,
            "run": source_echo_run_id,
            "sheet": review_sheet_sha,
            "cards": [card.card_sha256 for card in cards],
        }
    )
    fixture_payload = {
        "batch_id": f"echo-batch:{batch_seed[:24]}",
        "source_typed_fixture_path": Path(typed_fixture_path).as_posix(),
        "source_typed_fixture_sha256": typed_sha,
        "source_echo_run_id": source_echo_run_id,
        "review_sheet_sha256": review_sheet_sha,
        "card_count": len(cards),
        "cards": tuple(cards),
    }
    return hash_model(EchoBatchReviewedReleaseFixture, fixture_payload, "fixture_sha256")
