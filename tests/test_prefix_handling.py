"""Tests for the top-level-only prefix policy."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def test_subquery_prefixes_are_rejected(validator: QueryPlanValidator) -> None:
    sub = SelectPlan(
        prefixes=[EX],  # NOT allowed inside a subquery
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
    )
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[SubqueryPattern(select=sub)],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "subquery_prefixes_not_allowed" in codes


def test_top_level_prefixes_are_accepted(validator: QueryPlanValidator) -> None:
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
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_prefix_conflict_is_rejected(validator: QueryPlanValidator) -> None:
    plan = SelectPlan(
        prefixes=[
            Prefix(prefix="ex", iri="http://example.org/"),
            Prefix(prefix="ex", iri="http://different.example/"),
        ],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "prefix_conflict" in codes


def test_duplicate_prefix_with_same_iri_is_ok(validator: QueryPlanValidator) -> None:
    plan = SelectPlan(
        prefixes=[
            Prefix(prefix="ex", iri="http://example.org/"),
            Prefix(prefix="ex", iri="http://example.org/"),  # exact duplicate
        ],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues
