"""Strengthened aggregate-projection validation tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.models import (
    AggregateExpr,
    BinaryExpr,
    LiteralValue,
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


def test_mixed_expression_with_ungrouped_var_is_rejected(
    validator: QueryPlanValidator,
) -> None:
    """``(?x + COUNT(?y) AS ?bad)`` must be rejected unless ``?x`` is grouped."""
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(
                expression=BinaryExpr(
                    op="+",
                    left=Var(name="x"),
                    right=AggregateExpr(function="count", expression=Var(name="y")),
                ),
                alias=Var(name="bad"),
            ),
        ],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_ex("knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "non_grouped_in_expression" in codes


def test_mixed_expression_with_grouped_var_is_ok(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="x")),
            Projection(
                expression=BinaryExpr(
                    op="+",
                    left=Var(name="x"),  # now grouped
                    right=AggregateExpr(function="count", expression=Var(name="y")),
                ),
                alias=Var(name="ok"),
            ),
        ],
        group_by=[Var(name="x")],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_ex("knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_alias_collision_between_aliases_is_rejected(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name="y")),
                alias=Var(name="n"),
            ),
            Projection(
                expression=LiteralValue(value=1),
                alias=Var(name="n"),  # same alias twice
            ),
        ],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_ex("knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "alias_collision" in codes or "duplicate_projection" in codes


def test_order_by_non_grouped_in_aggregate_query_is_rejected(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="x")),
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name="y")),
                alias=Var(name="n"),
            ),
        ],
        group_by=[Var(name="x")],
        order_by=[OrderClause(expression=Var(name="y"))],  # ?y not grouped, not aliased
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_ex("knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "order_by_non_grouped" in codes


def test_order_by_alias_in_aggregate_query_is_ok(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="x")),
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name="y")),
                alias=Var(name="n"),
            ),
        ],
        group_by=[Var(name="x")],
        order_by=[OrderClause(expression=Var(name="n"), descending=True)],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_ex("knows"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues
