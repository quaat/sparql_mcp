"""Tests for the MCP tool functions and the FastMCP wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.graph.schema_discovery import (
    ClassTerm,
    PropertyTerm,
    SchemaSnapshot,
    StaticSchemaProvider,
)
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.mcp_tools.resources import (
    policy_security_json,
    query_plan_schema_json,
    schema_classes_json,
    schema_prefixes_json,
)
from graph_mcp.mcp_tools.tools import (
    ExplainQueryPlanInput,
    QueryGraphInput,
    RawSparqlInput,
    RenderSparqlInput,
    ResolveTermsInput,
    ValidateQueryPlanInput,
    tool_execute_sparql_raw,
    tool_explain_query_plan,
    tool_query_graph,
    tool_render_sparql,
    tool_resolve_terms,
    tool_validate_query_plan,
)
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.security import SecurityPolicy
from graph_mcp.server import build_server

EX = Prefix(prefix="ex", iri="http://example.org/")
FIXTURE = Path(__file__).parent / "fixtures" / "sample_graph.ttl"


def _basic_plan() -> SelectPlan:
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=PrefixedName(prefix="ex", local="Acme"),
            )
        ],
    )


def test_validate_tool_accepts_valid_plan(validator: QueryPlanValidator) -> None:
    res = tool_validate_query_plan(ValidateQueryPlanInput(plan=_basic_plan()), validator)
    assert res.ok


def test_render_tool_returns_sparql(
    validator: QueryPlanValidator, renderer: SparqlRenderer
) -> None:
    out = tool_render_sparql(RenderSparqlInput(plan=_basic_plan()), validator, renderer)
    assert out.query_type == "select"
    assert out.sparql.startswith("PREFIX")
    assert "ex:worksFor" in out.sparql


def test_resolve_terms_tool() -> None:
    schema = StaticSchemaProvider(
        SchemaSnapshot(
            classes=[
                ClassTerm(
                    iri="http://example.org/Person",
                    prefixed_name="ex:Person",
                    label="Person",
                )
            ],
            properties=[
                PropertyTerm(
                    iri="http://example.org/worksFor",
                    prefixed_name="ex:worksFor",
                    label="works for",
                )
            ],
        )
    )
    resolver = TermResolver(schema)
    res = tool_resolve_terms(
        ResolveTermsInput(mentions=["Person", "works for"], limit=3), resolver
    )
    iris = {c.iri for c in res.candidates}
    assert "http://example.org/Person" in iris
    assert "http://example.org/worksFor" in iris


@pytest.mark.asyncio
async def test_query_graph_tool_dry_run(
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    policy: SecurityPolicy,
) -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_plan(), dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.dry_run
    assert out.rendered is not None
    assert out.result is None


@pytest.mark.asyncio
async def test_query_graph_tool_executes(
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    policy: SecurityPolicy,
) -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_plan(), max_rows=10),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.result is not None
    assert out.result.kind == "select"
    assert len(out.result.rows) == 2


def test_explain_tool(validator: QueryPlanValidator) -> None:
    out = tool_explain_query_plan(
        ExplainQueryPlanInput(plan=_basic_plan()), validator
    )
    assert out.query_form == "select"
    assert "p" in out.projected_variables


@pytest.mark.asyncio
async def test_raw_sparql_disabled_by_default(policy: SecurityPolicy) -> None:
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    with pytest.raises(PermissionError):
        await tool_execute_sparql_raw(
            RawSparqlInput(sparql="SELECT * WHERE { ?s ?p ?o } LIMIT 1"),
            endpoint,
            policy,
        )


@pytest.mark.asyncio
async def test_raw_sparql_blocks_update_when_enabled() -> None:
    settings = Settings(enable_raw_sparql=True)
    policy = SecurityPolicy.from_settings(settings)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)
    with pytest.raises(PermissionError):
        await tool_execute_sparql_raw(
            RawSparqlInput(sparql="DELETE WHERE { ?s ?p ?o }"),
            endpoint,
            policy,
        )


def test_resources_emit_json() -> None:
    schema = StaticSchemaProvider(SchemaSnapshot(prefixes={"ex": "http://example.org/"}))
    policy = SecurityPolicy.from_settings(Settings())
    assert "ex" in schema_prefixes_json(schema)
    schema_classes_json(schema)  # smoke
    assert "default_limit" in policy_security_json(policy)
    qps = query_plan_schema_json()
    assert "kind" in qps  # discriminator field present


def test_build_server_smoke() -> None:
    s = build_server()
    assert s.name == "graph-mcp"


def test_invalid_plan_rejects_via_pydantic() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidateQueryPlanInput.model_validate(
            {"plan": {"kind": "select", "where": [{"kind": "triple"}]}}
        )
