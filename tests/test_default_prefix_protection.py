"""Default-prefix-override protection tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy


def _select_with_prefixes(*prefixes: Prefix) -> SelectPlan:
    return SelectPlan(
        prefixes=list(prefixes),
        projection=[Projection(var=Var(name="x"))],
        where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=PrefixedName(prefix="ex", local="knows"),
                object=Var(name="y"),
            )
        ],
    )


def test_redefining_rdf_prefix_is_rejected_by_default(
    validator: QueryPlanValidator,
) -> None:
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        # Try to redefine the built-in rdf: prefix.
        Prefix(prefix="rdf", iri="http://attacker.example/rdf#"),
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "default_prefix_override" in codes


def test_redefining_xsd_prefix_is_rejected_by_default(
    validator: QueryPlanValidator,
) -> None:
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="xsd", iri="http://attacker.example/xsd#"),
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "default_prefix_override" in codes


def test_redefining_custom_prefix_conflict_is_rejected(
    validator: QueryPlanValidator,
) -> None:
    """Two declarations of the same prefix with different IRIs is still a conflict."""
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="ex", iri="http://different.example/"),
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "prefix_conflict" in codes


def test_redefining_custom_prefix_with_same_iri_is_ok(
    validator: QueryPlanValidator,
) -> None:
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="ex", iri="http://example.org/"),
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_default_prefix_override_can_be_enabled_if_policy_allows() -> None:
    settings = Settings(allow_default_prefix_override=True)
    policy = SecurityPolicy.from_settings(settings)
    v = QueryPlanValidator(policy)
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="rdf", iri="http://attacker.example/rdf#"),
    )
    res = v.validate(plan)
    codes = {i.code for i in res.errors}
    assert "default_prefix_override" not in codes


def test_redefining_rdf_with_same_iri_is_ok(validator: QueryPlanValidator) -> None:
    """Re-declaring a built-in with the same IRI is harmless."""
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(
            prefix="rdf",
            iri="http://www.w3.org/1999/02/22-rdf-syntax-ns#",  # canonical
        ),
    )
    res = validator.validate(plan)
    assert res.ok, res.issues
