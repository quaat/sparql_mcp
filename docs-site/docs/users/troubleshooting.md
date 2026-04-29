---
id: troubleshooting
title: Troubleshooting
sidebar_position: 13
description: Symptoms you might hit when wiring up graph-mcp, and what to check first.
---

# Troubleshooting

## Server won't start

### `ConfigurationError: GRAPH_MCP_SCHEMA_PROVIDER=sparql requires a configured graph source`

Either set `GRAPH_MCP_ENDPOINT_URL` or `GRAPH_MCP_LOCAL_GRAPH_FILE`,
or change the provider mode to `auto`. The fail-fast behaviour is
intentional — a silently empty schema is worse than a clear error.

### `pydantic.ValidationError` mentioning `default_limit` / `max_limit` / `timeout_ms`

The variable is out of range. Check the validation rules in
[Configuration reference](/reference/configuration-reference/#validation-rules).

### `ImportError: pydantic-ai is required for the LLM planner`

You ran the eval runner with `--planner pydantic-ai` without
installing the optional extra. `pip install -e ".[ai]"` fixes it.

## Validation always fails

### `unknown_prefix` for `rdf:type` / `rdfs:label` / `xsd:date`

Plans no longer need to declare the seven built-in prefixes. If you
still see this error, you're on an old build — pull the latest.

### `default_prefix_override`

Your plan is redefining `rdf`, `rdfs`, `xsd`, `owl`, `skos`, `dct`, or
`foaf` to a different IRI. Almost always a mistake. Either drop the
override or set
`GRAPH_MCP_ALLOW_DEFAULT_PREFIX_OVERRIDE=true` if you really mean it.

### `service_not_allowed`

Add the endpoint to `GRAPH_MCP_ALLOWED_SERVICE_ENDPOINTS`. Match the
IRI exactly — no trailing slashes, no fragment normalization.

### `graph_variable_not_allowed`

You're using `GRAPH ?g { ... }` and the named-graph allowlist is on
(`GRAPH_MCP_ALLOWED_GRAPHS` is non-empty). The validator requires a
sibling `VALUES ?g { ... }` with allowlisted IRIs to prove `?g` is
constrained.

## Execution issues

### Empty `SelectResult.rows` from a query you expect to match

- Check the rendered SPARQL with `query_graph(dry_run=true)`.
- Read `graph://schema/status` — discovery may have failed silently
  and your IRIs do not exist on the endpoint.
- Confirm your schema was refreshed: call `refresh_schema(force=true)`.

### `EndpointError: endpoint request timed out after Nms`

Bump `GRAPH_MCP_TIMEOUT_MS` (caller-side) and ensure the upstream
engine has its own budget. Local rdflib timeouts cancel the caller
but cannot stop the worker thread; consider switching to
`HttpSparqlEndpoint` for hard cancellation.

### `EndpointError: ASK response has unexpected content-type ...`

Your SPARQL endpoint isn't returning the SPARQL JSON results format.
Some endpoints need an explicit `Accept` header negotiation; the
HTTP endpoint already does that, but a sufficiently exotic endpoint
may still respond with HTML. Confirm with `curl`.

### `EndpointError: SELECT response is missing 'head.vars'`

The endpoint returned JSON but in a non-SPARQL shape. Often a sign of
an authentication redirect.

## Raw mode

### `raw query LIMIT is invalid: ...`

The token-aware scanner caught a malformed `LIMIT`: negative,
decimal, signed, multiple top-level limits, etc. The error message
names the operand. See
[Raw SPARQL mode](/users/raw-sparql-mode/) for the rules.

### `expected_query_type='select' does not match the actual query form`

Your raw query starts with `ASK`, `CONSTRUCT`, or `DESCRIBE`, but the
input asked for a different form. Either fix the SPARQL or fix the
field. (`DESCRIBE` is rejected outright.)

## Client-side issues

### Tool not visible in Claude Desktop / Claude Code

- Restart the host after editing the MCP config.
- Confirm the `command` and `args` resolve to the right Python (use
  the venv's interpreter if you installed in a venv).
- Check the host's MCP log; the server's stderr is forwarded there.

### Tool visible but always errors out

- Read `graph://policy/security` to confirm the policy in effect.
- Run the smoke test from
  [Quickstart](/users/quickstart/#4-smoke-test) to verify the
  installation independently of the host.
