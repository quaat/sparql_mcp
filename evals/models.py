"""Typed planner I/O for the eval harness."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.graph.term_resolver import TermCandidate
from graph_mcp.models import QueryPlan
from graph_mcp.models.validation import ValidationIssue


class _PlannerOutputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    assumptions: list[str] = Field(default_factory=list)
    resolved_terms: list[TermCandidate] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class PlannedOutput(_PlannerOutputBase):
    """The planner produced a concrete, executable :class:`QueryPlan`."""

    status: Literal["planned"] = "planned"
    plan: QueryPlan


class ClarificationOutput(_PlannerOutputBase):
    """The planner needs the user to clarify before proceeding."""

    status: Literal["needs_clarification"] = "needs_clarification"
    clarification_question: str


class RefusedOutput(_PlannerOutputBase):
    """The planner refused to plan because the request is unsafe / out-of-policy."""

    status: Literal["refused"] = "refused"
    refusal_reason: str
    policy_code: str | None = None


PlanGenerationOutput = Annotated[
    PlannedOutput | ClarificationOutput | RefusedOutput,
    Field(discriminator="status"),
]
"""Discriminated union of every shape a planner may return.

Construct one of the three concrete classes (:class:`PlannedOutput`,
:class:`ClarificationOutput`, :class:`RefusedOutput`). Use this annotated
alias only as a type — never as a constructor.
"""


def is_planned(out: PlannedOutput | ClarificationOutput | RefusedOutput) -> bool:
    return out.status == "planned"


def is_clarification(out: PlannedOutput | ClarificationOutput | RefusedOutput) -> bool:
    return out.status == "needs_clarification"


def is_refused(out: PlannedOutput | ClarificationOutput | RefusedOutput) -> bool:
    return out.status == "refused"


# --- Golden-case structural matchers ---------------------------------------


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

    bindings: dict[str, str] = Field(default_factory=dict)


class GoldenCaseExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Legacy lightweight fields. These are now treated as **presentation**
    # signals only — they never fail a case on their own. Use the IR-level
    # structural fields below for semantic correctness.
    required_patterns: list[str] = Field(default_factory=list)
    """Pattern kind names (e.g. ``filter``, ``optional``) that must appear in the plan.
    Presentation-only; missing patterns are reported as warnings."""

    required_terms: list[str] = Field(default_factory=list)
    """Tokens that must appear in the rendered SPARQL. Presentation-only;
    missing terms are reported as warnings, not failures."""

    forbidden_features: list[str] = Field(default_factory=list)
    """Features that must NOT appear (e.g. ``raw_sparql``, ``service``)."""

    result_expectation: dict[str, Any] | None = None
    """Execution-result expectations: ``min_rows``, ``max_rows``, ``ask``."""

    expect_invalid: bool = False
    """If true, the case is expected to be rejected by the planner (refusal)
    or by the validator (invalid plan). The new path prefers a planner
    refusal, but legacy cases that produce a deliberately invalid plan still
    pass through the validator-rejection branch."""

    expect_clarification: bool = False
    """If true, the planner is expected to return a ``ClarificationOutput``."""

    # IR-level structural requirements. Cases that set these score on the
    # deeper metrics; the legacy fields above remain as presentation warnings.
    required_pattern_kinds: list[str] = Field(default_factory=list)
    """Pattern kinds that must appear *anywhere* in the plan tree."""

    required_triples: list[TripleSpec] = Field(default_factory=list)
    """Triple templates that must appear in the WHERE clause (any depth)."""

    required_filters: list[FilterSpec] = Field(default_factory=list)
    required_aggregates: list[AggregateSpec] = Field(default_factory=list)
    required_group_by: list[str] = Field(default_factory=list)
    required_order_by: list[OrderBySpec] = Field(default_factory=list)
    forbidden_pattern_kinds: list[str] = Field(default_factory=list)
    expected_bindings: list[dict[str, str]] = Field(default_factory=list)
    """Expected binding rows. Each row is a dict ``{var_name: iri-or-literal}``.
    Matching is set-style: every expected row must appear in the result, but
    the result may contain additional rows."""


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
    """Semantic failures that fail the case."""

    warnings: list[str] = Field(default_factory=list)
    """Validator warnings or presentation warnings (do NOT fail the case)."""

    # --- Structural metric inputs (set by the runner) -------------------
    required_features_total: int = 0
    required_features_present: int = 0
    forbidden_features_total: int = 0
    forbidden_features_violated: int = 0
    expected_terms_total: int = 0
    expected_terms_present: int = 0
    repair_attempted: bool = False
    repair_succeeded: bool = False
    repair_attempts: int = 0

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

    # --- New rich diagnostics for §10 reports ----------------------------
    planner_status: str | None = None
    """``"planned"``, ``"needs_clarification"``, or ``"refused"`` — the
    planner's structured status field."""

    planner_confidence: float | None = None
    planner_assumptions: list[str] = Field(default_factory=list)
    resolved_terms: list[TermCandidate] = Field(default_factory=list)
    generated_plan_json: dict[str, Any] | None = None
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    validation_warnings: list[ValidationIssue] = Field(default_factory=list)
    execution_rows: list[dict[str, str]] = Field(default_factory=list)
    semantic_failures: list[str] = Field(default_factory=list)
    presentation_warnings: list[str] = Field(default_factory=list)
    extracted_mentions: list[str] = Field(default_factory=list)
    unresolved_mentions: list[str] = Field(default_factory=list)
    refusal_reason: str | None = None
    clarification_question: str | None = None
    policy_code: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[CaseResult]
    metrics: dict[str, float]
