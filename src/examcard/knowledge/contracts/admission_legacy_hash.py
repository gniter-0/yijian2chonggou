"""Legal legacy hash variants for the admission fixture family (both lineages).

r8x lineage  (phase2r84, admission kernel; phase2r810 duplicates it verbatim):
  canonical_json / canonical_hash / text_hash / hash_model_r8x -- the builder
  does NOT exclude schema_version from the self-hash payload.
r9/r10x lineage (phase2r102 bytes; phase2r10 imports canonical_hash from
  phase2r9 and phase2r103/r104 call r102.canonical_hash):
  _jsonable / canonical_json_r10x / canonical_hash_r10x / hash_model_r10x /
  hash_model_r102 -- builders exclude {schema_version, field}.

Function bodies are byte-exact slices; only builder def-lines and the internal
canonical_hash binding inside the two r10x builders gained explicit _r10x names
(identifier-level, hash-invisible). Output equivalence of the lineages on
model_dump payloads is proven by migration/verify_admission_fixtures.py against
the frozen production artifacts. Never fold into examcard.common.hashing.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ...materials.schema import StrictModel


# --- r8x lineage (phase2r84, verbatim) ---


def canonical_json(value: Any) -> bytes:
    def normalize(item: Any) -> Any:
        if isinstance(item, StrictModel):
            return normalize(item.model_dump(mode="json"))
        if isinstance(item, Mapping):
            return {str(key): normalize(value) for key, value in sorted(item.items(), key=lambda pair: str(pair[0]))}
        if isinstance(item, (tuple, list)):
            return [normalize(value) for value in item]
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_model_r8x(model_type: type[StrictModel], payload: dict[str, Any], field_name: str) -> StrictModel:
    raw = dict(payload)
    for name, field in model_type.model_fields.items():
        if name in raw or field.is_required():
            continue
        raw[name] = field.default_factory() if field.default_factory is not None else field.default
    raw[field_name] = canonical_hash({key: value for key, value in raw.items() if key != field_name})
    return model_type.model_validate(raw)


# --- r9 lineage (phase2r9 canonical_json traversal, verbatim body; def-line


# disambiguated -- its canonical_hash/text_hash bodies are byte-identical to


# the r84 ones above, so the shared names cover them) ---


def canonical_json_r9x(value: Any) -> bytes:
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, dict):
        value = {key: json.loads(canonical_json_r9x(item).decode("utf-8")) for key, item in value.items()}
    elif isinstance(value, (tuple, list)):
        value = [json.loads(canonical_json_r9x(item).decode("utf-8")) for item in value]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --- r10x lineage (phase2r102 / phase2r10 bytes; builder names disambiguated) ---


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrictModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json_r10x(value: Any) -> bytes:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash_r10x(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hash_model_r102(model_type: type[StrictModel], payload: Mapping[str, Any], field: str) -> StrictModel:
    document = dict(payload)
    for name, definition in model_type.model_fields.items():
        if name in document or definition.is_required():
            continue
        document[name] = definition.default_factory() if definition.default_factory else definition.default
    document[field] = canonical_hash_r10x({key: value for key, value in document.items() if key not in {field, "schema_version"}})
    return model_type.model_validate(document)


def hash_model_r10x(model_type: type[StrictModel], payload: dict[str, Any], field: str) -> StrictModel:
    document = dict(payload)
    draft = model_type.model_construct(**{**document, field: "0" * 64})
    document[field] = canonical_hash_r10x(draft.model_dump(mode="json", exclude={"schema_version", field}))
    return model_type.model_validate(document)
