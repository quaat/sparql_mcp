---
id: 0003-raw-sparql-disabled-by-default
title: ADR 0003 — Raw SPARQL disabled by default
sidebar_position: 3
description: Why execute_sparql_raw is opt-in and gated by a token-aware scanner.
---

# ADR 0003 — Raw SPARQL disabled by default

**Status:** accepted

## Context

The IR-driven workflow ([ADR 0001](/adr/0001-query-plan-ir-not-raw-sparql/))
covers the typical query shapes hosts need. But sometimes a developer
needs to hand-write SPARQL — debugging, an unusual feature the IR
hasn't grown yet, copy-pasting a working query from a notebook.

We need to decide:

1. Whether to expose a raw-SPARQL tool at all;
2. If yes, what safety controls it needs;
3. What the default behavior should be.

## Decision

Ship an `execute_sparql_raw` tool. **Disable it by default.** When
enabled (`GRAPH_MCP_ENABLE_RAW_SPARQL=true`), every input is gated by
a token-aware safety scanner before reaching the executor.

## Risks

A raw-SPARQL tool that just forwards to the engine accepts:

- SPARQL Update (`INSERT`, `DELETE`, ...);
- arbitrary `DESCRIBE`;
- `SERVICE <evil-endpoint>` for data exfiltration;
- unbounded queries that overwhelm the engine;
- comment / IRI / string-literal smuggling of any of the above.

All of these are out-of-scope for a read-only graph server.

## Why not a full SPARQL parser?

A full SPARQL 1.1 parser is large, easy to drift from upstream, and
would either:

- pull in a heavy dependency;
- or require us to maintain a parser that lags real-world endpoints.

The realistic alternative is a *conservative tokenizer* that
distinguishes default-state code from string literals, comments, and
IRI references. That is enough to enforce the safety properties we
care about without claiming to parse SPARQL.

## What the scanner does

`graph_mcp/mcp_tools/sparql_scanner.py`:

- segments input into `KEYWORD`, `PUNCT`, `STRING`, `IRI`,
  `PREFIXED`, `NUMBER`, with `COMMENT`s dropped;
- correctly leaves `<http://example.org/#frag>` intact (the `#`
  inside an IRI never starts a comment);
- treats triple-quoted strings as opaque;
- detects update keywords token-by-token, so `INSERT\nDATA`,
  `INSERT\tDATA`, `Insert\ndata` are all rejected;
- enforces `SERVICE` IRIs against the allowlist exactly;
- enforces explicit top-level `LIMIT` for `SELECT` / `CONSTRUCT`,
  with strict integer validation (no negatives, decimals, signed
  plus, or multiple top-level limits).

## Default off

A raw tool that ships enabled would put pressure on every host
operator to know it exists and turn it off. Defaulting to off
inverts that pressure: the safe state is the default state, and
operators only flip it on with knowledge of what they're enabling.

## Alternatives considered

### A. Don't ship raw mode at all.

Pros: smaller surface. Cons: hosts hit the IR's expressive limits
during debugging and have no escape valve. Maintenance pressure to
add IR features for one-off needs.

### B. Ship raw mode enabled.

Pros: zero-config debugging. Cons: regrettable defaults; surprise
data exfiltration in production deployments.

### C. Ship raw mode disabled, with a token-aware scanner (chosen).

Pros: defaults are safe, expert use is supported, scanner catches
realistic smuggling without committing to a parser. Cons: scanner is
not a parser — there are theoretical SPARQL inputs that confuse it.
Documented honestly.

## Consequences

- The MCP tool list visible to clients is shorter when raw mode is
  off, which is a feature: clients will not invent calls to a tool
  that doesn't exist.
- Raw mode requires a deliberate operator action. Documentation
  ([Raw SPARQL mode](/users/raw-sparql-mode/)) is explicit about
  what the scanner does and does not guarantee.
- The scanner is unit-tested with both positive and negative cases
  (`tests/test_sparql_scanner.py`,
  `tests/test_raw_sparql_hardening.py`) so regressions are visible.

## References

- [User guide → Raw SPARQL mode](/users/raw-sparql-mode/)
- [Tools reference → execute-sparql-raw](/reference/tools-reference/#execute-sparql-raw)
