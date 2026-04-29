---
id: connecting-clients
title: Connecting MCP clients
sidebar_position: 6
description: Wire graph-mcp into Claude Desktop, Claude Code, and other MCP clients.
---

# Connecting MCP clients

`graph-mcp` is a stdio MCP server by default, so any MCP host that
supports stdio servers can talk to it. The exact configuration file
varies between hosts; the JSON shape is the same.

:::tip
Always use **absolute paths** in MCP host configurations. MCP hosts
launch servers from working directories that vary by host and
platform, so relative paths to your interpreter, your graph file, or
anything else inside `env` will surprise you.
:::

## Generic stdio MCP server stanza

This is the canonical shape every host expects. Substitute the
absolute paths for your installation:

```json
{
  "mcpServers": {
    "graph-mcp": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "graph_mcp.server"],
      "env": {
        "GRAPH_MCP_LOCAL_GRAPH_FILE": "/absolute/path/to/evals/sample_graph.ttl",
        "GRAPH_MCP_DEFAULT_LIMIT": "50",
        "GRAPH_MCP_MAX_LIMIT": "500"
      }
    }
  }
}
```

Notes:

- `command` should point at the Python interpreter that has
  `graph-mcp` installed — typically the `python` from your
  virtualenv. Avoid the system Python unless you installed
  `graph-mcp` there.
- `env` values must be strings (JSON has no integer-vs-string
  ambiguity here, but MCP hosts pass them through verbatim).
- For real deployments, replace `GRAPH_MCP_LOCAL_GRAPH_FILE` with
  `GRAPH_MCP_ENDPOINT_URL` and tighten the limits per
  [Configuration](/users/configuration/).

## Claude Desktop

Edit your `claude_desktop_config.json`:

| Platform | Path |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add the generic stanza above, then **fully restart Claude Desktop**
(quit and relaunch — restarting the window is not enough). Once the
host reconnects, the tools (`resolve_terms`, `query_graph`, …),
resources (`graph://schema/…`), and the `build_query_plan` prompt
appear in the host UI.

## Claude Code

Claude Code's MCP configuration mechanism evolves over time. Rather
than reproduce a command here that might be out of date, use the
generic stdio stanza above with whatever MCP-server configuration
mechanism your version of Claude Code currently exposes (the Claude
Code documentation under
`https://docs.claude.com/` covers the current path).

The shape is the same as Claude Desktop's `mcpServers` block; only
the wrapper around it changes.

## Other MCP clients

Any client that supports MCP stdio servers will work. The contract is:

- the host launches the configured `command` with `args`;
- the host writes JSON-RPC to the server's stdin and reads JSON-RPC
  from its stdout;
- the server logs to stderr (so the stdio channel stays clean for
  JSON-RPC).

For HTTP-based clients, run the server with
`--transport streamable-http` and point the client at the URL the
host displays at startup.

## Environment recommendations

- Provide `GRAPH_MCP_*` variables in the host's `env` block, not in a
  system-wide `.env`, so different hosts can target different graphs.
- Use absolute paths for `GRAPH_MCP_LOCAL_GRAPH_FILE`. The working
  directory at host launch is host-specific.
- Restart the host after editing the config; MCP server processes
  are launched once at host start.

## Verifying the connection

Once the host is connected, ask the LLM to:

1. Read `graph://schema/status` — confirms the schema is populated.
2. Read `graph://policy/security` — confirms the active policy and
   shows whether raw mode is enabled.
3. Call `validate_query_plan` with a tiny placeholder plan — confirms
   the validator is wired.

If any of these fails, jump to
[Troubleshooting](/users/troubleshooting/).
