"""SPARQL endpoint executors.

Two implementations:

- :class:`HttpSparqlEndpoint` — talks to a remote 1.1 endpoint over HTTP.
- :class:`LocalRdflibEndpoint` — runs queries against an in-memory rdflib
  graph; intended for tests, demos, and offline development.

Both implement the :class:`GraphEndpoint` Protocol.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from graph_mcp.graph.result_normalizer import normalize_sparql_json
from graph_mcp.models import (
    AskResult,
    ConstructResult,
    QueryExecutionMetadata,
    QueryResult,
    SelectResult,
    Triple,
)


class EndpointError(Exception):
    """Wraps any failure to talk to the SPARQL endpoint."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@runtime_checkable
class GraphEndpoint(Protocol):
    """Read-only SPARQL endpoint."""

    async def query(
        self,
        sparql: str,
        *,
        query_type: str,
        timeout_ms: int,
        max_rows: int,
    ) -> QueryResult:
        ...

    async def aclose(self) -> None:
        ...


# --- HTTP endpoint --------------------------------------------------------


class HttpSparqlEndpoint:
    """SPARQL 1.1 endpoint over HTTP."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Accept": "application/sparql-results+json, application/rdf+xml;q=0.5",
            "User-Agent": "graph-mcp/0.1",
        }
        if default_headers:
            self._headers.update(default_headers)

    async def query(
        self,
        sparql: str,
        *,
        query_type: str,
        timeout_ms: int,
        max_rows: int,
    ) -> QueryResult:
        timeout = httpx.Timeout(timeout_ms / 1000.0)
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                self.url,
                data={"query": sparql},
                headers=self._headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise EndpointError(f"endpoint request timed out after {timeout_ms}ms") from exc
        except httpx.HTTPError as exc:
            raise EndpointError(f"endpoint request failed: {exc}") from exc
        duration_ms = (time.perf_counter() - started) * 1000.0

        if resp.status_code >= 400:
            raise EndpointError(
                f"endpoint returned HTTP {resp.status_code}",
                status=resp.status_code,
            )

        meta = QueryExecutionMetadata(
            duration_ms=duration_ms,
            endpoint=self.url,
        )

        ctype = resp.headers.get("content-type", "").lower()

        if query_type == "ask":
            data = resp.json()
            return AskResult(boolean=bool(data.get("boolean", False)), metadata=meta)

        if query_type == "construct":
            # Many endpoints return turtle/n-triples; we keep this minimal —
            # the LocalRdflibEndpoint handles CONSTRUCT in a typed way.
            return ConstructResult(triples=[], metadata=meta)

        if "json" not in ctype:
            raise EndpointError(f"unexpected content-type: {ctype}")

        result = normalize_sparql_json(resp.json(), meta)
        truncated = False
        if len(result.rows) > max_rows:
            result = result.model_copy(
                update={
                    "rows": result.rows[:max_rows],
                    "metadata": result.metadata.model_copy(
                        update={"row_count": max_rows, "truncated": True},
                    ),
                }
            )
            truncated = True
        if not truncated:
            result = result.model_copy(
                update={
                    "metadata": result.metadata.model_copy(
                        update={"row_count": len(result.rows)},
                    ),
                }
            )
        return result

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# --- Local rdflib endpoint -----------------------------------------------


class LocalRdflibEndpoint:
    """In-memory rdflib endpoint. Useful for tests and offline use."""

    def __init__(self, graph: Any | None = None) -> None:
        # Imported lazily so that environments without rdflib can still import
        # the module.
        import rdflib

        self._rdflib = rdflib
        self._graph = graph if graph is not None else rdflib.Dataset()

    @classmethod
    def from_turtle_file(cls, path: str | Path) -> LocalRdflibEndpoint:
        import rdflib

        g = rdflib.Dataset()
        g.parse(str(path), format="turtle")
        return cls(graph=g)

    @classmethod
    def from_turtle_string(cls, ttl: str) -> LocalRdflibEndpoint:
        import rdflib

        g = rdflib.Dataset()
        g.parse(data=ttl, format="turtle")
        return cls(graph=g)

    @property
    def graph(self) -> Any:
        return self._graph

    async def query(
        self,
        sparql: str,
        *,
        query_type: str,
        timeout_ms: int,
        max_rows: int,
    ) -> QueryResult:
        # rdflib is synchronous; we run it in-thread and treat timeout as
        # advisory — the validator already bounded the work.
        started = time.perf_counter()
        try:
            res = self._graph.query(sparql)
        except Exception as exc:  # pragma: no cover - rdflib parser errors
            raise EndpointError(f"rdflib query failed: {exc}") from exc
        duration_ms = (time.perf_counter() - started) * 1000.0
        meta = QueryExecutionMetadata(duration_ms=duration_ms, endpoint="local:rdflib")

        if query_type == "ask":
            return AskResult(boolean=bool(res.askAnswer), metadata=meta)

        if query_type == "construct":
            triples: list[Triple] = []
            res_any: Any = res
            for triple in res_any:
                s, p, o = triple
                triples.append(
                    Triple(subject=str(s), predicate=str(p), object=str(o))
                )
            return ConstructResult(triples=triples, metadata=meta)

        return self._normalize_select(res, meta, max_rows=max_rows)

    def _normalize_select(
        self, res: Any, meta: QueryExecutionMetadata, *, max_rows: int
    ) -> SelectResult:
        from graph_mcp.models import BindingValue, SolutionRow

        variables = [str(v) for v in (res.vars or [])]
        rows: list[SolutionRow] = []
        for row in res:
            bindings: dict[str, BindingValue] = {}
            for var in variables:
                val = row[self._rdflib.Variable(var)]
                if val is None:
                    continue
                if isinstance(val, self._rdflib.URIRef):
                    bindings[var] = BindingValue(type="uri", value=str(val))
                elif isinstance(val, self._rdflib.BNode):
                    bindings[var] = BindingValue(type="bnode", value=str(val))
                elif isinstance(val, self._rdflib.Literal):
                    bindings[var] = BindingValue(
                        type="literal",
                        value=str(val),
                        datatype=str(val.datatype) if val.datatype else None,
                        lang=str(val.language) if val.language else None,
                    )
            rows.append(SolutionRow(bindings=bindings))
            if len(rows) >= max_rows:
                break

        truncated = len(rows) >= max_rows
        return SelectResult(
            variables=variables,
            rows=rows,
            metadata=meta.model_copy(
                update={"row_count": len(rows), "truncated": truncated}
            ),
        )

    async def aclose(self) -> None:
        return None
