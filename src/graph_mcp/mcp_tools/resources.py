"""MCP resources: schema, policy snapshot, IR JSON schema."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from graph_mcp.graph.schema_discovery import SchemaProvider
from graph_mcp.models import QueryPlan
from graph_mcp.security.policy import SecurityPolicy


def schema_prefixes_json(schema: SchemaProvider) -> str:
    snap = schema.snapshot()
    return json.dumps(snap.prefixes, indent=2, sort_keys=True)


def schema_classes_json(schema: SchemaProvider) -> str:
    snap = schema.snapshot()
    return json.dumps([c.model_dump() for c in snap.classes], indent=2)


def schema_properties_json(schema: SchemaProvider) -> str:
    snap = schema.snapshot()
    return json.dumps([p.model_dump() for p in snap.properties], indent=2)


def schema_named_graphs_json(schema: SchemaProvider) -> str:
    snap = schema.snapshot()
    return json.dumps([g.model_dump() for g in snap.named_graphs], indent=2)


def schema_examples_json(schema: SchemaProvider) -> str:
    snap = schema.snapshot()
    return json.dumps([e.model_dump() for e in snap.examples], indent=2)


def policy_security_json(policy: SecurityPolicy) -> str:
    return json.dumps(
        {
            "default_limit": policy.default_limit,
            "max_limit": policy.max_limit,
            "timeout_ms": policy.timeout_ms,
            "max_triple_patterns": policy.max_triple_patterns,
            "max_query_depth": policy.max_query_depth,
            "max_property_path_complexity": policy.max_property_path_complexity,
            "allow_unbounded_paths": policy.allow_unbounded_paths,
            "allowed_graphs": sorted(policy.allowed_graphs),
            "allowed_service_endpoints": sorted(policy.allowed_service_endpoints),
            "raw_sparql_enabled": policy.enable_raw_sparql,
        },
        indent=2,
    )


def query_plan_schema_json() -> str:
    """JSON Schema for the QueryPlan IR. Useful for the host LLM."""
    adapter: TypeAdapter[QueryPlan] = TypeAdapter(QueryPlan)
    return json.dumps(adapter.json_schema(), indent=2)
