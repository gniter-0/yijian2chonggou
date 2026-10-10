"""Pure KnowledgeCard -> Step4 image request bridge.

This module is deliberately narrower than the live Step4 stage.  It turns one
validated :class:`KnowledgeCard` into a deterministic, provider-neutral image
request and can project that request through the existing public image-request
allow-list.  It does not call an image provider, write artifacts, retry, or
participate in delivery authority.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal, Mapping

from pydantic import Field, StrictInt, StrictStr, model_validator

from examcard.common.hashing import canonical_hash, sha256_text
from examcard.knowledge.schema import (
    ContentSegment,
    EvidenceRef,
    GroundingStatus,
    KnowledgeCard,
    Renderability,
)
from examcard.materials.schema import StrictModel
from examcard.providers.envelope_profiles import (
    ImageEnvelopeProfile,
    ImageRequestProjection,
    project_image_request,
)

__all__ = [
    "KNOWLEDGE_CARD_IMAGE_REQUEST_VERSION",
    "PRESENTATIONS",
    "RENDERER_ALLOWLIST",
    "KnowledgeCardImageRequestError",
    "KnowledgeCardImageSourceElementV1",
    "KnowledgeCardImageRequestV1",
    "build_knowledge_card_image_request_v1",
    "project_knowledge_card_image_request_v1",
]


KNOWLEDGE_CARD_IMAGE_REQUEST_VERSION: Final = "examcard_knowledge_card_image_request_v1"
PRESENTATIONS: Final = (
    "text",
    "diagram",
    "table",
    "ordered_list",
    "constraint",
    "formula",
)

# These are the renderer values currently emitted by the fact/card builder.
# Values that imply unresolved review or visual-reference-only content are
# intentionally absent and therefore fail closed.
RENDERER_ALLOWLIST: Final[Mapping[str, str]] = {
    "text": "text",
    "structural_anchor": "text",
    "formula": "formula",
    "ordered_list": "ordered_list",
    "enumeration": "ordered_list",
    "table": "table",
    "composition": "table",
    "diagram": "diagram",
    "reviewed_diagram": "diagram",
    "constraint": "constraint",
    "threshold": "constraint",
    "exception": "constraint",
}

_GROUNDING_ALLOWED = frozenset(
    {GroundingStatus.VERIFIED, GroundingStatus.MANUAL_VERIFIED}
)
_REJECTED_CARD_TYPES = frozenset({"exercise", "visual_reference"})
_RENDER_POLICY: tuple[str, ...] = (
    "render_only_supplied_source_text",
    "preserve_source_order_and_typed_presentation",
    "preserve_formula_numbers_and_units_exactly",
    "do_not_add_labels_claims_or_values",
)
_REQUEST_SIZE_PATTERN = r"^\d{3,4}x\d{3,4}$"
_REQUEST_ID_PATTERN = r"^knowledge-card-image-request:[0-9a-f]{24}$"


class KnowledgeCardImageRequestError(ValueError):
    """Raised when a card cannot be safely converted into an image request."""


class KnowledgeCardImageSourceElementV1(StrictModel):
    """One source-owned visible content segment in stable order."""

    schema_version: Literal["examcard_knowledge_card_image_source_element_v1"] = (
        "examcard_knowledge_card_image_source_element_v1"
    )
    element_index: StrictInt = Field(ge=0)
    text: StrictStr = Field(min_length=1)
    fact_ids: tuple[StrictStr, ...] = Field(min_length=1)
    evidence_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_renderer: StrictStr = Field(min_length=1)
    presentation: Literal[
        "text", "diagram", "table", "ordered_list", "constraint", "formula"
    ]
    text_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_text_hash(self) -> "KnowledgeCardImageSourceElementV1":
        if self.text_sha256 != sha256_text(self.text):
            raise ValueError("source element text hash mismatch")
        return self


class KnowledgeCardImageRequestV1(StrictModel):
    """Deterministic provider-neutral image request derived from one card."""

    schema_version: Literal["examcard_knowledge_card_image_request_v1"] = (
        KNOWLEDGE_CARD_IMAGE_REQUEST_VERSION
    )
    request_id: StrictStr = Field(pattern=_REQUEST_ID_PATTERN)
    card_id: StrictStr = Field(min_length=1)
    raw_id: StrictStr = Field(pattern=r"^kp:[0-9a-f]{24}$")
    title: StrictStr = Field(min_length=1)
    presentation: Literal[
        "text", "diagram", "table", "ordered_list", "constraint", "formula"
    ]
    source_elements: tuple[KnowledgeCardImageSourceElementV1, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    source_pages: tuple[StrictInt, ...] = Field(min_length=1)
    diagram_type: StrictStr = ""
    diagram_elements: tuple[StrictStr, ...] = ()
    annotations: tuple[StrictStr, ...] = ()
    expected_visible_texts: tuple[StrictStr, ...] = Field(min_length=1)
    render_policy: tuple[StrictStr, ...] = _RENDER_POLICY
    content_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    model: StrictStr = Field(min_length=1)
    n: Literal[1] = 1
    response_format: Literal["b64_json"] = "b64_json"
    size: StrictStr = Field(pattern=_REQUEST_SIZE_PATTERN)
    prompt_text: StrictStr = Field(min_length=1)
    prompt_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    provider_neutral: Literal[True] = True

    @model_validator(mode="after")
    def validate_request(self) -> "KnowledgeCardImageRequestV1":
        expected_indexes = tuple(range(len(self.source_elements)))
        actual_indexes = tuple(item.element_index for item in self.source_elements)
        if actual_indexes != expected_indexes:
            raise ValueError("source elements must be contiguous and ordered")

        ref_by_id = {item.evidence_id: item for item in self.evidence_refs}
        if len(ref_by_id) != len(self.evidence_refs):
            raise ValueError("evidence references must be unique")
        for element in self.source_elements:
            if not set(element.evidence_ids) <= set(ref_by_id):
                raise ValueError("source element evidence is not bound to the card")
            if element.presentation != self.presentation:
                raise ValueError("source element presentation mismatch")

        expected_pages = tuple(sorted({item.page_num for item in self.evidence_refs}))
        if self.source_pages != expected_pages:
            raise ValueError("source pages must be derived from evidence references")

        expected_texts = _unique_texts(
            (
                self.title,
                *(item.text for item in self.source_elements),
                *(self.diagram_elements if self.presentation == "diagram" else ()),
                *(self.annotations if self.presentation == "diagram" else ()),
            )
        )
        if self.expected_visible_texts != expected_texts:
            raise ValueError("expected visible text is not the source projection")
        if any(_prompt_json_text(text) not in self.prompt_text for text in self.expected_visible_texts):
            raise ValueError("expected visible text is missing from the prompt")

        expected_prompt = _render_prompt(
            title=self.title,
            presentation=self.presentation,
            source_elements=self.source_elements,
            diagram_type=self.diagram_type,
            diagram_elements=self.diagram_elements,
            annotations=self.annotations,
            expected_visible_texts=self.expected_visible_texts,
            render_policy=self.render_policy,
        )
        if self.prompt_text != expected_prompt:
            raise ValueError("prompt text is not the deterministic source projection")
        if self.prompt_sha256 != sha256_text(self.prompt_text):
            raise ValueError("prompt hash mismatch")

        expected_content = canonical_hash(
            {
                "card_id": self.card_id,
                "raw_id": self.raw_id,
                "title": self.title,
                "presentation": self.presentation,
                "source_elements": self.source_elements,
                "diagram_type": self.diagram_type,
                "diagram_elements": self.diagram_elements,
                "annotations": self.annotations,
            }
        )
        if self.content_sha256 != expected_content:
            raise ValueError("content hash mismatch")

        expected_provenance = canonical_hash(
            {
                "card_id": self.card_id,
                "raw_id": self.raw_id,
                "source_elements": tuple(
                    {
                        "element_index": item.element_index,
                        "fact_ids": item.fact_ids,
                        "evidence_ids": item.evidence_ids,
                    }
                    for item in self.source_elements
                ),
                "evidence_refs": self.evidence_refs,
                "source_pages": self.source_pages,
            }
        )
        if self.provenance_sha256 != expected_provenance:
            raise ValueError("provenance hash mismatch")

        expected_request = canonical_hash(
            self.model_dump(
                mode="json",
                exclude={"schema_version", "request_id", "request_sha256"},
            )
        )
        if self.request_sha256 != expected_request:
            raise ValueError("request hash mismatch")
        expected_id = f"knowledge-card-image-request:{expected_request[:24]}"
        if self.request_id != expected_id:
            raise ValueError("request identity mismatch")
        return self


def _unique_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _prompt_json_text(value: str) -> str:
    """Return ``value`` exactly as ``_render_prompt`` serializes it.

    The prompt embeds visible texts as JSON, so newlines, quotes and backslashes
    appear escaped.  Texts without such characters are unchanged.
    """
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _presentation_for_segment(segment: ContentSegment) -> str:
    try:
        return RENDERER_ALLOWLIST[segment.renderer]
    except KeyError as error:
        raise KnowledgeCardImageRequestError(
            f"unsupported KnowledgeCard renderer: {segment.renderer}"
        ) from error


def _source_elements(card: KnowledgeCard) -> tuple[KnowledgeCardImageSourceElementV1, ...]:
    presentations = tuple(_presentation_for_segment(segment) for segment in card.content_segments)
    if len(set(presentations)) != 1:
        raise KnowledgeCardImageRequestError(
            "KnowledgeCard content segments use inconsistent renderers"
        )
    presentation = presentations[0]
    return tuple(
        KnowledgeCardImageSourceElementV1(
            element_index=index,
            text=segment.text,
            fact_ids=segment.fact_ids,
            evidence_ids=segment.evidence_ids,
            source_renderer=segment.renderer,
            presentation=presentation,
            text_sha256=sha256_text(segment.text),
        )
        for index, segment in enumerate(card.content_segments)
    )


def _render_prompt(
    *,
    title: str,
    presentation: str,
    source_elements: tuple[KnowledgeCardImageSourceElementV1, ...],
    diagram_type: str,
    diagram_elements: tuple[str, ...],
    annotations: tuple[str, ...],
    expected_visible_texts: tuple[str, ...],
    render_policy: tuple[str, ...],
) -> str:
    projection = {
        "render_policy": render_policy,
        "title": title,
        "presentation": presentation,
        "source_elements": [
            {
                "element_index": item.element_index,
                "text": item.text,
                "fact_ids": item.fact_ids,
                "evidence_ids": item.evidence_ids,
            }
            for item in source_elements
        ],
        "diagram_type": diagram_type if presentation == "diagram" else "",
        "diagram_elements": diagram_elements if presentation == "diagram" else (),
        "annotations": annotations if presentation == "diagram" else (),
        "expected_visible_texts": expected_visible_texts,
    }
    return (
        "Render the supplied source-derived knowledge card exactly. "
        "Do not add visible text, claims, numbers, formulas, or units.\n"
        + json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def build_knowledge_card_image_request_v1(
    card: KnowledgeCard,
    *,
    profile: ImageEnvelopeProfile,
) -> KnowledgeCardImageRequestV1:
    """Build one deterministic request from a validated ``KnowledgeCard``.

    ``profile`` is runtime image policy.  It is not a source-content input and
    contains no credentials.  The function intentionally accepts no caller,
    client, transport, path, or raw legacy prompt mapping.
    """

    if not isinstance(card, KnowledgeCard):
        raise KnowledgeCardImageRequestError("bridge requires a KnowledgeCard instance")
    if not isinstance(profile, ImageEnvelopeProfile):
        raise KnowledgeCardImageRequestError("bridge requires an ImageEnvelopeProfile")
    if card.grounding_status not in _GROUNDING_ALLOWED:
        raise KnowledgeCardImageRequestError(
            "KnowledgeCard grounding is not verified for image generation"
        )
    if card.renderability is not Renderability.PUBLISHABLE:
        raise KnowledgeCardImageRequestError(
            "KnowledgeCard is not publishable for image generation"
        )
    if card.kp_type in _REJECTED_CARD_TYPES:
        raise KnowledgeCardImageRequestError(
            "KnowledgeCard type is excluded from image generation"
        )

    elements = _source_elements(card)
    presentation = elements[0].presentation
    diagram_type = card.diagram_type if presentation == "diagram" else ""
    diagram_elements = card.diagram_elements if presentation == "diagram" else ()
    annotations = card.annotations if presentation == "diagram" else ()
    expected_visible_texts = _unique_texts(
        (
            card.title,
            *(item.text for item in elements),
            *diagram_elements,
            *annotations,
        )
    )
    prompt_text = _render_prompt(
        title=card.title,
        presentation=presentation,
        source_elements=elements,
        diagram_type=diagram_type,
        diagram_elements=diagram_elements,
        annotations=annotations,
        expected_visible_texts=expected_visible_texts,
        render_policy=_RENDER_POLICY,
    )
    source_pages = tuple(sorted({item.page_num for item in card.evidence_refs}))
    content_sha256 = canonical_hash(
        {
            "card_id": card.id,
            "raw_id": card.raw_id,
            "title": card.title,
            "presentation": presentation,
            "source_elements": elements,
            "diagram_type": diagram_type,
            "diagram_elements": diagram_elements,
            "annotations": annotations,
        }
    )
    provenance_sha256 = canonical_hash(
        {
            "card_id": card.id,
            "raw_id": card.raw_id,
            "source_elements": tuple(
                {
                    "element_index": item.element_index,
                    "fact_ids": item.fact_ids,
                    "evidence_ids": item.evidence_ids,
                }
                for item in elements
            ),
            "evidence_refs": card.evidence_refs,
            "source_pages": source_pages,
        }
    )
    size = f"{profile.image_width}x{profile.image_height}"
    request_payload: dict[str, Any] = {
        "card_id": card.id,
        "raw_id": card.raw_id,
        "title": card.title,
        "presentation": presentation,
        "source_elements": elements,
        "evidence_refs": card.evidence_refs,
        "source_pages": source_pages,
        "diagram_type": diagram_type,
        "diagram_elements": diagram_elements,
        "annotations": annotations,
        "expected_visible_texts": expected_visible_texts,
        "content_sha256": content_sha256,
        "provenance_sha256": provenance_sha256,
        "model": profile.model,
        "size": size,
        "prompt_text": prompt_text,
        "prompt_sha256": sha256_text(prompt_text),
    }
    request_sha256 = canonical_hash(
        {
            **request_payload,
            "n": 1,
            "response_format": "b64_json",
            "provider_neutral": True,
            "render_policy": _RENDER_POLICY,
        }
    )
    request_payload["request_sha256"] = request_sha256
    request_payload["request_id"] = f"knowledge-card-image-request:{request_sha256[:24]}"
    return KnowledgeCardImageRequestV1.model_validate(request_payload)


def project_knowledge_card_image_request_v1(
    request: KnowledgeCardImageRequestV1,
    *,
    profile: ImageEnvelopeProfile,
) -> tuple[ImageRequestProjection, dict[str, Any]]:
    """Project a bridge request through the existing provider allow-list.

    The returned sidecar is internal metadata.  Only the existing five public
    image fields are eligible for the provider request; this helper still does
    not call an image provider.
    """

    if not isinstance(request, KnowledgeCardImageRequestV1):
        raise KnowledgeCardImageRequestError("projection requires a bridge request")
    if not isinstance(profile, ImageEnvelopeProfile):
        raise KnowledgeCardImageRequestError("projection requires an ImageEnvelopeProfile")
    payload: dict[str, Any] = {
        "model": request.model,
        "n": request.n,
        "prompt": request.prompt_text,
        "response_format": request.response_format,
        "size": request.size,
        "schema_version": request.schema_version,
        "request_id": request.request_id,
        "source_card_id": request.card_id,
        "source_card_raw_id": request.raw_id,
        "source_presentation": request.presentation,
        "source_pages": request.source_pages,
        "evidence_refs": request.evidence_refs,
        "source_elements": request.source_elements,
        "content_sha256": request.content_sha256,
        "provenance_sha256": request.provenance_sha256,
        "request_sha256": request.request_sha256,
    }
    return project_image_request(payload, profile)
