"""Typed planner I/O for the eval harness."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.graph.term_resolver import TermCandidate
from graph_mcp.models import QueryPlan


class PlanGenerationOutput(BaseModel):
    """The strict output type a planner agent must produce."""

    model_config = ConfigDict(extra="forbid")

    question: str
    assumptions: list[str] = Field(default_factory=list)
    resolved_terms: list[TermCandidate] = Field(default_factory=list)
    plan: QueryPlan
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str | None = None


class TripleSpec(BaseModel):
    """A subject/predicate/object triple template used for structural matching.

    Variables are written with the leading ``?`` (or ``$``) just like SPARQL.
    Prefixed names (``ex:Person``) and absolute IRIs are matched literally.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str


class FilterSpec(BaseModel):
    """A semantic filter requirement.

    ``kind`` is a small whitelist so we never depend on string matching the
    rendered SPARQL.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "lang_equals",
        "regex",
        "compare",
        "in",
        "bound",
        "not_exists",
        "exists",
    ]
    var: str | None = None
    op: str | None = None
    value: Any = None


class AggregateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: Literal["count", "sum", "avg", "min", "max", "sample", "group_concat"]
    expression: str | None = None
    """Variable being aggregated, written ``?name``. ``None`` means COUNT(*)."""

    alias: str | None = None
    """Projection alias if the aggregate is part of a SELECT projection."""


class OrderBySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str
    """Either a variable (``?n``) or an aggregate alias name."""

    descending: bool = False


class BindingSpec(BaseModel):
    """An expected binding in the executed result set."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Free-form mapping; arbitrary variable names → expected IRI/literal.
    # Implemented as a generic ``dict[str, str]`` so cases stay readable.
    bindings: dict[str, str] = Field(default_factory=dict)


class GoldenCaseExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Legacy lightweight fields (kept for back-compat).
    required_patterns: list[str] = Field(default_factory=list)
    """Pattern kind names (e.g. ``filter``, ``optional``) that must appear in the plan."""

    required_terms: list[str] = Field(default_factory=list)
    """Tokens (IRIs, prefixed names, or SPARQL keywords) that must appear in the rendered SPARQL."""

    forbidden_features: list[str] = Field(default_factory=list)
    """Features that must NOT appear (e.g. ``raw_sparql``, ``service``)."""

    result_expectation: dict[str, Any] | None = None
    """Execution-result expectations: ``min_rows``, ``max_rows``, ``ask``."""

    expect_invalid: bool = False
    """If true, the case must FAIL validation (used for safety/unsafe-request cases)."""

    expect_clarification: bool = False
    """If true, the planner is expected to set ``needs_clarification=True``."""

    # New IR-level structural requirements. Cases that set these score on the
    # deeper metrics; the legacy fields are still summed into the simple
    # ``required_feature_recall`` so old cases keep working.
    required_pattern_kinds: list[str] = Field(default_factory=list)
    """Pattern kinds that must appear *anywhere* in the plan tree."""

    required_triples: list[TripleSpec] = Field(default_factory=list)
    """Triple templates that must appear in the WHERE clause (any depth).

    Variable position is matched by *role* (``?p`` matches any variable in the
    same slot), so the eval is robust to the planner's choice of variable name.
    Use a literal IRI/prefixed name to require a specific term.
    """

    required_filters: list[FilterSpec] = Field(default_factory=list)
    required_aggregates: list[AggregateSpec] = Field(default_factory=list)
    required_group_by: list[str] = Field(default_factory=list)
    """List of variables (``?x``) or expressions expected in GROUP BY."""

    required_order_by: list[OrderBySpec] = Field(default_factory=list)
    forbidden_pattern_kinds: list[str] = Field(default_factory=list)
    expected_bindings: list[dict[str, str]] = Field(default_factory=list)
    """Expected binding rows. Each row is a dict ``{var_name: iri-or-literal}``.

    Matching is set-style: every expected row must appear in the result, but
    the result may contain additional rows.
    """


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    schema_context: str = "default"
    expected: GoldenCaseExpected = Field(default_factory=GoldenCaseExpected)


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    plan_generated: bool
    plan_valid: bool
    rendered_sparql: str | None = None
    executed: bool = False
    row_count: int | None = None
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # --- Structural metric inputs (set by the runner) -------------------
    required_features_total: int = 0
    required_features_present: int = 0
    forbidden_features_total: int = 0
    forbidden_features_violated: int = 0
    expected_terms_total: int = 0
    expected_terms_present: int = 0
    repair_attempted: bool = False
    repair_succeeded: bool = False

    # IR-level structural recall (deeper metrics)
    triple_total: int = 0
    triple_present: int = 0
    filter_total: int = 0
    filter_present: int = 0
    aggregate_total: int = 0
    aggregate_present: int = 0
    group_by_total: int = 0
    group_by_present: int = 0
    order_by_total: int = 0
    order_by_present: int = 0
    expected_bindings_total: int = 0
    expected_bindings_present: int = 0
    forbidden_pattern_kinds_total: int = 0
    forbidden_pattern_kinds_violated: int = 0
    # Special-case classification
    is_clarification_case: bool = False
    clarification_correct: bool = False
    is_unsafe_request_case: bool = False
    unsafe_request_rejected: bool = False


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[CaseResult]
    metrics: dict[str, float]
