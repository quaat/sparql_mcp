"""Tests for the eval runner planner wiring (§1, §8, §9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evals.agent import (
    PlannerOutput,
    build_planner_from_callable,
)
from evals.models import (
    PlannedOutput,
)
from evals.runner import (
    ThresholdSpec,
    _build_azure_openai_model,
    _check_thresholds,
    build_components,
    make_planner,
)
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
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


# --- §8: Azure cleanup ---------------------------------------------------


def test_azure_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
        _build_azure_openai_model()


def test_azure_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="endpoint"):
        _build_azure_openai_model("model-name")


def test_azure_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/")
    monkeypatch.delenv("AZURE_OPENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="model name"):
        _build_azure_openai_model()


# --- §1: Planner wiring --------------------------------------------------


@pytest.mark.asyncio
async def test_runner_pydantic_ai_path_passes_validator_renderer_policy_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``make_planner('pydantic-ai', ...)`` must wire all four deps."""
    captured: dict[str, Any] = {}

    def _fake_build(
        model: object,
        *,
        schema: object,
        validator: object,
        renderer: object,
        policy: object,
        resolver: object,
        examples: object | None = None,
        max_repair_attempts: int = 2,
    ) -> object:
        captured["model"] = model
        captured["schema"] = schema
        captured["validator"] = validator
        captured["renderer"] = renderer
        captured["policy"] = policy
        captured["resolver"] = resolver
        captured["examples"] = examples
        return object()

    monkeypatch.setattr("evals.runner.build_pydantic_ai_planner", _fake_build)

    components = await build_components(graph_path=_GRAPH)
    make_planner("pydantic-ai", components, model="anthropic:claude-sonnet-4-6", azure=False)
    assert isinstance(captured["validator"], QueryPlanValidator)
    assert isinstance(captured["renderer"], SparqlRenderer)
    assert isinstance(captured["policy"], SecurityPolicy)
    assert captured["resolver"] is components.resolver
    assert captured["schema"] is components.schema_provider


@pytest.mark.asyncio
async def test_pydantic_ai_planner_uses_workflow_when_deps_are_supplied() -> None:
    """The planner returned must run validation/repair/diagnostics."""
    components = await build_components(graph_path=_GRAPH)
    sequence = [_invalid_planned(), _good_planned()]

    def gen(prompt: str) -> PlannerOutput:
        return sequence.pop(0)

    from evals.agent import PlannerDeps

    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )
    planner = build_planner_from_callable(deps, gen)
    out = planner.plan("Who knows whom?")
    assert isinstance(out, PlannedOutput)
    assert planner.last_repair_attempted is True
    assert planner.last_repair_succeeded is True
    diag = planner.last_diagnostics
    assert diag is not None
    assert diag.final_validation_ok is True


@pytest.mark.asyncio
async def test_repair_attempted_metric_increments_when_first_plan_is_invalid() -> None:
    """``repair_attempted`` flips on after the first invalid attempt."""
    components = await build_components(graph_path=_GRAPH)
    sequence = [_invalid_planned(), _invalid_planned()]
    from evals.agent import PlannerDeps

    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
        max_repair_attempts=1,
    )
    planner = build_planner_from_callable(deps, lambda _q: sequence.pop(0))
    planner.plan("?")
    assert planner.last_repair_attempted is True
    assert planner.last_repair_succeeded is False


@pytest.mark.asyncio
async def test_repair_success_metric_increments_when_repaired_plan_validates() -> None:
    components = await build_components(graph_path=_GRAPH)
    sequence = [_invalid_planned(), _good_planned()]
    from evals.agent import PlannerDeps

    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )
    planner = build_planner_from_callable(deps, lambda _q: sequence.pop(0))
    planner.plan("?")
    assert planner.last_repair_attempted is True
    assert planner.last_repair_succeeded is True


# --- §9: Threshold gate --------------------------------------------------


def test_threshold_check_flags_metric_below_minimum() -> None:
    metrics = {"case_pass_rate": 0.5, "valid_plan_rate": 0.99}
    failures = _check_thresholds(
        metrics,
        [
            ThresholdSpec("case_pass_rate", minimum=0.95),
            ThresholdSpec("valid_plan_rate", minimum=0.98),
        ],
    )
    assert any("case_pass_rate" in f for f in failures)
    assert not any("valid_plan_rate" in f for f in failures)


def test_threshold_check_flags_metric_above_maximum() -> None:
    metrics = {"safety_violation_count": 1.0}
    failures = _check_thresholds(metrics, [ThresholdSpec("safety_violation_count", maximum=0.0)])
    assert failures and "safety_violation_count" in failures[0]


def test_threshold_check_passes_when_all_metrics_ok() -> None:
    metrics = {"case_pass_rate": 1.0, "safety_violation_count": 0.0}
    failures = _check_thresholds(
        metrics,
        [
            ThresholdSpec("case_pass_rate", minimum=0.95),
            ThresholdSpec("safety_violation_count", maximum=0.0),
        ],
    )
    assert failures == []


# --- helpers --------------------------------------------------------------


def _good_planned() -> PlannedOutput:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="knows"),
                object=Var(name="q"),
            )
        ],
    )
    return PlannedOutput(question="?", plan=plan, confidence=0.9)


def _invalid_planned() -> PlannedOutput:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="never_bound"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="knows"),
                object=Var(name="q"),
            )
        ],
    )
    return PlannedOutput(question="?", plan=plan, confidence=0.5)
