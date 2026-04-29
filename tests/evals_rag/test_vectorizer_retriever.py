"""Tests for the eval-harness adapter onto ``ontology_vectorizer``.

Replaces the in-tree Qdrant retrieval logic. The adapter is the single
hand-off point between the RAG harness's :class:`RetrievedConcept` shape
and the vectorizer's :class:`OntologyConceptSearchResult`.
"""

from __future__ import annotations

import pytest
from ontology_vectorizer import (
    OntologyConceptRetriever,
    OntologyConceptSearchResult,
    OntologyRetrievalError,
    OntologyVectorizerConfig,
)
from ontology_vectorizer.config import OntologyConfig
from ontology_vectorizer.models import ScoredConcept

from evals_rag.models import RetrievalQuery
from evals_rag.retrieval import (
    RetrievalError,
    VectorizerOntologyRetriever,
)


class _StubService:
    def __init__(self, hits: list[OntologyConceptSearchResult]) -> None:
        self._hits = hits
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None

    def query(self, text: str, *, ontology_id=None, include_deprecated=False, kinds=None):
        self.calls.append(
            {
                "text": text,
                "ontology_id": ontology_id,
                "include_deprecated": include_deprecated,
                "kinds": list(kinds) if kinds else None,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        return [
            ScoredConcept(
                concept_id=h.concept_id,
                iri=h.iri,
                compact_id=h.compact_id,
                preferred_label=h.preferred_label,
                kind=h.kind,
                final_score=h.score,
                components={},
                payload={
                    "iri": h.iri,
                    "preferred_label": h.preferred_label,
                    "labels": list(h.labels),
                    "alt_labels": list(h.alt_labels),
                    "definitions": [h.definition] if h.definition else [],
                    "ontology_id": h.ontology_id,
                    "deprecated": h.deprecated,
                    "parents": list(h.parents),
                    "ancestors": list(h.ancestors),
                    "group_ids": list(h.group_ids),
                    "branch_ids": [],
                },
                explanation=[h.explanation] if h.explanation else [],
            )
            for h in self._hits
        ]


def _retriever(stub: _StubService) -> OntologyConceptRetriever:
    return OntologyConceptRetriever(
        config=OntologyVectorizerConfig(ontology=OntologyConfig(ontology_id="x")),
        retrieval_service=stub,
    )


def _hit(**overrides) -> OntologyConceptSearchResult:
    base = {
        "concept_id": "c1",
        "iri": "https://example.org/sst",
        "compact_id": "var:sst",
        "preferred_label": "sea surface temperature",
        "labels": ["sea surface temperature"],
        "alt_labels": ["SST"],
        "kind": "skos_concept",
        "definition": "Temperature at the ocean surface.",
        "ontology_id": "ocean-demo",
        "score": 0.9,
        "deprecated": False,
        "parents": ["var:temperature"],
        "ancestors": [],
        "explanation": "exact match",
        "children": [],
        "siblings": [],
        "group_ids": [],
    }
    base.update(overrides)
    return OntologyConceptSearchResult(**base)


@pytest.mark.asyncio
async def test_adapter_maps_results_to_retrieved_concept() -> None:
    stub = _StubService([_hit()])
    adapter = VectorizerOntologyRetriever(_retriever(stub), ontology_id="ocean-demo")
    out = await adapter.retrieve(
        RetrievalQuery(question="what is SST?", mention="sea surface temperature", limit=5)
    )
    assert len(out) == 1
    rc = out[0]
    assert rc.concept.iri == "https://example.org/sst"
    # ``skos_concept`` collapses to ``class`` in the eval-harness vocabulary.
    assert rc.concept.kind == "class"
    assert rc.concept.prefixed_name == "var:sst"
    assert rc.concept.label == "sea surface temperature"
    assert rc.concept.aliases == ["SST"]
    assert rc.retrieval_source == "vectorizer"
    assert rc.score == pytest.approx(0.9)
    # Useful payload fields stash into metadata so the planner / report can see them.
    assert rc.concept.metadata["parents"] == ["var:temperature"]
    assert rc.concept.metadata["vectorizer_kind"] == "skos_concept"


@pytest.mark.asyncio
async def test_adapter_translates_expected_kinds() -> None:
    stub = _StubService([_hit()])
    adapter = VectorizerOntologyRetriever(_retriever(stub))
    await adapter.retrieve(
        RetrievalQuery(
            question="who works for x", mention="works for", expected_kinds=["property"]
        )
    )
    assert stub.calls
    # ``property`` expands to all four vectorizer property kinds.
    assert stub.calls[0]["kinds"] == [
        "object_property",
        "datatype_property",
        "annotation_property",
        "rdf_property",
    ]


@pytest.mark.asyncio
async def test_adapter_drops_kinds_with_no_mapping() -> None:
    """``individual`` doesn't exist in the vectorizer vocabulary; pass-through is None."""
    stub = _StubService([_hit()])
    adapter = VectorizerOntologyRetriever(_retriever(stub))
    await adapter.retrieve(
        RetrievalQuery(question="x", mention="x", expected_kinds=["individual"])
    )
    assert stub.calls[0]["kinds"] is None


@pytest.mark.asyncio
async def test_adapter_returns_empty_when_query_blank() -> None:
    stub = _StubService([_hit()])
    adapter = VectorizerOntologyRetriever(_retriever(stub))
    out = await adapter.retrieve(RetrievalQuery(question=" ", mention=" "))
    assert out == []
    assert stub.calls == []


@pytest.mark.asyncio
async def test_adapter_wraps_unexpected_errors() -> None:
    stub = _StubService([])
    stub.fail_with = RuntimeError("Qdrant timeout")
    adapter = VectorizerOntologyRetriever(_retriever(stub))
    with pytest.raises(RetrievalError) as exc:
        await adapter.retrieve(RetrievalQuery(question="hi"))
    assert "vectorizer retrieval failed" in str(exc.value)
    assert "Qdrant timeout" in str(exc.value)


@pytest.mark.asyncio
async def test_adapter_preserves_vectorizer_validation_errors() -> None:
    """An :class:`OntologyRetrievalError` from the facade reaches the caller intact."""
    stub = _StubService([])
    stub.fail_with = OntologyRetrievalError("query is empty")
    adapter = VectorizerOntologyRetriever(_retriever(stub))
    with pytest.raises(RetrievalError):
        await adapter.retrieve(RetrievalQuery(question="hi"))
