#!/usr/bin/env python3
"""Raw SPARQL smoke checks against the ocean Fuseki dataset.

Reads the query endpoint URL from ``GRAPH_MCP_ENDPOINT_URL`` (default
``http://localhost:3030/ocean/sparql``). If Fuseki requires Basic Auth,
set ``FUSEKI_ADMIN_USER`` and ``FUSEKI_ADMIN_PASSWORD``; the smoke runs
as the configured user but never echoes the password back to the
console or report.

Each named check runs a SELECT query and is expected to return at least
one row. Any query that returns zero rows or errors out causes the
script to exit non-zero so CI / on-call can spot a broken Fuseki snapshot
without reading log spam.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

from graph_mcp.graph.endpoint import EndpointError, HttpSparqlEndpoint
from graph_mcp.models import SelectResult


@dataclass
class _Check:
    name: str
    sparql: str
    expect_rows: bool = True


_CHECKS: list[_Check] = [
    _Check(
        name="01_temperature_datasets",
        sparql="""\
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX var:  <https://example.org/ocean-demo/id/observable-property/>

SELECT DISTINCT ?dataset ?datasetLabel ?variableLabel
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcat:theme ?variable .
  ?variable skos:prefLabel ?variableLabel ;
            skos:broader var:temperature-variable .
}
ORDER BY ?datasetLabel
""",
    ),
    _Check(
        name="02_platforms_sensors_variables",
        sparql="""\
PREFIX sosa: <http://www.w3.org/ns/sosa/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?platformLabel ?sensorLabel ?variableLabel
WHERE {
  ?platform a sosa:Platform ;
            rdfs:label ?platformLabel ;
            sosa:hosts ?sensor .
  ?sensor rdfs:label ?sensorLabel ;
          sosa:observes ?variable .
  ?variable skos:prefLabel ?variableLabel .
}
ORDER BY ?platformLabel ?sensorLabel ?variableLabel
""",
    ),
    _Check(
        name="03_spatial_geometries",
        sparql="""\
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX geo:     <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?featureLabel ?wkt
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcterms:spatial ?feature .
  ?feature rdfs:label ?featureLabel ;
           geo:hasGeometry ?geometry .
  ?geometry geo:asWKT ?wkt .
}
ORDER BY ?datasetLabel
""",
    ),
    _Check(
        name="04_active_after_2020",
        sparql="""\
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?start ?end
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcterms:temporal ?period .
  ?period dcat:startDate ?start .
  OPTIONAL { ?period dcat:endDate ?end }
  FILTER(!BOUND(?end) || STR(?end) >= "2020-01-01")
}
ORDER BY ?start
""",
    ),
    _Check(
        name="05_buoys_floats_vessels",
        sparql="""\
PREFIX app:     <https://example.org/ontology/app#>
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX sosa:    <http://www.w3.org/ns/sosa/>
PREFIX skos:    <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?datasetLabel ?platformLabel ?platformType
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           app:describesEntity ?platform .
  ?platform a sosa:Platform ;
            rdfs:label ?platformLabel ;
            dcterms:type ?type .
  ?type skos:prefLabel ?platformType .
}
ORDER BY ?platformType ?datasetLabel
""",
    ),
    _Check(
        name="06_publishers",
        sparql="""\
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?publisherLabel
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcterms:publisher ?publisher .
  ?publisher rdfs:label ?publisherLabel .
}
ORDER BY ?publisherLabel ?datasetLabel
""",
    ),
    _Check(
        name="07_provenance",
        sparql="""\
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?source
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           prov:wasDerivedFrom ?source .
}
ORDER BY ?datasetLabel
""",
    ),
    _Check(
        name="08_distributions_formats",
        sparql="""\
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?datasetLabel ?format ?accessURL
WHERE {
  ?dataset a dcat:Dataset ;
           rdfs:label ?datasetLabel ;
           dcat:distribution ?distribution .
  ?distribution dcterms:format ?format ;
                dcat:accessURL ?accessURL .
}
ORDER BY ?datasetLabel
""",
    ),
]


def _safe_endpoint_repr(url: str) -> str:
    """Return ``scheme://host/path`` so logs do not echo any embedded creds."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}"


def _build_auth() -> tuple[str, str] | None:
    user = os.environ.get("FUSEKI_ADMIN_USER")
    if not user:
        return None
    password = os.environ.get("FUSEKI_ADMIN_PASSWORD") or ""
    if not password:
        return None
    return (user, password)


def _format_row(row_bindings: dict[str, object]) -> str:
    return ", ".join(
        f"{var}={getattr(value, 'value', value)!r}" for var, value in row_bindings.items()
    )


async def _run_one(endpoint: HttpSparqlEndpoint, check: _Check) -> tuple[bool, int, list[str]]:
    """Run one check; return (passed, row_count, sample_rows)."""
    try:
        result = await endpoint.query(
            check.sparql,
            query_type="select",
            timeout_ms=15_000,
            max_rows=200,
        )
    except EndpointError as exc:
        return False, 0, [f"ERROR: {exc}"]
    if not isinstance(result, SelectResult):
        return False, 0, [f"ERROR: unexpected result kind {type(result).__name__}"]
    rows = result.rows
    samples = [_format_row(r.bindings) for r in rows[:3]]
    passed = (len(rows) > 0) if check.expect_rows else (len(rows) == 0)
    return passed, len(rows), samples


async def _run(endpoint_url: str, auth: tuple[str, str] | None) -> int:
    print(f"endpoint: {_safe_endpoint_repr(endpoint_url)}")
    print(f"auth:     {'configured' if auth else 'none'}")
    print()
    print(f"{'check':32}  {'status':6}  {'rows':>4}  sample")
    print("-" * 80)

    endpoint = HttpSparqlEndpoint(endpoint_url, auth=auth)
    failed = 0
    try:
        for check in _CHECKS:
            passed, row_count, samples = await _run_one(endpoint, check)
            status = "PASS" if passed else "FAIL"
            sample_str = samples[0] if samples else ""
            print(f"{check.name:32}  {status:6}  {row_count:>4}  {sample_str}")
            for extra in samples[1:]:
                print(f"{'':32}  {'':6}  {'':>4}  {extra}")
            if not passed:
                failed += 1
    finally:
        await endpoint.aclose()
    print()
    print(f"{len(_CHECKS) - failed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_ocean_fuseki_smoke")
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("GRAPH_MCP_ENDPOINT_URL", "http://localhost:3030/ocean/sparql"),
        help="SPARQL query endpoint. Default: $GRAPH_MCP_ENDPOINT_URL or local Fuseki.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.endpoint_url, _build_auth()))


if __name__ == "__main__":
    sys.exit(main())
