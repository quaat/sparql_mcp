"""Mock + Qdrant retriever tests.

Qdrant is exercised through a fake async client so the test suite never
needs a running vector database. The fake records the search arguments so
we can assert the retriever passes the right collection name, vector, and
filter.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals_rag.models import OntologyConcept, RetrievalQuery
from evals_rag.retrieval import (
    FakeEmbeddingProvider,
    MissingEmbeddingProvider,
    MockOntologyRetriever,
    QdrantOntologyRetriever,
    RetrievalError,
)


def _concepts() -> list[OntologyConcept]:
    return [
        OntologyConcept(
            iri="http://example.org/Person",
            prefixed_name="ex:Person",
            label="Person",
            aliases=["human"],
            kind="class",
        ),
        OntologyConcept(
            iri="http://example.org/Company",
            prefixed_name="ex:Company",
            label="Company",
            kind="class",
        ),
        OntologyConcept(
            iri="http://example.org/worksFor",
            prefixed_name="ex:worksFor",
            label="works for",
            kind="property",
            domain=["http://example.org/Person"],
            range=["http://example.org/Company"],
        ),
        OntologyConcept(
            iri="http://example.org/Acme",
            prefixed_name="ex:Acme",
            label="Acme",
            kind="individual",
        ),
    ]


@pytest.mark.asyncio
async def test_mock_retriever_returns_deterministic_concepts():
    retriever = MockOntologyRetriever(_concepts())
    q = RetrievalQuery(question="Who works for Acme?", mention="works for", limit=5)
    first = await retriever.retrieve(q)
    second = await retriever.retrieve(q)
    assert [c.concept.iri for c in first] == [c.concept.iri for c in second]
    assert first[0].concept.iri == "http://example.org/worksFor"
    assert first[0].retrieval_source == "mock"


@pytest.mark.asyncio
async def test_mock_retriever_respects_expected_kinds():
    retriever = MockOntologyRetriever(_concepts())
    q = RetrievalQuery(
        question="Who works for Acme?",
        mention="acme",
        expected_kinds=["individual"],
        limit=5,
    )
    out = await retriever.retrieve(q)
    assert out, "expected at least one match"
    assert {c.concept.kind for c in out} == {"individual"}


@pytest.mark.asyncio
async def test_mock_retriever_respects_limit():
    retriever = MockOntologyRetriever(_concepts())
    q = RetrievalQuery(question="person company works", limit=2)
    out = await retriever.retrieve(q)
    assert len(out) <= 2


@pytest.mark.asyncio
async def test_mock_retriever_handles_no_matches():
    retriever = MockOntologyRetriever(_concepts())
    q = RetrievalQuery(question="zzz", mention="nonsense_token")
    out = await retriever.retrieve(q)
    assert out == []


@pytest.mark.asyncio
async def test_mock_retriever_handles_empty_text():
    retriever = MockOntologyRetriever(_concepts())
    out = await retriever.retrieve(RetrievalQuery(question="", mention=""))
    assert out == []


# --- Qdrant retriever (fake client) ---------------------------------------


class _FakeHit:
    def __init__(self, payload: dict[str, Any], score: float) -> None:
        self.payload = payload
        self.score = score


class _FakeQdrantClient:
    def __init__(self, hits: list[Any] | None = None) -> None:
        self._hits = hits if hits is not None else []
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return self._hits


@pytest.mark.asyncio
async def test_qdrant_requires_embedding_provider():
    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="x",
        client=_FakeQdrantClient(),
    )
    assert isinstance(retriever.embedding_provider, MissingEmbeddingProvider)
    with pytest.raises(RuntimeError) as excinfo:
        await retriever.retrieve(RetrievalQuery(question="hi"))
    assert "vectorizer is not implemented yet" in str(excinfo.value)


@pytest.mark.asyncio
async def test_qdrant_constructs_search_with_filter():
    embed = FakeEmbeddingProvider(dim=4)
    payload = {
        "iri": "http://example.org/worksFor",
        "prefixed_name": "ex:worksFor",
        "label": "works for",
        "kind": "property",
        "domain": ["http://example.org/Person"],
        "range": ["http://example.org/Company"],
        "extra": "metadata-bag",
    }
    fake = _FakeQdrantClient(hits=[_FakeHit(payload, score=0.91)])
    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="ontology_concepts",
        embedding_provider=embed,
        client=fake,
    )
    out = await retriever.retrieve(
        RetrievalQuery(
            question="who works for Acme?",
            mention="works for",
            expected_kinds=["property"],
            limit=3,
        )
    )
    assert len(out) == 1
    assert out[0].concept.iri == "http://example.org/worksFor"
    assert out[0].score == pytest.approx(0.91)
    # The retriever must propagate metadata.
    assert out[0].concept.metadata == {"extra": "metadata-bag"}
    # And it must build a kind-filter and pass the embedded vector.
    call = fake.calls[0]
    assert call["collection_name"] == "ontology_concepts"
    assert call["limit"] == 3
    assert isinstance(call["query_vector"], list) and len(call["query_vector"]) == 4
    assert call["query_filter"] == {"must": [{"key": "kind", "match": {"any": ["property"]}}]}
    assert embed.calls == ["works for"]


@pytest.mark.asyncio
async def test_qdrant_handles_empty_results():
    fake = _FakeQdrantClient(hits=[])
    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="x",
        embedding_provider=FakeEmbeddingProvider(dim=4),
        client=fake,
    )
    out = await retriever.retrieve(RetrievalQuery(question="anything"))
    assert out == []


@pytest.mark.asyncio
async def test_qdrant_wraps_search_errors():
    class _BoomClient:
        async def search(self, **kwargs: Any) -> list[Any]:
            raise RuntimeError("network fell over")

    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="x",
        embedding_provider=FakeEmbeddingProvider(dim=4),
        client=_BoomClient(),
    )
    with pytest.raises(RetrievalError) as excinfo:
        await retriever.retrieve(RetrievalQuery(question="anything"))
    assert "Qdrant search failed" in str(excinfo.value)


@pytest.mark.asyncio
async def test_qdrant_invalid_payload_raises():
    fake = _FakeQdrantClient(hits=[_FakeHit(payload={"kind": "class"}, score=0.5)])
    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="x",
        embedding_provider=FakeEmbeddingProvider(dim=4),
        client=fake,
    )
    with pytest.raises(RetrievalError):
        await retriever.retrieve(RetrievalQuery(question="anything"))
