# `evals_rag`

Retrieval-augmented evaluation harness for the planner pipeline.

This subproject extends `evals/` with an ontology-concept retrieval step
(Qdrant + optional re-ranker) and a parallel runner that scores
RAG-enhanced planning against the same golden cases used by the non-RAG
harness. The production MCP server's execution path is unchanged: the LLM
still produces a strict `QueryPlan` IR, and the validator / renderer /
executor are reused as-is.

## How it relates to `evals/`

```
evals/                                evals_rag/
└─ runner.py (Planner → workflow)     └─ runner.py (RagPlanner → workflow)
└─ agent.py  (workflow)         ←──── reused; now accepts supplemental_candidates
└─ models.py (PlanGenerationOutput)   reused unchanged
└─ metrics.py                         └─ metrics.py (RAG-specific + base)
└─ planner_prompt.py            ←──── prompts.py (RAG_GUIDANCE appended)
```

The RAG planner reuses `evals.agent.run_planner_workflow`. Selected
retrieval candidates are **promoted** into the workflow's authoritative
resolved-term block via the new `supplemental_candidates` argument, so
the LLM sees them in the same place it sees deterministic resolutions.

## Architecture

```
natural-language question
  → mention extraction (evals.mention_extractor)
  → ontology concept retrieval (mock | Qdrant)
  → deduplication by IRI (lineage merged across mentions)
  → re-ranking (noop | heuristic; question-aware via RerankContext)
  → score-eligible selected concepts → TermCandidate promotion
  → run_planner_workflow with supplemental_candidates
  → LLM planner (PydanticAI agent OR deterministic stub)
  → QueryPlan IR
  → existing validator / renderer / executor
  → existing semantic eval scoring + RAG metrics
```

The retrieve / re-rank cycle is async; the planner workflow is sync. The
wrapper bridges between them safely (it runs a fresh event loop in a
worker thread when called from inside an existing loop).

## Promotion: how RAG candidates become first-class resolved terms

The deterministic `TermResolver` runs first and produces the *baseline*
selected terms. The RAG cycle runs in parallel and produces a list of
selected concepts. Each selected concept is converted to a
`TermCandidate` and passed to `run_planner_workflow` as
`supplemental_candidates`. The workflow merges the two lists with these
rules:

- **Dedupe by IRI.** When a candidate's IRI already appears in the
  baseline, the baseline entry wins (deterministic resolution stays
  authoritative). The RAG entry is recorded in
  `PlannerDiagnostics.rag_selected_terms` for lineage.
- **Promote new IRIs.** When a candidate's IRI is new, it is appended to
  `selected_terms` and the originating mention is removed from
  `unresolved_mentions`. The candidate appears in the prompt's
  Resolved-terms block on equal footing with deterministic entries.
- **Respect ambiguity.** If the baseline marked a mention ambiguous, RAG
  cannot break the tie silently. The mention stays ambiguous so the
  planner can ask for clarification.
- **Score threshold.** Candidates below
  `GRAPH_MCP_RAG_SCORE_THRESHOLD` are not promoted.
- **Kind compatibility.** Candidates whose `kind` conflicts with the
  originating mention's `expected_kinds` are not promoted (e.g. a
  property mention does not pick up an individual that happens to share
  the local name).

The report shows three groups separately: *baseline resolved terms*,
*RAG-promoted terms*, and post-merge *unresolved mentions*.

## Deduplication

When the same concept is retrieved for multiple mentions, the planner
collapses the duplicates into a single `RetrievedConcept` whose
`metadata["rag_mentions"]` lists every originating mention. The prompt
shows the multi-mention lineage:

```
ex:Person | kind=class | score=0.98 | label='Person' | mentions=people,person
```

After re-ranking, the deduper runs again (so a reranker that returns
duplicates cannot waste prompt budget).

## Running

### Mock retriever (no network)

```bash
python -m evals_rag.runner \
  --planner rag \
  --cases evals/golden_cases.yaml \
  --retriever mock \
  --reranker heuristic \
  --report-dir reports/rag-mock
```

The mock retriever derives `OntologyConcept` instances from the live
schema snapshot via `evals_rag.fixtures.concepts_from_snapshot`, so it
exercises the full retrieve / re-rank / promotion / planner cycle without
touching a vector database.

### Qdrant retriever (smoke test with fake embeddings)

```bash
python -m evals_rag.runner \
  --planner rag \
  --cases evals/golden_cases.yaml \
  --retriever qdrant \
  --embedding-provider fake \
  --reranker heuristic \
  --report-dir reports/rag-qdrant-smoke \
  --no-execute
```

The CLI **fails fast** when `--retriever qdrant` is supplied without
`--embedding-provider`. The `fake` provider lets you smoke-test the
runner / report wiring before the real vectorizer lands. Per-case Qdrant
errors are recorded as retrieval diagnostics rather than crashing the
run.

### Real Qdrant retriever (requires the `rag` extra and a vectorizer)

```bash
pip install -e ".[rag]"

# Once the ontology vectorizer lands and a real EmbeddingProvider exists:
python -m evals_rag.runner \
  --planner rag \
  --cases evals/golden_cases.yaml \
  --retriever qdrant \
  --embedding-provider <real>  # not 'fake' / 'missing'
  --reranker heuristic \
  --report-dir reports/rag-qdrant
```

### Live SPARQL endpoint (Fuseki / etc.)

```bash
export GRAPH_MCP_ENDPOINT_URL="http://localhost:3030/ocean/sparql"
# Optional Basic Auth — the password is read from the named env var only.
export FUSEKI_ADMIN_USER="admin"
export FUSEKI_ADMIN_PASSWORD="…"

python -m evals_rag.runner \
  --planner rag \
  --retriever mock \
  --reranker heuristic \
  --graph-source sparql \
  --endpoint-url "$GRAPH_MCP_ENDPOINT_URL" \
  --endpoint-user "$FUSEKI_ADMIN_USER" \
  --endpoint-password-env FUSEKI_ADMIN_PASSWORD \
  --cases evals_rag/ocean_golden_cases.yaml \
  --report-dir reports/ocean-fuseki-smoke
```

When `--graph-source sparql` is used:

- `--endpoint-url` (or `GRAPH_MCP_ENDPOINT_URL`) is required.
- `--sparql-update-url` (or `GRAPH_MCP_SPARQL_UPDATE_URL`) is recorded in
  the report; the eval runner itself never issues updates.
- Schema discovery runs against the live endpoint with a base-prefix
  block that includes the ocean vocabulary (dcat / dcterms / geo / prov /
  sosa / app / var) so the LLM and the validator both see them. These
  prefixes are advertised, not added to the validator's protected
  default-prefix override list — plans are still free to declare them
  themselves.
- `--endpoint-user` + `--endpoint-password-env` enable Basic Auth. The
  password itself is **never** read from a CLI flag.

Two convenience scripts wrap the most common workflows:

```bash
# 1. Pure SPARQL smoke — confirms the live KG actually answers the raw
#    queries. No LLM, no eval harness. Always safe in CI.
python scripts/run_ocean_fuseki_smoke.py

# 2. Free-text RAG smoke — discovers schema, builds candidate packs
#    from the live snapshot, runs the planner workflow, and writes a
#    full RAG report. **Requires an LLM** because the deterministic
#    baseline planner only knows the small ex: fixture vocabulary; it
#    cannot answer dcat / sosa / geo questions.
python scripts/run_ocean_rag_smoke.py --azure --model "$AZURE_OPENAI_MODEL"

# 2b. Plumbing-only mode (cases will FAIL the structural eval; the
#     intent is to verify connectivity, schema discovery, dedup, and
#     report wiring against a live endpoint without an LLM key).
python scripts/run_ocean_rag_smoke.py --allow-deterministic-plumbing-smoke
```

### Lifecycle and credential handling

- The runner (`evals_rag.runner.main`) drives all async work — including
  schema discovery, retrieval, and execution — inside a single
  `asyncio.run` call. The HTTP endpoint is created once, used, and
  closed in the same event loop, so reusing an `httpx.AsyncClient`
  across loops is impossible.
- `components.endpoint.aclose()` is called in a `finally` block so the
  HTTP client is released even when the planner step raises.
- Endpoint URLs are written to reports via `safe_endpoint_repr`, which
  drops any embedded `userinfo` and query string. A URL such as
  `http://admin:secret@host:3030/sparql` is recorded as
  `http://host:3030/sparql`. The Basic Auth password is read only from
  the environment variable named by `--endpoint-password-env` (default
  `FUSEKI_ADMIN_PASSWORD`); it is never on the CLI and never echoed.

### Live integration tests

`tests/evals_rag/test_ocean_fuseki_integration.py` runs raw-SPARQL +
schema-discovery checks against a live endpoint. Skipped by default;
enable via either env var:

```bash
export RUN_FUSEKI_INTEGRATION=1
# or
export GRAPH_MCP_ENDPOINT_URL=http://localhost:3030/ocean/sparql
python -m pytest tests/evals_rag/test_ocean_fuseki_integration.py
```

CI does not depend on Fuseki being available.

### Quality-gated CI

```bash
python -m evals_rag.runner \
  --planner rag \
  --cases evals/golden_cases.yaml \
  --retriever mock \
  --reranker heuristic \
  --report-dir reports/rag-gated \
  --min-case-pass-rate 0.95 \
  --min-selected-case-recall 0.95 \
  --min-retrieval-case-recall-at-k 0.95 \
  --max-unresolved-mention-rate 0.05 \
  --max-safety-violations 0 \
  --fail-below-threshold
```

Or use the wrapper script `scripts/run_rag_eval_gate.py`.

Without `--fail-below-threshold` the runner reports thresholds in
`metrics.json` / `report.md` but still exits `0` even if cases fail —
exploration mode rather than CI gate.

### Compare against a baseline

```bash
python -m evals_rag.runner \
  --compare-baseline \
  --baseline-report reports/live-golden/metrics.json \
  --report-dir reports/rag-qdrant
```

`*_delta_vs_baseline` keys are appended to `metrics.json`.

## Configuration

Environment variables (loaded via `evals_rag.config.RagSettings`,
parsed strictly — invalid values raise `RagConfigError`):

| Variable                              | Default              | Notes |
| ------------------------------------- | -------------------- | ----- |
| `GRAPH_MCP_RAG_QDRANT_URL`            | `http://localhost:6333` | Qdrant host. |
| `GRAPH_MCP_RAG_QDRANT_API_KEY`        | _(unset)_            | Optional. |
| `GRAPH_MCP_RAG_QDRANT_COLLECTION`     | `ontology_concepts`  | Collection to search. |
| `GRAPH_MCP_RAG_RETRIEVAL_LIMIT`       | `20`                 | Per-query top-K. Must be > 0. |
| `GRAPH_MCP_RAG_SELECTED_LIMIT`        | `8`                  | Top-N injected into the prompt. Must be > 0 and ≤ retrieval limit. |
| `GRAPH_MCP_RAG_SCORE_THRESHOLD`       | `0.0`                | Drop concepts below this. Must be ≥ 0. |
| `GRAPH_MCP_RAG_USE_RERANKER`          | `true`               | Toggle the heuristic reranker. |

## Metrics

### Strengthened RAG metrics

- `retrieval_concept_recall_at_k` — expected IRIs recalled / expected IRIs
  total in the retrieved top-K.
- `retrieval_case_recall_at_k` — fraction of cases where every expected
  IRI is in the retrieved top-K.
- `selected_concept_recall` — expected IRIs in the *selected* set /
  expected IRIs total.
- `selected_case_recall` — fraction of cases where every expected IRI
  ends up selected.
- `selected_precision` — expected IRIs in the selected set / total
  selected IRIs.
- `mean_selected_candidates`, `mean_retrieved_candidates`.
- `reranker_promotion_rate` — fraction of cases where re-ranking
  promoted at least one expected concept that was not in the retrieval
  top-K.
- `reranker_demotion_error_rate` — fraction of cases where re-ranking
  removed an expected concept that *was* in the retrieval top-K.
- `unresolved_mention_rate`, `concept_ambiguity_rate`,
  `empty_selection_rate`, `retrieval_error_rate`.
- `*_delta_vs_baseline` — emitted when `--compare-baseline` is used.

### Deprecated aliases

These keys are still emitted so older dashboards do not break:

- `retrieval_recall_at_k` ⇒ alias for `retrieval_concept_recall_at_k`.
- `selected_concept_accuracy` ⇒ alias for `selected_concept_recall`.
- `reranker_improvement_rate` ⇒ alias for `reranker_promotion_rate`.

## What is not implemented yet

- **The ontology vectorizer.** `MissingEmbeddingProvider` raises
  `MissingEmbeddingProviderError` (a `RetrievalError` subclass) so the
  runner records the failure as a retrieval diagnostic instead of
  crashing. The CLI **rejects** `--retriever qdrant` without an
  `--embedding-provider` so the smoke path is always intentional.
- **`ModelReranker`.** The class is kept as an internal placeholder; the
  CLI rejects `--reranker model` until a real cross-encoder / LLM scorer
  is wired up.

The vectorizer should:

1. Read `OntologyConcept` instances (from a curated catalog or from
   `evals_rag.fixtures.concepts_from_snapshot`).
2. Embed each concept's label / aliases / description / domain / range
   into a fixed-dimension vector.
3. Upsert them into the Qdrant collection named by
   `GRAPH_MCP_RAG_QDRANT_COLLECTION`, with the payload shape
   consumed by `_concept_from_payload` in `retrieval.py`:
   ```json
   {
     "iri": "http://example.org/worksFor",
     "prefixed_name": "ex:worksFor",
     "label": "works for",
     "aliases": ["employed by"],
     "kind": "property",
     "description": "...",
     "domain": ["http://example.org/Person"],
     "range": ["http://example.org/Company"],
     "examples": [],
     "source": "..."
   }
   ```
4. Provide an `EmbeddingProvider.embed_query` implementation that uses
   the same model as the indexer.

## Tests

Tests live under `tests/evals_rag/` and cover models, retrievers
(including a faked Qdrant client), reranker (with `RerankContext`),
planner integration, runner end-to-end, quality gates, candidate
promotion, deduplication, embedding errors, config validation, and
prompt contract. They never touch the network and never require an LLM
key.

```bash
cd /tmp && PYTHONPATH=src:. .venv/bin/python -m pytest tests/evals_rag -q
```

(Run from `/tmp` to sidestep the project `.env` issue documented in the
parent repo's eval workflow notes.)
