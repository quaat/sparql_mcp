---
id: schema-discovery
title: Schema discovery
sidebar_position: 10
description: How graph-mcp discovers classes, properties, individuals, and named graphs.
---

# Schema discovery

The server populates a cached `SchemaSnapshot` so the LLM can read
classes, properties, and named graphs as MCP resources rather than
guessing IRIs.

## Modes

`GRAPH_MCP_SCHEMA_PROVIDER` selects the mode.

| Mode | Behavior |
| --- | --- |
| `static` | Use whatever snapshot the host injected via `build_server(schema=...)`. Empty by default. |
| `sparql` | Run discovery queries against the configured endpoint. **Requires** `GRAPH_MCP_ENDPOINT_URL` or `GRAPH_MCP_LOCAL_GRAPH_FILE`; otherwise the server fails fast with `ConfigurationError`. |
| `auto` (default) | Use `sparql` when an endpoint or local file is configured; otherwise fall back to `static`. |

## What it discovers

- declared classes (`rdfs:Class`, `owl:Class`) and instance-observed
  classes (`?s a ?cls`);
- declared properties (`rdf:Property`, `owl:ObjectProperty`,
  `owl:DatatypeProperty`) and observed predicates;
- `rdfs:label` and `skos:prefLabel` for both classes and properties;
- `rdfs:domain` and `rdfs:range` for properties;
- named graphs reachable via `GRAPH ?g`;
- a capped sample of individuals (instances).

`prefixed_name` values are filled in from the configured prefix map so
`graph://schema/classes` returns both the absolute IRI and the
human-friendly form.

## Caps and timeouts

Discovery is best-effort. Each section runs as a separate query under
`GRAPH_MCP_SCHEMA_DISCOVERY_TIMEOUT_MS`, and each section is capped:

| Variable | Default |
| --- | --- |
| `GRAPH_MCP_SCHEMA_MAX_CLASSES` | 200 |
| `GRAPH_MCP_SCHEMA_MAX_PROPERTIES` | 500 |
| `GRAPH_MCP_SCHEMA_MAX_INDIVIDUALS` | 200 |
| `GRAPH_MCP_SCHEMA_MAX_NAMED_GRAPHS` | 200 |

If a section times out or returns an unexpected shape, the failure is
recorded as a `SchemaDiagnostic` on the snapshot rather than aborting
discovery. Inspect them at `graph://schema/status`.

## Caching

A successful refresh stays in memory for `GRAPH_MCP_SCHEMA_CACHE_TTL_SECONDS`
(default 300 s). Within that window, calls to `refresh_schema` return
the cached snapshot. Pass `force: true` to bypass the TTL.

## Startup discovery

When `GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP=true` (default), the
server runs an initial refresh as soon as the SPARQL provider is
selected. Failures are logged but do not crash the server — the
snapshot just stays empty until the next refresh.

If your endpoint is slow, set this to `false` and call `refresh_schema`
on demand from the host LLM.

## Inspecting the result

```text
graph://schema/status        — counts and per-section diagnostics
graph://schema/prefixes      — prefix map
graph://schema/classes       — discovered classes
graph://schema/properties    — discovered properties (with domain/range)
graph://schema/individuals   — discovered individuals (capped)
graph://schema/named-graphs  — discovered named graphs
```

## Limitations

- Endpoints that hide their schema (no `rdfs:Class` declarations, no
  reachable `GRAPH ?g`) will produce empty resources. Inject a
  curated `SchemaSnapshot` via `build_server(schema=...)` for these.
- Large graphs may time out before discovery finishes. Increase
  `GRAPH_MCP_SCHEMA_DISCOVERY_TIMEOUT_MS` or tighten the caps.
- The discovery queries are deliberately simple. They do not chase
  imports, ontology URIs, or VoID descriptions.
