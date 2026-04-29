"""Tests for the live-SPARQL graph-source path.

Covers:

- :class:`HttpSparqlEndpoint` accepts optional Basic Auth and threads it
  through to ``httpx``.
- :func:`evals.runner.build_components` can build component bundles from
  a local fixture, an injected fake endpoint, and a SPARQL endpoint URL.
- The eval runner's CLI parser accepts the new ``--graph-source`` /
  ``--endpoint-url`` flags and resolves them via env-var fallbacks.
- The ocean prefix table does not leak into the validator's protected
  default-prefix override list.

No live Fuseki server is required for any of these tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.runner import build_components
from evals_rag.runner import _build_parser, _resolve_graph_source
from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.graph.endpoint import GraphEndpoint, HttpSparqlEndpoint
from graph_mcp.models import (
    DEFAULT_PREFIXES,
    OCEAN_KG_PREFIXES,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.models.results import (
    BindingValue,
    QueryExecutionMetadata,
    SelectResult,
    SolutionRow,
)
from graph_mcp.security.policy import SecurityPolicy

_GRAPH = Path(__file__).resolve().parent.parent.parent / "evals" / "sample_dataset.trig"


# --- HttpSparqlEndpoint auth ---------------------------------------------


def test_http_endpoint_threads_auth_to_httpx():
    """Auth tuple supplied to the constructor lands on the underlying client."""
    endpoint = HttpSparqlEndpoint("http://example.org/sparql", auth=("alice", "secret"))
    auth = endpoint._client.auth
    assert isinstance(auth, httpx.BasicAuth)
    # The stored ``_auth`` is the original tuple; useful for tests / reports.
    assert endpoint._auth == ("alice", "secret")


def test_http_endpoint_owned_client_can_be_closed():
    endpoint = HttpSparqlEndpoint("http://example.org/sparql", auth=("alice", "secret"))
    asyncio.run(endpoint.aclose())


def test_http_endpoint_caller_supplied_client_overrides_auth():
    """When the caller supplies a client, our ``auth`` argument is ignored."""
    client = httpx.AsyncClient()
    endpoint = HttpSparqlEndpoint(
        "http://example.org/sparql",
        client=client,
        auth=("alice", "secret"),
    )
    assert endpoint._auth is None
    assert endpoint._client is client
    asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_http_endpoint_post_sends_basic_auth_header():
    """End-to-end: a POSTed SELECT carries an Authorization header."""

    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "head": {"vars": ["s"]},
                "results": {"bindings": []},
            },
            headers={"content-type": "application/sparql-results+json"},
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, auth=("admin", "pw"))
    endpoint = HttpSparqlEndpoint("http://example.org/sparql", client=client)
    await endpoint.query(
        "SELECT * WHERE { ?s ?p ?o }", query_type="select", timeout_ms=5000, max_rows=10
    )
    auth_header = captured["headers"].get("authorization", "")
    # Basic Y2RtaW46cHc=  → "admin:pw"
    assert auth_header.lower().startswith("basic ")
    await client.aclose()


# --- build_components for both graph sources ------------------------------


def test_build_components_local_path_works():
    components = asyncio.run(build_components(graph_path=_GRAPH))
    snap = components.schema_provider.snapshot()
    assert snap.classes, "expected at least one class from the local fixture"
    assert components.endpoint is not None


def test_build_components_requires_exactly_one_source():
    with pytest.raises(ValueError):
        asyncio.run(build_components())
    with pytest.raises(ValueError):
        asyncio.run(
            build_components(
                graph_path=_GRAPH,
                endpoint_url="http://example.org/sparql",
            )
        )


def test_build_components_with_fake_endpoint():
    """Inject a fake :class:`GraphEndpoint` so build_components stays offline."""

    class _FakeEndpoint:
        url = "fake://x"

        async def query(
            self, sparql: str, *, query_type: str, timeout_ms: int, max_rows: int
        ) -> SelectResult:
            return SelectResult(
                variables=[],
                rows=[],
                metadata=QueryExecutionMetadata(duration_ms=0.0, endpoint=self.url),
            )

        async def aclose(self) -> None:
            return None

    fake = _FakeEndpoint()
    components = asyncio.run(build_components(endpoint=fake))
    assert components.endpoint is fake
    # Schema discovery returns empty but the bundle is still well-formed.
    assert components.schema_provider.snapshot() is not None


@pytest.mark.asyncio
async def test_build_components_endpoint_url_constructs_http_endpoint(monkeypatch):
    """When given a URL, build_components instantiates HttpSparqlEndpoint."""

    def _fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"head": {"vars": []}, "results": {"bindings": []}},
            headers={"content-type": "application/sparql-results+json"},
        )

    # Swap httpx.AsyncClient to use our MockTransport so no real socket is opened.
    real_async_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_fake_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    components = await build_components(
        endpoint_url="http://example.org/sparql",
        auth=("u", "p"),
        extra_prefixes={"dcat": "http://www.w3.org/ns/dcat#"},
    )
    assert isinstance(components.endpoint, HttpSparqlEndpoint)
    assert components.endpoint.url == "http://example.org/sparql"
    await components.endpoint.aclose()


# --- CLI parser flags -----------------------------------------------------


def test_cli_parser_accepts_graph_source_local():
    parser = _build_parser()
    args = parser.parse_args(["--graph-source", "local", "--graph", str(_GRAPH)])
    src = _resolve_graph_source(args)
    assert src.kind == "local"
    assert src.graph_path == _GRAPH


def test_cli_parser_accepts_graph_source_sparql():
    parser = _build_parser()
    args = parser.parse_args(
        ["--graph-source", "sparql", "--endpoint-url", "http://example.org/sparql"]
    )
    src = _resolve_graph_source(args)
    assert src.kind == "sparql"
    assert src.endpoint_url == "http://example.org/sparql"
    # Ocean prefixes are advertised to schema discovery for sparql sources.
    assert src.extra_prefixes is not None and "dcat" in src.extra_prefixes


def test_cli_parser_uses_env_for_endpoint_url(monkeypatch):
    parser = _build_parser()
    args = parser.parse_args(["--graph-source", "sparql"])
    monkeypatch.setenv("GRAPH_MCP_ENDPOINT_URL", "http://envhost:3030/x/sparql")
    src = _resolve_graph_source(args)
    assert src.endpoint_url == "http://envhost:3030/x/sparql"


def test_cli_parser_sparql_without_endpoint_fails(monkeypatch):
    parser = _build_parser()
    args = parser.parse_args(["--graph-source", "sparql"])
    monkeypatch.delenv("GRAPH_MCP_ENDPOINT_URL", raising=False)
    from evals_rag.runner import _GraphSourceError

    with pytest.raises(_GraphSourceError):
        _resolve_graph_source(args)


def test_cli_parser_user_without_password_env_fails(monkeypatch):
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--graph-source",
            "sparql",
            "--endpoint-url",
            "http://example.org/sparql",
            "--endpoint-user",
            "admin",
            "--endpoint-password-env",
            "TEST_PW_NOT_SET",
        ]
    )
    monkeypatch.delenv("TEST_PW_NOT_SET", raising=False)
    from evals_rag.runner import _GraphSourceError

    with pytest.raises(_GraphSourceError):
        _resolve_graph_source(args)


def test_cli_parser_password_env_resolves(monkeypatch):
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--graph-source",
            "sparql",
            "--endpoint-url",
            "http://example.org/sparql",
            "--endpoint-user",
            "admin",
            "--endpoint-password-env",
            "TEST_FAKE_PW",
        ]
    )
    monkeypatch.setenv("TEST_FAKE_PW", "shh")
    src = _resolve_graph_source(args)
    assert src.auth == ("admin", "shh")


# --- Default-prefix protection regression --------------------------------


def test_ocean_prefixes_are_not_in_default_prefix_table():
    """Adding ocean prefixes must not extend DEFAULT_PREFIXES (default-prefix
    override protection should still flag user redefinitions of dcat etc.).
    """
    for key in OCEAN_KG_PREFIXES:
        assert key not in DEFAULT_PREFIXES, (
            f"{key} must not leak into DEFAULT_PREFIXES; that would change "
            "the validator's protected-prefix list"
        )


def test_validator_still_rejects_protected_prefix_override():
    """Sanity-check: the validator still flags an attempt to redefine xsd."""
    settings = Settings()
    policy = SecurityPolicy.from_settings(settings)
    validator = QueryPlanValidator(policy)
    plan = SelectPlan(
        prefixes=[
            Prefix(prefix="ex", iri="http://example.org/"),
            Prefix(prefix="xsd", iri="http://attacker.example/xsd#"),
        ],
        projection=[Projection(var=Var(name="x"))],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=PrefixedName(prefix="ex", local="knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    assert "default_prefix_override" in {issue.code for issue in res.errors}


def test_validator_accepts_dcat_prefix_in_plan():
    """The plan layer must remain free to declare `dcat:` itself; only the
    protected default prefixes are enforced.
    """
    settings = Settings()
    policy = SecurityPolicy.from_settings(settings)
    validator = QueryPlanValidator(policy)
    plan = SelectPlan(
        prefixes=[
            Prefix(prefix="dcat", iri="http://www.w3.org/ns/dcat#"),
        ],
        projection=[Projection(var=Var(name="d"))],
        where=[
            TriplePattern(
                subject=Var(name="d"),
                predicate=PrefixedName(prefix="rdf", local="type"),
                object=PrefixedName(prefix="dcat", local="Dataset"),
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, [issue.model_dump() for issue in res.errors]


# Silence linter: BindingValue / SolutionRow are imported for typing parity
# with other tests that mock SelectResult.
_ = BindingValue
_ = SolutionRow
_ = GraphEndpoint
