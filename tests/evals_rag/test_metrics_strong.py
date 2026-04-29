"""Tests for the strengthened RAG metrics (case-level recall, precision, etc.)."""

from __future__ import annotations

from evals.models import CaseResult, GoldenCase, GoldenCaseExpected, TripleSpec
from evals_rag.metrics import RagCaseResult, compute_rag_metrics
from evals_rag.models import (
    OntologyConcept,
    RagPlannerDiagnostics,
    RerankedConcept,
    RetrievedConcept,
)


def _concept(iri: str, *, kind: str = "class") -> OntologyConcept:
    return OntologyConcept(iri=iri, kind=kind)


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


def _case(case_id: str) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        question="?",
        expected=GoldenCaseExpected(
            required_triples=[
                TripleSpec(subject="?_", predicate="ex:worksFor", object="ex:Acme"),
            ]
        ),
    )


def _result(case_id: str, *, failed: bool = False) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        question="?",
        plan_generated=True,
        plan_valid=True,
        failures=["FAIL"] if failed else [],
    )


def test_retrieval_case_recall_requires_all_expected_iris():
    # Case 1 misses ex:Acme; case 2 has both.
    case1 = _case("c1")
    case2 = _case("c2")
    diag1 = RagPlannerDiagnostics(
        retrieved_concepts=[_retrieved("ex:worksFor")],
        reranked_concepts=[_reranked("ex:worksFor")],
        selected_concepts=[_reranked("ex:worksFor")],
    )
    diag2 = RagPlannerDiagnostics(
        retrieved_concepts=[_retrieved("ex:worksFor"), _retrieved("ex:Acme")],
        reranked_concepts=[_reranked("ex:worksFor"), _reranked("ex:Acme")],
        selected_concepts=[_reranked("ex:worksFor"), _reranked("ex:Acme")],
    )
    metrics = compute_rag_metrics(
        [
            RagCaseResult(case=case1, result=_result("c1"), rag_diagnostics=diag1),
            RagCaseResult(case=case2, result=_result("c2"), rag_diagnostics=diag2),
        ],
        k=8,
    )
    # Case 1 misses ex:Acme → not full-recall. Case 2 is full-recall.
    assert metrics["retrieval_case_recall_at_8"] == 0.5
    assert metrics["selected_case_recall"] == 0.5
    # Concept-level recall: 3 of 4 expected IRIs were retrieved.
    assert metrics["retrieval_concept_recall_at_8"] == 0.75


def test_selected_precision_penalizes_extra_wrong_concepts():
    case = _case("c1")
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[
            _retrieved("ex:worksFor"),
            _retrieved("ex:Acme"),
            _retrieved("ex:Other"),
        ],
        reranked_concepts=[
            _reranked("ex:worksFor"),
            _reranked("ex:Acme"),
            _reranked("ex:Other"),
        ],
        selected_concepts=[
            _reranked("ex:worksFor"),
            _reranked("ex:Acme"),
            _reranked("ex:Other"),  # noise
        ],
    )
    metrics = compute_rag_metrics(
        [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    )
    # 2 of 3 selected IRIs are expected.
    assert abs(metrics["selected_precision"] - 2 / 3) < 1e-6


def test_reranker_promotion_rate_detects_concept_moved_up():
    case = _case("c1")
    # ex:worksFor missed top-K of retrieval but lands in top-K of rerank.
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[
            _retrieved("ex:noise1", score=0.9, rank=0),
            _retrieved("ex:noise2", score=0.8, rank=1),
            _retrieved("ex:worksFor", score=0.3, rank=2),
            _retrieved("ex:Acme", score=0.85, rank=3),
        ],
        reranked_concepts=[
            _reranked("ex:worksFor", final=0.99, rank=0),
            _reranked("ex:Acme", final=0.95, rank=1),
        ],
        selected_concepts=[_reranked("ex:worksFor", final=0.99, rank=0)],
    )
    metrics = compute_rag_metrics(
        [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)],
        k=2,
    )
    assert metrics["reranker_promotion_rate"] == 1.0


def test_reranker_demotion_error_rate_detects_concept_moved_down():
    case = _case("c1")
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[
            _retrieved("ex:worksFor", score=0.9, rank=0),
            _retrieved("ex:Acme", score=0.85, rank=1),
            _retrieved("ex:noise", score=0.4, rank=2),
        ],
        reranked_concepts=[
            _reranked("ex:noise", final=0.99, rank=0),
            _reranked("ex:Acme", final=0.5, rank=1),  # ex:worksFor demoted out
        ],
        selected_concepts=[_reranked("ex:noise", final=0.99, rank=0)],
    )
    metrics = compute_rag_metrics(
        [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)],
        k=2,
    )
    assert metrics["reranker_demotion_error_rate"] > 0


def test_metrics_are_stable_with_duplicate_retrievals():
    case = _case("c1")
    # Same IRI listed twice in retrieved (post-dedup safety check).
    diag = RagPlannerDiagnostics(
        retrieved_concepts=[
            _retrieved("ex:worksFor", score=0.9, rank=0),
            _retrieved("ex:Acme", score=0.85, rank=1),
        ],
        reranked_concepts=[
            _reranked("ex:worksFor"),
            _reranked("ex:Acme"),
        ],
        selected_concepts=[
            _reranked("ex:worksFor"),
            _reranked("ex:Acme"),
        ],
    )
    metrics = compute_rag_metrics(
        [RagCaseResult(case=case, result=_result("c1"), rag_diagnostics=diag)]
    )
    assert metrics["selected_concept_recall"] == 1.0
    assert metrics["selected_case_recall"] == 1.0
