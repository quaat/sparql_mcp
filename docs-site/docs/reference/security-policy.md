---
id: security-policy
title: Security policy reference
sidebar_position: 7
description: Fields of SecurityPolicy and how validator and renderer consume them.
---

# Security policy reference

`SecurityPolicy` is a frozen, slot-using dataclass declared in
`src/graph_mcp/security/policy.py`. It is the runtime view of
`Settings` that the validator and renderer consume. Construct it via
`SecurityPolicy.from_settings(settings)`.

## Fields

| Field | Type | Source `Settings` field | Used by |
| --- | --- | --- | --- |
| `default_limit` | `int` | `default_limit` | renderer (default LIMIT) |
| `max_limit` | `int` | `max_limit` | validator + renderer (cap) |
| `timeout_ms` | `int` | `timeout_ms` | endpoint |
| `allowed_graphs` | `frozenset[str]` | `allowed_graphs` (CSV) | validator |
| `allowed_service_endpoints` | `frozenset[str]` | `allowed_service_endpoints` (CSV) | validator + raw scanner |
| `enable_raw_sparql` | `bool` | `enable_raw_sparql` | server (registers raw tool) |
| `max_triple_patterns` | `int` | `max_triple_patterns` | validator |
| `max_query_depth` | `int` | `max_query_depth` | validator |
| `max_property_path_complexity` | `int` | `max_property_path_complexity` | validator |
| `allow_unbounded_paths` | `bool` | `allow_unbounded_paths` | validator |
| `allow_default_prefix_override` | `bool` | `allow_default_prefix_override` | validator + renderer |
| `allowed_path_predicates` | `frozenset[str]` | `allowed_path_predicates` (CSV) | validator |

## Methods

| Method | Behaviour |
| --- | --- |
| `is_graph_allowed(iri)` | Returns `True` when `allowed_graphs` is empty (no allowlist) or `iri in allowed_graphs`. |
| `is_service_allowed(iri)` | Returns `True` only when `iri` is in `allowed_service_endpoints`. **No empty-allowlist exemption** — empty means *no* SERVICE. |
| `is_path_predicate_allowed(iri)` | Returns `True` when `allowed_path_predicates` is empty or the IRI is on the list. |

The asymmetry between `is_graph_allowed` and `is_service_allowed` is
intentional. Graphs default to "any graph allowed" because the typical
endpoint exposes legitimate graphs; SERVICE defaults to "none allowed"
because every additional endpoint is a data-exfiltration channel.

## Construction

```python
from graph_mcp.config import Settings
from graph_mcp.security import SecurityPolicy

settings = Settings()  # reads GRAPH_MCP_* from env
policy = SecurityPolicy.from_settings(settings)
```

`SecurityPolicy` is `frozen=True, slots=True` so it cannot be mutated
after construction. Use a fresh `Settings` and a fresh policy if you
need different limits per request — but note that the server captures
one policy for the lifetime of the process.

## Inspecting at runtime

`graph_mcp/mcp_tools/resources.py:policy_security_json` formats the
policy for the `graph://policy/security` resource. The shape is:

```json
{
  "default_limit": 100,
  "max_limit": 1000,
  "timeout_ms": 5000,
  "max_triple_patterns": 200,
  "max_query_depth": 8,
  "max_property_path_complexity": 16,
  "allow_unbounded_paths": false,
  "allowed_graphs": [],
  "allowed_service_endpoints": [],
  "raw_sparql_enabled": false
}
```

Hosts can read this resource to format error messages or refuse
impossible plans up front.

## See also

- [Configuration reference](/reference/configuration-reference/) for
  the env-variable mapping.
- [Validation errors](/reference/validation-errors/) for the codes
  the policy drives.
