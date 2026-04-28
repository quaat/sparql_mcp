"""Schema providers: declared prefixes, classes, properties, named graphs.

Two implementations are provided:

- :class:`StaticSchemaProvider`: backed by an in-memory snapshot. Used in
  tests and when the host wants to inject a curated schema.
- :class:`SparqlSchemaProvider`: queries an actual endpoint (typically the
  same one used for queries) and caches the result. Use this in production
  to discover the live schema.

The provider returns a :class:`SchemaSnapshot`, which the MCP layer exposes
as resources (``graph://schema/...``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class SchemaTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iri: str
    prefixed_name: str | None = None
    label: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None


class ClassTerm(SchemaTerm):
    pass


class PropertyTerm(SchemaTerm):
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)


class IndividualTerm(SchemaTerm):
    types: list[str] = Field(default_factory=list)


class NamedGraphTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iri: str
    label: str | None = None
    description: str | None = None


class ExamplePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    plan: dict[str, object]


class SchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefixes: dict[str, str] = Field(default_factory=dict)
    classes: list[ClassTerm] = Field(default_factory=list)
    properties: list[PropertyTerm] = Field(default_factory=list)
    individuals: list[IndividualTerm] = Field(default_factory=list)
    named_graphs: list[NamedGraphTerm] = Field(default_factory=list)
    examples: list[ExamplePlan] = Field(default_factory=list)


class SchemaProvider(Protocol):
    def snapshot(self) -> SchemaSnapshot: ...


class StaticSchemaProvider:
    """Schema provider backed by an in-memory snapshot."""

    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> SchemaSnapshot:
        return self._snapshot

    @classmethod
    def empty(cls) -> StaticSchemaProvider:
        return cls(SchemaSnapshot())


@dataclass
class SparqlDiscoveryConfig:
    """Knobs for the endpoint-backed schema discovery."""

    timeout_ms: int = 10_000
    """Per-discovery query timeout."""

    max_classes: int = 200
    max_properties: int = 500
    max_individuals: int = 200
    cache_ttl_seconds: float = 300.0
    """How long to keep a snapshot before re-querying."""

    base_prefixes: dict[str, str] = field(default_factory=dict)
    """Prefixes to expose alongside whatever the endpoint advertises."""


class SparqlSchemaProvider:
    """Schema provider that queries a SPARQL endpoint and caches the result.

    Discovery is best-effort: a query that fails (timeout, network, unsupported
    feature) is reported as an empty list for that section but does not raise.
    The provider always returns a usable snapshot.
    """

    def __init__(
        self,
        endpoint: object,
        config: SparqlDiscoveryConfig | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._config = config or SparqlDiscoveryConfig()
        self._cache: SchemaSnapshot | None = None
        self._cache_at: float = 0.0

    def snapshot(self) -> SchemaSnapshot:
        """Return the cached snapshot, refreshing if expired.

        This is the synchronous accessor used by MCP resources. Internally it
        consumes the cached value populated by :meth:`refresh` — the first
        call returns an empty snapshot if no refresh has run yet, so callers
        that need fresh data should ``await provider.refresh()`` once at
        startup.
        """
        if self._cache is None:
            return SchemaSnapshot(prefixes=dict(self._config.base_prefixes))
        return self._cache

    async def refresh(self) -> SchemaSnapshot:
        """Re-query the endpoint and return the new snapshot."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_at) < self._config.cache_ttl_seconds:
            return self._cache

        cfg = self._config
        prefixes = dict(cfg.base_prefixes)

        classes = await self._discover_classes(cfg)
        properties = await self._discover_properties(cfg)
        individuals = await self._discover_individuals(cfg)
        named_graphs = await self._discover_named_graphs(cfg)

        snap = SchemaSnapshot(
            prefixes=prefixes,
            classes=classes,
            properties=properties,
            individuals=individuals,
            named_graphs=named_graphs,
        )
        self._cache = snap
        self._cache_at = now
        return snap

    # --- Discovery queries (best-effort) ---------------------------------

    async def _select(self, sparql: str, *, timeout_ms: int, max_rows: int) -> list[dict[str, str]]:
        """Execute a SELECT and return raw bindings."""
        try:
            result = await self._endpoint.query(  # type: ignore[attr-defined]
                sparql,
                query_type="select",
                timeout_ms=timeout_ms,
                max_rows=max_rows,
            )
        except Exception:
            return []
        if getattr(result, "kind", None) != "select":
            return []
        return [
            {var: binding.value for var, binding in row.bindings.items()} for row in result.rows
        ]

    async def _discover_classes(self, cfg: SparqlDiscoveryConfig) -> list[ClassTerm]:
        sparql = (
            "SELECT DISTINCT ?cls (SAMPLE(?l) AS ?label) WHERE { "
            "  ?s a ?cls . "
            "  OPTIONAL { ?cls <http://www.w3.org/2000/01/rdf-schema#label> ?l } "
            "} GROUP BY ?cls "
            f"LIMIT {cfg.max_classes}"
        )
        rows = await self._select(sparql, timeout_ms=cfg.timeout_ms, max_rows=cfg.max_classes)
        return [ClassTerm(iri=r["cls"], label=r.get("label")) for r in rows if r.get("cls")]

    async def _discover_properties(self, cfg: SparqlDiscoveryConfig) -> list[PropertyTerm]:
        sparql = (
            "SELECT DISTINCT ?p (SAMPLE(?l) AS ?label) WHERE { "
            "  ?s ?p ?o . "
            "  OPTIONAL { ?p <http://www.w3.org/2000/01/rdf-schema#label> ?l } "
            "} GROUP BY ?p "
            f"LIMIT {cfg.max_properties}"
        )
        rows = await self._select(sparql, timeout_ms=cfg.timeout_ms, max_rows=cfg.max_properties)
        return [PropertyTerm(iri=r["p"], label=r.get("label")) for r in rows if r.get("p")]

    async def _discover_individuals(self, cfg: SparqlDiscoveryConfig) -> list[IndividualTerm]:
        sparql = (
            "SELECT DISTINCT ?s (SAMPLE(?l) AS ?label) (SAMPLE(?t) AS ?type) WHERE { "
            "  ?s a ?t . "
            "  FILTER (!isBlank(?s)) "
            "  OPTIONAL { ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l } "
            "} GROUP BY ?s "
            f"LIMIT {cfg.max_individuals}"
        )
        rows = await self._select(sparql, timeout_ms=cfg.timeout_ms, max_rows=cfg.max_individuals)
        out: list[IndividualTerm] = []
        for r in rows:
            iri = r.get("s")
            if not iri:
                continue
            types = [r["type"]] if r.get("type") else []
            out.append(IndividualTerm(iri=iri, label=r.get("label"), types=types))
        return out

    async def _discover_named_graphs(self, cfg: SparqlDiscoveryConfig) -> list[NamedGraphTerm]:
        sparql = f"SELECT DISTINCT ?g WHERE {{ GRAPH ?g {{ ?s ?p ?o }} }} LIMIT {cfg.max_classes}"
        rows = await self._select(sparql, timeout_ms=cfg.timeout_ms, max_rows=cfg.max_classes)
        return [NamedGraphTerm(iri=r["g"]) for r in rows if r.get("g")]
