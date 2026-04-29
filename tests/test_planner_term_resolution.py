"""Tests for term-resolution wiring inside the planner workflow (§3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.agent import (
    PlannerDeps,
    _format_resolved_terms_block,
    _resolve_question_terms,
    build_planner_from_callable,
    run_planner_workflow,
)
from evals.models import (
    ClarificationOutput,
    PlannedOutput,
)
from evals.runner import build_components
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph.schema_discovery import (
    SchemaSnapshot,
    StaticSchemaProvider,
)
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

_GRAPH = Path(__file__).parent.parent / "evals" / "sample_graph.ttl"

EX = Prefix(prefix="ex", iri="http://example.org/")


@pytest.mark.asyncio
async def test_resolver_finds_acme_and_works_for() -> None:
    components = await build_components(graph_path=_GRAPH)
    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )
    _, _, selected, _ = _resolve_question_terms(deps, "Who works for Acme?")
    iris = {c.iri for c in selected}
    assert "http://example.org/worksFor" in iris
    assert "http://example.org/Acme" in iris


def test_planner_prompt_contains_resolved_candidates() -> None:
    block = _format_resolved_terms_block(
        selected=[
            _term("works for", "http://example.org/worksFor", "ex:worksFor", "property"),
            _term("Acme", "http://example.org/Acme", "ex:Acme", "individual"),
        ],
        unresolved=[],
    )
    assert "ex:worksFor" in block
    assert "ex:Acme" in block
    assert "Resolved candidates" in block


@pytest.mark.asyncio
async def test_planner_prompt_warns_about_unresolved_mentions() -> None:
    """When a mention has no schema match, the prompt must list it as
    unresolved so the LLM knows to ask for clarification."""
    schema = StaticSchemaProvider(SchemaSnapshot(prefixes={"ex": "http://example.org/"}))
    settings = Settings()
    policy = SecurityPolicy.from_settings(settings)
    deps = PlannerDeps(
        schema=schema,
        resolver=TermResolver(schema),
        validator=QueryPlanValidator(policy),
        renderer=SparqlRenderer(policy),
        policy=policy,
    )
    captured: dict[str, str] = {}

    def gen(prompt: str) -> ClarificationOutput:
        captured["prompt"] = prompt
        return ClarificationOutput(question="?", confidence=0.1, clarification_question="Which?")

    out, diag = run_planner_workflow(deps, "Show me information about Aurora.", generate=gen)
    assert isinstance(out, ClarificationOutput)
    # The mention extractor should have produced "Aurora" and the resolver
    # should have failed to map it.
    assert "Aurora" in diag.unresolved_mentions
    assert "unresolved" in captured["prompt"].lower()


@pytest.mark.asyncio
async def test_invented_terms_are_rejected_or_repaired() -> None:
    """Plans referencing an undeclared prefix must surface validation errors."""
    components = await build_components(graph_path=_GRAPH)
    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
        max_repair_attempts=1,
    )
    invented = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="unknown", local="thing"),
                object=Var(name="q"),
            )
        ],
    )

    def gen(prompt: str) -> PlannedOutput:
        return PlannedOutput(question="?", plan=invented, confidence=0.5)

    planner = build_planner_from_callable(deps, gen)
    planner.plan("?")
    diag = planner.last_diagnostics
    assert diag is not None
    assert diag.final_validation_ok is False
    codes = {e.code for e in diag.validation_errors_seen}
    assert "unknown_prefix" in codes


def _term(mention: str, iri: str, prefixed: str, kind: str):  # type: ignore[no-untyped-def]
    from graph_mcp.graph.term_resolver import TermCandidate

    return TermCandidate(
        mention=mention,
        iri=iri,
        prefixed_name=prefixed,
        kind=kind,  # type: ignore[arg-type]
        label=mention,
        score=1.0,
        explanation="test",
    )


@pytest.mark.asyncio
async def test_resolved_terms_block_handles_empty_state() -> None:
    """The block must be a stable string even when there are no mentions."""
    block = _format_resolved_terms_block(selected=[], unresolved=[])
    assert "no terms" in block.lower()
