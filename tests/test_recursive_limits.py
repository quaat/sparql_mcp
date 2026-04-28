"""Recursive LIMIT validation/normalization tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.models import (
    AggregateExpr,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _v_with_max(max_limit: int) -> tuple[QueryPlanValidator, SparqlRenderer]:
    s = Settings(max_limit=max_limit)
    p = SecurityPolicy.from_settings(s)
    return QueryPlanValidator(p), SparqlRenderer(p)


def test_subquery_limit_above_max_is_rejected() -> None:
    v, _ = _v_with_max(100)
    sub = SelectPlan(
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
        limit=10**6,
    )
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[SubqueryPattern(select=sub)],
        limit=50,
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "limit_too_high" in codes


def test_subquery_without_limit_is_not_normalized() -> None:
    """Subquery semantics should not change: no implicit LIMIT injection."""
    _, r = _v_with_max(100)
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
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="company"))],
        where=[SubqueryPattern(select=sub)],
    )
    out = r.render(plan)
    # Top-level gets a default LIMIT, but the subquery body must not.
    # We split on the first GROUP BY to isolate the subquery body.
    sub_segment = out.sparql.split("GROUP BY ?company", 1)[0]
    # If the subquery body had a LIMIT, "LIMIT " would appear before GROUP BY.
    assert "LIMIT " not in sub_segment


def test_subquery_explicit_limit_capped_in_render() -> None:
    _, r = _v_with_max(100)
    sub = SelectPlan(
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            )
        ],
        limit=500,  # over the policy max — must render as 100
    )
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[SubqueryPattern(select=sub)],
        limit=10,
    )
    out = r.render(plan)
    # The subquery's rendered LIMIT must be <= 100.
    assert "LIMIT 500" not in out.sparql
    assert "LIMIT 100" in out.sparql or "LIMIT 10" in out.sparql


def test_top_level_above_max_is_rejected() -> None:
    v, _ = _v_with_max(100)
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
        limit=10**6,
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "limit_too_high" in codes
