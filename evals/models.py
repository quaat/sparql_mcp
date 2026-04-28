"""Typed planner I/O for the eval harness."""

from __future__ import annotations

from typing import Any

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


class GoldenCaseExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_patterns: list[str] = Field(default_factory=list)
    """Pattern type names that must appear in the rendered SPARQL or plan."""

    required_terms: list[str] = Field(default_factory=list)
    """IRIs (full or prefixed) that must appear in the rendered SPARQL."""

    forbidden_features: list[str] = Field(default_factory=list)
    """Features that must NOT appear (e.g. ``raw_sparql``, ``service``)."""

    result_expectation: dict[str, Any] | None = None
    """Optional execution-result expectations: ``min_rows``, ``max_rows``, ``ask``."""

    expect_invalid: bool = False
    """If true, the case must FAIL validation (used for safety/unsafe-request cases)."""

    expect_clarification: bool = False
    """If true, the planner is expected to set ``needs_clarification=True``."""


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


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[CaseResult]
    metrics: dict[str, float]
