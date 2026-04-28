"""Property-path predicate-resolution and allowlist tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    ExistsExpr,
    FilterPattern,
    Iri,
    Prefix,
    PrefixedName,
    Projection,
    PropertyPathAlt,
    PropertyPathOneOrMore,
    PropertyPathSeq,
    PropertyPathTerm,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy

EX = Prefix(prefix="ex", iri="http://example.org/")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def _select_with_path(path) -> SelectPlan:  # type: ignore[no-untyped-def]
    return SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="b"))],
        where=[
            TriplePattern(
                subject=_ex("alice"),
                predicate=path,
                object=Var(name="b"),
            )
        ],
    )


def test_unknown_prefix_inside_property_path_is_rejected(
    permissive_policy: SecurityPolicy,
) -> None:
    """Allowing unbounded paths must not bypass prefix resolution."""
    v = QueryPlanValidator(permissive_policy)
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="b"))],
        where=[
            TriplePattern(
                subject=_ex("alice"),
                predicate=PropertyPathOneOrMore(
                    operand=PropertyPathTerm(iri=PrefixedName(prefix="missing", local="thing"))
                ),
                object=Var(name="b"),
            )
        ],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_allowed_path_predicate_passes_when_allowlist_configured() -> None:
    settings = Settings(
        allow_unbounded_paths=True,
        allowed_path_predicates="http://example.org/knows",  # type: ignore[arg-type]
    )
    v = QueryPlanValidator(SecurityPolicy.from_settings(settings))
    plan = _select_with_path(PropertyPathOneOrMore(operand=PropertyPathTerm(iri=_ex("knows"))))
    res = v.validate(plan)
    assert res.ok, res.issues


def test_disallowed_path_predicate_fails_when_allowlist_configured() -> None:
    settings = Settings(
        allow_unbounded_paths=True,
        allowed_path_predicates="http://example.org/knows",  # type: ignore[arg-type]
    )
    v = QueryPlanValidator(SecurityPolicy.from_settings(settings))
    plan = _select_with_path(
        PropertyPathOneOrMore(
            # ex:worksFor is NOT in the allowlist.
            operand=PropertyPathTerm(iri=_ex("worksFor"))
        )
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "path_predicate_not_allowed" in codes


def test_path_predicate_allowlist_applies_to_seq_and_alt() -> None:
    settings = Settings(
        allow_unbounded_paths=True,
        allowed_path_predicates="http://example.org/knows",  # type: ignore[arg-type]
    )
    v = QueryPlanValidator(SecurityPolicy.from_settings(settings))
    plan = _select_with_path(
        PropertyPathSeq(
            elements=[
                PropertyPathTerm(iri=_ex("knows")),  # OK
                PropertyPathAlt(
                    elements=[
                        PropertyPathTerm(iri=_ex("worksFor")),  # not allowlisted
                        PropertyPathTerm(iri=_ex("knows")),
                    ]
                ),
            ]
        )
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "path_predicate_not_allowed" in codes


def test_property_path_inside_exists_is_validated(
    permissive_policy: SecurityPolicy,
) -> None:
    """Path predicates inside FILTER EXISTS must be resolved."""
    v = QueryPlanValidator(permissive_policy)
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("knows"),
                object=Var(name="q"),
            ),
            FilterPattern(
                expression=ExistsExpr(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=PropertyPathOneOrMore(
                                operand=PropertyPathTerm(
                                    iri=PrefixedName(prefix="ghost", local="x")
                                )
                            ),
                            object=Var(name="z"),
                        )
                    ]
                )
            ),
        ],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_property_path_inside_subquery_is_validated(
    permissive_policy: SecurityPolicy,
) -> None:
    v = QueryPlanValidator(permissive_policy)
    sub = SelectPlan(
        projection=[Projection(var=Var(name="b"))],
        where=[
            TriplePattern(
                subject=_ex("alice"),
                predicate=PropertyPathOneOrMore(
                    operand=PropertyPathTerm(iri=PrefixedName(prefix="ghost", local="x"))
                ),
                object=Var(name="b"),
            )
        ],
    )
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="b"))],
        where=[SubqueryPattern(select=sub)],
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_path_predicate_with_absolute_iri_does_not_need_prefix() -> None:
    """An absolute IRI in a property path must work with no prefix declared."""
    settings = Settings(allow_unbounded_paths=True)
    v = QueryPlanValidator(SecurityPolicy.from_settings(settings))
    plan = SelectPlan(
        prefixes=[],
        projection=[Projection(var=Var(name="b"))],
        where=[
            TriplePattern(
                subject=Iri(value="http://example.org/alice"),
                predicate=PropertyPathOneOrMore(
                    operand=PropertyPathTerm(iri=Iri(value="http://example.org/knows"))
                ),
                object=Var(name="b"),
            )
        ],
    )
    res = v.validate(plan)
    assert res.ok, res.issues
