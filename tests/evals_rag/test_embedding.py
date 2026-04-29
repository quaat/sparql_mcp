"""Embedding provider / Qdrant fail-fast / RetrievalError handling."""

from __future__ import annotations

from typing import Any

import pytest

from evals_rag.models import RetrievalQuery
from evals_rag.retrieval import (
    FakeEmbeddingProvider,
    MissingEmbeddingProvider,
    MissingEmbeddingProviderError,
    QdrantOntologyRetriever,
    RetrievalError,
)


@pytest.mark.asyncio
async def test_missing_embedding_provider_raises_retrieval_error():
    provider = MissingEmbeddingProvider()
    with pytest.raises(MissingEmbeddingProviderError) as excinfo:
        await provider.embed_query("anything")
    assert isinstance(excinfo.value, RetrievalError)
    assert "vectorizer is not implemented yet" in str(excinfo.value)


@pytest.mark.asyncio
async def test_qdrant_retrieve_raises_retrieval_error_when_provider_missing():
    class _ClientStub:
        async def search(self, **kwargs: Any) -> list[Any]:
            return []

    retriever = QdrantOntologyRetriever(
        url="http://nope:6333",
        collection="x",
        client=_ClientStub(),
    )
    with pytest.raises(RetrievalError):
        await retriever.retrieve(RetrievalQuery(question="anything"))


@pytest.mark.asyncio
async def test_safe_retrieve_records_embedding_error_in_diagnostics(monkeypatch):
    # Build a planner wrapper inline to reuse its _safe_retrieve helper.
    from evals_rag.models import RagPlannerDiagnostics

    diag = RagPlannerDiagnostics()

    class _FailingRetriever:
        async def retrieve(self, query: RetrievalQuery):
            raise MissingEmbeddingProviderError("embedding provider missing")

    from evals_rag.planner import RagPlannerWrapper

    wrapper = RagPlannerWrapper.__new__(RagPlannerWrapper)
    wrapper._retriever = _FailingRetriever()  # type: ignore[attr-defined]

    out = await wrapper._safe_retrieve(RetrievalQuery(question="?", mention="thing"), diag)
    assert out == []
    assert any("embedding provider missing" in err for err in diag.retrieval_errors)
    assert "thing" in diag.unresolved_mentions


def test_fake_embedding_provider_is_deterministic():
    p = FakeEmbeddingProvider(dim=4)
    import asyncio

    a = asyncio.run(p.embed_query("hello"))
    b = asyncio.run(p.embed_query("hello"))
    c = asyncio.run(p.embed_query("world"))
    assert a == b
    assert a != c
