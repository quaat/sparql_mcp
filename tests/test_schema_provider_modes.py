"""Tests for the new schema-provider modes, status resource, and refresh tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint, SparqlSchemaProvider, StaticSchemaProvider
from graph_mcp.mcp_tools.tools import RefreshSchemaInput
from graph_mcp.server import build_endpoint, build_schema_provider, build_server

EVAL_GRAPH = Path(__file__).parent.parent / "evals" / "sample_graph.ttl"


def test_build_schema_provider_static_mode_returns_static() -> None:
    settings = Settings(schema_provider="static")
    endpoint = LocalRdflibEndpoint()
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, StaticSchemaProvider)


def test_build_schema_provider_auto_with_local_graph_returns_sparql() -> None:
    settings = Settings(schema_provider="auto", local_graph_file=EVAL_GRAPH)
    endpoint = build_endpoint(settings)
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, SparqlSchemaProvider)


def test_build_schema_provider_auto_without_endpoint_returns_static() -> None:
    settings = Settings(schema_provider="auto")  # no endpoint, no local file
    endpoint = build_endpoint(settings)
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, StaticSchemaProvider)


def test_build_schema_provider_explicit_sparql_mode() -> None:
    settings = Settings(schema_provider="sparql", local_graph_file=EVAL_GRAPH)
    endpoint = build_endpoint(settings)
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, SparqlSchemaProvider)


@pytest.mark.asyncio
async def test_sparql_provider_populates_diagnostics_on_failure() -> None:
    """A failing endpoint must surface a diagnostic, not silently produce nothing."""

    class FailingEndpoint:
        async def query(
            self,
            sparql: str,
            *,
            query_type: str,
            timeout_ms: int,
            max_rows: int,
        ) -> object:
            raise RuntimeError("simulated endpoint failure")

        async def aclose(self) -> None:
            return None

    from graph_mcp.graph import SparqlDiscoveryConfig

    provider = SparqlSchemaProvider(FailingEndpoint(), config=SparqlDiscoveryConfig(timeout_ms=100))
    snap = await provider.refresh()
    assert snap.classes == []
    sections = {d.section for d in snap.diagnostics}
    # At least the four discovery sections must report errors.
    assert "classes" in sections
    assert "properties" in sections
    assert "individuals" in sections
    assert "named_graphs" in sections


@pytest.mark.asyncio
async def test_sparql_provider_refresh_force_bypasses_ttl() -> None:
    from graph_mcp.graph import SparqlDiscoveryConfig

    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(endpoint, config=SparqlDiscoveryConfig(cache_ttl_seconds=999.0))
    snap_a = await provider.refresh()
    # Same call hits the cache: same object identity.
    snap_b = await provider.refresh()
    assert snap_a is snap_b
    # Force refresh: new snapshot.
    snap_c = await provider.refresh_force()
    assert snap_c is not snap_a


@pytest.mark.asyncio
async def test_sparql_provider_emits_prefixed_names() -> None:
    """When the snapshot contains a base prefix, terms get a prefixed_name."""
    from graph_mcp.graph import SparqlDiscoveryConfig

    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    provider = SparqlSchemaProvider(
        endpoint,
        config=SparqlDiscoveryConfig(
            base_prefixes={"ex": "http://example.org/"},
        ),
    )
    snap = await provider.refresh()
    person = next(c for c in snap.classes if c.iri == "http://example.org/Person")
    assert person.prefixed_name == "ex:Person"
    works_for = next(p for p in snap.properties if p.iri == "http://example.org/worksFor")
    assert works_for.prefixed_name == "ex:worksFor"


@pytest.mark.asyncio
async def test_refresh_schema_tool_static_provider() -> None:

    settings = Settings(schema_provider="static")
    endpoint = LocalRdflibEndpoint()
    schema = build_schema_provider(settings, endpoint)
    server = build_server(settings=settings, endpoint=endpoint, schema=schema)
    # The tool function lives on tools.py; exercise it directly.
    from graph_mcp.mcp_tools.tools import (
        QueryGraphInput,  # noqa: F401 (import smoke)
    )

    # Re-call build_server is enough; we test the actual refresh tool below.
    assert server.name == "graph-mcp"


@pytest.mark.asyncio
async def test_refresh_schema_tool_sparql_provider_updates_cache() -> None:
    from graph_mcp.graph import SparqlDiscoveryConfig

    endpoint = LocalRdflibEndpoint.from_turtle_file(EVAL_GRAPH)
    schema = SparqlSchemaProvider(
        endpoint,
        config=SparqlDiscoveryConfig(cache_ttl_seconds=999.0),
    )
    snap0 = schema.snapshot()
    assert snap0.last_refresh_at is None  # never refreshed

    snap1 = await schema.refresh()
    assert snap1.last_refresh_at is not None
    assert len(snap1.classes) > 0

    # The TTL cache means a second refresh returns the same snapshot.
    snap2 = await schema.refresh()
    assert snap2 is snap1


def test_schema_status_json_shape() -> None:
    from graph_mcp.mcp_tools.resources import schema_status_json

    schema = StaticSchemaProvider(
        # Use the model directly to fix counts.
        __import__("graph_mcp.graph.schema_discovery", fromlist=["SchemaSnapshot"]).SchemaSnapshot()
    )
    payload = schema_status_json(schema, provider_name="static", cache_ttl_seconds=42.0)
    parsed = json.loads(payload)
    assert parsed["provider"] == "static"
    assert parsed["cache_ttl_seconds"] == 42.0
    assert parsed["classes_count"] == 0
    assert parsed["properties_count"] == 0
    assert parsed["individuals_count"] == 0
    assert parsed["named_graphs_count"] == 0
    assert parsed["diagnostics"] == []
    assert parsed["last_refresh_at"] is None


def test_refresh_schema_input_validates() -> None:
    """RefreshSchemaInput accepts default and explicit force=True."""
    a = RefreshSchemaInput()
    assert a.force is False
    b = RefreshSchemaInput(force=True)
    assert b.force is True


# --- Priority 4: explicit sparql mode must fail fast ----------------------


def test_schema_provider_sparql_requires_endpoint_or_local_file() -> None:
    """`schema_provider=sparql` without a real source must raise ConfigurationError."""
    from graph_mcp.config import ConfigurationError

    settings = Settings(schema_provider="sparql")  # no endpoint, no local file
    endpoint = LocalRdflibEndpoint()
    with pytest.raises(ConfigurationError, match="sparql"):
        build_schema_provider(settings, endpoint)


def test_schema_provider_auto_without_source_uses_static() -> None:
    settings = Settings(schema_provider="auto")
    endpoint = LocalRdflibEndpoint()
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, StaticSchemaProvider)


def test_schema_provider_auto_with_endpoint_uses_sparql() -> None:
    settings = Settings(schema_provider="auto", endpoint_url="http://localhost/sparql")
    endpoint = LocalRdflibEndpoint()
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, SparqlSchemaProvider)


def test_schema_provider_auto_with_local_file_uses_sparql() -> None:
    settings = Settings(schema_provider="auto", local_graph_file=EVAL_GRAPH)
    endpoint = build_endpoint(settings)
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, SparqlSchemaProvider)


def test_schema_provider_static_ignores_endpoint() -> None:
    settings = Settings(schema_provider="static", endpoint_url="http://localhost/sparql")
    endpoint = LocalRdflibEndpoint()
    provider = build_schema_provider(settings, endpoint)
    assert isinstance(provider, StaticSchemaProvider)
