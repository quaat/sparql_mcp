"""MCP tool implementations.

These functions are pure: they take Pydantic input models and return Pydantic
output models. The MCP layer (``server.py``) wires them up to ``@mcp.tool()``
decorators with their dependencies (validator, renderer, endpoint, schema).
"""

from __future__ import annotations

import re as _re
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
    inferred = _infer_query_type(sparql)
    if inferred != inp.expected_query_type:
        raise PermissionError(
            f"expected_query_type={inp.expected_query_type!r} does not match "
            f"the actual query form ({inferred!r})"
        )
    timeout_ms = inp.timeout_ms or policy.timeout_ms
    max_rows = min(inp.max_rows or policy.default_limit, policy.max_limit)
    result = await endpoint.query(
        sparql,
        query_type=inferred,
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


_UPDATE_KEYWORDS: tuple[str, ...] = (
    "INSERT",
    "DELETE",
    "DROP",
    "CLEAR",
    "LOAD",
    "CREATE",
    "COPY",
    "MOVE",
    "ADD",
)
_FORBIDDEN_QUERY_FORMS: tuple[str, ...] = ("DESCRIBE",)
_QUERY_FORM_KEYWORDS: tuple[str, ...] = ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE")


def _strip_sparql_comments_and_strings(sparql: str) -> str:
    """Return SPARQL with string literals and comments replaced by spaces.

    This is the prerequisite for any keyword-based safety check: substring
    matches on the raw text would false-positive on ``"INSERT in a string"``
    and false-negative on ``INSERT\\nDATA`` once whitespace is normalized.

    The transformation preserves character positions (replaces with spaces)
    so error messages can still point at the offending byte if we want.
    """
    out: list[str] = []
    i = 0
    n = len(sparql)
    while i < n:
        c = sparql[i]
        # Line comment: # to end-of-line.
        if c == "#":
            while i < n and sparql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # Triple-quoted strings (rare in queries but legal).
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
        # Single-line strings.
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
    """Determine the query form from the cleaned SPARQL text.

    The first matching query keyword wins. ``DESCRIBE`` is forbidden — callers
    invoke ``_reject_unsafe_raw`` before this for the safety verdict.
    """
    cleaned = _strip_sparql_comments_and_strings(sparql).upper()
    positions: dict[str, int] = {}
    for kw in _QUERY_FORM_KEYWORDS:
        match = _re.search(rf"\b{kw}\b", cleaned)
        if match:
            positions[kw] = match.start()
    if not positions:
        raise PermissionError("could not determine SPARQL query form")
    first = min(positions, key=lambda k: positions[k])
    if first == "DESCRIBE":
        raise PermissionError("DESCRIBE is not supported in raw mode")
    return first.lower()  # type: ignore[return-value]


def _reject_unsafe_raw(sparql: str, policy: SecurityPolicy) -> None:
    """Pre-flight safety check for raw SPARQL.

    Strips comments and string literals, then word-boundary-matches the
    forbidden update keywords. Verifies that any ``SERVICE`` references an
    allowlisted endpoint.
    """
    cleaned = _strip_sparql_comments_and_strings(sparql)
    upper = cleaned.upper()

    for kw in _UPDATE_KEYWORDS:
        if _re.search(rf"\b{kw}\b", upper):
            raise PermissionError(f"forbidden SPARQL keyword in raw query: {kw}")
    for kw in _FORBIDDEN_QUERY_FORMS:
        if _re.search(rf"\b{kw}\b", upper):
            raise PermissionError(f"unsupported query form: {kw}")

    # Each SERVICE must point to an explicitly allowlisted endpoint.
    for match in _re.finditer(r"\bSERVICE\s+(?:SILENT\s+)?<([^>\s]+)>", upper, _re.IGNORECASE):
        # Use the original-cased text from the cleaned buffer for matching.
        original = cleaned[match.start(1) : match.end(1)]
        if not policy.is_service_allowed(original):
            raise PermissionError(f"SERVICE endpoint not in allowlist: {original}")

    # SERVICE with a variable endpoint is impossible to allowlist; reject it.
    if _re.search(r"\bSERVICE\s+(?:SILENT\s+)?\?", upper, _re.IGNORECASE):
        raise PermissionError("SERVICE with a variable endpoint is not permitted")

    # Bare SERVICE with prefixed-name (e.g. SERVICE ex:foo) — also reject; the
    # raw path does not resolve prefixes itself.
    if _re.search(r"\bSERVICE\s+(?:SILENT\s+)?[A-Za-z_][A-Za-z0-9_\-]*:", upper, _re.IGNORECASE):
        raise PermissionError(
            "SERVICE with a prefixed-name endpoint is not permitted in raw mode; "
            "use an absolute IRI"
        )


# --- Registration -----------------------------------------------------------


def register_tools() -> None:
    """Reserved for future use; FastMCP wiring lives in ``server.py``."""
    return
