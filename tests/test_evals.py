"""Tests for the evals package."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.agent import DeterministicPlanner
from evals.metrics import compute_metrics
from evals.models import (
    CaseResult,
    GoldenCase,
    PlannedOutput,
)
from evals.runner import (
    _DEFAULT_CASES,
    _DEFAULT_GRAPH,
    build_components,
    load_cases,
    render_markdown_report,
    run,
)

CASES_PATH = Path(__file__).parent.parent / "evals" / "golden_cases.yaml"


def test_load_cases() -> None:
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 20
    assert all(isinstance(c, GoldenCase) for c in cases)


def test_planner_output_validates() -> None:
    p = DeterministicPlanner()
    out = p.plan("Who works for Acme?")
    assert isinstance(out, PlannedOutput)
    assert out.plan.kind == "select"


def test_metrics_computation() -> None:
    results = [
        CaseResult(case_id="a", question="?", plan_generated=True, plan_valid=True, executed=True),
        CaseResult(
            case_id="b",
            question="?",
            plan_generated=True,
            plan_valid=False,
            failures=["INVALID_PLAN: x"],
        ),
    ]
    m = compute_metrics(results)
    assert m["total_cases"] == 2
    assert m["valid_plan_rate"] == 0.5
    assert m["case_pass_rate"] == 0.5


@pytest.mark.asyncio
async def test_run_deterministic_offline() -> None:
    """End-to-end eval run, no LLM, using the bundled sample graph."""
    cases = load_cases(_DEFAULT_CASES)
    components = await build_components(graph_path=_DEFAULT_GRAPH)
    report = await run(cases, DeterministicPlanner(), components=components, execute=True)
    # The deterministic baseline must achieve perfect case-pass rate.
    assert report.metrics["case_pass_rate"] == 1.0
    assert report.metrics["safety_violation_count"] == 0


def test_markdown_report_renders() -> None:
    from evals.models import EvaluationReport

    report = EvaluationReport(
        cases=[
            CaseResult(
                case_id="x",
                question="?",
                plan_generated=True,
                plan_valid=True,
                rendered_sparql="SELECT * WHERE { ?s ?p ?o }",
                executed=True,
                row_count=1,
            )
        ],
        metrics={"valid_plan_rate": 1.0},
    )
    md = render_markdown_report(report)
    assert "# Evaluation Report" in md
    assert "x" in md
