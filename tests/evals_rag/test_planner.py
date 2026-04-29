"""Tests for the RAG planner.

The tests build a real :class:`PlannerDeps` (validator, renderer, resolver,
schema provider, policy) using the local sample graph so the candidate
pack reflects the live schema. The LLM is stubbed by a Python callable
that records the prompt and returns a hand-built ``PlannedOutput`` /
``ClarificationOutput`` / ``RefusedOutput``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evals.agent import PlannerDeps
from evals.models import (
    ClarificationOutput,
    PlannedOutput,
    RefusedOutput,
)
from evals.runner import build_components
from evals_rag.config import RagSettings
from evals_rag.fixtures import concepts_from_snapshot
from evals_rag.planner import RagPlannerConfig, build_rag_planner
from evals_rag.reranking import HeuristicReranker, NoopReranker
from evals_rag.retrieval import MockOntologyRetriever
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

_GRAPH = Path(__file__).resolve().parent.parent.parent / "evals" / "sample_dataset.trig"


@pytest.fixture
def components():
    return asyncio.run(build_components(graph_path=_GRAPH))


@pytest.fixture
def deps(components):
    return PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )


def _planned_for_acme(question: str) -> PlannedOutput:
    plan = SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="person"))],
        where=[
            TriplePattern(
                subject=Var(name="person"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=PrefixedName(prefix="ex", local="Acme"),
            )
        ],
        limit=50,
    )
    return PlannedOutput(question=question, plan=plan, confidence=0.95)


def test_rag_planner_injects_candidate_pack(components, deps):
    captured: list[str] = []

    def generate(prompt: str):
        captured.append(prompt)
        return _planned_for_acme("Who works for Acme?")

    retriever = MockOntologyRetriever(concepts_from_snapshot(deps.schema.snapshot()))
    planner = build_rag_planner(
        deps,
        retriever=retriever,
        reranker=HeuristicReranker(),
        generate=generate,
        config=RagPlannerConfig(settings=RagSettings()),
    )
    out = planner.plan("Who works for Acme?")
    assert isinstance(out, PlannedOutput)
    assert captured, "expected the LLM stub to be called"
    rendered = captured[0]
    assert "Retrieved ontology candidates" in rendered
    assert "ex:worksFor" in rendered or "worksFor" in rendered
    rag_diag = planner.last_rag_diagnostics
    assert rag_diag is not None
    assert rag_diag.selected_concepts, "expected at least one selected concept"


def test_rag_planner_respects_refusal(components, deps):
    def generate(prompt: str):
        return RefusedOutput(
            question="DROP TABLE people",
            confidence=0.0,
            refusal_reason="destructive",
            policy_code="unsafe_destructive_request",
        )

    retriever = MockOntologyRetriever(concepts_from_snapshot(deps.schema.snapshot()))
    planner = build_rag_planner(
        deps,
        retriever=retriever,
        reranker=NoopReranker(),
        generate=generate,
        config=RagPlannerConfig(settings=RagSettings()),
    )
    out = planner.plan("DROP TABLE people")
    assert isinstance(out, RefusedOutput)
    # Even on refusal the RAG cycle should have produced diagnostics.
    assert planner.last_rag_diagnostics is not None


def test_rag_planner_clarifies_when_llm_clarifies(components, deps):
    def generate(prompt: str):
        return ClarificationOutput(
            question="ambiguous?",
            confidence=0.2,
            clarification_question="Which entity did you mean?",
        )

    retriever = MockOntologyRetriever(concepts_from_snapshot(deps.schema.snapshot()))
    planner = build_rag_planner(
        deps,
        retriever=retriever,
        reranker=NoopReranker(),
        generate=generate,
        config=RagPlannerConfig(settings=RagSettings()),
    )
    out = planner.plan("ambiguous Term")
    assert isinstance(out, ClarificationOutput)


def test_rag_planner_produces_valid_plan_with_mock_retrieval(components, deps):
    def generate(prompt: str):
        return _planned_for_acme("Who works for Acme?")

    retriever = MockOntologyRetriever(concepts_from_snapshot(deps.schema.snapshot()))
    planner = build_rag_planner(
        deps,
        retriever=retriever,
        reranker=HeuristicReranker(),
        generate=generate,
        config=RagPlannerConfig(settings=RagSettings()),
    )
    out = planner.plan("Who works for Acme?")
    assert isinstance(out, PlannedOutput)
    res = components.validator.validate(out.plan)
    assert res.ok, f"validator complained: {[e.code for e in res.errors]}"
    rendered = components.renderer.render(out.plan)
    assert "ex:worksFor" in rendered.sparql
