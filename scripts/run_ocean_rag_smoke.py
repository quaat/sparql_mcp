#!/usr/bin/env python3
"""Run the ocean free-text RAG smoke against a live Fuseki dataset.

Thin wrapper around ``python -m evals_rag.runner --graph-source sparql``
that pins the ocean-specific cases and writes a report under
``reports/ocean-fuseki-smoke``. Schema is discovered live from the
endpoint; the planner uses :class:`evals_rag.retrieval.MockOntologyRetriever`
over that snapshot plus the heuristic reranker. No LLM is required;
without ``--azure`` / ``--model`` the deterministic baseline planner is
used so the script can still be called as a CI smoke test.

Pre-flight: refresh schema discovery and warn loudly when zero classes /
properties are discovered, since that almost always means the Fuseki URL
or dataset name is wrong rather than a planner bug.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from evals.runner import build_components
from graph_mcp.models.literals import OCEAN_KG_PREFIXES

REPO = Path(__file__).resolve().parent.parent

_DEFAULT_ENDPOINT = "http://localhost:3030/ocean/sparql"


def _safe_endpoint_repr(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}"


def _build_auth() -> tuple[str, str] | None:
    user = os.environ.get("FUSEKI_ADMIN_USER")
    if not user:
        return None
    password = os.environ.get("FUSEKI_ADMIN_PASSWORD")
    if not password:
        return None
    return (user, password)


async def _preflight(endpoint_url: str, auth: tuple[str, str] | None) -> int:
    """Discover schema once and warn if it looks empty."""
    components = await build_components(
        endpoint_url=endpoint_url,
        auth=auth,
        extra_prefixes=dict(OCEAN_KG_PREFIXES),
    )
    snap = components.schema_provider.snapshot()
    print(f"endpoint:    {_safe_endpoint_repr(endpoint_url)}")
    print(f"auth:        {'configured' if auth else 'none'}")
    print(f"classes:     {len(snap.classes)}")
    print(f"properties:  {len(snap.properties)}")
    print(f"individuals: {len(snap.individuals)}")
    print(f"named graphs: {len(snap.named_graphs)}")
    if snap.diagnostics:
        print("schema discovery diagnostics:")
        for d in snap.diagnostics:
            print(f"  - {d.section}: {d.error}")
    if not snap.classes and not snap.properties:
        print(
            "WARNING: schema discovery returned zero classes and zero properties.\n"
            "         Check the endpoint URL, dataset name, and that the dataset\n"
            "         is loaded with content (raw SPARQL `?s ?p ?o` should match).",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_ocean_rag_smoke")
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("GRAPH_MCP_ENDPOINT_URL", _DEFAULT_ENDPOINT),
        help="SPARQL query endpoint. Default: $GRAPH_MCP_ENDPOINT_URL or local Fuseki.",
    )
    parser.add_argument(
        "--cases",
        default=str(REPO / "evals_rag" / "ocean_golden_cases.yaml"),
    )
    parser.add_argument(
        "--report-dir",
        default=str(REPO / "reports" / "ocean-fuseki-smoke"),
    )
    parser.add_argument("--azure", action="store_true", help="Use Azure OpenAI as the LLM backend.")
    parser.add_argument("--azure-endpoint", default=None)
    parser.add_argument("--model", default=None)
    parsed = parser.parse_args(argv)

    auth = _build_auth()
    rc = asyncio.run(_preflight(parsed.endpoint_url, auth))
    if rc != 0:
        return rc

    # Forward to the runner CLI rather than re-implementing it.
    import subprocess

    argv_runner = [
        sys.executable,
        "-m",
        "evals_rag.runner",
        "--planner",
        "rag",
        "--retriever",
        "mock",
        "--reranker",
        "heuristic",
        "--graph-source",
        "sparql",
        "--endpoint-url",
        parsed.endpoint_url,
        "--cases",
        parsed.cases,
        "--report-dir",
        parsed.report_dir,
    ]
    if auth is not None:
        argv_runner.extend(["--endpoint-user", auth[0]])
    if parsed.azure:
        argv_runner.append("--azure")
        if parsed.azure_endpoint:
            argv_runner.extend(["--azure-endpoint", parsed.azure_endpoint])
    if parsed.model:
        argv_runner.extend(["--model", parsed.model])

    print()
    print("$ " + " ".join(argv_runner), flush=True)
    return subprocess.call(argv_runner)


if __name__ == "__main__":
    sys.exit(main())
