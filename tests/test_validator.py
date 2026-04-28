"""Tests for QueryPlanValidator."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    AggregateExpr,
    BinaryExpr,
    BindPattern,
    FilterPattern,
    Iri,
    LiteralValue,
    NotExistsExpr,
    OptionalPattern,
    Prefix,
    PrefixedName,
    Projection,
    PropertyPathOneOrMore,
    PropertyPathTerm,
    SelectPlan,
    ServicePattern,
    SubqueryPattern,
    TriplePattern,
    ValuesPattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _select(*patterns, **kwargs) -> SelectPlan:  # type: ignore[no-untyped-def]
    return SelectPlan(prefixes=[EX], where=list(patterns), **kwargs)


def test_unbound_projection_var(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(
            subject=Var(name="p"),
            predicate=_ex("knows"),
            object=Var(name="q"),
        ),
        projection=[Projection(var=Var(name="missing"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unbound_projection_var" in codes


def test_filter_var_unbound(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=BinaryExpr(op="=", left=Var(name="z"), right=LiteralValue(value=1))
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "filter_var_unbound" in codes


def test_bind_rebind(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("age"), object=Var(name="age")),
        BindPattern(
            expression=LiteralValue(value=1),
            var=Var(name="age"),
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "bind_rebind" in codes


def test_having_non_grouped(validator: QueryPlanValidator) -> None:
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
        having=[BinaryExpr(op=">", left=Var(name="p"), right=LiteralValue(value=1))],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("worksFor"), object=Var(name="company")
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "having_non_grouped_var" in codes


def test_having_with_aggregate_ok(validator: QueryPlanValidator) -> None:
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
        having=[
            BinaryExpr(
                op=">",
                left=AggregateExpr(function="count", expression=Var(name="p")),
                right=LiteralValue(value=1),
            )
        ],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("worksFor"), object=Var(name="company")
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_non_grouped_projection(validator: QueryPlanValidator) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[
            Projection(var=Var(name="p")),  # not grouped
            Projection(
                expression=AggregateExpr(function="count", expression=Var(name="p")),
                alias=Var(name="n"),
            ),
        ],
        where=[TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "non_grouped_projection" in codes


def test_filter_after_optional_warning(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        OptionalPattern(
            patterns=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex("age"),
                    object=Var(name="age"),
                )
            ]
        ),
        FilterPattern(
            expression=BinaryExpr(op=">", left=Var(name="age"), right=LiteralValue(value=18))
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.warnings}
    assert "filter_after_optional" in codes


def test_service_disabled_by_default(validator: QueryPlanValidator) -> None:
    plan = _select(
        ServicePattern(
            endpoint=Iri(value="http://other.example/sparql"),
            patterns=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_ex("knows"),
                    object=Var(name="y"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="x"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "service_not_allowed" in codes


def test_service_allowlist() -> None:
    settings = Settings(allowed_service_endpoints=["http://other.example/sparql"])
    policy = SecurityPolicy.from_settings(settings)
    v = QueryPlanValidator(policy)
    plan = _select(
        ServicePattern(
            endpoint=Iri(value="http://other.example/sparql"),
            patterns=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_ex("knows"),
                    object=Var(name="y"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="x"))],
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_unbounded_path_disabled_by_default(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(
            subject=Var(name="x"),
            predicate=PropertyPathOneOrMore(operand=PropertyPathTerm(iri=_ex("knows"))),
            object=Var(name="y"),
        ),
        projection=[Projection(var=Var(name="x"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unbounded_property_path" in codes


def test_unbounded_path_allowed_when_policy_allows(permissive_policy: SecurityPolicy) -> None:
    v = QueryPlanValidator(permissive_policy)
    plan = _select(
        TriplePattern(
            subject=Var(name="x"),
            predicate=PropertyPathOneOrMore(operand=PropertyPathTerm(iri=_ex("knows"))),
            object=Var(name="y"),
        ),
        projection=[Projection(var=Var(name="x"))],
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_named_graph_allowlist() -> None:
    settings = Settings(allowed_graphs=["http://example.org/graph1"])
    policy = SecurityPolicy.from_settings(settings)
    v = QueryPlanValidator(policy)
    from graph_mcp.models.patterns import GraphPattern

    plan = _select(
        GraphPattern(
            graph=Iri(value="http://other.example/graph"),
            patterns=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_ex("knows"),
                    object=Var(name="y"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="x"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_not_allowed" in codes


def test_unknown_prefix(validator: QueryPlanValidator) -> None:
    plan = SelectPlan(
        prefixes=[],
        projection=[Projection(var=Var(name="x"))],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=PrefixedName(prefix="missing", local="thing"),
                object=Var(name="y"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_subquery_projects_outward(validator: QueryPlanValidator) -> None:
    sub = SelectPlan(
        projection=[Projection(var=Var(name="company"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=Var(name="company"),
            )
        ],
    )
    plan = _select(
        SubqueryPattern(select=sub),
        FilterPattern(
            expression=BinaryExpr(op="!=", left=Var(name="company"), right=LiteralValue(value="x"))
        ),
        projection=[Projection(var=Var(name="company"))],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_not_exists_inner_var_does_not_leak(validator: QueryPlanValidator) -> None:
    """Variables introduced inside NOT EXISTS must not be flagged as unbound."""
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=NotExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("blocks"),
                        object=Var(name="anyBlocked"),
                    )
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_limit_cap(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        projection=[Projection(var=Var(name="p"))],
        limit=10**9,
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "limit_too_high" in codes


def test_aggregate_outside_projection_or_having(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=BinaryExpr(
                op=">",
                left=AggregateExpr(function="count", expression=Var(name="q")),
                right=LiteralValue(value=1),
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "aggregate_outside_projection_or_having" in codes


def test_values_binds_variables(validator: QueryPlanValidator) -> None:
    plan = _select(
        ValuesPattern(
            variables=[Var(name="p")],
            rows=[[_ex("alice")], [_ex("bob")]],
        ),
        TriplePattern(subject=Var(name="p"), predicate=_ex("worksFor"), object=Var(name="c")),
        projection=[Projection(var=Var(name="c"))],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues
