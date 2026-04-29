"""Retrieval-augmented evaluation harness for the planner pipeline.

This package extends the production planner with an ontology-concept
retrieval step (Qdrant + optional re-ranker) and a parallel runner that
scores RAG-enhanced planning against the same golden cases used by the
non-RAG ``evals/`` harness.

The core production contract is unchanged: the LLM must still produce a
strict :class:`graph_mcp.models.QueryPlan` IR, never raw SPARQL. Retrieval
adds a *candidate pack* the LLM can lean on; it does not bypass validation,
rendering, execution, or scoring.

Public surface:

- :mod:`evals_rag.models` — retrieval / re-ranking Pydantic models.
- :mod:`evals_rag.retrieval` — :class:`OntologyRetriever` protocol plus
  mock and Qdrant-backed implementations.
- :mod:`evals_rag.reranking` — re-ranker protocol with no-op + heuristic
  implementations.
- :mod:`evals_rag.planner` — RAG planner that wraps the existing planner
  workflow.
- :mod:`evals_rag.runner` — CLI entry point analogous to ``evals.runner``.
- :mod:`evals_rag.metrics` — retrieval-specific aggregate metrics.
- :mod:`evals_rag.report` — markdown report rendering.
"""

from __future__ import annotations
