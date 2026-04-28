"""MCP tool implementations.

These functions are pure: they take Pydantic input models and return Pydantic
output models. The MCP layer (``server.py``) wires them up to ``@mcp.tool()``
decorators with their dependencies (validator, renderer, endpoint, schema).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.compiler import QueryPlanValidator, RenderedQuery, SparqlRenderer
from graph_mcp.graph import GraphEndpoint, TermResolutionResult, TermResolver
from graph_mcp.models import (
    AskPlan,
    ConstructPlan,
    QueryPlan,
    QueryResult,
    SelectPlan,
    ValidationResult,
)
from graph_mcp.security.policy import SecurityPolicy

# --- Tool input/output models -----------------------------------------------


class ResolveTermsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[str] = Field(min_length=1)
    expected_kinds: list[Literal["class", "property", "individual", "graph"]] | None = None
    limit: int = Field(default=10, ge=1, le=100)


class ValidateQueryPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan


class RenderSparqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan


class QueryGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan
    max_rows: int | None = Field(default=None, ge=1)
    timeout_ms: int | None = Field(default=None, ge=1)
    dry_run: bool = False


class QueryGraphOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation: ValidationResult
    rendered: RenderedQuery | None = None
    result: QueryResult | None = None
    dry_run: bool = False


class ExplainQueryPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan


class ExplainQueryPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: str
    query_form: Literal["select", "ask", "construct"]
    projected_variables: list[str] = Field(default_factory=list)
    where_summary: list[str] = Field(default_factory=list)
    filter_summary: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RawSparqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sparql: str
    max_rows: int | None = Field(default=None, ge=1)
    timeout_ms: int | None = Field(default=None, ge=1)
    expected_query_type: Literal["select", "ask", "construct"] = "select"


class RawSparqlOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: QueryResult
    raw_mode: Literal[True] = True


# --- Pure tool functions ----------------------------------------------------


def tool_resolve_terms(
    inp: ResolveTermsInput, resolver: TermResolver
) -> TermResolutionResult:
    # The MCP tool input narrows to four kinds; the resolver's type allows a
    # fifth ("unknown") that we never request here.
    kinds = list(inp.expected_kinds) if inp.expected_kinds else None
    return resolver.resolve(inp.mentions, expected_kinds=kinds, limit=inp.limit)  # type: ignore[arg-type]


def tool_validate_query_plan(
    inp: ValidateQueryPlanInput, validator: QueryPlanValidator
) -> ValidationResult:
    return validator.validate(inp.plan)


def tool_render_sparql(
    inp: RenderSparqlInput,
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
) -> RenderedQuery:
    res = validator.validate(inp.plan)
    if not res.ok:
        # Surface validation errors via warnings on the rendered query so the
        # LLM still sees them; we still refuse to render an invalid plan.
        return RenderedQuery(
            sparql="",
            query_type=_query_type(inp.plan),
            warnings=res.errors,
        )
    rendered = renderer.render(inp.plan)
    return rendered.model_copy(update={"warnings": res.warnings})


async def tool_query_graph(
    inp: QueryGraphInput,
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    endpoint: GraphEndpoint,
    policy: SecurityPolicy,
) -> QueryGraphOutput:
    validation = validator.validate(inp.plan)
    out = QueryGraphOutput(validation=validation, dry_run=inp.dry_run)
    if not validation.ok:
        return out
    rendered = renderer.render(inp.plan)
    out = out.model_copy(update={"rendered": rendered})
    if inp.dry_run:
        return out

    timeout_ms = inp.timeout_ms or policy.timeout_ms
    max_rows = min(inp.max_rows or policy.default_limit, policy.max_limit)
    result = await endpoint.query(
        rendered.sparql,
        query_type=rendered.query_type,
        timeout_ms=timeout_ms,
        max_rows=max_rows,
    )
    return out.model_copy(update={"result": result})


def tool_explain_query_plan(
    inp: ExplainQueryPlanInput,
    validator: QueryPlanValidator,
) -> ExplainQueryPlanOutput:
    plan = inp.plan
    res = validator.validate(plan)
    qtype = _query_type(plan)
    projected: list[str] = []
    if isinstance(plan, SelectPlan):
        projected = [p.output_name for p in plan.projection] if plan.projection else ["*"]

    where_summary = _summarize_patterns(plan.where)
    filter_summary = _summarize_filters(plan.where)
    warnings = [f"{w.code}: {w.message}" for w in res.warnings]
    explanation = _build_explanation(plan, qtype, projected, where_summary, filter_summary)
    return ExplainQueryPlanOutput(
        explanation=explanation,
        query_form=qtype,
        projected_variables=projected,
        where_summary=where_summary,
        filter_summary=filter_summary,
        warnings=warnings,
    )


async def tool_execute_sparql_raw(
    inp: RawSparqlInput,
    endpoint: GraphEndpoint,
    policy: SecurityPolicy,
) -> RawSparqlOutput:
    if not policy.enable_raw_sparql:
        raise PermissionError("raw SPARQL execution is disabled by policy")
    sparql = inp.sparql
    _reject_unsafe_raw(sparql, policy)
    timeout_ms = inp.timeout_ms or policy.timeout_ms
    max_rows = min(inp.max_rows or policy.default_limit, policy.max_limit)
    result = await endpoint.query(
        sparql,
        query_type=inp.expected_query_type,
        timeout_ms=timeout_ms,
        max_rows=max_rows,
    )
    return RawSparqlOutput(result=result)


# --- Helpers ----------------------------------------------------------------


def _query_type(plan: QueryPlan) -> Literal["select", "ask", "construct"]:
    if isinstance(plan, SelectPlan):
        return "select"
    if isinstance(plan, AskPlan):
        return "ask"
    if isinstance(plan, ConstructPlan):
        return "construct"
    raise ValueError(f"unknown plan type: {type(plan).__name__}")


def _summarize_patterns(patterns: list) -> list[str]:  # type: ignore[type-arg]
    return [type(p).__name__ for p in patterns]


def _summarize_filters(patterns: list) -> list[str]:  # type: ignore[type-arg]
    from graph_mcp.models import FilterPattern, GroupPattern, OptionalPattern

    out: list[str] = []
    for p in patterns:
        if isinstance(p, FilterPattern):
            out.append(_expr_summary(p.expression))
        elif isinstance(p, GroupPattern | OptionalPattern):
            out.extend(_summarize_filters(p.patterns))
    return out


def _expr_summary(expr: object) -> str:
    return type(expr).__name__


def _build_explanation(
    plan: QueryPlan,
    qtype: str,
    projected: list[str],
    where_summary: list[str],
    filter_summary: list[str],
) -> str:
    lines = [f"Query form: {qtype.upper()}"]
    if projected:
        lines.append("Projected variables: " + ", ".join(projected))
    lines.append("Where clause patterns: " + (", ".join(where_summary) or "(none)"))
    if filter_summary:
        lines.append("Filters: " + ", ".join(filter_summary))
    if isinstance(plan, SelectPlan) and plan.limit is not None:
        lines.append(f"Limit: {plan.limit}")
    return "\n".join(lines)


def _reject_unsafe_raw(sparql: str, policy: SecurityPolicy) -> None:
    """Conservative pre-flight checks for raw SPARQL.

    The remote endpoint is the ultimate arbiter; we reject obviously unsafe
    forms here so they never even reach the wire.
    """
    upper = sparql.upper()
    forbidden = (
        "INSERT ", "DELETE ", "DROP ", "CLEAR ", "LOAD ",
        "CREATE ", "COPY ", "MOVE ", "ADD ",
    )
    for kw in forbidden:
        if kw in upper:
            raise PermissionError(f"forbidden SPARQL keyword in raw query: {kw.strip()}")
    if "SERVICE" in upper and not policy.allowed_service_endpoints:
        raise PermissionError("SERVICE not allowed by policy")


# --- Registration -----------------------------------------------------------


def register_tools() -> None:
    """Reserved for future use; FastMCP wiring lives in ``server.py``."""
    return
