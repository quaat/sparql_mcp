"""Unit tests for the planner workflow with stub generators."""

from __future__ import annotations

from evals.agent import (
    PlannerDeps,
    PlannerDiagnostics,
    build_planner_from_callable,
    run_planner_workflow,
)
from evals.models import PlanGenerationOutput
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import StaticSchemaProvider
from graph_mcp.graph.schema_discovery import SchemaSnapshot
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.models import (
    AskPlan,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _good_output() -> PlanGenerationOutput:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
    )
    return PlanGenerationOutput(question="?", plan=plan, confidence=0.9)


def _bad_output() -> PlanGenerationOutput:
    """A plan whose projection references an unbound variable."""
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="never_bound"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
    )
    return PlanGenerationOutput(question="?", plan=plan, confidence=0.5)


def _clarification_output() -> PlanGenerationOutput:
    plan = AskPlan(prefixes=[EX], where=[])
    return PlanGenerationOutput(
        question="?",
        plan=plan,
        confidence=0.1,
        needs_clarification=True,
        clarification_question="Which entity do you mean?",
    )


def _make_deps(max_repair_attempts: int = 2) -> PlannerDeps:
    settings = Settings()
    policy = SecurityPolicy.from_settings(settings)
    schema = StaticSchemaProvider(SchemaSnapshot())
    resolver = TermResolver(schema)
    return PlannerDeps(
        schema=schema,
        resolver=resolver,
        validator=QueryPlanValidator(policy),
        renderer=SparqlRenderer(policy),
        policy=policy,
        max_repair_attempts=max_repair_attempts,
    )


# --- Workflow scenarios --------------------------------------------------


def test_workflow_invalid_first_then_valid_repair() -> None:
    deps = _make_deps()
    calls: list[str] = []
    sequence = [_bad_output(), _good_output()]

    def generate(question: str) -> PlanGenerationOutput:
        calls.append(question)
        return sequence.pop(0)

    _, diag = run_planner_workflow(deps, "Who knows whom?", generate=generate)
    assert diag.final_validation_ok is True
    assert diag.repair_attempts == 1
    assert diag.rendered_sparql is not None
    assert len(diag.validation_errors_seen) >= 1
    # The second call must include feedback text with the validation code.
    assert "filter_var_unbound" in calls[1] or "unbound_projection_var" in calls[1]


def test_workflow_invalid_unrepaired() -> None:
    deps = _make_deps(max_repair_attempts=1)

    def generate(question: str) -> PlanGenerationOutput:
        return _bad_output()  # never gets better

    _, diag = run_planner_workflow(deps, "Who knows?", generate=generate)
    assert diag.final_validation_ok is False
    assert diag.repair_attempts == 1
    assert diag.rendered_sparql is None
    # We should have collected validation errors from BOTH attempts (initial + 1 repair).
    assert len(diag.validation_errors_seen) >= 2


def test_workflow_clarification_short_circuits() -> None:
    deps = _make_deps()
    calls: list[str] = []

    def generate(question: str) -> PlanGenerationOutput:
        calls.append(question)
        return _clarification_output()

    out, diag = run_planner_workflow(deps, "Show me Aurora.", generate=generate)
    assert out.needs_clarification is True
    assert diag.final_validation_ok is False
    assert diag.repair_attempts == 0
    assert len(calls) == 1


def test_workflow_first_try_valid() -> None:
    deps = _make_deps()
    calls = 0

    def generate(question: str) -> PlanGenerationOutput:
        nonlocal calls
        calls += 1
        return _good_output()

    _, diag = run_planner_workflow(deps, "Who knows?", generate=generate)
    assert diag.final_validation_ok is True
    assert diag.repair_attempts == 0
    assert calls == 1
    assert diag.rendered_sparql is not None


# --- build_planner_from_callable behaviour -------------------------------


def test_build_planner_from_callable_reports_repair_attempted() -> None:
    deps = _make_deps()
    sequence = [_bad_output(), _good_output()]

    def generate(question: str) -> PlanGenerationOutput:
        return sequence.pop(0)

    planner = build_planner_from_callable(deps, generate)
    planner.plan("Anything")
    assert planner.last_repair_attempted is True
    assert planner.last_repair_succeeded is True
    assert planner.last_diagnostics is not None
    assert planner.last_diagnostics.final_validation_ok is True


def test_build_planner_from_callable_invented_term_path() -> None:
    """Planner returns a plan with an unknown prefix; validator rejects it."""
    deps = _make_deps(max_repair_attempts=1)
    invented = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ghost", local="thing"),
                object=Var(name="q"),
            )
        ],
    )
    output = PlanGenerationOutput(question="?", plan=invented, confidence=0.5)

    def generate(question: str) -> PlanGenerationOutput:
        return output

    planner = build_planner_from_callable(deps, generate)
    planner.plan("?")
    assert planner.last_diagnostics is not None
    assert planner.last_diagnostics.final_validation_ok is False
    codes = {e.code for e in planner.last_diagnostics.validation_errors_seen}
    assert "unknown_prefix" in codes


def test_planner_diagnostics_serializes_to_dict() -> None:
    diag = PlannerDiagnostics()
    diag.repair_attempts = 2
    diag.final_validation_ok = True
    diag.rendered_sparql = "SELECT * WHERE { ?s ?p ?o }"
    payload = diag.model_dump()
    assert payload["repair_attempts"] == 2
    assert payload["final_validation_ok"] is True
    assert "SELECT" in payload["rendered_sparql"]
