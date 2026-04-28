"""Aggregate metrics over per-case results."""

from __future__ import annotations

from collections.abc import Iterable

from evals.models import CaseResult


def compute_metrics(results: Iterable[CaseResult]) -> dict[str, float]:
    results = list(results)
    n = max(1, len(results))

    plan_generated = sum(1 for r in results if r.plan_generated)
    plan_valid = sum(1 for r in results if r.plan_valid)
    rendered = sum(1 for r in results if r.rendered_sparql is not None)
    executed = sum(1 for r in results if r.executed)
    failures = sum(1 for r in results if r.failures)
    safety = sum(1 for r in results if any(f.startswith("SAFETY:") for f in r.failures))

    return {
        "valid_plan_rate": plan_valid / n,
        "render_success_rate": rendered / n,
        "execution_success_rate": executed / n,
        "case_pass_rate": (n - failures) / n,
        "safety_violation_count": float(safety),
        "total_cases": float(n),
        "planner_output_rate": plan_generated / n,
    }
