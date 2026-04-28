"""Tests for the SPARQL renderer."""

from __future__ import annotations

from graph_mcp.compiler import SparqlRenderer
from graph_mcp.models import (
    AggregateExpr,
    AskPlan,
    BinaryExpr,
    BindPattern,
    ConstructPlan,
    FilterPattern,
    Iri,
    LiteralValue,
    MinusPattern,
    NotExistsExpr,
    OptionalPattern,
    Prefix,
    PrefixedName,
    Projection,
    PropertyPathOneOrMore,
    PropertyPathTerm,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    UnionPattern,
    ValuesPattern,
    Var,
)

EX = Prefix(prefix="ex", iri="http://example.org/")
XSD = Prefix(prefix="xsd", iri="http://www.w3.org/2001/XMLSchema#")


def _ex(local: str) -> PrefixedName:
    return PrefixedName(prefix="ex", local=local)


def test_simple_select(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=_ex("Acme"),
            )
        ],
    )
    out = renderer.render(plan)
    assert out.query_type == "select"
    assert "SELECT ?p" in out.sparql
    assert "ex:worksFor ex:Acme" in out.sparql
    assert "LIMIT 100" in out.sparql  # default applied


def test_optional_block(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("a"), object=_ex("Person")
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
    assert "OPTIONAL {" in out.sparql


def test_union_block(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="x"))],
        where=[
            UnionPattern(
                branches=[
                    [
                        TriplePattern(
                            subject=Var(name="x"),
                            predicate=_ex("a"),
                            object=_ex("Person"),
                        )
                    ],
                    [
                        TriplePattern(
                            subject=Var(name="x"),
                            predicate=_ex("a"),
                            object=_ex("Company"),
                        )
                    ],
                ]
            )
        ],
    )
    out = renderer.render(plan)
    assert "UNION {" in out.sparql


def test_filter_not_exists(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("a"), object=_ex("Person")
            ),
            FilterPattern(
                expression=NotExistsExpr(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=_ex("blocked"),
                            object=Var(name="any"),
                        )
                    ]
                )
            ),
        ],
    )
    out = renderer.render(plan)
    assert "NOT EXISTS" in out.sparql


def test_property_path(permissive_policy) -> None:  # type: ignore[no-untyped-def]
    r = SparqlRenderer(permissive_policy)
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="b"))],
        where=[
            TriplePattern(
                subject=_ex("alice"),
                predicate=PropertyPathOneOrMore(
                    operand=PropertyPathTerm(iri=_ex("knows"))
                ),
                object=Var(name="b"),
            )
        ],
    )
    out = r.render(plan)
    assert "ex:knows+" in out.sparql


def test_aggregation_with_having(renderer: SparqlRenderer) -> None:
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
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=Var(name="company"),
            )
        ],
    )
    out = renderer.render(plan)
    assert "GROUP BY ?company" in out.sparql
    assert "HAVING" in out.sparql
    assert "COUNT" in out.sparql


def test_subquery(renderer: SparqlRenderer) -> None:
    sub = SelectPlan(
        projection=[Projection(var=Var(name="x"))],
        where=[
            TriplePattern(
                subject=Var(name="x"), predicate=_ex("a"), object=_ex("Person")
            )
        ],
    )
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="x"))],
        where=[SubqueryPattern(select=sub)],
    )
    out = renderer.render(plan)
    assert "SELECT" in out.sparql


def test_named_graph(renderer: SparqlRenderer) -> None:
    from graph_mcp.models.patterns import GraphPattern

    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="x"))],
        where=[
            GraphPattern(
                graph=_ex("g1"),
                patterns=[
                    TriplePattern(
                        subject=Var(name="x"),
                        predicate=_ex("knows"),
                        object=Var(name="y"),
                    )
                ],
            )
        ],
    )
    out = renderer.render(plan)
    assert "GRAPH ex:g1 {" in out.sparql


def test_values(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            ValuesPattern(
                variables=[Var(name="p")],
                rows=[[_ex("alice")], [_ex("bob")]],
            ),
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("worksFor"), object=Var(name="c")
            ),
        ],
    )
    out = renderer.render(plan)
    assert "VALUES ?p" in out.sparql
    assert "ex:alice" in out.sparql


def test_bind(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p")), Projection(var=Var(name="d"))],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("age"), object=Var(name="a")
            ),
            BindPattern(
                expression=BinaryExpr(
                    op="*", left=Var(name="a"), right=LiteralValue(value=2)
                ),
                var=Var(name="d"),
            ),
        ],
    )
    out = renderer.render(plan)
    assert "BIND (" in out.sparql
    assert "AS ?d" in out.sparql


def test_minus(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("a"), object=_ex("Person")
            ),
            MinusPattern(
                patterns=[
                    TriplePattern(
                        subject=Var(name="p"),
                        predicate=_ex("excluded"),
                        object=Var(name="x"),
                    )
                ]
            ),
        ],
    )
    out = renderer.render(plan)
    assert "MINUS {" in out.sparql


def test_escaped_string_literal(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("name"),
                object=LiteralValue(value='He said "hi"\nto her'),
            )
        ],
    )
    out = renderer.render(plan)
    assert '\\"hi\\"' in out.sparql
    assert "\\n" in out.sparql


def test_datatype_compaction(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX, XSD],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("joined"),
                object=Var(name="d"),
            ),
            FilterPattern(
                expression=BinaryExpr(
                    op=">",
                    left=Var(name="d"),
                    right=LiteralValue(
                        value="2019-01-01",
                        datatype="http://www.w3.org/2001/XMLSchema#date",
                    ),
                )
            ),
        ],
    )
    out = renderer.render(plan)
    assert "^^xsd:date" in out.sparql


def test_ask_render(renderer: SparqlRenderer) -> None:
    plan = AskPlan(
        prefixes=[EX],
        where=[
            TriplePattern(
                subject=_ex("alice"),
                predicate=_ex("worksFor"),
                object=_ex("Acme"),
            )
        ],
    )
    out = renderer.render(plan)
    assert out.query_type == "ask"
    assert out.sparql.startswith("PREFIX")
    assert "ASK WHERE" in out.sparql


def test_construct_render(renderer: SparqlRenderer) -> None:
    plan = ConstructPlan(
        prefixes=[EX],
        template=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("name"), object=Var(name="n")
            )
        ],
        where=[
            TriplePattern(
                subject=Var(name="p"), predicate=_ex("name"), object=Var(name="n")
            )
        ],
    )
    out = renderer.render(plan)
    assert out.query_type == "construct"
    assert "CONSTRUCT {" in out.sparql


def test_renderer_is_deterministic(renderer: SparqlRenderer) -> None:
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=_ex("worksFor"),
                object=_ex("Acme"),
            )
        ],
    )
    a = renderer.render(plan).sparql
    b = renderer.render(plan).sparql
    assert a == b


def test_iri_compaction_in_predicate(renderer: SparqlRenderer) -> None:
    """Even when input is a full IRI, the renderer keeps it absolute (we don't auto-compact)."""
    plan = SelectPlan(
        prefixes=[EX],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=Iri(value="http://example.org/worksFor"),
                object=_ex("Acme"),
            )
        ],
    )
    out = renderer.render(plan)
    # Triple predicates we render exactly as given; only literal datatypes are auto-compacted.
    assert "<http://example.org/worksFor>" in out.sparql
