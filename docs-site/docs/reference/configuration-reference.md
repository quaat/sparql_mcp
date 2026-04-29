---
id: configuration-reference
title: Configuration reference
sidebar_position: 1
description: Every GRAPH_MCP_* environment variable, with default, purpose, and security impact.
---

# Configuration reference

`graph-mcp` is configured entirely through environment variables. The
table below is generated from `.env.example` and
`graph_mcp.config.Settings` (in `src/graph_mcp/config.py`) by
`scripts/generate_docs_reference.py`. Do not edit the table by
hand — edit `.env.example` and re-run the script.

:::tip
For a higher-level walk-through grouped by deployment scenario, see the
user-facing [Configuration](/users/configuration/) page.
:::

## All `GRAPH_MCP_*` variables

<!-- BEGIN: managed:config-table -->
| Variable | Default | Required | Security impact | Description |
| --- | --- | --- | --- | --- |
| `GRAPH_MCP_ENDPOINT_URL` | `_(empty)_` | one of these two for `sparql` schema provider | Empty falls back to the local in-memory rdflib executor. | Endpoint to query. Leave blank to use the local in-memory rdflib executor. |
| `GRAPH_MCP_DEFAULT_LIMIT` | `100` | no |  | Default LIMIT applied to SELECT queries when the plan does not specify one. |
| `GRAPH_MCP_MAX_LIMIT` | `1000` | no | Cap on rows per query; tighter than the upstream engine's budget. | Hard maximum for any executed query. |
| `GRAPH_MCP_TIMEOUT_MS` | `5000` | no | Caller-side timeout; the engine must enforce its own per-query budget for hard cancellation. | Request timeout (milliseconds) per executed query. |
| `GRAPH_MCP_ALLOWED_GRAPHS` | `_(empty)_` | no | Empty disables the named-graph allowlist; an explicit list is recommended in production. | Comma-separated list of allowed named graph IRIs. Empty disables the GRAPH allowlist. |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` | `_(empty)_` | no | Each entry is a data-exfiltration channel; treat like a firewall rule. | Comma-separated list of allowed SERVICE endpoint IRIs. Empty disables SERVICE entirely. |
| `GRAPH_MCP_ENABLE_RAW_SPARQL` | `false` | no | Bypasses IR-level structural checks; only enable for trusted callers. | When true, exposes the execute_sparql_raw tool. Strongly discouraged in untrusted contexts. |
| `GRAPH_MCP_MAX_TRIPLE_PATTERNS` | `200` | no |  | Maximum number of triple patterns in a single rendered query. |
| `GRAPH_MCP_MAX_QUERY_DEPTH` | `8` | no |  | Maximum nesting depth (groups, optionals, subqueries). |
| `GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY` | `16` | no |  | Maximum number of nodes in a property-path AST. |
| `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` | `false` | no | Allows `*` / `+` paths; pair with `GRAPH_MCP_ALLOWED_PATH_PREDICATES` if enabled. | When true, the renderer will emit unbounded property paths (NOT recommended). |
| `GRAPH_MCP_LOCAL_GRAPH_FILE` | `_(empty)_` | one of these two for `sparql` schema provider | Loaded once at startup; on-disk changes are not picked up. | Path to a Turtle file loaded into the local executor at startup. Used for tests/demos. |
| `GRAPH_MCP_ALLOWED_PATH_PREDICATES` | `_(empty)_` | no | Empty disables the predicate allowlist for property paths. | Property-path predicate allowlist (CSV of IRIs). Empty disables. |
| `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE` | `false` | no | Permits redefinition of `rdf`, `xsd`, etc.; rarely correct. | Allow plans to redefine built-in prefixes (rdf, rdfs, xsd, owl, skos, dct, foaf). |
| `GRAPH_MCP_SCHEMA_PROVIDER` | `auto` | no |  | Provider mode: static, sparql, or auto. |
| `GRAPH_MCP_SCHEMA_CACHE_TTL_SECONDS` | `300` | no |  | How long to keep a discovered schema snapshot before re-querying. |
| `GRAPH_MCP_SCHEMA_DISCOVERY_TIMEOUT_MS` | `10000` | no |  | Per-discovery query timeout. |
| `GRAPH_MCP_SCHEMA_MAX_CLASSES` | `200` | no |  | Cap on the number of distinct classes returned by schema discovery. |
| `GRAPH_MCP_SCHEMA_MAX_PROPERTIES` | `500` | no |  | Cap on the number of distinct properties returned by schema discovery. |
| `GRAPH_MCP_SCHEMA_MAX_INDIVIDUALS` | `200` | no |  | Cap on the number of individuals (instances) returned by schema discovery. |
| `GRAPH_MCP_SCHEMA_MAX_NAMED_GRAPHS` | `200` | no |  | Cap on the number of named graphs returned by schema discovery. |
| `GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP` | `true` | no |  | Run an initial schema refresh when the server starts (only for sparql provider). |
| `GRAPH_MCP_LOG_LEVEL` | `INFO` | no | `DEBUG` may emit query text; keep `INFO` or higher in production. | Logging level (DEBUG, INFO, WARNING, ERROR). |
<!-- END: managed:config-table -->

## Validation rules

The Pydantic model that backs these variables enforces:

- `default_limit` ∈ (0, 10_000];
- `max_limit` ∈ (0, 100_000];
- `timeout_ms` ∈ (0, 600_000];
- `max_triple_patterns` ∈ (0, 10_000];
- `max_query_depth` ∈ (0, 64];
- `max_property_path_complexity` ∈ (0, 256];
- `schema_provider` ∈ {`static`, `sparql`, `auto`};
- `schema_cache_ttl_seconds` ≥ 0;
- `schema_discovery_timeout_ms` ∈ (0, 600_000];
- `schema_max_*` ∈ (0, 10_000];
- CSV variables (`allowed_graphs`, `allowed_service_endpoints`,
  `allowed_path_predicates`) accept comma-separated lists; surrounding
  whitespace is trimmed and empty entries are dropped.

If any value is out of range, the server fails to start with a
`pydantic.ValidationError`.

## `ConfigurationError`

Beyond the per-field checks above, `build_schema_provider` raises a
dedicated `ConfigurationError` when:

- `GRAPH_MCP_SCHEMA_PROVIDER=sparql` is set explicitly, **and**
- neither `GRAPH_MCP_ENDPOINT_URL` nor `GRAPH_MCP_LOCAL_GRAPH_FILE` is
  set.

Use `auto` if you want graceful fall-back to the static provider.

## See also

- [Tools reference](/reference/tools-reference/) — what each tool does
  with these settings.
- [Security policy reference](/reference/security-policy/) — how the
  configuration becomes a runtime `SecurityPolicy`.
