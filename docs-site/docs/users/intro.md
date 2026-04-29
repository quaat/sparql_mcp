---
id: intro
title: Introduction
sidebar_position: 1
description: What graph-mcp is, who it is for, and why it exposes a strict QueryPlan IR rather than raw SPARQL.
---

# Introduction

`graph-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io/)
server that lets an LLM query an RDF / SPARQL graph **safely**. Instead of
asking the LLM to emit raw SPARQL strings, the server exposes a strict,
validated intermediate representation called **`QueryPlan`**. The LLM
plans; the server validates, compiles, and executes.

## Who is this for?

- **MCP host operators** who want to connect Claude Code, Claude Desktop,
  or another MCP client to a SPARQL endpoint without giving the LLM raw
  query power.
- **Developers** building agents on top of a knowledge graph who need
  deterministic safety, structured errors, and reproducible rendering.
- **Maintainers of internal graphs** who want to expose read-only access
  to a curated set of named graphs and SERVICE endpoints.

## Why a `QueryPlan` IR instead of raw SPARQL?

Letting an LLM write SPARQL strings conflates intent with syntax. It hides
bugs and makes safety review hard. A typed IR lets the server:

- **enforce safety** — limits, depth, allowlists, no Update, no arbitrary
  `SERVICE` — without parsing untrusted text;
- **catch semantic errors deterministically** — unbound variables, wrong
  `HAVING` shape, `BIND` rebinds, unbounded property paths;
- **render canonical SPARQL** — stable output that diffs cleanly in PRs;
- **measure plan quality** — golden cases compare structure, not strings.

See [ADR 0001](/adr/0001-query-plan-ir-not-raw-sparql/) for the full
decision and alternatives considered.

## What you get

| Capability | How it shows up |
| --- | --- |
| Plan validation | `validate_query_plan` MCP tool, structured `ValidationResult` |
| Plan rendering | `render_sparql` MCP tool, deterministic and escape-safe |
| Plan execution | `query_graph` MCP tool, with `dry_run`, timeouts, and `max_rows` |
| Plan explanation | `explain_query_plan` MCP tool |
| Term resolution | `resolve_terms` MCP tool, deterministic label/alias matcher |
| Schema discovery | `graph://schema/...` resources (classes, properties, individuals, named graphs, prefixes) |
| Policy snapshot | `graph://policy/security` resource |
| Raw SPARQL (off by default) | `execute_sparql_raw` tool, gated by config |

## What is *not* included

`graph-mcp` is intentionally read-only. It does not implement:

- SPARQL Update (no `INSERT`/`DELETE`/...);
- `DESCRIBE` queries;
- arbitrary `SERVICE` calls — only allowlisted endpoints;
- multi-tenant authentication or quota — these belong in the host.

For the full list, see
[Security and deployment](/users/security-and-deployment/) and
[Production readiness](/developers/production-readiness/).

## Where to start

- New here? → [Quickstart](/users/quickstart/).
- Need to wire up Claude Desktop or Claude Code? →
  [Connecting MCP clients](/users/connecting-clients/).
- Planning a deployment? →
  [Security and deployment](/users/security-and-deployment/) and
  [Configuration](/users/configuration/).
- Want internals? → [Architecture](/developers/architecture/).
