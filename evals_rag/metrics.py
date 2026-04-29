"""Aggregate metrics specific to the RAG eval.

The base eval metrics (:mod:`evals.metrics`) are reused unchanged and
merged into the RAG report so the same case-pass-rate / valid-plan-rate
language is comparable across runs. This module adds retrieval-side
metrics:

- ``retrieval_recall_at_k`` — fraction of cases where every expected term
  IRI was returned within the retrieved set.
- ``selected_concept_accuracy`` — fraction of cases where every expected
  term IRI ended up in the *selected* (post-rerank, top-N) set.
- ``reranker_improvement_rate`` — fraction of cases where re-ranking
  promoted at least one expected concept that was not in the top-N before.
- ``unresolved_mention_rate`` / ``concept_ambiguity_rate`` — how often the
  retriever / reranker fail open or hand the planner ambiguous candidates.
- ``planner_case_pass_delta_vs_baseline`` — overall pass-rate delta
  against an optional baseline metrics file from the non-RAG runner.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from evals.metrics import compute_metrics
from evals.models import CaseResult, GoldenCase
from evals_rag.models import RagPlannerDiagnostics


@dataclass
class RagCaseResult:
    """Pairing of a case result with its RAG-specific diagnostics.

    The runner builds these so the metrics layer doesn't have to dig into
    ``CaseResult.generated_plan_json`` for retrieval data.
    """

    case: GoldenCase
    result: CaseResult
    rag_diagnostics: RagPlannerDiagnostics


def compute_rag_metrics(
    rag_results: Iterable[RagCaseResult],
    *,
    baseline_metrics: dict[str, float] | None = None,
    k: int = 8,
) -> dict[str, float]:
    """Combine base eval metrics with RAG-specific aggregates.

    ``baseline_metrics`` is the metrics dict from a non-RAG run loaded by
    the runner via ``--baseline-report``. When supplied, this function adds
    ``*_delta_vs_baseline`` keys for the metrics where comparison is
    meaningful. Missing baseline keys are silently ignored.
    """
    rag_results = list(rag_results)
    base = compute_metrics([r.result for r in rag_results])

    expected_iris_total = 0
    expected_iris_recalled = 0
    expected_iris_selected = 0
    rerank_promoted = 0
    rerank_eligible = 0
    cases_with_unresolved = 0
    cases_with_ambiguity = 0

    for entry in rag_results:
        expected = _expected_concept_iris(entry.case)
        if not expected:
            continue
        # Normalize both sides through the same prefix-expansion step so a
        # case spec saying ``ex:worksFor`` matches a retrieved concept whose
        # IRI is ``http://example.org/worksFor``.
        retrieved = {_expand(rc.concept.iri) for rc in entry.rag_diagnostics.retrieved_concepts}
        retrieved |= {
            _expand(rc.concept.prefixed_name)
            for rc in entry.rag_diagnostics.retrieved_concepts
            if rc.concept.prefixed_name
        }
        selected = {_expand(rc.concept.iri) for rc in entry.rag_diagnostics.selected_concepts}
        selected |= {
            _expand(rc.concept.prefixed_name)
            for rc in entry.rag_diagnostics.selected_concepts
            if rc.concept.prefixed_name
        }
        expected = {_expand(e) for e in expected}
        # Top-K retrieval before rerank: the first K hits per retrieval.
        topk_retrieved = _topk_iris(entry.rag_diagnostics.retrieved_concepts, k)
        topk_reranked = _topk_iris(entry.rag_diagnostics.reranked_concepts, k)

        for iri in expected:
            expected_iris_total += 1
            if iri in retrieved:
                expected_iris_recalled += 1
            if iri in selected:
                expected_iris_selected += 1

        promoted = (expected & topk_reranked) - (expected & topk_retrieved)
        if expected & topk_retrieved or expected & topk_reranked:
            rerank_eligible += 1
        if promoted:
            rerank_promoted += 1

        if entry.rag_diagnostics.unresolved_mentions:
            cases_with_unresolved += 1
        if _has_ambiguous_selection(entry.rag_diagnostics):
            cases_with_ambiguity += 1

    n_cases = max(1, len(rag_results))
    rag_metrics = {
        f"retrieval_recall_at_{k}": (
            (expected_iris_recalled / expected_iris_total) if expected_iris_total else 1.0
        ),
        "selected_concept_accuracy": (
            (expected_iris_selected / expected_iris_total) if expected_iris_total else 1.0
        ),
        "reranker_improvement_rate": (
            (rerank_promoted / rerank_eligible) if rerank_eligible else 0.0
        ),
        "unresolved_mention_rate": cases_with_unresolved / n_cases,
        "concept_ambiguity_rate": cases_with_ambiguity / n_cases,
        "planner_case_pass_rate": base.get("case_pass_rate", 0.0),
    }
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


def _expected_concept_iris(case: GoldenCase) -> set[str]:
    """Approximate the set of ontology IRIs a case is expected to use.

    Falls back to the legacy ``required_terms`` list (prefixed names like
    ``ex:worksFor``) when no IR-level requirement is specified, since that
    is what the existing golden cases populate. Variables (``?_``) are
    skipped — they are not concept IRIs.
    """
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
    return iris


def _topk_iris(items: list, k: int) -> set[str]:
    """Return the IRI set of the first ``k`` items (retrieved or reranked).

    Expands prefixed names so the set is directly comparable with the
    expected IRI set produced by :func:`_expected_concept_iris`.
    """
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
