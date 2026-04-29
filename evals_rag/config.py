"""RAG eval configuration loaded from environment variables.

The settings here are deliberately separate from :class:`graph_mcp.config.Settings`
so the production server is not coupled to an optional Qdrant dependency.
Any value that points at infrastructure (Qdrant URL, collection name) lives
here; defaults are safe placeholders that explicitly fail closed when used
without configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RagSettings:
    """Static configuration for the RAG retrieval/re-ranking pipeline.

    Loaded once with :meth:`from_env` so tests can construct alternative
    settings without monkeypatching ``os.environ``.
    """

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "ontology_concepts"
    retrieval_limit: int = 20
    selected_limit: int = 8
    score_threshold: float = 0.0
    use_reranker: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RagSettings:
        e = env if env is not None else dict(os.environ)
        return cls(
            qdrant_url=e.get("GRAPH_MCP_RAG_QDRANT_URL", cls.qdrant_url),
            qdrant_api_key=e.get("GRAPH_MCP_RAG_QDRANT_API_KEY") or None,
            qdrant_collection=e.get("GRAPH_MCP_RAG_QDRANT_COLLECTION", cls.qdrant_collection),
            retrieval_limit=_int_or(e.get("GRAPH_MCP_RAG_RETRIEVAL_LIMIT"), cls.retrieval_limit),
            selected_limit=_int_or(e.get("GRAPH_MCP_RAG_SELECTED_LIMIT"), cls.selected_limit),
            score_threshold=_float_or(e.get("GRAPH_MCP_RAG_SCORE_THRESHOLD"), cls.score_threshold),
            use_reranker=_bool_or(e.get("GRAPH_MCP_RAG_USE_RERANKER"), cls.use_reranker),
        )


def _int_or(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_or(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool_or(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
