---
id: validator
title: Validator
sidebar_position: 4
description: How QueryPlanValidator turns a parsed plan into a structured ValidationResult.
---

# Validator

`QueryPlanValidator` (`src/graph_mcp/compiler/validator.py`) is the
deterministic safety check that runs between the IR and the renderer.
It produces a structured `ValidationResult` so the LLM can repair plans
without parsing prose.

## Phases

1. **Prefix resolution.** Seed `ctx.prefixes` from `DEFAULT_PREFIXES`,
   then layer the plan's `prefixes` on top. Detect:
   - same prefix declared twice with different IRIs → `prefix_conflict`;
   - built-in redefinition with a different IRI →
     `default_prefix_override` (unless
     `policy.allow_default_prefix_override`).
2. **Plan-shape dispatch.** `SelectPlan`, `AskPlan`, and `ConstructPlan`
   each get their own walker. Subquery `SelectPlan`s skip the prefix
   step (only the top-level plan declares prefixes; redeclaring inside
   a subquery is `subquery_prefixes_not_allowed`).
3. **WHERE walk.** A depth-first traversal of the pattern tree with a
   `_Scope` per nesting level tracks which variables are *bound* vs
   merely *seen* (introduced inside an OPTIONAL, for example).
4. **Projection / GROUP BY / HAVING / ORDER BY.** The validator
   enforces the standard SPARQL aggregate rules: every projected
   variable must either be in `GROUP BY` or wrapped in an aggregate.
5. **LIMIT / OFFSET checks.** `limit > policy.max_limit` →
   `limit_too_high`. (The `query_graph` tool caps the LIMIT *before*
   calling the validator; `render_sparql` does not.)

The full file is small enough to read in one sitting (~950 lines
including helper methods).

## Scoping rules

`_Scope` distinguishes:

- `bound` — variables guaranteed to be bound at this point;
- `seen` — variables that *may* be bound (e.g. inside an OPTIONAL);
- `values_constraints` — for each variable, the set of IRIs a
  preceding `VALUES` has constrained it to (used for the
  `GRAPH ?g` allowlist proof).

Merge functions:

- `merge_required` — used after a `GROUP { ... }` or sequential pattern;
  promotes inner bindings out.
- `merge_optional` — only adds bindings to `seen`.
- `merge_union` — variables bound in **every** branch are guaranteed
  outward; variables bound in **any** branch are seen.

`MINUS` does not propagate any bindings outward; that's intentional.

## OPTIONAL / FILTER warnings

`_check_filter_placement_warning` warns
(`filter_after_optional`) when a FILTER references a variable that is
only in `seen` and the FILTER does not use `BOUND(?var)`. This helps
the LLM realize that an unguarded FILTER outside the OPTIONAL will
silently drop rows where `?var` is unbound.

## EXISTS / NOT EXISTS

`_validate_nested_exists` recurses into `ExistsExpr` and
`NotExistsExpr` patterns:

- inner SERVICE / GRAPH / property-path / depth / triple-count
  policies are enforced;
- variables introduced **only** inside the EXISTS/NOT EXISTS do not
  leak outward.

## Subquery scoping

`SubqueryPattern` opens a fresh `_Scope`. Only the variables in the
subquery's `projection` (or `SELECT *` projection list) become bound
in the outer scope. Inner bindings are isolated.

## Aggregate rules

`_validate_projection`, `_validate_having`, and the ORDER BY walker
all share the `_collect_non_aggregated_vars` helper. A variable is
"non-aggregated" if it appears outside every enclosing
`AggregateExpr`. In an aggregate query (any aggregate in projection
or non-empty `GROUP BY`), every non-aggregated variable must be in
`GROUP BY`.

Errors:

- `non_grouped_projection` — projected variable not in `GROUP BY`.
- `non_grouped_in_expression` — variable used outside an aggregate
  inside a projected expression but not in `GROUP BY`.
- `having_non_grouped_var` — `HAVING` references a non-grouped variable
  outside an aggregate.
- `order_by_non_grouped` — `ORDER BY` does the same.

## Graph allowlist rules

When `policy.allowed_graphs` is non-empty:

- `GRAPH <iri> { ... }` — the IRI is checked against the allowlist; a
  miss yields `graph_not_allowed`.
- `GRAPH ?g { ... }` — only allowed when a sibling `VALUES ?g { allowlisted IRIs }` precedes it in the same required-group scope.
  Errors:
  - `graph_variable_not_allowed` — no preceding `VALUES`;
  - `graph_values_constraint_empty` — empty intersection (the query would never bind `?g`);
  - `graph_values_not_allowed` — at least one `VALUES` IRI is not on the allowlist.

VALUES constraints from inside OPTIONAL / UNION / MINUS / FILTER
EXISTS / subqueries do not propagate to the outer scope.

## Property-path rules

`_validate_property_path`:

- complexity = number of nodes in the path tree;
- exceeds `policy.max_property_path_complexity` →
  `property_path_too_complex`;
- contains `*` or `+` and `policy.allow_unbounded_paths` is `false` →
  `unbounded_property_path`;
- every predicate IRI is resolved against `ctx.prefixes` (catches
  `unknown_prefix`) and checked against
  `policy.allowed_path_predicates` (when non-empty) →
  `path_predicate_not_allowed`.

## SERVICE rules

`ServicePattern` resolves the endpoint IRI; if absent or not on the
`policy.allowed_service_endpoints` allowlist, `service_not_allowed`.

The same allowlist is applied independently by the raw-SPARQL
scanner — see [Raw SPARQL mode](/users/raw-sparql-mode/).

## Limit / depth / triple count

- `policy.max_query_depth` — incremented on every `_validate_where`
  call. Exceeds → `max_depth_exceeded`.
- `policy.max_triple_patterns` — incremented per
  `TriplePattern`. Exceeds → `too_many_triples`.
- `policy.max_limit` — every plan's `limit` (subquery and top-level)
  must be `<= max_limit`. Exceeds → `limit_too_high`.

## Common error codes

The full list (with explanations) is in
[Validation errors reference](/reference/validation-errors/). Highlights:

- `unknown_prefix` — a `prefix:local` reference whose prefix isn't in
  the (built-ins ∪ plan.prefixes) map.
- `prefix_conflict` — same prefix declared twice with different IRIs.
- `default_prefix_override` — built-in prefix redefined.
- `bind_rebind` — `BIND` reuses an already-bound variable name.
- `filter_var_unbound` — expression references a free variable that
  is not in scope.
- `unbound_projection_var` — projected variable never bound by the
  WHERE clause.
- `aggregate_outside_projection_or_having` — aggregate used somewhere
  it isn't allowed.

## Returned shape

Always a `ValidationResult`:

```python
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue]
    @property
    def errors(self) -> list[ValidationIssue]: ...
    @property
    def warnings(self) -> list[ValidationIssue]: ...
```

`ok` is `False` whenever any issue has `severity="error"`. Warnings
do not flip `ok` — they are advisory.
