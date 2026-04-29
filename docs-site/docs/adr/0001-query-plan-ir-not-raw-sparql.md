---
id: 0001-query-plan-ir-not-raw-sparql
title: ADR 0001 — QueryPlan IR, not raw SPARQL
sidebar_position: 1
description: Why graph-mcp exposes a strict typed IR rather than letting the LLM emit SPARQL strings.
---

# ADR 0001 — QueryPlan IR, not raw SPARQL

**Status:** accepted

## Context

`graph-mcp` is an MCP server that lets a host LLM query an RDF / SPARQL
graph. The shape of the LLM-server contract is a one-way decision —
once tools and resources are public, callers depend on the shape — so
it is worth picking carefully.

The obvious shape is "send raw SPARQL to the server and let it
execute." This is what most LLM↔database integrations do. But raw
SPARQL conflates intent with syntax:

- safety review depends on parsing untrusted text;
- structural mistakes (unbound variables, wrong `HAVING`, bad
  aggregate shape) become string-shaped errors the LLM cannot
  programmatically repair;
- canonical formatting is impossible — diffing two semantically
  equivalent queries is a string-distance exercise;
- evaluation has to compare strings, not structure.

## Decision

Expose a **strict, typed `QueryPlan` IR** — a discriminated union of
`SelectPlan`, `AskPlan`, and `ConstructPlan` whose patterns,
expressions, and property paths are all separate Pydantic models. The
LLM produces a `QueryPlan`. The server validates, renders, and
executes deterministically. Raw SPARQL is a separate, opt-in,
expert-mode tool.

## Alternatives considered

### A. Direct raw SPARQL generation

The simplest path: tools accept a SPARQL string. Pros: zero IR
maintenance, immediate feature parity with the underlying engine.
Cons: every safety check has to parse the string; structural errors
are textual; the host has no programmatic repair signal; comparison
between plans is string-shaped.

Rejected because the entire point of putting an MCP server between
the LLM and the engine is to provide structural safety. A pass-through
tool would be a thin RPC, not a planner-friendly surface.

### B. Simplified high-level API only

Ship a small set of opinionated tools (`get_class_instances(class)`,
`get_property_values(s, p)`, ...) and never expose SPARQL at all.
Pros: maximum safety. Cons: every new query shape requires a new
tool; the LLM cannot express anything outside the curated set; bad
fit for a query language as expressive as SPARQL.

Rejected as too limiting. Real graph use cases need OPTIONAL, UNION,
property paths, aggregates, and subqueries.

### C. Hidden server-side LLM agent

Have the server run its own LLM that translates natural language into
SPARQL internally. Pros: the host can be dumb. Cons: doubles LLM
cost, requires server-side credentials, makes the server stateful and
unauditable, takes the planner out of the host's reach.

Rejected because MCP exists precisely to let the host's LLM drive
external tools. Hiding a planner inside the server defeats the
purpose.

### D. Structured IR (chosen)

A typed `QueryPlan` IR with a deterministic validator and renderer.
Pros:

- safety checks are **structural** — they look at fields, not text;
- structural errors are **codes** the LLM can repair against;
- rendering is **deterministic** — diffs are stable;
- evaluation compares **structure** — golden cases score required
  pattern kinds and required tokens, not strings;
- the IR can be JSON-Schema'd and shipped as an MCP resource so the
  host LLM can produce it via structured output;
- we can still ship a raw-SPARQL escape hatch for expert use, gated
  by a separate flag.

Cons:

- IR maintenance: every new SPARQL feature needs an IR class, a
  validator branch, a renderer branch, and tests.
- A learning curve for hosts unfamiliar with structured-output
  prompting.

## Consequences

- The server has three deterministic layers (validator, renderer,
  executor) that can be tested in isolation.
- LLM repair becomes a structured loop: validation produces an
  ordered list of `ValidationIssue` records; the LLM gets one shot to
  fix specific codes.
- Raw SPARQL is opt-in and gated by a token-aware scanner — see
  [ADR 0003](/adr/0003-raw-sparql-disabled-by-default/).
- We pay an ongoing maintenance cost to track SPARQL feature additions
  (mostly a non-event — SPARQL 1.1 is stable).
- Adding new IR features is documented in the
  [Extension guide](/developers/extension-guide/).

## References

- [Architecture overview](/developers/architecture/)
- [QueryPlan IR](/developers/query-plan-ir/)
- [Validator](/developers/validator/)
