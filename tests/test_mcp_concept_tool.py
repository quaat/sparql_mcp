"""Tests for the ``discover_ontology_concepts`` MCP tool.

These tests exercise the tool as the MCP server would call it: through
:func:`tool_discover_ontology_concepts`, with the public
:class:`OntologyConceptRetriever` contract injected via ``retriever=``.
The retrieval pipeline is stubbed at the vectorizer's own facade so we
never spin up Qdrant or Foundry.
"""

from __future__ import annotations

from typing import Any

import pytest
from ontology_vectorizer import (
    OntologyConceptRetriever,
    OntologyConceptSearchResult,
    OntologyRetrievalError,
    OntologyRetrieverConfigError,
    OntologyVectorizerConfig,
)
from ontology_vectorizer.config import OntologyConfig, QdrantConfig

from graph_mcp.concept_retrieval import (
    DiscoverOntologyConceptsInput,
    DiscoverOntologyConceptsOutput,
    MCPConceptRetrievalSettings,
    reset_ontology_retriever,
    set_ontology_retriever,
    tool_discover_ontology_concepts,
)


@pytest.fixture(autouse=True)
def _isolate_retriever_singleton() -> None:
    """Make sure no test inherits the cached singleton from another test."""
    reset_ontology_retriever()
    yield
    reset_ontology_retriever()


def _config() -> OntologyVectorizerConfig:
    return OntologyVectorizerConfig(
        ontology=OntologyConfig(ontology_id="ocean-demo"),
        qdrant=QdrantConfig(collection_name="test"),
    )


class _FakeService:
    """Minimal RetrievalService stand-in for the vectorizer facade."""

    def __init__(
        self, results: list[OntologyConceptSearchResult] | None = None
    ) -> None:
        self.results = results or []
        self.calls: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None

    def query(
        self,
        text: str,
        *,
        ontology_id: str | None = None,
        include_deprecated: bool = False,
        kinds: list[str] | None = None,
    ) -> list[Any]:
        self.calls.append(
            {
                "text": text,
                "ontology_id": ontology_id,
                "include_deprecated": include_deprecated,
                "kinds": list(kinds) if kinds else None,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        # The facade expects a list of ScoredConcept; we produce one and
        # let the facade map it. This avoids reproducing the wire schema
        # twice.
        from ontology_vectorizer.models import ScoredConcept

        return [
            ScoredConcept(
                concept_id=r.concept_id,
                iri=r.iri,
                compact_id=r.compact_id,
                preferred_label=r.preferred_label,
                kind=r.kind,  # type: ignore[arg-type]
                final_score=r.score,
                components={
                    "identity": r.identity_score or 0.0,
                    "reranker": r.reranker_score or 0.0,
                    "lexical": r.lexical_score or 0.0,
                    "context": r.context_score or 0.0,
                    "group": r.group_score or 0.0,
                },
                payload={
                    "iri": r.iri,
                    "ontology_id": r.ontology_id,
                    "preferred_label": r.preferred_label,
                    "labels": list(r.labels),
                    "alt_labels": list(r.alt_labels),
                    "deprecated": r.deprecated,
                    "definitions": [r.definition] if r.definition else [],
                    "parents": list(r.parents),
                    "ancestors": list(r.ancestors),
                    "children": list(r.children),
                    "siblings": list(r.siblings),
                    "group_ids": list(r.group_ids),
                    "branch_ids": [],
                },
                explanation=[r.explanation] if r.explanation else [],
            )
            for r in self.results
        ]


def _make_retriever(service: _FakeService) -> OntologyConceptRetriever:
    return OntologyConceptRetriever(
        config=_config(),
        retrieval_service=service,  # type: ignore[arg-type]
    )


def _result(
    *,
    concept_id: str = "c1",
    iri: str = "https://example.org/sst",
    preferred_label: str = "sea surface temperature",
    score: float = 0.94,
    deprecated: bool = False,
) -> OntologyConceptSearchResult:
    return OntologyConceptSearchResult(
        concept_id=concept_id,
        iri=iri,
        compact_id="var:sea-surface-temperature",
        preferred_label=preferred_label,
        labels=[preferred_label],
        alt_labels=["SST"],
        kind="skos_concept",
        definition="Temperature at the ocean surface.",
        ontology_id="ocean-demo",
        score=score,
        identity_score=0.7,
        reranker_score=0.85,
        deprecated=deprecated,
        parents=["var:temperature"],
        ancestors=["var:temperature", "var:physical-property"],
        explanation="exact-label match",
    )


@pytest.mark.asyncio
async def test_tool_returns_structured_results() -> None:
    service = _FakeService([_result()])
    retriever = _make_retriever(service)
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="sea surface temperature"),
        settings=MCPConceptRetrievalSettings(),
        retriever=retriever,
    )
    assert isinstance(out, DiscoverOntologyConceptsOutput)
    assert out.error is None
    assert len(out.results) == 1
    r = out.results[0]
    assert r.iri == "https://example.org/sst"
    assert r.preferred_label == "sea surface temperature"
    assert r.compact_id == "var:sea-surface-temperature"
    assert r.alt_labels == ["SST"]
    assert r.parents == ["var:temperature"]
    assert r.score == pytest.approx(0.94)
    assert r.identity_score == pytest.approx(0.7)
    assert r.explanation == "exact-label match"
    assert out.retrieval_strategy == "hybrid_multi_stage_graph_aware"


@pytest.mark.asyncio
async def test_tool_handles_no_results() -> None:
    retriever = _make_retriever(_FakeService([]))
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="nothing matches"),
        retriever=retriever,
    )
    assert out.results == []
    assert out.error is None


@pytest.mark.asyncio
async def test_tool_returns_structured_error_when_retriever_fails() -> None:
    service = _FakeService()
    service.fail_with = OntologyRetrievalError("Qdrant unreachable")
    retriever = _make_retriever(service)
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="anything"),
        retriever=retriever,
    )
    assert out.results == []
    assert out.error is not None
    assert "Qdrant unreachable" in out.error


@pytest.mark.asyncio
async def test_tool_redacts_credentials_in_errors() -> None:
    service = _FakeService()
    service.fail_with = RuntimeError("auth failed: Bearer abcdef-secret")
    retriever = _make_retriever(service)
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="anything"),
        retriever=retriever,
    )
    assert out.error is not None
    assert "Bearer" not in out.error
    assert "abcdef-secret" not in out.error


@pytest.mark.asyncio
async def test_tool_disabled_short_circuits() -> None:
    settings = MCPConceptRetrievalSettings(enabled=False)
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="x"),
        settings=settings,
        retriever=None,
    )
    assert out.error is not None
    assert "disabled" in out.error
    assert out.results == []


@pytest.mark.asyncio
async def test_tool_does_not_expose_raw_payload_fields() -> None:
    """The MCP wire schema must not leak debug or internal payload fields."""
    service = _FakeService([_result()])
    retriever = _make_retriever(service)
    out = await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(query="hi"),
        retriever=retriever,
    )
    assert out.results
    # The MCP result schema is closed (extra=forbid); attempting to dump it
    # and look for debug fields confirms we didn't smuggle them through.
    dumped = out.results[0].model_dump()
    assert "debug" not in dumped
    assert "rerank_text" not in dumped
    assert "identity_text" not in dumped
    assert "ingestion_run_id" not in dumped


@pytest.mark.asyncio
async def test_tool_propagates_filters_to_retriever() -> None:
    service = _FakeService([_result()])
    retriever = _make_retriever(service)
    await tool_discover_ontology_concepts(
        DiscoverOntologyConceptsInput(
            query="hi",
            kind_filter=["skos_concept"],
            include_deprecated=True,
            ontology_id="ocean-demo",
        ),
        retriever=retriever,
    )
    assert service.calls
    call = service.calls[0]
    assert call["ontology_id"] == "ocean-demo"
    assert call["include_deprecated"] is True
    assert call["kinds"] == ["skos_concept"]


@pytest.mark.asyncio
async def test_singleton_initialised_once_per_process() -> None:
    """``set_ontology_retriever`` is honoured and not rebuilt per request."""
    service = _FakeService([_result()])
    set_ontology_retriever(_make_retriever(service))
    inp = DiscoverOntologyConceptsInput(query="hi")
    out1 = await tool_discover_ontology_concepts(inp)
    out2 = await tool_discover_ontology_concepts(inp)
    assert out1.results and out2.results
    # Two requests, one underlying service: both calls land on the same
    # ``_FakeService`` instance (which proves we're not building a fresh
    # retriever per call).
    assert len(service.calls) == 2


@pytest.mark.asyncio
async def test_tool_input_rejects_empty_query() -> None:
    """Pydantic validation refuses empty queries before the tool is called."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DiscoverOntologyConceptsInput(query="")


@pytest.mark.asyncio
async def test_init_failure_surfaces_as_structured_error() -> None:
    """If the singleton can't be built, the tool returns ``error=...``."""

    def _boom() -> OntologyConceptRetriever:
        raise OntologyRetrieverConfigError("missing FOUNDRY_API_BASE_URL")

    # Patch the lazy initializer used by ``tool_discover_ontology_concepts``
    # by clearing the cache and forcing the next call to fail.
    reset_ontology_retriever()
    import graph_mcp.concept_retrieval as cr

    saved = cr.get_ontology_retriever
    cr.get_ontology_retriever = _boom  # type: ignore[assignment]
    try:
        out = await tool_discover_ontology_concepts(
            DiscoverOntologyConceptsInput(query="hi")
        )
    finally:
        cr.get_ontology_retriever = saved  # type: ignore[assignment]

    assert out.error is not None
    assert "FOUNDRY_API_BASE_URL" in out.error
