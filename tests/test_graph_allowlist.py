"""Strict named-graph allowlist enforcement tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    GraphPattern,
    Iri,
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
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


def _validator_with_allowlist(*allowed: str) -> QueryPlanValidator:
    s = Settings(allowed_graphs=",".join(allowed) if allowed else "")  # type: ignore[arg-type]
    return QueryPlanValidator(SecurityPolicy.from_settings(s))


def test_graph_variable_rejected_when_allowlist_configured() -> None:
    v = _validator_with_allowlist("http://example.org/g1")
    plan = _select(
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_variable_not_allowed" in codes


def test_graph_variable_allowed_without_allowlist() -> None:
    v = _validator_with_allowlist()  # no allowlist
    plan = _select(
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_graph_variable_allowed_with_values_restricted_to_allowlist() -> None:
    v = _validator_with_allowlist("http://example.org/g1", "http://example.org/g2")
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
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_graph_variable_rejected_with_values_outside_allowlist() -> None:
    v = _validator_with_allowlist("http://example.org/g1")
    plan = _select(
        ValuesPattern(
            variables=[Var(name="g")],
            rows=[
                [Iri(value="http://example.org/g1")],
                [Iri(value="http://other.example/forbidden")],
            ],
        ),
        GraphPattern(
            graph=Var(name="g"),
            patterns=[
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_values_not_allowed" in codes


def test_graph_iri_in_allowlist_passes() -> None:
    v = _validator_with_allowlist("http://example.org/g1")
    plan = _select(
        GraphPattern(
            graph=Iri(value="http://example.org/g1"),
            patterns=[
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    assert res.ok, res.issues


def test_graph_iri_outside_allowlist_rejected() -> None:
    v = _validator_with_allowlist("http://example.org/g1")
    plan = _select(
        GraphPattern(
            graph=Iri(value="http://other.example/g"),
            patterns=[
                TriplePattern(
                    subject=Var(name="s"),
                    predicate=_ex("knows"),
                    object=Var(name="o"),
                )
            ],
        ),
        projection=[Projection(var=Var(name="s"))],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "graph_not_allowed" in codes
