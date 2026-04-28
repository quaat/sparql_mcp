"""Default-prefix-override protection tests."""

from __future__ import annotations

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
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


# --- Default-prefix availability tests (Priority 1) -----------------------


def _plan_using(predicate: PrefixedName, *, ex_local: str = "Person") -> SelectPlan:
    return SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="s"))],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=predicate,
                object=PrefixedName(prefix="ex", local=ex_local),
            )
        ],
    )


def test_default_rdf_prefix_is_available_without_explicit_declaration(
    validator: QueryPlanValidator,
) -> None:
    plan = _plan_using(PrefixedName(prefix="rdf", local="type"))
    res = validator.validate(plan)
    assert res.ok, res.issues
    assert all(i.code != "unknown_prefix" for i in res.issues)


def test_default_rdfs_prefix_is_available_without_explicit_declaration(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="s"))],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=PrefixedName(prefix="rdfs", local="label"),
                object=Var(name="label"),
            )
        ],
    )
    res = validator.validate(plan)
    assert res.ok, res.issues
    assert all(i.code != "unknown_prefix" for i in res.issues)


def test_default_xsd_prefix_is_available_without_explicit_declaration(
    validator: QueryPlanValidator,
) -> None:
    from graph_mcp.models import FilterPattern, FunctionExpr, LiteralValue

    plan = SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="s"))],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=PrefixedName(prefix="ex", local="when"),
                object=Var(name="d"),
            ),
            FilterPattern(
                expression=FunctionExpr(
                    name="datatype",
                    args=[
                        LiteralValue(
                            value="2024-01-01",
                            datatype="http://www.w3.org/2001/XMLSchema#date",
                        ),
                    ],
                )
            ),
        ],
    )
    res = validator.validate(plan)
    # The plan does not explicitly declare xsd:; it must still validate.
    assert res.ok, res.issues
    # The literal also references an xsd datatype IRI directly, exercising
    # the "default prefix without explicit declaration" code path.


def test_unknown_non_default_prefix_is_still_rejected(
    validator: QueryPlanValidator,
) -> None:
    plan = SelectPlan(
        prefixes=[],  # No prefixes declared at all.
        projection=[Projection(var=Var(name="s"))],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=PrefixedName(prefix="totally_unknown", local="prop"),
                object=Var(name="o"),
            )
        ],
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "unknown_prefix" in codes


def test_redefining_builtin_prefix_is_rejected_by_default(
    validator: QueryPlanValidator,
) -> None:
    """Redefining a built-in to a different IRI must be rejected by default."""
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="rdf", iri="http://attacker.example/rdf#"),
    )
    res = validator.validate(plan)
    codes = {i.code for i in res.errors}
    assert "default_prefix_override" in codes


def test_redefining_builtin_prefix_with_same_iri_is_ok(
    validator: QueryPlanValidator,
) -> None:
    """Redeclaring a built-in with its canonical IRI is permitted."""
    plan = _select_with_prefixes(
        Prefix(prefix="ex", iri="http://example.org/"),
        Prefix(prefix="rdf", iri="http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
    )
    res = validator.validate(plan)
    assert res.ok, res.issues


def test_builtin_prefix_override_enabled_is_used_by_validator_and_renderer() -> None:
    """When override is permitted, both validator and renderer use the new IRI."""
    settings = Settings(allow_default_prefix_override=True)
    policy = SecurityPolicy.from_settings(settings)
    validator_ = QueryPlanValidator(policy)
    renderer_ = SparqlRenderer(policy)

    custom_rdf_iri = "http://example.org/custom-rdf#"
    plan = SelectPlan(
        prefixes=[
            Prefix(prefix="ex", iri="http://example.org/"),
            Prefix(prefix="rdf", iri=custom_rdf_iri),
        ],
        projection=[Projection(var=Var(name="s"))],
        where=[
            TriplePattern(
                subject=Var(name="s"),
                predicate=PrefixedName(prefix="rdf", local="type"),
                object=PrefixedName(prefix="ex", local="Thing"),
            )
        ],
    )
    res = validator_.validate(plan)
    assert res.ok, res.issues

    rendered = renderer_.render(plan)
    # The rendered PREFIX block must reflect the overridden IRI, not the default.
    assert f"PREFIX rdf: <{custom_rdf_iri}>" in rendered.sparql
    assert "http://www.w3.org/1999/02/22-rdf-syntax-ns#" not in rendered.sparql
