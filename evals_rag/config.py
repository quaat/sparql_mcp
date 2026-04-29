"""RAG eval configuration loaded from environment variables.

The settings here are deliberately separate from :class:`graph_mcp.config.Settings`
so the production server is not coupled to an optional Qdrant dependency.
Any value that points at infrastructure (Qdrant URL, collection name) lives
here; defaults are safe placeholders that explicitly fail closed when used
without configuration.

Parsing is strict: invalid values raise :class:`RagConfigError` rather than
silently falling back to defaults. The runner surfaces those errors as a
top-level startup failure so misconfiguration cannot be confused with a
planner-quality regression.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class RagConfigError(ValueError):
    """Raised when an environment variable carries an invalid RAG setting."""


@dataclass(frozen=True)
class RagSettings:
    """Static configuration for the RAG retrieval/re-ranking pipeline."""

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "ontology_concepts"
    retrieval_limit: int = 20
    selected_limit: int = 8
    score_threshold: float = 0.0
    use_reranker: bool = True

    def __post_init__(self) -> None:
        if not self.qdrant_url:
            raise RagConfigError("GRAPH_MCP_RAG_QDRANT_URL must not be empty")
        if not self.qdrant_collection:
            raise RagConfigError("GRAPH_MCP_RAG_QDRANT_COLLECTION must not be empty")
        if self.retrieval_limit <= 0:
            raise RagConfigError(f"retrieval_limit must be > 0; got {self.retrieval_limit}")
        if self.selected_limit <= 0:
            raise RagConfigError(f"selected_limit must be > 0; got {self.selected_limit}")
        if self.selected_limit > self.retrieval_limit:
            raise RagConfigError(
                "selected_limit must not exceed retrieval_limit "
                f"(selected_limit={self.selected_limit}, "
                f"retrieval_limit={self.retrieval_limit})"
            )
        if self.score_threshold < 0:
            raise RagConfigError(f"score_threshold must be >= 0; got {self.score_threshold}")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RagSettings:
        e = env if env is not None else dict(os.environ)
        return cls(
            qdrant_url=e.get("GRAPH_MCP_RAG_QDRANT_URL", cls.qdrant_url),
            qdrant_api_key=e.get("GRAPH_MCP_RAG_QDRANT_API_KEY") or None,
            qdrant_collection=e.get("GRAPH_MCP_RAG_QDRANT_COLLECTION", cls.qdrant_collection),
            retrieval_limit=_int(
                e.get("GRAPH_MCP_RAG_RETRIEVAL_LIMIT"),
                default=cls.retrieval_limit,
                key="GRAPH_MCP_RAG_RETRIEVAL_LIMIT",
            ),
            selected_limit=_int(
                e.get("GRAPH_MCP_RAG_SELECTED_LIMIT"),
                default=cls.selected_limit,
                key="GRAPH_MCP_RAG_SELECTED_LIMIT",
            ),
            score_threshold=_float(
                e.get("GRAPH_MCP_RAG_SCORE_THRESHOLD"),
                default=cls.score_threshold,
                key="GRAPH_MCP_RAG_SCORE_THRESHOLD",
            ),
            use_reranker=_bool(
                e.get("GRAPH_MCP_RAG_USE_RERANKER"),
                default=cls.use_reranker,
                key="GRAPH_MCP_RAG_USE_RERANKER",
            ),
        )


def _int(value: str | None, *, default: int, key: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RagConfigError(f"Invalid {key}: expected integer, got {value!r}") from exc


def _float(value: str | None, *, default: float, key: str) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RagConfigError(f"Invalid {key}: expected float, got {value!r}") from exc


_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n", "off"})


def _bool(value: str | None, *, default: bool, key: str) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RagConfigError(f"Invalid {key}: expected bool (true/false/1/0/yes/no), got {value!r}")
