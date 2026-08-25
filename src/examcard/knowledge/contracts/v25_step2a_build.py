"""step2a 请求构建（拆分层 4）。

从章节输入与覆盖义务装出六个侧写各自的请求：先算投影摘要、再定 identity，最后整体自证。
离线中继基址来自 ``v25_support``，**本模块不认识任何客户端**，因此可离线跑全部分支。
"""
from __future__ import annotations

from . import (
    Step2ACoverageObligationKind,
    Step2ASectionInput,
    Step2DecoderConfig,
    Step2Stage,
    canonical_step2_sha256,
    derive_step2a_coverage_obligation_input,
    make_step2_request_identity,
)

from .v25_versions import (
    V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_STEP2A_MODEL_INPUT_VERSION,
    V25_STEP2A_PROMPT_VERSION,
    V25_STEP2A_REQUEST_VERSION,
)
from .v25_support import _OFFLINE_RELAY_BASE_URL
from .v25_enums import V25Step2AContractProfile
from .v25_step2a_obligations import (
    V25AtomicSourceObligation,
    V25ListSourceObligation,
    V25ReviewedContinuationBinding,
    V25TextSourceObligation,
)
from .v25_step2a_prompt import _v25_contexts, v25_step2a_prompt_template
from .v25_step2a_request import V25Step2ARequest, V25Step2AResultPayload


def build_v25_step2a_request(
    *,
    sample_id: str,
    material_profile_id: str,
    source_document_sha256: str,
    source_manifest_sha256s: tuple[str, ...],
    projection_sha256s: tuple[str, ...],
    content_policy_id: str,
    content_policy_revision: int,
    content_policy_fingerprint_sha256: str,
    sections: tuple[Step2ASectionInput, ...],
    model: str = "deterministic-fake-llm",
    relay_base_url: str = _OFFLINE_RELAY_BASE_URL,
    manual_reissue_reason: str = "phase2r11_offline_contract",
    contract_profile: V25Step2AContractProfile = V25Step2AContractProfile.GENERIC_SOURCE_DERIVED,
    atomic_source_obligations: tuple[V25AtomicSourceObligation, ...] = (),
    text_source_obligations: tuple[V25TextSourceObligation, ...] = (),
    list_source_obligations: tuple[V25ListSourceObligation, ...] = (),
    source_contract_projection_sha256: str | None = None,
    reviewed_continuation_bindings: tuple[V25ReviewedContinuationBinding, ...] = (),
    reviewed_table_repair_ids: tuple[str, ...] = (),
    table_repair_sidecar_sha256s: tuple[str, ...] = (),
) -> V25Step2ARequest:
    """Build a new V25 identity from source projections only."""

    if contract_profile == V25Step2AContractProfile.GENERIC_SOURCE_DERIVED:
        if atomic_source_obligations or text_source_obligations or list_source_obligations or source_contract_projection_sha256 is not None:
            raise ValueError("generic V25 builder cannot accept atomic-source contract state")
        request_version = V25_STEP2A_REQUEST_VERSION
        model_input_version = V25_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_STEP2A_PROMPT_VERSION
    elif contract_profile == V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT:
        if not atomic_source_obligations or text_source_obligations or list_source_obligations or source_contract_projection_sha256 is None:
            raise ValueError("atomic-source V25 builder requires only atomic obligations and projection provenance")
        request_version = V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION
        model_input_version = V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION
    elif contract_profile == V25Step2AContractProfile.DIAGRAM_CANONICAL_PRECHECK:
        if atomic_source_obligations or not text_source_obligations or list_source_obligations or source_contract_projection_sha256 is None:
            raise ValueError("diagram canonical V25 builder requires source-text obligations and projection provenance")
        request_version = V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION
        model_input_version = V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION
    elif contract_profile == V25Step2AContractProfile.LIST_CANONICAL_PRECHECK:
        if atomic_source_obligations or text_source_obligations or not list_source_obligations or source_contract_projection_sha256 is None:
            raise ValueError("list canonical V25 builder requires source-list obligations and projection provenance")
        request_version = V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION
        model_input_version = V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION
    elif contract_profile == V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK:
        if atomic_source_obligations or not text_source_obligations or list_source_obligations or source_contract_projection_sha256 is None:
            raise ValueError("standard canonical V25 builder requires source-text obligations and projection provenance")
        request_version = V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION
        model_input_version = V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION
    else:
        if atomic_source_obligations or not text_source_obligations or not list_source_obligations or source_contract_projection_sha256 is None:
            raise ValueError("cost-flow canonical V25 builder requires source-text and source-list obligations")
        request_version = V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION
        model_input_version = V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION
        prompt_version = V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION

    tables = tuple(table for section in sections for table in section.tables)
    if tables:
        coverage = derive_step2a_coverage_obligation_input(
            source_document_sha256=source_document_sha256,
            content_policy_id=content_policy_id,
            content_policy_revision=content_policy_revision,
            content_policy_fingerprint_sha256=content_policy_fingerprint_sha256,
            sections=sections,
            continuation_bindings=(),
            table_continuation_inputs=(),
        )
        obligations = coverage.obligations
    else:
        obligations = ()
    if contract_profile == V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT:
        replaced_anchor_sets = tuple(
            frozenset(item.required_primary_anchor_span_ids)
            for item in atomic_source_obligations
        )
        obligations = tuple(
            item
            for item in obligations
            if not (
                item.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE
                and any(
                    frozenset(item.required_primary_anchor_span_ids) <= source_anchor_ids
                    for source_anchor_ids in replaced_anchor_sets
                )
            )
        )
    contexts = _v25_contexts(sections=sections, obligations=obligations)
    projection_payload = {
        "sample_id": sample_id,
        "source_manifest_sha256s": source_manifest_sha256s,
        "projection_sha256s": projection_sha256s,
    }
    projection_sha256 = canonical_step2_sha256(projection_payload)
    projection_id = f"step2a-projection:{projection_sha256[:24]}"
    draft = {
        "sample_id": sample_id,
        "material_profile_id": material_profile_id,
        "source_document_sha256": source_document_sha256,
        "source_manifest_sha256s": source_manifest_sha256s,
        "projection_sha256s": projection_sha256s,
        "content_policy_id": content_policy_id,
        "content_policy_revision": content_policy_revision,
        "content_policy_fingerprint_sha256": content_policy_fingerprint_sha256,
        "contract_profile": contract_profile,
        "model_input_version": model_input_version,
        "prompt_version": prompt_version,
        "sections": sections,
        "coverage_obligations": obligations,
        "coverage_contexts": contexts,
        "atomic_source_obligations": atomic_source_obligations,
        "text_source_obligations": text_source_obligations,
        "list_source_obligations": list_source_obligations,
        "source_contract_projection_sha256": source_contract_projection_sha256,
        "reviewed_continuation_bindings": reviewed_continuation_bindings,
        "reviewed_table_repair_ids": reviewed_table_repair_ids,
        "table_repair_sidecar_sha256s": table_repair_sidecar_sha256s,
    }
    payload = {
        "stage": "step2a",
        "model_input_version": draft["model_input_version"],
        "prompt_version": draft["prompt_version"],
        "sample_id": sample_id,
        "source_document_sha256": source_document_sha256,
        "source_manifest_sha256s": source_manifest_sha256s,
        "projection_sha256s": projection_sha256s,
        "content_policy": {
            "policy_id": content_policy_id,
            "revision": content_policy_revision,
            "fingerprint_sha256": content_policy_fingerprint_sha256,
        },
        "sections": [section.model_dump(mode="json") for section in sections],
        "coverage_contexts": [context.model_dump(mode="json") for context in contexts],
        "reviewed_continuation_bindings": [item.model_dump(mode="json") for item in reviewed_continuation_bindings],
        "reviewed_table_repair_ids": reviewed_table_repair_ids,
        "table_repair_sidecar_sha256s": table_repair_sidecar_sha256s,
    }
    if contract_profile == V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT:
        payload.update(
            {
                "contract_profile": contract_profile.value,
                "atomic_source_obligations": [item.model_dump(mode="json") for item in atomic_source_obligations],
                "source_contract_projection_sha256": source_contract_projection_sha256,
            }
        )
    elif contract_profile == V25Step2AContractProfile.DIAGRAM_CANONICAL_PRECHECK:
        payload.update(
            {
                "contract_profile": contract_profile.value,
                "text_source_obligations": [item.model_dump(mode="json") for item in text_source_obligations],
                "source_contract_projection_sha256": source_contract_projection_sha256,
            }
        )
    elif contract_profile == V25Step2AContractProfile.LIST_CANONICAL_PRECHECK:
        payload.update(
            {
                "contract_profile": contract_profile.value,
                "list_source_obligations": [item.model_dump(mode="json") for item in list_source_obligations],
                "source_contract_projection_sha256": source_contract_projection_sha256,
            }
        )
    elif contract_profile == V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK:
        payload.update(
            {
                "contract_profile": contract_profile.value,
                "text_source_obligations": [item.model_dump(mode="json") for item in text_source_obligations],
                "source_contract_projection_sha256": source_contract_projection_sha256,
            }
        )
    elif contract_profile == V25Step2AContractProfile.COST_FLOW_CANONICAL_PRECHECK:
        payload.update(
            {
                "contract_profile": contract_profile.value,
                "text_source_obligations": [item.model_dump(mode="json") for item in text_source_obligations],
                "list_source_obligations": [item.model_dump(mode="json") for item in list_source_obligations],
                "source_contract_projection_sha256": source_contract_projection_sha256,
            }
        )
    identity = make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model=model,
        payload_sha256=canonical_step2_sha256(payload),
        relay_base_url=relay_base_url,
        prompt_template=v25_step2a_prompt_template(contract_profile=contract_profile),
        response_schema=V25Step2AResultPayload.model_json_schema(),
        decoder=Step2DecoderConfig(temperature=0, max_tokens=2200),
        projection_id=projection_id,
        projection_sha256=projection_sha256,
        source_manifest_sha256=source_manifest_sha256s[0],
        content_policy_fingerprint_sha256=content_policy_fingerprint_sha256,
        manual_reissue_reason=manual_reissue_reason,
    )
    return V25Step2ARequest(identity=identity, **draft)
