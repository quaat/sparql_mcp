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
└─ agent.py  (workflow)         ←──── reused unchanged
└─ models.py (PlanGenerationOutput)   reused unchanged
└─ metrics.py                         └─ metrics.py (RAG-specific + base)
└─ planner_prompt.py            ←──── prompts.py (RAG_GUIDANCE appended)
```

The RAG planner reuses `evals.agent.run_planner_workflow` so extraction,
resolution, validation, and repair behave exactly as in the baseline. It
prepends a "Retrieved ontology candidates" block to every prompt the
underlying LLM sees.

## Architecture

```
natural-language question
  → mention extraction (evals.mention_extractor)
  → ontology concept retrieval (mock | Qdrant)
  → optional re-ranking (noop | heuristic | model placeholder)
  → ConceptCandidatePack
  → LLM planner (PydanticAI agent OR deterministic stub)
  → QueryPlan IR
  → existing validator / renderer / executor
  → existing semantic eval scoring + RAG metrics
```

The retrieve / re-rank cycle is async; the planner workflow is sync (the
existing API). The wrapper bridges between them safely (it runs a fresh
event loop in a worker thread when called from inside an existing loop).

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
exercises the full retrieve / re-rank / planner cycle without touching a
vector database.

### Qdrant retriever (requires the `rag` extra and a vectorizer)

```bash
pip install -e ".[rag]"

python -m evals_rag.runner \
  --planner rag \
  --cases evals/golden_cases.yaml \
  --retriever qdrant \
  --reranker heuristic \
  --report-dir reports/rag-qdrant
```

Qdrant mode currently fails closed because no `EmbeddingProvider` is
implemented yet (`MissingEmbeddingProvider` raises a clear error). Once
the vectorizer lands, callers will inject a real provider via
`build_retriever` (or by extending the runner CLI).

### Compare against a baseline

```bash
python -m evals_rag.runner \
  --compare-baseline \
  --baseline-report reports/live-golden/metrics.json \
  --report-dir reports/rag-qdrant
```

`*_delta_vs_baseline` keys are appended to `metrics.json`.

## Configuration

Environment variables (loaded via `evals_rag.config.RagSettings`):

| Variable                              | Default              | Notes |
| ------------------------------------- | -------------------- | ----- |
| `GRAPH_MCP_RAG_QDRANT_URL`            | `http://localhost:6333` | Qdrant host. |
| `GRAPH_MCP_RAG_QDRANT_API_KEY`        | _(unset)_            | Optional. |
| `GRAPH_MCP_RAG_QDRANT_COLLECTION`     | `ontology_concepts`  | Collection to search. |
| `GRAPH_MCP_RAG_RETRIEVAL_LIMIT`       | `20`                 | Per-query top-K. |
| `GRAPH_MCP_RAG_SELECTED_LIMIT`        | `8`                  | Top-N injected into the prompt. |
| `GRAPH_MCP_RAG_SCORE_THRESHOLD`       | `0.0`                | Drop concepts below this. |
| `GRAPH_MCP_RAG_USE_RERANKER`          | `true`               | Toggle the heuristic reranker. |

Tests instantiate `RagSettings(...)` directly so they never depend on the
process environment.

## Metrics

Aggregate metrics added by the RAG runner (in addition to the base
metrics from `evals.metrics`):

- `retrieval_recall_at_k` — fraction of expected concept IRIs returned in
  the top-K retrieval before re-ranking. `k` is the runner's selected
  limit (default 8); the metric key is suffixed (`retrieval_recall_at_8`).
- `selected_concept_accuracy` — fraction of expected concept IRIs that
  appear in the selected (post-rerank, top-N) set actually shown to the LLM.
- `reranker_improvement_rate` — fraction of cases where re-ranking
  promoted at least one expected concept that was not in the retrieved
  top-K. Useful for detecting when the heuristic is paying for itself.
- `unresolved_mention_rate` — fraction of cases where at least one
  extracted mention had no retrieval hit.
- `concept_ambiguity_rate` — fraction of cases whose selected pool
  contains duplicate labels.
- `planner_case_pass_rate` — alias for `case_pass_rate` to make
  comparisons against the baseline less ambiguous.
- `*_delta_vs_baseline` — emitted only when `--compare-baseline` is used.

## What is not implemented yet

- The ontology vectorizer. `evals_rag.retrieval` defines the
  `EmbeddingProvider` protocol and a `MissingEmbeddingProvider` sentinel
  that fails closed. The future vectorizer should:
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
- A real cross-encoder / LLM `ModelReranker`. The placeholder raises
  `NotImplementedError`; the heuristic reranker is the default.
- Multi-stage retrieval (BM25 + dense + reranker). The retriever
  protocol is single-stage; a hybrid retriever can be added by composing
  retrievers without changing the planner.

## Known limitations

- `retrieval_recall_at_k` and `selected_concept_accuracy` use the legacy
  `required_terms` plus IR-level `required_triples` slots to derive the
  expected IRI set. Cases that test pure pattern shapes (no concrete
  terms in their structural specs) report 1.0 for these metrics by
  construction.
- The mock retriever's scoring is token-overlap, not semantic; it is
  deliberately imperfect so tests can assert exact behaviour.
- The runner's `--planner rag` mode without `--azure` / `--model` falls
  back to the deterministic baseline planner. It still exercises the
  retrieval / re-rank / candidate-pack rendering pipeline so CI gates
  RAG plumbing without an LLM.

## Tests

Tests live under `tests/evals_rag/` and cover models, retriever
behaviour (including a faked Qdrant client), reranker boosts, planner
integration, runner end-to-end, and metrics deltas. They never touch
the network and never require an LLM key.

```bash
cd /tmp && PYTHONPATH=src:. .venv/bin/python -m pytest tests/evals_rag -q
```

(Run from `/tmp` to sidestep the project `.env` issue documented in the
parent repo's eval workflow notes.)
