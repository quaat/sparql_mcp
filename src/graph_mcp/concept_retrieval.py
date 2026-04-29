"""Concept-discovery integration for the MCP server.

The MCP server does not implement ontology retrieval directly. It delegates
concept discovery to the ``ontology_vectorizer`` library, which owns
embedding, Qdrant retrieval, graph-aware scoring, reranking, and concept
result normalisation.

This module is the only place inside ``graph_mcp`` that touches the
vectorizer. It exposes:

- :class:`MCPConceptRetrievalSettings` — knobs the operator sets via env.
- :class:`DiscoverOntologyConceptsInput` / :class:`...Output` — the MCP tool
  input/output models.
- :func:`tool_discover_ontology_concepts` — the pure tool function.
- :func:`get_ontology_retriever` — a thread-safe lazy singleton so each
  process holds at most one Qdrant + Foundry client pair.

The vectorizer is an optional dependency (``pip install graph-mcp[rag]``).
When the import fails the tool short-circuits with a structured error
instead of crashing the server at startup.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from ontology_vectorizer import OntologyConceptRetriever

logger = logging.getLogger(__name__)


# --- Settings ---------------------------------------------------------------


class MCPConceptRetrievalSettings(BaseSettings):
    """MCP-side knobs for the concept-discovery tool.

    Only the MCP-facing toggles live here; the underlying retrieval
    (collection name, Foundry models, etc.) is configured via the
    vectorizer's own environment variables (``QDRANT_*``, ``FOUNDRY_*``).
    """

    model_config = SettingsConfigDict(
        env_prefix="GRAPH_MCP_CONCEPTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    enabled: bool = True
    """Master switch. When ``False`` the tool is not registered."""

    default_ontology_id: str | None = None
    """Used when a request omits ``ontology_id``."""

    default_top_k: int = Field(default=10, ge=1, le=200)
    include_deprecated_by_default: bool = False


# --- Tool input / output ----------------------------------------------------


class DiscoverOntologyConceptsInput(BaseModel):
    """Input for the ``discover_ontology_concepts`` MCP tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, description="Natural-language concept query.")
    ontology_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=200)
    include_deprecated: bool = False
    kind_filter: list[str] | None = None
    branch_filter: list[str] | None = None
    group_filter: list[str] | None = None


class DiscoverOntologyConceptsResult(BaseModel):
    """One concept in :class:`DiscoverOntologyConceptsOutput`.

    Mirrors the vectorizer's ``OntologyConceptSearchResult`` but is restated
    here so the MCP boundary owns the wire schema. We deliberately omit the
    debug payload — operators who want it should use the vectorizer CLI.
    """

    model_config = ConfigDict(extra="forbid")

    concept_id: str
    iri: str
    compact_id: str | None = None
    preferred_label: str | None = None
    labels: list[str] = Field(default_factory=list)
    alt_labels: list[str] = Field(default_factory=list)
    kind: str
    definition: str | None = None
    ontology_id: str | None = None

    score: float
    reranker_score: float | None = None
    identity_score: float | None = None
    context_score: float | None = None
    lexical_score: float | None = None
    group_score: float | None = None

    deprecated: bool = False
    parents: list[str] = Field(default_factory=list)
    ancestors: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    siblings: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)

    explanation: str | None = None


class DiscoverOntologyConceptsOutput(BaseModel):
    """Structured response for ``discover_ontology_concepts``."""

    model_config = ConfigDict(extra="forbid")

    query: str
    ontology_id: str | None = None
    results: list[DiscoverOntologyConceptsResult] = Field(default_factory=list)
    retrieval_strategy: str = "hybrid_multi_stage_graph_aware"
    total_candidates_considered: int | None = None
    error: str | None = None
    """Populated when retrieval fails. ``results`` is empty in that case
    and the MCP tool returns this object instead of raising — host LLMs do
    not handle exceptions well, but they do parse a structured error."""


# --- Lazy singleton retriever ----------------------------------------------


_retriever_lock = threading.Lock()
_retriever_instance: OntologyConceptRetriever | None = None
_retriever_init_failed: str | None = None


def reset_ontology_retriever() -> None:
    """Drop the cached retriever (intended for tests).

    Production code should not call this; the retriever is meant to live
    for the lifetime of the process.
    """
    global _retriever_instance, _retriever_init_failed
    with _retriever_lock:
        _retriever_instance = None
        _retriever_init_failed = None


def get_ontology_retriever() -> OntologyConceptRetriever:
    """Return a process-wide :class:`OntologyConceptRetriever`.

    Raises :class:`OntologyRetrieverConfigError` (from the vectorizer) when
    the configuration is invalid, or :class:`ImportError` when the
    vectorizer is not installed. Callers should map both into the tool's
    structured error response.

    The first successful call performs the heavy initialization (Qdrant
    client setup, Foundry client setup, embedding cache) and caches the
    result. Subsequent calls are O(1).
    """
    global _retriever_instance, _retriever_init_failed
    if _retriever_instance is not None:
        return _retriever_instance
    with _retriever_lock:
        if _retriever_instance is not None:
            return _retriever_instance
        if _retriever_init_failed is not None:
            # Re-raising would lose the original cause across threads; emit a
            # structured config error so the tool layer can pass it through.
            from ontology_vectorizer import OntologyRetrieverConfigError

            raise OntologyRetrieverConfigError(_retriever_init_failed)
        try:
            from ontology_vectorizer import OntologyConceptRetriever

            _retriever_instance = OntologyConceptRetriever.from_env()
            logger.info("ontology_concept_retriever_ready")
            return _retriever_instance
        except Exception as exc:
            _retriever_init_failed = (
                f"could not initialise OntologyConceptRetriever: {exc}"
            )
            logger.error(
                "ontology_concept_retriever_init_failed: %s", exc
            )
            raise


def set_ontology_retriever(retriever: OntologyConceptRetriever) -> None:
    """Inject a pre-built retriever (intended for tests).

    This bypasses ``from_env`` and the lazy singleton lock. Useful when a
    test wants to wire a fake retrieval service through the MCP tool
    function.
    """
    global _retriever_instance, _retriever_init_failed
    with _retriever_lock:
        _retriever_instance = retriever
        _retriever_init_failed = None


# --- Tool function ----------------------------------------------------------


async def tool_discover_ontology_concepts(
    inp: DiscoverOntologyConceptsInput,
    *,
    settings: MCPConceptRetrievalSettings | None = None,
    retriever: OntologyConceptRetriever | None = None,
) -> DiscoverOntologyConceptsOutput:
    """Run a concept-discovery query and shape the response for MCP clients.

    ``settings`` and ``retriever`` are injection points used by tests; in
    production both are resolved lazily — settings from environment, the
    retriever from the singleton.

    Errors are caught and returned as a structured ``error`` field on the
    output. The tool never leaks API keys or raw stack traces to the host
    LLM, but does log them for the operator.
    """
    settings = settings or MCPConceptRetrievalSettings()
    if not settings.enabled:
        return DiscoverOntologyConceptsOutput(
            query=inp.query,
            ontology_id=inp.ontology_id,
            error="concept retrieval is disabled (GRAPH_MCP_CONCEPTS_ENABLED=false)",
        )

    try:
        retriever = retriever or get_ontology_retriever()
    except ImportError:
        return DiscoverOntologyConceptsOutput(
            query=inp.query,
            ontology_id=inp.ontology_id,
            error=(
                "ontology_vectorizer is not installed; install with "
                "`pip install graph-mcp[rag]`"
            ),
        )
    except Exception as exc:
        logger.exception("concept_retriever_unavailable")
        return DiscoverOntologyConceptsOutput(
            query=inp.query,
            ontology_id=inp.ontology_id,
            error=_safe_error(exc),
        )

    ontology_id = inp.ontology_id or settings.default_ontology_id
    include_deprecated = (
        inp.include_deprecated or settings.include_deprecated_by_default
    )
    if ontology_id is None:
        logger.warning(
            "concept_retrieval_missing_ontology_id query=%r", inp.query
        )

    try:
        # The vectorizer's retrieval pipeline is synchronous (Qdrant
        # client + Foundry are blocking I/O); offload to a worker thread so
        # we don't pin the asyncio loop.
        response = await asyncio.to_thread(
            retriever.search_concepts,
            query=inp.query,
            ontology_id=ontology_id,
            top_k=inp.top_k,
            include_deprecated=include_deprecated,
            kind_filter=inp.kind_filter,
            branch_filter=inp.branch_filter,
            group_filter=inp.group_filter,
        )
    except Exception as exc:
        logger.exception("concept_retrieval_failed")
        return DiscoverOntologyConceptsOutput(
            query=inp.query,
            ontology_id=ontology_id,
            error=_safe_error(exc),
        )

    return DiscoverOntologyConceptsOutput(
        query=response.query,
        ontology_id=response.ontology_id,
        retrieval_strategy=response.retrieval_strategy,
        total_candidates_considered=response.total_candidates_considered,
        results=[_map_result(r) for r in response.results],
    )


def _map_result(r: Any) -> DiscoverOntologyConceptsResult:
    """Convert a vectorizer search result to the MCP wire schema.

    Strips the ``debug`` field (the MCP boundary intentionally hides it)
    and otherwise copies fields through verbatim.
    """
    return DiscoverOntologyConceptsResult(
        concept_id=r.concept_id,
        iri=r.iri,
        compact_id=r.compact_id,
        preferred_label=r.preferred_label,
        labels=list(r.labels),
        alt_labels=list(r.alt_labels),
        kind=r.kind,
        definition=r.definition,
        ontology_id=r.ontology_id,
        score=r.score,
        reranker_score=r.reranker_score,
        identity_score=r.identity_score,
        context_score=r.context_score,
        lexical_score=r.lexical_score,
        group_score=r.group_score,
        deprecated=r.deprecated,
        parents=list(r.parents),
        ancestors=list(r.ancestors),
        children=list(r.children),
        siblings=list(r.siblings),
        group_ids=list(r.group_ids),
        explanation=r.explanation,
    )


def _safe_error(exc: Exception) -> str:
    """Return a redacted error string suitable for the MCP wire.

    We deliberately do not include the exception type or stack frames —
    those land in the operator log. The host LLM gets a one-line summary
    and the operator can correlate via timestamps.
    """
    msg = str(exc).strip() or exc.__class__.__name__
    # Belt-and-braces: never echo characters that look like a token.
    if "Bearer " in msg or "api_key" in msg.lower():
        return "concept retrieval failed (see server logs)"
    return msg


__all__ = [
    "DiscoverOntologyConceptsInput",
    "DiscoverOntologyConceptsOutput",
    "DiscoverOntologyConceptsResult",
    "MCPConceptRetrievalSettings",
    "get_ontology_retriever",
    "reset_ontology_retriever",
    "set_ontology_retriever",
    "tool_discover_ontology_concepts",
]
