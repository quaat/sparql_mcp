"""Deterministic SPARQL renderer.

The renderer produces canonical, indented SPARQL from a validated
:class:`QueryPlan`. It uses the escaping helpers as the only path that turns
user data into output text.

The renderer never modifies the plan. If a default LIMIT is needed, it is
handled by :meth:`SparqlRenderer.normalize_plan` *before* rendering.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from graph_mcp.compiler.errors import RenderError
from graph_mcp.compiler.escaping import escape_iri, escape_lang_tag, escape_string_literal
from graph_mcp.models import (
    AggregateExpr,
    AskPlan,
    BinaryExpr,
    BindPattern,
    BoundExpr,
    ConstructPlan,
    DateTimeExpr,
    ExistsExpr,
    Expression,
    FilterPattern,
    FunctionExpr,
    GraphPattern,
    GroupPattern,
    InExpr,
    Iri,
    LangMatchesExpr,
    LiteralValue,
    MinusPattern,
    NotExistsExpr,
    NotExpr,
    OptionalPattern,
    Pattern,
    PrefixedName,
    PropertyPath,
    PropertyPathAlt,
    PropertyPathInverse,
    PropertyPathOneOrMore,
    PropertyPathSeq,
    PropertyPathTerm,
    PropertyPathZeroOrMore,
    PropertyPathZeroOrOne,
    QueryPlan,
    RegexExpr,
    SelectPlan,
    ServicePattern,
    SubqueryPattern,
    TriplePattern,
    UnaryExpr,
    UnionPattern,
    ValidationIssue,
    ValuesPattern,
    Var,
)
from graph_mcp.models.literals import DEFAULT_PREFIXES
from graph_mcp.security.policy import SecurityPolicy


class RenderedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sparql: str
    query_type: Literal["select", "ask", "construct"]
    projected_variables: list[str] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


# --- Helpers --------------------------------------------------------------


def _indent(level: int) -> str:
    return "  " * level


def _is_xsd_datatype(iri: str, suffix: str) -> bool:
    return iri == f"http://www.w3.org/2001/XMLSchema#{suffix}"


# --- Renderer ------------------------------------------------------------


class SparqlRenderer:
    """Render a :class:`QueryPlan` to canonical SPARQL."""

    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy
        # Populated per-render: maps full IRI → prefix name for compaction.
        self._iri_to_prefix: dict[str, str] = {}

    def _compact_iri(self, iri: str) -> str | None:
        """Return ``prefix:local`` form when ``iri`` starts with a declared prefix IRI.

        Returns ``None`` when no prefix matches or when the local part is not a
        valid prefixed-name local (e.g. contains characters we'd need to escape).
        """
        from graph_mcp.models.literals import PREFIXED_LOCAL_REGEX

        for prefix_iri, name in sorted(
            self._iri_to_prefix.items(), key=lambda kv: -len(kv[0])
        ):
            if iri.startswith(prefix_iri):
                local = iri[len(prefix_iri):]
                if PREFIXED_LOCAL_REGEX.match(local):
                    return f"{name}:{local}"
        return None

    # --- Public API --------------------------------------------------

    def normalize_plan(self, plan: QueryPlan) -> QueryPlan:
        """Apply policy-driven defaults to a plan (e.g. default LIMIT).

        Returns a new plan with adjustments. Does not mutate the input.
        """
        if isinstance(plan, SelectPlan):
            limit = plan.limit
            if limit is None:
                limit = self.policy.default_limit
            elif limit > self.policy.max_limit:
                limit = self.policy.max_limit
            return plan.model_copy(update={"limit": limit})
        if isinstance(plan, ConstructPlan):
            limit = plan.limit
            if limit is None:
                limit = self.policy.default_limit
            elif limit > self.policy.max_limit:
                limit = self.policy.max_limit
            return plan.model_copy(update={"limit": limit})
        return plan

    def render(self, plan: QueryPlan) -> RenderedQuery:
        plan = self.normalize_plan(plan)
        prefixes = self._collect_prefixes(plan)
        # Per-render lookup: full-IRI → prefix name. Used to compress
        # datatype IRIs in literals and IRIs in expressions.
        self._iri_to_prefix = {iri: name for name, iri in prefixes.items()}

        if isinstance(plan, SelectPlan):
            body = self._render_select(plan, prefixes, level=0)
            projected = self._projected_names(plan)
            return RenderedQuery(
                sparql=body,
                query_type="select",
                projected_variables=projected,
            )
        if isinstance(plan, AskPlan):
            body = self._render_ask(plan, prefixes)
            return RenderedQuery(sparql=body, query_type="ask")
        if isinstance(plan, ConstructPlan):
            body = self._render_construct(plan, prefixes)
            return RenderedQuery(sparql=body, query_type="construct")
        raise RenderError(f"unsupported plan type: {type(plan).__name__}")  # pragma: no cover

    # --- Prefixes ----------------------------------------------------

    def _collect_prefixes(self, plan: QueryPlan) -> dict[str, str]:
        """Merge plan prefixes with sane defaults; plan wins on conflict."""
        out = dict(DEFAULT_PREFIXES)
        for p in plan.prefixes:
            out[p.prefix] = p.iri
        return out

    def _render_prefix_block(self, prefixes: dict[str, str]) -> str:
        # Sort for deterministic output.
        lines = [f"PREFIX {p}: <{escape_iri(iri)}>" for p, iri in sorted(prefixes.items())]
        return "\n".join(lines)

    # --- SELECT ------------------------------------------------------

    def _render_select(
        self, plan: SelectPlan, prefixes: dict[str, str], *, level: int
    ) -> str:
        parts: list[str] = []
        if level == 0:
            parts.append(self._render_prefix_block(prefixes))
            parts.append("")

        head = "SELECT"
        if plan.distinct:
            head += " DISTINCT"
        elif plan.reduced:
            head += " REDUCED"

        if not plan.projection:
            head += " *"
        else:
            head += " " + " ".join(self._render_projection_item(p) for p in plan.projection)

        parts.append(head)

        parts.append("WHERE {")
        parts.extend(self._render_patterns(plan.where, level=level + 1))
        parts.append("}")

        if plan.group_by:
            items = []
            for g in plan.group_by:
                if isinstance(g, Var):
                    items.append(f"?{g.name}")
                else:
                    items.append(f"({self._render_expr(cast(Expression, g))})")
            parts.append("GROUP BY " + " ".join(items))

        for h in plan.having:
            parts.append(f"HAVING ({self._render_expr(h)})")

        if plan.order_by:
            items = []
            for oc in plan.order_by:
                rendered = self._render_expr(oc.expression)
                if isinstance(oc.expression, Var):
                    items.append(("DESC(" if oc.descending else "ASC(") + rendered + ")")
                else:
                    items.append(("DESC(" if oc.descending else "ASC(") + rendered + ")")
            parts.append("ORDER BY " + " ".join(items))

        if plan.limit is not None:
            parts.append(f"LIMIT {plan.limit}")
        if plan.offset is not None:
            parts.append(f"OFFSET {plan.offset}")

        return "\n".join(parts)

    def _render_projection_item(self, proj: object) -> str:
        from graph_mcp.models.query_plan import Projection
        if not isinstance(proj, Projection):  # pragma: no cover - defensive
            raise RenderError(f"invalid projection item: {proj!r}")
        if proj.var is not None:
            return f"?{proj.var.name}"
        assert proj.expression is not None and proj.alias is not None
        return f"({self._render_expr(proj.expression)} AS ?{proj.alias.name})"

    def _projected_names(self, plan: SelectPlan) -> list[str]:
        if not plan.projection:
            return []
        return [p.output_name for p in plan.projection]

    # --- ASK / CONSTRUCT -------------------------------------------

    def _render_ask(self, plan: AskPlan, prefixes: dict[str, str]) -> str:
        parts = [self._render_prefix_block(prefixes), "", "ASK WHERE {"]
        parts.extend(self._render_patterns(plan.where, level=1))
        parts.append("}")
        return "\n".join(parts)

    def _render_construct(self, plan: ConstructPlan, prefixes: dict[str, str]) -> str:
        parts = [self._render_prefix_block(prefixes), "", "CONSTRUCT {"]
        for t in plan.template:
            parts.append(_indent(1) + self._render_triple(t) + " .")
        parts.append("}")
        parts.append("WHERE {")
        parts.extend(self._render_patterns(plan.where, level=1))
        parts.append("}")
        if plan.limit is not None:
            parts.append(f"LIMIT {plan.limit}")
        if plan.offset is not None:
            parts.append(f"OFFSET {plan.offset}")
        return "\n".join(parts)

    # --- Patterns --------------------------------------------------

    def _render_patterns(self, patterns: list[Pattern], *, level: int) -> list[str]:
        out: list[str] = []
        for p in patterns:
            out.extend(self._render_pattern(p, level=level))
        return out

    def _render_pattern(self, p: Pattern, *, level: int) -> list[str]:
        ind = _indent(level)
        if isinstance(p, TriplePattern):
            return [ind + self._render_triple(p) + " ."]
        if isinstance(p, GroupPattern):
            inner = self._render_patterns(p.patterns, level=level + 1)
            return [ind + "{", *inner, ind + "}"]
        if isinstance(p, OptionalPattern):
            inner = self._render_patterns(p.patterns, level=level + 1)
            return [ind + "OPTIONAL {", *inner, ind + "}"]
        if isinstance(p, UnionPattern):
            lines: list[str] = []
            for i, branch in enumerate(p.branches):
                if i == 0:
                    lines.append(ind + "{")
                else:
                    lines.append(ind + "UNION {")
                lines.extend(self._render_patterns(branch, level=level + 1))
                lines.append(ind + "}")
            return lines
        if isinstance(p, MinusPattern):
            inner = self._render_patterns(p.patterns, level=level + 1)
            return [ind + "MINUS {", *inner, ind + "}"]
        if isinstance(p, FilterPattern):
            return [ind + f"FILTER ({self._render_expr(p.expression)})"]
        if isinstance(p, BindPattern):
            return [ind + f"BIND ({self._render_expr(p.expression)} AS ?{p.var.name})"]
        if isinstance(p, ValuesPattern):
            return self._render_values(p, level=level)
        if isinstance(p, GraphPattern):
            graph = self._render_term(p.graph)
            inner = self._render_patterns(p.patterns, level=level + 1)
            return [ind + f"GRAPH {graph} {{", *inner, ind + "}"]
        if isinstance(p, ServicePattern):
            kw = "SERVICE SILENT" if p.silent else "SERVICE"
            ep = self._render_term(p.endpoint)
            inner = self._render_patterns(p.patterns, level=level + 1)
            return [ind + f"{kw} {ep} {{", *inner, ind + "}"]
        if isinstance(p, SubqueryPattern):
            sub = self._render_select(p.select, prefixes={}, level=level + 1)
            sub_indented = "\n".join(_indent(level + 1) + line if line else line
                                     for line in sub.splitlines())
            return [ind + "{", sub_indented, ind + "}"]
        raise RenderError(f"unknown pattern type: {type(p).__name__}")  # pragma: no cover

    def _render_values(self, p: ValuesPattern, *, level: int) -> list[str]:
        ind = _indent(level)
        if len(p.variables) == 1:
            head = f"VALUES ?{p.variables[0].name}"
            rows = []
            for r in p.rows:
                v = r[0]
                tok = self._render_term(v) if v is not None else "UNDEF"
                rows.append(_indent(level + 1) + tok)
            return [ind + head + " {", *rows, ind + "}"]
        head = "VALUES (" + " ".join(f"?{v.name}" for v in p.variables) + ")"
        rows = []
        for r in p.rows:
            tokens = [self._render_term(v) if v is not None else "UNDEF" for v in r]
            rows.append(_indent(level + 1) + "(" + " ".join(tokens) + ")")
        return [ind + head + " {", *rows, ind + "}"]

    def _render_triple(self, t: TriplePattern) -> str:
        s = self._render_term(t.subject)
        o = self._render_term(t.object)
        if isinstance(t.predicate, Var):
            p = f"?{t.predicate.name}"
        elif isinstance(t.predicate, Iri | PrefixedName):
            p = self._render_term(t.predicate)
        else:
            p = self._render_path(t.predicate)
        return f"{s} {p} {o}"

    # --- Property paths --------------------------------------------

    def _render_path(self, path: PropertyPath) -> str:
        if isinstance(path, PropertyPathTerm):
            iri = self._render_term(path.iri)
            return f"^{iri}" if path.inverse else iri
        if isinstance(path, PropertyPathInverse):
            return "^" + self._render_path_atom(path.operand)
        if isinstance(path, PropertyPathSeq):
            return "/".join(self._render_path_atom(e) for e in path.elements)
        if isinstance(path, PropertyPathAlt):
            return "|".join(self._render_path_atom(e) for e in path.elements)
        if isinstance(path, PropertyPathZeroOrMore):
            return self._render_path_atom(path.operand) + "*"
        if isinstance(path, PropertyPathOneOrMore):
            return self._render_path_atom(path.operand) + "+"
        if isinstance(path, PropertyPathZeroOrOne):
            return self._render_path_atom(path.operand) + "?"
        raise RenderError(f"unknown property path: {type(path).__name__}")  # pragma: no cover

    def _render_path_atom(self, path: PropertyPath) -> str:
        rendered = self._render_path(path)
        if isinstance(path, PropertyPathTerm):
            return rendered
        return f"({rendered})"

    # --- Terms -----------------------------------------------------

    def _render_term(self, term: object) -> str:
        if isinstance(term, Var):
            return f"?{term.name}"
        if isinstance(term, Iri):
            return f"<{escape_iri(term.value)}>"
        if isinstance(term, PrefixedName):
            return f"{term.prefix}:{term.local}"
        if isinstance(term, LiteralValue):
            return self._render_literal(term)
        raise RenderError(f"unknown term: {type(term).__name__}")  # pragma: no cover

    def _render_literal(self, lit: LiteralValue) -> str:
        # Booleans first (bool is a subclass of int).
        if isinstance(lit.value, bool):
            return "true" if lit.value else "false"
        if isinstance(lit.value, int):
            if lit.datatype is not None and not _is_xsd_datatype(lit.datatype, "integer"):
                return f'"{lit.value}"^^{self._render_datatype(lit.datatype)}'
            return str(lit.value)
        if isinstance(lit.value, float):
            if lit.datatype is not None and not _is_xsd_datatype(lit.datatype, "double"):
                return f'"{lit.value}"^^{self._render_datatype(lit.datatype)}'
            return f"{lit.value:.16g}"
        # str
        s = '"' + escape_string_literal(lit.value) + '"'
        if lit.lang is not None:
            s += "@" + escape_lang_tag(lit.lang)
        elif lit.datatype is not None:
            s += "^^" + self._render_datatype(lit.datatype)
        return s

    def _render_datatype(self, iri: str) -> str:
        compact = self._compact_iri(iri)
        return compact if compact is not None else f"<{escape_iri(iri)}>"

    # --- Expressions ----------------------------------------------

    def _render_expr(self, e: Expression) -> str:
        if isinstance(e, Var):
            return f"?{e.name}"
        if isinstance(e, Iri):
            return f"<{escape_iri(e.value)}>"
        if isinstance(e, PrefixedName):
            return f"{e.prefix}:{e.local}"
        if isinstance(e, LiteralValue):
            return self._render_literal(e)
        if isinstance(e, BinaryExpr):
            return f"({self._render_expr(e.left)} {e.op} {self._render_expr(e.right)})"
        if isinstance(e, UnaryExpr):
            return f"({e.op}{self._render_expr(e.operand)})"
        if isinstance(e, NotExpr):
            return f"(!{self._render_expr(e.operand)})"
        if isinstance(e, InExpr):
            kw = "NOT IN" if e.negated else "IN"
            opts = ", ".join(self._render_expr(o) for o in e.options)
            return f"({self._render_expr(e.operand)} {kw} ({opts}))"
        if isinstance(e, FunctionExpr):
            args = ", ".join(self._render_expr(a) for a in e.args)
            return f"{e.name.upper()}({args})"
        if isinstance(e, RegexExpr):
            base = f"REGEX({self._render_expr(e.text)}, \"{escape_string_literal(e.pattern)}\""
            if e.flags:
                base += f", \"{e.flags}\""
            return base + ")"
        if isinstance(e, BoundExpr):
            return f"BOUND(?{e.var.name})"
        if isinstance(e, LangMatchesExpr):
            return f"LANGMATCHES({self._render_expr(e.tag)}, {self._render_expr(e.range)})"
        if isinstance(e, NotExistsExpr):
            inner = self._render_patterns(e.patterns, level=1)
            return "NOT EXISTS {\n" + "\n".join(inner) + "\n}"
        if isinstance(e, ExistsExpr):
            inner = self._render_patterns(e.patterns, level=1)
            return "EXISTS {\n" + "\n".join(inner) + "\n}"
        if isinstance(e, AggregateExpr):
            agg_inner = "*" if e.expression is None else self._render_expr(e.expression)
            distinct = "DISTINCT " if e.distinct else ""
            sep = ""
            if e.function == "group_concat" and e.separator is not None:
                sep = f'; SEPARATOR="{escape_string_literal(e.separator)}"'
            return f"{e.function.upper()}({distinct}{agg_inner}{sep})"
        if isinstance(e, DateTimeExpr):
            if e.accessor == "now":
                return "NOW()"
            assert e.operand is not None
            return f"{e.accessor.upper()}({self._render_expr(e.operand)})"
        raise RenderError(f"unknown expression: {type(e).__name__}")  # pragma: no cover
