---
id: schema-provider
title: Schema provider
sidebar_position: 7
description: SchemaProvider Protocol, the static and SPARQL implementations, and how they are cached.
---

# Schema provider

`graph_mcp/graph/schema_discovery.py` exposes a `SchemaProvider`
Protocol with one method:

```python
class SchemaProvider(Protocol):
    def snapshot(self) -> SchemaSnapshot: ...
```

`SchemaSnapshot` carries the full discovered schema:

| Field | Notes |
| --- | --- |
| `prefixes: dict[str, str]` | merged prefix map |
| `classes: list[ClassTerm]` | declared and observed classes |
| `properties: list[PropertyTerm]` | with optional domain/range |
| `individuals: list[IndividualTerm]` | capped sample, with `types` |
| `named_graphs: list[NamedGraphTerm]` | reachable via `GRAPH ?g` |
| `examples: list[ExamplePlan]` | curated by the host (or empty) |
| `diagnostics: list[SchemaDiagnostic]` | per-section discovery errors |
| `last_refresh_at: str \| None` | ISO-8601 timestamp |

## StaticSchemaProvider

Backed by an in-memory `SchemaSnapshot`. Useful for tests and for hosts
that want to inject a curated schema rather than discover one live.

```python
provider = StaticSchemaProvider(SchemaSnapshot(
    prefixes={"ex": "http://example.org/"},
    classes=[ClassTerm(iri="http://example.org/Person", label="Person")],
))
server = build_server(schema=provider)
```

`StaticSchemaProvider.empty()` returns a snapshot with all empty
collections, used by `auto` mode when no source is configured.

## SparqlSchemaProvider

Discovers the schema by running queries against a `GraphEndpoint`. The
queries are deliberately simple; the goal is best-effort schema
awareness, not a complete reasoner.

### Sections discovered

1. **Classes** — declared (`?c rdf:type rdfs:Class`,
   `?c rdf:type owl:Class`) and instance-observed
   (`?s rdf:type ?c`).
2. **Properties** — declared (`?p rdf:type rdf:Property`,
   `owl:ObjectProperty`, `owl:DatatypeProperty`) and observed
   (`?s ?p ?o`); enriched with `rdfs:domain`, `rdfs:range`,
   `rdfs:label`, `skos:prefLabel`.
3. **Individuals** — `?ind rdf:type ?cls` with `rdfs:label` /
   `skos:prefLabel`.
4. **Named graphs** — `GRAPH ?g { ?s ?p ?o }`.

Each section runs as a separate query under `cfg.timeout_ms` and
yields up to `cfg.max_<section>` records. Failures are caught and
recorded as `SchemaDiagnostic(section=..., error=...)` on the
snapshot.

### Caching

`SparqlSchemaProvider.refresh()`:

- if a snapshot is in cache and its age is `< cache_ttl_seconds`,
  returns it (object identity preserved);
- otherwise runs discovery and stores the new snapshot.

`refresh_force()` always re-runs.

`snapshot()` returns the current cached snapshot (or an empty one if
discovery has never run).

### `build_schema_provider`

`server.py` chooses an implementation based on
`GRAPH_MCP_SCHEMA_PROVIDER`:

| Mode | Behaviour |
| --- | --- |
| `static` | `StaticSchemaProvider(SchemaSnapshot())`. |
| `sparql` | Requires `GRAPH_MCP_ENDPOINT_URL` or `GRAPH_MCP_LOCAL_GRAPH_FILE`; otherwise raises `ConfigurationError`. |
| `auto` | Use `sparql` when a source is configured, otherwise fall back to `static`. |

The `auto` fallback is what makes the server runnable out of the box
without an endpoint. `sparql`'s fail-fast keeps operators from
silently shipping an empty schema in production.

### Startup discovery

`server.py.main()` calls `await schema.refresh()` once at startup when
the provider is `SparqlSchemaProvider` and
`GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP=true`. Failures are logged but
do not crash the server.

## `refresh_schema` MCP tool

The MCP tool `refresh_schema` is registered unconditionally. For
static providers it returns the cached counts and `refreshed=False`.
For SPARQL providers it runs `refresh()` (or `refresh_force()` when
`force=true`) and returns the new counts.

## Adding a new discovery field

1. Define a new query string and a Pydantic record type in
   `schema_discovery.py`.
2. Add a method on `SparqlSchemaProvider` that runs the query under
   the section timeout and appends to the snapshot.
3. Add a cap to `SparqlDiscoveryConfig` and a corresponding
   `GRAPH_MCP_SCHEMA_MAX_*` setting.
4. Register a JSON resource in `mcp_tools/resources.py` and wire it
   in `server.py`.
5. Update the user-facing
   [Schema discovery](/users/schema-discovery/) and
   [Resources reference](/reference/resources-reference/) pages.
6. Add a test under `tests/test_schema_discovery.py`.
