"""IR-level structural matching for golden-case requirements.

This module walks a :class:`graph_mcp.models.QueryPlan` and reports whether
it satisfies the structural requirements described in
:class:`evals.models.GoldenCaseExpected`. It does **not** depend on rendered
SPARQL substring matching.

The matcher tolerates the kinds of cosmetic variation a competent LLM
planner introduces: it accepts both ``ex:Acme`` and ``<http://example.org/Acme>``
in triple slots and bindings, accepts ``LANG(?x) = "en"`` and
``langMatches(lang(?x), "en")`` for ``lang_equals`` filters, and accepts
common variable aliases (``?p`` ↔ ``?person``, ``?b`` ↔ ``?person`` for a
single-column path result, etc.). Brittle exact-name matching at the eval
layer creates false negatives that mask the planner's real failure modes.
"""

from __future__ import annotations

from typing import Any

from evals.models import (
    AggregateSpec,
    FilterSpec,
    OrderBySpec,
    PropertyPathSpec,
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
    LangMatchesExpr,
    LiteralValue,
    MinusPattern,
    NotExistsExpr,
    NotExpr,
    OptionalPattern,
    Pattern,
    PrefixedName,
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
# ``<http://example.org/Acme>`` even when the plan declared no prefixes.
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
    """Expand ``prefix:local`` to a full IRI when a prefix is known.

    Plan-declared prefixes win over the built-in map; the built-ins are a
    fallback for cases where the plan emits absolute IRIs without declaring
    ``ex`` / ``rdfs`` etc.
    """
    if ":" not in text or text.startswith(("?", "$", "<", "http://", "https://")):
        return text
    prefix, _, local = text.partition(":")
    base = (prefixes or {}).get(prefix) or _BUILTIN_PREFIXES.get(prefix)
    return f"{base}{local}" if base else text


def _matches_slot(spec_slot: str, actual: str, *, prefixes: dict[str, str] | None = None) -> bool:
    """Match one triple-slot specification against an actual term.

    A spec slot starting with ``?`` or ``$`` matches any variable in that
    slot. A spec slot of ``?_`` is the wildcard. Otherwise the slot is
    normalized to absolute-IRI form before being compared, so the matcher
    accepts both ``ex:Acme`` and ``http://example.org/Acme``.
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
            for inner in _exists_inner_patterns(p.expression):
                out.extend(_walk_patterns(inner))
        elif isinstance(p, SubqueryPattern):
            out.extend(_walk_patterns(p.select.where))
    return out


def _exists_inner_patterns(expr: Any) -> list[list[Pattern]]:
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
    return out


# --- Variable alias matching ---------------------------------------------

DEFAULT_VAR_ALIASES: dict[str, tuple[str, ...]] = {
    "p": ("p", "person", "employee", "entity", "x", "s", "individual"),
    "person": ("person", "p", "employee", "entity", "x", "s"),
    "employee": ("employee", "p", "person"),
    "a": ("a", "subject", "s", "person"),
    "b": ("b", "target", "object", "person", "o", "knownperson"),
    "c": ("c", "company", "org", "employer"),
    "company": ("company", "c", "org", "organization", "employer"),
    "n": ("n", "count", "employeecount", "personcount", "total"),
    "dbl": ("dbl", "double", "doubled", "agedoubled", "doubleage"),
    "lbl": ("lbl", "label", "l", "name"),
    "label": ("label", "lbl", "l", "name"),
    "age": ("age", "yrs", "years"),
    "company_max": ("maxage", "max_age"),
    "d": ("d", "date", "joined", "joindate"),
    "g": ("g", "graph", "namedgraph"),
    "s": ("s", "subject", "x", "p", "person"),
}


def _strip_var(text: str) -> str:
    return text[1:] if text.startswith(("?", "$")) else text


def _aliases_for(name: str, *, extra: dict[str, list[str]] | None = None) -> set[str]:
    """Return the lowercase alias set for ``name``.

    Extra aliases (e.g. from a golden case's ``binding_aliases``) extend the
    default map. The mention itself is always part of its own alias set.
    """
    n = _strip_var(name).lower()
    out: set[str] = {n}
    if extra and n in extra:
        out.update(a.lower() for a in extra[n])
    if n in DEFAULT_VAR_ALIASES:
        out.update(DEFAULT_VAR_ALIASES[n])
    return out


def _var_matches_expected(
    actual: str, expected: str | None, *, extra: dict[str, list[str]] | None = None
) -> bool:
    """Match a variable name against an expected name with alias tolerance.

    Compares lowercase, accepts the alias map, and treats ``None`` as wildcard.
    """
    if expected is None:
        return True
    a = _strip_var(actual).lower()
    if a == _strip_var(expected).lower():
        return True
    return a in _aliases_for(expected, extra=extra)


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
            if isinstance(p.predicate, (Iri, PrefixedName, Var)):
                if _matches_slot(spec.predicate, _term_str(p.predicate), prefixes=prefixes):
                    count += 1
            else:
                # Property path predicate: only wildcard specs match.
                if spec.predicate.startswith("?_"):
                    count += 1
    return count


# --- Property path matching ----------------------------------------------


_PATH_OPERATOR_CLASSES = {
    "one_or_more": PropertyPathOneOrMore,
    "zero_or_more": PropertyPathZeroOrMore,
    "zero_or_one": PropertyPathZeroOrOne,
    "sequence": PropertyPathSeq,
    "alternative": PropertyPathAlt,
    "inverse": PropertyPathInverse,
    "term": PropertyPathTerm,
}


def has_property_path(plan: QueryPlan, spec: PropertyPathSpec) -> bool:
    """Return True if the plan contains a triple whose predicate is the
    requested property-path operator wrapping ``spec.predicate``.
    """
    target_cls = _PATH_OPERATOR_CLASSES.get(spec.operator)
    if target_cls is None:
        return False
    prefixes = _plan_prefixes(plan)
    for p in _walk_patterns(list(getattr(plan, "where", []))):
        if not isinstance(p, TriplePattern):
            continue
        if not isinstance(p.predicate, target_cls):  # type: ignore[arg-type]
            continue
        # The atomic operand must reference the requested predicate.
        operand = getattr(p.predicate, "operand", None)
        if isinstance(operand, PropertyPathTerm):
            inner = operand.iri
            if not _matches_slot(spec.predicate, _term_str(inner), prefixes=prefixes):
                continue
        elif target_cls is PropertyPathTerm:
            inner = p.predicate.iri  # type: ignore[union-attr]
            if not _matches_slot(spec.predicate, _term_str(inner), prefixes=prefixes):
                continue
        if not _matches_slot(spec.subject, _term_str(p.subject), prefixes=prefixes):
            continue
        if not _matches_slot(spec.object, _term_str(p.object), prefixes=prefixes):
            continue
        return True
    return False


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
        return _is_lang_equals(expr, spec.var, spec.value)
    if spec.kind == "compare":
        return _is_compare(expr, spec.op or "=", spec.var, spec.value)
    if spec.kind == "in":
        return _expr_contains(expr, InExpr)
    return False


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
    if isinstance(expr, LangMatchesExpr):
        return [expr.tag, expr.range]
    if isinstance(expr, AggregateExpr):
        return [expr.expression] if expr.expression is not None else []
    return []


def _bound_uses_var(expr: Any, target_var: str | None) -> bool:
    if target_var is None:
        return _expr_contains(expr, BoundExpr)
    if isinstance(expr, BoundExpr) and expr.var.name == _strip_var(target_var):
        return True
    return any(_bound_uses_var(c, target_var) for c in _expr_children(expr))


def _lang_call_var(fn_or_expr: Any) -> str | None:
    """If ``fn_or_expr`` is ``LANG(?x)`` (or wraps one), return the variable name."""
    if isinstance(fn_or_expr, FunctionExpr) and fn_or_expr.name == "lang":
        for a in fn_or_expr.args:
            if isinstance(a, Var):
                return a.name
    return None


def _literal_string(expr: Any) -> str | None:
    if isinstance(expr, LiteralValue):
        return str(expr.value)
    return None


def _is_lang_equals(expr: Any, var: str | None, value: Any) -> bool:
    """Recognize ``LANG(?var) = "en"`` *or* ``langMatches(lang(?var), "en")``.

    Both forms appear in the wild; the matcher accepts either, with variable
    alias tolerance for ``?var`` (``?lbl`` ↔ ``?label`` ↔ ``?l``) and the
    literal compared case-insensitively (``"en"`` vs ``"EN"``).
    """
    target = (str(value) if value is not None else "").lower()

    # LANG(?x) = "..."
    if isinstance(expr, BinaryExpr) and expr.op == "=":
        for left, right in ((expr.left, expr.right), (expr.right, expr.left)):
            v = _lang_call_var(left)
            lit = _literal_string(right)
            if v is not None and lit is not None:
                if not _var_matches_expected(v, var):
                    continue
                if not target or lit.lower() == target:
                    return True

    # langMatches(lang(?x), "...")
    if isinstance(expr, LangMatchesExpr):
        v = _lang_call_var(expr.tag)
        lit = _literal_string(expr.range)
        if (
            v is not None
            and lit is not None
            and _var_matches_expected(v, var)
            and (not target or lit.lower() == target)
        ):
            return True
    if (
        isinstance(expr, FunctionExpr)
        and expr.name in {"langmatches", "lang_matches"}
        and len(expr.args) >= 2
    ):
        v = _lang_call_var(expr.args[0])
        lit = _literal_string(expr.args[1])
        if (
            v is not None
            and lit is not None
            and _var_matches_expected(v, var)
            and (not target or lit.lower() == target)
        ):
            return True

    return any(_is_lang_equals(c, var, value) for c in _expr_children(expr))


def _is_compare(expr: Any, op: str, var: str | None, value: Any) -> bool:
    if isinstance(expr, BinaryExpr) and expr.op == op:
        for a, b in ((expr.left, expr.right), (expr.right, expr.left)):
            if (
                isinstance(a, Var)
                and isinstance(b, LiteralValue)
                and _var_matches_expected(a.name, var)
                and (value is None or b.value == value)
            ):
                return True
    return any(_is_compare(c, op, var, value) for c in _expr_children(expr))


# --- Aggregates / group by / order by ------------------------------------


def find_matching_aggregate(plan: QueryPlan, spec: AggregateSpec) -> bool:
    """Find a matching aggregate anywhere in the plan, including subqueries.

    The aggregate also matches when it appears in a ``HAVING`` expression of
    a SelectPlan (renderers don't expose having aggregates as projection
    items, but they're still semantically present).
    """
    if _projection_has_matching_aggregate(getattr(plan, "projection", []), spec):
        return True
    # Walk HAVING clauses for the aggregate-in-having pattern.
    if isinstance(plan, SelectPlan):
        for h in plan.having:
            if _expr_has_matching_aggregate(h, spec):
                return True
    # Walk subqueries that may live in the WHERE clause.
    for p in _walk_patterns(list(getattr(plan, "where", []))):
        if isinstance(p, SubqueryPattern) and find_matching_aggregate(p.select, spec):
            return True
    return False


def _aggregate_expression_matches(agg: AggregateExpr, spec: AggregateSpec) -> bool:
    """Compare a concrete ``AggregateExpr`` to a structural ``AggregateSpec``.

    Spec semantics:

    - ``expression == None`` — wildcard. Any aggregate of the right function
      passes (this is the most permissive default and is what the live eval
      needs since LLM-generated variable names are not predictable).
    - ``expression == "*"`` — strict ``COUNT(*)`` (or other aggregate with
      ``expression is None``).
    - ``expression == "?_"`` — explicit wildcard variable; equivalent to
      omitting expression.
    - ``expression == "?p"`` — match the variable name with alias tolerance.
    """
    if spec.expression is None or spec.expression == "?_":
        return True
    if spec.expression == "*":
        return agg.expression is None
    target = _strip_var(spec.expression)
    if agg.expression is None:
        return False
    if isinstance(agg.expression, Var):
        return _var_matches_expected(agg.expression.name, target)
    return False


def _alias_matches(actual_alias: Any, spec_alias: str | None) -> bool:
    if spec_alias is None:
        return True
    if actual_alias is None:
        return False
    return _var_matches_expected(getattr(actual_alias, "name", ""), spec_alias)


def _projection_has_matching_aggregate(projection: list[Any], spec: AggregateSpec) -> bool:
    for proj in projection:
        if getattr(proj, "expression", None) is None:
            continue
        agg = _find_aggregate(proj.expression, spec.function)
        if agg is None:
            continue
        if _aggregate_expression_matches(agg, spec) and _alias_matches(
            getattr(proj, "alias", None), spec.alias
        ):
            return True
    return False


def _expr_has_matching_aggregate(expr: Any, spec: AggregateSpec) -> bool:
    if (
        isinstance(expr, AggregateExpr)
        and expr.function == spec.function
        and _aggregate_expression_matches(expr, spec)
    ):
        return True
    return any(_expr_has_matching_aggregate(c, spec) for c in _expr_children(expr))


def _find_aggregate(expr: Any, function: str) -> AggregateExpr | None:
    if isinstance(expr, AggregateExpr) and expr.function == function:
        return expr
    for c in _expr_children(expr):
        found = _find_aggregate(c, function)
        if found is not None:
            return found
    return None


def has_group_by_var(plan: QueryPlan, var_or_expr: str) -> bool:
    """Match a GROUP BY variable with alias tolerance."""
    if not isinstance(plan, SelectPlan):
        return False
    target = _strip_var(var_or_expr)
    return any(isinstance(g, Var) and _var_matches_expected(g.name, target) for g in plan.group_by)


def has_order_by(plan: QueryPlan, spec: OrderBySpec) -> bool:
    if not isinstance(plan, SelectPlan):
        return False
    target = _strip_var(spec.expression)
    return any(
        isinstance(oc.expression, Var)
        and _var_matches_expected(oc.expression.name, target)
        and oc.descending == spec.descending
        for oc in plan.order_by
    )


# --- Binding accuracy ----------------------------------------------------


def _expand_prefixed(value: str, prefixes: dict[str, str] | None) -> str:
    """Expand ``prefix:local`` to a full IRI when the prefix is known.

    Falls back to the built-in prefix map so an expected ``ex:alice`` matches
    an actual ``http://example.org/alice`` even when the plan never declared
    the ``ex`` prefix (the renderer may emit absolute IRIs in that case).
    """
    if ":" not in value or value.startswith(("http://", "https://")):
        return value
    prefix, _, local = value.partition(":")
    base = (prefixes or {}).get(prefix) or _BUILTIN_PREFIXES.get(prefix)
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
    """Compare a single binding value, handling IRIs, numerics, and literals."""
    if expected == actual:
        return True
    expanded_expected = _expand_prefixed(expected, prefixes)
    expanded_actual = _expand_prefixed(actual, prefixes)
    if expanded_expected == expanded_actual:
        return True
    if _is_numeric(expected) and _is_numeric(actual):
        return float(expected) == float(actual)

    def _lex(s: str) -> str:
        if s.startswith('"') and '"^^' in s:
            return s[1 : s.index('"^^')]
        return s

    return _lex(expected) == _lex(actual)


def _row_matches_expected_with_aliases(
    row: dict[str, str],
    expected: dict[str, str],
    *,
    prefixes: dict[str, str] | None,
    binding_aliases: dict[str, list[str]] | None,
) -> bool:
    """Match an expected row to ``row`` allowing variable name aliasing.

    Multi-column expected rows use a small backtracking search so each
    expected key maps to a distinct actual column. Single-column rows fall
    through to a permissive search across all actual values.
    """
    keys = list(expected.keys())

    # Single-column shortcut: if the expected row has one column, accept
    # any actual binding whose value matches. This handles the common case
    # where the LLM names its projection ``?person`` while the golden case
    # uses ``?p``.
    if len(keys) == 1:
        k = keys[0]
        v = expected[k]
        if any(_values_match(v, av, prefixes=prefixes) for av in row.values()):
            return True

    # Multi-column: try a direct mapping first.
    if all(_values_match(v, row.get(k, ""), prefixes=prefixes) for k, v in expected.items()):
        return True

    # Multi-column with alias matching: pair each expected key with an
    # actual key whose name aliases match and whose value matches.
    available = list(row.keys())
    used: set[str] = set()

    def _backtrack(i: int) -> bool:
        if i == len(keys):
            return True
        k = keys[i]
        v = expected[k]
        candidates = [
            a
            for a in available
            if a not in used and _var_matches_expected(a, k, extra=binding_aliases)
        ]
        # Fall back to ALL unused columns if no alias-named match works
        # (covers the case where the LLM picks a name we haven't seen).
        for a in candidates or [a for a in available if a not in used]:
            actual_v = row.get(a, "")
            if _values_match(v, actual_v, prefixes=prefixes):
                used.add(a)
                if _backtrack(i + 1):
                    return True
                used.remove(a)
        return False

    return _backtrack(0)


def matches_bindings(
    actual_rows: list[dict[str, str]],
    expected: dict[str, str],
    *,
    prefixes: dict[str, str] | None = None,
    binding_aliases: dict[str, list[str]] | None = None,
) -> bool:
    """Return True if any actual row matches ``expected`` (with alias tolerance).

    ``prefixes`` lets callers expand ``ex:alice`` to ``http://example.org/alice``
    (the built-in prefix map is also consulted).

    ``binding_aliases`` lets a golden case override the default variable-name
    aliasing, e.g. ``{"a": ["A"], "b": ["B"]}``.
    """
    return any(
        _row_matches_expected_with_aliases(
            row, expected, prefixes=prefixes, binding_aliases=binding_aliases
        )
        for row in actual_rows
    )
