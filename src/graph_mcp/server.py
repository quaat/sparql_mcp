"""Entry point: wires FastMCP tools/resources/prompts to the compiler and endpoint."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.concept_retrieval import (
    DiscoverOntologyConceptsInput,
    DiscoverOntologyConceptsOutput,
    MCPConceptRetrievalSettings,
    tool_discover_ontology_concepts,
)
from graph_mcp.config import ConfigurationError, Settings, load_settings
from graph_mcp.graph import (
    GraphEndpoint,
    HttpSparqlEndpoint,
    LocalRdflibEndpoint,
    SparqlDiscoveryConfig,
    SparqlSchemaProvider,
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
    RefreshSchemaInput,
    RenderSparqlInput,
    RenderSparqlOutput,
    ResolveTermsInput,
    SchemaRefreshResult,
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
    schema_status_json,
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


def build_schema_provider(settings: Settings, endpoint: GraphEndpoint) -> SchemaProvider:
    """Construct a :class:`SchemaProvider` per the configured mode.

    - ``static``: an empty :class:`StaticSchemaProvider`.
    - ``sparql``: a :class:`SparqlSchemaProvider` against the supplied endpoint.
      Requires ``GRAPH_MCP_ENDPOINT_URL`` or ``GRAPH_MCP_LOCAL_GRAPH_FILE`` to
      be set; otherwise raises :class:`ConfigurationError` so the operator is
      not silently given an empty in-memory provider.
    - ``auto`` (default): use ``SparqlSchemaProvider`` when an endpoint URL or
      a local graph file is configured; otherwise fall back to static.
    """
    from graph_mcp.models.literals import DEFAULT_PREFIXES

    mode = settings.schema_provider
    has_real_endpoint = settings.endpoint_url is not None or settings.local_graph_file is not None
    if mode == "static":
        return StaticSchemaProvider(SchemaSnapshot())
    if mode == "sparql" and not has_real_endpoint:
        raise ConfigurationError(
            "GRAPH_MCP_SCHEMA_PROVIDER=sparql requires a configured graph "
            "source: set GRAPH_MCP_ENDPOINT_URL or "
            "GRAPH_MCP_LOCAL_GRAPH_FILE. To run without a real source, use "
            "GRAPH_MCP_SCHEMA_PROVIDER=auto (falls back to static) or "
            "GRAPH_MCP_SCHEMA_PROVIDER=static."
        )
    if mode == "auto" and not has_real_endpoint:
        return StaticSchemaProvider(SchemaSnapshot())
    cfg = SparqlDiscoveryConfig(
        timeout_ms=settings.schema_discovery_timeout_ms,
        max_classes=settings.schema_max_classes,
        max_properties=settings.schema_max_properties,
        max_individuals=settings.schema_max_individuals,
        max_named_graphs=settings.schema_max_named_graphs,
        cache_ttl_seconds=settings.schema_cache_ttl_seconds,
        base_prefixes=dict(DEFAULT_PREFIXES),
    )
    return SparqlSchemaProvider(endpoint, config=cfg)


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
    schema = schema or build_schema_provider(settings, endpoint)
    resolver = TermResolver(schema)

    # Determine the provider name for status reporting (without inspecting types).
    provider_name = "sparql" if isinstance(schema, SparqlSchemaProvider) else "static"

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

    @mcp.resource("graph://schema/status")
    def res_status() -> str:
        """Schema-discovery status: provider, last refresh, counts, diagnostics."""
        return schema_status_json(
            schema,
            provider_name=provider_name,
            cache_ttl_seconds=settings.schema_cache_ttl_seconds,
        )

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
    def render_sparql(input: RenderSparqlInput) -> RenderSparqlOutput:
        """Validate, then render a QueryPlan to canonical SPARQL.

        On validation failure, ``rendered`` is ``None`` and ``validation``
        carries the structured errors.
        """
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

    @mcp.tool()
    async def refresh_schema(input: RefreshSchemaInput) -> SchemaRefreshResult:
        """Refresh the schema cache. ``force=True`` bypasses the TTL."""
        if not isinstance(schema, SparqlSchemaProvider):
            snap = schema.snapshot()
            return SchemaRefreshResult(
                provider="static",
                refreshed=False,
                last_refresh_at=snap.last_refresh_at,
                classes_count=len(snap.classes),
                properties_count=len(snap.properties),
                individuals_count=len(snap.individuals),
                named_graphs_count=len(snap.named_graphs),
                diagnostics=[f"{d.section}: {d.error}" for d in snap.diagnostics],
            )
        if input.force:
            snap = await schema.refresh_force()
        else:
            snap = await schema.refresh()
        return SchemaRefreshResult(
            provider="sparql",
            refreshed=True,
            last_refresh_at=snap.last_refresh_at,
            classes_count=len(snap.classes),
            properties_count=len(snap.properties),
            individuals_count=len(snap.individuals),
            named_graphs_count=len(snap.named_graphs),
            diagnostics=[f"{d.section}: {d.error}" for d in snap.diagnostics],
        )

    if policy.enable_raw_sparql:

        @mcp.tool()
        async def execute_sparql_raw(input: RawSparqlInput) -> RawSparqlOutput:
            """Expert-mode raw SPARQL execution. Read-only; gated by policy."""
            assert endpoint is not None
            return await tool_execute_sparql_raw(input, endpoint, policy)

    concept_settings = MCPConceptRetrievalSettings()
    if concept_settings.enabled:

        @mcp.tool()
        async def discover_ontology_concepts(
            input: DiscoverOntologyConceptsInput,
        ) -> DiscoverOntologyConceptsOutput:
            """Discover ontology concepts via the ``ontology_vectorizer`` library.

            Returns ranked concepts with score components and graph context
            (parents/ancestors/group_ids). Retrieval logic — embeddings,
            Qdrant queries, reranking, graph-aware scoring — lives entirely
            in the vectorizer; this tool is only the MCP boundary.

            Errors (vectorizer not installed, Qdrant unreachable, missing
            credentials) are returned as a structured ``error`` field rather
            than raised, so the host LLM can surface them gracefully.
            """
            return await tool_discover_ontology_concepts(
                input, settings=concept_settings
            )

    # ----- Prompts ----------------------------------------------------------

    @mcp.prompt("build_query_plan")
    def build_query_plan(question: str) -> str:
        """Steers the host LLM to the QueryPlan workflow."""
        return BUILD_QUERY_PLAN_PROMPT.format(question=question)

    return mcp


def main() -> None:
    import asyncio

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

    endpoint = build_endpoint(settings)
    schema = build_schema_provider(settings, endpoint)

    if settings.schema_discovery_on_startup and isinstance(schema, SparqlSchemaProvider):
        try:
            asyncio.run(schema.refresh())
            log.info(
                "schema_discovery_complete",
                classes=len(schema.snapshot().classes),
                properties=len(schema.snapshot().properties),
                individuals=len(schema.snapshot().individuals),
                named_graphs=len(schema.snapshot().named_graphs),
                diagnostics=len(schema.snapshot().diagnostics),
            )
        except Exception as exc:
            log.warning("schema_discovery_startup_failed", error=str(exc))

    server = build_server(settings=settings, endpoint=endpoint, schema=schema)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
