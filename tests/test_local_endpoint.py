"""LocalRdflibEndpoint truncation and timeout tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mcp.graph import EndpointError, LocalRdflibEndpoint

FIXTURE = Path(__file__).parent / "fixtures" / "sample_graph.ttl"

# Larger turtle for truncation tests.
_BIG_TTL = """
@prefix ex: <http://example.org/> .
""" + "\n".join(f"ex:item{i} ex:n {i} ." for i in range(50))


@pytest.mark.asyncio
async def test_local_max_rows_returns_at_most_max_rows() -> None:
    ep = LocalRdflibEndpoint.from_turtle_string(_BIG_TTL)
    out = await ep.query(
        "SELECT ?s WHERE { ?s <http://example.org/n> ?n }",
        query_type="select",
        timeout_ms=2000,
        max_rows=10,
    )
    assert out.kind == "select"
    assert len(out.rows) == 10
    assert out.metadata.truncated is True


@pytest.mark.asyncio
async def test_local_result_truncation_only_true_when_extra_row_exists() -> None:
    """If max_rows >= total rows, truncated must be False."""
    ep = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    out = await ep.query(
        "SELECT ?s WHERE { ?s <http://example.org/worksFor> <http://example.org/Acme> }",
        query_type="select",
        timeout_ms=2000,
        max_rows=100,  # well above the actual 2 rows in the fixture
    )
    assert out.kind == "select"
    assert len(out.rows) == 2
    assert out.metadata.truncated is False


@pytest.mark.asyncio
async def test_local_max_rows_exact_match_not_truncated() -> None:
    """Requesting exactly the number of available rows must not flag truncated."""
    ep = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    out = await ep.query(
        "SELECT ?s WHERE { ?s <http://example.org/worksFor> <http://example.org/Acme> }",
        query_type="select",
        timeout_ms=2000,
        max_rows=2,  # exactly the number of rows in the fixture
    )
    assert len(out.rows) == 2
    assert out.metadata.truncated is False


@pytest.mark.asyncio
async def test_local_timeout_raises_on_short_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The timeout path must raise EndpointError when the executor blocks.

    We cannot reliably make rdflib's query engine slow without hitting flaky
    timing, so we monkeypatch the underlying ``graph.query`` to sleep —
    that exercises ``asyncio.wait_for`` deterministically.
    """
    import time as _time

    ep = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    def slow_query(_sparql: str) -> object:
        _time.sleep(2.0)
        raise AssertionError("should have been cancelled by the timeout")

    monkeypatch.setattr(ep._graph, "query", slow_query)

    with pytest.raises(EndpointError, match="timed out"):
        await ep.query(
            "SELECT ?s WHERE { ?s ?p ?o }",
            query_type="select",
            timeout_ms=20,
            max_rows=1,
        )
