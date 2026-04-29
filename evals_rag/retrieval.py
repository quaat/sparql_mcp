"""Ontology concept retrievers.

Two implementations are wired up:

- :class:`MockOntologyRetriever`: pure-Python, deterministic. Used in tests
  and the default ``--retriever mock`` runner mode. Scores concepts by
  shared-token overlap against the question / mention. No network, no
  embeddings.
- :class:`QdrantOntologyRetriever`: takes an :class:`EmbeddingProvider` and
  queries a Qdrant collection. The vectorizer that populates the collection
  is **not** implemented yet, so :class:`MissingEmbeddingProvider` is the
  default and fails closed with a clear message.

The retriever protocol is async-only so the Qdrant flow can use
``AsyncQdrantClient`` without forcing the mock implementation to fake an
event loop.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from evals_rag.models import (
    ConceptKind,
    OntologyConcept,
    RetrievalQuery,
    RetrievalSource,
    RetrievedConcept,
)


class OntologyRetriever(Protocol):
    """Async protocol every retriever implements.

    The contract is: given a :class:`RetrievalQuery`, return a ranked list
    of :class:`RetrievedConcept` (best-first), respecting ``limit`` and
    optionally filtering on ``expected_kinds``. Failures should be raised as
    :class:`RetrievalError` so the runner can record a structured error
    instead of swallowing exceptions.
    """

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedConcept]: ...


class RetrievalError(RuntimeError):
    """Raised when a retriever cannot fulfill a query.

    Wraps Qdrant client errors / network failures with a structured tag so
    the runner can surface them in the report without leaking provider-specific
    exception types.
    """


class MissingEmbeddingProviderError(RetrievalError):
    """Raised when a retriever is configured without an embedding provider.

    A subclass of :class:`RetrievalError` so the planner's ``_safe_retrieve``
    can record it as a retrieval diagnostic instead of letting it crash
    the run.
    """


# --- Embedding provider ----------------------------------------------------


class EmbeddingProvider(Protocol):
    """Async protocol for query-time embeddings.

    The vectorizer (planned, not implemented) will produce concept-side
    embeddings during indexing. At query time the retriever asks the
    provider for a vector representation of the user's question / mention
    and uses that as the Qdrant search vector.
    """

    async def embed_query(self, text: str) -> list[float]: ...


class MissingEmbeddingProvider:
    """Sentinel embedding provider that always fails.

    The Qdrant retriever defaults to this when no provider is supplied.
    Calling :meth:`embed_query` raises
    :class:`MissingEmbeddingProviderError` (a subclass of
    :class:`RetrievalError`) so the runner can record the failure as a
    retrieval diagnostic rather than confusing it with a planner crash.
    """

    async def embed_query(self, text: str) -> list[float]:
        raise MissingEmbeddingProviderError(
            "Qdrant retrieval requires an EmbeddingProvider. The ontology "
            "vectorizer is not implemented yet."
        )


class FakeEmbeddingProvider:
    """Deterministic, hashing-based embedding provider for tests.

    Produces a fixed-dimension vector seeded from the input text. Two
    different inputs always yield two different vectors, and the same input
    always yields the same vector — that is enough to verify the Qdrant
    retriever calls ``embed_query`` and threads the result into the search
    correctly.
    """

    def __init__(self, dim: int = 8) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        # Deterministic per-character bucket sum, scaled to [0, 1).
        buckets = [0.0] * self.dim
        for i, ch in enumerate(text):
            buckets[i % self.dim] += (ord(ch) % 31) / 31.0
        # Normalize so vectors are in a comparable range across inputs.
        total = sum(buckets) or 1.0
        return [b / total for b in buckets]


# --- Mock retriever --------------------------------------------------------


class MockOntologyRetriever:
    """Deterministic, in-memory retriever over a list of concepts.

    Scoring uses a coarse token-overlap heuristic that is **not** trying to
    imitate embedding similarity — it just needs to be stable, ordered, and
    sensitive to label / alias matches so the test suite can assert exact
    behaviour. Real semantic retrieval is the Qdrant retriever's job.
    """

    def __init__(self, concepts: Iterable[OntologyConcept]) -> None:
        self._concepts = list(concepts)

    @property
    def concepts(self) -> list[OntologyConcept]:
        return list(self._concepts)

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedConcept]:
        text = (query.mention or query.question).strip()
        if not text:
            return []
        kinds = set(query.expected_kinds)
        scored: list[tuple[float, str, OntologyConcept]] = []
        for concept in self._concepts:
            if kinds and concept.kind not in kinds:
                continue
            score, matched = _score_concept(text, concept)
            if score <= 0.0:
                continue
            scored.append((score, matched, concept))
        # Stable order: score desc, then IRI asc as a tiebreaker.
        scored.sort(key=lambda t: (-t[0], t[2].iri))
        out: list[RetrievedConcept] = []
        for rank, (score, matched, concept) in enumerate(scored[: query.limit]):
            out.append(
                RetrievedConcept(
                    concept=concept,
                    score=score,
                    retrieval_rank=rank,
                    retrieval_source="mock",
                    matched_text=matched,
                    explanation=f"mock token-overlap score={score:.3f}",
                )
            )
        return out


def _tokens(text: str) -> set[str]:
    return {t for t in _split_tokens(text.lower()) if t}


def _split_tokens(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                out.append("".join(current))
                current = []
    if current:
        out.append("".join(current))
    # camelCase split: "worksFor" → "works", "for"
    expanded: list[str] = []
    for tok in out:
        chunk: list[str] = []
        for ch in tok:
            if ch.isupper() and chunk:
                expanded.append("".join(chunk).lower())
                chunk = [ch]
            else:
                chunk.append(ch)
        if chunk:
            expanded.append("".join(chunk).lower())
    return expanded


def _score_concept(text: str, concept: OntologyConcept) -> tuple[float, str]:
    """Return ``(score, matched_text)`` for ``text`` vs ``concept``.

    Exact-label / alias / prefixed-name matches score 1.0; substring matches
    score 0.85; shared-token overlap scores in [0, 0.7). Below-threshold
    matches return 0.0 so the retriever omits them.
    """
    qt = text.lower().strip()
    if not qt:
        return 0.0, ""
    # Build candidate strings from the concept.
    candidates: list[str] = []
    if concept.label:
        candidates.append(concept.label)
    candidates.extend(concept.aliases)
    if concept.prefixed_name:
        candidates.append(concept.prefixed_name)
        local = concept.prefixed_name.split(":", 1)[-1]
        candidates.append(local)
    # Last IRI segment as a final fallback.
    last = concept.iri.rstrip("#/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    if last:
        candidates.append(last)

    best_score = 0.0
    best_text = ""
    qt_tokens = _tokens(qt)
    for cand in candidates:
        ct = cand.lower().strip()
        if not ct:
            continue
        if ct == qt:
            return 1.0, cand
        if qt in ct or ct in qt:
            if best_score < 0.85:
                best_score = 0.85
                best_text = cand
            continue
        ct_tokens = _tokens(cand)
        if not ct_tokens or not qt_tokens:
            continue
        overlap = qt_tokens & ct_tokens
        if not overlap:
            continue
        score = 0.5 * len(overlap) / max(len(qt_tokens), len(ct_tokens))
        if score > best_score:
            best_score = score
            best_text = cand
    return best_score, best_text


# --- Qdrant retriever ------------------------------------------------------


class QdrantOntologyRetriever:
    """Retriever backed by a Qdrant collection.

    The actual ``qdrant-client`` import is deferred until :meth:`retrieve`
    runs so importing this module never requires the optional ``rag`` extra.
    Tests inject a fake ``client`` to assert how the retriever shapes
    queries; they do not need a running Qdrant.
    """

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        embedding_provider: EmbeddingProvider | None = None,
        api_key: str | None = None,
        client: Any = None,
        score_threshold: float = 0.0,
        retrieval_source: RetrievalSource = "qdrant",
    ) -> None:
        self.url = url
        self.collection = collection
        self.api_key = api_key
        self.embedding_provider: EmbeddingProvider = (
            embedding_provider if embedding_provider is not None else MissingEmbeddingProvider()
        )
        self.score_threshold = score_threshold
        self._client = client
        self._retrieval_source = retrieval_source

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import AsyncQdrantClient  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RetrievalError(
                "qdrant-client is required for QdrantOntologyRetriever; "
                "install with `pip install graph-mcp[rag]`"
            ) from exc
        self._client = AsyncQdrantClient(url=self.url, api_key=self.api_key)
        return self._client

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedConcept]:
        text = (query.mention or query.question).strip()
        if not text:
            return []
        try:
            vector = await self.embedding_provider.embed_query(text)
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"embedding provider failed: {exc}") from exc

        client = self._ensure_client()
        kinds = list(query.expected_kinds)
        try:
            hits = await _qdrant_search(
                client,
                collection=self.collection,
                vector=vector,
                limit=query.limit,
                expected_kinds=kinds,
                score_threshold=self.score_threshold,
            )
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Qdrant search failed: {exc}") from exc

        out: list[RetrievedConcept] = []
        for rank, hit in enumerate(hits):
            payload = _hit_payload(hit)
            try:
                concept = _concept_from_payload(payload)
            except ValueError as exc:
                raise RetrievalError(f"invalid Qdrant payload: {exc}") from exc
            out.append(
                RetrievedConcept(
                    concept=concept,
                    score=float(_hit_score(hit)),
                    retrieval_rank=rank,
                    retrieval_source=self._retrieval_source,
                    matched_text=concept.label or concept.prefixed_name or concept.iri,
                    explanation=f"qdrant collection={self.collection!r}",
                )
            )
        return out


async def _qdrant_search(
    client: Any,
    *,
    collection: str,
    vector: list[float],
    limit: int,
    expected_kinds: list[ConceptKind],
    score_threshold: float,
) -> list[Any]:
    """Issue a search against ``client`` and return raw hits.

    Kept as a free function so tests can patch it without subclassing the
    retriever. The kind filter is applied client-side when the fake client
    cannot express Qdrant's structured filter API.
    """
    query_filter = _build_qdrant_filter(expected_kinds)
    search = getattr(client, "search", None)
    if search is None:
        raise RetrievalError("qdrant client is missing a 'search' coroutine")
    result = await search(
        collection_name=collection,
        query_vector=vector,
        limit=limit,
        query_filter=query_filter,
        score_threshold=score_threshold or None,
    )
    return list(result or [])


def _build_qdrant_filter(expected_kinds: list[ConceptKind]) -> dict[str, Any] | None:
    """Build a minimal payload filter for ``expected_kinds``.

    Returns a plain dict so the function works regardless of whether the
    caller has the optional ``qdrant_client.models`` types available. The
    real Qdrant client accepts dict-shaped filters directly.
    """
    if not expected_kinds:
        return None
    return {
        "must": [
            {
                "key": "kind",
                "match": {"any": list(expected_kinds)},
            }
        ]
    }


def _hit_payload(hit: Any) -> dict[str, Any]:
    payload = getattr(hit, "payload", None)
    if payload is None and isinstance(hit, dict):
        payload = hit.get("payload")
    if not isinstance(payload, dict):
        raise RetrievalError("Qdrant hit is missing a payload dict")
    return payload


def _hit_score(hit: Any) -> float:
    score = getattr(hit, "score", None)
    if score is None and isinstance(hit, dict):
        score = hit.get("score")
    if score is None:
        return 0.0
    return float(score)


_VALID_KINDS: frozenset[str] = frozenset(
    ("class", "property", "individual", "graph", "datatype", "unknown")
)


def _concept_from_payload(payload: dict[str, Any]) -> OntologyConcept:
    """Build an :class:`OntologyConcept` from a Qdrant payload dict.

    Validates only the subset of fields the retriever cares about; unknown
    keys are placed into ``metadata`` so the future vectorizer can stash
    extra hints without breaking the schema.
    """
    iri = payload.get("iri")
    if not isinstance(iri, str) or not iri:
        raise ValueError("payload missing required 'iri' string")
    kind = payload.get("kind", "unknown")
    if kind not in _VALID_KINDS:
        kind = "unknown"
    known = {
        "iri",
        "prefixed_name",
        "label",
        "aliases",
        "kind",
        "description",
        "domain",
        "range",
        "examples",
        "source",
    }
    metadata = {k: v for k, v in payload.items() if k not in known}
    return OntologyConcept(
        iri=iri,
        prefixed_name=_optional_str(payload.get("prefixed_name")),
        label=_optional_str(payload.get("label")),
        aliases=_string_list(payload.get("aliases")),
        kind=kind,
        description=_optional_str(payload.get("description")),
        domain=_string_list(payload.get("domain")),
        range=_string_list(payload.get("range")),
        examples=_string_list(payload.get("examples")),
        source=_optional_str(payload.get("source")),
        metadata=metadata,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []
