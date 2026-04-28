"""Validator tests for EXISTS / NOT EXISTS — recursive checks + scope rules."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    BinaryExpr,
    ExistsExpr,
    FilterPattern,
    Iri,
    LiteralValue,
    NotExistsExpr,
    Prefix,
    PrefixedName,
    Projection,
    PropertyPathOneOrMore,
    PropertyPathTerm,
    SelectPlan,
    ServicePattern,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _select(*patterns, **kwargs) -> SelectPlan:  # type: ignore[no-untyped-def]
    return SelectPlan(prefixes=[EX], where=list(patterns), **kwargs)


def test_service_inside_not_exists_is_rejected(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=NotExistsExpr(
                patterns=[
                    ServicePattern(
                        endpoint=Iri(value="http://other.example/sparql"),
                        patterns=[
                            TriplePattern(
                                subject=Var(name="p"),
                                predicate=_ex("flagged"),
                                object=Var(name="x"),
                            )
                        ],
                    )
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "service_not_allowed" in codes


def test_unknown_prefix_inside_exists_is_rejected(validator: QueryPlanValidator) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=ExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        # `missing` prefix is not declared in plan.prefixes.
                        predicate=PrefixedName(prefix="missing", local="thing"),
                        object=Var(name="x"),
                    )
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_unbounded_path_inside_not_exists_is_rejected(
    validator: QueryPlanValidator,
) -> None:
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=NotExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=PropertyPathOneOrMore(operand=PropertyPathTerm(iri=_ex("knows"))),
                        object=Var(name="x"),
                    )
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unbounded_property_path" in codes


def test_inner_exists_variable_does_not_leak_outward(
    validator: QueryPlanValidator,
) -> None:
    """A variable introduced only inside EXISTS cannot satisfy a later projection."""
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=ExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("hasInner"),
                        object=Var(name="onlyInside"),
                    )
                ]
            )
        ),
        projection=[
            Projection(var=Var(name="p")),
            Projection(var=Var(name="onlyInside")),  # must be flagged unbound
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unbound_projection_var" in codes


def test_outer_variable_can_be_used_inside_exists(
    validator: QueryPlanValidator,
) -> None:
    """The outer ``?p`` is in scope inside the EXISTS block — not a free-var error."""
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=ExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),  # outer reference — fine
                        predicate=_ex("flag"),
                        object=Var(name="freshLocal"),
                    )
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_exists_too_many_triples_in_inner_is_rejected() -> None:
    """The inner EXISTS counts toward the global triple-pattern limit."""
    settings = Settings(max_triple_patterns=2)
    policy = SecurityPolicy.from_settings(settings)
    v = QueryPlanValidator(policy)
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("a"), object=_ex("Person")),
        FilterPattern(
            expression=ExistsExpr(
                patterns=[
                    TriplePattern(subject=Var(name="p"), predicate=_ex("b"), object=Var(name="x1")),
                    TriplePattern(subject=Var(name="p"), predicate=_ex("c"), object=Var(name="x2")),
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "too_many_triples" in codes


def test_filter_inside_not_exists_is_validated() -> None:
    """A FILTER nested inside NOT EXISTS that references an unbound var must error."""
    settings = Settings()
    policy = SecurityPolicy.from_settings(settings)
    v = QueryPlanValidator(policy)
    plan = _select(
        TriplePattern(subject=Var(name="p"), predicate=_ex("knows"), object=Var(name="q")),
        FilterPattern(
            expression=NotExistsExpr(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("blocked"),
                        object=Var(name="b"),
                    ),
                    FilterPattern(
                        expression=BinaryExpr(
                            op=">",
                            left=Var(name="zNotBound"),  # not bound anywhere
                            right=LiteralValue(value=1),
                        )
                    ),
                ]
            )
        ),
        projection=[Projection(var=Var(name="p"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "filter_var_unbound" in codes
