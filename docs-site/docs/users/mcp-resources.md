---
id: mcp-resources
title: MCP resources (overview)
sidebar_position: 8
description: The graph:// resources graph-mcp exposes, written for non-developers.
---

# MCP resources

Resources are read-only documents the LLM can fetch. They give it
schema awareness, IR shape, and the active security policy without
making the LLM guess.

For the schema-level details of each body, see
[Resources reference](/reference/resources-reference/).

## Schema resources

| URI | Purpose |
| --- | --- |
| `graph://schema/prefixes` | Prefix → IRI map. Includes the seven default prefixes (`rdf`, `rdfs`, `xsd`, `owl`, `skos`, `dct`, `foaf`) and any prefixes the schema provider discovered. |
| `graph://schema/classes` | Known classes, with optional labels and aliases. |
| `graph://schema/properties` | Known properties, with `domain` / `range` IRIs when available. |
| `graph://schema/individuals` | Capped list of individuals (instances). Useful when the user mentions a specific entity. |
| `graph://schema/named-graphs` | Known named graphs. Combine with `GRAPH_MCP_ALLOWED_GRAPHS` to plan safe `GRAPH ?g` queries. |
| `graph://schema/examples` | Curated example plans, when the host injects them. |
| `graph://schema/status` | Provider name, last refresh time, counts, and any per-section discovery diagnostics. |

## Policy and IR resources

| URI | Purpose |
| --- | --- |
| `graph://policy/security` | The active `SecurityPolicy` snapshot — limits, allowlists, raw-mode flag. |
| `graph://query-plan/schema` | The full JSON Schema of the `QueryPlan` IR. |

## How the prompt uses these

The bundled `build_query_plan` prompt tells the LLM to read these
resources before producing a plan. That keeps the LLM honest: every
class, property, and named graph it references must come from the
discovery snapshot or `resolve_terms`.

## What if a resource is empty?

- An empty `graph://schema/classes` usually means the schema provider
  is `static` or discovery hasn't run yet — call `refresh_schema`.
- An empty `graph://schema/named-graphs` means either the endpoint
  doesn't expose graphs (e.g. an in-memory rdflib graph) or the cap
  was hit. Check the `diagnostics` list at
  `graph://schema/status`.

## Refreshing

Resources read the latest cached `SchemaSnapshot`. To force a refresh,
call the `refresh_schema` tool with `force: true`. The cache TTL is
controlled by `GRAPH_MCP_SCHEMA_CACHE_TTL_SECONDS`.

## Resources, tools, and prompts

Resources are one of three MCP surfaces. The other two are
[tools](/users/mcp-tools/) and
[prompts](/reference/prompts-reference/). Prompts are host-renderable
templates rather than callable actions; the bundled
`build_query_plan` prompt steers the LLM into the safe planning
workflow before any tools fire.
