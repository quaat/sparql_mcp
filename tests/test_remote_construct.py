"""Tests for HttpSparqlEndpoint CONSTRUCT response handling.

We use httpx's ``MockTransport`` to inject canned responses without hitting
the network.
"""

from __future__ import annotations

import httpx
import pytest

from graph_mcp.graph import EndpointError, HttpSparqlEndpoint
from graph_mcp.models import ConstructResult


def _client_with_response(*, status: int, body: str, content_type: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"content-type": content_type},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_remote_construct_parses_turtle() -> None:
    body = """
    @prefix ex: <http://example.org/> .
    ex:alice ex:knows ex:bob .
    ex:bob   ex:knows ex:carol .
    """
    client = _client_with_response(status=200, body=body, content_type="text/turtle; charset=utf-8")
    ep = HttpSparqlEndpoint("http://example.org/sparql", client=client)
    try:
        result = await ep.query(
            "CONSTRUCT WHERE { ?s ?p ?o }",
            query_type="construct",
            timeout_ms=1000,
            max_rows=1000,
        )
    finally:
        await ep.aclose()
    assert isinstance(result, ConstructResult)
    assert len(result.triples) == 2
    assert result.metadata.row_count == 2


@pytest.mark.asyncio
async def test_remote_construct_parses_ntriples() -> None:
    body = "<http://example.org/alice> <http://example.org/knows> <http://example.org/bob> .\n"
    client = _client_with_response(status=200, body=body, content_type="application/n-triples")
    ep = HttpSparqlEndpoint("http://example.org/sparql", client=client)
    try:
        result = await ep.query(
            "CONSTRUCT WHERE { ?s ?p ?o }",
            query_type="construct",
            timeout_ms=1000,
            max_rows=1000,
        )
    finally:
        await ep.aclose()
    assert isinstance(result, ConstructResult)
    assert len(result.triples) == 1


@pytest.mark.asyncio
async def test_remote_construct_unknown_content_type_raises() -> None:
    client = _client_with_response(
        status=200, body="garbage", content_type="application/octet-stream"
    )
    ep = HttpSparqlEndpoint("http://example.org/sparql", client=client)
    try:
        with pytest.raises(EndpointError, match="content-type"):
            await ep.query(
                "CONSTRUCT WHERE { ?s ?p ?o }",
                query_type="construct",
                timeout_ms=1000,
                max_rows=1000,
            )
    finally:
        await ep.aclose()


@pytest.mark.asyncio
async def test_remote_construct_does_not_silently_return_empty() -> None:
    """A regression guard: empty body should *not* yield an empty result."""
    client = _client_with_response(status=200, body="", content_type="text/turtle")
    ep = HttpSparqlEndpoint("http://example.org/sparql", client=client)
    try:
        result = await ep.query(
            "CONSTRUCT WHERE { ?s ?p ?o }",
            query_type="construct",
            timeout_ms=1000,
            max_rows=1000,
        )
    finally:
        await ep.aclose()
    # Empty turtle is a valid empty graph (rdflib accepts it). Assert that
    # the row count reflects what we actually parsed (zero) rather than the
    # old hardcoded empty list.
    assert isinstance(result, ConstructResult)
    assert result.metadata.row_count == 0
