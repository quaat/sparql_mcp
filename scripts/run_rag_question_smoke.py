#!/usr/bin/env python3
"""20-question RAG smoke against the live ocean stack.

For each question this harness:

1. Calls :class:`evals_rag.retrieval.VectorizerOntologyRetriever` (the new
   RAG component that delegates to :mod:`ontology_vectorizer`) to fetch
   ranked concept candidates from the live Foundry + Qdrant pipeline.
2. Asks the Foundry-hosted LLM to draft a SPARQL query that answers the
   question, grounded in the retrieved IRIs.
3. Executes the SPARQL against Fuseki via
   :class:`graph_mcp.graph.endpoint.HttpSparqlEndpoint`.
4. Records question, retrieved concepts, generated SPARQL, and Fuseki rows.

Outputs a Markdown report at ``--report``. The report is the deliverable —
the script does not assert anything beyond "every question produced a
SPARQL string and an HTTP response from Fuseki".

Run with the ontology-vectorizer ``.env`` already sourced:

    set -a; source ~/project/ontology_vectorizer/.env; set +a
    PYTHONPATH=src:. .venv/bin/python scripts/run_rag_question_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent

# Make the editable repo importable when invoked from the repo root.
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals_rag.models import RetrievalQuery  # noqa: E402
from evals_rag.retrieval import (  # noqa: E402
    RetrievalError,
    VectorizerOntologyRetriever,
)
from graph_mcp.graph.endpoint import EndpointError, HttpSparqlEndpoint  # noqa: E402
from graph_mcp.models import (  # noqa: E402
    AskResult,
    ConstructResult,
    SelectResult,
)


# --- Curated questions ------------------------------------------------------
#
# Each entry has a short label describing the *style* of question being
# probed. Intent: cover the spectrum of NL-to-RAG patterns the planner
# should handle, not to maximize SPARQL coverage.

QUESTIONS: list[tuple[str, str]] = [
    ("direct concept lookup",
     "What is sea surface temperature?"),
    ("acronym / synonym",
     "What does SST mean?"),
    ("alt-label paraphrase",
     "Tell me about Argo floats."),
    ("alt-label paraphrase",
     "What is a Sea-Bird MicroCAT?"),
    ("definition lookup",
     "Define hydrographic variable."),
    ("class hierarchy (narrower)",
     "What types of CTD instruments exist in the ontology?"),
    ("class hierarchy (broader)",
     "Which kind of instrument is a MicroCAT CTD?"),
    ("multi-concept",
     "Find variables related to seawater temperature and salinity."),
    ("property metadata",
     "Which property links a sensor or platform to its deployment location?"),
    ("property usage",
     "Find platforms together with their deployment locations."),
    ("ABox enumeration",
     "List all datasets and their titles."),
    ("ABox via concept",
     "Which datasets describe sea surface temperature?"),
    ("multi-step traversal",
     "Find datasets that describe any temperature-related variable."),
    ("class enumeration",
     "Show me all organizations recorded in the dataset."),
    ("top-level concept",
     "What kinds of biogeochemical observations are tracked?"),
    ("taxonomy traversal",
     "What is Hexacorallia and what taxon does it belong to?"),
    ("Soft7 TBox",
     "What is a Soft7 property descriptor used for?"),
    ("deprecated handling",
     "Is there a legacy sensor identifier I should avoid using?"),
    ("instance listing",
     "List the sensors and the platforms they ride on."),
    ("spatial / location",
     "Where is the platform 'NASA Aqua satellite' deployed?"),
]


# --- Foundry chat client ---------------------------------------------------


@dataclass
class FoundryChatClient:
    """Tiny OpenAI-compatible chat client for the Foundry APIM gateway.

    The gateway authenticates with ``api-key`` (Azure-APIM convention),
    not ``Authorization: Bearer``. We avoid the OpenAI SDK so we can set
    the header directly without monkey-patching.
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        temperature: float = 0.0,
    ) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as c:
            r = await c.post(url, headers={"api-key": self.api_key}, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Foundry chat HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body["choices"][0]["message"]["content"]


# --- Prompt construction ---------------------------------------------------

_SYSTEM_PROMPT = """\
You are a SPARQL query planner for a small ocean-observations knowledge graph.

Your job is to turn a user question plus a list of retrieved ontology
concept candidates into a single SPARQL SELECT query that answers the
question against the graph. Output JSON only, with this exact shape:

{
  "reasoning": "<one sentence explaining how you grounded the question>",
  "sparql": "<a valid SPARQL 1.1 SELECT query>"
}

Hard rules:

- Output only the JSON object, no prose, no fences.
- The SPARQL must be a SELECT query and end with an explicit LIMIT (<= 25).
- Use only concept IRIs that appear in the candidate list, or the
  standard ontology IRIs listed below.
- Always declare every prefix you use.
- Never invent IRIs the candidate list doesn't contain.
- If the question is a definition / lookup question, project skos:prefLabel,
  skos:definition, or rdfs:comment for the matching concept.
- Use ?label / ?title / ?def style variable names for projected literals.

Standard prefixes you may rely on:

  rdf:     http://www.w3.org/1999/02/22-rdf-syntax-ns#
  rdfs:    http://www.w3.org/2000/01/rdf-schema#
  owl:     http://www.w3.org/2002/07/owl#
  skos:    http://www.w3.org/2004/02/skos/core#
  xsd:     http://www.w3.org/2001/XMLSchema#
  dcterms: http://purl.org/dc/terms/
  dcat:    http://www.w3.org/ns/dcat#
  prov:    http://www.w3.org/ns/prov#
  schema:  https://schema.org/
  sosa:    http://www.w3.org/ns/sosa/
  geo:     http://www.opengis.net/ont/geosparql#
  foaf:    http://xmlns.com/foaf/0.1/
  app:     https://example.org/ocean-demo/ontology/

The KG also uses these instance namespaces (do not redeclare):

  var:      observable-property concepts (skos:Concept)
  itype:    instrument-type concepts
  ptype:    platform-type concepts
  taxon:    taxon concepts
  ds:       dcat:Dataset instances
  sensor:   sosa:Sensor instances
  platform: sosa:Platform instances
  org:      prov:Organization instances
  feature:  geo:Feature instances

Common shapes in this KG:

- Datasets: ds:* a dcat:Dataset ; dcterms:title ?t ; dcterms:description ?d ;
  app:describesEntity <var:concept-iri> .
- Platforms: platform:* a sosa:Platform ; rdfs:label ?l ;
  app:hasDeploymentLocation feature:* .
- SKOS hierarchy: ?narrow skos:broader ?broad . Use SKOS for variable /
  instrument-type / platform-type / taxon questions.
- TBox property metadata: ?prop a owl:ObjectProperty / owl:DatatypeProperty ;
  rdfs:label ?l ; rdfs:comment ?c ; rdfs:domain ?d ; rdfs:range ?r .
- Deprecated terms: ?prop owl:deprecated true .

Match the question's intent: a definition question should not return
dataset rows, and a dataset-listing question should not project SKOS
labels of unrelated concepts.
"""


def _user_prompt(question: str, retrieved: list[dict[str, Any]]) -> str:
    lines = [f"Question: {question}", "", "Retrieved concept candidates (top 8 by score):"]
    if not retrieved:
        lines.append("- (no concepts retrieved — fall back to ABox structure)")
    else:
        for i, c in enumerate(retrieved):
            label = c.get("preferred_label") or c.get("compact_id") or c.get("iri")
            iri = c.get("iri")
            kind = c.get("kind")
            score = c.get("score")
            lines.append(
                f"- [{i}] iri={iri}  kind={kind}  label={label!r}  score={score:.3f}"
            )
    lines.append("")
    lines.append("Return only the JSON object with reasoning + sparql.")
    return "\n".join(lines)


# --- Per-question pipeline -------------------------------------------------


@dataclass
class QuestionResult:
    label: str
    question: str
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None
    sparql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    fuseki_status: str = "ok"


async def _retrieve(
    retriever: VectorizerOntologyRetriever, question: str
) -> list[dict[str, Any]]:
    """Run the live RAG retrieval and surface a JSON-friendly summary."""
    out = await retriever.retrieve(
        RetrievalQuery(question=question, mention=question, limit=8)
    )
    summary: list[dict[str, Any]] = []
    for c in out:
        meta = c.concept.metadata or {}
        summary.append(
            {
                "iri": c.concept.iri,
                "compact_id": c.concept.prefixed_name,
                "preferred_label": c.concept.label,
                "alt_labels": list(c.concept.aliases),
                "kind": meta.get("vectorizer_kind") or c.concept.kind,
                "score": float(c.score),
                "deprecated": bool(meta.get("deprecated")),
                "parents": list(meta.get("parents") or []),
            }
        )
    return summary


def _coerce_select_rows(result: SelectResult) -> list[dict[str, Any]]:
    """Flatten ``SolutionRow.bindings`` to plain JSON-able dicts.

    Strips out the type/datatype/lang detail since the report only shows
    raw values; the JSON sidecar keeps the structured form.
    """
    out: list[dict[str, Any]] = []
    for row in result.rows:
        out.append({k: v.value for k, v in row.bindings.items() if v is not None})
    return out


async def _run_one(
    item: tuple[str, str],
    *,
    retriever: VectorizerOntologyRetriever,
    chat: FoundryChatClient,
    endpoint: HttpSparqlEndpoint,
    timeout_ms: int,
) -> QuestionResult:
    label, question = item
    qr = QuestionResult(label=label, question=question)
    started = time.perf_counter()

    # 1) Retrieval through the new VectorizerOntologyRetriever.
    try:
        qr.retrieved = await _retrieve(retriever, question)
    except RetrievalError as exc:
        qr.error = f"retrieval failed: {exc}"
        qr.duration_ms = (time.perf_counter() - started) * 1000.0
        return qr

    # 2) LLM plan grounded in retrieved concepts.
    try:
        raw = await chat.complete(
            system=_SYSTEM_PROMPT,
            user=_user_prompt(question, qr.retrieved),
        )
    except Exception as exc:  # noqa: BLE001
        qr.error = f"LLM call failed: {exc}"
        qr.duration_ms = (time.perf_counter() - started) * 1000.0
        return qr
    try:
        plan_obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        qr.error = f"LLM JSON parse failed: {exc}; raw={raw[:300]!r}"
        qr.duration_ms = (time.perf_counter() - started) * 1000.0
        return qr
    qr.reasoning = (plan_obj.get("reasoning") or "").strip() or None
    sparql = (plan_obj.get("sparql") or "").strip()
    if not sparql:
        qr.error = "LLM returned empty 'sparql' field"
        qr.duration_ms = (time.perf_counter() - started) * 1000.0
        return qr
    qr.sparql = sparql

    # 3) Execute against Fuseki.
    try:
        result = await endpoint.query(
            sparql, query_type="select", timeout_ms=timeout_ms, max_rows=25
        )
    except EndpointError as exc:
        qr.error = f"Fuseki rejected the query: {exc}"
        qr.fuseki_status = f"error ({exc.status})" if exc.status else "error"
        qr.duration_ms = (time.perf_counter() - started) * 1000.0
        return qr

    if isinstance(result, SelectResult):
        qr.rows = _coerce_select_rows(result)
        qr.row_count = len(qr.rows)
    elif isinstance(result, AskResult | ConstructResult):  # pragma: no cover
        qr.rows = []
        qr.row_count = 0
        qr.fuseki_status = "non-select result"
    qr.duration_ms = (time.perf_counter() - started) * 1000.0
    return qr


# --- Report writer ---------------------------------------------------------


def _render_report(
    results: list[QuestionResult],
    *,
    endpoint_url: str,
    qdrant_url: str,
    qdrant_collection: str,
    foundry_model: str,
) -> str:
    total = len(results)
    ok = sum(1 for r in results if r.error is None and r.row_count > 0)
    rendered = sum(1 for r in results if r.sparql)
    executed = sum(1 for r in results if r.fuseki_status == "ok" and r.error is None)

    lines: list[str] = []
    lines.append("# RAG smoke against the live ocean stack")
    lines.append("")
    lines.append("Each question flows: vectorizer concept retrieval (live "
                 "Foundry + Qdrant) → Foundry LLM plan → Fuseki execute.")
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append(f"- **Fuseki endpoint**: `{endpoint_url}`")
    lines.append(f"- **Qdrant**: `{qdrant_url}` collection `{qdrant_collection}`")
    lines.append(f"- **LLM**: Foundry `{foundry_model}` (OpenAI-compatible)")
    lines.append(f"- **Questions**: {total}")
    lines.append(f"- **SPARQL drafted**: {rendered}/{total}")
    lines.append(f"- **Executed without endpoint error**: {executed}/{total}")
    lines.append(f"- **Returned ≥ 1 row**: {ok}/{total}")
    lines.append("")

    for i, r in enumerate(results, start=1):
        lines.append(f"## {i}. {r.label}")
        lines.append("")
        lines.append(f"**Question**: {r.question}")
        lines.append("")
        if r.duration_ms:
            lines.append(f"_Total wall time: {r.duration_ms:.0f} ms._")
            lines.append("")

        # Retrieval section
        lines.append("**Retrieved concepts (top 5):**")
        lines.append("")
        if not r.retrieved:
            lines.append("- (none)")
        else:
            for c in r.retrieved[:5]:
                label = c.get("preferred_label") or c.get("compact_id") or c.get("iri")
                lines.append(
                    f"- `{c.get('compact_id') or c.get('iri')}` "
                    f"({c.get('kind')}, score={c.get('score'):.3f}) — {label!r}"
                )
        lines.append("")

        # Reasoning + SPARQL
        if r.reasoning:
            lines.append(f"**Planner reasoning**: {r.reasoning}")
            lines.append("")
        if r.sparql:
            lines.append("**Generated SPARQL:**")
            lines.append("")
            lines.append("```sparql")
            lines.append(r.sparql.strip())
            lines.append("```")
            lines.append("")

        # Results
        if r.error:
            lines.append(f"**ERROR**: {r.error}")
            lines.append("")
            continue
        lines.append(f"**Fuseki rows ({r.row_count}):**")
        lines.append("")
        if not r.rows:
            lines.append("_(no rows)_")
        else:
            cols = list({k for row in r.rows for k in row.keys()})
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in r.rows[:10]:
                cells = [str(row.get(k, "")).replace("|", "\\|") for k in cols]
                lines.append("| " + " | ".join(cells) + " |")
            if len(r.rows) > 10:
                lines.append(f"_(+ {len(r.rows) - 10} more rows)_")
        lines.append("")

    return "\n".join(lines)


# --- Driver ---------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    foundry_base = os.environ.get("FOUNDRY_BASE_URL") or os.environ.get(
        "FOUNDRY_API_BASE_URL"
    )
    foundry_key = os.environ.get("FOUNDRY_API_KEY") or os.environ.get(
        "FOUNDRY_API_TOKEN"
    )
    foundry_model = os.environ.get("FOUNDRY_LLM_MODEL", "gpt-4.1-mini")
    if not foundry_base or not foundry_key:
        print("FOUNDRY_BASE_URL and FOUNDRY_API_KEY must be set "
              "(source ~/project/ontology_vectorizer/.env)", file=sys.stderr)
        return 2

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    qdrant_collection = os.environ.get("QDRANT_COLLECTION_NAME", "ontology_concepts")

    chat = FoundryChatClient(
        base_url=foundry_base, api_key=foundry_key, model=foundry_model
    )
    retriever = VectorizerOntologyRetriever.from_env(
        ontology_id=os.environ.get(
            "ONTOLOGY_VECTORIZER_DEFAULT_ONTOLOGY_ID", "ocean-demo"
        )
    )
    endpoint = HttpSparqlEndpoint(args.endpoint_url)

    results: list[QuestionResult] = []
    try:
        for i, item in enumerate(QUESTIONS, start=1):
            print(f"[{i:02d}/{len(QUESTIONS)}] {item[1][:80]}", flush=True)
            qr = await _run_one(
                item,
                retriever=retriever,
                chat=chat,
                endpoint=endpoint,
                timeout_ms=args.timeout_ms,
            )
            print(
                f"    rows={qr.row_count} "
                f"sparql={'yes' if qr.sparql else 'no'} "
                f"error={qr.error or 'none'} "
                f"({qr.duration_ms:.0f}ms)",
                flush=True,
            )
            results.append(qr)
    finally:
        await endpoint.aclose()

    md = _render_report(
        results,
        endpoint_url=args.endpoint_url,
        qdrant_url=qdrant_url,
        qdrant_collection=qdrant_collection,
        foundry_model=foundry_model,
    )
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(md)
    print(f"\nWrote {args.report}", flush=True)

    # Also dump structured JSON for later analysis.
    json_path = Path(args.report).with_suffix(".json")
    json_path.write_text(
        json.dumps(
            [
                {
                    "label": r.label,
                    "question": r.question,
                    "retrieved": r.retrieved,
                    "reasoning": r.reasoning,
                    "sparql": r.sparql,
                    "rows": r.rows,
                    "row_count": r.row_count,
                    "fuseki_status": r.fuseki_status,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
                for r in results
            ],
            indent=2,
        )
    )
    print(f"Wrote {json_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_rag_question_smoke")
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get(
            "GRAPH_MCP_ENDPOINT_URL", "http://localhost:3030/ocean/sparql"
        ),
    )
    parser.add_argument(
        "--report",
        default=str(REPO / "reports" / "rag-20q-smoke" / "report.md"),
    )
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    return asyncio.run(_amain(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
