"""RAG-augmented planner.

Wraps the existing planner workflow (extract → resolve → generate →
validate → repair) with a retrieval/re-rank step. Selected retrieval
candidates are promoted into the planner workflow's authoritative
resolved-term block via the ``supplemental_candidates`` argument on
:func:`evals.agent.run_planner_workflow`; the re-ranked candidate pack is
also rendered into the prompt for additional context.

The module exposes two main entry points:

- :func:`build_rag_planner` — builds a :class:`evals.agent.Planner` that
  runs the RAG cycle then delegates to a user-supplied generate callable
  (LLM agent, deterministic stub, etc.).
- :class:`RagPlannerWrapper` — the concrete planner returned by
  :func:`build_rag_planner`. Stores the most-recent
  :class:`RagPlannerDiagnostics` on ``last_rag_diagnostics`` so the runner
  can attach it to the case result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from evals.agent import (
    PlannerDeps,
    PlannerDiagnostics,
    PlannerOutput,
    run_planner_workflow,
)
from evals.mention_extractor import TermMention, extract_mentions
from evals_rag.config import RagSettings
from evals_rag.models import (
    ConceptCandidatePack,
    ConceptKind,
    OntologyConcept,
    RagMentionDiagnostic,
    RagPlannerDiagnostics,
    RerankedConcept,
    RetrievalQuery,
    RetrievedConcept,
)
from evals_rag.prompts import RAG_GUIDANCE, render_candidate_pack
from evals_rag.reranking import ConceptReranker, NoopReranker, RerankContext
from evals_rag.retrieval import OntologyRetriever, RetrievalError
from graph_mcp.graph.schema_discovery import SchemaSnapshot
from graph_mcp.graph.term_resolver import TermCandidate, TermResolver


@dataclass
class RagPlannerConfig:
    """Knobs that control how the RAG planner orchestrates retrieval.

    ``per_mention`` runs a separate retrieval call per extracted mention and
    is the default — it produces tighter, more interpretable candidate
    packs than a single full-question search. Setting it to ``False`` falls
    back to a single retrieval call against the whole question, which is
    sometimes useful for debugging.
    """

    settings: RagSettings
    per_mention: bool = True
    include_question_retrieval: bool = False


class RagPlannerWrapper:
    """Planner that wraps any plan-generating callable with a RAG cycle.

    Implements the same ``plan(question)`` contract as the existing
    workflow planners so it can drop into :func:`evals.runner.run_one`
    untouched. The wrapper stores per-call diagnostics on
    ``last_rag_diagnostics`` and ``last_diagnostics`` (the underlying
    workflow's diagnostics) so the runner can serialize both into the
    report.
    """

    def __init__(
        self,
        deps: PlannerDeps,
        retriever: OntologyRetriever,
        reranker: ConceptReranker,
        generate: Callable[[str], PlannerOutput],
        *,
        config: RagPlannerConfig,
    ) -> None:
        self._deps = deps
        self._retriever = retriever
        self._reranker = reranker
        self._generate = generate
        self._config = config
        self.last_rag_diagnostics: RagPlannerDiagnostics | None = None
        self.last_diagnostics: PlannerDiagnostics | None = None
        self.last_output: PlannerOutput | None = None
        self.last_repair_attempted: bool = False
        self.last_repair_succeeded: bool = False
        self.last_candidate_pack: ConceptCandidatePack | None = None

    def plan(self, question: str, *, resolver: TermResolver | None = None) -> PlannerOutput:
        rag_diag = RagPlannerDiagnostics()
        try:
            pack = asyncio.run(self._build_candidate_pack(question, rag_diag))
        except RuntimeError as exc:
            if "asyncio.run() cannot be called from a running event loop" in str(exc):
                pack = _run_in_running_loop(self._build_candidate_pack(question, rag_diag))
            else:
                raise

        candidate_block = render_candidate_pack(pack)
        rag_diag.candidate_pack_text = candidate_block
        self.last_candidate_pack = pack

        # Promote the re-ranked, score-eligible selected concepts into the
        # planner workflow's authoritative resolved-term block. The block is
        # also passed as supplemental context so the LLM can see the
        # background candidates that were considered but not promoted.
        kinds_by_mention: dict[str, list[ConceptKind]] = {
            d.text: list(d.expected_kinds) for d in rag_diag.mention_diagnostics
        }
        supplemental_terms = rag_concepts_to_term_candidates(
            pack.selected,
            score_threshold=self._config.settings.score_threshold,
            mention_to_kinds=kinds_by_mention,
        )

        output, workflow_diag = run_planner_workflow(
            self._deps,
            question,
            generate=self._generate,
            supplemental_candidates=supplemental_terms,
            supplemental_block=f"## Retrieved ontology candidates (RAG)\n{candidate_block}",
        )
        rag_diag.planner_diagnostics = workflow_diag.model_dump()
        rag_diag.promoted_term_iris = [t.iri for t in workflow_diag.rag_selected_terms if t.iri]
        # After workflow merge, recompute unresolved relative to the merged
        # resolved-term set so the report reflects post-promotion state.
        rag_diag.unresolved_mentions = list(workflow_diag.unresolved_mentions)
        self.last_diagnostics = workflow_diag
        self.last_rag_diagnostics = rag_diag
        self.last_output = output
        self.last_repair_attempted = workflow_diag.repair_attempts > 0
        self.last_repair_succeeded = (
            workflow_diag.final_validation_ok and self.last_repair_attempted
        )
        return output

    async def _build_candidate_pack(
        self, question: str, diag: RagPlannerDiagnostics
    ) -> ConceptCandidatePack:
        snapshot = self._deps.schema.snapshot()
        term_mentions = extract_mentions(question, snapshot)
        diag.mentions = [m.text for m in term_mentions]
        diag.mention_diagnostics = [
            RagMentionDiagnostic(
                text=m.text,
                expected_kinds=list(m.expected_kinds),
                sources=list(m.sources),
                span=(int(m.span[0]), int(m.span[1])) if m.span else None,
            )
            for m in term_mentions
        ]

        retrievals: list[tuple[str, RetrievalQuery, list[RetrievedConcept]]] = []
        if self._config.per_mention and term_mentions:
            for mention in term_mentions:
                kinds = _kinds_for_term_mention(mention, snapshot)
                rq = RetrievalQuery(
                    question=question,
                    mention=mention.text,
                    expected_kinds=kinds,
                    limit=self._config.settings.retrieval_limit,
                )
                hits = await self._safe_retrieve(rq, diag)
                retrievals.append((mention.text, rq, hits))
        if self._config.include_question_retrieval or not retrievals:
            rq = RetrievalQuery(
                question=question,
                mention=None,
                expected_kinds=[],
                limit=self._config.settings.retrieval_limit,
            )
            hits = await self._safe_retrieve(rq, diag)
            retrievals.append(("<question>", rq, hits))

        tagged: list[RetrievedConcept] = []
        for mention_text, _rq, hits in retrievals:
            for h in hits:
                tagged.append(_tag_retrieval(h, mention_text))
        deduped = dedupe_retrieved_concepts(tagged)
        diag.retrieval_queries = [rq for _m, rq, _h in retrievals]
        diag.retrieved_concepts = deduped

        # Compute baseline-resolver context so the reranker can see what
        # the deterministic resolver already settled on. The reranker
        # decides how (or whether) to use that information.
        baseline_terms = _baseline_resolved_terms(self._deps, term_mentions)
        inferred_classes = _inferred_class_terms(question, baseline_terms, snapshot)
        rerank_ctx = RerankContext(
            question=question,
            mentions=list(diag.mention_diagnostics),
            expected_kinds_by_mention={
                d.text: list(d.expected_kinds) for d in diag.mention_diagnostics
            },
            baseline_iris=[t.iri for t in baseline_terms if t.iri],
            inferred_class_terms=inferred_classes,
        )
        reranked = await self._reranker.rerank(
            question,
            deduped,
            limit=self._config.settings.retrieval_limit,
            context=rerank_ctx,
        )
        # Re-ranked output should also be unique by IRI even if the
        # reranker did not enforce that itself.
        reranked = _dedupe_reranked(reranked)
        diag.reranked_concepts = list(reranked)

        threshold = self._config.settings.score_threshold
        selected = [r for r in reranked if r.final_score >= threshold]
        selected = selected[: self._config.settings.selected_limit]
        diag.selected_concepts = list(selected)

        unresolved = [m for m, _rq, hits in retrievals if m != "<question>" and not hits]
        diag.unresolved_mentions = list(unresolved)

        return ConceptCandidatePack(
            question=question,
            mentions=[m.text for m in term_mentions],
            retrieved=deduped,
            reranked=list(reranked),
            selected=list(selected),
            unresolved_mentions=list(unresolved),
            diagnostics=_pack_diagnostics(retrievals, len(reranked), len(selected)),
        )

    async def _safe_retrieve(
        self, query: RetrievalQuery, diag: RagPlannerDiagnostics
    ) -> list[RetrievedConcept]:
        try:
            return await self._retriever.retrieve(query)
        except RetrievalError as exc:
            scope = query.mention or "<question>"
            diag.retrieval_errors.append(f"{scope}: {exc}")
            if scope != "<question>":
                diag.unresolved_mentions.append(scope)
            return []


# --- Tagging / deduplication ----------------------------------------------


def _tag_retrieval(retrieved: RetrievedConcept, mention: str) -> RetrievedConcept:
    """Annotate ``retrieved`` with the originating mention.

    Stamps lineage onto ``concept.metadata['rag_mention']`` and appends to
    ``concept.metadata['rag_mentions']`` so the candidate-pack renderer
    can group results by mention without re-running retrieval. Returns a
    copy so callers cannot mutate the underlying concept.
    """
    metadata = dict(retrieved.concept.metadata)
    metadata["rag_mention"] = mention
    mentions_list = list(metadata.get("rag_mentions") or [])
    if mention not in mentions_list:
        mentions_list.append(mention)
    metadata["rag_mentions"] = mentions_list
    concept = retrieved.concept.model_copy(update={"metadata": metadata})
    return retrieved.model_copy(update={"concept": concept})


def dedupe_retrieved_concepts(
    retrieved: Iterable[RetrievedConcept],
) -> list[RetrievedConcept]:
    """Collapse retrieved concepts by IRI.

    For each unique IRI, keep the highest-scoring entry and merge the
    ``rag_mentions`` lineage from the lower-scoring duplicates. The
    surviving entry's ``retrieval_rank`` is the smallest seen across
    duplicates so downstream metrics still treat the concept as
    "early in the result list".
    """
    by_iri: dict[str, RetrievedConcept] = {}
    for entry in retrieved:
        iri = entry.concept.iri
        if not iri:
            continue
        existing = by_iri.get(iri)
        if existing is None:
            by_iri[iri] = entry
            continue
        keep = existing if existing.score >= entry.score else entry
        loser = entry if keep is existing else existing
        merged_mentions = _merge_mentions(
            keep.concept.metadata.get("rag_mentions"),
            loser.concept.metadata.get("rag_mentions"),
        )
        metadata = dict(keep.concept.metadata)
        metadata["rag_mentions"] = merged_mentions
        if merged_mentions and "rag_mention" not in metadata:
            metadata["rag_mention"] = merged_mentions[0]
        concept = keep.concept.model_copy(update={"metadata": metadata})
        rank = min(keep.retrieval_rank, loser.retrieval_rank)
        matched = keep.matched_text or loser.matched_text
        by_iri[iri] = keep.model_copy(
            update={
                "concept": concept,
                "retrieval_rank": rank,
                "matched_text": matched,
            }
        )
    return list(by_iri.values())


def _dedupe_reranked(reranked: list[RerankedConcept]) -> list[RerankedConcept]:
    """Collapse reranked concepts by IRI, keeping the highest final score."""
    by_iri: dict[str, RerankedConcept] = {}
    for entry in reranked:
        iri = entry.concept.iri
        if not iri:
            continue
        existing = by_iri.get(iri)
        if existing is None or entry.final_score > existing.final_score:
            by_iri[iri] = entry
    # Re-sort by final score desc so the consumer still sees a ranked list.
    out = sorted(by_iri.values(), key=lambda r: -r.final_score)
    # Re-assign rank to reflect the new ordering.
    return [r.model_copy(update={"rank": i}) for i, r in enumerate(out)]


def _merge_mentions(a: Any, b: Any) -> list[str]:
    aa = list(a or [])
    bb = list(b or [])
    out: list[str] = []
    for m in (*aa, *bb):
        if isinstance(m, str) and m and m not in out:
            out.append(m)
    return out


# --- TermCandidate promotion ----------------------------------------------


def rag_concepts_to_term_candidates(
    selected: list[RerankedConcept],
    *,
    score_threshold: float,
    mention_to_kinds: dict[str, list[ConceptKind]] | None = None,
) -> list[TermCandidate]:
    """Convert eligible RAG selected concepts into ``TermCandidate`` objects.

    Filters out:

    - concepts whose ``final_score`` is below ``score_threshold``;
    - concepts whose ``kind`` is ``"unknown"``;
    - concepts whose ``kind`` conflicts with the originating mention's
      ``expected_kinds`` (when those are non-empty in ``mention_to_kinds``).

    The resulting ``TermCandidate.mention`` is taken from the concept's
    ``rag_mention`` metadata when present so the workflow's unresolved-
    mention removal works correctly.
    """
    kind_table = mention_to_kinds or {}
    out: list[TermCandidate] = []
    for item in selected:
        concept = item.concept
        if not concept.iri:
            continue
        if concept.kind == "unknown":
            continue
        if item.final_score < score_threshold:
            continue
        mention = (
            concept.metadata.get("rag_mention")
            if isinstance(concept.metadata.get("rag_mention"), str)
            else None
        )
        if mention is not None:
            expected_kinds = kind_table.get(mention, [])
            if expected_kinds and concept.kind not in expected_kinds:
                continue
        candidate_kind = concept.kind if concept.kind != "datatype" else "unknown"
        out.append(
            TermCandidate(
                mention=mention or "",
                iri=concept.iri,
                prefixed_name=concept.prefixed_name,
                kind=candidate_kind,
                label=concept.label,
                score=item.final_score,
                explanation=(
                    f"rag (final={item.final_score:.2f}, "
                    f"retrieval={item.retrieval_score:.2f}, "
                    f"rerank+={item.rerank_score:.2f})"
                ),
            )
        )
    return out


# --- Helpers --------------------------------------------------------------


def _kinds_for_term_mention(mention: TermMention, snapshot: SchemaSnapshot) -> list[ConceptKind]:
    """Return the kind hint to send into a retrieval query.

    Uses the mention's own ``expected_kinds`` when populated. Falls back to
    a small heuristic over the schema snapshot only when the extractor did
    not assign any kinds (e.g. a freeform capitalized token).
    """
    if mention.expected_kinds:
        valid = {"class", "property", "individual", "graph", "datatype", "unknown"}
        return [k for k in mention.expected_kinds if k in valid]  # type: ignore[misc]
    return _kinds_for_mention(mention.text, snapshot)


def _kinds_for_mention(text: str, snapshot: SchemaSnapshot) -> list[ConceptKind]:
    """Best-effort kind hint for a bare mention string.

    Used only as a fallback when the upstream extractor did not produce
    ``expected_kinds`` for the mention.
    """
    out: list[ConceptKind] = []
    lower = text.lower()
    for c in snapshot.classes:
        if c.label and c.label.lower() == lower:
            out.append("class")
    for p in snapshot.properties:
        if p.label and p.label.lower() == lower:
            out.append("property")
    for ind in snapshot.individuals:
        if ind.label and ind.label.lower() == lower:
            out.append("individual")
    if not out:
        if " " in text or text.endswith(("ing", "ed")):
            out = ["property"]
        elif text and text[0].isupper():
            out = ["class", "individual"]
    return out


def _baseline_resolved_terms(deps: PlannerDeps, mentions: list[TermMention]) -> list[TermCandidate]:
    """Run the deterministic resolver to anticipate baseline-selected terms.

    Used purely to feed the reranker's :class:`RerankContext` — the
    authoritative resolution still happens inside
    :func:`evals.agent.run_planner_workflow`. We deliberately accept the
    cost of resolving twice in exchange for a more informed reranker.
    """
    baseline: list[TermCandidate] = []
    for m in mentions:
        kinds_typed: Any = list(m.expected_kinds) if m.expected_kinds else None
        result = deps.resolver.resolve([m.text], expected_kinds=kinds_typed)
        for c in result.candidates:
            if c.kind != "unknown":
                baseline.append(c)
                break
    return baseline


def _inferred_class_terms(
    question: str,
    baseline: list[TermCandidate],
    snapshot: SchemaSnapshot,
) -> list[str]:
    """Class IRIs / labels the reranker can use for domain/range matching."""
    seen: set[str] = set()
    out: list[str] = []
    for c in baseline:
        if c.kind != "class" or not c.iri:
            continue
        if c.iri in seen:
            continue
        seen.add(c.iri)
        out.append(c.iri)
        if c.label:
            out.append(c.label)
        if c.prefixed_name:
            out.append(c.prefixed_name)
    # Also accept literal class labels appearing in the question text.
    ql = question.lower()
    for cls in snapshot.classes:
        if cls.label and cls.label.lower() in ql and cls.iri not in seen:
            seen.add(cls.iri)
            out.append(cls.iri)
            out.append(cls.label)
            if cls.prefixed_name:
                out.append(cls.prefixed_name)
    return out


def _pack_diagnostics(
    retrievals: list[tuple[str, RetrievalQuery, list[RetrievedConcept]]],
    reranked_count: int,
    selected_count: int,
) -> list[str]:
    """Short status lines that surface in the prompt's diagnostics block."""
    lines = []
    for mention, _q, hits in retrievals:
        lines.append(f"{mention}: {len(hits)} retrieved")
    lines.append(f"reranked: {reranked_count}; selected: {selected_count}")
    return lines


def _run_in_running_loop(coro: Any) -> Any:
    """Run ``coro`` to completion when an event loop is already running."""
    import threading

    result_box: dict[str, Any] = {}
    exc_box: dict[str, BaseException] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            result_box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:
            exc_box["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_runner)
    t.start()
    t.join()
    if "error" in exc_box:
        raise exc_box["error"]
    return result_box["value"]


# --- Build helpers ---------------------------------------------------------


def build_rag_planner(
    deps: PlannerDeps,
    *,
    retriever: OntologyRetriever,
    reranker: ConceptReranker | None = None,
    generate: Callable[[str], PlannerOutput],
    config: RagPlannerConfig,
) -> RagPlannerWrapper:
    """Construct a :class:`RagPlannerWrapper` ready for the eval runner."""
    reranker_obj = reranker if reranker is not None else NoopReranker()
    return RagPlannerWrapper(
        deps=deps,
        retriever=retriever,
        reranker=reranker_obj,
        generate=generate,
        config=config,
    )


def selected_concepts_iris(selected: list[RerankedConcept]) -> set[str]:
    """Convenience helper used by metrics to compare expected/observed IRIs."""
    return {item.concept.iri for item in selected}


def selected_concept_by_kind(
    selected: list[RerankedConcept], kind: ConceptKind
) -> list[OntologyConcept]:
    """Return selected concepts of a given kind (used in metrics + tests)."""
    return [item.concept for item in selected if item.concept.kind == kind]


__all__ = [
    "RAG_GUIDANCE",
    "RagPlannerConfig",
    "RagPlannerWrapper",
    "build_rag_planner",
    "dedupe_retrieved_concepts",
    "rag_concepts_to_term_candidates",
    "selected_concept_by_kind",
    "selected_concepts_iris",
]
