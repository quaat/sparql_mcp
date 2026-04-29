---
id: raw-sparql-mode
title: Raw SPARQL mode
sidebar_position: 11
description: When and how to use execute_sparql_raw — and why it is off by default.
---

# Raw SPARQL mode

:::warning
Raw SPARQL mode is **disabled by default**. Enabling it lets the LLM
hand-write SPARQL strings, which bypasses the IR's structural safety
guarantees. Only enable it for trusted callers in trusted contexts.
:::

## Enabling

Set:

```bash
GRAPH_MCP_ENABLE_RAW_SPARQL=true
```

When this is true, the server registers an extra MCP tool:
`execute_sparql_raw`. With the variable false (the default), the tool
is not registered at all.

## What raw mode is for

- expert-mode debugging when the IR cannot express a query you need;
- one-off offline inspections of a graph;
- copy-pasting working SPARQL from a notebook into the same pipeline.

For everything else, prefer the `QueryPlan` IR. The IR catches more
mistakes earlier, and renders deterministic SPARQL you can review.

## What raw mode is *not*

Raw mode is **read-only**. It is not a way to "just run SPARQL" — it
applies a token-aware pre-flight scanner that rejects anything that
looks like:

- SPARQL Update keywords (`INSERT`, `DELETE`, `DROP`, `CLEAR`,
  `LOAD`, `CREATE`, `COPY`, `MOVE`, `ADD`, `WITH`);
- `DESCRIBE` queries;
- `SERVICE` IRIs that are not on
  `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS`;
- `SERVICE` referenced via a variable or prefixed name;
- raw `SELECT` / `CONSTRUCT` without an explicit top-level `LIMIT`;
- top-level `LIMIT` operands that are negative, decimal,
  signed-plus, non-numeric, or duplicated;
- top-level `LIMIT` greater than the effective `max_rows`.

`LIMIT 0` is permitted because it returns no rows and is useful for
schema/debug checks.

## What the scanner is

The scanner is a small token-aware reader, not a full SPARQL parser:

- distinguishes default-state code from string literals (`"..."`,
  `'...'`, `"""..."""`, `'''...'''`), comments (`#...\n`), and IRI
  refs (`<...>`);
- correctly leaves `<http://example.org/#frag>` intact (the `#`
  inside an IRI never starts a comment);
- detects update keywords token-by-token, so `INSERT\nDATA`,
  `INSERT\tDATA`, and `Insert\ndata` are all rejected;
- enforces the rules above even when the same keyword appears in a
  string literal or comment.

## Tool input

```json
{
  "sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10",
  "max_rows": 100,
  "timeout_ms": 5000,
  "expected_query_type": "select"
}
```

`expected_query_type` is checked against the form inferred from the
first query keyword. A mismatch is rejected — the host cannot ask for
a SELECT and silently get back the rows of an `ASK`.

## Output

```json
{
  "result": { "kind": "select", "variables": ["s"], "rows": [...], "metadata": {...} },
  "raw_mode": true
}
```

The discriminator `raw_mode: true` lets host code distinguish raw-mode
results from IR-rendered results.

## When raw mode still bites you

Even with the scanner:

- a query against an unknown named graph can still leak data if you
  forgot to set `GRAPH_MCP_ALLOWED_GRAPHS`;
- a slow query runs to completion against an HTTP endpoint that
  doesn't enforce its own per-query budget;
- a syntactically valid but unbounded property path can amplify load
  on the engine.

This is why raw mode is off by default. Treat it as defence-in-depth
around a feature you should generally leave off.

## See also

- [Tools reference → execute-sparql-raw](/reference/tools-reference/#execute-sparql-raw)
- [ADR 0003 — Raw SPARQL disabled by default](/adr/0003-raw-sparql-disabled-by-default/)
