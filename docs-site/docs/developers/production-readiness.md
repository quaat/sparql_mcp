---
id: production-readiness
title: Production readiness
sidebar_position: 14
description: Threat model, hardened controls, and known non-goals for graph-mcp deployments.
---

# Production-readiness checklist

This page is the canonical operator-facing checklist. The same content
also lives in `docs/production_readiness.md` at the repository root for
offline reading.

It does **not** claim that the server has been operated under
production load by the authors. It documents what is hardened and
what an operator must still configure.

## Threat model

The server is intended to run inside a trusted MCP host (e.g. Claude
Code, a private agent runtime) and expose **read-only** SPARQL access
to a configured graph.

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
- Side channels of the underlying graph engine (e.g. timing attacks
  against large unindexed predicates).
- Denial of service on the upstream SPARQL endpoint by an authorized
  but expensive plan that nonetheless validates.

## Required deployment settings

| Variable | Recommended | Why |
| --- | --- | --- |
| `GRAPH_MCP_ENDPOINT_URL` | required for SPARQL provider | Without this the server can only run against a local file or in-memory graph. |
| `GRAPH_MCP_SCHEMA_PROVIDER` | `sparql` for production, `auto` for dev | `sparql` now fails fast if no source is configured. |
| `GRAPH_MCP_DEFAULT_LIMIT` | small (e.g. 100) | Applied when a plan omits `LIMIT`. |
| `GRAPH_MCP_MAX_LIMIT` | bounded (e.g. 1_000–10_000) | Hard upper bound enforced by validator and renderer. |
| `GRAPH_MCP_TIMEOUT_MS` | ≤ what the upstream engine enforces | Caller-side timeout. |
| `GRAPH_MCP_ALLOWED_GRAPHS` | enumerated allowlist | Empty means "any named graph"; explicit allowlist is strongly recommended. |
| `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` | empty (default) unless required | If `SERVICE` must be allowed, list only fully-trusted endpoints. |
| `GRAPH_MCP_ALLOWED_PATH_PREDICATES` | enumerated allowlist when `*`/`+` is permitted | Cap the predicate IRIs that can appear in property paths. |
| `GRAPH_MCP_ALLOW_UNBOUNDED_PATHS` | `false` | Only enable for trusted callers. |
| `GRAPH_MCP_ENABLE_RAW_SPARQL` | `false` | Raw mode is expert-only. |
| `GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE` | `false` | Override is rarely correct in production. |
| `GRAPH_MCP_MAX_TRIPLE_PATTERNS` | bounded | Caps plan size. |
| `GRAPH_MCP_MAX_QUERY_DEPTH` | bounded | Caps nesting depth. |
| `GRAPH_MCP_MAX_PROPERTY_PATH_COMPLEXITY` | bounded | Caps property-path AST size. |
| `GRAPH_MCP_LOG_LEVEL` | `INFO` or `WARNING` | `DEBUG` may emit user query text. |

## Endpoint timeout behaviour

| Endpoint | Cancellation behaviour |
| --- | --- |
| `HttpSparqlEndpoint` | `httpx` timeout fires; request is closed and `EndpointError` raised. The upstream engine is responsible for actually stopping the running query. |
| `LocalRdflibEndpoint` | Query runs in a worker thread under `asyncio.wait_for`. Caller observes `EndpointError` on timeout, but rdflib has no first-class cancellation — a runaway query continues to consume CPU on its worker. |

For hard cancellation, prefer `HttpSparqlEndpoint` against an engine
that enforces query budgets at the engine level (Virtuoso
`MaxQueryCostEstimationTime`, Jena Fuseki `query.timeout`, ...).

## Local rdflib limitations

- Single-process, in-memory only. No persistence, no replication.
- No engine-level query budget.
- Loaded once at startup; on-disk changes are not picked up.
- Suitable for tests, demos, offline development; not recommended for
  production.

## Schema discovery behaviour

- `SparqlSchemaProvider` runs four discovery sub-queries on startup
  and on every `refresh_schema` call.
- Sub-query failures are recorded as `SchemaDiagnostic` entries and
  surfaced via `graph://schema/status`. Discovery never raises.
- Caps (`GRAPH_MCP_SCHEMA_MAX_*`) prevent runaway result sets.
- `GRAPH_MCP_SCHEMA_PROVIDER=sparql` now fails fast if no source is
  configured — silent degradation to an empty in-memory graph is no
  longer possible.

## Recommended allowlists

Operators should configure at least:

1. `GRAPH_MCP_ALLOWED_GRAPHS` — the named graphs callers may target.
2. `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS` — usually empty.
3. `GRAPH_MCP_ALLOWED_PATH_PREDICATES` — only when unbounded paths are
   enabled.

A misconfigured allowlist is the most common production-time issue.
The validator rejects access at request time, but the operator is
responsible for keeping the lists current.

## CI gates

The shipped CI workflow runs the following on Python 3.11, 3.12, and
3.13 in a fresh virtualenv:

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
`PYTHONHASHSEED ∈ {0..9, random}`.

The docs build runs in `.github/workflows/docs.yml`; see
[CI / CD](/developers/ci/).

## Known non-goals

The following are intentionally **not** within scope:

- multi-tenant authentication;
- billing / quota;
- SPARQL Update;
- a full raw SPARQL parser;
- tool-backed term resolution inside the bundled PydanticAI eval
  agent.

These belong in a host agent or a separate gateway service, not in
this MCP server.
