"""Regression tests for the eval false negatives identified in the v7 live run.

The v7 live report failed several cases that were actually semantically
correct. The matchers below recreate the exact rows / SPARQL the LLM
produced and confirm the eval no longer fails them.
"""

from __future__ import annotations

import pytest

from evals.models import (
    AggregateSpec,
    FilterSpec,
)
from evals.structural import (
    DEFAULT_VAR_ALIASES,
    _aliases_for,
    _var_matches_expected,
    find_matching_aggregate,
    has_filter,
    matches_bindings,
)
from graph_mcp.models import (
    AggregateExpr,
    BinaryExpr,
    FilterPattern,
    FunctionExpr,
    LangMatchesExpr,
    LiteralValue,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")


# --- §2: prefix expansion + variable alias bindings ----------------------


def test_binding_matches_prefixed_expected_against_absolute_actual_without_plan_prefix() -> None:
    """v7 case_001: actual rows used absolute IRIs and the plan declared no
    ``ex`` prefix. The matcher must fall back to the built-in prefix map."""
    rows = [{"person": "http://example.org/alice"}, {"person": "http://example.org/bob"}]
    assert matches_bindings(rows, {"person": "ex:alice"})
    assert matches_bindings(rows, {"person": "ex:bob"})


def test_binding_matches_alias_p_vs_person() -> None:
    """v7 case_001/003: expected ``?p`` but the LLM projected ``?person``."""
    rows = [{"person": "http://example.org/alice"}]
    assert matches_bindings(rows, {"p": "ex:alice"})


def test_binding_matches_case_insensitive_variable_names() -> None:
    """v7 case_006: expected ``?a/?b`` but the LLM used ``?A/?B``."""
    rows = [{"A": "http://example.org/alice", "B": "http://example.org/bob"}]
    assert matches_bindings(rows, {"a": "ex:alice", "b": "ex:bob"})


def test_binding_matches_alias_b_vs_person_for_single_column_path_result() -> None:
    """v7 case_009: expected key ``b`` but the LLM projected ``?person``."""
    rows = [{"person": "http://example.org/bob"}, {"person": "http://example.org/carol"}]
    assert matches_bindings(rows, {"b": "ex:bob"})
    assert matches_bindings(rows, {"b": "ex:carol"})


def test_binding_does_not_match_when_values_differ() -> None:
    rows = [{"person": "http://example.org/alice"}]
    assert not matches_bindings(rows, {"person": "ex:dan"})


def test_binding_aliases_require_distinct_actual_columns_for_multi_column_rows() -> None:
    """A single actual column can't satisfy two expected keys."""
    rows = [{"only": "http://example.org/alice"}]
    assert not matches_bindings(rows, {"a": "ex:alice", "b": "ex:alice"})


def test_per_case_binding_aliases_extend_default_map() -> None:
    rows = [{"X": "http://example.org/alice"}]
    assert matches_bindings(rows, {"a": "ex:alice"}, binding_aliases={"a": ["X"]})


def test_aliases_for_includes_default_map() -> None:
    aliases = _aliases_for("p")
    assert "person" in aliases
    assert "employee" in aliases


def test_var_matches_expected_handles_question_mark_prefix() -> None:
    assert _var_matches_expected("?person", "?p")
    assert _var_matches_expected("$person", "p")
    assert not _var_matches_expected("?carol", "?dan")


# --- §3: LANGMATCHES + var alias for filter matchers ---------------------


def _lang_filter_binary(var_name: str = "lbl") -> FilterPattern:
    return FilterPattern(
        expression=BinaryExpr(
            op="=",
            left=FunctionExpr(name="lang", args=[Var(name=var_name)]),
            right=LiteralValue(value="en"),
        )
    )


def _lang_filter_langmatches(var_name: str = "label") -> FilterPattern:
    return FilterPattern(
        expression=LangMatchesExpr(
            tag=FunctionExpr(name="lang", args=[Var(name=var_name)]),
            range=LiteralValue(value="en"),
        )
    )


def _select_with_filter(filter_pat: FilterPattern, label_var: str) -> SelectPlan:
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="x")), Projection(var=Var(name=label_var))],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=PrefixedName(prefix="rdfs", local="label"),
                object=Var(name=label_var),
            ),
            filter_pat,
        ],
    )


def test_lang_equals_matches_binary_lang_equals() -> None:
    plan = _select_with_filter(_lang_filter_binary("lbl"), "lbl")
    assert has_filter(plan, FilterSpec(kind="lang_equals", var="?lbl", value="en"))


def test_lang_equals_matches_lang_matches_expr() -> None:
    """v7 case_002: the LLM emitted langMatches(lang(?label), "en")."""
    plan = _select_with_filter(_lang_filter_langmatches("label"), "label")
    assert has_filter(plan, FilterSpec(kind="lang_equals", var="?lbl", value="en"))


def test_lang_equals_accepts_label_variable_alias() -> None:
    plan = _select_with_filter(_lang_filter_binary("label"), "label")
    assert has_filter(plan, FilterSpec(kind="lang_equals", var="?lbl", value="en"))


def test_lang_equals_rejects_wrong_language() -> None:
    plan = _select_with_filter(_lang_filter_binary("lbl"), "lbl")
    assert not has_filter(plan, FilterSpec(kind="lang_equals", var="?lbl", value="fr"))


# --- §4: aggregate matching ----------------------------------------------


def _select_with_count(var_name: str, alias: str = "n") -> SelectPlan:
    return SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="company")),
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name=var_name)),
                alias=Var(name=alias),
            ),
        ],
        group_by=[Var(name="company")],
        where=[
            TriplePattern(
                subject=Var(name=var_name),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=Var(name="company"),
            )
        ],
    )


def test_aggregate_wildcard_matches_count_employee() -> None:
    """v7 case_014: expected COUNT(?p) but the LLM used COUNT(?employee).
    A wildcard expression must match either."""
    plan = _select_with_count("employee", alias="employeeCount")
    assert find_matching_aggregate(plan, AggregateSpec(function="count", expression="?_"))
    # Wildcard expression treats alias as wildcard too.
    assert find_matching_aggregate(plan, AggregateSpec(function="count"))


def test_aggregate_exact_var_matches_alias() -> None:
    """When the spec gives ``?p``, alias matching makes ``?employee`` pass."""
    plan = _select_with_count("employee", alias="n")
    assert find_matching_aggregate(plan, AggregateSpec(function="count", expression="?p"))


def test_aggregate_count_star_is_distinct_from_count_var() -> None:
    """``expression="*"`` strictly requires ``COUNT(*)``; bindings of a var
    should NOT match."""
    plan = _select_with_count("employee", alias="n")
    assert not find_matching_aggregate(plan, AggregateSpec(function="count", expression="*"))


def test_aggregate_matcher_walks_subqueries() -> None:
    """Aggregates inside a subquery still count for `find_matching_aggregate`."""
    from graph_mcp.models import SubqueryPattern

    inner = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="company")),
            Projection(
                expression=AggregateExpr(function="max", expression=Var(name="age")),
                alias=Var(name="maxAge"),
            ),
        ],
        group_by=[Var(name="company")],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="age"),
                object=Var(name="age"),
            )
        ],
    )
    outer = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=Var(name="company"),
            ),
            SubqueryPattern(select=inner),
        ],
    )
    assert find_matching_aggregate(outer, AggregateSpec(function="max", expression="?_"))


def test_having_aggregate_is_detected() -> None:
    """When the same aggregate is in HAVING but not in projection, the
    matcher still finds it (case_014's plan structure)."""
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="company"))],
        group_by=[Var(name="company")],
        having=[
            BinaryExpr(
                op=">",
                left=AggregateExpr(function="count", expression=Var(name="employee")),
                right=LiteralValue(value=1),
            )
        ],
        where=[
            TriplePattern(
                subject=Var(name="employee"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=Var(name="company"),
            )
        ],
    )
    assert find_matching_aggregate(plan, AggregateSpec(function="count", expression="?_"))


# --- Sanity: default alias map covers the cases the live report exposed ---


def test_default_aliases_cover_live_report_cases() -> None:
    """Spot-check that key live-report aliases are in the default map."""
    assert "person" in DEFAULT_VAR_ALIASES["p"]
    assert "employee" in DEFAULT_VAR_ALIASES["p"]
    assert "person" in DEFAULT_VAR_ALIASES["b"]


# --- Live row replay ------------------------------------------------------


def test_case_001_live_rows_pass_with_new_matcher() -> None:
    """Direct replay of v7 case_001 actual rows against the v7 expected bindings."""
    rows = [{"person": "http://example.org/alice"}, {"person": "http://example.org/bob"}]
    expected = [{"person": "ex:alice"}, {"person": "ex:bob"}]
    for row in expected:
        assert matches_bindings(rows, row)


def test_case_006_live_rows_pass_with_new_matcher() -> None:
    """v7 case_006: LLM used ``?A/?B``; expected uses ``?a/?b``."""
    rows = [
        {"A": "http://example.org/alice", "B": "http://example.org/bob"},
        {"A": "http://example.org/alice", "B": "http://example.org/Acme"},
    ]
    assert matches_bindings(rows, {"a": "ex:alice", "b": "ex:bob"})
    assert matches_bindings(rows, {"a": "ex:alice", "b": "ex:Acme"})


@pytest.mark.parametrize(
    "actual_var,expected_value,expected_var",
    [
        ("person", "ex:bob", "b"),
        ("person", "ex:carol", "b"),
        ("person", "ex:alice", "p"),
    ],
)
def test_single_column_path_results_pass_with_alias_matching(
    actual_var: str, expected_value: str, expected_var: str
) -> None:
    rows = [{actual_var: f"http://example.org/{expected_value.split(':')[1]}"}]
    assert matches_bindings(rows, {expected_var: expected_value})
