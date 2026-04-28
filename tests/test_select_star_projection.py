"""Tests for inferring projected_variables when SELECT * is used."""

from __future__ import annotations

from graph_mcp.compiler import SparqlRenderer
from graph_mcp.models import (
    AggregateExpr,
    BindPattern,
    LiteralValue,
    OptionalPattern,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    UnionPattern,
    ValuesPattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def test_select_star_lists_triple_pattern_vars(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=_ex("knows"),
                object=Var(name="o"),
            )
        ],
    )
    out = renderer.render(plan)
    assert out.projected_variables == ["s", "o"]


def test_select_star_includes_optional_vars(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            ),
            OptionalPattern(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("nickname"),
                        object=Var(name="nick"),
                    )
                ]
            ),
        ],
    )
    out = renderer.render(plan)
    assert "nick" in out.projected_variables
    assert "p" in out.projected_variables
    assert "q" in out.projected_variables


def test_select_star_includes_union_branch_vars(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        where=[
            UnionPattern(
                branches=[
                    [
                        TriplePattern(
                            subject=Var(name="a"),
                            predicate=_ex("knows"),
                            object=Var(name="b"),
                        )
                    ],
                    [
                        TriplePattern(
                            subject=Var(name="a"),
                            predicate=_ex("worksFor"),
                            object=Var(name="c"),
                        )
                    ],
                ]
            )
        ],
    )
    out = renderer.render(plan)
    assert set(out.projected_variables) == {"a", "b", "c"}


def test_select_star_includes_bind_and_values(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        where=[
            ValuesPattern(
                variables=[Var(name="x")],
                rows=[[_ex("alice")]],
            ),
            BindPattern(
                expression=LiteralValue(value=42),
                var=Var(name="answer"),
            ),
        ],
    )
    out = renderer.render(plan)
    assert "x" in out.projected_variables
    assert "answer" in out.projected_variables


def test_select_star_uses_subquery_projection(renderer: SparqlRenderer) -> None:
    sub = SelectPlan(
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
    plan = SelectPlan(prefixes=[EX], where=[SubqueryPattern(select=sub)])
    out = renderer.render(plan)
    assert "company" in out.projected_variables
    assert "n" in out.projected_variables


def test_select_star_skips_filter_minus_not_exists(renderer: SparqlRenderer) -> None:
    """Variables only inside MINUS / FILTER must not appear in projected_variables."""
    from graph_mcp.models import FilterPattern, MinusPattern, NotExistsExpr

    plan = SelectPlan(
        prefixes=[EX],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            ),
            MinusPattern(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("excluded"),
                        object=Var(name="onlyMinus"),
                    )
                ]
            ),
            FilterPattern(
                expression=NotExistsExpr(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=_ex("blocked"),
                            object=Var(name="onlyExists"),
                        )
                    ]
                )
            ),
        ],
    )
    out = renderer.render(plan)
    assert "onlyMinus" not in out.projected_variables
    assert "onlyExists" not in out.projected_variables
    assert set(out.projected_variables) == {"p", "q"}
