"""Tests for first-class promotion of RAG candidates into resolved terms.

Covers the merge logic in :func:`evals.agent._merge_supplemental_candidates`
plus the end-to-end promotion through :class:`evals_rag.planner.RagPlannerWrapper`.
The tests use a stub retriever and a stub generate callable so no LLM or
vector database is involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from evals.agent import (
    PlannerDeps,
    PlannerDiagnostics,
    _merge_supplemental_candidates,
    run_planner_workflow,
)
from evals.models import PlannedOutput
from evals.runner import build_components
from evals_rag.config import RagSettings
from evals_rag.models import OntologyConcept, RetrievalQuery, RetrievedConcept
from evals_rag.planner import (
    RagPlannerConfig,
    build_rag_planner,
    rag_concepts_to_term_candidates,
)
from evals_rag.reranking import HeuristicReranker, NoopReranker
from evals_rag.retrieval import OntologyRetriever
from graph_mcp.graph.term_resolver import TermCandidate
from graph_mcp.models import (
    Prefix,
    PrefixedName,
    Projection,
    SelectPlan,
    TriplePattern,
    Var,
)

_GRAPH = Path(__file__).resolve().parent.parent.parent / "evals" / "sample_dataset.trig"


def _term(
    iri: str,
    *,
    mention: str = "x",
    kind: str = "class",
    label: str | None = None,
    score: float = 0.9,
) -> TermCandidate:
    return TermCandidate(
        mention=mention,
        iri=iri,
        prefixed_name=None,
        kind=kind,
        label=label,
        score=score,
        explanation="test",
    )


def test_merge_promotes_new_iri_and_clears_unresolved():
    baseline = [_term("ex:A", mention="a")]
    unresolved = ["b"]
    supplemental = [_term("ex:B", mention="b", kind="property")]
    merged, promoted, remaining = _merge_supplemental_candidates(
        baseline, unresolved, [], supplemental
    )
    assert [c.iri for c in merged] == ["ex:A", "ex:B"]
    assert [c.iri for c in promoted] == ["ex:B"]
    assert remaining == []


def test_merge_dedupes_against_existing_iri():
    baseline = [_term("ex:A", mention="a")]
    supplemental = [_term("ex:A", mention="a-rag")]
    merged, promoted, remaining = _merge_supplemental_candidates(baseline, [], [], supplemental)
    assert [c.iri for c in merged] == ["ex:A"]
    # The supplemental entry is not added to the merged list (baseline wins),
    # but it is recorded in promoted so reports can show "RAG agreed".
    assert [c.iri for c in promoted] == ["ex:A"]
    assert remaining == []


def test_merge_skips_ambiguous_mentions():
    baseline: list[TermCandidate] = []
    ambiguous = ["foo"]
    supplemental = [_term("ex:Foo", mention="foo")]
    merged, promoted, remaining = _merge_supplemental_candidates(
        baseline, ["foo"], ambiguous, supplemental
    )
    assert merged == []
    assert promoted == []
    assert remaining == ["foo"]


def test_workflow_records_baseline_and_rag_terms_separately():
    deps = _build_minimal_deps()
    diag = PlannerDiagnostics()

    def generate(prompt: str):
        # Return a trivial PlannedOutput; the test only inspects diagnostics.
        return PlannedOutput(
            question="who works for Acme?",
            plan=_minimal_plan(),
            confidence=0.9,
        )

    supplemental = [_term("http://example.org/RagOnly", mention="ghost", kind="class")]
    output, dout = run_planner_workflow(
        deps,
        "Who works for Acme?",
        generate=generate,
        diagnostics=diag,
        supplemental_candidates=supplemental,
    )
    assert isinstance(output, PlannedOutput)
    # Baseline-only IRIs must not include the RAG-only IRI.
    baseline_iris = {t.iri for t in dout.baseline_selected_terms}
    assert "http://example.org/RagOnly" not in baseline_iris
    # The RAG IRI is in the rag_selected_terms list and merged into selected_terms.
    rag_iris = {t.iri for t in dout.rag_selected_terms}
    assert "http://example.org/RagOnly" in rag_iris
    merged_iris = {t.iri for t in dout.selected_terms}
    assert "http://example.org/RagOnly" in merged_iris


def test_rag_planner_promotes_terms_through_workflow():
    components = asyncio.run(build_components(graph_path=_GRAPH))
    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )

    # Stub retriever returns a synthetic RAG-only concept anchored to the
    # mention "ghost", which the deterministic resolver cannot find.
    class StubRetriever:
        async def retrieve(self, query: RetrievalQuery) -> list[RetrievedConcept]:
            if (query.mention or "").lower() != "ghost":
                return []
            return [
                RetrievedConcept(
                    concept=OntologyConcept(
                        iri="http://example.org/Ghost",
                        prefixed_name="ex:Ghost",
                        label="Ghost",
                        kind="class",
                    ),
                    score=0.99,
                    retrieval_rank=0,
                    retrieval_source="mock",
                )
            ]

    captured: list[str] = []

    def generate(prompt: str):
        captured.append(prompt)
        return PlannedOutput(question="?", plan=_minimal_plan(), confidence=0.9)

    # Force the wrapper to retrieve for "ghost" by using a question containing it.
    planner = build_rag_planner(
        deps,
        retriever=StubRetriever(),
        reranker=NoopReranker(),
        generate=generate,
        config=RagPlannerConfig(settings=RagSettings()),
    )
    planner.plan("Show me every Ghost")
    rag_diag = planner.last_rag_diagnostics
    assert rag_diag is not None
    assert "http://example.org/Ghost" in rag_diag.promoted_term_iris
    # The Resolved-terms block in the prompt the generate callable saw must
    # list the RAG-promoted concept.
    assert any("ex:Ghost" in p or "http://example.org/Ghost" in p for p in captured)


def test_kind_conflict_filters_out_supplemental_candidate():
    # The candidate is kind=individual but the mention only expects "class".
    pack_selected = []
    from evals_rag.models import RerankedConcept

    pack_selected.append(
        RerankedConcept(
            concept=OntologyConcept(
                iri="http://example.org/Bogus",
                kind="individual",
                metadata={"rag_mention": "thing"},
            ),
            retrieval_score=0.9,
            rerank_score=0.0,
            final_score=0.9,
            rank=0,
        )
    )
    candidates = rag_concepts_to_term_candidates(
        pack_selected,
        score_threshold=0.0,
        mention_to_kinds={"thing": ["class"]},
    )
    assert candidates == []


def test_score_threshold_drops_low_scoring_supplemental():
    from evals_rag.models import RerankedConcept

    pack_selected = [
        RerankedConcept(
            concept=OntologyConcept(
                iri="http://example.org/Low",
                kind="class",
                metadata={"rag_mention": "x"},
            ),
            retrieval_score=0.05,
            rerank_score=0.0,
            final_score=0.05,
            rank=0,
        )
    ]
    candidates = rag_concepts_to_term_candidates(
        pack_selected, score_threshold=0.5, mention_to_kinds={}
    )
    assert candidates == []


def _build_minimal_deps() -> PlannerDeps:
    components = asyncio.run(build_components(graph_path=_GRAPH))
    return PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
    )


def _minimal_plan() -> SelectPlan:
    return SelectPlan(
        prefixes=[Prefix(prefix="ex", iri="http://example.org/")],
        projection=[Projection(var=Var(name="p"))],
        where=[
            TriplePattern(
                subject=Var(name="p"),
                predicate=PrefixedName(prefix="ex", local="worksFor"),
                object=PrefixedName(prefix="ex", local="Acme"),
            )
        ],
        limit=10,
    )


# Silence unused-import warnings for OntologyRetriever (kept for typing
# clarity in StubRetriever).
_ = OntologyRetriever
_ = HeuristicReranker
