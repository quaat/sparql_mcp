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
    invalid_count = sum(1 for r in results if any(f.startswith("INVALID_PLAN") for f in r.failures))

    # Required-feature recall: fraction of required (pattern, term) hits.
    rf_total = sum(r.required_features_total for r in results)
    rf_present = sum(r.required_features_present for r in results)
    et_total = sum(r.expected_terms_total for r in results)
    et_present = sum(r.expected_terms_present for r in results)

    # Forbidden-feature violation rate.
    ff_total = sum(r.forbidden_features_total for r in results)
    ff_violated = sum(r.forbidden_features_violated for r in results)

    # Repair stats (LLM planners only; deterministic baseline reports zero).
    repair_attempted = sum(1 for r in results if r.repair_attempted)
    repair_succeeded = sum(1 for r in results if r.repair_succeeded)

    # Term-resolution accuracy reuses the expected-terms hit rate as a proxy.
    term_accuracy = (et_present / et_total) if et_total else 1.0

    return {
        # Pipeline health
        "valid_plan_rate": plan_valid / n,
        "render_success_rate": rendered / n,
        "execution_success_rate": executed / n,
        "case_pass_rate": (n - failures) / n,
        "planner_output_rate": plan_generated / n,
        # Quality
        "required_feature_recall": (rf_present / rf_total) if rf_total else 1.0,
        "forbidden_feature_violation_rate": ((ff_violated / ff_total) if ff_total else 0.0),
        "term_resolution_accuracy": term_accuracy,
        "structural_plan_score": _structural_score(rf_present, rf_total, ff_violated, ff_total),
        "execution_result_accuracy": _execution_accuracy(results),
        # Safety
        "safety_violation_count": float(safety),
        "validation_error_rate": invalid_count / n,
        # Repair
        "repair_attempted_rate": repair_attempted / n,
        "repair_success_rate": ((repair_succeeded / repair_attempted) if repair_attempted else 0.0),
        # Totals
        "total_cases": float(n),
    }


def _structural_score(rf_present: int, rf_total: int, ff_violated: int, ff_total: int) -> float:
    """Combined structural quality in [0, 1]: required-recall * forbidden-purity."""
    recall = (rf_present / rf_total) if rf_total else 1.0
    purity = (1.0 - (ff_violated / ff_total)) if ff_total else 1.0
    return recall * purity


def _execution_accuracy(results: list[CaseResult]) -> float:
    """Fraction of executed cases whose row count matched the expected bound.

    Cases with no execution or no expectation are excluded.
    """
    relevant: list[CaseResult] = [
        r
        for r in results
        if r.executed and not any(f.startswith("RESULT_MISMATCH") for f in r.failures)
    ]
    executed_with_expectation = [r for r in results if r.executed and r.row_count is not None]
    if not executed_with_expectation:
        return 1.0
    return len(relevant) / len(executed_with_expectation)
