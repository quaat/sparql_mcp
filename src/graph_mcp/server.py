"""Entry point: wires FastMCP tools/resources/prompts to the compiler and endpoint."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from graph_mcp.compiler import QueryPlanValidator, RenderedQuery, SparqlRenderer
from graph_mcp.config import Settings, load_settings
from graph_mcp.graph import (
    GraphEndpoint,
    HttpSparqlEndpoint,
    LocalRdflibEndpoint,
    StaticSchemaProvider,
    TermResolver,
)
from graph_mcp.graph.schema_discovery import SchemaProvider, SchemaSnapshot
from graph_mcp.graph.term_resolver import TermResolutionResult
from graph_mcp.logging import configure_logging, get_logger
from graph_mcp.mcp_tools import (
    ExplainQueryPlanInput,
    QueryGraphInput,
    QueryGraphOutput,
    RawSparqlInput,
    RenderSparqlInput,
    ResolveTermsInput,
    ValidateQueryPlanInput,
)
from graph_mcp.mcp_tools.prompts import BUILD_QUERY_PLAN_PROMPT
from graph_mcp.mcp_tools.resources import (
    policy_security_json,
    query_plan_schema_json,
    schema_classes_json,
    schema_examples_json,
    schema_individuals_json,
    schema_named_graphs_json,
    schema_prefixes_json,
    schema_properties_json,
)
from graph_mcp.mcp_tools.tools import (
    RawSparqlOutput,
    tool_execute_sparql_raw,
    tool_explain_query_plan,
    tool_query_graph,
    tool_render_sparql,
    tool_resolve_terms,
    tool_validate_query_plan,
)
from graph_mcp.models import ValidationResult
from graph_mcp.security import SecurityPolicy

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


log = get_logger(__name__)


def build_endpoint(settings: Settings) -> GraphEndpoint:
    """Construct an endpoint based on configuration."""
    if settings.endpoint_url:
        return HttpSparqlEndpoint(settings.endpoint_url)
    if settings.local_graph_file:
        return LocalRdflibEndpoint.from_turtle_file(settings.local_graph_file)
    return LocalRdflibEndpoint()


def build_schema_provider(_settings: Settings) -> SchemaProvider:
    """Default schema provider is empty; downstream code may inject a richer one."""
    return StaticSchemaProvider(SchemaSnapshot())


def build_server(
    *,
    settings: Settings | None = None,
    endpoint: GraphEndpoint | None = None,
    schema: SchemaProvider | None = None,
) -> FastMCP:
    from mcp.server.fastmcp import FastMCP

    settings = settings or load_settings()
    policy = SecurityPolicy.from_settings(settings)
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    endpoint = endpoint or build_endpoint(settings)
    schema = schema or build_schema_provider(settings)
    resolver = TermResolver(schema)

    mcp = FastMCP("graph-mcp")

    # ----- Resources --------------------------------------------------------

    @mcp.resource("graph://schema/prefixes")
    def res_prefixes() -> str:
        """JSON map of prefix → IRI."""
        return schema_prefixes_json(schema)

    @mcp.resource("graph://schema/classes")
    def res_classes() -> str:
        """Known classes as JSON."""
        return schema_classes_json(schema)

    @mcp.resource("graph://schema/properties")
    def res_properties() -> str:
        """Known properties (with domain/range when available) as JSON."""
        return schema_properties_json(schema)

    @mcp.resource("graph://schema/named-graphs")
    def res_named_graphs() -> str:
        """Known named graphs as JSON."""
        return schema_named_graphs_json(schema)

    @mcp.resource("graph://schema/individuals")
    def res_individuals() -> str:
        """Known individuals (capped) as JSON.

        Useful when the user mentions a specific entity by name.
        """
        return schema_individuals_json(schema)

    @mcp.resource("graph://schema/examples")
    def res_examples() -> str:
        """Example QueryPlan objects keyed to common questions."""
        return schema_examples_json(schema)

    @mcp.resource("graph://policy/security")
    def res_policy() -> str:
        """The security policy currently enforced by the server."""
        return policy_security_json(policy)

    @mcp.resource("graph://query-plan/schema")
    def res_qp_schema() -> str:
        """JSON Schema for the QueryPlan IR."""
        return query_plan_schema_json()

    # ----- Tools ------------------------------------------------------------

    @mcp.tool()
    def resolve_terms(input: ResolveTermsInput) -> TermResolutionResult:
        """Resolve natural-language mentions to ranked schema-term candidates."""
        return tool_resolve_terms(input, resolver)

    @mcp.tool()
    def validate_query_plan(input: ValidateQueryPlanInput) -> ValidationResult:
        """Statically validate a QueryPlan against the security policy."""
        return tool_validate_query_plan(input, validator)

    @mcp.tool()
    def render_sparql(input: RenderSparqlInput) -> RenderedQuery:
        """Render a validated QueryPlan to canonical SPARQL."""
        return tool_render_sparql(input, validator, renderer)

    @mcp.tool()
    async def query_graph(input: QueryGraphInput) -> QueryGraphOutput:
        """Validate, render, and (unless dry_run) execute a QueryPlan."""
        assert endpoint is not None
        return await tool_query_graph(input, validator, renderer, endpoint, policy)

    @mcp.tool()
    def explain_query_plan(input: ExplainQueryPlanInput) -> object:
        """Produce a human-readable explanation of the plan's structure."""
        return tool_explain_query_plan(input, validator)

    if policy.enable_raw_sparql:

        @mcp.tool()
        async def execute_sparql_raw(input: RawSparqlInput) -> RawSparqlOutput:
            """Expert-mode raw SPARQL execution. Read-only; gated by policy."""
            assert endpoint is not None
            return await tool_execute_sparql_raw(input, endpoint, policy)

    # ----- Prompts ----------------------------------------------------------

    @mcp.prompt("build_query_plan")
    def build_query_plan(question: str) -> str:
        """Steers the host LLM to the QueryPlan workflow."""
        return BUILD_QUERY_PLAN_PROMPT.format(question=question)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="graph-mcp")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.log_level)
    log.info("starting graph-mcp", endpoint=settings.endpoint_url or "local:rdflib")
    server = build_server(settings=settings)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
