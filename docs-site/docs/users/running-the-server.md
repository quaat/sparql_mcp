---
id: running-the-server
title: Running the server
sidebar_position: 5
description: Transports, logging, and process supervision for graph-mcp.
---

# Running the server

`graph-mcp` ships a single entry point. Choose a transport and start it.

## Transports

```bash
# stdio (the default; expected by Claude Desktop, Claude Code, etc.)
python -m graph_mcp.server

# Streamable-HTTP (for clients that prefer HTTP)
python -m graph_mcp.server --transport streamable-http

# SSE (server-sent events)
python -m graph_mcp.server --transport sse
```

The console-script equivalent installed by the package is `graph-mcp`:

```bash
graph-mcp --transport stdio
```

## What the server does at startup

1. Loads `Settings` from environment variables.
2. Configures structlog at `GRAPH_MCP_LOG_LEVEL` (logs go to stderr —
   stdout stays clean for JSON-RPC).
3. Builds the `GraphEndpoint`:
   - if `GRAPH_MCP_ENDPOINT_URL` is set → `HttpSparqlEndpoint`;
   - else if `GRAPH_MCP_LOCAL_GRAPH_FILE` is set →
     `LocalRdflibEndpoint.from_turtle_file(path)`;
   - else an in-memory `LocalRdflibEndpoint` (empty graph).
4. Builds the `SchemaProvider` per
   `GRAPH_MCP_SCHEMA_PROVIDER` (`auto`, `sparql`, or `static`).
5. If using a SPARQL provider and
   `GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP=true` (default), runs an
   initial `refresh()` and logs the per-section counts. Failures are
   recorded as diagnostics on the snapshot — they do not crash the
   server.
6. Registers tools, resources, and the `build_query_plan` prompt with
   FastMCP.

## Logging

Logs are structured (structlog) and emitted to **stderr** so the stdio
transport can keep stdout dedicated to JSON-RPC.

```bash
GRAPH_MCP_LOG_LEVEL=INFO python -m graph_mcp.server
```

Set `DEBUG` only in trusted environments — at debug level, the server
may log query text.

## Process supervision

The server is a normal Python process. Running it under any supervisor
(systemd, supervisord, Docker, your MCP host) is sufficient:

- restart on crash;
- inject the `GRAPH_MCP_*` environment;
- capture stderr to your log pipeline.

Most MCP hosts launch the server themselves. In that case, the host's
`mcpServers` config is your "supervisor"; see
[Connecting MCP clients](/users/connecting-clients/).

## Health checks

There is no dedicated health endpoint (the MCP host is the
liveness signal). For the Streamable-HTTP transport you can probe the
MCP root URL — see the host or transport docs for the precise path.

## Stopping cleanly

`Ctrl+C` is the canonical way to stop the server. The HTTP transports
shut down their listeners on `SIGINT` / `SIGTERM`; pending in-flight
queries against `LocalRdflibEndpoint` cannot be hard-cancelled (rdflib
limitation — see
[Endpoints](/developers/endpoints/) for details).
