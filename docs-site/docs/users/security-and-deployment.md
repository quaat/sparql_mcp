---
id: security-and-deployment
title: Security and deployment
sidebar_position: 12
description: Production-oriented configuration, allowlists, raw mode, and known non-goals.
---

# Security and deployment

This page is the operator-facing security checklist. For threats and
non-goals at the design level, see the
[Production-readiness checklist](/developers/production-readiness/).

## Recommended environment

Pick the most restrictive values your callers can tolerate:

```bash
GRAPH_MCP_ENABLE_RAW_SPARQL=false
GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=false
GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=false
GRAPH_MCP_DEFAULT_LIMIT=100
GRAPH_MCP_MAX_LIMIT=1000
GRAPH_MCP_TIMEOUT_MS=10000
GRAPH_MCP_MAX_TRIPLE_PATTERNS=200
GRAPH_MCP_MAX_QUERY_DEPTH=8
GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY=16
GRAPH_MCP_LOG_LEVEL=INFO
```

## Allowlists

| Allowlist | Default | Recommended for production |
| --- | --- | --- |
| `GRAPH_MCP_ALLOWED_GRAPHS` | empty (any graph) | enumerate the named graphs callers may target |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` | empty (none) | leave empty unless federation is required |
| `GRAPH_MCP_ALLOWED_PATH_PREDICATES` | empty (any) | enumerate when `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=true` |

Values are CSVs of absolute IRIs. The validator and the raw-mode
scanner compare **exactly** — no prefix or substring matching, no
percent-encoding normalization.

## Endpoint timeout behaviour

| Endpoint | Cancellation |
| --- | --- |
| `HttpSparqlEndpoint` | `httpx` timeout fires; the request is closed; an `EndpointError` is raised. The upstream engine must enforce its own per-query budget for the actual stop. |
| `LocalRdflibEndpoint` | The query runs in a worker thread under `asyncio.wait_for`. The caller sees `EndpointError`, but rdflib has no first-class cancellation; a runaway query continues to consume CPU on its worker until completion. |

For hard cancellation, prefer `HttpSparqlEndpoint` against an engine
that supports query budgets (Virtuoso `MaxQueryCostEstimationTime`,
Jena Fuseki `query.timeout`, etc.).

## Logging and secrets

- All logs go to stderr; stdout is reserved for JSON-RPC.
- Errors and exceptions never include endpoint credentials.
- Set `GRAPH_MCP_LOG_LEVEL=INFO` (or higher) in production. `DEBUG`
  may emit query text.

## Reverse proxy / authentication

`graph-mcp` is intended to run **inside** a trusted host (Claude
Desktop, Claude Code, a private agent runtime). It does not implement
authentication — the host is responsible for auth.

If you expose the HTTP transports to a network, put the server behind
a reverse proxy that enforces:

- TLS;
- mutual auth or signed-token auth;
- per-caller rate limits;
- request body size limits.

## Raw SPARQL warning

`GRAPH_MCP_ENABLE_RAW_SPARQL=true` lets a host submit raw SPARQL
strings. The pre-flight check is a token-aware scanner, not a full
SPARQL parser. See
[Raw SPARQL mode](/users/raw-sparql-mode/) before flipping it on.

## Known non-goals

The server intentionally does **not** implement:

- multi-tenant authentication;
- per-caller billing or quota;
- SPARQL Update;
- a full SPARQL parser for the raw-mode tool.

Implementations of any of these belong in the host or a gateway, not
in the MCP server. See the
[Production-readiness checklist](/developers/production-readiness/)
for the complete list.

## Pre-deployment checklist

1. Pick the strictest `GRAPH_MCP_MAX_LIMIT` and `GRAPH_MCP_TIMEOUT_MS`
   that still accommodate legitimate use.
2. Enumerate `GRAPH_MCP_ALLOWED_GRAPHS` and confirm with the
   data-owner that those are the graphs the LLM should see.
3. Leave `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` empty unless federation
   is required.
4. Leave `GRAPH_MCP_ENABLE_RAW_SPARQL=false`.
5. Run `python -m graph_mcp.server` once with your final env to make
   sure no `ConfigurationError` is raised.
6. Check `graph://policy/security` after start; confirm it reflects
   what you intended.
