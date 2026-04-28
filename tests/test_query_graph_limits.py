"""Tests for pre-execution row-limit enforcement in ``query_graph``."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.mcp_tools.tools import (
    QueryGraphInput,
    tool_query_graph,
)
from graph_mcp.models import (
    ConstructPlan,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")
FIXTURE = Path(__file__).parent / "fixtures" / "sample_graph.ttl"


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _basic_select(limit: int | None = None) -> SelectPlan:
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=_ex("Acme"),
            )
        ],
        limit=limit,
    )


@pytest.mark.asyncio
async def test_query_graph_max_rows_caps_rendered_top_level_limit() -> None:
    """A request-level max_rows must reach into the rendered SPARQL LIMIT."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_select(), max_rows=3),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.rendered is not None
    assert "LIMIT 3" in out.rendered.sparql
    assert "LIMIT 100" not in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_dry_run_shows_effective_limit() -> None:
    """dry_run must echo the effective limit in the rendered SPARQL."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_select(), max_rows=10, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.dry_run
    assert out.result is None
    assert out.rendered is not None
    assert "LIMIT 10" in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_preserves_smaller_existing_limit() -> None:
    """A plan that asks for fewer rows than max_rows keeps its smaller LIMIT."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_select(limit=2), max_rows=100, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.rendered is not None
    assert "LIMIT 2" in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_caps_construct_limit_before_execution() -> None:
    """CONSTRUCT plans also get the request-level cap applied before render."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    plan = ConstructPlan(
        prefixes=[EX],
        template=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=Var(name="c"),
            )
        ],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=Var(name="c"),
            )
        ],
        # No limit — must be set to effective max_rows.
    )

    out = await tool_query_graph(
        QueryGraphInput(plan=plan, max_rows=5, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.rendered is not None
    assert "LIMIT 5" in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_max_rows_above_policy_caps_to_policy() -> None:
    """A request-level max_rows above the policy maximum must be capped."""
    policy = SecurityPolicy.from_settings(Settings(max_limit=50))
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    out = await tool_query_graph(
        QueryGraphInput(plan=_basic_select(), max_rows=10_000, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.rendered is not None
    assert "LIMIT 50" in out.rendered.sparql
    assert "LIMIT 10000" not in out.rendered.sparql


# --- Priority 2 tests: cap before validation -------------------------------


@pytest.mark.asyncio
async def test_query_graph_caps_limit_before_validation() -> None:
    """A plan with LIMIT 9999 + max_rows=10 must validate and render LIMIT 10."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    plan = _basic_select(limit=9999)
    out = await tool_query_graph(
        QueryGraphInput(plan=plan, max_rows=10, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    # Validation should succeed because the cap was applied first.
    assert out.validation.ok, out.validation.issues
    assert out.rendered is not None
    assert "LIMIT 10" in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_limit_above_policy_but_below_request_cap_is_capped() -> None:
    """A plan limit above policy.max_limit can still be capped via max_rows."""
    policy = SecurityPolicy.from_settings(Settings(max_limit=100))
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    plan = _basic_select(limit=500)  # exceeds max_limit=100
    out = await tool_query_graph(
        QueryGraphInput(plan=plan, max_rows=50, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.validation.ok, out.validation.issues
    assert out.rendered is not None
    assert "LIMIT 50" in out.rendered.sparql


@pytest.mark.asyncio
async def test_query_graph_dry_run_shows_capped_limit() -> None:
    """dry_run output reflects the capped LIMIT before any execution."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    plan = _basic_select(limit=9999)
    out = await tool_query_graph(
        QueryGraphInput(plan=plan, max_rows=7, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.dry_run
    assert out.result is None
    assert out.validation.ok, out.validation.issues
    assert out.rendered is not None
    assert "LIMIT 7" in out.rendered.sparql


@pytest.mark.asyncio
async def test_render_sparql_still_rejects_limit_above_policy() -> None:
    """``render_sparql`` validates the user-supplied plan directly — no cap."""
    from graph_mcp.mcp_tools.tools import RenderSparqlInput, tool_render_sparql

    policy = SecurityPolicy.from_settings(Settings(max_limit=100))
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)

    plan = _basic_select(limit=500)
    out = tool_render_sparql(RenderSparqlInput(plan=plan), validator, renderer)
    assert not out.validation.ok
    assert out.rendered is None
    codes = {i.code for i in out.validation.errors}
    assert "limit_too_high" in codes


@pytest.mark.asyncio
async def test_query_graph_preserves_smaller_existing_limit_priority2() -> None:
    """Re-state of test 1: a smaller existing LIMIT survives the cap."""
    policy = SecurityPolicy.from_settings(Settings())
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = LocalRdflibEndpoint.from_turtle_file(FIXTURE)

    plan = _basic_select(limit=2)
    out = await tool_query_graph(
        QueryGraphInput(plan=plan, max_rows=100, dry_run=True),
        validator,
        renderer,
        endpoint,
        policy,
    )
    assert out.validation.ok, out.validation.issues
    assert out.rendered is not None
    assert "LIMIT 2" in out.rendered.sparql
    assert "LIMIT 100" not in out.rendered.sparql
