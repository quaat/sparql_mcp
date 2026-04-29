---
id: renderer
title: Renderer
sidebar_position: 5
description: How SparqlRenderer turns a validated plan into canonical, escape-safe SPARQL.
---

# Renderer

`SparqlRenderer` (`src/graph_mcp/compiler/renderer.py`) maps a
validated `QueryPlan` to canonical SPARQL. It never mutates the plan;
all normalization happens via `model_copy(update=...)`.

## Determinism

Two principles make the renderer deterministic:

1. Every output token comes from one of three places:
   - a literal Python string in the renderer (keywords, punctuation);
   - a name from `Var`, `Prefix`, or a `PrefixedName` (already
     validated);
   - the output of an escape helper (`escape_iri`,
     `escape_string_literal`, `escape_lang_tag`).
2. Iteration order is stable: the prefix block is sorted by prefix
   name; pattern lists are walked in their original order.

Two sources of stable output combined mean the same plan always
renders the same SPARQL. Tests in `tests/test_renderer.py` verify
this property.

## Phases

`render(plan)`:

1. `normalize_plan(plan)` returns a copy with default LIMIT applied
   and subquery LIMITs capped (see below).
2. `_collect_prefixes(plan)` produces the merged prefix map (defaults
   ∪ plan.prefixes). When `policy.allow_default_prefix_override` is
   `False`, plan attempts to redefine a built-in are silently dropped
   here as a defence-in-depth measure (the validator already
   rejected the plan by this point).
3. Plan-form dispatch (`_render_select` / `_render_ask` /
   `_render_construct`) builds the `RenderedQuery`.

## Escaping

`graph_mcp/compiler/escaping.py` is the only path from user-supplied
strings to output text. It exposes:

| Function | Behaviour |
| --- | --- |
| `escape_iri(value)` | rejects control characters and the unsafe set `&lt;&gt;"{}\|\\^`. |
| `escape_string_literal(value)` | escapes `\`, `"`, `\n`, `\r`, `\t`, plus control chars. |
| `escape_lang_tag(value)` | re-validates against `LANG_TAG_REGEX`. |

The renderer never bypasses these. Any new branch that emits
user-controlled text must call the corresponding helper.

## Prefix block

```text
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX ex: <http://example.org/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
```

Always sorted alphabetically by prefix name. Always emitted at the
top, never inside subqueries.

## LIMIT handling

`normalize_plan` enforces two rules:

1. **Top-level** `SELECT` and `CONSTRUCT` get a default LIMIT
   (`policy.default_limit`) when the plan omits one. Existing limits
   above `policy.max_limit` are capped.
2. **Subquery** LIMITs are not given defaults (changing subquery
   semantics would silently break top-N patterns) but are still
   capped at `policy.max_limit`.

This runs before rendering so the rendered SPARQL is what the executor
actually sends.

## Compaction

`_compact_iri(iri)` returns a `prefix:local` form when:

- the IRI starts with one of the declared prefix IRIs;
- the local part matches `PREFIXED_LOCAL_REGEX`.

This is used to compact datatype IRIs in literals
(`"42"^^xsd:integer`) and IRIs in expressions. When no prefix matches,
the renderer falls back to `<...>`.

## SELECT structure

```text
PREFIX block
SELECT [DISTINCT|REDUCED] <projection list> | *
WHERE {
  <patterns>
}
[GROUP BY ...]
[HAVING (...)]
[ORDER BY ...]
[LIMIT N]
[OFFSET N]
```

Subqueries are rendered without a PREFIX block (prefixes live on the
top-level plan). The body is indented inside `{ ... }`.

## CONSTRUCT structure

```text
PREFIX block
CONSTRUCT {
  template triples
}
WHERE {
  patterns
}
[LIMIT N]
[OFFSET N]
```

`CONSTRUCT` requires a non-empty template — enforced by Pydantic at
plan construction time.

## ASK structure

```text
PREFIX block
ASK WHERE {
  patterns
}
```

ASK has no LIMIT/OFFSET (the result is a single boolean).

## SELECT * variable inference

When `plan.projection` is empty, the renderer infers the projected
variable list by walking the WHERE clause (`_iter_visible_variables`).
The walk:

- includes triple-pattern subjects/predicates/objects, BIND/VALUES
  variables, and subquery projections;
- descends into OPTIONAL, UNION, GRAPH, SERVICE branches (variables
  visible there reach the outer scope);
- skips MINUS and FILTER (no outward bindings);
- preserves first-mention order.

## Adding a new IR feature

Three locations to touch when adding a new pattern, expression, or
property-path operator:

1. **IR** — `src/graph_mcp/models/_ir.py` (or its shims).
2. **Validator** — `src/graph_mcp/compiler/validator.py`. Add a
   branch in `_validate_pattern` / `_check_expr_vars` / etc.
3. **Renderer** — `src/graph_mcp/compiler/renderer.py`. Add a branch
   in `_render_pattern` / `_render_expr` / etc.

Then add tests under `tests/`. See the
[Extension guide](/developers/extension-guide/).

## Tests to look at

- `tests/test_renderer.py` — golden output for SELECT, ASK, CONSTRUCT,
  property paths, aggregates, subqueries.
- `tests/test_select_star_projection.py` — `SELECT *` ordering.
- `tests/test_recursive_limits.py` — LIMIT capping for nested
  subqueries.
