"""Contract-level assertions for the threshold-preserving Step 3 V3 lane."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.knowledge.contracts.step3_card_v3 import (
    LayoutContractV3,
    ModelCardV3,
    Step3CardContractV3,
    Step3ModelInputV3,
)
from tests.fixtures.step3_v3 import compile_v3


def test_threshold_contract_keeps_source_layout_roles_order_and_provenance(tmp_path: Path) -> None:
    _path, fixture, file_sha, compilation = compile_v3(tmp_path)
    contract = compilation.contracts[0]

    assert fixture.fixture_sha256 == compilation.source_typed_fixture_sha256
    assert file_sha == compilation.source_typed_fixture_file_sha256
    assert file_sha != fixture.fixture_sha256
    assert contract.presentation == "threshold"
    assert contract.layout.presentation == "threshold"
    assert tuple(item.source_role for item in contract.display_elements) == ("threshold_token",)
    assert tuple(item.source_order for item in contract.display_elements) == (1,)
    assert tuple(item.element_id for item in contract.layout.threshold_blocks) == tuple(
        item.element_id for item in contract.display_elements
    )
    assert contract.display_elements[0].provenance.fact_id == fixture.candidates[0].fact_id
    assert contract.display_elements[0].text_sha256


def test_v3_model_input_binds_both_fixture_hashes_and_exact_element_ids(tmp_path: Path) -> None:
    _path, fixture, file_sha, compilation = compile_v3(tmp_path)
    model_input = compilation.model_input

    assert isinstance(model_input, Step3ModelInputV3)
    assert model_input.source_typed_fixture_file_sha256 == file_sha
    assert model_input.source_typed_fixture_sha256 == fixture.fixture_sha256
    assert model_input.model_input_id.startswith("step3-v3-model-input:")
    card = model_input.cards[0]
    assert card.presentation == "threshold"
    assert card.layout.threshold_blocks
    assert tuple(item.element_id for item in card.visible_elements) == card.layout.element_ids
    assert tuple(item.text_sha256 for item in card.visible_elements)
    assert card.visible_elements[0].provenance == compilation.contracts[0].display_elements[0].provenance


def test_threshold_cannot_be_aliased_to_constraint_layout() -> None:
    with pytest.raises(ValueError, match="layout is incomplete|layout mixes presentation types"):
        LayoutContractV3.model_validate(
            {
                "presentation": "threshold",
                "threshold_blocks": (),
                "constraint_blocks": (
                    {"element_id": "element:test:1", "token_order": 1},
                ),
            }
        )


def test_v3_card_roundtrip_is_strict(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    payload = compilation.contracts[0].model_dump(mode="json")
    restored = Step3CardContractV3.model_validate(payload)
    assert restored == compilation.contracts[0]
    with pytest.raises(ValueError):
        Step3CardContractV3.model_validate({**payload, "unexpected": True})


def test_model_card_rejects_contradictory_card_and_element_authority(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    card = compilation.model_input.cards[0]
    tampered_binding = card.source_binding.model_copy(update={"subject_id": "subject:other"})
    with pytest.raises(ValueError, match="contradicts element provenance"):
        ModelCardV3.model_validate({**card.model_dump(mode="json"), "source_binding": tampered_binding.model_dump(mode="json")})
