---
id: faq
title: FAQ
sidebar_position: 14
description: Short answers to common questions about graph-mcp.
---

# FAQ

## Is this an LLM?

No. `graph-mcp` is an MCP server. The LLM is whatever the host
provides (Claude Desktop, Claude Code, or another MCP client). The
server's job is to validate, compile, execute, and explain plans the
LLM produces.

## Why an IR instead of letting the LLM write SPARQL?

Because raw SPARQL conflates intent with syntax, hides bugs, and makes
safety review hard. A typed IR enforces structural and security
invariants deterministically. See
[ADR 0001](/adr/0001-query-plan-ir-not-raw-sparql/).

## Does it support SPARQL Update?

No. The IR has no Update construct, the renderer cannot emit one, and
the raw-mode scanner explicitly rejects update keywords. This is
intentional.

## Does it support `DESCRIBE`?

No. `DESCRIBE` is intentionally not in the IR and is rejected by the
raw-mode scanner.

## Does it support `SERVICE`?

Yes — but only for IRIs explicitly listed in
`GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS`. Variables and prefixed-name
endpoints are rejected. The default is no `SERVICE` at all.

## Why is raw mode disabled by default?

The IR is the safe path. Raw mode bypasses the structural checks the
IR provides and relies on a token-aware scanner that is deliberately
not a full SPARQL parser. See
[Raw SPARQL mode](/users/raw-sparql-mode/) and
[ADR 0003](/adr/0003-raw-sparql-disabled-by-default/).

## Can I use it without a SPARQL endpoint?

Yes. Set `GRAPH_MCP_LOCAL_GRAPH_FILE` to a Turtle file. The bundled
`LocalRdflibEndpoint` will load it into an in-memory graph at
startup. Best for tests, demos, and offline development — see
[Endpoints (developers)](/developers/endpoints/) for the
limitations.

## How do I add my own classes / properties to the schema?

Inject a curated `SchemaSnapshot` via `build_server(schema=...)`. See
[Schema discovery (developers)](/developers/schema-provider/).

## How do I add a new MCP tool?

See the
[Extension guide](/developers/extension-guide/) for a step-by-step.

## Is it production-ready?

Define "production-ready". The CI gate runs lint, format, type
checking, the test suite, and both eval golden-case files on Python
3.11–3.13. The
[production-readiness checklist](/developers/production-readiness/)
documents what is hardened and what an operator must still configure.
The authors have not deployed this server under multi-tenant
production load.

## Where do I file bugs?

Open an issue on the GitHub repository. The footer of every page on
this site links to the issues board.
