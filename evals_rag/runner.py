"""RAG eval runner CLI.

Run with::

    python -m evals_rag.runner --planner rag --cases evals/golden_cases.yaml \\
        --retriever mock --reranker heuristic --report-dir reports/rag-mock

The runner reuses :func:`evals.runner.build_components` and
:func:`evals.runner.run_one` to keep the validator/renderer/executor logic
identical between the RAG and non-RAG harnesses. The only thing that
changes is the planner: instead of the deterministic baseline or the
direct PydanticAI planner, the runner constructs a
:class:`evals_rag.planner.RagPlannerWrapper` that injects retrieved
candidates into the LLM prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.agent import (
    DeterministicPlanner,
    Planner,
    PlannerDeps,
    PlannerOutput,
)
from evals.models import GoldenCase
from evals.runner import (
    PlannerComponents,
    build_components,
    load_cases,
    run_one,
)
from evals_rag.config import RagSettings
from evals_rag.fixtures import concepts_from_snapshot
from evals_rag.metrics import RagCaseResult, compute_rag_metrics
from evals_rag.models import ConceptKind, RagPlannerDiagnostics
from evals_rag.planner import RagPlannerConfig, build_rag_planner
from evals_rag.report import RagEvaluationReport, metrics_to_json, render_rag_report
from evals_rag.reranking import (
    ConceptReranker,
    HeuristicReranker,
    ModelReranker,
    NoopReranker,
)
from evals_rag.retrieval import (
    EmbeddingProvider,
    MockOntologyRetriever,
    OntologyRetriever,
    QdrantOntologyRetriever,
)

_DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "golden_cases.yaml"
_DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "evals" / "sample_dataset.trig"


# --- Retriever / reranker factories ---------------------------------------


@dataclass
class _RetrieverChoice:
    """Resolved retriever + the reason the runner picked it (for the report)."""

    retriever: OntologyRetriever
    description: str


def build_retriever(
    name: str,
    *,
    components: PlannerComponents,
    settings: RagSettings,
    embedding_provider: EmbeddingProvider | None = None,
) -> _RetrieverChoice:
    """Construct an :class:`OntologyRetriever` by short name.

    ``mock`` is the default and uses :func:`concepts_from_snapshot`. ``qdrant``
    requires an :class:`EmbeddingProvider`; without one the
    :class:`MissingEmbeddingProvider` sentinel will fail closed at first
    call (intentionally — the vectorizer is not yet implemented).
    """
    if name == "mock":
        snap = components.schema_provider.snapshot()
        concepts = concepts_from_snapshot(snap)
        return _RetrieverChoice(
            retriever=MockOntologyRetriever(concepts),
            description=f"mock ({len(concepts)} concepts from local schema)",
        )
    if name == "qdrant":
        retriever = QdrantOntologyRetriever(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            api_key=settings.qdrant_api_key,
            embedding_provider=embedding_provider,
            score_threshold=settings.score_threshold,
        )
        return _RetrieverChoice(
            retriever=retriever,
            description=f"qdrant ({settings.qdrant_url}, collection={settings.qdrant_collection})",
        )
    raise ValueError(f"unknown retriever: {name!r}; expected 'mock' or 'qdrant'")


def build_reranker(
    name: str,
    *,
    expected_kinds: list[ConceptKind] | None = None,
) -> ConceptReranker:
    """Construct a reranker by short name."""
    if name == "noop":
        return NoopReranker()
    if name == "heuristic":
        return HeuristicReranker(expected_kinds=expected_kinds)
    if name == "model":
        return ModelReranker()
    raise ValueError(f"unknown reranker: {name!r}; expected 'noop', 'heuristic', or 'model'")


# --- Planner construction --------------------------------------------------


def build_rag_planner_for_run(
    *,
    components: PlannerComponents,
    retriever: OntologyRetriever,
    reranker: ConceptReranker,
    config: RagPlannerConfig,
    generate: Callable[[str], PlannerOutput],
) -> Planner:
    """Wrap the underlying generate callable with a RAG cycle."""
    deps = PlannerDeps(
        schema=components.schema_provider,
        resolver=components.resolver,
        validator=components.validator,
        renderer=components.renderer,
        policy=components.policy,
        max_repair_attempts=2,
    )
    return build_rag_planner(
        deps,
        retriever=retriever,
        reranker=reranker,
        generate=generate,
        config=config,
    )


def _generate_for_baseline(planner: DeterministicPlanner) -> Callable[[str], PlannerOutput]:
    """Adapt a :class:`DeterministicPlanner` into a ``generate`` callable.

    Used when ``--planner rag`` is run without an LLM backend: the
    retrieval/rerank cycle still runs and is reflected in the report, but
    the underlying plan is produced by the deterministic baseline so the
    runner stays usable in CI.
    """

    def _generate(prompt_text: str) -> PlannerOutput:
        question = _strip_appended_blocks(prompt_text)
        return planner.plan(question)

    return _generate


def _strip_appended_blocks(prompt_text: str) -> str:
    """Recover the original question from a prompt that has blocks appended."""
    head = prompt_text.split("\n\n## ", 1)[0]
    return head.split("\n\n## ", 1)[0].strip() or prompt_text


# --- Main run loop ---------------------------------------------------------


async def run_rag(
    cases: list[GoldenCase],
    planner: Planner,
    *,
    components: PlannerComponents,
    execute: bool = True,
) -> list[RagCaseResult]:
    """Run every case through ``planner`` and collect RAG diagnostics."""
    rag_results: list[RagCaseResult] = []
    for case in cases:
        result = await run_one(
            case,
            planner,
            validator=components.validator,
            renderer=components.renderer,
            endpoint=components.endpoint,
            policy=components.policy,
            execute=execute,
            semantic_repair_attempts=0,
        )
        rag_diag = getattr(planner, "last_rag_diagnostics", None) or RagPlannerDiagnostics()
        rag_results.append(RagCaseResult(case=case, result=result, rag_diagnostics=rag_diag))
    return rag_results


def _load_baseline(path: str | None) -> dict[str, float] | None:
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    # Accept both a bare metrics dict and the full report.json shape.
    if isinstance(raw, dict) and "metrics" in raw and isinstance(raw["metrics"], dict):
        raw = raw["metrics"]
    if not isinstance(raw, dict):
        return None
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    components = asyncio.run(build_components(graph_path=Path(args.graph)))

    settings = RagSettings.from_env()
    retriever_choice = build_retriever(args.retriever, components=components, settings=settings)
    reranker = build_reranker(args.reranker)

    config = RagPlannerConfig(
        settings=settings,
        per_mention=not args.full_question_retrieval,
        include_question_retrieval=args.include_question_retrieval,
    )

    if args.planner == "rag":
        if args.azure or args.model:
            generate = _build_pydantic_ai_generate(
                model=args.model, azure=args.azure, azure_endpoint=args.azure_endpoint
            )
        else:
            generate = _generate_for_baseline(DeterministicPlanner())
        planner: Planner = build_rag_planner_for_run(
            components=components,
            retriever=retriever_choice.retriever,
            reranker=reranker,
            config=config,
            generate=generate,
        )
    elif args.planner == "deterministic":
        planner = DeterministicPlanner()
    else:
        raise ValueError(f"unknown planner: {args.planner}")

    rag_results = asyncio.run(
        run_rag(cases, planner, components=components, execute=not args.no_execute)
    )

    baseline = _load_baseline(args.baseline_report) if args.compare_baseline else None
    metrics = compute_rag_metrics(rag_results, baseline_metrics=baseline)

    report = RagEvaluationReport(
        rag_results=rag_results,
        metrics=metrics,
        baseline_metrics=baseline,
        runner_args={
            "planner": args.planner,
            "retriever": f"{args.retriever} ({retriever_choice.description})",
            "reranker": args.reranker,
            "cases": str(args.cases),
            "graph": str(args.graph),
            "executed": str(not args.no_execute),
            "baseline_report": str(args.baseline_report) if args.baseline_report else "",
        },
    )

    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metrics.json").write_text(metrics_to_json(metrics))
        (out_dir / "report.md").write_text(render_rag_report(report))
        (out_dir / "report.json").write_text(_serialize_report_json(report))

    print(metrics_to_json(metrics))

    return 0 if all(not entry.result.failures for entry in rag_results) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graph-mcp-rag-evals")
    parser.add_argument("--planner", default="rag", choices=("rag", "deterministic"))
    parser.add_argument("--retriever", default="mock", choices=("mock", "qdrant"))
    parser.add_argument("--reranker", default="heuristic", choices=("noop", "heuristic", "model"))
    parser.add_argument("--cases", default=str(_DEFAULT_CASES))
    parser.add_argument("--graph", default=str(_DEFAULT_GRAPH))
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--report-dir", default=None)
    parser.add_argument(
        "--include-question-retrieval",
        action="store_true",
        help="Add a whole-question retrieval call alongside per-mention calls.",
    )
    parser.add_argument(
        "--full-question-retrieval",
        action="store_true",
        help="Skip per-mention retrieval and run a single full-question call.",
    )
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Load --baseline-report and emit *_delta_vs_baseline metrics.",
    )
    parser.add_argument("--baseline-report", default=None)
    parser.add_argument(
        "--azure",
        action="store_true",
        help="Use Azure OpenAI as the LLM backend (requires AZURE_OPENAI_* env).",
    )
    parser.add_argument("--azure-endpoint", default=None)
    parser.add_argument("--model", default=None)
    return parser


def _build_pydantic_ai_generate(
    *, model: str | None, azure: bool, azure_endpoint: str | None
) -> Callable[[str], PlannerOutput]:
    """Build a generate callable backed by PydanticAI.

    Imported lazily so the runner does not require ``pydantic-ai`` for
    mock-retriever runs. Reuses :func:`evals.runner._build_azure_openai_model`
    indirectly via the public :mod:`evals.runner` factory.
    """
    try:
        from pydantic_ai import Agent
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "pydantic-ai is required for --azure / --model; "
            "install with `pip install graph-mcp[ai]`"
        ) from exc
    from evals.agent import (
        ClarificationOutput,
        PlannedOutput,
        PydanticAIPlannerConfig,
        RefusedOutput,
        _build_full_system_prompt,
    )
    from evals_rag.prompts import RAG_GUIDANCE

    if azure:
        from evals.runner import _build_azure_openai_model

        model_obj: Any = _build_azure_openai_model(model, endpoint=azure_endpoint)
    else:
        if not model:
            raise ValueError("rag planner requires --model or --azure")
        model_obj = model

    cfg = PydanticAIPlannerConfig(model=model_obj)
    base_system = _build_full_system_prompt(cfg)
    system = base_system + "\n\n" + RAG_GUIDANCE
    output_type: Any = PlannedOutput | ClarificationOutput | RefusedOutput
    agent: Any = Agent(model=model_obj, output_type=output_type, system_prompt=system)

    def _generate(prompt_text: str) -> PlannerOutput:
        return agent.run_sync(prompt_text).output  # type: ignore[no-any-return]

    return _generate


def _serialize_report_json(report: RagEvaluationReport) -> str:
    """Serialize the full report as JSON for downstream tools.

    Pydantic models are dumped via ``model_dump``; everything else is
    coerced through ``json.dumps`` with ``default=str``.
    """
    payload = {
        "metrics": report.metrics,
        "baseline_metrics": report.baseline_metrics or {},
        "runner_args": report.runner_args or {},
        "cases": [
            {
                "case_id": entry.case.id,
                "question": entry.case.question,
                "result": entry.result.model_dump(),
                "rag_diagnostics": entry.rag_diagnostics.model_dump(),
            }
            for entry in report.rag_results
        ],
    }
    return json.dumps(payload, indent=2, default=str, sort_keys=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
