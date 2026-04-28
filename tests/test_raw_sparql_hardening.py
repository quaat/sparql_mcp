"""Hardened raw-SPARQL safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.mcp_tools.tools import (
    RawSparqlInput,
    _infer_query_type,
    _reject_unsafe_raw,
    tool_execute_sparql_raw,
)
from graph_mcp.security.policy import SecurityPolicy

FIXTURE = Path(__file__).parent / "fixtures" / "sample_graph.ttl"


def _enabled_policy(*, allowed_services: str = "") -> SecurityPolicy:
    s = Settings(
        enable_raw_sparql=True,
        allowed_service_endpoints=allowed_services,  # type: ignore[arg-type]
    )
    return SecurityPolicy.from_settings(s)


def test_raw_insert_newline_is_rejected() -> None:
    policy = _enabled_policy()
    with pytest.raises(PermissionError, match="INSERT"):
        _reject_unsafe_raw("INSERT\nDATA { <http://x/a> <http://x/b> <http://x/c> }", policy)


def test_raw_delete_where_is_rejected() -> None:
    policy = _enabled_policy()
    with pytest.raises(PermissionError, match="DELETE"):
        _reject_unsafe_raw("DELETE WHERE { ?s ?p ?o }", policy)


def test_raw_describe_is_rejected() -> None:
    policy = _enabled_policy()
    with pytest.raises(PermissionError, match="DESCRIBE"):
        _reject_unsafe_raw("DESCRIBE <http://example.org/x>", policy)


def test_raw_keyword_inside_string_literal_does_not_false_positive() -> None:
    policy = _enabled_policy()
    sparql = 'SELECT ?p WHERE { ?p <http://example.org/note> "I will INSERT later"@en }'
    # Must not raise: INSERT is inside a string literal.
    _reject_unsafe_raw(sparql, policy)


def test_raw_keyword_inside_comment_does_not_false_positive() -> None:
    policy = _enabled_policy()
    sparql = "SELECT ?p WHERE { ?p ?q ?o } # DROP this comment\n"
    _reject_unsafe_raw(sparql, policy)


def test_raw_service_endpoint_must_match_allowlist() -> None:
    policy = _enabled_policy(allowed_services="http://allowed.example/sparql")

    # Allowed:
    sparql_ok = "SELECT ?s WHERE { SERVICE <http://allowed.example/sparql> { ?s ?p ?o } }"
    _reject_unsafe_raw(sparql_ok, policy)

    # Not allowed:
    sparql_bad = "SELECT ?s WHERE { SERVICE <http://other.example/sparql> { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="SERVICE"):
        _reject_unsafe_raw(sparql_bad, policy)


def test_raw_service_with_variable_is_rejected() -> None:
    policy = _enabled_policy(allowed_services="http://x/")
    sparql = "SELECT ?s WHERE { SERVICE ?endpoint { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="variable"):
        _reject_unsafe_raw(sparql, policy)


def test_raw_service_with_prefixed_name_is_rejected() -> None:
    policy = _enabled_policy(allowed_services="http://x/")
    sparql = "SELECT ?s WHERE { SERVICE ex:remote { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="prefixed"):
        _reject_unsafe_raw(sparql, policy)


def test_compat_reject_unsafe_raw_does_not_treat_hash_inside_iri_as_comment() -> None:
    """Compatibility wrapper inherits the scanner's IRI-hash safety semantics.

    The legacy string-based pre-processor would have treated ``#`` inside
    ``<http://example.org/#frag>`` as the start of a comment, hiding any
    keywords after the IRI on the same line. The scanner-based wrapper used
    today must not.
    """
    policy = _enabled_policy()
    # An IRI with a fragment, on the same line as a closing brace and a
    # legitimate query keyword. If `#` were misinterpreted as a comment,
    # the closing brace and `LIMIT 5` would vanish and the query would parse
    # incorrectly.
    sparql = "SELECT ?s WHERE { ?s ?p <http://example.org/#frag> } LIMIT 5"
    # No raise: the IRI's `#` is not a comment marker.
    _reject_unsafe_raw(sparql, policy)


def test_compat_infer_query_type_does_not_treat_hash_inside_iri_as_comment() -> None:
    sparql = "SELECT ?s WHERE { ?s ?p <http://example.org/#frag> }"
    assert _infer_query_type(sparql) == "select"


def test_infer_query_type_select() -> None:
    assert _infer_query_type("SELECT ?x WHERE { ?x ?y ?z }") == "select"


def test_infer_query_type_ask() -> None:
    assert _infer_query_type("ASK { <http://x/a> <http://x/b> <http://x/c> }") == "ask"


def test_infer_query_type_construct() -> None:
    sparql = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
    assert _infer_query_type(sparql) == "construct"


def test_infer_query_type_describe_rejected() -> None:
    with pytest.raises(PermissionError):
        _infer_query_type("DESCRIBE <http://example.org/x>")


@pytest.mark.asyncio
async def test_raw_expected_query_type_mismatch_is_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="ASK WHERE { ?s ?p ?o }",
        expected_query_type="select",  # mismatch
    )
    with pytest.raises(PermissionError, match="does not match"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_select_executes_when_correct() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
        expected_query_type="select",
    )
    out = await tool_execute_sparql_raw(inp, endpoint, policy)
    assert out.raw_mode is True
    assert out.result.kind == "select"


# --- Priority 3: raw LIMIT validation -------------------------------------


@pytest.mark.asyncio
async def test_raw_negative_limit_is_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT * WHERE { ?s ?p ?o } LIMIT -1",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_decimal_limit_is_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT * WHERE { ?s ?p ?o } LIMIT 10.5",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_plus_signed_limit_is_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT * WHERE { ?s ?p ?o } LIMIT +5",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_multiple_top_level_limits_are_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT * WHERE { ?s ?p ?o } LIMIT 5 LIMIT 10",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_limit_inside_subquery_does_not_satisfy_top_level_requirement() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        # LIMIT 5 is inside the subquery's braces, not at top level.
        sparql="SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } LIMIT 5 } }",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="top-level LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_zero_limit_is_allowed() -> None:
    """LIMIT 0 is a safe, debug-friendly construct and is permitted."""
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o } LIMIT 0",
        expected_query_type="select",
    )
    out = await tool_execute_sparql_raw(inp, endpoint, policy)
    assert out.raw_mode is True


@pytest.mark.asyncio
async def test_raw_positive_limit_within_cap_is_allowed() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o } LIMIT 3",
        expected_query_type="select",
        max_rows=10,
    )
    out = await tool_execute_sparql_raw(inp, endpoint, policy)
    assert out.raw_mode is True


@pytest.mark.asyncio
async def test_raw_limit_above_cap_is_rejected() -> None:
    policy = _enabled_policy()
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o } LIMIT 9999",
        expected_query_type="select",
        max_rows=10,
    )
    with pytest.raises(PermissionError, match="exceeds"):
        await tool_execute_sparql_raw(inp, endpoint, policy)
