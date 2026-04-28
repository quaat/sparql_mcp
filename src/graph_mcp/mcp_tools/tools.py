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
from graph_mcp.mcp_tools.sparql_scanner import (
    find_top_level_limit,
    infer_query_type,
    reject_unsafe_raw,
)
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


class RenderSparqlOutput(BaseModel):
    """Result of :func:`tool_render_sparql`.

    If validation fails, ``rendered`` is ``None`` and ``validation`` carries
    the structured errors. The tool never returns a fake empty SPARQL string.
    """

    model_config = ConfigDict(extra="forbid")

    validation: ValidationResult
    rendered: RenderedQuery | None = None


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


class RefreshSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class SchemaRefreshResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["static", "sparql"]
    refreshed: bool
    last_refresh_at: str | None
    classes_count: int
    properties_count: int
    individuals_count: int
    named_graphs_count: int
    diagnostics: list[str] = Field(default_factory=list)


class SchemaStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["static", "sparql"]
    last_refresh_at: str | None
    cache_ttl_seconds: float
    classes_count: int
    properties_count: int
    individuals_count: int
    named_graphs_count: int
    diagnostics: list[str] = Field(default_factory=list)


# --- Pure tool functions ----------------------------------------------------


def tool_resolve_terms(inp: ResolveTermsInput, resolver: TermResolver) -> TermResolutionResult:
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
) -> RenderSparqlOutput:
    """Validate then render a plan.

    Returns the structured :class:`ValidationResult` together with a
    :class:`RenderedQuery` when validation succeeded, or ``rendered=None`` when
    it failed. The tool never fabricates an empty rendered SPARQL string.
    """
    res = validator.validate(inp.plan)
    if not res.ok:
        return RenderSparqlOutput(validation=res, rendered=None)
    rendered = renderer.render(inp.plan)
    rendered = rendered.model_copy(update={"warnings": res.warnings})
    return RenderSparqlOutput(validation=res, rendered=rendered)


async def tool_query_graph(
    inp: QueryGraphInput,
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    endpoint: GraphEndpoint,
    policy: SecurityPolicy,
) -> QueryGraphOutput:
    """Validate, render, and (unless ``dry_run``) execute a plan.

    The effective row limit is computed *before* rendering so that the
    rendered ``LIMIT`` reflects the smaller of the request-level ``max_rows``
    and the policy maximum. This protects remote endpoints from being asked
    to materialize unbounded results before truncation.
    """
    validation = validator.validate(inp.plan)
    out = QueryGraphOutput(validation=validation, dry_run=inp.dry_run)
    if not validation.ok:
        return out

    effective_max_rows = min(inp.max_rows or policy.default_limit, policy.max_limit)
    capped_plan = _cap_top_level_limit(inp.plan, effective_max_rows)
    rendered = renderer.render(capped_plan)
    out = out.model_copy(update={"rendered": rendered})
    if inp.dry_run:
        return out

    timeout_ms = inp.timeout_ms or policy.timeout_ms
    result = await endpoint.query(
        rendered.sparql,
        query_type=rendered.query_type,
        timeout_ms=timeout_ms,
        max_rows=effective_max_rows,
    )
    return out.model_copy(update={"result": result})


def _cap_top_level_limit(plan: QueryPlan, effective_max_rows: int) -> QueryPlan:
    """Return a plan whose top-level LIMIT is capped at ``effective_max_rows``.

    - For ``SELECT`` and ``CONSTRUCT``: if ``limit`` is ``None`` or above the
      cap, set it to ``effective_max_rows``; preserve a smaller existing limit.
    - For ``ASK``: nothing to cap (boolean result).
    - Subquery limits are *not* touched here; the renderer's normalize_plan
      caps them at ``policy.max_limit`` separately.
    """
    if isinstance(plan, SelectPlan):
        if plan.limit is None or plan.limit > effective_max_rows:
            return plan.model_copy(update={"limit": effective_max_rows})
        return plan
    if isinstance(plan, ConstructPlan):
        if plan.limit is None or plan.limit > effective_max_rows:
            return plan.model_copy(update={"limit": effective_max_rows})
        return plan
    return plan


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
    """Execute a raw SPARQL query under the policy.

    Uses a token-aware scanner to:

    - reject SPARQL Update keywords (INSERT/DELETE/DROP/...);
    - reject DESCRIBE;
    - require an absolute, allowlisted IRI for every SERVICE;
    - infer the query form from the first query keyword and require
      ``expected_query_type`` to match;
    - require a conservative top-level LIMIT for SELECT and CONSTRUCT no
      greater than the effective max_rows.
    """
    if not policy.enable_raw_sparql:
        raise PermissionError("raw SPARQL execution is disabled by policy")
    sparql = inp.sparql
    tokens = reject_unsafe_raw(
        sparql,
        allowed_service_endpoints=policy.allowed_service_endpoints,
    )
    inferred = infer_query_type(tokens)
    if inferred != inp.expected_query_type:
        raise PermissionError(
            f"expected_query_type={inp.expected_query_type!r} does not match "
            f"the actual query form ({inferred!r})"
        )

    timeout_ms = inp.timeout_ms or policy.timeout_ms
    effective_max_rows = min(inp.max_rows or policy.default_limit, policy.max_limit)

    if inferred in ("select", "construct"):
        top_limit = find_top_level_limit(tokens)
        if top_limit is None:
            raise PermissionError(
                "raw SELECT/CONSTRUCT queries must include an explicit "
                f"top-level LIMIT no greater than {effective_max_rows}"
            )
        if top_limit > effective_max_rows:
            raise PermissionError(
                f"raw query top-level LIMIT {top_limit} exceeds the "
                f"effective max_rows {effective_max_rows}"
            )

    result = await endpoint.query(
        sparql,
        query_type=inferred,
        timeout_ms=timeout_ms,
        max_rows=effective_max_rows,
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


# --- Backwards-compatible shims for the previous string-preprocessing API.
# The real safety logic now lives in ``graph_mcp.mcp_tools.sparql_scanner``.


def _strip_sparql_comments_and_strings(sparql: str) -> str:
    """Replace string literals and comments with spaces (legacy helper).

    Preserved for tests that imported it directly. The production path uses
    :func:`graph_mcp.mcp_tools.sparql_scanner.tokenize` instead.
    """
    out: list[str] = []
    i = 0
    n = len(sparql)
    while i < n:
        c = sparql[i]
        if c == "#":
            while i < n and sparql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if sparql.startswith('"""', i) or sparql.startswith("'''", i):
            quote = sparql[i : i + 3]
            out.append("   ")
            i += 3
            while i < n and not sparql.startswith(quote, i):
                out.append(" " if sparql[i] != "\n" else "\n")
                i += 1
            if i < n:
                out.append("   ")
                i += 3
            continue
        if c in ('"', "'"):
            quote = c
            out.append(" ")
            i += 1
            while i < n and sparql[i] != quote:
                if sparql[i] == "\\" and i + 1 < n:
                    out.append("  ")
                    i += 2
                    continue
                out.append(" " if sparql[i] != "\n" else "\n")
                i += 1
            if i < n:
                out.append(" ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _infer_query_type(sparql: str) -> Literal["select", "ask", "construct"]:
    """Legacy wrapper around the scanner's ``infer_query_type``."""
    from graph_mcp.mcp_tools.sparql_scanner import _ScannerError, tokenize

    try:
        tokens = tokenize(sparql)
    except _ScannerError as exc:
        raise PermissionError(f"could not tokenize raw SPARQL: {exc}") from exc
    return infer_query_type(tokens)  # type: ignore[return-value]


def _reject_unsafe_raw(sparql: str, policy: SecurityPolicy) -> None:
    """Legacy wrapper around the scanner's ``reject_unsafe_raw``."""
    reject_unsafe_raw(sparql, allowed_service_endpoints=policy.allowed_service_endpoints)


# --- Registration -----------------------------------------------------------


def register_tools() -> None:
    """Reserved for future use; FastMCP wiring lives in ``server.py``."""
    return
