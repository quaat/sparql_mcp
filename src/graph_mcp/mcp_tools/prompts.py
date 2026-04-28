"""MCP prompts that steer the host LLM toward the QueryPlan workflow."""

from __future__ import annotations

BUILD_QUERY_PLAN_PROMPT = """\
You are working with the graph-mcp server, which exposes a strict, validated
intermediate representation called QueryPlan instead of raw SPARQL.

Workflow you must follow:

1. Read the resources `graph://schema/prefixes`, `graph://schema/classes`,
   `graph://schema/properties`, `graph://schema/named-graphs`, and — when the
   user names a specific entity — `graph://schema/individuals`, to understand
   the schema. Read `graph://query-plan/schema` to learn the QueryPlan IR.
2. For each natural-language entity or relation in the user's question,
   call `resolve_terms` to get ranked candidate IRIs. Do **not** invent IRIs,
   prefixes, classes, properties, or named graphs.
3. Build a strict QueryPlan object (NOT raw SPARQL). Use:
   - precise filters over broad scans;
   - OPTIONAL only for genuinely optional information; place a FILTER inside
     the OPTIONAL when the filter should only constrain optional bindings;
   - FILTER NOT EXISTS for absence-of-pattern semantics; use MINUS only when
     specifically appropriate;
   - subqueries for top-N, grouped aggregation, and nested constraints;
   - aggregates only with a valid GROUP BY;
   - a reasonable LIMIT for exploratory SELECT queries.
4. Call `validate_query_plan`. If errors are present, repair the plan and
   re-validate. Do not proceed until validation succeeds.
5. Call `render_sparql` (or `query_graph` with `dry_run=true`) to inspect the
   compiled SPARQL before running it on a complex query.
6. Call `query_graph` to execute. Default to a small `max_rows`.
7. In your final answer, state any assumptions and the IRIs you chose. If the
   question cannot be safely mapped, ask a clarifying question instead of
   guessing.

You must NOT:
- write raw SPARQL strings;
- use unsupported SPARQL features (DESCRIBE, SPARQL Update, arbitrary SERVICE);
- use unbounded property paths without explicit justification;
- execute broad queries without a LIMIT.

Question: {question}
"""


def get_prompts() -> list[tuple[str, str, str]]:
    """Return ``(name, description, template)`` tuples for prompt registration."""
    return [
        (
            "build_query_plan",
            "Plan and execute a graph query for the given natural-language question.",
            BUILD_QUERY_PLAN_PROMPT,
        ),
    ]
