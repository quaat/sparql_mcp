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
from datetime import UTC
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


class SchemaDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str
    """Discovery section: classes / properties / individuals / named_graphs."""
    error: str


class SchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefixes: dict[str, str] = Field(default_factory=dict)
    classes: list[ClassTerm] = Field(default_factory=list)
    properties: list[PropertyTerm] = Field(default_factory=list)
    individuals: list[IndividualTerm] = Field(default_factory=list)
    named_graphs: list[NamedGraphTerm] = Field(default_factory=list)
    examples: list[ExamplePlan] = Field(default_factory=list)
    diagnostics: list[SchemaDiagnostic] = Field(default_factory=list)
    """Errors encountered during the most recent discovery refresh."""

    last_refresh_at: str | None = None
    """ISO-8601 timestamp of the last successful refresh, or ``None`` for a
    snapshot that was never refreshed (e.g., a fresh static provider)."""


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
    max_named_graphs: int = 200
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
        """Re-query the endpoint and return the new snapshot.

        The optional ``force`` flag (call as ``await refresh()``) bypasses
        the TTL check; the underlying signature is preserved for back-compat.
        """
        return await self._refresh(force=False)

    async def refresh_force(self) -> SchemaSnapshot:
        """Re-query the endpoint, ignoring the TTL cache."""
        return await self._refresh(force=True)

    async def _refresh(self, *, force: bool) -> SchemaSnapshot:
        from datetime import datetime

        now = time.monotonic()
        if (
            not force
            and self._cache is not None
            and (now - self._cache_at) < self._config.cache_ttl_seconds
        ):
            return self._cache

        cfg = self._config
        prefixes = dict(cfg.base_prefixes)
        diagnostics: list[SchemaDiagnostic] = []

        classes = await self._discover_classes(cfg, diagnostics)
        properties = await self._discover_properties(cfg, diagnostics)
        individuals = await self._discover_individuals(cfg, diagnostics)
        named_graphs = await self._discover_named_graphs(cfg, diagnostics)

        # Generate prefixed_name for every term whose IRI starts with a known prefix.
        for term in (*classes, *properties, *individuals):
            term.prefixed_name = _to_prefixed(term.iri, prefixes)

        snap = SchemaSnapshot(
            prefixes=prefixes,
            classes=classes,
            properties=properties,
            individuals=individuals,
            named_graphs=named_graphs,
            diagnostics=diagnostics,
            last_refresh_at=datetime.now(tz=UTC).isoformat(),
        )
        self._cache = snap
        self._cache_at = now
        return snap

    # --- Discovery queries (best-effort) ---------------------------------

    async def _select(
        self,
        sparql: str,
        *,
        timeout_ms: int,
        max_rows: int,
        section: str,
        diagnostics: list[SchemaDiagnostic],
    ) -> list[dict[str, str]]:
        """Execute a SELECT and return raw bindings.

        Records errors as :class:`SchemaDiagnostic` rather than raising; the
        caller still gets ``[]`` on failure but the operator can see the
        cause via ``graph://schema/status``.
        """
        try:
            result = await self._endpoint.query(  # type: ignore[attr-defined]
                sparql,
                query_type="select",
                timeout_ms=timeout_ms,
                max_rows=max_rows,
            )
        except Exception as exc:
            diagnostics.append(
                SchemaDiagnostic(section=section, error=f"{type(exc).__name__}: {exc}")
            )
            return []
        if getattr(result, "kind", None) != "select":
            diagnostics.append(
                SchemaDiagnostic(
                    section=section,
                    error=f"unexpected result kind: {getattr(result, 'kind', None)!r}",
                )
            )
            return []
        return [
            {var: binding.value for var, binding in row.bindings.items()} for row in result.rows
        ]

    async def _discover_classes(
        self, cfg: SparqlDiscoveryConfig, diag: list[SchemaDiagnostic]
    ) -> list[ClassTerm]:
        # Combine declared classes (rdfs:Class / owl:Class) with classes
        # observed via ``?s a ?cls``. Use UNION so the query works on stores
        # that lack ontology metadata.
        sparql = (
            "SELECT DISTINCT ?cls (SAMPLE(?l) AS ?label) WHERE { "
            "  { ?cls <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "         <http://www.w3.org/2000/01/rdf-schema#Class> } "
            "  UNION "
            "  { ?cls <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "         <http://www.w3.org/2002/07/owl#Class> } "
            "  UNION "
            "  { ?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?cls } "
            "  OPTIONAL { ?cls <http://www.w3.org/2000/01/rdf-schema#label> ?l } "
            "  FILTER (isIRI(?cls)) "
            "} GROUP BY ?cls "
            f"LIMIT {cfg.max_classes}"
        )
        rows = await self._select(
            sparql,
            timeout_ms=cfg.timeout_ms,
            max_rows=cfg.max_classes,
            section="classes",
            diagnostics=diag,
        )
        return [ClassTerm(iri=r["cls"], label=r.get("label")) for r in rows if r.get("cls")]

    async def _discover_properties(
        self, cfg: SparqlDiscoveryConfig, diag: list[SchemaDiagnostic]
    ) -> list[PropertyTerm]:
        """Discover properties by combining declared and observed predicates.

        We intentionally split this into two queries: one for the basic
        ``?p`` / label binding (uses GROUP BY safely because every row has
        ?p bound), and a separate, simpler query for ``rdfs:domain`` /
        ``rdfs:range`` which we aggregate in Python.

        Some triple stores (notably rdflib) raise ``NotBoundError`` when
        ``GROUP_CONCAT(DISTINCT ?x; SEPARATOR=' ')`` is combined with an
        ``OPTIONAL`` that may leave ``?x`` unbound — so we avoid that
        pattern entirely.
        """
        sparql = (
            "SELECT DISTINCT ?p (SAMPLE(?l) AS ?label) WHERE { "
            "  { ?p <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "       <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property> } "
            "  UNION "
            "  { ?p <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "       <http://www.w3.org/2002/07/owl#ObjectProperty> } "
            "  UNION "
            "  { ?p <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "       <http://www.w3.org/2002/07/owl#DatatypeProperty> } "
            "  UNION "
            "  { ?s ?p ?o } "
            "  OPTIONAL { "
            "    ?p <http://www.w3.org/2000/01/rdf-schema#label> ?l "
            "  } "
            "  FILTER (isIRI(?p)) "
            "} GROUP BY ?p "
            f"LIMIT {cfg.max_properties}"
        )
        rows = await self._select(
            sparql,
            timeout_ms=cfg.timeout_ms,
            max_rows=cfg.max_properties,
            section="properties",
            diagnostics=diag,
        )
        out: list[PropertyTerm] = []
        # Domain / range as a separate, side query. Aggregated in Python.
        domain_range = await self._discover_property_domain_range(cfg, diag)
        for r in rows:
            iri = r.get("p")
            if not iri:
                continue
            domain, range_ = domain_range.get(iri, ([], []))
            out.append(
                PropertyTerm(
                    iri=iri,
                    label=r.get("label"),
                    domain=domain,
                    range=range_,
                )
            )
        return out

    async def _discover_property_domain_range(
        self, cfg: SparqlDiscoveryConfig, diag: list[SchemaDiagnostic]
    ) -> dict[str, tuple[list[str], list[str]]]:
        sparql = (
            "SELECT ?p ?dom ?rng WHERE { "
            "  { ?p <http://www.w3.org/2000/01/rdf-schema#domain> ?dom } "
            "  UNION "
            "  { ?p <http://www.w3.org/2000/01/rdf-schema#range> ?rng } "
            "  FILTER (isIRI(?p)) "
            f"}} LIMIT {cfg.max_properties * 4}"
        )
        rows = await self._select(
            sparql,
            timeout_ms=cfg.timeout_ms,
            max_rows=cfg.max_properties * 4,
            section="properties_domain_range",
            diagnostics=diag,
        )
        out: dict[str, tuple[list[str], list[str]]] = {}
        for r in rows:
            iri = r.get("p")
            if not iri:
                continue
            domain, range_ = out.setdefault(iri, ([], []))
            if r.get("dom") and r["dom"] not in domain:
                domain.append(r["dom"])
            if r.get("rng") and r["rng"] not in range_:
                range_.append(r["rng"])
        return out

    async def _discover_individuals(
        self, cfg: SparqlDiscoveryConfig, diag: list[SchemaDiagnostic]
    ) -> list[IndividualTerm]:
        sparql = (
            "SELECT DISTINCT ?s "
            "(SAMPLE(?l) AS ?label) "
            "(SAMPLE(?t) AS ?type) "
            "WHERE { "
            "  ?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t . "
            "  OPTIONAL { "
            "    { ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l } "
            "    UNION "
            "    { ?s <http://www.w3.org/2004/02/skos/core#prefLabel> ?l } "
            "  } "
            "  FILTER (isIRI(?s)) "
            "} GROUP BY ?s "
            f"LIMIT {cfg.max_individuals}"
        )
        rows = await self._select(
            sparql,
            timeout_ms=cfg.timeout_ms,
            max_rows=cfg.max_individuals,
            section="individuals",
            diagnostics=diag,
        )
        out: list[IndividualTerm] = []
        for r in rows:
            iri = r.get("s")
            if not iri:
                continue
            types = [r["type"]] if r.get("type") else []
            out.append(IndividualTerm(iri=iri, label=r.get("label"), types=types))
        return out

    async def _discover_named_graphs(
        self, cfg: SparqlDiscoveryConfig, diag: list[SchemaDiagnostic]
    ) -> list[NamedGraphTerm]:
        sparql = (
            f"SELECT DISTINCT ?g WHERE {{ GRAPH ?g {{ ?s ?p ?o }} }} LIMIT {cfg.max_named_graphs}"
        )
        rows = await self._select(
            sparql,
            timeout_ms=cfg.timeout_ms,
            max_rows=cfg.max_named_graphs,
            section="named_graphs",
            diagnostics=diag,
        )
        return [NamedGraphTerm(iri=r["g"]) for r in rows if r.get("g")]


def _to_prefixed(iri: str, prefixes: dict[str, str]) -> str | None:
    """Return ``prefix:local`` if ``iri`` starts with a declared prefix IRI."""
    from graph_mcp.models.literals import PREFIXED_LOCAL_REGEX

    for prefix_name, prefix_iri in sorted(prefixes.items(), key=lambda kv: -len(kv[1])):
        if iri.startswith(prefix_iri):
            local = iri[len(prefix_iri) :]
            if PREFIXED_LOCAL_REGEX.match(local):
                return f"{prefix_name}:{local}"
    return None
