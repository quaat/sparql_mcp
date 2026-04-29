"""Tests for retrieved-concept deduplication and lineage merging."""

from __future__ import annotations

import pytest

from evals_rag.models import (
    ConceptCandidatePack,
    OntologyConcept,
    RerankedConcept,
    RetrievedConcept,
)
from evals_rag.planner import dedupe_retrieved_concepts
from evals_rag.prompts import render_candidate_pack


def _retrieved(
    iri: str,
    *,
    score: float = 0.5,
    rank: int = 0,
    mention: str | None = None,
    label: str | None = None,
) -> RetrievedConcept:
    metadata: dict[str, object] = {}
    if mention is not None:
        metadata["rag_mention"] = mention
        metadata["rag_mentions"] = [mention]
    return RetrievedConcept(
        concept=OntologyConcept(
            iri=iri,
            prefixed_name=None,
            label=label,
            kind="class",
            metadata=metadata,
        ),
        score=score,
        retrieval_rank=rank,
        retrieval_source="mock",
        matched_text=label,
    )


def test_dedupe_collapses_by_iri_keeping_max_score():
    out = dedupe_retrieved_concepts(
        [
            _retrieved("ex:A", score=0.4, rank=2, mention="alpha"),
            _retrieved("ex:A", score=0.9, rank=0, mention="aleph"),
        ]
    )
    assert len(out) == 1
    survivor = out[0]
    assert survivor.score == pytest.approx(0.9)
    # Lineage is merged across duplicates.
    assert sorted(survivor.concept.metadata["rag_mentions"]) == ["aleph", "alpha"]
    # The smallest retrieval rank is preserved.
    assert survivor.retrieval_rank == 0


def test_dedupe_preserves_all_source_mentions_in_metadata():
    out = dedupe_retrieved_concepts(
        [
            _retrieved("ex:Person", score=0.5, mention="people"),
            _retrieved("ex:Person", score=0.6, mention="person"),
            _retrieved("ex:Person", score=0.4, mention="human"),
        ]
    )
    survivor = out[0]
    mentions = survivor.concept.metadata["rag_mentions"]
    assert sorted(mentions) == ["human", "people", "person"]


def test_dedupe_skips_concepts_without_iri():
    out = dedupe_retrieved_concepts([_retrieved("", score=0.9, mention="x")])
    assert out == []


def test_prompt_renders_multiple_source_mentions():
    pack = ConceptCandidatePack(
        question="show people",
        mentions=["people", "person"],
        selected=[
            RerankedConcept(
                concept=OntologyConcept(
                    iri="http://example.org/Person",
                    prefixed_name="ex:Person",
                    label="Person",
                    kind="class",
                    metadata={
                        "rag_mention": "people",
                        "rag_mentions": ["people", "person"],
                    },
                ),
                retrieval_score=0.6,
                rerank_score=0.35,
                final_score=0.95,
                rank=0,
            )
        ],
    )
    rendered = render_candidate_pack(pack)
    assert "mentions=people,person" in rendered
