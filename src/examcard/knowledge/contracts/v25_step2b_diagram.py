"""语义图三件：节点、边、桥（拆分层 1）。

桥模型自证节点与边的引用闭合（边的两端必须在节点集里），这条闭合校验是 diagram 族
契约放行的前提。三个模型只用到 ``v25_support`` 的摘要形状与 id 助手。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .v25_support import _SHA256_PATTERN, _v25_id


class V25SemanticDiagramNode(StrictModel):
    """One reviewed semantic_v2 node, anchored to original Evidence IDs."""

    node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=160)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_node(self) -> "V25SemanticDiagramNode":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("V25 semantic-diagram node Evidence IDs must be unique")
        return self


class V25SemanticDiagramEdge(StrictModel):
    """One reviewed semantic_v2 edge without an invented text-anchor ID."""

    source_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    target_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    relation: str = Field(min_length=1, max_length=160)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    basis: str = Field(min_length=16, max_length=1600)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_edge(self) -> "V25SemanticDiagramEdge":
        if self.source_node_id == self.target_node_id:
            raise ValueError("V25 semantic-diagram edges cannot self-reference")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("V25 semantic-diagram edge Evidence IDs must be unique")
        return self


class V25SemanticDiagramBridge(StrictModel):
    """Source-bound bridge from a reviewed semantic_v2 diagram to Step 2B."""

    bridge_id: str = Field(pattern=r"^v25-semantic-diagram-bridge:[0-9a-f]{24}$")
    review_id: str = Field(pattern=r"^diagram:[0-9a-f]{24}$")
    semantic_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    figure_evidence_id: str
    semantic_title: str = Field(min_length=2, max_length=160)
    nodes: tuple[V25SemanticDiagramNode, ...] = Field(min_length=2)
    edges: tuple[V25SemanticDiagramEdge, ...] = Field(min_length=1)
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_bridge(self) -> "V25SemanticDiagramBridge":
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("V25 semantic-diagram bridge node IDs must be unique")
        if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.edges):
            raise ValueError("V25 semantic-diagram bridge edges must reference reviewed nodes")
        if any(self.figure_evidence_id not in node.evidence_ids for node in self.nodes):
            raise ValueError("V25 semantic-diagram bridge nodes must retain the source figure Evidence")
        if any(self.figure_evidence_id not in edge.evidence_ids for edge in self.edges):
            raise ValueError("V25 semantic-diagram bridge edges must retain the source figure Evidence")
        payload = self.model_dump(mode="json", exclude={"bridge_id"})
        if self.bridge_id != _v25_id("v25-semantic-diagram-bridge", payload):
            raise ValueError("V25 semantic-diagram bridge ID must be deterministic")
        return self
