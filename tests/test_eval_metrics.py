"""Tests for the new structural and safety metrics."""

from __future__ import annotations

from evals.metrics import compute_metrics
from evals.models import CaseResult


def _r(**kwargs: object) -> CaseResult:
    base = {
        "case_id": kwargs.get("case_id", "x"),
        "question": kwargs.get("question", "?"),
        "plan_generated": True,
        "plan_valid": True,
    }
    base.update(kwargs)
    return CaseResult.model_validate(base)


def test_required_feature_recall() -> None:
    results = [
        _r(required_features_total=4, required_features_present=4),  # 100% recall
        _r(required_features_total=4, required_features_present=2),  # 50% recall
    ]
    m = compute_metrics(results)
    # Aggregate: 6 / 8 = 0.75
    assert m["required_feature_recall"] == 0.75


def test_forbidden_feature_violation_rate() -> None:
    results = [
        _r(forbidden_features_total=2, forbidden_features_violated=0),
        _r(forbidden_features_total=2, forbidden_features_violated=1),
    ]
    m = compute_metrics(results)
    assert m["forbidden_feature_violation_rate"] == 0.25


def test_term_resolution_accuracy() -> None:
    results = [
        _r(expected_terms_total=3, expected_terms_present=3),
        _r(expected_terms_total=3, expected_terms_present=1),
    ]
    m = compute_metrics(results)
    assert m["term_resolution_accuracy"] == 4 / 6


def test_structural_plan_score_combines_recall_and_purity() -> None:
    results = [
        _r(
            required_features_total=2,
            required_features_present=2,
            forbidden_features_total=2,
            forbidden_features_violated=0,
        ),
    ]
    m = compute_metrics(results)
    # recall=1, purity=1 → 1.0
    assert m["structural_plan_score"] == 1.0

    results2 = [
        _r(
            required_features_total=2,
            required_features_present=1,
            forbidden_features_total=2,
            forbidden_features_violated=1,
        ),
    ]
    m2 = compute_metrics(results2)
    # recall=0.5, purity=0.5 → 0.25
    assert m2["structural_plan_score"] == 0.25


def test_validation_error_rate() -> None:
    results = [
        _r(plan_valid=True, failures=[]),
        _r(plan_valid=False, failures=["INVALID_PLAN: foo"]),
    ]
    m = compute_metrics(results)
    assert m["validation_error_rate"] == 0.5


def test_repair_metrics() -> None:
    results = [
        _r(repair_attempted=True, repair_succeeded=True),
        _r(repair_attempted=True, repair_succeeded=False),
        _r(repair_attempted=False, repair_succeeded=False),
    ]
    m = compute_metrics(results)
    assert m["repair_attempted_rate"] == 2 / 3
    assert m["repair_success_rate"] == 0.5
