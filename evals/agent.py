"""Planner implementations.

The deterministic planner is exercised in unit tests and the default eval run;
it does not call any LLM. The PydanticAI planner is opt-in and requires the
``pydantic-ai`` extra plus an API key.

The planner workflow is wired through :class:`PlannerDeps`: the runner builds
the validator, renderer, policy, schema provider, and resolver once, then
hands them to ``build_pydantic_ai_planner``. The resulting planner runs the
generate → resolve mentions → validate → repair loop, capturing structured
diagnostics per call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import TypeAdapter

from evals.mention_extractor import extract_mentions
from evals.models import (
    ClarificationOutput,
    PlannedOutput,
    RefusedOutput,
)
from evals.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    build_full_system_prompt,
    load_curated_examples,
)
from evals.relation_hints import (
    RelationHint,
    format_hints_block,
    infer_relation_hints,
)
from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.graph.schema_discovery import SchemaProvider, SchemaSnapshot
from graph_mcp.graph.term_resolver import TermCandidate, TermResolutionResult, TermResolver
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
from graph_mcp.models.validation import ValidationIssue

# A single union alias used as the runtime / annotation type for "anything a
# planner may return". Construct a concrete variant; never call this directly.
PlannerOutput = PlannedOutput | ClarificationOutput | RefusedOutput

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


def _ex_iri(local: str):  # type: ignore[no-untyped-def]
    """Backwards-compatible alias kept so older internal helpers compile."""
    from graph_mcp.models import PrefixedName

    return PrefixedName(prefix="ex", local=local)


class Planner(Protocol):
    def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlannerOutput: ...


# --- Deterministic planner --------------------------------------------------


class DeterministicPlanner:
    """Hand-coded planner used for offline tests and the default eval mode.

    It pattern-matches on a small set of keywords in the question and returns
    a known-good plan / clarification / refusal. This is *not* an LLM; it
    exists so the rest of the pipeline can be exercised end-to-end without
    any API key.
    """

    def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlannerOutput:
        q = question.lower()
        if "drop" in q or "delete table" in q or "raw sparql" in q:
            return self._refuse_unsafe(question)
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
        if "zero or one knows hop" in q or "at most one knows hop" in q:
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
        return PlannedOutput(
            question=question,
            assumptions=["fallback: ASK whether any ex:Person exists"],
            resolved_terms=[],
            plan=plan,
            confidence=0.3,
        )

    # --- individual cases (kept short and self-contained) -----------------

    def _who_works_for_acme(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(
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

    def _labels_in_english(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.9)

    def _optional_with_inner_filter(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _people_with_optional_label(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.9)

    def _union_knows_or_works(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _people_without_label(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.9)

    def _people_minus_founders(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _knows_one_or_more(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.8)

    def _knows_zero_or_one(self, q: str) -> PlannedOutput:
        from graph_mcp.models import PropertyPathZeroOrOne

        path: PropertyPath = PropertyPathZeroOrOne(operand=PropertyPathTerm(iri=_ex_iri("knows")))
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _values_alice_bob(self, q: str) -> PlannedOutput:
        plan = SelectPlan(
            prefixes=[EX],
            projection=[
                Projection(var=Var(name="p")),
                Projection(var=Var(name="company")),
            ],
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
        return PlannedOutput(question=q, plan=plan, confidence=0.9)

    def _bind_age_doubled(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _count_people_per_company(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _companies_with_many(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _top1_oldest_per_company(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.7)

    def _named_graph_query(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.7)

    def _joined_after_2019(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.85)

    def _age_greater_than_30(self, q: str) -> PlannedOutput:
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
        return PlannedOutput(question=q, plan=plan, confidence=0.9)

    def _ambiguous_clarification(self, q: str) -> ClarificationOutput:
        return ClarificationOutput(
            question=q,
            confidence=0.2,
            clarification_question=(
                "The question mentions an entity that does not match any known "
                "schema term. Can you clarify which class or individual you mean?"
            ),
        )

    def _refuse_unsafe(self, q: str) -> RefusedOutput:
        return RefusedOutput(
            question=q,
            confidence=0.0,
            refusal_reason=(
                "The request asks for a destructive or out-of-policy operation "
                "(e.g. raw SPARQL DROP / DELETE). The graph-mcp server only "
                "executes validated read-only QueryPlan IR."
            ),
            policy_code="unsafe_destructive_request",
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


@dataclass
class PlannerDiagnostics:
    """Mutable diagnostics collector for one ``plan(question)`` call."""

    extracted_mentions: list[str] = field(default_factory=list)
    term_resolution_results: list[TermResolutionResult] = field(default_factory=list)
    selected_terms: list[TermCandidate] = field(default_factory=list)
    unresolved_mentions: list[str] = field(default_factory=list)
    ambiguous_mentions: list[str] = field(default_factory=list)
    relation_hints: list[RelationHint] = field(default_factory=list)
    repair_attempts: int = 0
    semantic_repair_attempts: int = 0
    validation_errors_seen: list[ValidationIssue] = field(default_factory=list)
    final_validation_ok: bool = False
    rendered_sparql: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "extracted_mentions": list(self.extracted_mentions),
            "term_resolution_results": [r.model_dump() for r in self.term_resolution_results],
            "selected_terms": [t.model_dump() for t in self.selected_terms],
            "unresolved_mentions": list(self.unresolved_mentions),
            "ambiguous_mentions": list(self.ambiguous_mentions),
            "relation_hints": [h.model_dump() for h in self.relation_hints],
            "repair_attempts": self.repair_attempts,
            "semantic_repair_attempts": self.semantic_repair_attempts,
            "validation_errors_seen": [e.model_dump() for e in self.validation_errors_seen],
            "final_validation_ok": self.final_validation_ok,
            "rendered_sparql": self.rendered_sparql,
        }


_RESOLVER_AUTO_SELECT_THRESHOLD = 0.85
"""Score floor for auto-selecting a top candidate. Below this we require an
exact-form match (label/prefixed-name/local-name) or treat the mention as
ambiguous so the planner asks for clarification."""

_RESOLVER_TIE_MARGIN = 0.05
"""If the runner-up's score is within this margin of the top, treat the
mention as ambiguous rather than auto-selecting."""


def _is_exact_form_match(mention: str, candidate: TermCandidate) -> bool:
    """True when the mention matches the candidate's label / prefixed name /
    local name exactly (case-insensitive). Exact matches are safe even when
    the score is below the auto-select threshold."""
    m = mention.strip().lower()
    if candidate.label and candidate.label.lower() == m:
        return True
    if candidate.prefixed_name and candidate.prefixed_name.lower() == m:
        return True
    if candidate.prefixed_name:
        local = candidate.prefixed_name.split(":", 1)[-1].lower()
        if local == m:
            return True
    last = candidate.iri.rstrip("#/").rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
    return last == m


def _resolve_question_terms(
    deps: PlannerDeps, question: str
) -> tuple[
    list[str],
    list[TermResolutionResult],
    list[TermCandidate],
    list[str],
    list[str],
]:
    """Run the deterministic mention extractor + resolver before the LLM call.

    Returns ``(extracted, results, selected, unresolved, ambiguous)``.

    Selection is deliberately strict: the top candidate is auto-selected
    only when its score is at or above
    :data:`_RESOLVER_AUTO_SELECT_THRESHOLD`, **or** the mention exactly
    matches the candidate's label / prefixed name / local name. When two
    candidates tie within :data:`_RESOLVER_TIE_MARGIN` of the top, the
    mention is recorded as ambiguous and the planner is expected to ask
    for clarification.

    This stricter behaviour fixes the v7 live failure mode where ``Term``
    was resolved to ``ex:erin`` (fuzzy match score 0.5) and the planner
    proceeded to plan instead of clarifying.
    """
    snap = deps.schema.snapshot()
    mentions = extract_mentions(question, snap)
    extracted = [m.text for m in mentions]
    results: list[TermResolutionResult] = []
    selected: list[TermCandidate] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for m in mentions:
        kinds = list(m.expected_kinds) if m.expected_kinds else None
        kinds_typed: Any = kinds  # silence mypy; resolver accepts list[str].
        result = deps.resolver.resolve([m.text], expected_kinds=kinds_typed)
        results.append(result)
        candidates = [c for c in result.candidates if c.kind != "unknown"]
        if not candidates:
            unresolved.append(m.text)
            continue
        top = candidates[0]
        runner = candidates[1] if len(candidates) > 1 else None
        exact = _is_exact_form_match(m.text, top)
        if exact:
            selected.append(top)
            continue
        if top.score < _RESOLVER_AUTO_SELECT_THRESHOLD:
            unresolved.append(m.text)
            continue
        if runner is not None and (top.score - runner.score) < _RESOLVER_TIE_MARGIN:
            # Near-tie across candidates — ambiguous. Don't pick one.
            ambiguous.append(m.text)
            continue
        selected.append(top)
    return extracted, results, selected, unresolved, ambiguous


def _format_resolved_terms_block(
    selected: list[TermCandidate],
    unresolved: list[str],
    ambiguous: list[str] | None = None,
) -> str:
    """Render a compact candidate table for the planner prompt."""
    if not selected and not unresolved and not (ambiguous or []):
        return "(no terms extracted from question)"
    lines: list[str] = []
    if selected:
        lines.append("Resolved candidates (use these IRIs / prefixed names exactly):")
        for c in selected:
            label = c.label or "?"
            kind = c.kind
            pn = c.prefixed_name or c.iri
            lines.append(f"  - mention {c.mention!r} → {pn} ({kind}, label={label!r})")
    if unresolved:
        lines.append(
            "Unresolved mentions (no schema match — return needs_clarification "
            "if any of these are required):"
        )
        for m in unresolved:
            lines.append(f"  - {m!r}")
    if ambiguous:
        lines.append(
            "Ambiguous mentions (multiple candidates close in score — return "
            "needs_clarification if these are required):"
        )
        for m in ambiguous:
            lines.append(f"  - {m!r}")
    return "\n".join(lines)


def run_planner_workflow(
    deps: PlannerDeps,
    question: str,
    *,
    generate: Callable[[str], PlannerOutput],
    diagnostics: PlannerDiagnostics | None = None,
) -> tuple[PlannerOutput, PlannerDiagnostics]:
    """Run extract → resolve → generate → validate → repair and return the final output.

    Workflow:

    1. Extract candidate mentions from the question.
    2. Resolve each mention via the deterministic :class:`TermResolver`.
    3. Build a prompt that includes the resolved candidate table.
    4. Call ``generate`` to get a :class:`PlannerOutput`.
    5. Short-circuit on ``ClarificationOutput`` / ``RefusedOutput``.
    6. Validate the planned QueryPlan; if valid, render and return.
    7. Otherwise, run up to ``deps.max_repair_attempts`` repair iterations:
       feed the validator's structured errors back to the agent and ask
       for a corrected plan. Each repair attempt counts toward
       ``diagnostics.repair_attempts``.
    """
    diag = diagnostics or PlannerDiagnostics()

    extracted, results, selected, unresolved, ambiguous = _resolve_question_terms(deps, question)
    diag.extracted_mentions = extracted
    diag.term_resolution_results = results
    diag.selected_terms = selected
    diag.unresolved_mentions = unresolved
    diag.ambiguous_mentions = ambiguous

    snapshot = deps.schema.snapshot()
    hints = infer_relation_hints(question, selected, snapshot)
    diag.relation_hints = list(hints)

    resolved_block = _format_resolved_terms_block(selected, unresolved, ambiguous)
    hints_block = format_hints_block(hints)
    prompt_text = (
        f"{question}\n\n## Resolved terms\n{resolved_block}\n\n## Relation hints\n{hints_block}"
    )

    output = generate(prompt_text)

    if not isinstance(output, PlannedOutput):
        diag.final_validation_ok = False
        return output, diag

    def _try_render(planned: PlannedOutput) -> None:
        try:
            rendered = deps.renderer.render(planned.plan)  # type: ignore[attr-defined]
            diag.rendered_sparql = rendered.sparql
        except Exception:  # pragma: no cover - render shouldn't fail on valid plan
            diag.rendered_sparql = None

    planned: PlannedOutput = output

    result = deps.validator.validate(planned.plan)
    if result.ok:
        diag.final_validation_ok = True
        _try_render(planned)
        return planned, diag
    diag.validation_errors_seen.extend(result.errors)

    for repair in range(deps.max_repair_attempts):
        diag.repair_attempts = repair + 1
        feedback = (
            "\n\nYour previous plan failed validation with these errors:\n"
            + "\n".join(f"- {e.code}: {e.message}" for e in result.errors)
            + "\n\nProduce a corrected PlannedOutput. Do not switch to raw "
            "SPARQL. Preserve the resolved terms shown above."
        )
        next_output = generate(prompt_text + feedback)
        if not isinstance(next_output, PlannedOutput):
            diag.final_validation_ok = False
            return next_output, diag
        planned = next_output
        result = deps.validator.validate(planned.plan)
        if result.ok:
            diag.final_validation_ok = True
            _try_render(planned)
            return planned, diag
        diag.validation_errors_seen.extend(result.errors)

    diag.final_validation_ok = False
    return planned, diag


# --- PydanticAI planner (optional) -----------------------------------------


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
    """Compose the full PydanticAI system prompt from the cookbook + schema."""
    from evals.models import (
        ClarificationOutput,
        PlanGenerationOutput,  # noqa: F401  - used by TypeAdapter
        PlannedOutput,
        RefusedOutput,
    )

    schema_block = _format_schema_for_prompt(cfg.schema or SchemaSnapshot())
    # Render the discriminated-union schema by adapting each variant.
    qp_schema = json.dumps(
        {
            "PlannedOutput": TypeAdapter(PlannedOutput).json_schema(),
            "ClarificationOutput": TypeAdapter(ClarificationOutput).json_schema(),
            "RefusedOutput": TypeAdapter(RefusedOutput).json_schema(),
        },
        indent=2,
        sort_keys=True,
    )
    examples = cfg.examples or load_curated_examples()
    return build_full_system_prompt(
        cookbook=PLANNER_SYSTEM_PROMPT,
        schema_block=schema_block,
        qp_schema=qp_schema,
        examples=examples,
    )


def build_planner_from_callable(
    deps: PlannerDeps,
    generate: Callable[[str], PlannerOutput],
) -> Planner:
    """Build a :class:`Planner` from any plan-generating callable.

    This is the main extension point: pass any function that returns a
    :class:`PlannerOutput` for a given question and the workflow handles
    extraction + resolution + validation + repair + diagnostics. Production
    code uses the PydanticAI agent; tests use stub callables.
    """

    class _WorkflowPlanner:
        def __init__(self) -> None:
            self.last_repair_attempted: bool = False
            self.last_repair_succeeded: bool = False
            self.last_diagnostics: PlannerDiagnostics | None = None
            self.last_output: PlannerOutput | None = None

        def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlannerOutput:
            self.last_repair_attempted = False
            self.last_repair_succeeded = False
            output, diag = run_planner_workflow(deps, question, generate=generate)
            self.last_repair_attempted = diag.repair_attempts > 0
            self.last_repair_succeeded = diag.final_validation_ok and self.last_repair_attempted
            self.last_diagnostics = diag
            self.last_output = output
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

    All of ``validator``, ``renderer``, ``policy``, and ``resolver`` are now
    required. The eval runner builds them once and threads them in. Without
    them the workflow cannot validate, render, repair, or resolve, which is
    exactly the failure mode the v6 eval surfaced.
    """
    try:
        from pydantic_ai import Agent
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "pydantic-ai is required for the LLM planner; install with `pip install graph-mcp[ai]`"
        ) from exc

    if (
        validator is None
        or renderer is None
        or policy is None
        or resolver is None
        or schema is None
    ):
        raise ValueError(
            "build_pydantic_ai_planner requires schema, validator, renderer, policy, "
            "and resolver; the eval runner must construct them before building the planner"
        )

    snap = schema.snapshot()
    cfg = PydanticAIPlannerConfig(
        model=model,
        schema=snap,
        examples=examples or [],
        max_repair_attempts=max_repair_attempts,
    )
    system = _build_full_system_prompt(cfg)
    # The discriminated union is the agent's output type so the LLM can return
    # any of planned / needs_clarification / refused.
    output_type: Any = PlannedOutput | ClarificationOutput | RefusedOutput
    agent: Any = Agent(model=model, output_type=output_type, system_prompt=system)

    deps = PlannerDeps(
        schema=schema,
        resolver=resolver,
        validator=validator,
        renderer=renderer,
        policy=policy,
        max_repair_attempts=max_repair_attempts,
    )

    def generate(prompt_text: str) -> PlannerOutput:
        return agent.run_sync(prompt_text).output  # type: ignore[no-any-return]

    return build_planner_from_callable(deps, generate)


class _ImmutableStaticProvider:
    """Tiny provider wrapper used when only a snapshot is available."""

    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> SchemaSnapshot:
        return self._snapshot
