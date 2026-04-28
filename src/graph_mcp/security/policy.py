"""Security policy: declarative limits used by validator and executor."""

from __future__ import annotations

from dataclasses import dataclass

from graph_mcp.config import Settings


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    """Runtime-enforced limits and allowlists.

    The policy is consulted by the validator (static checks) and the executor
    (timeout / max-rows). Construct via :meth:`from_settings` to bind to the
    process-wide configuration.
    """

    default_limit: int
    max_limit: int
    timeout_ms: int
    allowed_graphs: frozenset[str]
    allowed_service_endpoints: frozenset[str]
    enable_raw_sparql: bool
    max_triple_patterns: int
    max_query_depth: int
    max_property_path_complexity: int
    allow_unbounded_paths: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> SecurityPolicy:
        return cls(
            default_limit=settings.default_limit,
            max_limit=settings.max_limit,
            timeout_ms=settings.timeout_ms,
            allowed_graphs=frozenset(settings.allowed_graphs),
            allowed_service_endpoints=frozenset(settings.allowed_service_endpoints),
            enable_raw_sparql=settings.enable_raw_sparql,
            max_triple_patterns=settings.max_triple_patterns,
            max_query_depth=settings.max_query_depth,
            max_property_path_complexity=settings.max_property_path_complexity,
            allow_unbounded_paths=settings.allow_unbounded_paths,
        )

    def is_graph_allowed(self, iri: str) -> bool:
        """A named graph is allowed when no allowlist is configured or it is listed."""
        return not self.allowed_graphs or iri in self.allowed_graphs

    def is_service_allowed(self, iri: str) -> bool:
        """SERVICE is allowed only if explicitly listed."""
        return iri in self.allowed_service_endpoints
