---
id: quickstart
title: Quickstart
sidebar_position: 2
description: Install graph-mcp, configure a local graph, run the server, and connect a client in under five minutes.
---

# Quickstart

This walk-through gets you from `git clone` to a working MCP server in
about five minutes, using the bundled sample graph and the in-memory
rdflib executor — no external SPARQL endpoint required.

## Prerequisites

- Python 3.11, 3.12, or 3.13 (standard CPython; the free-threaded
  `python3.13t` build is **not** supported because of a CFFI dependency).
- `pip` ≥ 24.

## 1. Clone and install

```bash
git clone https://github.com/<owner>/graph-mcp.git
cd graph-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

If you also want to experiment with the optional PydanticAI eval planner:

```bash
pip install -e ".[dev,ai]"
```

## 2. Minimal `.env`

Copy the template and edit:

```bash
cp .env.example .env
```

For a local-only run against the bundled sample graph, set:

```bash
GRAPH_MCP_LOCAL_GRAPH_FILE=evals/sample_graph.ttl
GRAPH_MCP_DEFAULT_LIMIT=100
GRAPH_MCP_MAX_LIMIT=1000
GRAPH_MCP_TIMEOUT_MS=5000
GRAPH_MCP_LOG_LEVEL=INFO
```

Leave `GRAPH_MCP_ENDPOINT_URL` empty.

:::tip
The full list of variables is in
[Configuration](/users/configuration/) and
[`reference/configuration-reference`](/reference/configuration-reference/).
:::

## 3. Run the server

```bash
python -m graph_mcp.server
```

The server listens on stdio by default — that's what MCP hosts like
Claude Desktop and Claude Code expect. To use the HTTP transport
instead:

```bash
python -m graph_mcp.server --transport streamable-http
```

## 4. Smoke test

You don't need an MCP client to confirm the server boots. The shipped
import-and-render path runs offline:

```bash
python - <<'PY'
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.models import (
    Prefix, PrefixedName, Projection, SelectPlan, TriplePattern, Var,
)
from graph_mcp.security import SecurityPolicy

policy   = SecurityPolicy.from_settings(Settings())
validate = QueryPlanValidator(policy)
render   = SparqlRenderer(policy)

plan = SelectPlan(
    prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
    projection=[Projection(var=Var(name="person"))],
    where=[TriplePattern(
        subject=Var(name="person"),
        predicate=PrefixedName(prefix="ex", local="worksFor"),
        object=PrefixedName(prefix="ex", local="Acme"),
    )],
)
print("ok =", validate.validate(plan).ok)
print(render.render(plan).sparql)
PY
```

Expected output (formatted; `LIMIT 100` comes from `GRAPH_MCP_DEFAULT_LIMIT`):

```sparql
PREFIX ex: <http://example.org/>
...
SELECT ?person
WHERE {
  ?person ex:worksFor ex:Acme .
}
LIMIT 100
```

## 5. Connect an MCP client

Add `graph-mcp` to your MCP client config. The exact path varies per
client; see [Connecting MCP clients](/users/connecting-clients/) for
Claude Desktop and Claude Code examples. The minimum stanza is:

```json
{
  "mcpServers": {
    "graph-mcp": {
      "command": "python",
      "args": ["-m", "graph_mcp.server"],
      "env": {
        "GRAPH_MCP_LOCAL_GRAPH_FILE": "/absolute/path/to/your.ttl"
      }
    }
  }
}
```

## What's next?

- Learn the
  [QueryPlan basics](/users/query-plan-basics/) so you understand what
  the LLM is producing.
- Skim the
  [tools](/users/mcp-tools/) and
  [resources](/users/mcp-resources/) the server exposes.
- Read
  [Security and deployment](/users/security-and-deployment/) before
  pointing the server at a real endpoint.
