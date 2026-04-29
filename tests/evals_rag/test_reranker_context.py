"""Tests for the question-aware :class:`HeuristicReranker`."""

from __future__ import annotations

import pytest

from evals_rag.models import OntologyConcept, RagMentionDiagnostic, RetrievedConcept
from evals_rag.reranking import HeuristicReranker, RerankContext


def _retrieved(
    iri: str,
    *,
    label: str | None = None,
    kind: str = "property",
    score: float = 0.5,
    domain: list[str] | None = None,
    range_: list[str] | None = None,
    mention: str | None = None,
) -> RetrievedConcept:
    metadata: dict[str, object] = {}
    if mention is not None:
        metadata["rag_mention"] = mention
    return RetrievedConcept(
        concept=OntologyConcept(
            iri=iri,
            label=label,
            kind=kind,
            domain=list(domain or []),
            range=list(range_ or []),
            metadata=metadata,
        ),
        score=score,
        retrieval_rank=0,
        retrieval_source="mock",
    )


@pytest.mark.asyncio
async def test_per_mention_expected_kind_overrides_static():
    candidates = [
        _retrieved(
            "http://example.org/PersonClass",
            label="Person",
            kind="class",
            score=0.7,
            mention="people",
        ),
        _retrieved(
            "http://example.org/personFn",
            label="Person",
            kind="property",
            score=0.7,
            mention="people",
        ),
    ]
    ctx = RerankContext(
        question="show every person",
        expected_kinds_by_mention={"people": ["class"]},
    )
    out = await HeuristicReranker().rerank("show every person", candidates, limit=2, context=ctx)
    assert out[0].concept.kind == "class"


@pytest.mark.asyncio
async def test_kind_conflict_penalizes_candidate():
    candidates = [
        _retrieved(
            "http://example.org/personIndividual",
            label="Person",
            kind="individual",
            score=0.7,
            mention="people",
        ),
        _retrieved(
            "http://example.org/PersonClass",
            label="Person",
            kind="class",
            score=0.65,
            mention="people",
        ),
    ]
    ctx = RerankContext(
        question="show every person",
        expected_kinds_by_mention={"people": ["class"]},
    )
    out = await HeuristicReranker().rerank("show every person", candidates, limit=2, context=ctx)
    assert out[0].concept.kind == "class"


@pytest.mark.asyncio
async def test_domain_range_overlap_uses_inferred_class_terms():
    candidates = [
        _retrieved(
            "http://example.org/randomProp",
            label="random",
            kind="property",
            score=0.6,
        ),
        _retrieved(
            "http://example.org/worksFor",
            label="works for",
            kind="property",
            score=0.6,
            domain=["http://example.org/Person"],
            range_=["http://example.org/Company"],
        ),
    ]
    ctx = RerankContext(
        question="who works for the company",
        inferred_class_terms=["http://example.org/Person", "http://example.org/Company"],
    )
    out = await HeuristicReranker().rerank(
        "who works for the company", candidates, limit=2, context=ctx
    )
    assert out[0].concept.iri == "http://example.org/worksFor"


@pytest.mark.asyncio
async def test_relation_cue_boosts_age_for_oldest_question():
    candidates = [
        _retrieved(
            "http://example.org/foundedBy",
            label="founded by",
            kind="property",
            score=0.6,
        ),
        _retrieved(
            "http://example.org/age",
            label="age",
            kind="property",
            score=0.55,
            range_=["http://www.w3.org/2001/XMLSchema#integer"],
        ),
    ]
    out = await HeuristicReranker().rerank("who is the oldest person?", candidates, limit=2)
    assert out[0].concept.iri == "http://example.org/age"


@pytest.mark.asyncio
async def test_relation_cue_boosts_date_property_for_joined_after_question():
    candidates = [
        _retrieved(
            "http://example.org/age",
            label="age",
            kind="property",
            score=0.6,
            range_=["http://www.w3.org/2001/XMLSchema#integer"],
        ),
        _retrieved(
            "http://example.org/joined",
            label="joined",
            kind="property",
            score=0.55,
            range_=["http://www.w3.org/2001/XMLSchema#date"],
        ),
    ]
    out = await HeuristicReranker().rerank("people who joined after 2019", candidates, limit=2)
    assert out[0].concept.iri == "http://example.org/joined"


@pytest.mark.asyncio
async def test_mention_diagnostics_thread_through_context():
    ctx = RerankContext(
        question="people",
        mentions=[
            RagMentionDiagnostic(
                text="people",
                expected_kinds=["class"],
                sources=["class_noun"],
            )
        ],
        expected_kinds_by_mention={"people": ["class"]},
    )
    candidates = [
        _retrieved("http://example.org/Person", label="Person", kind="class", mention="people")
    ]
    out = await HeuristicReranker().rerank("people", candidates, limit=1, context=ctx)
    assert out[0].concept.iri == "http://example.org/Person"
