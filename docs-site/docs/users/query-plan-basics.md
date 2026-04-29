---
id: query-plan-basics
title: QueryPlan basics
sidebar_position: 9
description: The shape of QueryPlan objects, by example, with no IR-internals deep-dive.
---

# QueryPlan basics

A `QueryPlan` is one of three shapes:

| Kind | Purpose |
| --- | --- |
| `SelectPlan` | Return rows of bindings. The most common shape. |
| `AskPlan` | Return a single boolean. |
| `ConstructPlan` | Return a graph of triples constructed from a template. |

Every plan carries:

- a list of `prefixes` (optional — built-in prefixes are seeded
  automatically by the validator, so plans can use `rdf:type` /
  `rdfs:label` without declaring them);
- a `where` clause — a list of patterns;
- form-specific fields (projection, template, limit, etc.).

For the developer-level deep dive, see
[QueryPlan IR (developers)](/developers/query-plan-ir/) and the
[JSON Schema reference](/reference/query-plan-schema/).

## Example: simple SELECT

```json
{
  "kind": "select",
  "prefixes": [{"prefix": "ex", "iri": "http://example.org/"}],
  "projection": [{"var": {"name": "person"}}],
  "where": [{
    "kind": "triple",
    "subject": {"kind": "var", "name": "person"},
    "predicate": {"kind": "prefixed_name", "prefix": "ex", "local": "worksFor"},
    "object": {"kind": "prefixed_name", "prefix": "ex", "local": "Acme"}
  }],
  "limit": 50
}
```

Renders to:

```sparql
PREFIX ex: <http://example.org/>
...
SELECT ?person
WHERE {
  ?person ex:worksFor ex:Acme .
}
LIMIT 50
```

## Example: OPTIONAL with a label

```json
{
  "kind": "select",
  "prefixes": [{"prefix": "ex", "iri": "http://example.org/"}],
  "projection": [
    {"var": {"name": "person"}},
    {"var": {"name": "label"}}
  ],
  "where": [
    {
      "kind": "triple",
      "subject": {"kind": "var", "name": "person"},
      "predicate": {"kind": "prefixed_name", "prefix": "rdf", "local": "type"},
      "object": {"kind": "prefixed_name", "prefix": "ex", "local": "Person"}
    },
    {
      "kind": "optional",
      "patterns": [
        {
          "kind": "triple",
          "subject": {"kind": "var", "name": "person"},
          "predicate": {"kind": "prefixed_name", "prefix": "rdfs", "local": "label"},
          "object": {"kind": "var", "name": "label"}
        }
      ]
    }
  ]
}
```

Notes:

- `rdf:type` and `rdfs:label` did not need explicit `prefixes` entries.
- The validator will warn if a downstream `FILTER` references `?label`
  outside the OPTIONAL block without a `BOUND(?label)` guard.

## Example: aggregate with GROUP BY

```json
{
  "kind": "select",
  "prefixes": [{"prefix": "ex", "iri": "http://example.org/"}],
  "projection": [
    {"var": {"name": "company"}},
    {"expression": {"kind": "aggregate", "function": "count", "expression": null},
     "alias": {"name": "n"}}
  ],
  "where": [{
    "kind": "triple",
    "subject": {"kind": "var", "name": "person"},
    "predicate": {"kind": "prefixed_name", "prefix": "ex", "local": "worksFor"},
    "object": {"kind": "var", "name": "company"}
  }],
  "group_by": [{"kind": "var", "name": "company"}],
  "having": [],
  "order_by": [{"expression": {"kind": "var", "name": "n"}, "descending": true}],
  "limit": 25
}
```

The validator enforces the standard SPARQL aggregate rule: every
projected variable must either be in `GROUP BY` or wrapped in an
aggregate (e.g. `SAMPLE`). Violations produce
`non_grouped_projection`.

## What the validator checks

Common checks (full list in
[Validation errors](/reference/validation-errors/)):

- variables referenced are in scope;
- `BIND` doesn't rebind an already-bound variable;
- `GRAPH ?g` with an allowlist must be preceded by a constraining
  `VALUES`;
- `SERVICE` IRIs are on the allowlist;
- property-path complexity is within the cap;
- limits, depth, and triple counts within policy.

## When in doubt

- Call `validate_query_plan` first.
- Pass `dry_run: true` to `query_graph` to see the rendered SPARQL.
- Read `graph://policy/security` to see the active limits.

## Where the workflow comes from

The `build_query_plan` MCP prompt encodes this whole workflow
(read schema resources → resolve terms → emit a `QueryPlan` →
validate → render → execute). It is a host-renderable template, not
a tool — the host substitutes the user's question, and the rendered
text steers the LLM. See
[Prompts reference](/reference/prompts-reference/) for arguments and
the complete workflow it instructs.
