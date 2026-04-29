"""Tests for RAG-specific aggregate metrics."""

from __future__ import annotations

from evals.models import CaseResult, GoldenCase, GoldenCaseExpected, TripleSpec
from evals_rag.metrics import RagCaseResult, compute_rag_metrics
from evals_rag.models import (
    OntologyConcept,
    RagPlannerDiagnostics,
    RerankedConcept,
    RetrievedConcept,
)


def _concept(iri: str, *, label: str | None = None, kind: str = "class") -> OntologyConcept:
    return OntologyConcept(
        iri=iri,
        prefixed_name=None,
        label=label,
        kind=kind,
    )


def _retrieved(iri: str, score: float = 0.5, rank: int = 0) -> RetrievedConcept:
    return RetrievedConcept(
        concept=_concept(iri),
        score=score,
        retrieval_rank=rank,
        retrieval_source="mock",
    )


def _reranked(iri: str, final: float = 0.5, rank: int = 0) -> RerankedConcept:
    return RerankedConcept(
        concept=_concept(iri),
        retrieval_score=final,
        rerank_score=0.0,
        final_score=final,
        rank=rank,
    )


def _case(case_id: str, *, required_terms: list[str] | None = None) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        question="?",
        expected=GoldenCaseExpected(
            required_terms=list(required_terms or []),
            required_triples=[
                TripleSpec(subject="?_", predicate="ex:worksFor", object="ex:Acme"),
            ]
            if required_terms is None
            else [],
        ),
    )


def _result(case_id: str, *, failed: bool = False) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        question="?",
        plan_generated=True,
        plan_valid=True,
        failures=["FAKE_FAIL"] if failed else [],
    )


def test_metrics_recall_and_selected_accuracy():
    case = _case("c1")  # required_triples references ex:worksFor and ex:Acme
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[
            _retrieved("ex:worksFor", score=0.9),
            _retrieved("ex:Acme", score=0.8, rank=1),
            _retrieved("ex:other", score=0.2, rank=2),
        ],
        reranked_concepts=[
            _reranked("ex:worksFor", final=0.95),
            _reranked("ex:Acme", final=0.85, rank=1),
        ],
        selected_concepts=[
            _reranked("ex:worksFor", final=0.95),
            _reranked("ex:Acme", final=0.85, rank=1),
        ],
    )
    rag_results = [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    metrics = compute_rag_metrics(rag_results, k=3)
    assert metrics["retrieval_recall_at_3"] == 1.0
    assert metrics["selected_concept_accuracy"] == 1.0
    assert metrics["unresolved_mention_rate"] == 0.0


def test_metrics_unresolved_mentions_counted():
    case = _case("c1", required_terms=["ex:worksFor"])
    diag = RagPlannerDiagnostics(
        unresolved_mentions=["acme"],
    )
    rag_results = [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    metrics = compute_rag_metrics(rag_results)
    assert metrics["unresolved_mention_rate"] == 1.0


def test_metrics_baseline_deltas():
    case = _case("c1", required_terms=["ex:worksFor"])
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[_retrieved("ex:worksFor", score=0.8)],
        reranked_concepts=[_reranked("ex:worksFor", final=0.9)],
        selected_concepts=[_reranked("ex:worksFor", final=0.9)],
    )
    rag_results = [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    metrics = compute_rag_metrics(
        rag_results,
        baseline_metrics={"case_pass_rate": 0.4, "term_resolution_accuracy": 0.6},
    )
    assert metrics["planner_case_pass_delta_vs_baseline"] == 1.0 - 0.4
    assert "term_resolution_delta_vs_baseline" in metrics


def test_reranker_improvement_rate():
    case = _case("c1", required_terms=["ex:worksFor"])
    diag = RagPlannerDiagnostics(
        # The expected concept is *not* in the retrieved top-K but appears
        # in the reranked top-K, simulating a successful promotion.
        retrieved_concepts=[
            _retrieved("ex:noise1", score=0.9, rank=0),
            _retrieved("ex:noise2", score=0.8, rank=1),
            _retrieved("ex:worksFor", score=0.3, rank=2),
        ],
        reranked_concepts=[
            _reranked("ex:worksFor", final=0.99, rank=0),
            _reranked("ex:noise1", final=0.5, rank=1),
        ],
        selected_concepts=[_reranked("ex:worksFor", final=0.99, rank=0)],
    )
    rag_results = [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    metrics = compute_rag_metrics(rag_results, k=2)
    assert metrics["reranker_improvement_rate"] == 1.0
