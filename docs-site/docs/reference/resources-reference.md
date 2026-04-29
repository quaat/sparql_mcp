---
id: resources-reference
title: MCP resources reference
sidebar_position: 3
description: Every MCP resource graph-mcp registers, with body shape and refresh behavior.
---

# MCP resources reference

Resources are read-only. They are intended for the host LLM to discover
the schema, the IR, and the active policy without firing tools.

<!-- BEGIN: managed:resources-table -->
| URI | Anchor |
| --- | --- |
| `graph://schema/prefixes` | [Details](#schema-prefixes) |
| `graph://schema/classes` | [Details](#schema-classes) |
| `graph://schema/properties` | [Details](#schema-properties) |
| `graph://schema/named-graphs` | [Details](#schema-named-graphs) |
| `graph://schema/individuals` | [Details](#schema-individuals) |
| `graph://schema/status` | [Details](#schema-status) |
| `graph://schema/examples` | [Details](#schema-examples) |
| `graph://policy/security` | [Details](#policy-security) |
| `graph://query-plan/schema` | [Details](#query-plan-schema) |
<!-- END: managed:resources-table -->

## schema-prefixes

**Body:** A JSON object mapping prefix → IRI. Includes the seven
defaults (`rdf`, `rdfs`, `xsd`, `owl`, `skos`, `dct`, `foaf`) plus any
prefixes discovered or injected via the schema provider.

**Refresh:** Updated whenever the underlying snapshot is refreshed
(see `refresh_schema`).

**Security notes:** Cannot be used to override built-in prefixes —
those are protected at validation time unless
`GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true`.

## schema-classes

**Body:** JSON array of `ClassTerm` records:

```json
[
  {
    "iri": "http://example.org/Person",
    "prefixed_name": "ex:Person",
    "label": "Person",
    "aliases": [],
    "description": null
  }
]
```

**Refresh:** Repopulated by `SparqlSchemaProvider.refresh()`. Capped
at `GRAPH_MCP_SCHEMA_MAX_CLASSES`.

## schema-properties

**Body:** JSON array of `PropertyTerm` records — like classes but
also carrying optional `domain` and `range` IRI lists harvested from
`rdfs:domain` / `rdfs:range`.

**Refresh:** Same as classes; capped at
`GRAPH_MCP_SCHEMA_MAX_PROPERTIES`.

## schema-individuals

**Body:** JSON array of `IndividualTerm` records (subset, capped). Each
includes a `types` list — the classes the individual is a member of.

**Refresh:** Capped at `GRAPH_MCP_SCHEMA_MAX_INDIVIDUALS`.

**Security notes:** Useful when a user names a specific entity
("Acme"). Individuals are an open set, so this is intentionally
truncated; the LLM should fall back to `resolve_terms` when the cap
might hide a match.

## schema-named-graphs

**Body:** JSON array of `NamedGraphTerm` objects with `iri`, optional
`label`, and optional `description`.

**Refresh:** Discovered via `GRAPH ?g`; capped at
`GRAPH_MCP_SCHEMA_MAX_NAMED_GRAPHS`.

## schema-examples

**Body:** JSON array of curated example plans (`ExamplePlan`). The
default static provider ships an empty list; hosts can inject curated
examples by passing a populated `SchemaSnapshot` to `build_server`.

**Refresh:** Static unless the host swaps in a new snapshot.

## schema-status

**Body:**

```json
{
  "provider": "sparql",
  "last_refresh_at": "2025-01-01T00:00:00+00:00",
  "cache_ttl_seconds": 300.0,
  "classes_count": 12,
  "properties_count": 47,
  "individuals_count": 8,
  "named_graphs_count": 1,
  "diagnostics": ["properties: timeout"]
}
```

**Refresh:** Reflects the latest snapshot. `diagnostics` is the list
of best-effort discovery failures.

**Security notes:** Exposes counts and a list of section names that
failed during discovery. It does not expose endpoint credentials or
raw query bodies.

## policy-security

**Body:** JSON snapshot of the runtime `SecurityPolicy`:

```json
{
  "default_limit": 100,
  "max_limit": 1000,
  "timeout_ms": 5000,
  "max_triple_patterns": 200,
  "max_query_depth": 8,
  "max_property_path_complexity": 16,
  "allow_unbounded_paths": false,
  "allowed_graphs": [],
  "allowed_service_endpoints": [],
  "raw_sparql_enabled": false
}
```

**Refresh:** Captured at server start; restart to apply changes.

**Security notes:** Read-only mirror of the active policy, so the LLM
can format error messages or refuse impossible plans without guessing.

## query-plan-schema

**Body:** The full JSON Schema for the `QueryPlan` IR (the discriminated
union of `SelectPlan`, `AskPlan`, `ConstructPlan`).

**Refresh:** Static; computed once via `pydantic.TypeAdapter`.

**Security notes:** Use this resource (or the precomputed
`/schema/query-plan.schema.json`) to drive structured-output mode in
your LLM client.

## See also

- [User guide → MCP resources](/users/mcp-resources/) — the same
  resources written for non-developer audiences.
- [Schema provider (developers)](/developers/schema-provider/) — how
  the snapshot is produced.
