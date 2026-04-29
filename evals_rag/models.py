"""Pydantic models for the RAG retrieval / re-ranking pipeline.

The shapes here are deliberately strict: ``ConfigDict(extra="forbid")``
catches misnamed Qdrant payload fields the moment a retriever maps them in
wrong, and the runner / metrics layer trusts these models as the single
source of truth for what a retrieved or re-ranked concept looks like.

The vectorizer (yet to be implemented) will produce ``OntologyConcept``
instances; the retriever returns those wrapped in :class:`RetrievedConcept`,
the reranker re-scores them as :class:`RerankedConcept`, and the planner
consumes a :class:`ConceptCandidatePack` summarizing the whole cycle.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConceptKind = Literal["class", "property", "individual", "graph", "datatype", "unknown"]
RetrievalSource = Literal["qdrant", "mock", "hybrid"]


class OntologyConcept(BaseModel):
    """A single ontology concept stored (eventually) in the vector index.

    Mirrors the shape of :class:`graph_mcp.graph.schema_discovery.SchemaTerm`
    where possible, but adds the alias / example / metadata fields a vector
    pipeline needs. ``source`` records where the concept was loaded from so
    the report can distinguish hand-curated entries from auto-extracted ones.
    """

    model_config = ConfigDict(extra="forbid")

    iri: str
    prefixed_name: str | None = None
    label: str | None = None
    aliases: list[str] = Field(default_factory=list)
    kind: ConceptKind
    description: str | None = None
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalQuery(BaseModel):
    """One retrieval call against the ontology index.

    ``mention`` is set when the retrieval is scoped to a specific extracted
    mention; full-question retrievals leave it ``None``. ``expected_kinds``
    narrows the search to e.g. only properties when the mention looks like
    a verb phrase.
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    mention: str | None = None
    expected_kinds: list[ConceptKind] = Field(default_factory=list)
    limit: int = 20


class RetrievedConcept(BaseModel):
    """A concept returned by a retriever, with its retrieval-time score."""

    model_config = ConfigDict(extra="forbid")

    concept: OntologyConcept
    score: float
    retrieval_rank: int
    retrieval_source: RetrievalSource
    matched_text: str | None = None
    explanation: str | None = None


class RerankedConcept(BaseModel):
    """A retrieved concept after the re-ranker has assigned a final score.

    ``rank`` is the post-rerank ordinal (0-indexed). ``final_score`` is the
    score the planner ultimately uses to sort candidates; the retrieval and
    re-rank scores are kept side-by-side for diagnostics.
    """

    model_config = ConfigDict(extra="forbid")

    concept: OntologyConcept
    retrieval_score: float
    rerank_score: float
    final_score: float
    rank: int
    explanation: str | None = None


class ConceptCandidatePack(BaseModel):
    """Compact summary of one retrieve-then-rerank cycle.

    Passed to the planner prompt builder. ``selected`` is the truncated set
    the LLM should see; ``retrieved`` and ``reranked`` are kept for
    diagnostics so the report can show what was filtered out.
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    mentions: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedConcept] = Field(default_factory=list)
    reranked: list[RerankedConcept] = Field(default_factory=list)
    selected: list[RerankedConcept] = Field(default_factory=list)
    unresolved_mentions: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class RagPlannerDiagnostics(BaseModel):
    """Per-question diagnostics emitted by the RAG planner workflow.

    The runner attaches one of these to every case result so the report can
    show which retrieval queries ran, which concepts came back, what the
    re-ranker did, and what the underlying planner workflow saw.
    """

    model_config = ConfigDict(extra="forbid")

    mentions: list[str] = Field(default_factory=list)
    retrieval_queries: list[RetrievalQuery] = Field(default_factory=list)
    retrieved_concepts: list[RetrievedConcept] = Field(default_factory=list)
    reranked_concepts: list[RerankedConcept] = Field(default_factory=list)
    selected_concepts: list[RerankedConcept] = Field(default_factory=list)
    unresolved_mentions: list[str] = Field(default_factory=list)
    planner_diagnostics: dict[str, Any] = Field(default_factory=dict)
    """Serialized :class:`evals.agent.PlannerDiagnostics` for cross-checks."""

    candidate_pack_text: str | None = None
    """Rendered candidate-pack section as it was injected into the prompt."""
