"""Concept re-rankers.

Re-ranking is optional: the runner can be invoked with ``--reranker noop``
to compare bare retrieval against retrieval+rerank in the metrics. The
:class:`HeuristicReranker` is what the default offline eval uses; it is
deterministic and does not call any model, which keeps CI reproducible.

The placeholder :class:`ModelReranker` documents the interface a future
cross-encoder / LLM re-ranker should implement and fails closed if used
before that wiring exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from evals_rag.models import (
    ConceptKind,
    OntologyConcept,
    RerankedConcept,
    RetrievedConcept,
)


class ConceptReranker(Protocol):
    """Async re-ranker interface.

    Takes the question text and a candidate list, returns a re-ranked
    list trimmed to ``limit``. The contract is order-independent: the
    caller does not assume the reranker preserves input order unless it
    explicitly says so (the no-op reranker does).
    """

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
    ) -> list[RerankedConcept]: ...


class NoopReranker:
    """Pass-through reranker that preserves retrieval order.

    Useful as a baseline to verify whether re-ranking adds anything: when
    ``HeuristicReranker`` makes a case go from FAIL to PASS that NoopReranker
    fails on, the win is attributable to the heuristic, not retrieval alone.
    """

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
    ) -> list[RerankedConcept]:
        out: list[RerankedConcept] = []
        for rank, c in enumerate(candidates[: max(0, limit)]):
            out.append(
                RerankedConcept(
                    concept=c.concept,
                    retrieval_score=c.score,
                    rerank_score=c.score,
                    final_score=c.score,
                    rank=rank,
                    explanation="noop reranker (preserved retrieval order)",
                )
            )
        return out


class HeuristicReranker:
    """Deterministic reranker that boosts schema-anchored matches.

    The boosts encode rules of thumb the planner prompt also relies on:

    - exact label / alias / prefixed-name match → big boost.
    - matching the mention's expected kind (class / property / etc.) →
      moderate boost.
    - properties whose declared / observed domain or range overlaps with a
      class mentioned in the question → small boost.
    - ambiguity penalty: if the same label appears on multiple concepts,
      none of them should be promoted aggressively.

    Knobs are exposed on :class:`HeuristicWeights` so the eval can search
    over them without touching the algorithm.
    """

    def __init__(
        self,
        weights: HeuristicWeights | None = None,
        *,
        question_class_terms: Iterable[str] | None = None,
        expected_kinds: Iterable[ConceptKind] | None = None,
    ) -> None:
        self.weights = weights or HeuristicWeights()
        self._class_terms = {t.lower() for t in (question_class_terms or [])}
        self._expected_kinds = set(expected_kinds or [])

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
    ) -> list[RerankedConcept]:
        if not candidates:
            return []
        q = question.lower()
        # Detect ambiguity: identical labels appearing on multiple concepts.
        label_counts: dict[str, int] = {}
        for c in candidates:
            label = (c.concept.label or "").lower()
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1

        scored: list[tuple[float, float, RetrievedConcept, str]] = []
        for c in candidates:
            boost = 0.0
            reasons: list[str] = []
            label = (c.concept.label or "").lower()
            if label and label in q:
                boost += self.weights.exact_label_match
                reasons.append("label_in_question")
            for alias in c.concept.aliases:
                if alias and alias.lower() in q:
                    boost += self.weights.alias_match
                    reasons.append(f"alias:{alias!r}")
                    break
            if c.concept.prefixed_name and c.concept.prefixed_name.lower() in q:
                boost += self.weights.exact_label_match
                reasons.append("prefixed_name_in_question")
            if self._expected_kinds and c.concept.kind in self._expected_kinds:
                boost += self.weights.kind_match
                reasons.append(f"kind={c.concept.kind}")
            if self._class_terms and (
                _any_overlap(self._class_terms, c.concept.domain)
                or _any_overlap(self._class_terms, c.concept.range)
            ):
                boost += self.weights.domain_range_match
                reasons.append("domain_or_range_overlap")
            if label and label_counts.get(label, 0) > 1:
                boost -= self.weights.ambiguity_penalty
                reasons.append("ambiguous_label")
            final = c.score + boost
            explanation = ", ".join(reasons) if reasons else "no boosts applied"
            scored.append((final, boost, c, explanation))

        # Sort by final score desc, retrieval rank asc as tiebreaker.
        scored.sort(key=lambda t: (-t[0], t[2].retrieval_rank))
        out: list[RerankedConcept] = []
        for rank, (final, boost, retrieved, explanation) in enumerate(scored[: max(0, limit)]):
            out.append(
                RerankedConcept(
                    concept=retrieved.concept,
                    retrieval_score=retrieved.score,
                    rerank_score=boost,
                    final_score=final,
                    rank=rank,
                    explanation=explanation,
                )
            )
        return out


class HeuristicWeights:
    """Tunable boost weights for :class:`HeuristicReranker`.

    Defaults were chosen so a clean exact-label match dominates partial
    domain/range overlaps, but two cumulative weak signals (kind match +
    domain match) can still flip the order of two retrieval-tied candidates.
    """

    __slots__ = (
        "alias_match",
        "ambiguity_penalty",
        "domain_range_match",
        "exact_label_match",
        "kind_match",
    )

    def __init__(
        self,
        *,
        exact_label_match: float = 0.35,
        alias_match: float = 0.15,
        kind_match: float = 0.20,
        domain_range_match: float = 0.10,
        ambiguity_penalty: float = 0.05,
    ) -> None:
        self.exact_label_match = exact_label_match
        self.alias_match = alias_match
        self.kind_match = kind_match
        self.domain_range_match = domain_range_match
        self.ambiguity_penalty = ambiguity_penalty


def _any_overlap(needles: set[str], haystacks: list[str]) -> bool:
    """True if any needle appears as a token / suffix of any haystack."""
    if not needles or not haystacks:
        return False
    for h in haystacks:
        last = h.rstrip("#/").rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
        if last in needles:
            return True
        if h.lower() in needles:
            return True
    return False


# --- Placeholder model reranker -------------------------------------------


class ModelReranker:
    """Placeholder for a future cross-encoder / LLM re-ranker.

    The eval runner accepts ``--reranker model`` so the CLI surface is
    stable, but :meth:`rerank` raises :class:`NotImplementedError` until a
    concrete model is wired up. The intended shape is:

    1. Build a prompt or pair-encoder input from ``question`` plus each
       candidate's label / description / domain / range.
    2. Score each candidate with the model.
    3. Return :class:`RerankedConcept` objects with ``rerank_score`` set to
       the model output and ``final_score = retrieval_score + rerank_score``.
    """

    def __init__(self, *, model: Any | None = None) -> None:
        self.model = model

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
    ) -> list[RerankedConcept]:
        raise NotImplementedError(
            "ModelReranker is a placeholder; wire a concrete cross-encoder "
            "or LLM scorer here once one is available."
        )


def _safe_concept(retrieved: RetrievedConcept) -> OntologyConcept:
    """Defensive copy used when callers want to keep the concept immutable."""
    return retrieved.concept.model_copy(deep=True)
