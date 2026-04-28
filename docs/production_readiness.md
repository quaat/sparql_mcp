# Production-readiness checklist

This document is a deployment-time reference. It does **not** claim that the
server has been operated under production load by the authors. It describes
what has been hardened, what an operator must still configure, and what is
explicitly out of scope.

If a row reads "operator-configured", the project ships safe defaults but
the operator must review and confirm them for their environment.

---

## Threat model

The server is intended to run inside a trusted MCP host (e.g. Claude Code, a
private agent runtime) and expose **read-only** SPARQL access to a
configured graph.

The threats it is designed to mitigate:

| Threat | Mitigation |
| --- | --- |
| Plan injection via free-text fields | All plans are strict Pydantic IR; renderer escapes IRIs, language tags, and string literals deterministically. |
| Update / DDL via SPARQL | `INSERT`, `DELETE`, `DROP`, `CREATE`, `LOAD`, `CLEAR`, `COPY`, `MOVE`, `ADD`, `WITH` are rejected by the IR (no construct exists) and the raw-SPARQL pre-flight scanner. |
| Query-form smuggling | Only `SELECT`, `ASK`, `CONSTRUCT` plans are renderable; raw mode rejects `DESCRIBE`. |
| Unbounded property paths | Disabled by default; gated by `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` and `GRAPH_MCP_ALLOWED_PATH_PREDICATES`. |
| Cross-graph data exfiltration via `GRAPH ?g` | Allowed only when `?g` is bound by a preceding `VALUES` whose IRIs are all on `GRAPH_MCP_ALLOWED_GRAPHS`. |
| `SERVICE` exfiltration | Only IRIs explicitly listed in `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` are permitted. |
| Unbounded result materialization | Top-level `LIMIT` enforced by validator, raw-mode scanner, and request-level `max_rows`. CONSTRUCT results are also truncated by the HTTP executor. |
| Datatype/prefix substitution attacks | Built-in prefixes (`rdf`, `rdfs`, `xsd`, `owl`, `skos`, `dct`, `foaf`) cannot be redefined unless `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true`. |
| Comment / IRI smuggling of forbidden keywords | The raw-mode scanner is token-aware: `#` is a comment marker only in default state, never inside `<...>` or string literals. |
| Endpoint-side errors masking as success | `EndpointError` wraps malformed JSON, missing `boolean` for ASK, missing `head.vars` / `results.bindings` for SELECT, unsupported CONSTRUCT content-types. |

The server **does not** attempt to mitigate:

- Compromise of the host process or its environment variables.
- Side channels of the underlying graph engine (e.g. timing attacks against
  large unindexed predicates).
- Denial of service on the upstream SPARQL endpoint by an authorized but
  expensive plan that nonetheless validates.

---

## Required deployment settings

The following environment variables (full list is in the README) should be
set explicitly before exposing the server beyond a single-developer machine.

| Variable | Recommended | Why |
| --- | --- | --- |
| `GRAPH_MCP_ENDPOINT_URL` | required for SPARQL provider | Without this the server can only run against a local file or in-memory graph. |
| `GRAPH_MCP_SCHEMA_PROVIDER` | `sparql` for production, `auto` for dev | `sparql` now fails fast if no source is configured. |
| `GRAPH_MCP_DEFAULT_LIMIT` | small (e.g. 100) | Applied when a plan omits `LIMIT`. |
| `GRAPH_MCP_MAX_LIMIT` | bounded (e.g. 1_000–10_000) | Hard upper bound enforced by validator and renderer. |
| `GRAPH_MCP_TIMEOUT_MS` | <= what the upstream engine enforces | Caller-side timeout. |
| `GRAPH_MCP_ALLOWED_GRAPHS` | enumerated allowlist | Empty means "any named graph"; explicit allowlist is strongly recommended. |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` | empty (default) unless required | If `SERVICE` must be allowed, list only fully-trusted endpoints. |
| `GRAPH_MCP_ALLOWED_PATH_PREDICATES` | enumerated allowlist when `*`/`+` is permitted | Cap the predicate IRIs that can appear in property paths. |
| `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` | `false` | Only enable for trusted callers; consider an allowlist on top. |
| `GRAPH_MCP_ENABLE_RAW_SPARQL` | `false` | Raw mode is expert-only; see warning below. |
| `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE` | `false` | Override is rarely correct in production. |
| `GRAPH_MCP_MAX_TRIPLE_PATTERNS` | bounded | Caps plan size. |
| `GRAPH_MCP_MAX_QUERY_DEPTH` | bounded | Caps nesting depth. |
| `GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY` | bounded | Caps property-path AST size. |
| `GRAPH_MCP_LOG_LEVEL` | `INFO` or `WARNING` | `DEBUG` may emit user query text. |

---

## Raw SPARQL mode warning

`GRAPH_MCP_ENABLE_RAW_SPARQL=true` lets a host submit raw SPARQL strings.

The pre-flight check is a **token-aware scanner**, not a full SPARQL
parser. It tracks string/IRI/comment states and rejects update keywords,
`DESCRIBE`, and unallowlisted `SERVICE`. The scanner now also rejects:

- missing top-level `LIMIT` on `SELECT` / `CONSTRUCT`;
- negative, decimal, `+`-signed, or non-numeric `LIMIT` operands;
- multiple top-level `LIMIT` clauses;
- top-level `LIMIT` greater than the request's `max_rows`.

`LIMIT 0` is allowed because it returns no rows and is useful for schema
checks.

Raw mode bypasses the IR's structural guarantees (variable scope, GROUP BY
coherence, OPTIONAL placement) — operators should keep it disabled unless a
specific host needs it, and review every caller.

---

## Endpoint timeout behavior

| Endpoint | Cancellation behavior |
| --- | --- |
| `HttpSparqlEndpoint` | `httpx` timeout fires; the request is closed and `EndpointError` raised. The upstream engine is responsible for actually stopping the running query. |
| `LocalRdflibEndpoint` | The query runs in a worker thread under `asyncio.wait_for`. The caller observes `EndpointError` on timeout, but **rdflib has no first-class cancellation** — a runaway query continues to consume CPU on its worker until it finishes. |

For hard cancellation, use `HttpSparqlEndpoint` against an engine that
enforces query budgets at the engine level (e.g. Virtuoso `MaxQueryCostEstimationTime`,
Jena Fuseki `query.timeout`).

---

## Local rdflib limitations

- Single-process, in-memory only. No persistence, no replication.
- No engine-level query budget; rely on `GRAPH_MCP_TIMEOUT_MS` and plan-size caps.
- Loaded via `LocalRdflibEndpoint.from_turtle_file(path)`. The file is read
  once at startup; changes on disk are not picked up.
- Suitable for tests, demos, offline development, and small read-only
  fixtures; not recommended for production.

---

## Schema discovery behavior

- `SparqlSchemaProvider` runs four discovery sub-queries (classes,
  properties, individuals, named graphs) on startup (when
  `GRAPH_MCP_SCHEMA_DISCOVERY_ON_STARTUP=true`) and on every
  `refresh_schema` tool call.
- Sub-queries that fail are recorded as `SchemaDiagnostic` entries on the
  snapshot and surfaced via `graph://schema/status`. Discovery never raises.
- Caps (`GRAPH_MCP_SCHEMA_MAX_*`) prevent runaway result sets.
- `GRAPH_MCP_SCHEMA_PROVIDER=sparql` now **fails fast** if neither
  `GRAPH_MCP_ENDPOINT_URL` nor `GRAPH_MCP_LOCAL_GRAPH_FILE` is set —
  silent degradation to an empty in-memory graph is no longer possible.

---

## Recommended allowlists

Operators should configure at least:

1. `GRAPH_MCP_ALLOWED_GRAPHS` — the named graphs callers may target.
2. `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` — usually empty.
3. `GRAPH_MCP_ALLOWED_PATH_PREDICATES` — only when unbounded paths are
   enabled.

A misconfigured allowlist is the most common production-time issue. The
validator rejects access at request time, but the operator is responsible
for keeping the lists current.

---

## CI gates

The shipped CI workflow runs the following on Python 3.11, 3.12, and 3.13
in a fresh virtualenv:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
python -c "import graph_mcp.models; print('ok')"
python -c "from pydantic import TypeAdapter; from graph_mcp.models import QueryPlan; TypeAdapter(QueryPlan).json_schema(); print('ok')"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src evals
python -m evals.runner --planner deterministic
python -m evals.runner --cases evals/golden_cases_adversarial.yaml --planner deterministic
```

A separate hash-seed-stress job re-runs the import smoke test under
`PYTHONHASHSEED ∈ {0..9, random}` to detect dict-iteration-dependent
serialization bugs.

---

## Known non-goals

The following are intentionally **not** within scope. They belong in a
host agent or a separate gateway service, not in this MCP server.

- **Multi-tenant authentication** — the server trusts its host process.
- **Billing / quota** — caller throttling and accounting is the host's job.
- **SPARQL Update** — the IR has no Update construct, the renderer cannot
  emit one, and the raw-mode scanner rejects update keywords.
- **Full raw SPARQL parser** — the raw-mode scanner is deliberately a
  conservative tokenizer, not a parser. Hosts that need parser-level
  guarantees should disable raw mode and route everything through the
  QueryPlan IR.
- **Tool-backed term resolution inside the bundled PydanticAI eval
  agent** — host-side agents should call the server's `resolve_terms`
  MCP tool directly.
