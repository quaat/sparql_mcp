"""Tests for the endpoint-backed schema provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mcp.graph import LocalRdflibEndpoint, SparqlDiscoveryConfig, SparqlSchemaProvider
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.mcp_tools.resources import (
    schema_classes_json,
    schema_individuals_json,
    schema_properties_json,
)

EVAL_GRAPH = Path(__file__).parent.parent / "evals" / "sample_graph.ttl"


@pytest.mark.asyncio
async def test_sparql_schema_provider_discovers_classes_and_properties() -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(
        endpoint,
        config=SparqlDiscoveryConfig(
            base_prefixes={"ex": "http://example.org/"},
            timeout_ms=2000,
            max_classes=50,
            max_properties=50,
            max_individuals=50,
        ),
    )
    snap = await provider.refresh()

    class_iris = {c.iri for c in snap.classes}
    assert "http://example.org/Person" in class_iris
    assert "http://example.org/Company" in class_iris

    prop_iris = {p.iri for p in snap.properties}
    assert "http://example.org/worksFor" in prop_iris
    assert "http://example.org/knows" in prop_iris

    assert snap.prefixes.get("ex") == "http://example.org/"


@pytest.mark.asyncio
async def test_sparql_schema_provider_discovers_individuals() -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(endpoint, config=SparqlDiscoveryConfig(timeout_ms=2000))
    snap = await provider.refresh()
    iris = {i.iri for i in snap.individuals}
    assert "http://example.org/alice" in iris
    assert "http://example.org/Acme" in iris


@pytest.mark.asyncio
async def test_sparql_schema_provider_caches() -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(
        endpoint,
        config=SparqlDiscoveryConfig(cache_ttl_seconds=999.0, timeout_ms=2000),
    )
    snap1 = await provider.refresh()
    snap2 = await provider.refresh()  # Should hit the cache, not requery.
    assert snap1 is snap2


@pytest.mark.asyncio
async def test_individuals_resource_exposes_discovered_data() -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(endpoint, config=SparqlDiscoveryConfig(timeout_ms=2000))
    await provider.refresh()
    body = schema_individuals_json(provider)
    assert "alice" in body or "Alice" in body
    # Both classes and properties resources still work alongside.
    assert "Person" in schema_classes_json(provider)
    assert "worksFor" in schema_properties_json(provider)


@pytest.mark.asyncio
async def test_term_resolver_uses_discovered_schema() -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(endpoint, config=SparqlDiscoveryConfig(timeout_ms=2000))
    await provider.refresh()
    resolver = TermResolver(provider)
    result = resolver.resolve(["Person"], expected_kinds=["class"])
    iris = {c.iri for c in result.candidates if c.kind == "class"}
    assert "http://example.org/Person" in iris
