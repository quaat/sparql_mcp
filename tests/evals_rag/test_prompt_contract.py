"""Prompt-contract tests for the RAG guidance string and candidate pack."""

from __future__ import annotations

from evals_rag.models import (
    ConceptCandidatePack,
    OntologyConcept,
    RerankedConcept,
)
from evals_rag.prompts import RAG_GUIDANCE, render_candidate_pack


def test_rag_guidance_says_resolved_terms_authoritative():
    assert "Resolved terms" in RAG_GUIDANCE
    assert "authoritative" in RAG_GUIDANCE.lower()


def test_rag_guidance_says_do_not_use_unselected_candidates():
    assert "Do not introduce IRIs from there" in RAG_GUIDANCE
    assert "low-scoring" in RAG_GUIDANCE.lower()


def test_rag_guidance_preserves_safety_contract():
    assert "RefusedOutput" in RAG_GUIDANCE
    assert "ClarificationOutput" in RAG_GUIDANCE


def test_candidate_pack_renders_scores_kinds_domain_range_and_mentions():
    pack = ConceptCandidatePack(
        question="who works for Acme?",
        mentions=["works for"],
        selected=[
            RerankedConcept(
                concept=OntologyConcept(
                    iri="http://example.org/worksFor",
                    prefixed_name="ex:worksFor",
                    label="works for",
                    kind="property",
                    domain=["http://example.org/Person"],
                    range=["http://example.org/Company"],
                    metadata={
                        "rag_mention": "works for",
                        "rag_mentions": ["works for", "employed by"],
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
    assert "ex:worksFor" in rendered
    assert "kind=property" in rendered
    assert "score=0.95" in rendered
    assert "domain=" in rendered
    assert "range=" in rendered
    assert "mentions=works for,employed by" in rendered
