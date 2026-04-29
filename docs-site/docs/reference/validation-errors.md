---
id: validation-errors
title: Validation errors
sidebar_position: 5
description: Every validation issue code emitted by QueryPlanValidator, with one-line guidance.
---

# Validation errors

The validator emits structured `ValidationIssue` records:

```json
{
  "severity": "error" | "warning",
  "code": "unknown_prefix",
  "message": "prefix 'ex' used but not declared in plan.prefixes",
  "path": ["where", 0, "predicate"],
  "hint": "Declare it (e.g. {prefix: 'ex', iri: 'http://...'})."
}
```

Every code below is emitted by
[`graph_mcp/compiler/validator.py`](https://github.com/graph-mcp/graph-mcp/blob/main/src/graph_mcp/compiler/validator.py).
This list is maintained by hand — when you add a code, update this
page.

## Errors

| Code | When | What to do |
| --- | --- | --- |
| `aggregate_outside_projection_or_having` | An aggregate appears in a place that is not projection / `HAVING`. | Move the aggregate into projection or `HAVING`, or drop it. |
| `alias_collision` | A projection alias collides with an existing variable or another alias. | Rename the alias. |
| `bind_rebind` | `BIND ... AS ?v` reuses a name that is already in scope. | Use a fresh variable name. |
| `default_prefix_override` | Plan redefines a built-in prefix (`rdf`, `rdfs`, `xsd`, `owl`, `skos`, `dct`, `foaf`) to a non-canonical IRI. | Drop the redefinition, or set `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true` (rarely correct). |
| `duplicate_projection` | Same output name twice in the projection list. | Rename one of them. |
| `filter_var_unbound` | `FILTER` / `BIND` / projection expression references a free variable that is not in scope. | Bind the variable in the WHERE clause first. |
| `graph_not_allowed` | `GRAPH <iri>` IRI is not on `GRAPH_MCP_ALLOWED_GRAPHS`. | Add the IRI to the allowlist or remove the GRAPH block. |
| `graph_values_constraint_empty` | `GRAPH ?g` is constrained by a `VALUES` whose intersection is empty. | Fix the `VALUES` clauses or drop the GRAPH block. |
| `graph_values_not_allowed` | `GRAPH ?g` is constrained by `VALUES` to IRIs not all on the allowlist. | Drop the disallowed IRIs from `VALUES`. |
| `graph_variable_not_allowed` | `GRAPH ?g` with no preceding `VALUES ?g` while the allowlist is non-empty. | Bind `?g` via a `VALUES` listing only allowlisted IRIs, placed before the GRAPH block in the same required group. |
| `having_non_grouped_var` | `HAVING` references a variable that is not in `GROUP BY` and not wrapped in an aggregate. | Wrap it in an aggregate or add it to `GROUP BY`. |
| `limit_too_high` | `LIMIT` exceeds `policy.max_limit`. | Lower the limit, or use `query_graph(max_rows=N)` to cap before validation. |
| `max_depth_exceeded` | Pattern nesting exceeds `policy.max_query_depth`. | Flatten the plan. |
| `non_grouped_in_expression` | A non-aggregated variable appears outside an aggregate inside a projected expression in an aggregate query. | Add it to `GROUP BY` or wrap it in an aggregate (`SAMPLE`, `MIN`, ...). |
| `non_grouped_projection` | A projected variable is not in `GROUP BY` and not wrapped in an aggregate. | Same fix as `non_grouped_in_expression`. |
| `order_by_non_grouped` | `ORDER BY` references a non-grouped variable in an aggregate query. | Project the variable as an alias and order on the alias. |
| `path_predicate_not_allowed` | A property-path predicate IRI is not on `GRAPH_MCP_ALLOWED_PATH_PREDICATES`. | Add it to the allowlist or rewrite the path. |
| `prefix_conflict` | Same prefix declared twice with different IRIs. | Drop the duplicate or align the IRIs. |
| `property_path_too_complex` | Path AST has more nodes than `policy.max_property_path_complexity`. | Simplify the path. |
| `service_not_allowed` | `SERVICE <iri>` IRI is not on `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS`. | Add it to the allowlist. |
| `subquery_prefixes_not_allowed` | A nested `SelectPlan` declares its own `prefixes`. | Move all `prefixes` to the top-level plan. |
| `too_many_triples` | Plan exceeds `policy.max_triple_patterns`. | Split into multiple smaller plans, or raise the cap. |
| `unbounded_property_path` | `*` or `+` used while `policy.allow_unbounded_paths=False`. | Remove the unbounded operator, or set `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=true`. |
| `unbound_group_var` | `GROUP BY ?v` where `?v` is not bound. | Bind it or drop the `GROUP BY` entry. |
| `unbound_projection_var` | A projected variable is never bound by the WHERE clause. | Add a triple pattern that binds it, or remove it from the projection. |
| `unknown_pattern` | Internal: pattern type unknown to the validator. | Should not happen in shipped builds; please file a bug. |
| `unknown_prefix` | A `prefix:local` reference uses a prefix that is not in `(DEFAULT_PREFIXES ∪ plan.prefixes)`. | Declare the prefix, or use a built-in. |
| `unsupported_query_form` | Plan kind is not `select`, `ask`, or `construct`. | Should not happen in shipped builds. |

## Warnings

| Code | When | What to do |
| --- | --- | --- |
| `construct_template_unbound_var` | `CONSTRUCT` template references a variable that the WHERE clause does not bind. | Bind it or remove the template triple. |
| `empty_select_star` | `SELECT *` with no variables in scope. | Add a triple pattern, or use an explicit projection. |
| `filter_after_optional` | A `FILTER` references variables introduced only inside an OPTIONAL, without a `BOUND(?var)` guard. | Move the FILTER inside the OPTIONAL, or use `BOUND(?var)`. |

Warnings do not flip `validation.ok`; they are advisory.

## Path field

`ValidationIssue.path` is an ordered list of strings/integers
identifying the offending node — e.g. `["where", 2, "exists"]`.
Hosts can use it to point the LLM at the right sub-tree to repair.

## Programmatic access

```python
from graph_mcp.compiler import QueryPlanValidator
from graph_mcp.config import Settings
from graph_mcp.security import SecurityPolicy

v = QueryPlanValidator(SecurityPolicy.from_settings(Settings()))
res = v.validate(my_plan)
for issue in res.errors:
    print(issue.code, issue.message, issue.path)
```
