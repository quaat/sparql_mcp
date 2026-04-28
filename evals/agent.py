"""Planner implementations.

The deterministic planner is exercised in unit tests and the default eval run;
it does not call any LLM. The PydanticAI planner is opt-in and requires the
``pydantic-ai`` extra plus an API key.
"""

from __future__ import annotations

from typing import Any, Protocol

from evals.models import PlanGenerationOutput
from graph_mcp.graph.term_resolver import TermCandidate, TermResolver
from graph_mcp.models import (
    AggregateExpr,
    AskPlan,
    BinaryExpr,
    FilterPattern,
    GraphPattern,
    Iri,
    LiteralValue,
    MinusPattern,
    NotExistsExpr,
    OptionalPattern,
    Prefix,
    Projection,
    PropertyPath,
    PropertyPathOneOrMore,
    PropertyPathTerm,
    SelectPlan,
    SubqueryPattern,
    TriplePattern,
    UnionPattern,
    ValuesPattern,
    Var,
)
from graph_mcp.models.expressions import FunctionExpr

EX = Prefix(prefix="ex", iri="http://example.org/")
RDFS = Prefix(prefix="rdfs", iri="http://www.w3.org/2000/01/rdf-schema#")
RDF = Prefix(prefix="rdf", iri="http://www.w3.org/1999/02/22-rdf-syntax-ns#")
XSD = Prefix(prefix="xsd", iri="http://www.w3.org/2001/XMLSchema#")


def _ex(local: str):  # type: ignore[no-untyped-def]
    """Prefer prefixed names so the renderer emits readable, eval-friendly SPARQL."""
    from graph_mcp.models import PrefixedName
    return PrefixedName(prefix="ex", local=local)


def _rdfs(local: str):  # type: ignore[no-untyped-def]
    from graph_mcp.models import PrefixedName
    return PrefixedName(prefix="rdfs", local=local)


def _rdf(local: str):  # type: ignore[no-untyped-def]
    from graph_mcp.models import PrefixedName
    return PrefixedName(prefix="rdf", local=local)


class Planner(Protocol):
    def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlanGenerationOutput:
        ...


def _ex_iri(local: str):  # type: ignore[no-untyped-def]
    """Backwards-compatible alias: returns a prefixed name so the renderer
    emits ``ex:local`` rather than the absolute IRI form.
    """
    from graph_mcp.models import PrefixedName

    return PrefixedName(prefix="ex", local=local)


# --- Deterministic planner --------------------------------------------------


class DeterministicPlanner:
    """Hand-coded planner used for offline tests and the default eval mode.

    It pattern-matches on a small set of keywords in the question and returns
    a known-good QueryPlan. This is *not* an LLM; it exists so the rest of the
    pipeline (validator, renderer, executor, runner, metrics) can be exercised
    end-to-end without any API key.
    """

    def plan(
        self, question: str, *, resolver: TermResolver | None = None
    ) -> PlanGenerationOutput:
        q = question.lower()
        if "work" in q and "acme" in q:
            return self._who_works_for_acme(question)
        if "label" in q and "lang" in q:
            return self._labels_in_english(question)
        if "optional" in q and "filter inside" in q:
            return self._optional_with_inner_filter(question)
        if "label is optional" in q or (
            "optional" in q and "label" in q and "filter inside" not in q
        ):
            return self._people_with_optional_label(question)
        if "union" in q and "knows or works" in q:
            return self._union_knows_or_works(question)
        if "do not have a label" in q or "no label" in q:
            return self._people_without_label(question)
        if "minus" in q and "founded" in q:
            return self._people_minus_founders(question)
        if "knows^+" in q or "transitively knows" in q:
            return self._knows_one_or_more(question)
        if "at most one knows hop" in q:
            return self._knows_zero_or_one(question)
        if "values list" in q or "in list" in q:
            return self._values_alice_bob(question)
        if "computed" in q and "double" in q:
            return self._bind_age_doubled(question)
        if "count people per company" in q or "people per company" in q:
            return self._count_people_per_company(question)
        if "having" in q and "more than 1" in q:
            return self._companies_with_many(question)
        if "top-1 oldest" in q or "oldest person at each" in q:
            return self._top1_oldest_per_company(question)
        if "named graph" in q:
            return self._named_graph_query(question)
        if "joined after" in q:
            return self._joined_after_2019(question)
        if "age greater than 30" in q:
            return self._age_greater_than_30(question)
        if "ambiguous" in q:
            return self._ambiguous_clarification(question)
        if "drop" in q or "delete table" in q or "raw sparql" in q:
            return self._reject_unsafe(question)
        # default fallback: ASK whether anyone is a Person
        plan = AskPlan(
            prefixes=[EX],
            where=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                ),
            ],
        )
        return PlanGenerationOutput(
            question=question,
            assumptions=["fallback: ASK whether any ex:Person exists"],
            resolved_terms=[],
            plan=plan,
            confidence=0.3,
        )

    # --- individual cases (kept short and self-contained) -----------------

    def _who_works_for_acme(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="person"))],
            where=[
                TriplePattern(
                    subject=Var(name="person"),
                    predicate=_ex_iri("worksFor"),
                    object=_ex_iri("Acme"),
                ),
            ],
            limit=50,
        )
        return PlanGenerationOutput(
            question=q,
            plan=plan,
            confidence=0.95,
            resolved_terms=[
                TermCandidate(
                    mention="works for",
                    iri="http://example.org/worksFor",
                    prefixed_name="ex:worksFor",
                    kind="property",
                    label="works for",
                    score=1.0,
                    explanation="exact label match",
                ),
                TermCandidate(
                    mention="Acme",
                    iri="http://example.org/Acme",
                    prefixed_name="ex:Acme",
                    kind="individual",
                    label="Acme",
                    score=1.0,
                    explanation="exact label match",
                ),
            ],
        )

    def _labels_in_english(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, RDF, RDFS],
            projection=[Projection(var=Var(name="x")), Projection(var=Var(name="lbl"))],
            where=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_rdfs("label"),
                    object=Var(name="lbl"),
                ),
                FilterPattern(
                    expression=BinaryExpr(
                        op="=",
                        left=FunctionExpr(name="lang", args=[Var(name="lbl")]),
                        right=LiteralValue(value="en"),
                    ),
                ),
            ],
            limit=20,
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.9)

    def _optional_with_inner_filter(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, RDF, RDFS],
            projection=[Projection(var=Var(name="p")), Projection(var=Var(name="lbl"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                ),
                OptionalPattern(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=_rdfs("label"),
                            object=Var(name="lbl"),
                        ),
                        FilterPattern(
                            expression=BinaryExpr(
                                op="=",
                                left=FunctionExpr(name="lang", args=[Var(name="lbl")]),
                                right=LiteralValue(value="en"),
                            ),
                        ),
                    ],
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _people_with_optional_label(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, RDF, RDFS],
            projection=[Projection(var=Var(name="p")), Projection(var=Var(name="lbl"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                ),
                OptionalPattern(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="p"),
                            predicate=_rdfs("label"),
                            object=Var(name="lbl"),
                        )
                    ]
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.9)

    def _union_knows_or_works(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="a")), Projection(var=Var(name="b"))],
            where=[
                UnionPattern(
                    branches=[
                        [
                            TriplePattern(
                                subject=Var(name="a"),
                                predicate=_ex_iri("knows"),
                                object=Var(name="b"),
                            )
                        ],
                        [
                            TriplePattern(
                                subject=Var(name="a"),
                                predicate=_ex_iri("worksFor"),
                                object=Var(name="b"),
                            )
                        ],
                    ]
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _people_without_label(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, RDF, RDFS],
            projection=[Projection(var=Var(name="p"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                ),
                FilterPattern(
                    expression=NotExistsExpr(
                        patterns=[
                            TriplePattern(
                                subject=Var(name="p"),
                                predicate=_rdfs("label"),
                                object=Var(name="anyLabel"),
                            )
                        ]
                    ),
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.9)

    def _people_minus_founders(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, RDF],
            projection=[Projection(var=Var(name="p"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                ),
                MinusPattern(
                    patterns=[
                        TriplePattern(
                            subject=Var(name="company"),
                            predicate=_ex_iri("foundedBy"),
                            object=Var(name="p"),
                        )
                    ]
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _knows_one_or_more(self, q: str) -> PlanGenerationOutput:
        path: PropertyPath = PropertyPathOneOrMore(
            operand=PropertyPathTerm(iri=_ex_iri("knows"))
        )
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="b"))],
            where=[
                TriplePattern(
                    subject=_ex_iri("alice"),
                    predicate=path,
                    object=Var(name="b"),
                )
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.8)

    def _knows_zero_or_one(self, q: str) -> PlanGenerationOutput:
        # Bounded path: alternation of direct or one-hop, expressed as union to stay within policy.
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="b"))],
            where=[
                UnionPattern(
                    branches=[
                        [
                            TriplePattern(
                                subject=_ex_iri("alice"),
                                predicate=_ex_iri("knows"),
                                object=Var(name="b"),
                            )
                        ],
                        [
                            TriplePattern(
                                subject=_ex_iri("alice"),
                                predicate=_ex_iri("knows"),
                                object=Var(name="mid"),
                            ),
                            TriplePattern(
                                subject=Var(name="mid"),
                                predicate=_ex_iri("knows"),
                                object=Var(name="b"),
                            ),
                        ],
                    ]
                )
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.7)

    def _values_alice_bob(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="p"))],
            where=[
                ValuesPattern(
                    variables=[Var(name="p")],
                    rows=[[_ex_iri("alice")], [_ex_iri("bob")]],
                ),
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("worksFor"),
                    object=Var(name="company"),
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.9)

    def _bind_age_doubled(self, q: str) -> PlanGenerationOutput:
        from graph_mcp.models.patterns import BindPattern

        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="p")), Projection(var=Var(name="dbl"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("age"),
                    object=Var(name="age"),
                ),
                BindPattern(
                    expression=BinaryExpr(
                        op="*", left=Var(name="age"), right=LiteralValue(value=2)
                    ),
                    var=Var(name="dbl"),
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _count_people_per_company(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[
                Projection(var=Var(name="company")),
                Projection(
                    expression=AggregateExpr(
                        function="count", expression=Var(name="p"), distinct=True
                    ),
                    alias=Var(name="n"),
                ),
            ],
            group_by=[Var(name="company")],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("worksFor"),
                    object=Var(name="company"),
                )
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _companies_with_many(self, q: str) -> PlanGenerationOutput:
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
                    predicate=_ex_iri("worksFor"),
                    object=Var(name="company"),
                )
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _top1_oldest_per_company(self, q: str) -> PlanGenerationOutput:
        # Top-1 per group via subquery: SELECT max(age) AS maxAge GROUP BY company
        sub = SelectPlan(
            projection=[
                Projection(var=Var(name="company")),
                Projection(
                    expression=AggregateExpr(function="max", expression=Var(name="age")),
                    alias=Var(name="maxAge"),
                ),
            ],
            group_by=[Var(name="company")],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("worksFor"),
                    object=Var(name="company"),
                ),
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("age"),
                    object=Var(name="age"),
                ),
            ],
        )
        plan = SelectPlan(
            prefixes=[EX],
            projection=[
                Projection(var=Var(name="p")),
                Projection(var=Var(name="company")),
                Projection(var=Var(name="age")),
            ],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("worksFor"),
                    object=Var(name="company"),
                ),
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("age"),
                    object=Var(name="age"),
                ),
                SubqueryPattern(select=sub),
                FilterPattern(
                    expression=BinaryExpr(
                        op="=", left=Var(name="age"), right=Var(name="maxAge")
                    )
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.7)

    def _named_graph_query(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="s"))],
            where=[
                GraphPattern(
                    graph=_ex_iri("graph1"),
                    patterns=[
                        TriplePattern(
                            subject=Var(name="s"),
                            predicate=Iri(
                                value="http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
                            ),
                            object=_ex_iri("Person"),
                        )
                    ],
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.7)

    def _joined_after_2019(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, XSD],
            projection=[Projection(var=Var(name="p"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("joined"),
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
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.85)

    def _age_greater_than_30(self, q: str) -> PlanGenerationOutput:
        plan = SelectPlan(
            prefixes=[EX, XSD],
            projection=[Projection(var=Var(name="p"))],
            where=[
                TriplePattern(
                    subject=Var(name="p"),
                    predicate=_ex_iri("age"),
                    object=Var(name="age"),
                ),
                FilterPattern(
                    expression=BinaryExpr(
                        op=">",
                        left=Var(name="age"),
                        right=LiteralValue(value=30),
                    )
                ),
            ],
        )
        return PlanGenerationOutput(question=q, plan=plan, confidence=0.9)

    def _ambiguous_clarification(self, q: str) -> PlanGenerationOutput:
        plan = AskPlan(prefixes=[EX], where=[
            TriplePattern(
                subject=Var(name="x"),
                predicate=_rdf("type"),
                object=_ex_iri("Person"),
            )
        ])
        return PlanGenerationOutput(
            question=q,
            plan=plan,
            confidence=0.2,
            needs_clarification=True,
            clarification_question=(
                "The question mentions an entity that does not match any known "
                "schema term. Can you clarify which class or individual you mean?"
            ),
        )

    def _reject_unsafe(self, q: str) -> PlanGenerationOutput:
        # Return a deliberately unsafe plan: a SERVICE pattern that the
        # validator will reject when no allowlist permits it.
        from graph_mcp.models.patterns import ServicePattern

        plan = SelectPlan(
            prefixes=[EX],
            projection=[Projection(var=Var(name="x"))],
            where=[
                ServicePattern(
                    endpoint=Iri(value="http://malicious.example/sparql"),
                    patterns=[
                        TriplePattern(
                            subject=Var(name="x"),
                            predicate=_rdf("type"),
                            object=_ex_iri("Person"),
                        )
                    ],
                )
            ],
        )
        return PlanGenerationOutput(
            question=q,
            plan=plan,
            confidence=0.0,
            assumptions=[
                "The user asked for a destructive or out-of-policy operation; "
                "this plan is intentionally constructed to be rejected by the validator."
            ],
        )


# --- PydanticAI planner (optional) -----------------------------------------


def build_pydantic_ai_planner(model: str) -> Planner:
    """Construct a PydanticAI-backed planner. Imports are deferred so that the
    optional dependency does not block test execution."""
    try:
        from pydantic_ai import Agent  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "pydantic-ai is required for the LLM planner; "
            "install with `pip install graph-mcp[ai]`"
        ) from exc

    system = (
        "You produce strict QueryPlan IR objects (NEVER raw SPARQL) for a graph "
        "database. Use only schema terms supplied in context. Prefer precise "
        "filters over broad scans. Use OPTIONAL only for optional information; "
        "place FILTER inside OPTIONAL when the filter should only constrain "
        "optional bindings. Use FILTER NOT EXISTS for absence-of-pattern "
        "semantics. Use subqueries for top-N and grouped aggregation. Always "
        "add a reasonable LIMIT for exploratory SELECT queries. Set "
        "needs_clarification=true only when the question cannot be safely "
        "mapped to known schema terms."
    )
    agent: Any = Agent(model=model, output_type=PlanGenerationOutput, system_prompt=system)

    class _Wrapper:
        def plan(
            self, question: str, *, resolver: TermResolver | None = None
        ) -> PlanGenerationOutput:
            res = agent.run_sync(question)
            return res.output

    return _Wrapper()
