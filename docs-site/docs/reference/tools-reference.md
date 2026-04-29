---
id: tools-reference
title: MCP tools reference
sidebar_position: 2
description: Every MCP tool registered by graph-mcp, with input/output shape and security notes.
---

# MCP tools reference

The following tools are registered by `build_server` in
`graph_mcp.server`. The list below is auto-derived from the source so it
stays in sync with the actual server.

<!-- BEGIN: managed:tools-table -->
| Tool | Anchor |
| --- | --- |
| `resolve_terms` | [Details](#resolve-terms) |
| `validate_query_plan` | [Details](#validate-query-plan) |
| `render_sparql` | [Details](#render-sparql) |
| `query_graph` | [Details](#query-graph) |
| `explain_query_plan` | [Details](#explain-query-plan) |
| `refresh_schema` | [Details](#refresh-schema) |
| `execute_sparql_raw` | [Details](#execute-sparql-raw) |
<!-- END: managed:tools-table -->

:::note
`execute_sparql_raw` is registered **only** when
`GRAPH_MCP_ENABLE_RAW_SPARQL=true`. If you are reading this page on a
deployed server's docs and the tool is not present, raw mode is off.
:::

## resolve-terms

**Purpose:** Map natural-language mentions to ranked schema-term
candidates so the host LLM never has to invent IRIs.

**Input:** `ResolveTermsInput` (see `src/graph_mcp/mcp_tools/tools.py`)

```json
{
  "mentions": ["works for", "Acme"],
  "expected_kinds": ["property", "individual"],
  "limit": 10
}
```

**Output:** `TermResolutionResult` — a list of ranked
`TermCandidate` objects.

**Validation behavior:** Pydantic enforces `mentions` non-empty and
`limit ∈ [1, 100]`. Empty mention lists are rejected at the input
boundary.

**Security notes:** Resolver is deterministic and side-effect free. It
reads only the cached `SchemaSnapshot`; no SPARQL queries are issued by
this tool.

## validate-query-plan

**Purpose:** Static validation of a `QueryPlan` against the active
`SecurityPolicy`. Returns a structured `ValidationResult` so the LLM
can repair plans without parsing prose error text.

**Input:**

```json
{ "plan": { "...": "any QueryPlan" } }
```

**Output:** [`ValidationResult`](/reference/validation-errors/) —
`ok` flag plus an ordered list of `ValidationIssue` objects.

**Validation behavior:** see
[Validator](/developers/validator/) for the full rule set.

**Security notes:** No execution. No I/O. Safe to call repeatedly
during a repair loop.

## render-sparql

**Purpose:** Validate and then render a plan into canonical SPARQL.
The renderer normalizes the plan first (default `LIMIT`, subquery
`LIMIT` capping) and emits a `RenderedQuery` with the produced text and
the projected variable order.

**Input:**

```json
{ "plan": { "...": "any QueryPlan" } }
```

**Output:** `RenderSparqlOutput` — always includes a `validation`
field; `rendered` is `null` when validation fails (no fabricated empty
SPARQL string).

**Security notes:** This tool **rejects** plans whose `LIMIT` exceeds
`policy.max_limit`. To run a plan whose `LIMIT` would otherwise be too
high, use `query_graph(max_rows=...)` which caps before validating.

## query-graph

**Purpose:** Validate, render, and (unless `dry_run=true`) execute a
plan. Cap the rendered top-level `LIMIT` to
`min(max_rows, policy.max_limit)` *before* validation so a plan with
`LIMIT 9999` and `max_rows=10` runs as `LIMIT 10`.

**Input:**

```json
{
  "plan": { "...": "any QueryPlan" },
  "max_rows": 100,
  "timeout_ms": 5000,
  "dry_run": false
}
```

**Output:** `QueryGraphOutput` with `validation`, `rendered`, and
`result` fields.

**Security notes:**

- The HTTP executor truncates oversize results and sets
  `metadata.truncated=true` so callers know the row count is capped.
- `dry_run=true` runs validation + rendering only; nothing is sent to
  the endpoint.

## explain-query-plan

**Purpose:** Produce a human-readable summary of the plan — query
form, projections, where-clause pattern kinds, filter summary, and
warnings — without executing.

**Input:** `{ "plan": ... }`

**Output:** `ExplainQueryPlanOutput`.

**Security notes:** No execution. Useful for debugging plans the LLM
emits that fail validation.

## refresh-schema

**Purpose:** Refresh the cached `SchemaSnapshot`. With `force=false`
the TTL gates re-discovery; with `force=true` discovery runs
immediately.

**Input:**

```json
{ "force": false }
```

**Output:** `SchemaRefreshResult` with provider name, refresh
timestamp, counts, and any per-section diagnostics.

**Security notes:** Discovery is best-effort: failures are recorded as
diagnostics on the snapshot rather than raising. Inspect them via
[`graph://schema/status`](/reference/resources-reference/).

## execute-sparql-raw

**Status:** disabled by default. See
[Raw SPARQL mode](/users/raw-sparql-mode/) for the full controls and
caveats.

**Purpose:** Last-resort expert tool for hand-written read-only
SPARQL. Plans authored as raw text bypass the IR-level structural
guarantees.

**Input:**

```json
{
  "sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 10",
  "max_rows": 100,
  "timeout_ms": 5000,
  "expected_query_type": "select"
}
```

**Output:** `RawSparqlOutput` — the executed `QueryResult` plus a
`raw_mode: true` discriminator.

**Validation behavior:** The token-aware scanner rejects:

- update keywords (`INSERT`, `DELETE`, `DROP`, `CLEAR`, `LOAD`,
  `CREATE`, `COPY`, `MOVE`, `ADD`, `WITH`);
- `DESCRIBE`;
- `SERVICE` IRIs not on the allowlist; variables and prefixed names as
  endpoints;
- raw `SELECT` / `CONSTRUCT` without a top-level `LIMIT`;
- negative, decimal, signed-plus, or non-numeric `LIMIT` operands;
- multiple top-level `LIMIT` clauses.

`LIMIT 0` is allowed because it returns no rows and is useful for
schema/debug checks.

**Security notes:** The scanner is deliberately not a full SPARQL
parser. Keep this tool off in untrusted multi-tenant deployments.

## Common validation errors

See [Validation errors reference](/reference/validation-errors/) for
the structured codes the validator emits, with one-line guidance for
each.
