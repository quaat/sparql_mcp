---
id: mcp-integration
title: MCP integration
sidebar_position: 9
description: How tools, resources, and prompts are wired up via FastMCP.
---

# MCP integration

`build_server(settings, endpoint, schema)` (in
`src/graph_mcp/server.py`) constructs the `FastMCP` instance and
registers tools, resources, and prompts. The function is intentionally
boring — every dependency it needs is built once and captured in the
closure.

## Construction order

```python
settings  = settings or load_settings()           # env → Settings
policy    = SecurityPolicy.from_settings(settings)
validator = QueryPlanValidator(policy)
renderer  = SparqlRenderer(policy)
endpoint  = endpoint or build_endpoint(settings)  # HTTP or local
schema    = schema   or build_schema_provider(settings, endpoint)
resolver  = TermResolver(schema)
mcp       = FastMCP("graph-mcp")
```

The `endpoint` and `schema` parameters exist so tests and host
integrations can inject mocks. `LocalRdflibEndpoint.from_turtle_string`
is the usual test seam.

## Tool registration

Each tool is a `@mcp.tool()`-decorated function with a single
Pydantic input model. The body delegates to a pure function in
`graph_mcp/mcp_tools/tools.py`:

```python
@mcp.tool()
def validate_query_plan(input: ValidateQueryPlanInput) -> ValidationResult:
    return tool_validate_query_plan(input, validator)
```

The split keeps `server.py` thin and lets the tool functions be unit
tested without an MCP runtime.

`execute_sparql_raw` is registered conditionally on
`policy.enable_raw_sparql`. When the flag is `False` the tool is not
visible to the client.

## Resource registration

Resources are read-only string-returning callables registered with
`@mcp.resource("graph://...")`:

```python
@mcp.resource("graph://schema/prefixes")
def res_prefixes() -> str:
    return schema_prefixes_json(schema)
```

The body is always JSON. The renderer functions live in
`graph_mcp/mcp_tools/resources.py` and serialize Pydantic snapshots.

## Prompt registration

`mcp.prompt("build_query_plan")` registers the prompt template defined
in `src/graph_mcp/mcp_tools/prompts.py`. The template tells the LLM the
recommended workflow (read resources → resolve terms → build IR →
validate → render → execute).

### Prompts vs tools vs resources

- **Tools** are callable actions. The host invokes one and gets a
  structured response.
- **Resources** are read-only documents (`graph://...`). The host
  fetches them.
- **Prompts** are host-renderable templates. The host substitutes
  arguments into the template and sends the rendered text to the
  LLM. Calling a prompt does not execute server code beyond the
  string substitution.

The three surfaces are exposed independently — clients enumerate
them via `tools/list`, `resources/list`, and `prompts/list`. The
server enforces structural and security guarantees inside the **tool
path**; prompts only carry guidance text.

### Adding a new prompt

1. Add a template to `src/graph_mcp/mcp_tools/prompts.py`. Keep it a
   plain `str` with `{argument}` substitutions.
2. Register it in `server.py`:
   ```python
   @mcp.prompt("your_prompt_name")
   def your_prompt(arg1: str, arg2: str | None = None) -> str:
       return YOUR_TEMPLATE.format(arg1=arg1, arg2=arg2 or "")
   ```
3. Document it under
   [Prompts reference](/reference/prompts-reference/) with arguments,
   workflow, and limitations. The CI doc-coverage check fails if a
   newly registered prompt is missing from this page.
4. Re-run `python scripts/generate_docs_reference.py` so the managed
   prompts table picks up the new name.
5. Add a test under `tests/` exercising the substitution.

### What to update when a prompt changes

If you edit the template body:

- update `Prompts reference` so the documented workflow matches what
  the prompt actually instructs;
- update the user-guide pages that reference the workflow
  (`users/query-plan-basics.md`, `users/mcp-tools.md`).

If you change a prompt's argument list, also update any host
configuration examples that call it.

## Adding a new tool

1. Define `XxxInput` and `XxxOutput` Pydantic models in
   `graph_mcp/mcp_tools/tools.py` with `model_config = ConfigDict(extra="forbid")`.
2. Implement `tool_xxx(input, ...) -> XxxOutput` in the same file.
3. Register in `server.py`:
   ```python
   @mcp.tool()
   def xxx(input: XxxInput) -> XxxOutput:
       return tool_xxx(input, ...)
   ```
4. Document under the user guide
   ([MCP tools](/users/mcp-tools/)) and the
   [tools reference](/reference/tools-reference/).
5. Add a test under `tests/test_mcp_tools.py`.

The CI doc-coverage check (`scripts/check_docs.py`) will fail until
the new tool is mentioned in the reference page.

## Adding a new resource

1. Add a JSON renderer function in `mcp_tools/resources.py`.
2. Register in `server.py` with `@mcp.resource("graph://...")`.
3. Document in [Resources reference](/reference/resources-reference/).
4. Add a test under `tests/`.

The CI doc-coverage check verifies that the URI appears in the
reference page.

## Capability discovery

The MCP host calls `tools/list` and `resources/list` against the
server to enumerate the surface. Anything registered above appears in
that listing automatically. There is no separate manifest to keep in
sync.

## Logging

`graph_mcp/logging.py` configures structlog to emit JSON-friendly logs
to stderr. The stdio transport keeps stdout clean for JSON-RPC.
