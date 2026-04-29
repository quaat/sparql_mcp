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

## Claude Desktop

Edit your `claude_desktop_config.json` (the path is documented in the
Claude Desktop release notes; on macOS it lives under
`~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "graph-mcp": {
      "command": "python",
      "args": ["-m", "graph_mcp.server"],
      "env": {
        "GRAPH_MCP_LOCAL_GRAPH_FILE": "/abs/path/to/data.ttl",
        "GRAPH_MCP_DEFAULT_LIMIT": "50",
        "GRAPH_MCP_MAX_LIMIT": "500"
      }
    }
  }
}
```

If you installed the package in a virtualenv, point `command` at that
interpreter:

```json
"command": "/path/to/.venv/bin/python"
```

After editing, restart Claude Desktop. The tools (`resolve_terms`,
`query_graph`, ...) and resources (`graph://schema/...`) appear in the
host UI.

## Claude Code

Use the host's MCP configuration (see Claude Code docs for the file
location). The `mcpServers` block is identical.

To experiment without persisting config, you can usually launch the
server directly and point Claude Code at it via host-specific CLI
flags. Refer to the Claude Code documentation for current syntax.

## Other MCP clients

Any client that supports MCP stdio servers will work. The contract is:

- the host launches `python -m graph_mcp.server`;
- the host writes JSON-RPC to the server's stdin and reads JSON-RPC
  from its stdout;
- the server logs to stderr.

For HTTP-based clients, run the server with `--transport streamable-http`
and point the client at the URL the host displays at startup.

## Environment recommendations

Always provide `GRAPH_MCP_*` variables in the host's `env` block, not
in a system-wide `.env`, so different hosts can target different
graphs. Pass absolute paths for `GRAPH_MCP_LOCAL_GRAPH_FILE`; the
working directory at host launch is host-specific.

## Verifying the connection

Once the host is connected, ask the LLM to:

1. Read `graph://schema/status` — confirms the schema is populated.
2. Read `graph://policy/security` — confirms the active policy and
   shows whether raw mode is enabled.
3. Call `validate_query_plan` with a tiny placeholder plan — confirms
   the validator is wired.

If any of these fails, jump to
[Troubleshooting](/users/troubleshooting/).
