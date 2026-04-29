"""Tests for the eval runner's semantic-vs-presentation split (§2, §5, §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.agent import Planner
from evals.models import (
    ClarificationOutput,
    GoldenCase,
    GoldenCaseExpected,
    PlannedOutput,
    RefusedOutput,
    TripleSpec,
)
from evals.runner import build_components, run_one
from evals.structural import matches_bindings
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.models import (
    Iri,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

_GRAPH = Path(__file__).parent.parent / "evals" / "sample_graph.ttl"

EX = Prefix(prefix="ex", iri="http://example.org/")


# --- Stub planners --------------------------------------------------------


class _StubPlanner:
    def __init__(self, output: object) -> None:
        self._output = output
        self.last_repair_attempted = False
        self.last_repair_succeeded = False
        self.last_diagnostics = None

    def plan(self, question: str, *, resolver: TermResolver | None = None) -> object:
        return self._output


def _planned_for_acme(*, use_iri: bool = False) -> PlannedOutput:
    pred: PrefixedName | Iri
    obj: PrefixedName | Iri
    if use_iri:
        pred = Iri(value="http://example.org/worksFor")
        obj = Iri(value="http://example.org/Acme")
    else:
        pred = PrefixedName(prefix="ex", local="worksFor")
        obj = PrefixedName(prefix="ex", local="Acme")
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="person"))],
        where=[
            TriplePattern(
                subject=Var(name="person"),
                predicate=pred,
                object=obj,
            )
        ],
        limit=50,
    )
    return PlannedOutput(question="?", plan=plan, confidence=0.95)


# --- §2 / §5: refusal + invalid + clarification handling -----------------


@pytest.mark.asyncio
async def test_unsafe_case_passes_when_planner_refuses() -> None:
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="unsafe",
        question="DROP",
        expected=GoldenCaseExpected(expect_invalid=True, forbidden_features=["raw_sparql"]),
    )
    planner: Planner = _StubPlanner(  # type: ignore[assignment]
        RefusedOutput(question="?", confidence=0.0, refusal_reason="no")
    )
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=False,
    )
    assert result.unsafe_request_rejected is True
    assert not result.failures


@pytest.mark.asyncio
async def test_unsafe_case_fails_when_planner_outputs_valid_query() -> None:
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="unsafe",
        question="DROP",
        expected=GoldenCaseExpected(expect_invalid=True),
    )
    planner: Planner = _StubPlanner(_planned_for_acme())  # type: ignore[assignment]
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=False,
    )
    assert result.unsafe_request_rejected is False
    assert result.failures
    assert any("EXPECTED_REFUSAL" in f for f in result.failures)


@pytest.mark.asyncio
async def test_normal_case_fails_when_planner_refuses() -> None:
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="normal",
        question="Who works for Acme?",
        expected=GoldenCaseExpected(),
    )
    planner: Planner = _StubPlanner(  # type: ignore[assignment]
        RefusedOutput(question="?", confidence=0.0, refusal_reason="bad")
    )
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=False,
    )
    assert result.failures
    assert any("UNEXPECTED_REFUSAL" in f for f in result.failures)


@pytest.mark.asyncio
async def test_clarification_case_passes_with_needs_clarification() -> None:
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="clarif",
        question="?",
        expected=GoldenCaseExpected(expect_clarification=True),
    )
    planner: Planner = _StubPlanner(  # type: ignore[assignment]
        ClarificationOutput(question="?", confidence=0.1, clarification_question="Which?")
    )
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=False,
    )
    assert result.is_clarification_case is True
    assert result.clarification_correct is True
    assert not result.failures


# --- §5: presentation vs semantic ----------------------------------------


@pytest.mark.asyncio
async def test_semantic_case_passes_when_absolute_iri_used_instead_of_prefixed_name() -> None:
    """Using ``<http://example.org/worksFor>`` instead of ``ex:worksFor`` is
    only a presentation difference; the case must still pass."""
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="case_iri",
        question="Who works for Acme?",
        expected=GoldenCaseExpected(
            required_pattern_kinds=["triple"],
            required_triples=[
                TripleSpec(subject="?_", predicate="ex:worksFor", object="ex:Acme"),
            ],
            required_terms=["ex:worksFor"],  # legacy presentation signal
            expected_bindings=[{"person": "ex:alice"}, {"person": "ex:bob"}],
        ),
    )
    planner: Planner = _StubPlanner(_planned_for_acme(use_iri=True))  # type: ignore[assignment]
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=True,
    )
    # ``ex:worksFor`` may not be in the rendered SPARQL because the plan used
    # absolute IRIs — but that's a presentation warning, not a failure.
    assert any("MISSING_TERM" in w for w in result.presentation_warnings) or any(
        "MISSING_TERM" in w for w in result.warnings
    )
    assert not result.failures
    assert result.expected_bindings_present == 2


@pytest.mark.asyncio
async def test_required_terms_can_warn_without_failing_case() -> None:
    components = await build_components(graph_path=_GRAPH)
    case = GoldenCase(
        id="case_warn",
        question="Who works for Acme?",
        expected=GoldenCaseExpected(
            required_pattern_kinds=["triple"],
            required_terms=["ZZZ_NEVER_RENDERED"],
        ),
    )
    planner: Planner = _StubPlanner(_planned_for_acme())  # type: ignore[assignment]
    result = await run_one(
        case,
        planner,
        validator=components.validator,
        renderer=components.renderer,
        endpoint=components.endpoint,
        policy=components.policy,
        execute=False,
    )
    assert not result.failures
    assert any("ZZZ_NEVER_RENDERED" in w for w in result.warnings)


# --- §6: binding matching ------------------------------------------------


def test_matches_bindings_iri_form() -> None:
    rows = [{"p": "http://example.org/alice"}]
    assert matches_bindings(rows, {"p": "ex:alice"}, prefixes={"ex": "http://example.org/"})


def test_matches_bindings_numeric() -> None:
    rows = [{"n": "2"}]
    assert matches_bindings(rows, {"n": "2.0"})
    assert matches_bindings(rows, {"n": "2"})


def test_matches_bindings_typed_literal_strip() -> None:
    rows = [{"d": '"2019-01-01"^^http://www.w3.org/2001/XMLSchema#date'}]
    assert matches_bindings(rows, {"d": "2019-01-01"})


def test_matches_bindings_no_prefix_falls_through() -> None:
    rows = [{"p": "ex:alice"}]
    assert matches_bindings(rows, {"p": "ex:alice"})
