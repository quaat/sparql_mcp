---
id: mcp-tools
title: MCP tools (overview)
sidebar_position: 7
description: A tour of the tools graph-mcp exposes, written for non-developers.
---

# MCP tools

This page is the user-facing tour. For the schema-level reference (full
input/output, validation behavior, and security notes), see
[Tools reference](/reference/tools-reference/).

## Discovery first

The server registers three kinds of MCP surfaces:

- **Resources** under `graph://...` — read-only metadata about the
  schema and policy. Always start by reading these.
- **Tools** — actions the LLM can call.
- **Prompts** — host-renderable templates that steer the LLM into
  the safe workflow. See
  [Prompts reference](/reference/prompts-reference/).

The recommended workflow encoded in the `build_query_plan` prompt is:

```mermaid
graph TD
  A[Read graph://schema/* resources]
  A --> B[resolve_terms for each user mention]
  B --> C[Build a QueryPlan IR]
  C --> D[validate_query_plan]
  D -- ok --> E[query_graph with small max_rows]
  D -- errors --> F[Repair plan from validator output]
  F --> D
```

## resolve_terms

Mention → ranked candidates. Use this whenever the user names something
the LLM does not recognize.

```json
{
  "mentions": ["works for", "Acme"],
  "expected_kinds": ["property", "individual"],
  "limit": 10
}
```

Response is a list of `TermCandidate`s with `iri`, `prefixed_name`,
`label`, `score`, and an `explanation` of why each candidate matched.
Scores below 0.4 are filtered out; if nothing matches a mention, the
response includes a single `kind: "unknown"` placeholder so the LLM
knows to ask a clarifying question.

## validate_query_plan

Static check. Returns a `ValidationResult` with `ok: bool` and a list
of structured `ValidationIssue` records.

```json
{ "plan": { "kind": "select", "where": [], "projection": [] } }
```

Even when `ok` is `true`, you may receive `severity: "warning"`
issues — for example "filter references variables introduced only
inside OPTIONAL". Treat warnings as advisory.

## render_sparql

Validate → render. The output `RenderedQuery` is what the server would
send to the endpoint. Useful for showing the user the SPARQL before
running it.

```json
{ "plan": { "kind": "select", ... } }
```

If validation fails, `rendered` is `null` and `validation` carries the
errors. The tool never fabricates an empty SPARQL string.

## query_graph

Validate → render → execute. You can also pass `dry_run: true` to stop
after rendering. Pass `max_rows` to cap the result set; pass
`timeout_ms` to override the policy timeout.

```json
{
  "plan": { "kind": "select", ... },
  "max_rows": 100,
  "dry_run": false
}
```

`max_rows` is enforced **before** validation, so a plan with `LIMIT
9999` and `max_rows: 10` runs as `LIMIT 10` and validates fine.

## explain_query_plan

Renders a human-readable summary of the plan without executing it.
Handy when the user asks "what would this query do?".

## refresh_schema

Forces (or TTL-gates) a re-discovery of the schema:

```json
{ "force": true }
```

Returns counts and any per-section `diagnostics`. If your endpoint's
schema changes while the server is running, call this to pick up the
new classes/properties.

## execute_sparql_raw (off by default)

When `GRAPH_MCP_ENABLE_RAW_SPARQL=true`, this tool accepts a hand-written
read-only query string. See
[Raw SPARQL mode](/users/raw-sparql-mode/) for the constraints
enforced by the token-aware scanner.

## Worked example against the bundled graph

The examples below all assume `GRAPH_MCP_LOCAL_GRAPH_FILE` points at
`evals/sample_graph.ttl` and the default `GRAPH_MCP_DEFAULT_LIMIT=100`
/ `GRAPH_MCP_MAX_LIMIT=1000`. None of them require a remote SPARQL
endpoint.

### `resolve_terms`

Request:

```json
{
  "mentions": ["works for", "Acme"],
  "expected_kinds": ["property", "individual"],
  "limit": 5
}
```

Response (abridged; scores depend on the exact discovered schema):

```json
{
  "candidates": [
    {
      "mention": "works for",
      "iri": "http://example.org/worksFor",
      "prefixed_name": "ex:worksFor",
      "kind": "property",
      "label": "works for",
      "score": 1.0,
      "explanation": "matched property via 'works for'"
    },
    {
      "mention": "Acme",
      "iri": "http://example.org/Acme",
      "prefixed_name": "ex:Acme",
      "kind": "individual",
      "label": "Acme",
      "score": 1.0,
      "explanation": "matched individual via 'Acme'"
    }
  ]
}
```

### `validate_query_plan`

Request:

```json
{
  "plan": {
    "kind": "select",
    "prefixes": [{"prefix": "ex", "iri": "http://example.org/"}],
    "projection": [{"var": {"name": "person"}}],
    "where": [
      {
        "kind": "triple",
        "subject": {"kind": "var", "name": "person"},
        "predicate": {"kind": "prefixed_name", "prefix": "ex", "local": "worksFor"},
        "object": {"kind": "prefixed_name", "prefix": "ex", "local": "Acme"}
      }
    ],
    "limit": 50
  }
}
```

Response:

```json
{ "ok": true, "issues": [] }
```

### `query_graph` with `dry_run: true`

Same `plan` as above. Request:

```json
{
  "plan": { "...": "the same SelectPlan" },
  "max_rows": 10,
  "dry_run": true
}
```

Response (the rendered SPARQL is shown in full so you can see the
LIMIT cap):

```json
{
  "validation": { "ok": true, "issues": [] },
  "rendered": {
    "sparql": "PREFIX dct: <http://purl.org/dc/terms/>\nPREFIX ex: <http://example.org/>\nPREFIX foaf: <http://xmlns.com/foaf/0.1/>\nPREFIX owl: <http://www.w3.org/2002/07/owl#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX skos: <http://www.w3.org/2004/02/skos/core#>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n\nSELECT ?person\nWHERE {\n  ?person ex:worksFor ex:Acme .\n}\nLIMIT 10",
    "query_type": "select",
    "projected_variables": ["person"],
    "warnings": []
  },
  "result": null,
  "dry_run": true
}
```

The plan asked for `limit: 50`, but the request capped it to
`max_rows: 10` before validation, so the rendered SPARQL ends in
`LIMIT 10`.

### `query_graph` (executing)

Same plan, this time without `dry_run`:

```json
{
  "plan": { "...": "the same SelectPlan" },
  "max_rows": 10
}
```

Against `evals/sample_graph.ttl` the response includes:

```json
{
  "validation": { "ok": true, "issues": [] },
  "rendered": { "...": "rendered SPARQL as above" },
  "result": {
    "kind": "select",
    "variables": ["person"],
    "rows": [
      { "bindings": { "person": { "type": "uri", "value": "http://example.org/alice" } } },
      { "bindings": { "person": { "type": "uri", "value": "http://example.org/bob" } } }
    ],
    "metadata": { "duration_ms": 3.1, "row_count": 2, "truncated": false, "endpoint": "local:rdflib" }
  },
  "dry_run": false
}
```

The exact rows reflect `evals/sample_graph.ttl`; pointing the server
at a different graph yields different rows.

## What you should *not* do

- Don't hand-write SPARQL strings unless raw mode is explicitly
  enabled and you trust the caller.
- Don't invent IRIs — always go through `resolve_terms` so the LLM
  sees real schema candidates.
- Don't skip `validate_query_plan` before executing; the validator
  produces structured errors the LLM can repair from.
