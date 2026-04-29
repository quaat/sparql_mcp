"""Planner implementations.

The deterministic planner is exercised in unit tests and the default eval run;
it does not call any LLM. The PydanticAI planner is opt-in and requires the
``pydantic-ai`` extra plus an API key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import TypeAdapter

from evals.models import PlanGenerationOutput
from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.graph.schema_discovery import SchemaProvider, SchemaSnapshot
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
    def plan(
        self, question: str, *, resolver: TermResolver | None = None
    ) -> PlanGenerationOutput: ...


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

    def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlanGenerationOutput:
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
        path: PropertyPath = PropertyPathOneOrMore(operand=PropertyPathTerm(iri=_ex_iri("knows")))
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
                    expression=BinaryExpr(op="=", left=Var(name="age"), right=Var(name="maxAge"))
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
                            predicate=Iri(value="http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
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
        plan = AskPlan(
            prefixes=[EX],
            where=[
                TriplePattern(
                    subject=Var(name="x"),
                    predicate=_rdf("type"),
                    object=_ex_iri("Person"),
                )
            ],
        )
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


# --- Planner deps + diagnostics + repair workflow ---------------------------


@dataclass
class PlannerDeps:
    """Context object passed to the LLM-backed planner workflow.

    Holds everything the workflow needs to produce, validate, and render a
    plan without reaching for module globals. Tests can build a fake
    :class:`PlannerDeps` and exercise the workflow with a stub agent.
    """

    schema: SchemaProvider
    resolver: TermResolver
    validator: QueryPlanValidator
    renderer: object  # SparqlRenderer; importing here would create a cycle.
    policy: object  # SecurityPolicy; same reason.
    max_repair_attempts: int = 2


class PlannerDiagnostics:
    """Mutable diagnostics collector for one ``plan(question)`` call."""

    def __init__(self) -> None:
        from graph_mcp.models import ValidationIssue  # local import to keep cycles small

        self._ValidationIssue = ValidationIssue
        self.repair_attempts: int = 0
        self.validation_errors_seen: list[ValidationIssue] = []
        self.final_validation_ok: bool = False
        self.rendered_sparql: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "repair_attempts": self.repair_attempts,
            "validation_errors_seen": [e.model_dump() for e in self.validation_errors_seen],
            "final_validation_ok": self.final_validation_ok,
            "rendered_sparql": self.rendered_sparql,
        }


def run_planner_workflow(
    deps: PlannerDeps,
    question: str,
    *,
    generate: Callable[[str], PlanGenerationOutput],
    diagnostics: PlannerDiagnostics | None = None,
) -> tuple[PlanGenerationOutput, PlannerDiagnostics]:
    """Run the generate → validate → repair loop and return the final output.

    Workflow:

    1. Generate an initial plan.
    2. If the planner asks for clarification, return immediately.
    3. Validate; if it passes, render and return.
    4. Otherwise, run up to ``deps.max_repair_attempts`` repair iterations:
       feed the validator's structured errors back to the agent and ask
       for a corrected plan. Each repair attempt counts toward
       ``diagnostics.repair_attempts``.
    """
    diag = diagnostics or PlannerDiagnostics()
    output = generate(question)

    # Clarification short-circuit.
    if output.needs_clarification:
        diag.final_validation_ok = False
        return output, diag

    def _try_render() -> bool:
        try:
            rendered = deps.renderer.render(output.plan)  # type: ignore[attr-defined]
            diag.rendered_sparql = rendered.sparql
        except Exception:  # pragma: no cover - render shouldn't fail on valid plan
            diag.rendered_sparql = None
        return True

    # Initial validation.
    result = deps.validator.validate(output.plan)
    if result.ok:
        diag.final_validation_ok = True
        _try_render()
        return output, diag
    diag.validation_errors_seen.extend(result.errors)

    # Repair iterations.
    for repair in range(deps.max_repair_attempts):
        diag.repair_attempts = repair + 1
        feedback = (
            "Your previous plan failed validation with these errors:\n"
            + "\n".join(f"- {e.code}: {e.message}" for e in result.errors)
            + "\n\nProduce a corrected PlanGenerationOutput."
        )
        output = generate(question + "\n\n" + feedback)
        if output.needs_clarification:
            diag.final_validation_ok = False
            return output, diag
        result = deps.validator.validate(output.plan)
        if result.ok:
            diag.final_validation_ok = True
            _try_render()
            return output, diag
        diag.validation_errors_seen.extend(result.errors)

    diag.final_validation_ok = False
    return output, diag


# --- PydanticAI planner (optional) -----------------------------------------
#
# Scope note (Priority 8): the PydanticAI planner here is a benchmarking
# harness for the evals runner, not a production planner. It injects schema
# context (classes, properties, prefixes, named graphs, individuals) into
# the system prompt and runs the agent in a single-output mode rather than
# wiring a PydanticAI ``@agent.tool`` for term resolution.
#
# Tool-backed term resolution is **out of scope** for the MCP server
# package: the server already exposes ``resolve_terms`` as an MCP tool, so
# host agents (Claude Code, custom agents) can invoke it directly. Adding a
# parallel PydanticAI tool here would duplicate that surface inside the
# evals harness without changing what production hosts see.


_SYSTEM_PROMPT = """\
You produce strict QueryPlan IR objects (NEVER raw SPARQL) for a graph
database. Your output MUST validate against the PlanGenerationOutput Pydantic
schema.

Workflow guarantees:

- Use ONLY the schema terms given in this prompt. Do not invent prefixes,
  classes, properties, named graphs, or individuals.
- Prefer precise filters over broad graph scans.
- Use OPTIONAL only for genuinely optional information. Place a FILTER inside
  OPTIONAL when the filter should only constrain optional bindings.
- Use FILTER NOT EXISTS for absence-of-pattern semantics. Use MINUS only when
  it is specifically more appropriate.
- Use subqueries for top-N, grouped aggregation, and nested constraints.
- Use aggregates only with a valid GROUP BY.
- Always add a reasonable LIMIT for exploratory SELECT queries.
- Set needs_clarification=true only when the question cannot be safely
  mapped to known schema terms — and supply a concrete clarification_question.

Hard prohibitions:

- NEVER write raw SPARQL.
- NEVER use unsupported SPARQL features (DESCRIBE, SPARQL Update,
  arbitrary SERVICE).
- NEVER use unbounded property paths without explicit justification.

The available schema and the QueryPlan IR JSON schema are appended below.
"""


@dataclass
class PydanticAIPlannerConfig:
    model: Any
    """Model identifier string (e.g. ``anthropic:claude-sonnet-4-6``) or a
    pre-built ``pydantic_ai.models.Model`` instance (e.g. an Azure-backed
    ``OpenAIChatModel``)."""

    schema: SchemaSnapshot | None = None
    """Schema snapshot to expose to the planner."""

    examples: list[dict[str, Any]] = field(default_factory=list)
    """Plan examples to include in the system prompt."""

    max_repair_attempts: int = 2
    """Number of times to feed validator errors back and ask for a repair."""


def _format_schema_for_prompt(snap: SchemaSnapshot) -> str:
    """Render a schema snapshot as compact, LLM-friendly JSON."""
    payload = {
        "prefixes": snap.prefixes,
        "classes": [c.model_dump(exclude_none=True) for c in snap.classes],
        "properties": [p.model_dump(exclude_none=True) for p in snap.properties],
        "named_graphs": [g.model_dump(exclude_none=True) for g in snap.named_graphs],
        "individuals": [i.model_dump(exclude_none=True) for i in snap.individuals],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _build_full_system_prompt(cfg: PydanticAIPlannerConfig) -> str:
    schema_block = _format_schema_for_prompt(cfg.schema or SchemaSnapshot())
    qp_schema = json.dumps(
        TypeAdapter(PlanGenerationOutput).json_schema(), indent=2, sort_keys=True
    )
    examples_block = ""
    if cfg.examples:
        examples_block = "\n\nExamples (for reference):\n" + json.dumps(
            cfg.examples, indent=2, sort_keys=True
        )
    return (
        _SYSTEM_PROMPT
        + "\n\n## Available schema\n```json\n"
        + schema_block
        + "\n```\n\n## Output schema (PlanGenerationOutput)\n```json\n"
        + qp_schema
        + "\n```"
        + examples_block
    )


def build_planner_from_callable(
    deps: PlannerDeps,
    generate: Callable[[str], PlanGenerationOutput],
) -> Planner:
    """Build a :class:`Planner` from any plan-generating callable.

    This is the main extension point: pass any function that returns a
    :class:`PlanGenerationOutput` for a given question and the workflow
    handles validation + repair + diagnostics. Production code uses the
    PydanticAI agent; tests use stub callables.
    """

    class _WorkflowPlanner:
        def __init__(self) -> None:
            self.last_repair_attempted: bool = False
            self.last_repair_succeeded: bool = False
            self.last_diagnostics: PlannerDiagnostics | None = None

        def plan(
            self, question: str, *, resolver: TermResolver | None = None
        ) -> PlanGenerationOutput:
            self.last_repair_attempted = False
            self.last_repair_succeeded = False
            output, diag = run_planner_workflow(deps, question, generate=generate)
            self.last_repair_attempted = diag.repair_attempts > 0
            self.last_repair_succeeded = diag.final_validation_ok and self.last_repair_attempted
            self.last_diagnostics = diag
            return output

    return _WorkflowPlanner()


def build_pydantic_ai_planner(
    model: Any,
    *,
    schema: SchemaProvider | None = None,
    examples: list[dict[str, Any]] | None = None,
    max_repair_attempts: int = 2,
    validator: QueryPlanValidator | None = None,
    renderer: object | None = None,
    policy: object | None = None,
    resolver: TermResolver | None = None,
) -> Planner:
    """Construct a PydanticAI-backed planner.

    ``model`` may be a model identifier string or a pre-built
    ``pydantic_ai.models.Model`` instance — useful for non-default providers
    like Azure OpenAI, where the endpoint and credentials are wired through
    a custom provider rather than a string identifier.

    Imports are deferred so that the optional ``pydantic-ai`` dependency does
    not block test execution.
    """
    try:
        from pydantic_ai import Agent
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "pydantic-ai is required for the LLM planner; install with `pip install graph-mcp[ai]`"
        ) from exc

    snap = schema.snapshot() if schema is not None else SchemaSnapshot()
    cfg = PydanticAIPlannerConfig(
        model=model,
        schema=snap,
        examples=examples or [],
        max_repair_attempts=max_repair_attempts,
    )
    system = _build_full_system_prompt(cfg)
    agent: Any = Agent(model=model, output_type=PlanGenerationOutput, system_prompt=system)

    if validator is None or renderer is None or policy is None or resolver is None:
        # Backwards-compatible degraded mode: no workflow, no diagnostics.
        class _LegacyLLMPlanner:
            def __init__(self) -> None:
                self.last_repair_attempted: bool = False
                self.last_repair_succeeded: bool = False

            def plan(
                self, question: str, *, resolver: TermResolver | None = None
            ) -> PlanGenerationOutput:
                return agent.run_sync(question).output  # type: ignore[no-any-return]

        return _LegacyLLMPlanner()

    deps = PlannerDeps(
        schema=schema if schema is not None else _ImmutableStaticProvider(snap),
        resolver=resolver,
        validator=validator,
        renderer=renderer,
        policy=policy,
        max_repair_attempts=max_repair_attempts,
    )

    def generate(prompt_text: str) -> PlanGenerationOutput:
        return agent.run_sync(prompt_text).output  # type: ignore[no-any-return]

    return build_planner_from_callable(deps, generate)


class _ImmutableStaticProvider:
    """Tiny provider wrapper used when only a snapshot is available."""

    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> SchemaSnapshot:
        return self._snapshot
