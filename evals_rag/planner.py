"""RAG-augmented planner.

Wraps the existing planner workflow (extract → resolve → generate →
validate → repair) with a retrieval/re-rank step that produces a
:class:`ConceptCandidatePack`. The pack is rendered via
:func:`evals_rag.prompts.render_candidate_pack` and prepended to the prompt
the underlying LLM sees, so the agent gets schema-anchored hints without
the validator/renderer/policy contracts changing.

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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from evals.agent import (
    PlannerDeps,
    PlannerDiagnostics,
    PlannerOutput,
    run_planner_workflow,
)
from evals.mention_extractor import extract_mentions
from evals_rag.config import RagSettings
from evals_rag.models import (
    ConceptCandidatePack,
    ConceptKind,
    OntologyConcept,
    RagPlannerDiagnostics,
    RerankedConcept,
    RetrievalQuery,
    RetrievedConcept,
)
from evals_rag.prompts import RAG_GUIDANCE, render_candidate_pack
from evals_rag.reranking import ConceptReranker, NoopReranker
from evals_rag.retrieval import OntologyRetriever, RetrievalError
from graph_mcp.graph.term_resolver import TermResolver


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

        def generate_with_rag(prompt_text: str) -> PlannerOutput:
            full = f"{prompt_text}\n\n{candidate_block}"
            return self._generate(full)

        output, workflow_diag = run_planner_workflow(
            self._deps, question, generate=generate_with_rag
        )
        rag_diag.planner_diagnostics = workflow_diag.model_dump()
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
        mentions = [m.text for m in extract_mentions(question, snapshot)]
        diag.mentions = list(mentions)

        retrievals: list[tuple[str, RetrievalQuery, list[RetrievedConcept]]] = []
        if self._config.per_mention and mentions:
            for mention_text in mentions:
                kinds = _kinds_for_mention(mention_text, snapshot)
                rq = RetrievalQuery(
                    question=question,
                    mention=mention_text,
                    expected_kinds=kinds,
                    limit=self._config.settings.retrieval_limit,
                )
                hits = await self._safe_retrieve(rq, diag)
                retrievals.append((mention_text, rq, hits))
        if self._config.include_question_retrieval or not retrievals:
            rq = RetrievalQuery(
                question=question,
                mention=None,
                expected_kinds=[],
                limit=self._config.settings.retrieval_limit,
            )
            hits = await self._safe_retrieve(rq, diag)
            retrievals.append(("<question>", rq, hits))

        all_retrieved: list[RetrievedConcept] = []
        for mention_text, _rq, hits in retrievals:
            for h in hits:
                all_retrieved.append(_tag_retrieval(h, mention_text))
        diag.retrieval_queries = [rq for _m, rq, _h in retrievals]
        diag.retrieved_concepts = list(all_retrieved)

        # Re-rank against the original question text, not the per-mention
        # text: the rerank heuristics need to see the full sentence to
        # judge domain/range overlap.
        reranked = await self._reranker.rerank(
            question,
            all_retrieved,
            limit=self._config.settings.retrieval_limit,
        )
        diag.reranked_concepts = list(reranked)

        threshold = self._config.settings.score_threshold
        selected = [r for r in reranked if r.final_score >= threshold]
        selected = selected[: self._config.settings.selected_limit]
        diag.selected_concepts = list(selected)

        unresolved = [m for m, _rq, hits in retrievals if m != "<question>" and not hits]
        diag.unresolved_mentions = list(unresolved)

        return ConceptCandidatePack(
            question=question,
            mentions=list(mentions),
            retrieved=all_retrieved,
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
            diag.unresolved_mentions.append(query.mention or "<question>")
            diag.planner_diagnostics.setdefault("retrieval_errors", []).append(  # type: ignore[union-attr]
                f"{query.mention or '<question>'}: {exc}"
            )
            return []


def _tag_retrieval(retrieved: RetrievedConcept, mention: str) -> RetrievedConcept:
    """Annotate ``retrieved`` with the originating mention.

    Stamps the lineage onto ``concept.metadata['rag_mention']`` so the
    candidate-pack renderer can group results back by mention without
    re-running retrieval. Returns a copy so callers cannot mutate the
    underlying concept.
    """
    concept = retrieved.concept.model_copy(update={"metadata": dict(retrieved.concept.metadata)})
    concept.metadata["rag_mention"] = mention
    return retrieved.model_copy(update={"concept": concept})


def _kinds_for_mention(text: str, snapshot: Any) -> list[ConceptKind]:
    """Best-effort kind hint based on the schema and the mention shape."""
    out: list[ConceptKind] = []
    lower = text.lower()
    # If the mention exactly matches a known label, prefer that kind.
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
        # Verb-shaped phrases lean property; capitalized tokens lean class /
        # individual; everything else stays unconstrained.
        if " " in text or text.endswith(("ing", "ed")):
            out = ["property"]
        elif text and text[0].isupper():
            out = ["class", "individual"]
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
    """Run ``coro`` to completion when an event loop is already running.

    The eval runner calls planners via ``asyncio.to_thread`` so ``plan`` is
    invoked from a worker thread that does not have its own loop. This
    helper exists for the rare case where a caller invokes ``plan`` from
    inside a coroutine — we drop into a fresh nested loop in a thread so
    the candidate pack still gets built. Mostly defensive; production code
    paths use the thread-pool route.
    """
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
    """Construct a :class:`RagPlannerWrapper` ready for the eval runner.

    ``generate`` takes the augmented prompt (system context + RAG block)
    and returns a :class:`PlannerOutput`. Production callers wire this to a
    PydanticAI agent; tests pass a plain Python callable.
    """
    reranker_obj = reranker if reranker is not None else NoopReranker()
    # Optionally inject the RAG guidance into the system prompt builder if
    # the deps object supports it. Otherwise the candidate-pack block alone
    # is enough — the LLM still sees the RAG_GUIDANCE via the prompt builder
    # in :func:`evals_rag.runner.build_rag_pydantic_ai_planner`.
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


# Re-export the guidance string so callers building custom prompts can splice
# it in without importing from evals_rag.prompts directly.
__all__ = [
    "RAG_GUIDANCE",
    "RagPlannerConfig",
    "RagPlannerWrapper",
    "build_rag_planner",
    "selected_concept_by_kind",
    "selected_concepts_iris",
]
