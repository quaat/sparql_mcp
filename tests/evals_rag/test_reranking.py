"""Re-ranker behaviour tests."""

from __future__ import annotations

import pytest

from evals_rag.models import OntologyConcept, RetrievedConcept
from evals_rag.reranking import (
    HeuristicReranker,
    HeuristicWeights,
    ModelReranker,
    NoopReranker,
)


def _retrieved(
    iri: str,
    *,
    label: str | None = None,
    kind: str = "class",
    score: float = 0.5,
    rank: int = 0,
    domain: list[str] | None = None,
    range_: list[str] | None = None,
    aliases: list[str] | None = None,
    prefixed_name: str | None = None,
) -> RetrievedConcept:
    return RetrievedConcept(
        concept=OntologyConcept(
            iri=iri,
            prefixed_name=prefixed_name,
            label=label,
            aliases=list(aliases or []),
            kind=kind,
            domain=list(domain or []),
            range=list(range_ or []),
        ),
        score=score,
        retrieval_rank=rank,
        retrieval_source="mock",
    )


@pytest.mark.asyncio
async def test_noop_reranker_preserves_order():
    candidates = [
        _retrieved("http://example.org/A", score=0.4, rank=0),
        _retrieved("http://example.org/B", score=0.9, rank=1),
    ]
    out = await NoopReranker().rerank("anything", candidates, limit=5)
    assert [r.concept.iri for r in out] == [
        "http://example.org/A",
        "http://example.org/B",
    ]
    assert out[0].rerank_score == out[0].retrieval_score


@pytest.mark.asyncio
async def test_heuristic_boosts_exact_label_match():
    candidates = [
        _retrieved("http://example.org/Other", label="Other", score=0.9, rank=0),
        _retrieved("http://example.org/Person", label="Person", score=0.6, rank=1),
    ]
    out = await HeuristicReranker().rerank("Show me every Person", candidates, limit=2)
    assert out[0].concept.iri == "http://example.org/Person"


@pytest.mark.asyncio
async def test_heuristic_boosts_kind_match():
    candidates = [
        _retrieved("http://example.org/PersonClass", label="Person", kind="class", score=0.7),
        _retrieved("http://example.org/personFn", label="Person", kind="property", score=0.7),
    ]
    out = await HeuristicReranker(expected_kinds=["property"]).rerank("Person", candidates, limit=2)
    assert out[0].concept.kind == "property"


@pytest.mark.asyncio
async def test_heuristic_boosts_domain_or_range_overlap():
    candidates = [
        _retrieved("http://example.org/randomProp", label="random", kind="property", score=0.6),
        _retrieved(
            "http://example.org/worksFor",
            label="works for",
            kind="property",
            score=0.6,
            domain=["http://example.org/Person"],
            range_=["http://example.org/Company"],
        ),
    ]
    out = await HeuristicReranker(question_class_terms=["Person", "Company"]).rerank(
        "Who works for", candidates, limit=2
    )
    assert out[0].concept.iri == "http://example.org/worksFor"


@pytest.mark.asyncio
async def test_heuristic_respects_limit():
    candidates = [
        _retrieved(f"http://example.org/c{i}", label=f"c{i}", score=0.5, rank=i) for i in range(5)
    ]
    out = await HeuristicReranker().rerank("c1", candidates, limit=3)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_heuristic_alias_match():
    candidates = [
        _retrieved("http://example.org/Person", label="Person", aliases=["human"], score=0.5),
        _retrieved("http://example.org/Other", label="Other", score=0.6),
    ]
    out = await HeuristicReranker().rerank("describe each human", candidates, limit=2)
    assert out[0].concept.iri == "http://example.org/Person"


@pytest.mark.asyncio
async def test_heuristic_empty_input_returns_empty():
    out = await HeuristicReranker().rerank("anything", [], limit=4)
    assert out == []


@pytest.mark.asyncio
async def test_heuristic_weights_are_configurable():
    weights = HeuristicWeights(exact_label_match=0.0, kind_match=0.0)
    candidates = [
        _retrieved("http://example.org/Person", label="Person", score=0.5),
        _retrieved("http://example.org/Other", label="Other", score=0.6),
    ]
    out = await HeuristicReranker(weights=weights).rerank("Person", candidates, limit=2)
    # With zero weights the heuristic should not flip retrieval order.
    assert out[0].concept.iri == "http://example.org/Other"


@pytest.mark.asyncio
async def test_model_reranker_is_a_placeholder():
    with pytest.raises(NotImplementedError):
        await ModelReranker().rerank("q", [], limit=1)
