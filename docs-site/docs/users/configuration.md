---
id: configuration
title: Configuration
sidebar_position: 4
description: Group GRAPH_MCP_* variables by deployment scenario, with security impact called out.
---

# Configuration

`graph-mcp` reads every setting from environment variables prefixed
with `GRAPH_MCP_`. They can come from `.env` (loaded automatically),
your shell, the MCP host's `env` block, or a process supervisor.

For the full alphabetical list with defaults, jump to
[Configuration reference](/reference/configuration-reference/). This
page groups the variables by scenario and calls out the security
implications.

## Deployment scenarios

### Local-only development

Use the bundled rdflib executor and a Turtle file:

```bash
GRAPH_MCP_LOCAL_GRAPH_FILE=/abs/path/to/data.ttl
GRAPH_MCP_DEFAULT_LIMIT=100
GRAPH_MCP_MAX_LIMIT=1000
GRAPH_MCP_TIMEOUT_MS=5000
```

Leave `GRAPH_MCP_ENDPOINT_URL` empty. With this configuration the
server never makes HTTP requests.

### Remote SPARQL endpoint, single-tenant

Point the server at an existing endpoint and tighten allowlists:

```bash
GRAPH_MCP_ENDPOINT_URL=https://sparql.example.com/repositories/main
GRAPH_MCP_DEFAULT_LIMIT=100
GRAPH_MCP_MAX_LIMIT=1000
GRAPH_MCP_TIMEOUT_MS=10000
GRAPH_MCP_ALLOWED_GRAPHS=https://graphs.example.com/g1,https://graphs.example.com/g2
GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS=
GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=false
GRAPH_MCP_SCHEMA_PROVIDER=sparql
```

`GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS=` (empty) blocks every `SERVICE`
call entirely. That's the right default for most deployments.

### Federated queries (rare)

Allow `SERVICE` only when you fully trust the endpoint:

```bash
GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS=https://wikidata.example.com/sparql
```

The validator and the raw-mode scanner both compare endpoint IRIs
**exactly** against this allowlist — no prefix or substring matching.

### Disabled raw SPARQL (recommended)

```bash
GRAPH_MCP_ENABLE_RAW_SPARQL=false
```

This is the default. Raw mode bypasses the IR-level structural checks;
see [Raw SPARQL mode](/users/raw-sparql-mode/) before flipping it on.

## Variable groups

| Group | Variables |
| --- | --- |
| Endpoint | `GRAPH_MCP_ENDPOINT_URL`, `GRAPH_MCP_LOCAL_GRAPH_FILE` |
| Limits | `GRAPH_MCP_DEFAULT_LIMIT`, `GRAPH_MCP_MAX_LIMIT`, `GRAPH_MCP_TIMEOUT_MS` |
| Plan-shape caps | `GRAPH_MCP_MAX_TRIPLE_PATTERNS`, `GRAPH_MCP_MAX_QUERY_DEPTH`, `GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY`, `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` |
| Allowlists | `GRAPH_MCP_ALLOWED_GRAPHS`, `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS`, `GRAPH_MCP_ALLOWED_PATH_PREDICATES` |
| Prefix policy | `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE` |
| Raw mode | `GRAPH_MCP_ENABLE_RAW_SPARQL` |
| Schema discovery | `GRAPH_MCP_SCHEMA_PROVIDER`, `GRAPH_MCP_SCHEMA_CACHE_TTL_SECONDS`, `GRAPH_MCP_SCHEMA_DISCOVERY_TIMEOUT_MS`, `GRAPH_MCP_SCHEMA_MAX_*`, `GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP` |
| Logging | `GRAPH_MCP_LOG_LEVEL` |

## Security impact at a glance

:::warning
The defaults are tuned for development convenience, **not** untrusted
multi-tenant production. Read this section before pointing the server
at a real endpoint.
:::

| Setting | Risk if misconfigured |
| --- | --- |
| `GRAPH_MCP_ENABLE_RAW_SPARQL=true` | Lets the host LLM hand-write SPARQL; bypasses IR safety. Only enable for trusted callers. |
| `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS=true` | Allows `*` / `+` paths; can be used for amplification attacks against the engine. Pair with `GRAPH_MCP_ALLOWED_PATH_PREDICATES` if you need it. |
| `GRAPH_MCP_ALLOWED_GRAPHS=` (empty) | Disables the named-graph allowlist. Any plan can target any graph, including ones containing PII you didn't intend to expose. |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS=https://...` | Each endpoint listed is a data-exfiltration channel. Treat the allowlist like a firewall rule. |
| `GRAPH_MCP_MAX_LIMIT` / `GRAPH_MCP_TIMEOUT_MS` | Set the worst-case work per request. Make these tighter than your endpoint's per-query budget. |
| `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true` | Allows plans to redefine `rdf`, `xsd`, etc. Almost always a mistake. Leave `false`. |

For a deeper treatment, see
[Security and deployment](/users/security-and-deployment/) and the
[Production-readiness checklist](/developers/production-readiness/).

## Configuration is validated at startup

If a value is out of range, the server fails to start with a Pydantic
`ValidationError`. If `GRAPH_MCP_SCHEMA_PROVIDER=sparql` is set
explicitly but no source is configured, you'll see a `ConfigurationError`
instead — that's intentional, so the operator never gets a silently
empty schema.

## Where settings are read

`graph_mcp.config.Settings` is a `pydantic-settings.BaseSettings`
subclass. It reads, in order of precedence:

1. Process environment variables;
2. A `.env` file in the working directory;
3. The defaults baked into the model.

Restart the server to pick up changes.
