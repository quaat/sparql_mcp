"""Aggregate metrics specific to the RAG eval.

The base eval metrics (:mod:`evals.metrics`) are reused unchanged and
merged into the RAG report so the same case-pass-rate / valid-plan-rate
language is comparable across runs. This module adds retrieval-side
metrics with two granularities:

- *concept-level* metrics divide expected-IRI counts: useful when cases
  carry multiple expected IRIs.
- *case-level* metrics ask "did this case pass?" — i.e. were all expected
  IRIs present.

Old keys (``retrieval_recall_at_k``, ``selected_concept_accuracy``,
``reranker_improvement_rate``) are retained as aliases for backwards
compatibility but are deprecated; new code should use the strengthened
keys (``retrieval_concept_recall_at_k`` etc.).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from evals.metrics import compute_metrics
from evals.models import CaseResult, GoldenCase
from evals_rag.models import RagPlannerDiagnostics


@dataclass
class RagCaseResult:
    """Pairing of a case result with its RAG-specific diagnostics."""

    case: GoldenCase
    result: CaseResult
    rag_diagnostics: RagPlannerDiagnostics


def compute_rag_metrics(
    rag_results: Iterable[RagCaseResult],
    *,
    baseline_metrics: dict[str, float] | None = None,
    k: int = 8,
) -> dict[str, float]:
    """Combine base eval metrics with RAG-specific aggregates."""
    rag_results = list(rag_results)
    base = compute_metrics([r.result for r in rag_results])

    expected_iris_total = 0
    expected_iris_recalled_topk = 0
    expected_iris_in_selected = 0
    expected_iris_in_retrieved = 0
    cases_with_full_topk_recall = 0
    cases_with_full_selected_recall = 0
    cases_with_unresolved = 0
    cases_with_ambiguity = 0
    cases_with_empty_selection = 0
    cases_with_retrieval_errors = 0
    rerank_eligible = 0
    rerank_promoted = 0
    rerank_demoted = 0
    selected_total = 0
    selected_correct = 0
    retrieved_count_sum = 0
    selected_count_sum = 0
    cases_with_expected = 0

    for entry in rag_results:
        diag = entry.rag_diagnostics
        retrieved_count_sum += len(diag.retrieved_concepts)
        selected_count_sum += len(diag.selected_concepts)
        if diag.unresolved_mentions:
            cases_with_unresolved += 1
        if _has_ambiguous_selection(diag):
            cases_with_ambiguity += 1
        if not diag.selected_concepts and diag.retrieved_concepts:
            cases_with_empty_selection += 1
        if diag.retrieval_errors:
            cases_with_retrieval_errors += 1

        expected = _expected_iris_normalized(entry.case)
        retrieved_iris = _iri_set(diag.retrieved_concepts)
        topk_retrieved = _topk_iris(diag.retrieved_concepts, k)
        topk_reranked = _topk_iris(diag.reranked_concepts, k)
        selected_iris = _iri_set(diag.selected_concepts)

        # Selection precision applies even when the case has no expected
        # IRIs: any selected concept counts toward the denominator, but
        # the numerator is "selected ∧ in expected".
        selected_total += len(selected_iris)
        if expected:
            cases_with_expected += 1
            selected_correct += len(selected_iris & expected)
            for iri in expected:
                expected_iris_total += 1
                if iri in retrieved_iris:
                    expected_iris_in_retrieved += 1
                if iri in topk_retrieved:
                    expected_iris_recalled_topk += 1
                if iri in selected_iris:
                    expected_iris_in_selected += 1
            if expected.issubset(topk_retrieved):
                cases_with_full_topk_recall += 1
            if expected.issubset(selected_iris):
                cases_with_full_selected_recall += 1
            promoted = (expected & topk_reranked) - (expected & topk_retrieved)
            demoted = (expected & topk_retrieved) - (expected & topk_reranked)
            if expected & topk_retrieved or expected & topk_reranked:
                rerank_eligible += 1
            if promoted:
                rerank_promoted += 1
            if demoted:
                rerank_demoted += 1

    n_cases = max(1, len(rag_results))
    n_with_expected = max(1, cases_with_expected)
    rag_metrics: dict[str, float] = {
        f"retrieval_concept_recall_at_{k}": (
            (expected_iris_recalled_topk / expected_iris_total) if expected_iris_total else 1.0
        ),
        f"retrieval_case_recall_at_{k}": (
            cases_with_full_topk_recall / n_with_expected if cases_with_expected else 1.0
        ),
        "selected_concept_recall": (
            (expected_iris_in_selected / expected_iris_total) if expected_iris_total else 1.0
        ),
        "selected_case_recall": (
            cases_with_full_selected_recall / n_with_expected if cases_with_expected else 1.0
        ),
        "selected_precision": ((selected_correct / selected_total) if selected_total else 1.0),
        "mean_selected_candidates": selected_count_sum / n_cases,
        "mean_retrieved_candidates": retrieved_count_sum / n_cases,
        "unresolved_mention_rate": cases_with_unresolved / n_cases,
        "concept_ambiguity_rate": cases_with_ambiguity / n_cases,
        "empty_selection_rate": cases_with_empty_selection / n_cases,
        "retrieval_error_rate": cases_with_retrieval_errors / n_cases,
        "reranker_promotion_rate": (
            (rerank_promoted / rerank_eligible) if rerank_eligible else 0.0
        ),
        "reranker_demotion_error_rate": (
            (rerank_demoted / rerank_eligible) if rerank_eligible else 0.0
        ),
        "planner_case_pass_rate": base.get("case_pass_rate", 0.0),
    }
    # Deprecated aliases — kept so existing dashboards don't break.
    rag_metrics[f"retrieval_recall_at_{k}"] = rag_metrics[f"retrieval_concept_recall_at_{k}"]
    rag_metrics["selected_concept_accuracy"] = rag_metrics["selected_concept_recall"]
    rag_metrics["reranker_improvement_rate"] = rag_metrics["reranker_promotion_rate"]
    base.update(rag_metrics)

    if baseline_metrics:
        base.update(_baseline_deltas(base, baseline_metrics))
    return base


def _baseline_deltas(current: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    """Return a small subset of useful deltas vs ``baseline``."""
    keys = (
        "case_pass_rate",
        "valid_plan_rate",
        "term_resolution_accuracy",
        "execution_success_rate",
        "result_binding_accuracy",
    )
    out: dict[str, float] = {}
    for key in keys:
        if key in current and key in baseline:
            out[f"{key}_delta_vs_baseline"] = current[key] - baseline[key]
    if "case_pass_rate" in current and "case_pass_rate" in baseline:
        out["planner_case_pass_delta_vs_baseline"] = (
            current["case_pass_rate"] - baseline["case_pass_rate"]
        )
    if "term_resolution_accuracy" in current and "term_resolution_accuracy" in baseline:
        out["term_resolution_delta_vs_baseline"] = (
            current["term_resolution_accuracy"] - baseline["term_resolution_accuracy"]
        )
    return out


def _expected_iris_normalized(case: GoldenCase) -> set[str]:
    """Return the expected concept IRI set for a case, prefix-expanded."""
    iris: set[str] = set()
    for term in case.expected.required_terms:
        if term.startswith(("?", "$")) or term.startswith(("LANG", "DATATYPE")):
            continue
        if term.startswith(("http://", "https://")) or ":" in term:
            iris.add(term)
    for spec in case.expected.required_triples:
        for slot in (spec.subject, spec.predicate, spec.object):
            if slot.startswith("?_") or slot.startswith(("?", "$")):
                continue
            iris.add(slot)
    for pp in case.expected.required_property_paths:
        for slot in (pp.subject, pp.predicate, pp.object):
            if slot.startswith("?_") or slot.startswith(("?", "$")):
                continue
            iris.add(slot)
    return {_expand(i) for i in iris}


def _iri_set(items: list) -> set[str]:
    out: set[str] = set()
    for item in items:
        out.add(_expand(item.concept.iri))
        if item.concept.prefixed_name:
            out.add(_expand(item.concept.prefixed_name))
    return out


def _topk_iris(items: list, k: int) -> set[str]:
    """Return the IRI set of the first ``k`` items (retrieved or reranked)."""
    out: set[str] = set()
    for item in items[:k]:
        out.add(_expand(item.concept.iri))
        if item.concept.prefixed_name:
            out.add(_expand(item.concept.prefixed_name))
    return out


_BUILTIN_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "ex": "http://example.org/",
}


def _expand(term: str) -> str:
    """Expand a ``prefix:local`` term to an absolute IRI when possible."""
    if not term:
        return term
    if term.startswith(("http://", "https://", "?", "$")):
        return term
    if ":" not in term:
        return term
    prefix, _, local = term.partition(":")
    base = _BUILTIN_PREFIXES.get(prefix)
    if base is None:
        return term
    return f"{base}{local}"


def _has_ambiguous_selection(diag: RagPlannerDiagnostics) -> bool:
    """Detect ambiguity by spotting duplicate labels in the selected pool."""
    seen: dict[str, int] = {}
    for item in diag.selected_concepts:
        label = (item.concept.label or "").lower()
        if not label:
            continue
        seen[label] = seen.get(label, 0) + 1
    return any(count > 1 for count in seen.values())
