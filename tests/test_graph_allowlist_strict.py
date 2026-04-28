"""Strengthened graph-variable allowlist reasoning tests.

This file exercises the corner cases of the rule:

  GRAPH ?g is allowed under a non-empty named-graph allowlist only when a
  prior VALUES in the same required group scope has constrained ?g to
  allowlisted IRIs.

Constraints from inside OPTIONAL / UNION / MINUS / FILTER EXISTS / subqueries
must NOT escape into the parent scope. Multiple VALUES on the same variable
must intersect (not overwrite). UNDEF or literal cells invalidate the
constraint.
"""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    ExistsExpr,
    FilterPattern,
    GraphPattern,
    Iri,
    LiteralValue,
    MinusPattern,
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
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _select(*patterns) -> SelectPlan:  # type: ignore[no-untyped-def]
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="s"))],
        where=list(patterns),
    )


def _v(*allowed: str) -> QueryPlanValidator:
    s = Settings(
        allowed_graphs=",".join(allowed) if allowed else "",  # type: ignore[arg-type]
    )
    return QueryPlanValidator(SecurityPolicy.from_settings(s))


# --- Multiple VALUES intersecting -----------------------------------------


def test_graph_values_constraints_intersect() -> None:
    """Two VALUES on the same variable in the same scope must intersect.

    The first restricts ?g to {g1, g2}; the second to {g2, g3}. Intersection
    is {g2}. Allowlist contains all three; query passes.
    """
    v = _v("http://example.org/g1", "http://example.org/g2", "http://example.org/g3")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g1")],
                [Iri(value="http://example.org/g2")],
            ],
        ),
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g2")],
                [Iri(value="http://example.org/g3")],
            ],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_graph_values_intersection_outside_allowlist_is_rejected() -> None:
    """If the intersection includes any non-allowlisted IRI, reject.

    First VALUES: {g1, evil}. Second: {evil, g2}. Intersection: {evil}.
    Allowlist: {g1, g2}. evil is not allowed → reject.
    """
    v = _v("http://example.org/g1", "http://example.org/g2")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g1")],
                [Iri(value="http://example.org/evil")],
            ],
        ),
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/evil")],
                [Iri(value="http://example.org/g2")],
            ],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_values_not_allowed" in codes


def test_graph_values_empty_intersection_is_rejected() -> None:
    """Two VALUES with disjoint values intersect to ``{}`` — reject."""
    v = _v("http://example.org/g1", "http://example.org/g2")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[[Iri(value="http://example.org/g1")]],
        ),
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[[Iri(value="http://example.org/g2")]],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_values_constraint_empty" in codes


def test_graph_values_constraint_with_undef_is_not_safe() -> None:
    """A VALUES row containing a literal/UNDEF invalidates the constraint."""
    v = _v("http://example.org/g1")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[[Iri(value="http://example.org/g1")]],
        ),
        # Second VALUES has UNDEF (None in IR) — invalidates the constraint.
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[[None]],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_with_literal_invalidates_constraint() -> None:
    """A VALUES row containing a literal (non-IRI) invalidates."""
    v = _v("http://example.org/g1")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g1")],
                [LiteralValue(value="not-an-iri")],
            ],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


# --- Scope leakage --------------------------------------------------------


def test_graph_values_inside_optional_does_not_authorize_outer_graph() -> None:
    """A VALUES inside an OPTIONAL must not authorize a sibling outer GRAPH ?g."""
    v = _v("http://example.org/g1")
    plan = _select(
        OptionalPattern(
            patterns=[
                ValuesPattern(
                    variables=[Var(name="g")],
                    rows=[[Iri(value="http://example.org/g1")]],
                ),
            ]
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_inside_minus_does_not_authorize_outer_graph() -> None:
    v = _v("http://example.org/g1")
    plan = _select(
        MinusPattern(
            patterns=[
                ValuesPattern(
                    variables=[Var(name="g")],
                    rows=[[Iri(value="http://example.org/g1")]],
                ),
            ]
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_inside_subquery_does_not_authorize_outer_graph() -> None:
    v = _v("http://example.org/g1")
    sub = SelectPlan(
        projection=[Projection(var=Var(name="g"))],
        where=[
            ValuesPattern(
                variables=[Var(name="g")],
                rows=[[Iri(value="http://example.org/g1")]],
            ),
        ],
    )
    plan = _select(
        SubqueryPattern(select=sub),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_inside_exists_does_not_authorize_outer_graph() -> None:
    v = _v("http://example.org/g1")
    plan = _select(
        FilterPattern(
            expression=ExistsExpr(
                patterns=[
                    ValuesPattern(
                        variables=[Var(name="g")],
                        rows=[[Iri(value="http://example.org/g1")]],
                    ),
                ]
            )
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_inside_union_does_not_authorize_unless_all_branches_safe() -> None:
    """If even one UNION branch has GRAPH ?g without authorizing VALUES, fail.

    Each branch is validated independently; the failure surfaces on the
    branch that lacks the constraint.
    """
    v = _v("http://example.org/g1")
    plan = _select(
        UnionPattern(
            branches=[
                # Branch 1: VALUES restricts ?g to allowlisted, then GRAPH ?g — OK
                [
                    ValuesPattern(
                        variables=[Var(name="g")],
                        rows=[[Iri(value="http://example.org/g1")]],
                    ),
                    GraphPattern(
                        graph=Var(name="g"),
                        patterns=[
                            TriplePattern(
                                subject=Var(name="s"),
                                predicate=_ex("a"),
                                object=_ex("Person"),
                            )
                        ],
                    ),
                ],
                # Branch 2: GRAPH ?g without any VALUES — FAIL
                [
                    GraphPattern(
                        graph=Var(name="g"),
                        patterns=[
                            TriplePattern(
                                subject=Var(name="s"),
                                predicate=_ex("a"),
                                object=_ex("Person"),
                            )
                        ],
                    ),
                ],
            ]
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_ordering_is_documented_and_tested() -> None:
    """A VALUES that comes *after* GRAPH ?g does not retroactively authorize.

    The validator walks group children in order; a later VALUES is irrelevant
    to an earlier GRAPH ?g check. This is the documented order rule.
    """
    v = _v("http://example.org/g1")
    plan = _select(
        # GRAPH ?g first
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
        # VALUES second — too late to authorize the GRAPH above
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[[Iri(value="http://example.org/g1")]],
        ),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_values_correct_order_passes() -> None:
    """The happy path: VALUES before GRAPH, all values allowlisted."""
    v = _v("http://example.org/g1", "http://example.org/g2")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g1")],
                [Iri(value="http://example.org/g2")],
            ],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(subject=Var(name="s"), predicate=_ex("a"), object=_ex("Person"))
            ],
        ),
    )
    res = v.validate(plan)
    assert res.ok, res.issues
