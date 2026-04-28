"""Static validator for QueryPlan IR.

The validator is deterministic, side-effect free, and produces a structured
:class:`ValidationResult`. It performs:

- limit/depth/triple-pattern enforcement
- variable scope tracking
- projection / aggregate / GROUP BY / HAVING coherence
- BIND no-rebind
- named graph allowlist enforcement
- SERVICE gating
- property-path complexity limits
- optional/filter-placement warnings
- expression function/operator whitelisting (already enforced by models)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

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
    ValidationResult,
    ValuesPattern,
    Var,
)
from graph_mcp.security.policy import SecurityPolicy


@dataclass
class _Scope:
    """Variables visible at a given point in the plan tree."""

    bound: set[str] = field(default_factory=set)
    """Variables guaranteed to be bound here."""

    seen: set[str] = field(default_factory=set)
    """Variables that *may* be bound (e.g., inside an OPTIONAL)."""

    values_constraints: dict[str, set[str]] = field(default_factory=dict)
    """For each variable, the set of IRI values it has been restricted to via
    a sibling :class:`ValuesPattern` in this scope. Empty if unrestricted.
    Used by the named-graph allowlist check."""

    def fork(self) -> _Scope:
        return _Scope(
            bound=set(self.bound),
            seen=set(self.seen),
            values_constraints={k: set(v) for k, v in self.values_constraints.items()},
        )

    def merge_required(self, other: _Scope) -> None:
        self.bound |= other.bound
        self.seen |= other.seen

    def merge_optional(self, other: _Scope) -> None:
        # OPTIONAL bindings are not guaranteed; they extend `seen` only.
        self.seen |= other.bound | other.seen

    def merge_union(self, branches: list[_Scope]) -> None:
        if not branches:
            return
        # A variable is guaranteed only if every branch binds it.
        guaranteed = set.intersection(*[b.bound for b in branches])
        # `seen` aggregates across all branches.
        seen = set().union(*[b.bound | b.seen for b in branches])
        self.bound |= guaranteed
        self.seen |= seen


@dataclass
class _Ctx:
    issues: list[ValidationIssue] = field(default_factory=list)
    triple_count: int = 0
    depth: int = 0
    path: list[str | int] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=dict)

    def error(self, code: str, message: str, hint: str | None = None) -> None:
        self.issues.append(
            ValidationIssue(
                severity="error",
                code=code,
                message=message,
                path=list(self.path),
                hint=hint,
            )
        )

    def warn(self, code: str, message: str, hint: str | None = None) -> None:
        self.issues.append(
            ValidationIssue(
                severity="warning",
                code=code,
                message=message,
                path=list(self.path),
                hint=hint,
            )
        )


class QueryPlanValidator:
    """Validates a :class:`QueryPlan` against a :class:`SecurityPolicy`."""

    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy

    # --- Public API -------------------------------------------------------

    def validate(self, plan: QueryPlan) -> ValidationResult:
        from graph_mcp.models.literals import DEFAULT_PREFIXES

        ctx = _Ctx()
        # Build the prefix map and detect conflicts (same prefix → different IRI).
        ctx.prefixes = {}
        for p in plan.prefixes:
            existing = ctx.prefixes.get(p.prefix)
            if existing is not None and existing != p.iri:
                ctx.error(
                    "prefix_conflict",
                    f"prefix {p.prefix!r} declared twice with different IRIs: "
                    f"{existing!r} vs {p.iri!r}",
                )
            # Reject overriding well-known built-in prefixes unless policy permits.
            builtin = DEFAULT_PREFIXES.get(p.prefix)
            if (
                builtin is not None
                and builtin != p.iri
                and not self.policy.allow_default_prefix_override
            ):
                ctx.error(
                    "default_prefix_override",
                    f"prefix {p.prefix!r} is a built-in pointing at {builtin!r}; "
                    f"plan redefines it to {p.iri!r}",
                    hint=(
                        "Built-in prefixes (rdf, rdfs, xsd, owl, skos, dct, foaf) "
                        "cannot be overridden by plans. Set "
                        "GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true to permit."
                    ),
                )
            ctx.prefixes[p.prefix] = p.iri
        outer = _Scope()

        if isinstance(plan, SelectPlan):
            self._validate_select(plan, ctx, outer, top_level=True)
        elif isinstance(plan, AskPlan):
            self._validate_where(plan.where, ctx, outer)
        elif isinstance(plan, ConstructPlan):
            self._validate_where(plan.where, ctx, outer)
            self._check_limit(plan.limit, ctx)
            self._validate_construct_template(plan, ctx, outer)
        else:  # pragma: no cover - exhaustive
            ctx.error("unsupported_query_form", f"unsupported query form: {type(plan).__name__}")

        ok = not any(i.severity == "error" for i in ctx.issues)
        return ValidationResult(ok=ok, issues=ctx.issues)

    # --- SELECT -----------------------------------------------------------

    def _validate_select(
        self,
        plan: SelectPlan,
        ctx: _Ctx,
        outer: _Scope,
        *,
        top_level: bool,
    ) -> _Scope:
        # Prefix policy: prefixes are declared once at the top of the plan.
        # Nested SELECTs (subqueries) must not redeclare them — there is no
        # well-defined per-subquery prefix scope in our renderer, and the
        # rendered SPARQL has a single PREFIX block at the top.
        if not top_level and plan.prefixes:
            ctx.error(
                "subquery_prefixes_not_allowed",
                "subquery SelectPlan must not declare prefixes; "
                "declare all prefixes on the top-level plan",
                hint=(
                    "Move the prefix declarations to the outermost SelectPlan/"
                    "AskPlan/ConstructPlan."
                ),
            )

        scope = outer.fork()
        ctx.path.append("where")
        try:
            self._validate_where(plan.where, ctx, scope)
        finally:
            ctx.path.pop()

        # Validate projection
        ctx.path.append("projection")
        try:
            projected_names = self._validate_projection(plan, ctx, scope)
        finally:
            ctx.path.pop()

        # GROUP BY / HAVING
        ctx.path.append("group_by")
        try:
            grouped_names = self._validate_group_by(plan, ctx, scope)
        finally:
            ctx.path.pop()
        ctx.path.append("having")
        try:
            self._validate_having(plan, ctx, scope, grouped_names)
        finally:
            ctx.path.pop()

        # ORDER BY uses scope after projection: aliases are visible as variables.
        order_scope = scope.fork()
        order_scope.bound |= projected_names
        # In aggregate queries, ORDER BY may only reference grouped variables,
        # aggregate expressions, or projection aliases.
        is_aggregate = bool(plan.group_by) or any(
            self._contains_aggregate(p.expression)
            for p in plan.projection
            if p.expression is not None
        )
        grouped = {self._var_name(g) for g in plan.group_by if isinstance(g, Var)}
        ctx.path.append("order_by")
        try:
            for i, oc in enumerate(plan.order_by):
                ctx.path.append(i)
                try:
                    self._check_expr_vars(oc.expression, ctx, order_scope)
                    if is_aggregate:
                        free = self._collect_non_aggregated_vars(oc.expression)
                        for v in free:
                            if v not in grouped and v not in projected_names:
                                ctx.error(
                                    "order_by_non_grouped",
                                    f"ORDER BY in an aggregate query references ?{v} "
                                    "which is neither grouped nor a projection alias",
                                )
                finally:
                    ctx.path.pop()
        finally:
            ctx.path.pop()

        # LIMIT / OFFSET — enforced at every level (top-level *and* subqueries).
        self._check_limit(plan.limit, ctx)

        # When this SELECT is a subquery, only `projected_names` escape outward.
        out = _Scope()
        out.bound |= projected_names
        out.seen |= projected_names
        return out

    def _validate_projection(self, plan: SelectPlan, ctx: _Ctx, scope: _Scope) -> set[str]:
        if not plan.projection:
            # SELECT * — projection is the union of bound variables.
            if not scope.bound and not scope.seen:
                ctx.warn(
                    "empty_select_star",
                    "SELECT * with no variables in scope will return no useful columns.",
                )
            return set(scope.bound | scope.seen)

        seen_names: set[str] = set()
        alias_names: set[str] = set()
        out: set[str] = set()
        # An aggregate query is one with any aggregate expression in projection
        # OR a non-empty GROUP BY clause.
        has_aggregate = bool(plan.group_by) or any(
            self._contains_aggregate(p.expression)
            for p in plan.projection
            if p.expression is not None
        )
        grouped_names = {self._var_name(g) for g in plan.group_by if isinstance(g, Var)}
        for i, p in enumerate(plan.projection):
            ctx.path.append(i)
            try:
                name = p.output_name
                if name in seen_names:
                    ctx.error("duplicate_projection", f"duplicate projection name: {name!r}")
                seen_names.add(name)
                if p.var is not None:
                    if p.var.name not in scope.bound and p.var.name not in scope.seen:
                        ctx.error(
                            "unbound_projection_var",
                            f"projected variable ?{p.var.name} is not bound by the WHERE clause",
                            hint=(
                                "Add a triple pattern that binds it, or remove it "
                                "from the projection."
                            ),
                        )
                    if has_aggregate and p.var.name not in grouped_names:
                        ctx.error(
                            "non_grouped_projection",
                            f"variable ?{p.var.name} appears in projection alongside aggregates "
                            "but is not in GROUP BY",
                            hint="Add it to GROUP BY or wrap it in an aggregate.",
                        )
                if p.expression is not None:
                    self._check_expr_vars(p.expression, ctx, scope, allow_aggregate=True)
                    if p.alias is not None:
                        if p.alias.name in scope.bound:
                            ctx.error(
                                "alias_collision",
                                f"projection alias ?{p.alias.name} collides with an "
                                "existing variable",
                            )
                        if p.alias.name in alias_names:
                            ctx.error(
                                "alias_collision",
                                f"projection alias ?{p.alias.name} collides with another alias",
                            )
                        alias_names.add(p.alias.name)
                    # In an aggregate query, every variable inside the projected
                    # expression that appears OUTSIDE an aggregate must be in GROUP BY.
                    # This catches e.g. (?x + COUNT(?y) AS ?bad) with ?x not grouped.
                    if has_aggregate:
                        free_outside_agg = self._collect_non_aggregated_vars(p.expression)
                        for v in free_outside_agg:
                            if v not in grouped_names:
                                ctx.error(
                                    "non_grouped_in_expression",
                                    f"variable ?{v} appears outside an aggregate in a "
                                    "projected expression but is not in GROUP BY",
                                    hint=(
                                        f"Add ?{v} to GROUP BY, or wrap it in an "
                                        "aggregate (e.g. SAMPLE)."
                                    ),
                                )
                out.add(name)
            finally:
                ctx.path.pop()
        return out

    def _validate_group_by(self, plan: SelectPlan, ctx: _Ctx, scope: _Scope) -> set[str]:
        names: set[str] = set()
        for i, item in enumerate(plan.group_by):
            ctx.path.append(i)
            try:
                if isinstance(item, Var):
                    if item.name not in scope.bound and item.name not in scope.seen:
                        ctx.error(
                            "unbound_group_var",
                            f"GROUP BY variable ?{item.name} is not bound",
                        )
                    names.add(item.name)
                else:
                    self._check_expr_vars(cast(Expression, item), ctx, scope)
            finally:
                ctx.path.pop()
        return names

    def _validate_having(
        self, plan: SelectPlan, ctx: _Ctx, scope: _Scope, grouped: set[str]
    ) -> None:
        for i, expr in enumerate(plan.having):
            ctx.path.append(i)
            try:
                self._check_expr_vars(expr, ctx, scope, allow_aggregate=True)
                # Vars used directly (not inside any aggregate) must be in GROUP BY.
                free = self._collect_non_aggregated_vars(expr)
                for v in free:
                    if v not in grouped:
                        ctx.error(
                            "having_non_grouped_var",
                            f"HAVING references variable ?{v} which is not in GROUP BY",
                            hint="Wrap it in an aggregate or add it to GROUP BY.",
                        )
            finally:
                ctx.path.pop()

    # --- WHERE / patterns -------------------------------------------------

    def _validate_where(self, patterns: list[Pattern], ctx: _Ctx, scope: _Scope) -> None:
        ctx.depth += 1
        if ctx.depth > self.policy.max_query_depth:
            ctx.error(
                "max_depth_exceeded",
                f"query nesting exceeds max depth of {self.policy.max_query_depth}",
            )
            ctx.depth -= 1
            return
        try:
            for i, p in enumerate(patterns):
                ctx.path.append(i)
                try:
                    self._validate_pattern(p, ctx, scope)
                finally:
                    ctx.path.pop()
        finally:
            ctx.depth -= 1

    def _validate_pattern(self, p: Pattern, ctx: _Ctx, scope: _Scope) -> None:
        if isinstance(p, TriplePattern):
            self._validate_triple(p, ctx, scope)
        elif isinstance(p, GroupPattern):
            self._validate_where(p.patterns, ctx, scope)
        elif isinstance(p, OptionalPattern):
            inner = scope.fork()
            self._validate_where(p.patterns, ctx, inner)
            scope.merge_optional(inner)
        elif isinstance(p, UnionPattern):
            branch_scopes: list[_Scope] = []
            for j, branch in enumerate(p.branches):
                ctx.path.append(j)
                try:
                    inner = scope.fork()
                    self._validate_where(branch, ctx, inner)
                    branch_scopes.append(inner)
                finally:
                    ctx.path.pop()
            scope.merge_union(branch_scopes)
        elif isinstance(p, MinusPattern):
            inner = scope.fork()
            self._validate_where(p.patterns, ctx, inner)
            # MINUS does not bind anything outward.
        elif isinstance(p, FilterPattern):
            self._check_expr_vars(p.expression, ctx, scope)
            self._check_filter_placement_warning(p.expression, ctx, scope)
        elif isinstance(p, BindPattern):
            self._check_expr_vars(p.expression, ctx, scope)
            if p.var.name in scope.bound or p.var.name in scope.seen:
                ctx.error(
                    "bind_rebind",
                    f"BIND target ?{p.var.name} is already bound in this scope",
                    hint="Use a fresh variable name.",
                )
            scope.bound.add(p.var.name)
        elif isinstance(p, ValuesPattern):
            for col, v in enumerate(p.variables):
                scope.bound.add(v.name)
                # Capture per-variable IRI constraints so a downstream GRAPH ?v
                # can be proven safe against the allowlist. Mixed-type rows or
                # rows containing non-IRI values invalidate the constraint.
                iris: set[str] = set()
                bad = False
                for row in p.rows:
                    cell = row[col]
                    if isinstance(cell, Iri):
                        iris.add(cell.value)
                    elif isinstance(cell, PrefixedName):
                        resolved = self._resolve_iri(cell, ctx)
                        if resolved is None:
                            bad = True
                            break
                        iris.add(resolved)
                    else:
                        # UNDEF or a literal: cannot constrain to IRIs
                        bad = True
                        break
                if bad:
                    # Any UNDEF, literal, or unresolved-prefix value means we
                    # cannot prove the variable is restricted to IRIs at all.
                    # Invalidate any prior constraint so a later GRAPH ?g
                    # check is not falsely authorized.
                    scope.values_constraints.pop(v.name, None)
                elif iris:
                    # Multiple VALUES on the same variable intersect.
                    # SPARQL semantics: each VALUES clause restricts the
                    # variable, so a later VALUES makes it the conjunction
                    # (intersection) of the previous and the new value sets.
                    existing = scope.values_constraints.get(v.name)
                    if existing is None:
                        scope.values_constraints[v.name] = iris
                    else:
                        scope.values_constraints[v.name] = existing & iris
        elif isinstance(p, GraphPattern):
            if isinstance(p.graph, (Iri, PrefixedName)):
                iri = self._resolve_iri(p.graph, ctx)
                if iri and not self.policy.is_graph_allowed(iri):
                    ctx.error(
                        "graph_not_allowed",
                        f"named graph not in allowlist: {iri}",
                    )
            elif isinstance(p.graph, Var):
                if self.policy.allowed_graphs:
                    # An allowlist is configured: only accept GRAPH ?g when a
                    # prior VALUES *in the same required group scope* has
                    # constrained ?g to allowlisted IRIs.
                    #
                    # Order rule: VALUES must precede the GRAPH ?g block in
                    # the same required group; the validator walks group
                    # children in order, so a later VALUES does not
                    # retroactively authorize an earlier GRAPH.
                    #
                    # Constraints from inside OPTIONAL / UNION / MINUS /
                    # FILTER EXISTS / subqueries do NOT propagate to the
                    # outer scope (see ``_Scope.merge_optional`` and
                    # ``merge_union``).
                    constraint = scope.values_constraints.get(p.graph.name)
                    if constraint is None:
                        ctx.error(
                            "graph_variable_not_allowed",
                            (
                                f"GRAPH ?{p.graph.name} is not permitted with a "
                                "named-graph allowlist; bind ?{name} via a "
                                "preceding VALUES in the same required group "
                                "scope first."
                            ).replace("{name}", p.graph.name),
                            hint=(
                                "Restrict ?{name} with a VALUES pattern that "
                                "lists only allowlisted graph IRIs and place "
                                "it before the GRAPH ?{name} block."
                            ).replace("{name}", p.graph.name),
                        )
                    elif not constraint:
                        ctx.error(
                            "graph_values_constraint_empty",
                            f"GRAPH ?{p.graph.name} is restricted by VALUES "
                            "intersection to an empty set; query will produce "
                            "no rows. Either fix the VALUES clauses or remove "
                            "this GRAPH block.",
                        )
                    else:
                        forbidden = [g for g in constraint if not self.policy.is_graph_allowed(g)]
                        if forbidden:
                            ctx.error(
                                "graph_values_not_allowed",
                                f"GRAPH ?{p.graph.name} is restricted to graphs "
                                f"that include non-allowlisted IRIs: {sorted(forbidden)}",
                            )
                scope.bound.add(p.graph.name)
            self._validate_where(p.patterns, ctx, scope)
        elif isinstance(p, ServicePattern):
            iri = self._resolve_iri(p.endpoint, ctx)
            if not iri or not self.policy.is_service_allowed(iri):
                ctx.error(
                    "service_not_allowed",
                    f"SERVICE endpoint not in allowlist: {iri or p.endpoint}",
                    hint="Configure GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS to permit it.",
                )
            self._validate_where(p.patterns, ctx, scope)
        elif isinstance(p, SubqueryPattern):
            inner = _Scope()  # subqueries have a fresh scope
            sub_out = self._validate_select(p.select, ctx, inner, top_level=False)
            # Variables projected by the subquery are bound in the outer scope.
            scope.bound |= sub_out.bound
        else:  # pragma: no cover
            ctx.error("unknown_pattern", f"unknown pattern type: {type(p).__name__}")

    def _validate_triple(self, t: TriplePattern, ctx: _Ctx, scope: _Scope) -> None:
        ctx.triple_count += 1
        if ctx.triple_count > self.policy.max_triple_patterns:
            ctx.error(
                "too_many_triples",
                f"plan exceeds max triple patterns ({self.policy.max_triple_patterns})",
            )
        for term in (t.subject, t.object):
            if isinstance(term, Var):
                scope.bound.add(term.name)
            elif isinstance(term, (Iri, PrefixedName)):
                self._resolve_iri(term, ctx)
        # Predicate may be IRI / prefixed / var / property path.
        if isinstance(t.predicate, Var):
            scope.bound.add(t.predicate.name)
        elif isinstance(t.predicate, Iri | PrefixedName):
            self._resolve_iri(t.predicate, ctx)
        else:
            self._validate_property_path(t.predicate, ctx)

    # --- Property paths --------------------------------------------------

    def _validate_property_path(self, path: PropertyPath, ctx: _Ctx) -> None:
        complexity = self._path_complexity(path)
        if complexity > self.policy.max_property_path_complexity:
            ctx.error(
                "property_path_too_complex",
                f"property path complexity {complexity} exceeds limit "
                f"{self.policy.max_property_path_complexity}",
            )
        if not self.policy.allow_unbounded_paths and self._has_unbounded(path):
            ctx.error(
                "unbounded_property_path",
                "unbounded property path (* / +) is disabled by policy",
                hint="Set GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=true to permit, "
                "or rewrite to a bounded form.",
            )
        # Resolve every predicate IRI inside the path. This catches unknown
        # prefixes and (when configured) enforces the path-predicate allowlist.
        for term_iri in self._iter_path_predicates(path, ctx):
            if not self.policy.is_path_predicate_allowed(term_iri):
                ctx.error(
                    "path_predicate_not_allowed",
                    f"property path uses predicate {term_iri!r} which is not "
                    "in the path-predicate allowlist",
                    hint=(
                        "Add it to GRAPH_MCP_ALLOWED_PATH_PREDICATES, or rewrite "
                        "the query to use an allowlisted predicate."
                    ),
                )

    def _iter_path_predicates(self, path: PropertyPath, ctx: _Ctx) -> list[str]:
        """Yield resolved IRIs for every PropertyPathTerm inside ``path``.

        Resolves PrefixedName references against ``ctx.prefixes`` and emits
        an ``unknown_prefix`` error for any that are unresolved. Returns the
        list of resolved IRIs (excluding any whose prefix could not resolve).
        """
        out: list[str] = []
        if isinstance(path, PropertyPathTerm):
            iri = self._resolve_iri(path.iri, ctx)
            if iri is not None:
                out.append(iri)
        elif isinstance(
            path,
            (
                PropertyPathInverse,
                PropertyPathZeroOrMore,
                PropertyPathOneOrMore,
                PropertyPathZeroOrOne,
            ),
        ):
            out.extend(self._iter_path_predicates(path.operand, ctx))
        elif isinstance(path, (PropertyPathSeq, PropertyPathAlt)):
            for e in path.elements:
                out.extend(self._iter_path_predicates(e, ctx))
        return out

    def _path_complexity(self, path: PropertyPath) -> int:
        if isinstance(path, PropertyPathTerm):
            return 1
        if isinstance(
            path,
            (
                PropertyPathInverse,
                PropertyPathZeroOrMore,
                PropertyPathOneOrMore,
                PropertyPathZeroOrOne,
            ),
        ):
            return 1 + self._path_complexity(path.operand)
        if isinstance(path, (PropertyPathSeq, PropertyPathAlt)):
            return 1 + sum(self._path_complexity(e) for e in path.elements)
        return 0  # pragma: no cover

    def _has_unbounded(self, path: PropertyPath) -> bool:
        if isinstance(path, (PropertyPathZeroOrMore, PropertyPathOneOrMore)):
            return True
        if isinstance(path, (PropertyPathInverse, PropertyPathZeroOrOne)):
            return self._has_unbounded(path.operand)
        if isinstance(path, (PropertyPathSeq, PropertyPathAlt)):
            return any(self._has_unbounded(e) for e in path.elements)
        return False

    # --- Expressions ------------------------------------------------------

    def _check_expr_vars(
        self,
        expr: Expression,
        ctx: _Ctx,
        scope: _Scope,
        *,
        allow_aggregate: bool = False,
    ) -> None:
        # Free vars of the expression, not counting NOT EXISTS / EXISTS internals
        # (those open a sub-scope that can introduce fresh variable names).
        for v in self._collect_outer_free_vars(expr):
            if v not in scope.bound and v not in scope.seen:
                ctx.error(
                    "filter_var_unbound",
                    f"expression references variable ?{v} which is not in scope",
                )
        if not allow_aggregate and self._contains_aggregate(expr):
            ctx.error(
                "aggregate_outside_projection_or_having",
                "aggregate expression used where it is not permitted "
                "(allowed in projection or HAVING)",
            )
        # Recursively validate any nested EXISTS / NOT EXISTS patterns.
        self._validate_nested_exists(expr, ctx, scope)

    def _validate_nested_exists(self, expr: Expression, ctx: _Ctx, scope: _Scope) -> None:
        """Walk an expression and validate EXISTS / NOT EXISTS sub-patterns.

        The inner block sees outer-scope variables (so a reference to
        ``?p`` inside ``NOT EXISTS { ?p ex:blocked ?x }`` is OK) but cannot
        leak its locally introduced variables back outward. Safety checks
        (SERVICE, named graphs, property paths, depth, triple counts,
        unknown prefixes) are applied recursively.
        """
        if isinstance(expr, (NotExistsExpr, ExistsExpr)):
            ctx.path.append("exists" if isinstance(expr, ExistsExpr) else "not_exists")
            try:
                inner = scope.fork()
                self._validate_where(expr.patterns, ctx, inner)
            finally:
                ctx.path.pop()
            # Inner-only bindings do not propagate to the outer scope.
            return
        for child in self._children(expr):
            self._validate_nested_exists(child, ctx, scope)

    def _check_filter_placement_warning(self, expr: Expression, ctx: _Ctx, scope: _Scope) -> None:
        # If a filter references variables that are only in `seen` (i.e.
        # introduced inside an OPTIONAL) without using bound(), warn.
        free = self._collect_outer_free_vars(expr)
        only_optional = {v for v in free if v in scope.seen and v not in scope.bound}
        if only_optional and not self._uses_bound_check(expr, only_optional):
            ctx.warn(
                "filter_after_optional",
                "FILTER references variables introduced only inside OPTIONAL; "
                "consider moving the FILTER inside the OPTIONAL or using bound().",
                hint=f"affected vars: {sorted(only_optional)}",
            )

    def _uses_bound_check(self, expr: Expression, vars_: set[str]) -> bool:
        if isinstance(expr, BoundExpr) and expr.var.name in vars_:
            return True
        return any(self._uses_bound_check(child, vars_) for child in self._children(expr))

    def _contains_aggregate(self, expr: Expression) -> bool:
        if isinstance(expr, AggregateExpr):
            return True
        return any(self._contains_aggregate(child) for child in self._children(expr))

    def _collect_outer_free_vars(self, expr: Expression) -> set[str]:
        """Free variables referenced in ``expr`` from the outer scope.

        Variables that appear *only* inside ``NOT EXISTS`` / ``EXISTS`` are
        treated as scoped within those forms (SPARQL allows them to refer to
        outer scope but does not require it), and are excluded.
        """
        if isinstance(expr, Var):
            return {expr.name}
        if isinstance(expr, BoundExpr):
            return {expr.var.name}
        if isinstance(expr, AggregateExpr):
            if expr.expression is None:
                return set()
            return self._collect_outer_free_vars(expr.expression)
        if isinstance(expr, (NotExistsExpr, ExistsExpr)):
            return set()
        out: set[str] = set()
        for child in self._children(expr):
            out |= self._collect_outer_free_vars(child)
        return out

    def _collect_non_aggregated_vars(self, expr: Expression) -> set[str]:
        """Variables that appear outside any aggregate in ``expr``."""
        if isinstance(expr, Var):
            return {expr.name}
        if isinstance(expr, BoundExpr):
            return {expr.var.name}
        if isinstance(expr, AggregateExpr):
            return set()
        if isinstance(expr, (NotExistsExpr, ExistsExpr)):
            return set()
        out: set[str] = set()
        for child in self._children(expr):
            out |= self._collect_non_aggregated_vars(child)
        return out

    def _children(self, expr: Expression) -> list[Expression]:
        if isinstance(expr, BinaryExpr):
            return [expr.left, expr.right]
        if isinstance(expr, UnaryExpr):
            return [expr.operand]
        if isinstance(expr, NotExpr):
            return [expr.operand]
        if isinstance(expr, InExpr):
            return [expr.operand, *expr.options]
        if isinstance(expr, FunctionExpr):
            return list(expr.args)
        if isinstance(expr, RegexExpr):
            return [expr.text]
        if isinstance(expr, LangMatchesExpr):
            return [expr.tag, expr.range]
        if isinstance(expr, AggregateExpr):
            return [expr.expression] if expr.expression is not None else []
        if isinstance(expr, DateTimeExpr):
            return [expr.operand] if expr.operand is not None else []
        return []

    # --- Helpers ---------------------------------------------------------

    def _check_limit(self, limit: int | None, ctx: _Ctx) -> None:
        if limit is None:
            return
        if limit > self.policy.max_limit:
            ctx.error(
                "limit_too_high",
                f"LIMIT {limit} exceeds policy maximum {self.policy.max_limit}",
            )

    def _validate_construct_template(self, plan: ConstructPlan, ctx: _Ctx, scope: _Scope) -> None:
        # Variables in the template should be bound by WHERE.
        for i, t in enumerate(plan.template):
            ctx.path.append(("template", i))  # type: ignore[arg-type]
            try:
                for term in (t.subject, t.object):
                    if (
                        isinstance(term, Var)
                        and term.name not in scope.bound
                        and term.name not in scope.seen
                    ):
                        ctx.warn(
                            "construct_template_unbound_var",
                            f"CONSTRUCT template references ?{term.name} not bound by WHERE",
                        )
                if (
                    isinstance(t.predicate, Var)
                    and t.predicate.name not in scope.bound
                    and t.predicate.name not in scope.seen
                ):
                    ctx.warn(
                        "construct_template_unbound_var",
                        f"CONSTRUCT predicate ?{t.predicate.name} not bound by WHERE",
                    )
            finally:
                ctx.path.pop()

    def _resolve_iri(self, ref: Iri | PrefixedName, ctx: _Ctx) -> str | None:
        if isinstance(ref, Iri):
            return ref.value
        # PrefixedName
        full = ctx.prefixes.get(ref.prefix)
        if full is None:
            ctx.error(
                "unknown_prefix",
                f"prefix {ref.prefix!r} used but not declared in plan.prefixes",
                hint=f"Declare it (e.g. {{prefix: '{ref.prefix}', iri: 'http://...'}}).",
            )
            return None
        return full + ref.local

    def _var_name(self, v: object) -> str:
        if isinstance(v, Var):
            return v.name
        return ""


# --- Module-level helpers --------------------------------------------------


def _vars_in_pattern(p: Pattern) -> set[str]:
    """Best-effort enumeration of variable names appearing in a pattern."""
    out: set[str] = set()
    if isinstance(p, TriplePattern):
        for term in (p.subject, p.predicate, p.object):
            if isinstance(term, Var):
                out.add(term.name)
    elif isinstance(p, GroupPattern | OptionalPattern):
        for inner in p.patterns:
            out |= _vars_in_pattern(inner)
    elif isinstance(p, UnionPattern):
        for branch in p.branches:
            for inner in branch:
                out |= _vars_in_pattern(inner)
    elif isinstance(p, MinusPattern):
        for inner in p.patterns:
            out |= _vars_in_pattern(inner)
    elif isinstance(p, FilterPattern):
        out |= _vars_in_expr(p.expression)
    elif isinstance(p, BindPattern):
        out.add(p.var.name)
        out |= _vars_in_expr(p.expression)
    elif isinstance(p, ValuesPattern):
        out |= {v.name for v in p.variables}
    elif isinstance(p, GraphPattern):
        if isinstance(p.graph, Var):
            out.add(p.graph.name)
        for inner in p.patterns:
            out |= _vars_in_pattern(inner)
    elif isinstance(p, ServicePattern):
        for inner in p.patterns:
            out |= _vars_in_pattern(inner)
    elif isinstance(p, SubqueryPattern):
        for proj in p.select.projection:
            if proj.var is not None:
                out.add(proj.var.name)
            elif proj.alias is not None:
                out.add(proj.alias.name)
    return out


def _vars_in_expr(e: Expression) -> set[str]:
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, BoundExpr):
        return {e.var.name}
    out: set[str] = set()
    if isinstance(e, BinaryExpr):
        return _vars_in_expr(e.left) | _vars_in_expr(e.right)
    if isinstance(e, UnaryExpr):
        return _vars_in_expr(e.operand)
    if isinstance(e, NotExpr):
        return _vars_in_expr(e.operand)
    if isinstance(e, InExpr):
        out = _vars_in_expr(e.operand)
        for o in e.options:
            out |= _vars_in_expr(o)
        return out
    if isinstance(e, FunctionExpr):
        for a in e.args:
            out |= _vars_in_expr(a)
        return out
    if isinstance(e, RegexExpr):
        return _vars_in_expr(e.text)
    if isinstance(e, LangMatchesExpr):
        return _vars_in_expr(e.tag) | _vars_in_expr(e.range)
    if isinstance(e, AggregateExpr):
        return _vars_in_expr(e.expression) if e.expression is not None else set()
    if isinstance(e, DateTimeExpr):
        return _vars_in_expr(e.operand) if e.operand is not None else set()
    if isinstance(e, (NotExistsExpr, ExistsExpr)):
        for p in e.patterns:
            out |= _vars_in_pattern(p)
        return out
    if isinstance(e, LiteralValue):
        return set()
    if isinstance(e, (Iri, PrefixedName)):
        return set()
    return set()
