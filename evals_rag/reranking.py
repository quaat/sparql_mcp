"""Concept re-rankers.

Re-ranking is optional: the runner can be invoked with ``--reranker noop``
to compare bare retrieval against retrieval+rerank in the metrics. The
:class:`HeuristicReranker` is what the default offline eval uses; it is
deterministic and does not call any model, which keeps CI reproducible.

The placeholder :class:`ModelReranker` documents the interface a future
cross-encoder / LLM re-ranker should implement and fails closed if used
before that wiring exists. The runner CLI rejects ``--reranker model``
until that wiring lands so a misconfiguration cannot crash mid-eval.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from evals_rag.models import (
    ConceptKind,
    OntologyConcept,
    RagMentionDiagnostic,
    RerankedConcept,
    RetrievedConcept,
)


@dataclass
class RerankContext:
    """Per-call context the heuristic reranker uses to be question-aware.

    The runner builds one of these for every question and threads it
    through :meth:`ConceptReranker.rerank`. The fields make the reranker's
    decisions auditable from a report.
    """

    question: str
    mentions: list[RagMentionDiagnostic] = field(default_factory=list)
    expected_kinds_by_mention: dict[str, list[ConceptKind]] = field(default_factory=dict)
    baseline_iris: list[str] = field(default_factory=list)
    """IRIs the deterministic resolver already settled on."""

    inferred_class_terms: list[str] = field(default_factory=list)
    """Class IRIs / labels mentioned in the question or already resolved
    by the baseline. Used to award domain/range overlap boosts."""


class ConceptReranker(Protocol):
    """Async re-ranker interface.

    Takes the question text, candidate list, limit, and optional
    :class:`RerankContext`. Returns a re-ranked list trimmed to ``limit``.
    The contract is order-independent: the caller does not assume the
    reranker preserves input order unless it explicitly says so (the
    no-op reranker does).
    """

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
        context: RerankContext | None = None,
    ) -> list[RerankedConcept]: ...


class NoopReranker:
    """Pass-through reranker that preserves retrieval order."""

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
        context: RerankContext | None = None,
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
    - matching the originating mention's expected kind (class / property
      / etc.) → moderate boost.
    - kind conflict with the originating mention → moderate penalty so
      a "works for" mention does not pick up an individual that happens
      to share the local name.
    - properties whose declared / observed domain or range overlaps with a
      class term inferred from the question → small boost.
    - relation-cue boosts for properties whose range looks numeric /
      temporal when the question says "oldest" / "joined after" / etc.
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
        self._static_class_terms = {t.lower() for t in (question_class_terms or [])}
        self._static_expected_kinds: list[ConceptKind] = list(expected_kinds or [])

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
        context: RerankContext | None = None,
    ) -> list[RerankedConcept]:
        if not candidates:
            return []
        ctx = context or RerankContext(question=question)
        q = question.lower()
        class_terms = set(self._static_class_terms)
        for term in ctx.inferred_class_terms:
            class_terms.add(term.lower())
        relation_cues = _detect_relation_cues(q)
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
            mention_text = c.concept.metadata.get("rag_mention")
            mention_kinds = (
                ctx.expected_kinds_by_mention.get(mention_text, [])
                if isinstance(mention_text, str)
                else []
            )
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
            kinds_to_match = mention_kinds or self._static_expected_kinds
            if kinds_to_match:
                if c.concept.kind in kinds_to_match:
                    boost += self.weights.kind_match
                    reasons.append(f"kind={c.concept.kind}")
                else:
                    boost -= self.weights.kind_conflict_penalty
                    reasons.append(f"kind_conflict({c.concept.kind})")
            if class_terms and (
                _any_overlap(class_terms, c.concept.domain)
                or _any_overlap(class_terms, c.concept.range)
            ):
                boost += self.weights.domain_range_match
                reasons.append("domain_or_range_overlap")
            if relation_cues and _matches_relation_cue(c.concept, relation_cues):
                boost += self.weights.relation_cue_match
                reasons.append(f"relation_cue:{','.join(sorted(relation_cues))}")
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
    """Tunable boost weights for :class:`HeuristicReranker`."""

    __slots__ = (
        "alias_match",
        "ambiguity_penalty",
        "domain_range_match",
        "exact_label_match",
        "kind_conflict_penalty",
        "kind_match",
        "relation_cue_match",
    )

    def __init__(
        self,
        *,
        exact_label_match: float = 0.35,
        alias_match: float = 0.15,
        kind_match: float = 0.20,
        kind_conflict_penalty: float = 0.20,
        domain_range_match: float = 0.10,
        relation_cue_match: float = 0.15,
        ambiguity_penalty: float = 0.05,
    ) -> None:
        self.exact_label_match = exact_label_match
        self.alias_match = alias_match
        self.kind_match = kind_match
        self.kind_conflict_penalty = kind_conflict_penalty
        self.domain_range_match = domain_range_match
        self.relation_cue_match = relation_cue_match
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


# --- Relation-cue heuristics ---------------------------------------------


_NUMERIC_RANGE_FRAGMENTS = {
    "integer",
    "decimal",
    "double",
    "float",
    "int",
    "number",
    "year",
    "age",
}
_TEMPORAL_RANGE_FRAGMENTS = {
    "date",
    "datetime",
    "time",
    "timestamp",
    "joined",
    "founded",
}


def _detect_relation_cues(q_lower: str) -> set[str]:
    """Inspect the question for cue words that hint at a relation type."""
    cues: set[str] = set()
    if any(c in q_lower for c in ("oldest", "youngest", "average age", "older than")):
        cues.add("numeric")
    if any(c in q_lower for c in ("joined", "founded", "after ", "before ", "since ", " on 20")):
        cues.add("temporal")
    return cues


def _matches_relation_cue(concept: OntologyConcept, cues: set[str]) -> bool:
    if concept.kind != "property":
        return False
    haystacks: list[str] = []
    haystacks.extend(concept.range)
    haystacks.extend(concept.domain)
    if concept.label:
        haystacks.append(concept.label)
    if concept.prefixed_name:
        haystacks.append(concept.prefixed_name)
    fragments = [_last_segment(h).lower() for h in haystacks if h]
    has_numeric = any(
        any(token in frag for token in _NUMERIC_RANGE_FRAGMENTS) for frag in fragments
    )
    if "numeric" in cues and has_numeric:
        return True
    has_temporal = any(
        any(token in frag for token in _TEMPORAL_RANGE_FRAGMENTS) for frag in fragments
    )
    return "temporal" in cues and has_temporal


def _last_segment(iri: str) -> str:
    return iri.rstrip("#/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


# --- Placeholder model reranker -------------------------------------------


class ModelReranker:
    """Placeholder for a future cross-encoder / LLM re-ranker.

    The runner CLI rejects ``--reranker model`` until a real implementation
    lands; the class is kept here so internal callers can still type-check
    against the protocol.
    """

    def __init__(self, *, model: Any | None = None) -> None:
        self.model = model

    async def rerank(
        self,
        question: str,
        candidates: list[RetrievedConcept],
        limit: int,
        context: RerankContext | None = None,
    ) -> list[RerankedConcept]:
        raise NotImplementedError(
            "ModelReranker is a placeholder; wire a concrete cross-encoder "
            "or LLM scorer here once one is available."
        )
