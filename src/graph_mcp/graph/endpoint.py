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
    ) -> QueryResult: ...

    async def aclose(self) -> None: ...


# --- HTTP endpoint --------------------------------------------------------


class HttpSparqlEndpoint:
    """SPARQL 1.1 endpoint over HTTP.

    ``auth`` is forwarded to ``httpx`` so callers can configure Basic Auth
    (or any ``httpx.Auth`` instance) without us reaching for raw headers.
    The credentials are kept on the underlying client and never logged.
    """

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        default_headers: dict[str, str] | None = None,
        auth: tuple[str, str] | httpx.Auth | None = None,
    ) -> None:
        self.url = url
        self._owns_client = client is None
        if client is not None:
            self._client = client
            # Caller-supplied client is authoritative; we don't override its
            # auth even when ``auth`` is set, since the client may already
            # be configured with a richer auth flow.
            self._auth: tuple[str, str] | httpx.Auth | None = None
        else:
            self._client = httpx.AsyncClient(auth=auth)
            self._auth = auth
        self._select_headers = {
            "Accept": "application/sparql-results+json",
            "User-Agent": "graph-mcp/0.1",
        }
        self._construct_headers = {
            # text/turtle is widely supported; application/n-triples as fallback.
            "Accept": ("text/turtle, application/n-triples;q=0.9, application/rdf+xml;q=0.5"),
            "User-Agent": "graph-mcp/0.1",
        }
        if default_headers:
            self._select_headers.update(default_headers)
            self._construct_headers.update(default_headers)

    async def query(
        self,
        sparql: str,
        *,
        query_type: str,
        timeout_ms: int,
        max_rows: int,
    ) -> QueryResult:
        timeout = httpx.Timeout(timeout_ms / 1000.0)
        headers = self._construct_headers if query_type == "construct" else self._select_headers
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                self.url,
                data={"query": sparql},
                headers=headers,
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
            return self._parse_ask_response(resp, ctype, meta)

        if query_type == "construct":
            return self._parse_construct_response(resp.text, ctype, meta, max_rows=max_rows)

        # SELECT
        return self._parse_select_response(resp, ctype, meta, max_rows=max_rows)

    def _parse_ask_response(
        self, resp: httpx.Response, ctype: str, meta: QueryExecutionMetadata
    ) -> AskResult:
        """Parse a SPARQL ASK response. Validates content-type and shape."""
        if "json" not in ctype:
            raise EndpointError(
                f"ASK response has unexpected content-type {ctype!r}; "
                "expected a SPARQL JSON results document"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise EndpointError(f"ASK response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "boolean" not in data:
            raise EndpointError("ASK response is missing the required top-level 'boolean' field")
        boolean = data["boolean"]
        if not isinstance(boolean, bool):
            raise EndpointError(f"ASK response 'boolean' field is not a JSON boolean: {boolean!r}")
        return AskResult(boolean=boolean, metadata=meta)

    def _parse_select_response(
        self,
        resp: httpx.Response,
        ctype: str,
        meta: QueryExecutionMetadata,
        *,
        max_rows: int,
    ) -> QueryResult:
        """Parse a SPARQL SELECT response, normalizing failures to EndpointError."""
        if "json" not in ctype:
            raise EndpointError(
                f"SELECT response has unexpected content-type {ctype!r}; "
                "expected a SPARQL JSON results document"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise EndpointError(f"SELECT response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise EndpointError(
                f"SELECT response top-level is not a JSON object (got {type(payload).__name__})"
            )
        head = payload.get("head")
        results = payload.get("results")
        # SPARQL 1.1 results JSON requires both head.vars and results.bindings.
        if not isinstance(head, dict) or "vars" not in head:
            raise EndpointError(
                "SELECT response is missing 'head.vars'; not a valid SPARQL JSON document"
            )
        if not isinstance(results, dict) or not isinstance(results.get("bindings"), list):
            raise EndpointError(
                "SELECT response is missing 'results.bindings' array; "
                "not a valid SPARQL JSON document"
            )

        try:
            result = normalize_sparql_json(payload, meta)
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise EndpointError(f"failed to normalize SELECT response: {exc}") from exc

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

    def _parse_construct_response(
        self,
        body: str,
        ctype: str,
        meta: QueryExecutionMetadata,
        *,
        max_rows: int,
    ) -> ConstructResult:
        """Parse a remote CONSTRUCT response via rdflib.

        Maps the response ``content-type`` to an rdflib parser. Raises
        :class:`EndpointError` for unsupported types and never silently
        returns an empty result. Triple results are truncated to
        ``max_rows`` as defense-in-depth and ``metadata.truncated`` is set
        when truncation actually occurred.
        """
        import rdflib

        # Map content-type to rdflib parser format.
        if "turtle" in ctype:
            fmt = "turtle"
        elif "n-triples" in ctype or "ntriples" in ctype:
            fmt = "nt"
        elif "rdf+xml" in ctype:
            fmt = "xml"
        elif "n3" in ctype:
            fmt = "n3"
        else:
            raise EndpointError(f"unsupported CONSTRUCT response content-type: {ctype!r}")

        try:
            g = rdflib.Graph().parse(data=body, format=fmt)
        except Exception as exc:  # pragma: no cover - rdflib parser failures
            raise EndpointError(f"failed to parse CONSTRUCT response: {exc}") from exc

        triples: list[Triple] = []
        truncated = False
        for s, p, o in g:
            if len(triples) >= max_rows:
                truncated = True
                break
            triples.append(Triple(subject=str(s), predicate=str(p), object=str(o)))
        return ConstructResult(
            triples=triples,
            metadata=meta.model_copy(
                update={"row_count": len(triples), "truncated": truncated},
            ),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# --- Local rdflib endpoint -----------------------------------------------


class LocalRdflibEndpoint:
    """In-memory rdflib endpoint.

    Best suited for tests, offline development, and small datasets. Timeout
    behavior is implemented by running the query in a worker thread and
    abandoning the result on timeout — rdflib has no first-class
    cancellation, so a runaway query will continue to consume CPU on its
    worker thread until it completes. Callers that need hard cancellation
    should run their workload via the HTTP endpoint against a server that
    enforces query budgets at the engine level.
    """

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

    _RDF_FORMAT_BY_SUFFIX = {
        ".ttl": "turtle",
        ".trig": "trig",
        ".nt": "nt",
        ".nq": "nquads",
        ".rdf": "xml",
        ".xml": "xml",
        ".jsonld": "json-ld",
    }

    @classmethod
    def from_rdf_file(cls, path: str | Path) -> LocalRdflibEndpoint:
        """Load an RDF file, picking the parser format from the file suffix.

        Supports the formats rdflib ships with by default (Turtle, TriG,
        N-Triples, N-Quads, RDF/XML, JSON-LD). TriG / N-Quads bring
        named graphs into the dataset; the others populate the default
        graph only.
        """
        import rdflib

        path = Path(path)
        fmt = cls._RDF_FORMAT_BY_SUFFIX.get(path.suffix.lower())
        if fmt is None:
            raise ValueError(
                f"unsupported RDF fixture suffix {path.suffix!r}; "
                f"known suffixes: {sorted(cls._RDF_FORMAT_BY_SUFFIX)}"
            )
        g = rdflib.Dataset()
        g.parse(str(path), format=fmt)
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
        import asyncio

        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        try:
            res = await asyncio.wait_for(
                loop.run_in_executor(None, self._graph.query, sparql),
                timeout=timeout_ms / 1000.0,
            )
        except TimeoutError as exc:
            raise EndpointError(f"local rdflib query timed out after {timeout_ms}ms") from exc
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
                triples.append(Triple(subject=str(s), predicate=str(p), object=str(o)))
            return ConstructResult(triples=triples, metadata=meta)

        return self._normalize_select(res, meta, max_rows=max_rows)

    def _normalize_select(
        self, res: Any, meta: QueryExecutionMetadata, *, max_rows: int
    ) -> SelectResult:
        """Normalize an rdflib result into a typed SelectResult.

        Truncation logic: we read up to ``max_rows + 1`` rows from rdflib's
        result iterator. If we got the extra row, the result is truncated;
        otherwise the iterator was already exhausted.
        """
        from graph_mcp.models import BindingValue, SolutionRow

        variables = [str(v) for v in (res.vars or [])]
        rows: list[SolutionRow] = []
        truncated = False
        for row in res:
            if len(rows) >= max_rows:
                # We have already read max_rows; this extra row proves the
                # iterator had more, so set truncated and stop.
                truncated = True
                break
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

        return SelectResult(
            variables=variables,
            rows=rows,
            metadata=meta.model_copy(update={"row_count": len(rows), "truncated": truncated}),
        )

    async def aclose(self) -> None:
        return None
