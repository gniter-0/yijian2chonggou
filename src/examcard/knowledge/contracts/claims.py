"""Model-owned numeric claims and locally grounded numeric/condition claims."""
from __future__ import annotations
from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ...evidence.layout import normalized_compare_text
from ...materials.schema import StrictModel

from .enums import condition_operators_in_text
from .numeric_tokens import NumericTokenClassificationRecord


class ProposalNumericTokenAudit(StrictModel):
    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    tokens: tuple[NumericTokenClassificationRecord, ...] = ()


class ModelNumericClaim(StrictModel):
    """Minimal model-owned numeric selection; parent Evidence is local-only."""

    value: str = Field(
        min_length=1,
        description="Exact quantitative value present in the Fact assertion and the selected anchor span.",
        json_schema_extra={"examples": ["65MPa"]},
    )
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")

    @model_validator(mode="after")
    def validate_numeric_claim(self) -> "ModelNumericClaim":
        if self.value != self.value.strip() or not any(char.isdigit() for char in self.value):
            raise ValueError("numeric claims require a stripped value containing a digit")
        return self


class GroundedNumericClaim(ModelNumericClaim):
    """Canonical numeric claim with the locally-derived immutable parent Evidence."""

    evidence_id: str


class GroundedConditionClaim(StrictModel):
    value: str = Field(
        min_length=2,
        description="Complete condition span present in both the Fact assertion and selected source Evidence; never an isolated operator.",
        json_schema_extra={"examples": ["不得采用废机油", "吊环的拉应力应不大于65MPa"]},
    )
    evidence_id: str
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")

    @model_validator(mode="after")
    def validate_condition_span(self) -> "GroundedConditionClaim":
        if self.value != self.value.strip():
            raise ValueError("condition claim spans must be stripped")
        operators = condition_operators_in_text(self.value)
        if not operators:
            raise PydanticCustomError(
                "condition_operator_missing",
                "condition claim span must contain a recognized condition operator",
            )
        remainder = normalized_compare_text(self.value)
        for operator in set(operators):
            remainder = remainder.replace(normalized_compare_text(operator.value), "")
        if not remainder:
            raise PydanticCustomError(
                "condition_operator_only",
                "condition claim must bind a complete span, not an isolated operator",
            )
        return self
