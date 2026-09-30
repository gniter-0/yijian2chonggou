"""Typed, non-secret configuration for one exam subject runtime.

An exam subject profile selects the already-versioned evidence, knowledge, and
image policies.  It is configuration only: it has no provider credentials,
delivery authority, or runtime state.
"""
from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, StrictStr, model_validator

from examcard.common.hashing import canonical_hash
from examcard.evidence.ingest import EvidenceEngineConfig
from examcard.materials.schema import ContentPolicy, MaterialProfile, StrictModel
from examcard.providers.envelope_profiles import ImageEnvelopeProfile

__all__ = [
    "EXAM_SUBJECT_PROFILE_VERSION",
    "ExamSubjectProfileV1",
]


EXAM_SUBJECT_PROFILE_VERSION: Final = "examcard_exam_subject_profile_v1"


class ExamSubjectProfileV1(StrictModel):
    """Immutable, secret-free policy bundle for one exam subject.

    Multiple exams are supported by supplying different instances.  The
    runner never infers a subject from a PDF name or its contents.
    """

    schema_version: Literal["examcard_exam_subject_profile_v1"] = EXAM_SUBJECT_PROFILE_VERSION
    material_profile: MaterialProfile
    content_policy: ContentPolicy
    evidence_config: EvidenceEngineConfig = Field(default_factory=EvidenceEngineConfig)
    step2_model: StrictStr = Field(min_length=1)
    image_profile: ImageEnvelopeProfile

    @model_validator(mode="after")
    def validate_policy_binding(self) -> "ExamSubjectProfileV1":
        if self.material_profile.content_policy_id != self.content_policy.policy_id:
            raise ValueError("exam subject profile content policy does not match material profile")
        if self.material_profile.content_policy_revision != self.content_policy.revision:
            raise ValueError("exam subject profile content policy revision does not match material profile")
        return self

    @property
    def profile_id(self) -> str:
        """Return the source-owned material profile identity."""

        return self.material_profile.profile_id

    @property
    def profile_revision(self) -> int:
        """Return the source-owned material profile revision."""

        return self.material_profile.revision

    def canonical_sha256(self) -> str:
        """Hash only deterministic, non-secret runtime configuration."""

        return canonical_hash(self.model_dump(mode="json"))
