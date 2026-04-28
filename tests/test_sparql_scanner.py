"""Tests for the token-aware SPARQL scanner used in raw mode.

The scanner must distinguish code from string literals, comments, and IRIs,
so safety analysis is robust against payloads that try to smuggle update
keywords through any of those regions.
"""

from __future__ import annotations

import pytest

from graph_mcp.mcp_tools.sparql_scanner import (
    TokenKind,
    find_top_level_limit,
    infer_query_type,
    reject_unsafe_raw,
    tokenize,
)

# --- Tokenizer correctness -----------------------------------------------


def test_tokenizer_strings_keep_keywords_invisible() -> None:
    src = 'SELECT ?p WHERE { ?p ?q "INSERT DATA" }'
    tokens = tokenize(src)
    # The string token's value includes the quotes and the inner text, but
    # the token kind is STRING — not KEYWORD.
    string_tokens = [t for t in tokens if t.kind is TokenKind.STRING]
    assert len(string_tokens) == 1
    # No KEYWORD token must equal the smuggled INSERT.
    keyword_words = {t.value.upper() for t in tokens if t.kind is TokenKind.KEYWORD}
    assert "INSERT" not in keyword_words


def test_tokenizer_iri_keeps_hash_inside() -> None:
    """The ``#`` inside an IRI must NOT start a comment."""
    src = "SELECT ?s WHERE { ?s ?p <http://example.org/#frag> }"
    tokens = tokenize(src)
    iri_tokens = [t for t in tokens if t.kind is TokenKind.IRI]
    assert len(iri_tokens) == 1
    assert iri_tokens[0].value == "http://example.org/#frag"
    # Tokens after the IRI must include the closing brace, proving we
    # didn't run off the end of the line into a comment.
    closing = [t for t in tokens if t.kind is TokenKind.PUNCT and t.value == "}"]
    assert closing


def test_tokenizer_iri_with_delete_in_fragment_is_not_a_keyword() -> None:
    src = "SELECT ?s WHERE { ?s ?p <http://example.org/#DELETE> }"
    tokens = tokenize(src)
    keyword_words = {t.value.upper() for t in tokens if t.kind is TokenKind.KEYWORD}
    assert "DELETE" not in keyword_words


def test_tokenizer_triple_quoted_string_is_opaque() -> None:
    src = 'SELECT ?p WHERE { ?p ?q """INSERT DATA""" }'
    tokens = tokenize(src)
    keyword_words = {t.value.upper() for t in tokens if t.kind is TokenKind.KEYWORD}
    assert "INSERT" not in keyword_words


def test_tokenizer_unterminated_string_raises() -> None:
    with pytest.raises(PermissionError):
        # Wrap in reject_unsafe_raw so callers always see PermissionError
        reject_unsafe_raw(
            'SELECT ?s WHERE { ?s ?p "unterminated', allowed_service_endpoints=frozenset()
        )


# --- Forbidden update forms ----------------------------------------------


def test_insert_with_newline_separator_is_rejected() -> None:
    src = "INSERT\nDATA { <a> <b> <c> }"
    with pytest.raises(PermissionError, match="INSERT"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_insert_with_tab_separator_is_rejected() -> None:
    src = "INSERT\tDATA { <a> <b> <c> }"
    with pytest.raises(PermissionError, match="INSERT"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_with_delete_is_rejected() -> None:
    src = "WITH <http://x/g> DELETE { ?s ?p ?o } WHERE { ?s ?p ?o }"
    with pytest.raises(PermissionError):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_describe_is_rejected() -> None:
    src = "DESCRIBE ?s WHERE { ?s ?p ?o }"
    with pytest.raises(PermissionError, match="DESCRIBE"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_mixed_case_keywords_are_detected() -> None:
    src = "Insert\ndata { <a> <b> <c> }"
    with pytest.raises(PermissionError, match="INSERT"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


# --- False-positive immunity ---------------------------------------------


def test_keyword_inside_string_does_not_trigger() -> None:
    src = 'SELECT ?p WHERE { ?p ?q "# INSERT DATA" }'
    # No exception.
    reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_iri_with_delete_fragment_does_not_trigger() -> None:
    src = "SELECT ?s WHERE { ?s ?p <http://example.org/#DELETE> }"
    reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_comment_with_keyword_does_not_trigger() -> None:
    src = "SELECT ?s WHERE { ?s ?p ?o } # DROP this comment\n"
    reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


# --- SERVICE handling ----------------------------------------------------


def test_service_with_unallowed_iri_is_rejected() -> None:
    src = "SELECT * WHERE { SERVICE <http://evil.example/sparql#frag> { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="SERVICE"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


def test_service_with_allowed_iri_with_fragment_is_accepted() -> None:
    src = "SELECT * WHERE { SERVICE <http://allowed.example/sparql#frag> { ?s ?p ?o } }"
    reject_unsafe_raw(
        src,
        allowed_service_endpoints=frozenset({"http://allowed.example/sparql#frag"}),
    )


def test_service_silent_with_allowed_iri_is_accepted() -> None:
    src = "SELECT * WHERE { SERVICE SILENT <http://allowed/> { ?s ?p ?o } }"
    reject_unsafe_raw(
        src,
        allowed_service_endpoints=frozenset({"http://allowed/"}),
    )


def test_service_variable_endpoint_is_rejected() -> None:
    src = "SELECT * WHERE { SERVICE ?ep { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="variable"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset({"http://x/"}))


def test_service_prefixed_endpoint_is_rejected() -> None:
    src = "SELECT * WHERE { SERVICE ex:remote { ?s ?p ?o } }"
    with pytest.raises(PermissionError, match="prefixed"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset({"http://x/"}))


def test_service_with_iri_containing_keywords_is_extracted_exactly() -> None:
    """The IRI is treated opaquely; allowlist match must be exact."""
    src = "SELECT * WHERE { SERVICE <http://allowed/INSERT> { ?s ?p ?o } }"
    # Allowlist holds exactly the IRI text:
    reject_unsafe_raw(
        src,
        allowed_service_endpoints=frozenset({"http://allowed/INSERT"}),
    )
    # Without it on the allowlist, must reject.
    with pytest.raises(PermissionError, match="SERVICE"):
        reject_unsafe_raw(src, allowed_service_endpoints=frozenset())


# --- Query-form inference ------------------------------------------------


def test_infer_query_type_select_lowercase() -> None:
    tokens = tokenize("select ?x where { ?x ?y ?z }")
    assert infer_query_type(tokens) == "select"


def test_infer_query_type_ask() -> None:
    tokens = tokenize("ASK { <a> <b> <c> }")
    assert infer_query_type(tokens) == "ask"


def test_infer_query_type_construct() -> None:
    tokens = tokenize("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")
    assert infer_query_type(tokens) == "construct"


def test_infer_query_type_describe_raises() -> None:
    tokens = tokenize("DESCRIBE <a>")
    with pytest.raises(PermissionError):
        infer_query_type(tokens)


# --- Top-level LIMIT detection -------------------------------------------


def test_find_top_level_limit_simple() -> None:
    tokens = tokenize("SELECT ?x WHERE { ?x ?y ?z } LIMIT 25")
    assert find_top_level_limit(tokens) == 25


def test_find_top_level_limit_returns_none_when_only_inside_subquery() -> None:
    tokens = tokenize("SELECT ?x WHERE { { SELECT ?x WHERE { ?x ?y ?z } LIMIT 5 } }")
    assert find_top_level_limit(tokens) is None


def test_find_top_level_limit_picks_the_outer_one() -> None:
    tokens = tokenize("SELECT ?x WHERE { { SELECT ?x WHERE { ?x ?y ?z } LIMIT 5 } } LIMIT 50")
    assert find_top_level_limit(tokens) == 50


def test_find_top_level_limit_after_offset() -> None:
    tokens = tokenize("SELECT ?x WHERE { ?x ?y ?z } OFFSET 5 LIMIT 10")
    assert find_top_level_limit(tokens) == 10


# --- End-to-end via the tool path -----------------------------------------


@pytest.mark.asyncio
async def test_raw_select_without_limit_is_rejected() -> None:
    """Conservative rule: raw SELECT must include an explicit top-level LIMIT."""
    from pathlib import Path

    from graph_mcp.config import Settings
    from graph_mcp.graph import LocalRdflibEndpoint
    from graph_mcp.mcp_tools.tools import RawSparqlInput, tool_execute_sparql_raw
    from graph_mcp.security.policy import SecurityPolicy

    fixture = Path(__file__).parent / "fixtures" / "sample_graph.ttl"
    policy = SecurityPolicy.from_settings(Settings(enable_raw_sparql=True))
    endpoint = LocalRdflibEndpoint.from_turtle_file(fixture)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o }",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="LIMIT"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_select_with_limit_above_max_is_rejected() -> None:
    from pathlib import Path

    from graph_mcp.config import Settings
    from graph_mcp.graph import LocalRdflibEndpoint
    from graph_mcp.mcp_tools.tools import RawSparqlInput, tool_execute_sparql_raw
    from graph_mcp.security.policy import SecurityPolicy

    fixture = Path(__file__).parent / "fixtures" / "sample_graph.ttl"
    policy = SecurityPolicy.from_settings(Settings(enable_raw_sparql=True, max_limit=10))
    endpoint = LocalRdflibEndpoint.from_turtle_file(fixture)
    inp = RawSparqlInput(
        sparql="SELECT ?s WHERE { ?s ?p ?o } LIMIT 100",
        expected_query_type="select",
    )
    with pytest.raises(PermissionError, match="exceeds"):
        await tool_execute_sparql_raw(inp, endpoint, policy)


@pytest.mark.asyncio
async def test_raw_ask_does_not_require_limit() -> None:
    from pathlib import Path

    from graph_mcp.config import Settings
    from graph_mcp.graph import LocalRdflibEndpoint
    from graph_mcp.mcp_tools.tools import RawSparqlInput, tool_execute_sparql_raw
    from graph_mcp.security.policy import SecurityPolicy

    fixture = Path(__file__).parent / "fixtures" / "sample_graph.ttl"
    policy = SecurityPolicy.from_settings(Settings(enable_raw_sparql=True))
    endpoint = LocalRdflibEndpoint.from_turtle_file(fixture)
    inp = RawSparqlInput(
        sparql="ASK WHERE { ?s ?p ?o }",
        expected_query_type="ask",
    )
    out = await tool_execute_sparql_raw(inp, endpoint, policy)
    assert out.raw_mode is True
    assert out.result.kind == "ask"
