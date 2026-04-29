---
id: endpoints
title: Endpoints
sidebar_position: 6
description: HTTP and local rdflib executors, with their timeout, error, and result-normalization behavior.
---

# Endpoints

The `GraphEndpoint` Protocol describes a read-only async query
interface:

```python
class GraphEndpoint(Protocol):
    async def query(
        self, sparql: str, *, query_type: str,
        timeout_ms: int, max_rows: int,
    ) -> QueryResult: ...

    async def aclose(self) -> None: ...
```

`QueryResult` is a discriminated union of `SelectResult`, `AskResult`,
`ConstructResult`. Two implementations ship in
`src/graph_mcp/graph/endpoint.py`.

## HttpSparqlEndpoint

Talks to a SPARQL 1.1 HTTP endpoint via `httpx.AsyncClient`. Sends:

- `Accept: application/sparql-results+json` for SELECT/ASK;
- `Accept: text/turtle, application/n-triples;q=0.9, application/rdf+xml;q=0.5`
  for CONSTRUCT;
- `User-Agent: graph-mcp/0.1`;
- POST form body `query=<sparql>`.

### Failure normalization

Every failure surfaces as an `EndpointError`:

- `httpx.TimeoutException` → `endpoint request timed out after Nms`;
- other `httpx.HTTPError` → `endpoint request failed: ...`;
- HTTP `>= 400` → `endpoint returned HTTP <code>` (with `status` set);
- non-JSON content-type for ASK/SELECT → wrapped error;
- malformed JSON → wrapped `ValueError`;
- ASK without a `boolean` field → explicit error;
- SELECT without `head.vars` or `results.bindings` → explicit error;
- unsupported CONSTRUCT content-type → explicit error.

The point: the caller never sees a raw `httpx`/`json` exception.

### Truncation

After parsing, SELECT and CONSTRUCT results are truncated to
`max_rows`:

- if `len(rows) > max_rows`, the result is sliced to `max_rows` and
  `metadata.truncated` is set to `True`;
- if the result fits, `metadata.row_count` reflects the actual count
  and `truncated=False`.

CONSTRUCT applies the same truncation to its `triples` list as
defence-in-depth — even if a server sends more triples than the
plan's `LIMIT` permitted, the executor exposes at most `max_rows` to
the caller and sets `metadata.truncated=True`.

### Timeout cancellation

`httpx`'s timeout fires and the request is closed. The upstream engine
must enforce its own per-query budget for the actual execution to
stop.

## LocalRdflibEndpoint

Runs queries against an in-memory `rdflib.Dataset`.

```python
LocalRdflibEndpoint()                    # empty dataset
LocalRdflibEndpoint.from_turtle_file(p)  # parse a Turtle file
LocalRdflibEndpoint.from_turtle_string(s)
```

### Concurrency / cancellation

`rdflib.Graph.query` is synchronous. The endpoint runs it in a worker
thread under `asyncio.wait_for(timeout=timeout_ms/1000)`. On timeout
the caller observes `EndpointError`, but rdflib has no first-class
cancellation — the worker thread continues to consume CPU until it
finishes naturally.

For hard cancellation, use `HttpSparqlEndpoint` against an engine that
enforces query budgets at the engine level (Virtuoso, Fuseki, ...).

### Result normalization

- `_normalize_select` reads up to `max_rows + 1` rows; the extra row
  proves truncation. Variables come from `res.vars`;
  bindings come from `res[Variable(...)]` and are mapped to
  `BindingValue(type=...)` with `uri`, `bnode`, or `literal` flavors.
- ASK uses `res.askAnswer`.
- CONSTRUCT iterates `res` and emits a `Triple` per `(s, p, o)`.

## Result normalization library

`graph_mcp/graph/result_normalizer.py` contains
`normalize_sparql_json` — a pure function that converts a SPARQL 1.1
JSON results document to a `SelectResult`. The HTTP endpoint relies
on it; tests can use it directly without spinning up an endpoint.

## SELECT, ASK, CONSTRUCT support

| Form | Local | HTTP |
| --- | --- | --- |
| `SELECT` | rdflib SELECT | SPARQL JSON |
| `ASK` | rdflib boolean | SPARQL JSON |
| `CONSTRUCT` | rdflib triples | turtle / n-triples / rdf+xml / n3 |

`DESCRIBE` is not supported on either endpoint — the IR has no
`DescribePlan`, and the raw-mode scanner rejects the form outright.

## Limitations

- `LocalRdflibEndpoint` is single-process, in-memory only. No
  persistence, no replication. Use it for tests, demos, and small
  read-only fixtures.
- No engine-level query budget on the local endpoint. Combine with
  `GRAPH_MCP_TIMEOUT_MS` and IR-level caps.
- The HTTP endpoint does not retry. Transient network errors propagate
  as `EndpointError`. Retrying belongs in the host or a proxy.
- The HTTP endpoint does not pool connections aggressively — it uses
  the default `httpx.AsyncClient`. Inject a custom client via
  `HttpSparqlEndpoint(url, client=...)` for connection pooling
  policies.

## Tests to look at

- `tests/test_remote_construct.py` — turtle/ntriples parsing,
  truncation, malformed-response handling.
- `tests/test_local_endpoint.py` — local SELECT/ASK/CONSTRUCT.
