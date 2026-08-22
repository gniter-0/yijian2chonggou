"""Deterministic Step 3 release closure (distilled from phase2r98).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r98_generic_step3_deterministic_release_closure.py`
（distill:release-gate，设计依据 docs/design/p1_compilers_release_gate_design.md §5.2）。

- `DeterministicDraftCard / DeterministicReleaseBundle` 已按 A 案下沉
  `knowledge/contracts/admission_fixtures.py`（被 gates/review 的 r10 队列跨桶
  消费），本模块向下 import；R9 编译器是 Step 3 草稿的唯一权威，历史 provider
  输出只作不透明失败证据（hash-only）。
- 法定哈希依铁律 bc-b8b9aca6：`canonical_hash` 绑定 admission_legacy_hash 的
  r9/r10x 变体（r98 旧仓即 import r9 同一实现）；r98 本地 `_jsonable/
  canonical_json` 与 `_hash_model` 逐字节原样。
- 新入口 `build_deterministic_release`：消费 §4.1 `Step3Compilation` 纯计算组装
  发布束与门禁；r97-freeze / opaque-failure / r96-alignment 三项盘面检查由调用方
  以布尔注入（detail 缺省保留旧文案，注入 True 时门禁哈希与旧仓平价）。
- 旧仓留守（不在本模块）：`R97_CODE_FILES`/`build_r97_freeze`/`verify_r97_freeze`、
  `build_opaque_failure_evidence`（读 R97 运行）、`build_r96_alignment`（锁三代表
  卡与 R96 model-input 路径）、`RUN_DIR`/`REPORT_PATH`/`R96_INPUT`/`R97_*` 与
  `materialize_release`。本模块保留 R97Freeze/OpaqueFailureEvidence/
  R96SourceContractAlignment 等**数据模型**（MachineReport 内嵌，装载校验历史
  产物所需），其构建函数留旧仓。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final, Literal, Mapping

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.compilers.step3_card import DraftBundle, Step3Compilation
from examcard.knowledge.contracts.admission_fixtures import (
    DeterministicDraftCard,
    DeterministicReleaseBundle,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash_r10x as canonical_hash,  # r9/r10x 变体（r98 旧仓即 import r9 实现）
)

__all__ = [
    "AcceptanceReport",
    "AuthorityPolicy",
    "CandidateAudit",
    "DeterministicRelease",
    "ExternalCheck",
    "GateCheck",
    "JUnitReceipt",
    "MachineReport",
    "NonAuthoritativeModelCandidate",
    "OfflineGate",
    "OpaqueFailureEvidence",
    "PresentationCounts",
    "R96SourceContractAlignment",
    "R97Freeze",
    "RegressionResult",
    "build_deterministic_release",
    "build_regression_matrix",
    "submit_non_authoritative_candidate",
]

PHASE: Final = "2R.9.8"

FREEZE_VERSION: Final = "phase2r98_r97_freeze_v1"
OPAQUE_FAILURE_VERSION: Final = "phase2r98_opaque_r97_failure_evidence_v1"
AUTHORITY_VERSION: Final = "phase2r98_step3_authority_policy_v1"
ALIGNMENT_VERSION: Final = "phase2r98_r96_source_contract_alignment_v1"
CANDIDATE_VERSION: Final = "phase2r98_non_authoritative_model_candidate_v1"
CANDIDATE_AUDIT_VERSION: Final = "phase2r98_non_authoritative_candidate_audit_v1"
GATE_CHECK_VERSION: Final = "phase2r98_gate_check_v1"
GATE_VERSION: Final = "phase2r98_offline_gate_v1"
REGRESSION_VERSION: Final = "phase2r98_regression_result_v1"
REPORT_VERSION: Final = "phase2r98_machine_report_v1"
ACCEPTANCE_VERSION: Final = "phase2r98_acceptance_report_v1"

HASH = r"^[0-9a-f]{64}$"
CONTRACT_ID = r"^step3-card-contract:[0-9a-f]{24}$"
PRESENTATIONS: Final[tuple[Literal["table", "ordered_list", "formula"], ...]] = ("table", "ordered_list", "formula")

# gonglu 试点 v1 钉扎：R8.10 六个 reviewed 候选（门禁 r8-reviewed-fixture 检查的期望值）
DEFAULT_EXPECTED_CARD_COUNT = 6


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrictModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    # r98 模块自带局部实现，逐字节原样（铁律 bc-b8b9aca6）
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_model[ModelT: StrictModel](model_type: type[ModelT], payload: dict[str, Any], field: str) -> ModelT:
    document = dict(payload)
    draft = model_type.model_construct(**{**document, field: "0" * 64})
    document[field] = canonical_hash(draft.model_dump(mode="json", exclude={"schema_version", field}))
    return model_type.model_validate(document)


class FrozenArtifact(StrictModel):
    schema_version: Literal["phase2r98_r97_freeze_v1"] = FREEZE_VERSION
    path: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=HASH)


class R97Freeze(StrictModel):
    schema_version: Literal["phase2r98_r97_freeze_v1"] = FREEZE_VERSION
    phase: Literal["2R.9.8"] = PHASE
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=HASH)
    inputs_unchanged: Literal[True] = True

    @model_validator(mode="after")
    def validate_freeze(self) -> "R97Freeze":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("R9.8 R9.7 artifact count mismatch")
        if len({item.path for item in self.artifacts}) != self.artifact_count:
            raise ValueError("R9.8 R9.7 frozen paths must be unique")
        if self.aggregate_sha256 != canonical_hash(self.artifacts):
            raise ValueError("R9.8 R9.7 freeze aggregate mismatch")
        return self


class OpaqueFailureEvidence(StrictModel):
    """Hashes and Gate facts only.  The R9.7 provider body is never loaded."""

    schema_version: Literal["phase2r98_opaque_r97_failure_evidence_v1"] = OPAQUE_FAILURE_VERSION
    source_phase: Literal["2R.9.7"] = "2R.9.7"
    machine_report_sha256: str = Field(pattern=HASH)
    business_record_sha256: str = Field(pattern=HASH)
    request_id: str = Field(min_length=1)
    provider_request_id: str = Field(min_length=1)
    raw_response_sha256: str = Field(pattern=HASH)
    response_bytes: int = Field(gt=0)
    http_status: Literal[200] = 200
    stop_reason: Literal["business_failed"] = "business_failed"
    strict_gate_valid: Literal[False] = False
    response_body_persisted: Literal[False] = False
    body_read_or_reconstructed: Literal[False] = False
    permitted_use: Literal["opaque_failure_evidence_only"] = "opaque_failure_evidence_only"


class AuthorityPolicy(StrictModel):
    schema_version: Literal["phase2r98_step3_authority_policy_v1"] = AUTHORITY_VERSION
    phase: Literal["2R.9.8"] = PHASE
    draft_authority: Literal["local_deterministic_compiler"] = "local_deterministic_compiler"
    model_role: Literal["non_authoritative_candidate_only"] = "non_authoritative_candidate_only"
    model_can_mutate_draft: Literal[False] = False
    model_can_mutate_review_state: Literal[False] = False
    model_can_mutate_provenance: Literal[False] = False
    model_can_authorize_batch: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False


class R96SourceContractAlignment(StrictModel):
    schema_version: Literal["phase2r98_r96_source_contract_alignment_v1"] = ALIGNMENT_VERSION
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=HASH)
    contract_ids: tuple[str, str, str]
    presentations: tuple[Literal["table", "ordered_list", "formula"], ...] = PRESENTATIONS
    exact: Literal[True] = True

    @model_validator(mode="after")
    def validate_alignment(self) -> "R96SourceContractAlignment":
        if len(set(self.contract_ids)) != 3:
            raise ValueError("R9.8 R9.6 representative contracts must be unique")
        if any(not re.fullmatch(CONTRACT_ID, item) for item in self.contract_ids):
            raise ValueError("R9.8 R9.6 representative contract ID is invalid")
        return self


class NonAuthoritativeModelCandidate(StrictModel):
    schema_version: Literal["phase2r98_non_authoritative_model_candidate_v1"] = CANDIDATE_VERSION
    candidate_sha256: str = Field(pattern=HASH)
    claimed_contract_ids: tuple[str, ...] = ()
    proposed_payload_sha256: str = Field(pattern=HASH)
    authority_requested: Literal[False] = False


class CandidateAudit(StrictModel):
    schema_version: Literal["phase2r98_non_authoritative_candidate_audit_v1"] = CANDIDATE_AUDIT_VERSION
    candidate_sha256: str = Field(pattern=HASH)
    disposition: Literal["ignored_non_authoritative"] = "ignored_non_authoritative"
    draft_mutated: Literal[False] = False
    review_state_mutated: Literal[False] = False
    provenance_mutated: Literal[False] = False
    batch_authorized: Literal[False] = False


class GateCheck(StrictModel):
    schema_version: Literal["phase2r98_gate_check_v1"] = GATE_CHECK_VERSION
    check_id: str = Field(min_length=3, pattern=r"^[a-z0-9][a-z0-9._:-]+$")
    passed: bool
    detail: str = Field(min_length=1)


class PresentationCounts(StrictModel):
    schema_version: Literal["phase2r98_machine_report_v1"] = REPORT_VERSION
    diagram: int = Field(ge=0)
    table: int = Field(ge=0)
    ordered_list: int = Field(ge=0)
    constraint: int = Field(ge=0)
    formula: int = Field(ge=0)


class OfflineGate(StrictModel):
    schema_version: Literal["phase2r98_offline_gate_v1"] = GATE_VERSION
    phase: Literal["2R.9.8"] = PHASE
    valid: bool
    r97_freeze_valid: bool
    opaque_failure_evidence_only: bool
    r8_fixture_valid: bool
    r96_alignment_valid: bool
    deterministic_draft_authoritative: bool
    source_text_exactly_once: bool
    fact_card_exactly_once: bool
    evidence_anchor_exact: bool
    typed_contract_exact: bool
    provenance_exact: bool
    geometry_exact: bool
    strict_schema: bool
    regressions_fail_closed: bool
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    network_calls: Literal[0] = 0
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        expected = all(check.passed for check in self.checks) and not self.failure_codes
        if self.valid != expected:
            raise ValueError("R9.8 offline Gate validity mismatch")
        if self.valid and not all((
            self.r97_freeze_valid, self.opaque_failure_evidence_only, self.r8_fixture_valid,
            self.r96_alignment_valid, self.deterministic_draft_authoritative,
            self.source_text_exactly_once, self.fact_card_exactly_once,
            self.evidence_anchor_exact, self.typed_contract_exact, self.provenance_exact,
            self.geometry_exact, self.strict_schema, self.regressions_fail_closed,
        )):
            raise ValueError("R9.8 valid Gate has a failed aggregate")
        if self.gate_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"})):
            raise ValueError("R9.8 Gate hash mismatch")
        return self


class RegressionResult(StrictModel):
    schema_version: Literal["phase2r98_regression_result_v1"] = REGRESSION_VERSION
    case_id: str = Field(min_length=3, pattern=r"^[a-z0-9][a-z0-9._:-]+$")
    expected_failure: str = Field(min_length=1)
    caught: bool
    detail: str = Field(min_length=1)
    regression_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_result(self) -> "RegressionResult":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "regression_sha256"}))
        if self.regression_sha256 != expected:
            raise ValueError("R9.8 regression hash mismatch")
        return self


class MachineReport(StrictModel):
    schema_version: Literal["phase2r98_machine_report_v1"] = REPORT_VERSION
    phase: Literal["2R.9.8"] = PHASE
    freeze: R97Freeze
    opaque_failure: OpaqueFailureEvidence
    authority: AuthorityPolicy
    alignment: R96SourceContractAlignment
    release_sha256: str = Field(pattern=HASH)
    card_count: int = Field(ge=1)
    presentation_counts: PresentationCounts
    offline_gate: OfflineGate
    regression_count: int = Field(ge=1)
    regression_caught_count: int = Field(ge=0)
    step3_response_offline_ready: bool
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    report_sha256: str = Field(pattern=HASH)


class JUnitReceipt(StrictModel):
    schema_version: Literal["phase2r98_acceptance_report_v1"] = ACCEPTANCE_VERSION
    label: Literal["targeted", "full"]
    sha256: str = Field(pattern=HASH)
    tests: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AcceptanceReport(StrictModel):
    schema_version: Literal["phase2r98_acceptance_report_v1"] = ACCEPTANCE_VERSION
    phase: Literal["2R.9.8"] = PHASE
    accepted: bool
    machine_report_sha256: str = Field(pattern=HASH)
    receipts: tuple[JUnitReceipt, JUnitReceipt]
    no_network_or_model_calls: Literal[True] = True
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    acceptance_sha256: str = Field(pattern=HASH)


@dataclass(frozen=True)
class ExternalCheck:
    """盘面检查注入件（r97 冻结 / 不透明失败证据 / r96 对齐；detail 缺省=旧文案）。"""

    passed: bool
    detail: str


@dataclass(frozen=True)
class DeterministicRelease:
    """新入口产物：发布束 + 权威策略 + 门禁 + 回归 + 呈现计数（报告组装归调用方）。"""

    bundle: DeterministicReleaseBundle
    authority: AuthorityPolicy
    gate: OfflineGate
    regressions: tuple[RegressionResult, ...]
    presentation_counts: PresentationCounts


def _source_checks(bundle: DeterministicReleaseBundle, r9_draft: DraftBundle) -> dict[str, bool]:
    cards = bundle.cards
    r9_by_id = {item.contract_id: item for item in r9_draft.artifacts}
    source_text_exact = all(
        card.artifact.rendered_element_ids == tuple(item.element_id for item in card.contract.display_elements)
        and card.artifact.rendered_source_texts == tuple(item.text for item in card.contract.display_elements)
        and r9_by_id.get(card.contract.contract_id) == card.artifact
        for card in cards
    )
    provenance_exact = all(
        element.provenance.page >= 1
        and bool(element.provenance.anchor_ids)
        and bool(element.provenance.image_sha256)
        and bool(element.provenance.source_manifest_sha256)
        for card in cards for element in card.contract.display_elements
    )
    return {
        "source_text_exactly_once": source_text_exact,
        "fact_card_exactly_once": (
            len(cards) == len({card.contract.contract_id for card in cards})
            and all(
                len(card.contract.fact_ids) == len(set(card.contract.fact_ids))
                and {element.provenance.fact_id for element in card.contract.display_elements} == set(card.contract.fact_ids)
                for card in cards
            )
        ),
        "evidence_anchor_exact": all(bool(element.provenance.evidence_id) and bool(element.provenance.anchor_ids) for card in cards for element in card.contract.display_elements),
        "typed_contract_exact": all(bool(card.contract.source_typed_contract_sha256) for card in cards),
        "provenance_exact": provenance_exact,
        "geometry_exact": all(card.contract.geometry.inferred_geometry is False and card.contract.geometry.exact_source_text_instances is True for card in cards),
    }


def submit_non_authoritative_candidate(candidate: NonAuthoritativeModelCandidate, bundle: DeterministicReleaseBundle) -> CandidateAudit:
    """Record a candidate without providing a mutation path into the release."""
    if candidate.authority_requested:
        raise ValueError("a model candidate cannot request Step 3 authority")
    if set(candidate.claimed_contract_ids) - {card.contract.contract_id for card in bundle.cards}:
        raise ValueError("a model candidate cannot introduce cross-sample Cards")
    return CandidateAudit(candidate_sha256=candidate.candidate_sha256)


def _fail_closed(case_id: str, expected_failure: str, callback: Any) -> RegressionResult:
    caught = False
    detail = "mutation was unexpectedly accepted"
    try:
        callback()
    except (ValueError, TypeError, KeyError) as exc:
        caught = True
        detail = str(exc)[:500] or type(exc).__name__
    payload = {"case_id": case_id, "expected_failure": expected_failure, "caught": caught, "detail": detail}
    return RegressionResult(**payload, regression_sha256=canonical_hash(payload))


def build_regression_matrix(bundle: DeterministicReleaseBundle, authority: AuthorityPolicy) -> tuple[RegressionResult, ...]:
    baseline = bundle.model_dump(mode="json")

    def mutate(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
        copy: dict[str, Any] = json.loads(json.dumps(baseline, ensure_ascii=False))
        target: Any = copy
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return copy

    return (
        _fail_closed("unknown-field", "unknown_field", lambda: DeterministicReleaseBundle.model_validate({**baseline, "unknown_field": True})),
        _fail_closed("missing-card", "missing_card", lambda: DeterministicReleaseBundle.model_validate({**baseline, "cards": baseline["cards"][:-1]})),
        _fail_closed("duplicate-card", "duplicate_card", lambda: DeterministicReleaseBundle.model_validate({**baseline, "cards": baseline["cards"] + [baseline["cards"][0]], "card_count": len(baseline["cards"]) + 1})),
        _fail_closed("truncated-text", "source_text", lambda: DeterministicReleaseBundle.model_validate(mutate(("cards", 0, "contract", "display_elements", 0, "text"), "truncated"))),
        _fail_closed("fact-mismatch", "fact_binding", lambda: DeterministicReleaseBundle.model_validate(mutate(("cards", 0, "contract", "fact_ids", 0), "fact:wrong"))),
        _fail_closed("anchor-mismatch", "anchor_provenance", lambda: DeterministicReleaseBundle.model_validate(mutate(("cards", 0, "contract", "display_elements", 0, "provenance", "anchor_ids"), ["anchor:wrong"]))),
        _fail_closed("typed-contract-mismatch", "typed_contract", lambda: DeterministicReleaseBundle.model_validate(mutate(("cards", 0, "contract", "source_typed_contract_sha256"), "0" * 64))),
        _fail_closed("old-provenance", "old_provenance", lambda: DeterministicReleaseBundle.model_validate(mutate(("cards", 0, "contract", "display_elements", 0, "provenance", "image_sha256"), "0" * 64))),
        _fail_closed("cross-sample-grouping", "artifact_contract", lambda: DeterministicDraftCard.model_validate({**baseline["cards"][0], "artifact": baseline["cards"][1]["artifact"]})),
        _fail_closed("authority-escalation", "model_authority", lambda: AuthorityPolicy.model_validate({**authority.model_dump(mode="json"), "model_can_mutate_draft": True})),
        _fail_closed("candidate-cross-sample", "candidate_contract", lambda: submit_non_authoritative_candidate(NonAuthoritativeModelCandidate(candidate_sha256="3" * 64, proposed_payload_sha256="4" * 64, claimed_contract_ids=("step3-card-contract:000000000000000000000000",)), bundle)),
        _fail_closed("candidate-authorization", "candidate_authority", lambda: submit_non_authoritative_candidate(NonAuthoritativeModelCandidate(candidate_sha256="5" * 64, proposed_payload_sha256="6" * 64, authority_requested=True), bundle)),
        _fail_closed("strict-schema", "unknown_nested_field", lambda: DeterministicDraftCard.model_validate({**baseline["cards"][0], "unknown_field": True})),
    )


def build_deterministic_release(
    compilation: Step3Compilation,
    *,
    source_fixture_path_label: str,
    r97_freeze_check: ExternalCheck = ExternalCheck(True, "all R9.7 code, schemas, run records and reports are unchanged"),
    opaque_failure_check: ExternalCheck = ExternalCheck(True, "failed provider body is hash-only evidence"),
    r96_alignment_check: ExternalCheck = ExternalCheck(True, "table, ordered_list and formula representatives exactly match R9"),
    expected_card_count: int = DEFAULT_EXPECTED_CARD_COUNT,
) -> DeterministicRelease:
    """纯计算入口（设计 §5.2）：`Step3Compilation` → 发布束 + 门禁。

    r97 冻结 / 不透明失败证据 / r96 对齐三项盘面检查由调用方注入（旧仓
    `build_release` 粘合分别以 `verify_r97_freeze`、`build_opaque_failure_evidence`
    结果、`build_r96_alignment.exact` 提供）；`expected_card_count` 为
    r8-reviewed-fixture 检查的期望候选数（gonglu 试点 v1 = 6）。
    """
    if not compilation.gate.valid or not compilation.readiness.generic_step3_offline_ready:
        raise ValueError("R9 compiler is not a valid deterministic source")
    artifact_by_id = {item.contract_id: item for item in compilation.draft.artifacts}
    if len(artifact_by_id) != len(compilation.contracts):
        raise ValueError("R9 deterministic draft does not map one artifact per Card")
    cards = tuple(
        DeterministicDraftCard(contract=contract, artifact=artifact_by_id[contract.contract_id])
        for contract in compilation.contracts
    )
    release_payload = {
        "source_fixture_sha256": compilation.source_fixture_sha256,
        "source_fixture_path": source_fixture_path_label,
        "r9_draft_sha256": canonical_hash(compilation.draft),
        "cards": cards,
        "card_count": len(cards),
        "draft_authority": "local_deterministic_compiler",
        "reviewed_fixture_created": False,
        "step4_authorized": False,
        "batch_generation_authorized": False,
    }
    bundle = _hash_model(DeterministicReleaseBundle, release_payload, "release_sha256")
    authority = AuthorityPolicy()
    regressions = build_regression_matrix(bundle, authority)
    exact = _source_checks(bundle, compilation.draft)
    checks = tuple(
        GateCheck(check_id=key, passed=value, detail=detail)
        for key, value, detail in (
            ("r97-freeze", r97_freeze_check.passed, r97_freeze_check.detail),
            ("opaque-failure", opaque_failure_check.passed, opaque_failure_check.detail),
            ("r8-reviewed-fixture", compilation.source_fixture.fixture_authorized and len(cards) == expected_card_count, "only six R8.10 reviewed typed-Evidence candidates are compiled"),
            ("r96-alignment", r96_alignment_check.passed, r96_alignment_check.detail),
            ("deterministic-authority", authority.draft_authority == "local_deterministic_compiler", "model cannot author drafts, review state, provenance or batch"),
            ("source-text-exactly-once", exact["source_text_exactly_once"], "rendered element IDs and text exactly match each source contract"),
            ("fact-card-exactly-once", exact["fact_card_exactly_once"], "each Fact and Card is unique and source bound"),
            ("evidence-anchor-exact", exact["evidence_anchor_exact"], "Evidence and anchors remain contract-owned"),
            ("typed-contract-exact", exact["typed_contract_exact"], "typed presentation contracts are unchanged"),
            ("provenance-exact", exact["provenance_exact"], "page, image and manifest provenance are unchanged"),
            ("geometry-exact", exact["geometry_exact"], "source geometry remains explicit and non-inferred"),
            ("strict-schema", True, "all R9.8 documents use strict Pydantic schemas"),
            ("regressions-fail-closed", all(item.caught for item in regressions), "mutation, unknown-field and authority regressions are rejected"),
        )
    )
    gate_payload = {
        "valid": all(check.passed for check in checks),
        "r97_freeze_valid": checks[0].passed,
        "opaque_failure_evidence_only": checks[1].passed,
        "r8_fixture_valid": checks[2].passed,
        "r96_alignment_valid": checks[3].passed,
        "deterministic_draft_authoritative": checks[4].passed,
        "source_text_exactly_once": checks[5].passed,
        "fact_card_exactly_once": checks[6].passed,
        "evidence_anchor_exact": checks[7].passed,
        "typed_contract_exact": checks[8].passed,
        "provenance_exact": checks[9].passed,
        "geometry_exact": checks[10].passed,
        "strict_schema": checks[11].passed,
        "regressions_fail_closed": checks[12].passed,
        "reviewed_fixture_created": False,
        "step4_authorized": False,
        "batch_generation_authorized": False,
        "network_calls": 0,
        "checks": checks,
        "failure_codes": tuple(check.check_id for check in checks if not check.passed),
    }
    gate = _hash_model(OfflineGate, gate_payload, "gate_sha256")
    counts = PresentationCounts(**{presentation: sum(card.contract.presentation == presentation for card in cards) for presentation in ("diagram", "table", "ordered_list", "constraint", "formula")})
    return DeterministicRelease(bundle, authority, gate, regressions, counts)
