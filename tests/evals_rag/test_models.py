"""Strict-validation tests for the RAG Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evals_rag.models import (
    ConceptCandidatePack,
    OntologyConcept,
    RagPlannerDiagnostics,
    RerankedConcept,
    RetrievalQuery,
    RetrievedConcept,
)


def _concept(**overrides):
    base = {
        "iri": "http://example.org/Person",
        "prefixed_name": "ex:Person",
        "label": "Person",
        "kind": "class",
    }
    base.update(overrides)
    return OntologyConcept(**base)


def test_ontology_concept_round_trips():
    c = _concept(aliases=["human"], examples=["ex:alice"], metadata={"src": "curated"})
    dumped = c.model_dump()
    again = OntologyConcept.model_validate(dumped)
    assert again == c


def test_ontology_concept_forbids_extra_fields():
    with pytest.raises(ValidationError):
        OntologyConcept(
            iri="http://example.org/x",
            kind="class",
            unexpected="boom",
        )


def test_ontology_concept_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        OntologyConcept(iri="http://example.org/x", kind="not-a-kind")


def test_retrieval_query_defaults():
    q = RetrievalQuery(question="who works for Acme?")
    assert q.mention is None
    assert q.expected_kinds == []
    assert q.limit == 20


def test_retrieved_concept_strict():
    rc = RetrievedConcept(
        concept=_concept(),
        score=0.9,
        retrieval_rank=0,
        retrieval_source="mock",
    )
    assert rc.concept.kind == "class"
    with pytest.raises(ValidationError):
        RetrievedConcept(
            concept=_concept(),
            score=0.9,
            retrieval_rank=0,
            retrieval_source="not-a-source",
        )


def test_reranked_concept_strict():
    rk = RerankedConcept(
        concept=_concept(),
        retrieval_score=0.5,
        rerank_score=0.2,
        final_score=0.7,
        rank=0,
    )
    assert rk.final_score == 0.7
    with pytest.raises(ValidationError):
        RerankedConcept(  # missing rank
            concept=_concept(),
            retrieval_score=0.5,
            rerank_score=0.2,
            final_score=0.7,
        )


def test_concept_candidate_pack_serialization():
    pack = ConceptCandidatePack(
        question="who works for Acme?",
        mentions=["acme"],
        retrieved=[],
        reranked=[],
        selected=[],
        unresolved_mentions=[],
        diagnostics=["acme: 0 retrieved"],
    )
    assert "acme" in pack.diagnostics[0]
    again = ConceptCandidatePack.model_validate(pack.model_dump())
    assert again == pack


def test_rag_planner_diagnostics_defaults():
    diag = RagPlannerDiagnostics()
    assert diag.mentions == []
    assert diag.planner_diagnostics == {}
    assert diag.candidate_pack_text is None
