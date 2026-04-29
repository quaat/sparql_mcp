"""Live-Fuseki integration tests for the ocean RAG path.

Skipped by default. Enable with one of::

    RUN_FUSEKI_INTEGRATION=1
    GRAPH_MCP_ENDPOINT_URL=http://localhost:3030/ocean/sparql

These tests are deliberately small: they verify that

1. ``HttpSparqlEndpoint`` can hit the live endpoint and return a ``SelectResult``.
2. ``evals.runner.build_components`` runs schema discovery against a
   real Fuseki and exposes the ocean prefixes the planner relies on.

The tests never assume Fuseki exists in CI; they are skipped silently
when neither env var is set. Live LLM behaviour is *not* covered here —
that requires Azure / OpenAI credentials and is gated separately.
"""

from __future__ import annotations

import os

import pytest

from evals.runner import build_components
from graph_mcp.graph.endpoint import HttpSparqlEndpoint
from graph_mcp.models import OCEAN_KG_PREFIXES, SelectResult


def _live_enabled() -> bool:
    return bool(os.environ.get("RUN_FUSEKI_INTEGRATION")) or bool(
        os.environ.get("GRAPH_MCP_ENDPOINT_URL")
    )


def _endpoint_url() -> str:
    return os.environ.get("GRAPH_MCP_ENDPOINT_URL", "http://localhost:3030/ocean/sparql")


def _auth() -> tuple[str, str] | None:
    user = os.environ.get("FUSEKI_ADMIN_USER")
    if not user:
        return None
    pw = os.environ.get("FUSEKI_ADMIN_PASSWORD") or ""
    if not pw:
        return None
    return (user, pw)


pytestmark = pytest.mark.skipif(
    not _live_enabled(),
    reason=(
        "Live Fuseki integration tests require RUN_FUSEKI_INTEGRATION=1 or GRAPH_MCP_ENDPOINT_URL"
    ),
)


@pytest.mark.asyncio
async def test_live_temperature_datasets_returns_rows():
    """The temperature-variable check should always return >= 1 row on the
    canonical ocean dataset. Anything else points at a stale snapshot."""
    sparql = """\
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX var:  <https://example.org/ocean-demo/id/observable-property/>

SELECT DISTINCT ?dataset ?datasetLabel ?variableLabel
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcat:theme ?variable .
  ?variable skos:prefLabel ?variableLabel ;
            skos:broader var:temperature-variable .
}
LIMIT 5
"""
    endpoint = HttpSparqlEndpoint(_endpoint_url(), auth=_auth())
    try:
        result = await endpoint.query(sparql, query_type="select", timeout_ms=15_000, max_rows=10)
    finally:
        await endpoint.aclose()
    assert isinstance(result, SelectResult)
    assert len(result.rows) >= 1, (
        f"expected at least one row from {_endpoint_url()}; got 0 — is the dataset loaded?"
    )


@pytest.mark.asyncio
async def test_live_publishers_returns_rows():
    """A separate independent check so a single broken predicate doesn't
    mask total endpoint breakage."""
    sparql = """\
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?publisherLabel
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcterms:publisher ?publisher .
  ?publisher rdfs:label ?publisherLabel .
}
LIMIT 5
"""
    endpoint = HttpSparqlEndpoint(_endpoint_url(), auth=_auth())
    try:
        result = await endpoint.query(sparql, query_type="select", timeout_ms=15_000, max_rows=10)
    finally:
        await endpoint.aclose()
    assert isinstance(result, SelectResult)
    assert len(result.rows) >= 1


@pytest.mark.asyncio
async def test_live_build_components_discovers_schema_and_ocean_prefixes():
    """build_components against the live endpoint must populate the
    schema snapshot and expose the ocean prefixes."""
    components = await build_components(
        endpoint_url=_endpoint_url(),
        auth=_auth(),
        extra_prefixes=dict(OCEAN_KG_PREFIXES),
    )
    try:
        snap = components.schema_provider.snapshot()
        assert snap.classes or snap.properties, (
            "schema discovery returned empty snapshot — "
            f"endpoint {_endpoint_url()} may not be reachable / loaded"
        )
        # Ocean prefixes must be advertised so the planner can produce
        # `dcat:Dataset` etc. without inventing new prefixes.
        for required in ("dcat", "dcterms", "geo", "prov", "sosa", "app", "var"):
            assert required in snap.prefixes, (
                f"{required!r} prefix missing from snapshot.prefixes — "
                "extra_prefixes plumbing is broken"
            )
    finally:
        await components.endpoint.aclose()
