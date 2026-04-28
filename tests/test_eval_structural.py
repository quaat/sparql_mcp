"""Deterministic tests for the IR-level structural matchers and new metrics."""

from __future__ import annotations

import pytest

from evals.metrics import compute_metrics
from evals.models import (
    AggregateSpec,
    CaseResult,
    FilterSpec,
    GoldenCase,
    GoldenCaseExpected,
    OrderBySpec,
    TripleSpec,
)
from evals.structural import (
    collect_pattern_kinds,
    count_matching_triples,
    find_matching_aggregate,
    has_filter,
    has_group_by_var,
    has_order_by,
    matches_bindings,
)
from graph_mcp.models import (
    AggregateExpr,
    BinaryExpr,
    FilterPattern,
    FunctionExpr,
    LiteralValue,
    NotExistsExpr,
    OrderClause,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


# --- Structural matcher unit tests ---------------------------------------


def test_collect_pattern_kinds_walks_nested() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
            FilterPattern(
                expression=NotExistsExpr(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=_ex("blocks"),
                            object=Var(name="x"),
                        )
                    ]
                )
            ),
        ],
    )
    kinds = collect_pattern_kinds(plan)
    assert "triple" in kinds
    assert "filter" in kinds


def test_count_matching_triples_with_variable_slot() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[TriplePattern(subject=Var(name="p"), predicate=_ex("worksFor"), object=_ex("Acme"))],
    )
    spec = TripleSpec(subject="?_", predicate="ex:worksFor", object="ex:Acme")
    assert count_matching_triples(plan, spec) == 1


def test_count_matching_triples_with_literal_object() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[TriplePattern(subject=Var(name="p"), predicate=_ex("worksFor"), object=_ex("Acme"))],
    )
    spec = TripleSpec(subject="?_", predicate="ex:worksFor", object="ex:Globex")
    assert count_matching_triples(plan, spec) == 0


def test_filter_lang_equals_match() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="x"))],
        where=[
            TriplePattern(subject=Var(name="x"), predicate=_ex("name"), object=Var(name="lbl")),
            FilterPattern(
                expression=BinaryExpr(
                    op="=",
                    left=FunctionExpr(name="lang", args=[Var(name="lbl")]),
                    right=LiteralValue(value="en"),
                )
            ),
        ],
    )
    assert has_filter(plan, FilterSpec(kind="lang_equals", var="?lbl", value="en"))
    assert not has_filter(plan, FilterSpec(kind="lang_equals", var="?other", value="en"))


def test_filter_compare_match() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(subject=Var(name="p"), predicate=_ex("age"), object=Var(name="age")),
            FilterPattern(
                expression=BinaryExpr(op=">", left=Var(name="age"), right=LiteralValue(value=30))
            ),
        ],
    )
    assert has_filter(plan, FilterSpec(kind="compare", op=">", var="?age", value=30))
    assert not has_filter(plan, FilterSpec(kind="compare", op="<", var="?age", value=30))


def test_aggregate_match() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="company")),
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name="p")),
                alias=Var(name="n"),
            ),
        ],
        group_by=[Var(name="company")],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=Var(name="company"),
            )
        ],
    )
    assert find_matching_aggregate(
        plan, AggregateSpec(function="count", expression="?p", alias="?n")
    )
    assert has_group_by_var(plan, "?company")
    assert not has_group_by_var(plan, "?other")


def test_order_by_match() -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        order_by=[OrderClause(expression=Var(name="p"), descending=True)],
        where=[TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q"))],
    )
    assert has_order_by(plan, OrderBySpec(expression="?p", descending=True))
    assert not has_order_by(plan, OrderBySpec(expression="?p", descending=False))


def test_matches_bindings_subset() -> None:
    rows = [
        {"p": "http://example.org/alice", "c": "http://example.org/Acme"},
        {"p": "http://example.org/bob", "c": "http://example.org/Acme"},
    ]
    assert matches_bindings(rows, {"p": "http://example.org/alice"})
    assert not matches_bindings(rows, {"p": "http://example.org/carol"})


# --- Metric tests --------------------------------------------------------


def _make_result(**kwargs: object) -> CaseResult:
    base = {
        "case_id": "x",
        "question": "?",
        "plan_generated": True,
        "plan_valid": True,
    }
    base.update(kwargs)
    return CaseResult.model_validate(base)


def test_triple_pattern_recall_metric() -> None:
    results = [
        _make_result(triple_total=2, triple_present=2),
        _make_result(triple_total=2, triple_present=1),
    ]
    m = compute_metrics(results)
    assert m["triple_pattern_recall"] == 0.75


def test_filter_semantics_recall_metric() -> None:
    results = [
        _make_result(filter_total=1, filter_present=1),
        _make_result(filter_total=1, filter_present=0),
    ]
    m = compute_metrics(results)
    assert m["filter_semantics_recall"] == 0.5


def test_aggregate_semantics_recall_metric() -> None:
    results = [
        _make_result(aggregate_total=2, aggregate_present=1),
    ]
    m = compute_metrics(results)
    assert m["aggregate_semantics_recall"] == 0.5


def test_grouping_semantics_recall_metric() -> None:
    results = [
        _make_result(group_by_total=2, group_by_present=2, order_by_total=1, order_by_present=0),
    ]
    m = compute_metrics(results)
    # 2/3 of the group-or-order checks passed
    assert m["grouping_semantics_recall"] == pytest.approx(2 / 3)


def test_result_binding_accuracy_metric() -> None:
    results = [
        _make_result(expected_bindings_total=4, expected_bindings_present=3),
    ]
    m = compute_metrics(results)
    assert m["result_binding_accuracy"] == 0.75


def test_clarification_accuracy_metric() -> None:
    results = [
        _make_result(is_clarification_case=True, clarification_correct=True),
        _make_result(is_clarification_case=True, clarification_correct=False),
        _make_result(),  # not a clarification case — ignored
    ]
    m = compute_metrics(results)
    assert m["clarification_accuracy"] == 0.5


def test_unsafe_request_rejection_accuracy_metric() -> None:
    results = [
        _make_result(is_unsafe_request_case=True, unsafe_request_rejected=True),
        _make_result(is_unsafe_request_case=True, unsafe_request_rejected=True),
        _make_result(is_unsafe_request_case=True, unsafe_request_rejected=False),
        _make_result(),  # not an unsafe case — ignored
    ]
    m = compute_metrics(results)
    assert m["unsafe_request_rejection_accuracy"] == pytest.approx(2 / 3)


def test_forbidden_pattern_violation_rate_metric() -> None:
    results = [
        _make_result(forbidden_pattern_kinds_total=2, forbidden_pattern_kinds_violated=1),
    ]
    m = compute_metrics(results)
    assert m["forbidden_pattern_violation_rate"] == 0.5


def test_metrics_zero_total_returns_neutral_value() -> None:
    """When no case has the relevant requirement, the metric is 1.0 (recall) or 0.0 (rate)."""
    results = [_make_result()]
    m = compute_metrics(results)
    assert m["triple_pattern_recall"] == 1.0
    assert m["filter_semantics_recall"] == 1.0
    assert m["aggregate_semantics_recall"] == 1.0
    assert m["clarification_accuracy"] == 1.0
    assert m["unsafe_request_rejection_accuracy"] == 1.0
    assert m["forbidden_pattern_violation_rate"] == 0.0


def test_golden_case_with_ir_requirements_loads() -> None:
    case = GoldenCase(
        id="x",
        question="?",
        expected=GoldenCaseExpected(
            required_pattern_kinds=["triple"],
            required_triples=[TripleSpec(subject="?p", predicate="ex:worksFor", object="ex:Acme")],
            required_aggregates=[AggregateSpec(function="count", expression="?p")],
            required_group_by=["?company"],
            expected_bindings=[{"p": "http://example.org/alice"}],
        ),
    )
    assert case.expected.required_pattern_kinds == ["triple"]
    assert case.expected.required_triples[0].predicate == "ex:worksFor"
