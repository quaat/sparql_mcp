# graph-mcp

A production-oriented MCP server that lets an LLM query an RDF graph database
**through a strict, validated `QueryPlan` IR** — never by emitting raw SPARQL
strings directly. The server validates, compiles, and executes plans; it also
explains what they will do.

> The LLM plans. The MCP server validates, compiles, executes, and explains.

## Why an IR instead of free-form SPARQL?

Letting an LLM write SPARQL strings is convenient and unsafe: it conflates
intent with syntax, hides bugs, and makes safety review impossible. A typed
IR lets us:

- **enforce safety** — limits, depth, allowlists, no `Update`, no arbitrary
  `SERVICE` — without parsing untrusted text;
- **catch semantic errors deterministically** — unbound variables, wrong
  `HAVING` shape, `BIND` rebinds, unbounded property paths;
- **render canonical SPARQL** — stable output that diffs cleanly in PRs;
- **measure plan quality** — golden cases compare structure, not strings.

The deterministic eval baseline ships in this repo and reaches a
**100 % case-pass rate** against 20 golden cases out of the box.

## Architecture

```text
User question
  ↓
LLM planner / eval agent
  ↓
Strict QueryPlan IR (Pydantic v2)
  ↓
QueryPlanValidator   ← SecurityPolicy
  ↓
SparqlRenderer       ← deterministic, escaping-aware
  ↓
GraphEndpoint        ← rdflib (local) or HTTP (remote)
  ↓
Structured QueryResult
```

| Layer | Module |
| --- | --- |
| IR | `graph_mcp/models/` |
| Validator | `graph_mcp/compiler/validator.py` |
| Renderer | `graph_mcp/compiler/renderer.py` |
| Executors | `graph_mcp/graph/endpoint.py` |
| MCP wiring | `graph_mcp/server.py`, `graph_mcp/mcp_tools/` |
| Security | `graph_mcp/security/policy.py` |
| Evals | `evals/` |

## Installation

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"           # core + dev tools
pip install -e ".[dev,ai]"        # add the optional PydanticAI planner
```

Requires Python 3.11+. Tests are pinned to a non-free-threaded build.

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `GRAPH_MCP_ENDPOINT_URL` | _(empty)_ | Remote SPARQL endpoint. Empty → in-memory rdflib. |
| `GRAPH_MCP_DEFAULT_LIMIT` | `100` | Auto-applied to `SELECT` queries without a `LIMIT`. |
| `GRAPH_MCP_MAX_LIMIT` | `1000` | Hard cap on any executed query. |
| `GRAPH_MCP_TIMEOUT_MS` | `5000` | Per-query timeout. |
| `GRAPH_MCP_ALLOWED_GRAPHS` | _(empty)_ | CSV allowlist; empty disables the GRAPH allowlist. |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` | _(empty)_ | CSV allowlist for `SERVICE`; empty blocks all. |
| `GRAPH_MCP_ENABLE_RAW_SPARQL` | `false` | Expert-mode raw SPARQL tool. |
| `GRAPH_MCP_MAX_TRIPLE_PATTERNS` | `200` | Plan-size cap. |
| `GRAPH_MCP_MAX_QUERY_DEPTH` | `8` | Nesting cap. |
| `GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY` | `16` | Property-path AST cap. |
| `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` | `false` | Permit `*`/`+` paths. |
| `GRAPH_MCP_LOCAL_GRAPH_FILE` | _(empty)_ | Turtle file to load into the local executor. |
| `GRAPH_MCP_LOG_LEVEL` | `INFO` | Logging level (logs go to stderr). |

## Running the server

```bash
# stdio (recommended for MCP hosts like Claude Code)
python -m graph_mcp.server

# http transport
python -m graph_mcp.server --transport streamable-http
```

### Connecting from Claude Code (or any MCP client)

Add to your MCP client configuration (the exact path varies per client):

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

## End-to-end example

A plan, rendered, and executed against the bundled sample graph:

```python
from graph_mcp.models import (
    Iri, Prefix, PrefixedName, Projection, SelectPlan, TriplePattern, Var,
)
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.security import SecurityPolicy
from graph_mcp.config import Settings

policy   = SecurityPolicy.from_settings(Settings())
validate = QueryPlanValidator(policy)
render   = SparqlRenderer(policy)
endpoint = LocalRdflibEndpoint.from_turtle_file("evals/sample_graph.ttl")

plan = SelectPlan(
    prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
    projection=[Projection(var=Var(name="person"))],
    where=[
        TriplePattern(
            subject=Var(name="person"),
            predicate=PrefixedName(prefix="ex", local="worksFor"),
            object=PrefixedName(prefix="ex", local="Acme"),
        ),
    ],
)
assert validate.validate(plan).ok
print(render.render(plan).sparql)
# PREFIX ex: <http://example.org/>
# ...
# SELECT ?person
# WHERE {
#   ?person ex:worksFor ex:Acme .
# }
# LIMIT 100
```

## Tools, resources, prompts

| MCP tool | Purpose |
| --- | --- |
| `resolve_terms` | Map natural-language mentions → ranked IRIs (label/alias/local-name match) |
| `validate_query_plan` | Static check; structured `ValidationResult` |
| `render_sparql` | Validates first, then renders canonical SPARQL |
| `query_graph` | Validate → render → execute (or `dry_run=true` to stop after rendering) |
| `explain_query_plan` | Human-readable plan summary |
| `execute_sparql_raw` | Off by default; gated by `GRAPH_MCP_ENABLE_RAW_SPARQL`; rejects updates and unauthorized `SERVICE` |

| Resource | Body |
| --- | --- |
| `graph://schema/prefixes` | Prefix → IRI map |
| `graph://schema/classes` | Known classes |
| `graph://schema/properties` | Known properties |
| `graph://schema/named-graphs` | Known named graphs |
| `graph://schema/examples` | Example QueryPlan objects |
| `graph://policy/security` | Active policy |
| `graph://query-plan/schema` | JSON Schema of the QueryPlan IR |

| Prompt | Purpose |
| --- | --- |
| `build_query_plan` | Tells the host LLM how to plan, not write, SPARQL. |

## Tests, lint, type-check

```bash
make test         # pytest (73 tests, fully offline)
make lint         # ruff
make typecheck    # mypy --strict (clean)
make all          # lint + typecheck + tests
```

## Evaluations

The eval harness scores planner output against golden cases.

```bash
# Deterministic baseline — no API key, runs offline
make eval
# Or directly:
python -m evals.runner --planner deterministic

# LLM planner (requires `pip install -e .[ai]` and an API key)
python -m evals.runner --planner pydantic-ai --model anthropic:claude-sonnet-4-6
```

Output (deterministic baseline):

```json
{
  "valid_plan_rate": 0.9,
  "render_success_rate": 0.9,
  "execution_success_rate": 0.9,
  "case_pass_rate": 1.0,
  "safety_violation_count": 0.0,
  "total_cases": 20.0
}
```

(`valid_plan_rate < 1.0` is *expected*: case 19 deliberately requests
clarification, and case 20 deliberately produces a plan the validator must
reject as unsafe. Both are still counted as case passes by the runner because
they meet their stated expectations.)

The runner can also produce a JSON+markdown report:

```bash
python -m evals.runner --report-dir build/eval_report
```

## Extending

### Add a new expression function
1. Add the function name to `ALLOWED_FUNCTIONS` in
   `graph_mcp/models/expressions.py`.
2. Update the renderer if it requires a non-default rendering shape.
3. Add a test in `tests/test_renderer.py`.

### Add a new pattern type
1. Add the model in `graph_mcp/models/patterns.py` and to the `Pattern` union.
2. Update `QueryPlanValidator._validate_pattern` to handle scope/safety.
3. Update `SparqlRenderer._render_pattern` to emit it.
4. Update `_vars_in_pattern` in the validator if needed.
5. Add tests for both validator and renderer.

### Add schema-specific aliases
Inject a richer `SchemaProvider` into `build_server`:
```python
from graph_mcp.graph.schema_discovery import SchemaSnapshot, StaticSchemaProvider
schema = StaticSchemaProvider(SchemaSnapshot(...))
server = build_server(schema=schema)
```

### Add new golden eval cases
Append to `evals/golden_cases.yaml`. The `expected` block can specify
required pattern kinds, required tokens in the rendered SPARQL, forbidden
features, and execution expectations.

### Enable raw SPARQL safely
Set `GRAPH_MCP_ENABLE_RAW_SPARQL=true`. The tool still:

- rejects `INSERT`/`DELETE`/`DROP`/`CLEAR`/`LOAD`/`CREATE`/`COPY`/`MOVE`/`ADD`;
- rejects `SERVICE` unless explicitly allowlisted;
- enforces the configured timeout and row cap.

## Security model

- Read-only by default (no SPARQL Update, no arbitrary `SERVICE`).
- Validator enforces depth, triple-count, property-path complexity, and limit caps.
- Named-graph and SERVICE allowlists.
- All log output goes to stderr (stdio transport keeps stdout clean for JSON-RPC).
- Errors and exceptions never include endpoint credentials.
- Raw-SPARQL tool is disabled by default and clearly tagged when enabled.

## Limitations

- `DESCRIBE` is not in the IR (deliberately).
- The remote `HttpSparqlEndpoint` only normalizes JSON `SELECT`/`ASK` results;
  remote `CONSTRUCT` returns an empty triple list (use the local executor for
  CONSTRUCT round-trips).
- The deterministic planner is keyword-based; it exists to validate the
  pipeline, not to compete with an LLM.
