"""IR-level structural matching for golden-case requirements.

This module walks a :class:`graph_mcp.models.QueryPlan` and reports whether
it satisfies the structural requirements described in
:class:`evals.models.GoldenCaseExpected`. It does **not** depend on rendered
SPARQL substring matching.
"""

from __future__ import annotations

from typing import Any

from evals.models import (
    AggregateSpec,
    FilterSpec,
    OrderBySpec,
    TripleSpec,
)
from graph_mcp.models import (
    AggregateExpr,
    BinaryExpr,
    BoundExpr,
    ExistsExpr,
    FilterPattern,
    FunctionExpr,
    GraphPattern,
    GroupPattern,
    InExpr,
    Iri,
    LiteralValue,
    MinusPattern,
    NotExistsExpr,
    NotExpr,
    OptionalPattern,
    Pattern,
    PrefixedName,
    QueryPlan,
    RegexExpr,
    SelectPlan,
    ServicePattern,
    SubqueryPattern,
    TriplePattern,
    UnaryExpr,
    UnionPattern,
    Var,
)

# --- Helpers --------------------------------------------------------------


def _term_str(term: object, prefixes: dict[str, str] | None = None) -> str:
    """Render a term for textual comparison against a TripleSpec slot."""
    if isinstance(term, Var):
        return f"?{term.name}"
    if isinstance(term, Iri):
        return term.value
    if isinstance(term, PrefixedName):
        return f"{term.prefix}:{term.local}"
    if isinstance(term, LiteralValue):
        return repr(term.value)
    return str(term)


# Built-in prefixes the structural matcher can always expand. Plan-level
# prefixes (gathered from a ``QueryPlan.prefixes`` list at match time) are
# layered on top of these, so an ``ex:Acme`` spec matches a plan that uses
# ``<http://example.org/Acme>``.
_BUILTIN_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "ex": "http://example.org/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dct": "http://purl.org/dc/terms/",
}


def _normalize_term(text: str, prefixes: dict[str, str] | None) -> str:
    """Expand ``prefix:local`` to a full IRI when a prefix is known."""
    if ":" not in text or text.startswith(("?", "$", "<", "http://", "https://")):
        return text
    prefix, _, local = text.partition(":")
    base = (prefixes or {}).get(prefix) or _BUILTIN_PREFIXES.get(prefix)
    return f"{base}{local}" if base else text


def _matches_slot(spec_slot: str, actual: str, *, prefixes: dict[str, str] | None = None) -> bool:
    """Match one triple-slot specification against an actual term.

    A spec slot starting with ``?`` or ``$`` matches any variable in that
    slot (regardless of name). A spec slot of ``?_`` is the wildcard.
    Otherwise the slot is normalized to absolute-IRI form (using the plan's
    prefixes plus a builtin set) before being compared verbatim, so the
    matcher accepts both ``ex:Acme`` and ``http://example.org/Acme``.
    """
    if spec_slot.startswith("?_"):
        return True
    if spec_slot.startswith(("?", "$")):
        return actual.startswith(("?", "$"))
    return _normalize_term(spec_slot, prefixes) == _normalize_term(actual, prefixes)


def _plan_prefixes(plan: object) -> dict[str, str]:
    """Collect ``prefix → IRI`` declarations from a plan."""
    out: dict[str, str] = {}
    for p in getattr(plan, "prefixes", None) or []:
        prefix = getattr(p, "prefix", None)
        iri = getattr(p, "iri", None)
        if prefix and iri:
            out[prefix] = iri
    return out


def _walk_patterns(patterns: list[Pattern]) -> list[Pattern]:
    """Yield every pattern in the tree, including nested ones."""
    out: list[Pattern] = []
    for p in patterns:
        out.append(p)
        if isinstance(
            p, (GroupPattern, OptionalPattern, MinusPattern, GraphPattern, ServicePattern)
        ):
            out.extend(_walk_patterns(p.patterns))
        elif isinstance(p, UnionPattern):
            for branch in p.branches:
                out.extend(_walk_patterns(branch))
        elif isinstance(p, FilterPattern):
            # FILTER NOT EXISTS / EXISTS may contain nested patterns.
            for inner in _exists_inner_patterns(p.expression):
                out.extend(_walk_patterns(inner))
        elif isinstance(p, SubqueryPattern):
            out.extend(_walk_patterns(p.select.where))
    return out


def _exists_inner_patterns(expr: Any) -> list[list[Pattern]]:
    """Return the inner pattern lists of any nested EXISTS / NOT EXISTS."""
    out: list[list[Pattern]] = []
    if isinstance(expr, (ExistsExpr, NotExistsExpr)):
        out.append(list(expr.patterns))
    if isinstance(expr, BinaryExpr):
        out.extend(_exists_inner_patterns(expr.left))
        out.extend(_exists_inner_patterns(expr.right))
    if isinstance(expr, UnaryExpr):
        out.extend(_exists_inner_patterns(expr.operand))
    if isinstance(expr, NotExpr):
        out.extend(_exists_inner_patterns(expr.operand))
    if isinstance(expr, InExpr):
        out.extend(_exists_inner_patterns(expr.operand))
        for o in expr.options:
            out.extend(_exists_inner_patterns(o))
    if isinstance(expr, FunctionExpr):
        for a in expr.args:
            out.extend(_exists_inner_patterns(a))
    if isinstance(expr, RegexExpr):
        out.extend(_exists_inner_patterns(expr.text))
    if isinstance(expr, AggregateExpr) and expr.expression is not None:
        out.extend(_exists_inner_patterns(expr.expression))
    return out


def collect_pattern_kinds(plan: QueryPlan) -> set[str]:
    """Return the discriminator ``kind`` values of every pattern in the plan."""
    out: set[str] = set()
    where = plan.where if hasattr(plan, "where") else []
    for p in _walk_patterns(list(where)):
        kind = getattr(p, "kind", None)
        if isinstance(kind, str):
            out.add(kind)
    # Subqueries contribute ``subquery`` once at top level, but we also pick
    # up their inner patterns above.
    return out


# --- Triple matching ------------------------------------------------------


def count_matching_triples(plan: QueryPlan, spec: TripleSpec) -> int:
    """Count triples in the plan that match the spec."""
    count = 0
    prefixes = _plan_prefixes(plan)
    where = list(getattr(plan, "where", []))
    for p in _walk_patterns(where):
        if not isinstance(p, TriplePattern):
            continue
        if _matches_slot(spec.subject, _term_str(p.subject), prefixes=prefixes) and _matches_slot(
            spec.object, _term_str(p.object), prefixes=prefixes
        ):
            # Predicate may be a property path; in that case match only when
            # the spec is a wildcard variable.
            if isinstance(p.predicate, (Iri, PrefixedName, Var)):
                if _matches_slot(spec.predicate, _term_str(p.predicate), prefixes=prefixes):
                    count += 1
            else:
                # Property path predicate: only wildcard specs match.
                if spec.predicate.startswith("?_"):
                    count += 1
    return count


# --- Filter matching ------------------------------------------------------


def has_filter(plan: QueryPlan, spec: FilterSpec) -> bool:
    """Walk the WHERE clause and return True if any FILTER matches the spec."""
    for p in _walk_patterns(list(getattr(plan, "where", []))):
        if not isinstance(p, FilterPattern):
            continue
        if _filter_matches(p.expression, spec):
            return True
    return False


def _filter_matches(expr: Any, spec: FilterSpec) -> bool:
    if spec.kind == "not_exists":
        return isinstance(expr, NotExistsExpr) or _expr_contains(expr, NotExistsExpr)
    if spec.kind == "exists":
        return isinstance(expr, ExistsExpr) or _expr_contains(expr, ExistsExpr)
    if spec.kind == "bound":
        if not isinstance(expr, BoundExpr):
            return _expr_contains(expr, BoundExpr) and _bound_uses_var(expr, spec.var)
        return spec.var is None or expr.var.name == _strip_var(spec.var)
    if spec.kind == "regex":
        if isinstance(expr, RegexExpr):
            return True
        return _expr_contains(expr, RegexExpr)
    if spec.kind == "lang_equals":
        # Match: (LANG(?var) = "value")
        return _is_lang_equals(expr, spec.var, spec.value)
    if spec.kind == "compare":
        return _is_compare(expr, spec.op or "=", spec.var, spec.value)
    if spec.kind == "in":
        return _expr_contains(expr, InExpr)
    return False


def _strip_var(text: str) -> str:
    return text[1:] if text.startswith(("?", "$")) else text


def _expr_contains(expr: Any, target_type: type) -> bool:
    if isinstance(expr, target_type):
        return True
    return any(_expr_contains(child, target_type) for child in _expr_children(expr))


def _expr_children(expr: Any) -> list[Any]:
    if isinstance(expr, BinaryExpr):
        return [expr.left, expr.right]
    if isinstance(expr, UnaryExpr | NotExpr):
        return [expr.operand]
    if isinstance(expr, InExpr):
        return [expr.operand, *expr.options]
    if isinstance(expr, FunctionExpr):
        return list(expr.args)
    if isinstance(expr, RegexExpr):
        return [expr.text]
    if isinstance(expr, AggregateExpr):
        return [expr.expression] if expr.expression is not None else []
    return []


def _bound_uses_var(expr: Any, target_var: str | None) -> bool:
    if target_var is None:
        return _expr_contains(expr, BoundExpr)
    if isinstance(expr, BoundExpr) and expr.var.name == _strip_var(target_var):
        return True
    return any(_bound_uses_var(c, target_var) for c in _expr_children(expr))


def _is_lang_equals(expr: Any, var: str | None, value: Any) -> bool:
    """Recognize ``LANG(?var) = "value"`` regardless of operand order."""
    if not isinstance(expr, BinaryExpr) or expr.op != "=":
        return any(_is_lang_equals(c, var, value) for c in _expr_children(expr))
    for left, right in ((expr.left, expr.right), (expr.right, expr.left)):
        if (
            isinstance(left, FunctionExpr)
            and left.name == "lang"
            and isinstance(right, LiteralValue)
            and (var is None or _function_arg_matches_var(left, var))
            and right.value == value
        ):
            return True
    return False


def _function_arg_matches_var(fn: FunctionExpr, var: str) -> bool:
    target = _strip_var(var)
    return any(isinstance(a, Var) and a.name == target for a in fn.args)


def _is_compare(expr: Any, op: str, var: str | None, value: Any) -> bool:
    if isinstance(expr, BinaryExpr) and expr.op == op:
        left, right = expr.left, expr.right
        for a, b in ((left, right), (right, left)):
            if (
                isinstance(a, Var)
                and isinstance(b, LiteralValue)
                and (var is None or a.name == _strip_var(var))
                and (value is None or b.value == value)
            ):
                return True
    return any(_is_compare(c, op, var, value) for c in _expr_children(expr))


# --- Aggregates / group by / order by ------------------------------------


def find_matching_aggregate(plan: QueryPlan, spec: AggregateSpec) -> bool:
    """Find a matching aggregate anywhere in the plan, including nested subqueries."""
    if _projection_has_matching_aggregate(getattr(plan, "projection", []), spec):
        return True
    # Walk subqueries that may live in the WHERE clause.
    for p in _walk_patterns(list(getattr(plan, "where", []))):
        if isinstance(p, SubqueryPattern) and find_matching_aggregate(p.select, spec):
            return True
    return False


def _projection_has_matching_aggregate(projection: list[Any], spec: AggregateSpec) -> bool:
    for proj in projection:
        if getattr(proj, "expression", None) is None:
            continue
        agg = _find_aggregate(proj.expression, spec.function)
        if agg is None:
            continue
        if spec.expression is None and agg.expression is None:
            return spec.alias is None or (
                proj.alias is not None and proj.alias.name == _strip_var(spec.alias)
            )
        if (
            spec.expression is not None
            and agg.expression is not None
            and isinstance(agg.expression, Var)
            and agg.expression.name == _strip_var(spec.expression)
            and (
                spec.alias is None
                or (proj.alias is not None and proj.alias.name == _strip_var(spec.alias))
            )
        ):
            return True
    return False


def _find_aggregate(expr: Any, function: str) -> AggregateExpr | None:
    if isinstance(expr, AggregateExpr) and expr.function == function:
        return expr
    for c in _expr_children(expr):
        found = _find_aggregate(c, function)
        if found is not None:
            return found
    return None


def has_group_by_var(plan: QueryPlan, var_or_expr: str) -> bool:
    if not isinstance(plan, SelectPlan):
        return False
    target = _strip_var(var_or_expr)
    return any(isinstance(g, Var) and g.name == target for g in plan.group_by)


def has_order_by(plan: QueryPlan, spec: OrderBySpec) -> bool:
    if not isinstance(plan, SelectPlan):
        return False
    target = _strip_var(spec.expression)
    return any(
        isinstance(oc.expression, Var)
        and oc.expression.name == target
        and oc.descending == spec.descending
        for oc in plan.order_by
    )


# --- Binding accuracy ----------------------------------------------------


def _expand_prefixed(value: str, prefixes: dict[str, str] | None) -> str:
    """Expand ``prefix:local`` to a full IRI when ``prefix`` is in ``prefixes``."""
    if not prefixes or ":" not in value or value.startswith(("http://", "https://")):
        return value
    prefix, _, local = value.partition(":")
    base = prefixes.get(prefix)
    if base is None:
        return value
    return f"{base}{local}"


def _is_numeric(s: str) -> bool:
    try:
        float(s)
    except (TypeError, ValueError):
        return False
    return True


def _values_match(expected: str, actual: str, *, prefixes: dict[str, str] | None) -> bool:
    """Compare a single binding value, handling IRIs, numerics, and literals.

    The matcher is intentionally permissive: rdflib normalizes typed literals
    in ways that may differ from the IRI form a golden case writes
    (``http://example.org/alice`` vs ``ex:alice``). It accepts:

    - exact string equality;
    - prefixed-name expansion in either direction;
    - numeric equality (``2`` matches ``2.0`` and ``"2"^^xsd:integer``);
    - typed-literal lexical match after stripping a trailing ``^^<datatype>``.
    """
    if expected == actual:
        return True

    expanded_expected = _expand_prefixed(expected, prefixes)
    expanded_actual = _expand_prefixed(actual, prefixes)
    if expanded_expected == expanded_actual:
        return True

    # Numeric comparison.
    if _is_numeric(expected) and _is_numeric(actual):
        return float(expected) == float(actual)

    # Strip ``"value"^^<datatype>`` syntax from either side.
    def _lex(s: str) -> str:
        if s.startswith('"') and '"^^' in s:
            return s[1 : s.index('"^^')]
        return s

    return _lex(expected) == _lex(actual)


def matches_bindings(
    actual_rows: list[dict[str, str]],
    expected: dict[str, str],
    *,
    prefixes: dict[str, str] | None = None,
) -> bool:
    """Return True if ``expected`` is a subset of any actual row.

    ``prefixes`` lets callers expand ``ex:alice`` to ``http://example.org/alice``
    when comparing — useful because the golden cases use prefixed names but
    rdflib emits absolute IRIs.
    """
    return any(
        all(_values_match(v, row.get(k, ""), prefixes=prefixes) for k, v in expected.items())
        for row in actual_rows
    )
