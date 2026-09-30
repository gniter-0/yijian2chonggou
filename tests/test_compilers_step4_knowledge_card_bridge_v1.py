from __future__ import annotations

import pytest
from typing import Any, cast

from examcard.common.hashing import canonical_hash
from examcard.compilers.step4_knowledge_card_bridge_v1 import (
    KnowledgeCardImageRequestError,
    build_knowledge_card_image_request_v1,
    project_knowledge_card_image_request_v1,
)
from examcard.knowledge.schema import (
    ContentSegment,
    EvidenceRef,
    GroundingStatus,
    KnowledgeCard,
    Renderability,
)
from examcard.providers.envelope_profiles import build_image_profile


PROFILE = build_image_profile(
    model="gpt-image-2", image_width=1024, image_height=1536
)


def _card(
    *,
    renderer: str = "formula",
    grounding_status: GroundingStatus = GroundingStatus.VERIFIED,
    renderability: Renderability = Renderability.PUBLISHABLE,
    kp_type: str = "content",
    text: str = "F = ma，质量 2 kg，a = 3 m/s²。",
    diagram_elements: tuple[str, ...] = (),
    annotations: tuple[str, ...] = (),
) -> KnowledgeCard:
    fact_id = "fact:" + "1" * 24
    evidence_id = "evidence-1"
    segment = ContentSegment(
        text=text,
        fact_ids=(fact_id,),
        evidence_ids=(evidence_id,),
        renderer=renderer,
    )
    return KnowledgeCard(
        id="card:mechanics",
        raw_id="kp:" + "2" * 24,
        title="牛顿第二定律",
        content=text,
        content_segments=(segment,),
        evidence_refs=(EvidenceRef(evidence_id=evidence_id, page_num=3),),
        grounding_status=grounding_status,
        renderability=renderability,
        diagram_elements=diagram_elements,
        annotations=annotations,
        source_pages=(3,),
        kp_type=kp_type,
    )


def _with_segment(card: KnowledgeCard, *, renderer: str) -> KnowledgeCard:
    data = card.model_dump(mode="python")
    data["content_segments"] = (
        {
            **data["content_segments"][0],
            "renderer": renderer,
        },
    )
    return KnowledgeCard.model_validate(data)


def test_builds_deterministic_request_and_preserves_formula_numbers_and_units() -> None:
    card = _card()

    first = build_knowledge_card_image_request_v1(card, profile=PROFILE)
    second = build_knowledge_card_image_request_v1(card, profile=PROFILE)

    assert first == second
    assert first.presentation == "formula"
    assert "F = ma" in first.prompt_text
    assert "2 kg" in first.prompt_text
    assert "3 m/s²" in first.prompt_text
    assert first.source_elements[0].evidence_ids == ("evidence-1",)
    assert first.source_pages == (3,)
    assert first.request_id.endswith(first.request_sha256[:24])


def test_projection_reuses_provider_allow_list_and_keeps_provenance_internal() -> None:
    request = build_knowledge_card_image_request_v1(_card(), profile=PROFILE)

    projection, sidecar = project_knowledge_card_image_request_v1(
        request, profile=PROFILE
    )

    assert set(projection.http_request.model_dump(mode="json")) == {
        "model",
        "n",
        "prompt",
        "response_format",
        "size",
    }
    assert projection.http_request.prompt == request.prompt_text
    assert sidecar["source_card_id"] == request.card_id
    assert sidecar["evidence_refs"] == request.evidence_refs
    assert "evidence_refs" not in projection.http_request.model_dump(mode="json")


@pytest.mark.parametrize(
    ("renderer", "presentation"),
    [
        ("text", "text"),
        ("formula", "formula"),
        ("threshold", "constraint"),
        ("ordered_list", "ordered_list"),
        ("composition", "table"),
        ("reviewed_diagram", "diagram"),
    ],
)
def test_renderer_allow_list_maps_to_step4_presentation(
    renderer: str, presentation: str
) -> None:
    request = build_knowledge_card_image_request_v1(
        _with_segment(_card(renderer=renderer), renderer=renderer), profile=PROFILE
    )
    assert request.presentation == presentation


def test_unknown_renderer_fails_closed() -> None:
    with pytest.raises(KnowledgeCardImageRequestError, match="unsupported"):
        build_knowledge_card_image_request_v1(
            _with_segment(_card(), renderer="new_renderer"), profile=PROFILE
        )


@pytest.mark.parametrize(
    "card",
    [
        _card(grounding_status=GroundingStatus.UNRESOLVED),
        _card(grounding_status=GroundingStatus.DEGRADED),
        _card(renderability=Renderability.MANUAL_REVIEW),
        _card(kp_type="visual_reference"),
        _card(kp_type="exercise"),
    ],
)
def test_non_publishable_or_unresolved_cards_fail_closed(card: KnowledgeCard) -> None:
    with pytest.raises(KnowledgeCardImageRequestError):
        build_knowledge_card_image_request_v1(card, profile=PROFILE)


def test_only_knowledgecard_is_accepted_and_no_delivery_callable_is_needed() -> None:
    with pytest.raises(KnowledgeCardImageRequestError, match="KnowledgeCard"):
        build_knowledge_card_image_request_v1(
            cast(Any, {"title": "legacy prompt dictionary"}), profile=PROFILE
        )

    request = build_knowledge_card_image_request_v1(_card(), profile=PROFILE)
    assert not hasattr(request, "upload")
    assert not hasattr(request, "lookup")
    assert not hasattr(request, "retry")


def test_request_hash_changes_with_source_content_but_not_from_nondelivery_calls() -> None:
    first = build_knowledge_card_image_request_v1(_card(), profile=PROFILE)
    changed = build_knowledge_card_image_request_v1(
        _card(text="F = ma，质量 4 kg，a = 3 m/s²。"), profile=PROFILE
    )

    assert first.content_sha256 != changed.content_sha256
    assert first.request_sha256 != changed.request_sha256
    assert canonical_hash(first.evidence_refs) == canonical_hash(changed.evidence_refs)
