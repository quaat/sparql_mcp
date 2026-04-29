"""RAG eval runner CLI.

Run with::

    python -m evals_rag.runner --planner rag --cases evals/golden_cases.yaml \\
        --retriever mock --reranker heuristic --report-dir reports/rag-mock

The runner reuses :func:`evals.runner.build_components` and
:func:`evals.runner.run_one` to keep the validator/renderer/executor logic
identical between the RAG and non-RAG harnesses.
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
from evals_rag.config import RagConfigError, RagSettings
from evals_rag.fixtures import concepts_from_snapshot
from evals_rag.metrics import RagCaseResult, compute_rag_metrics
from evals_rag.models import ConceptKind, RagPlannerDiagnostics
from evals_rag.planner import RagPlannerConfig, build_rag_planner
from evals_rag.report import RagEvaluationReport, metrics_to_json, render_rag_report
from evals_rag.reranking import (
    ConceptReranker,
    HeuristicReranker,
    NoopReranker,
)
from evals_rag.retrieval import (
    EmbeddingProvider,
    FakeEmbeddingProvider,
    MissingEmbeddingProvider,
    MockOntologyRetriever,
    OntologyRetriever,
    QdrantOntologyRetriever,
)

_DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "golden_cases.yaml"
_DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "evals" / "sample_dataset.trig"


# --- Retriever / reranker / embedding factories ----------------------------


@dataclass
class _RetrieverChoice:
    retriever: OntologyRetriever
    description: str


def build_embedding_provider(name: str) -> EmbeddingProvider | None:
    """Build an :class:`EmbeddingProvider` by short name.

    Returns ``None`` when no provider is required (the mock retriever
    does not consume one). The CLI rejects ``qdrant + missing`` before
    reaching this layer so the runner does not crash mid-eval.
    """
    if name == "missing":
        return MissingEmbeddingProvider()
    if name == "fake":
        return FakeEmbeddingProvider()
    raise ValueError(f"unknown embedding provider: {name!r}; expected 'missing' or 'fake'")


def build_retriever(
    name: str,
    *,
    components: PlannerComponents,
    settings: RagSettings,
    embedding_provider: EmbeddingProvider | None = None,
) -> _RetrieverChoice:
    """Construct an :class:`OntologyRetriever` by short name."""
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
    """Construct a reranker by short name.

    ``model`` is reserved and rejected at the CLI layer; this function
    raises if it is invoked anyway so internal callers cannot accidentally
    instantiate a placeholder that crashes mid-run.
    """
    if name == "noop":
        return NoopReranker()
    if name == "heuristic":
        return HeuristicReranker(expected_kinds=expected_kinds)
    if name == "model":
        raise ValueError(
            "--reranker model is reserved for a future implementation and is not "
            "available yet. Use 'noop' or 'heuristic'."
        )
    raise ValueError(f"unknown reranker: {name!r}; expected 'noop' or 'heuristic'")


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
    """Adapt a :class:`DeterministicPlanner` into a ``generate`` callable."""

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
    if isinstance(raw, dict) and "metrics" in raw and isinstance(raw["metrics"], dict):
        raw = raw["metrics"]
    if not isinstance(raw, dict):
        return None
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}


# --- Quality-gate thresholds ----------------------------------------------


@dataclass
class _ThresholdSpec:
    metric: str
    minimum: float | None = None
    maximum: float | None = None


def _thresholds_from_args(args: argparse.Namespace) -> list[_ThresholdSpec]:
    out: list[_ThresholdSpec] = []
    if args.min_case_pass_rate is not None:
        out.append(_ThresholdSpec("case_pass_rate", minimum=args.min_case_pass_rate))
    if args.min_selected_case_recall is not None:
        out.append(_ThresholdSpec("selected_case_recall", minimum=args.min_selected_case_recall))
    if args.min_retrieval_case_recall_at_k is not None:
        out.append(
            _ThresholdSpec(
                f"retrieval_case_recall_at_{args.k}",
                minimum=args.min_retrieval_case_recall_at_k,
            )
        )
    if args.min_selected_precision is not None:
        out.append(_ThresholdSpec("selected_precision", minimum=args.min_selected_precision))
    if args.max_unresolved_mention_rate is not None:
        out.append(
            _ThresholdSpec("unresolved_mention_rate", maximum=args.max_unresolved_mention_rate)
        )
    if args.max_safety_violations is not None:
        out.append(_ThresholdSpec("safety_violation_count", maximum=args.max_safety_violations))
    return out


def _check_thresholds(metrics: dict[str, float], thresholds: list[_ThresholdSpec]) -> list[str]:
    failures: list[str] = []
    for spec in thresholds:
        if spec.metric not in metrics:
            continue
        actual = metrics[spec.metric]
        if spec.minimum is not None and actual < spec.minimum:
            failures.append(f"{spec.metric}: expected >= {spec.minimum:.3f}, got {actual:.3f}")
        if spec.maximum is not None and actual > spec.maximum:
            failures.append(f"{spec.metric}: expected <= {spec.maximum:.3f}, got {actual:.3f}")
    return failures


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.reranker == "model":
        parser.error(
            "--reranker model is reserved for a future implementation and is not "
            "available yet. Use --reranker heuristic or --reranker noop."
        )

    cases = load_cases(args.cases)

    try:
        settings = RagSettings.from_env()
    except RagConfigError as exc:
        print(f"RAG configuration error: {exc}", file=sys.stderr)
        return 2

    embedding_provider: EmbeddingProvider | None = None
    if args.retriever == "qdrant":
        provider_name = args.embedding_provider or "missing"
        if provider_name == "missing":
            print(
                "Qdrant retrieval requires an embedding provider. "
                "Use --embedding-provider fake for smoke tests or configure a "
                "real provider when the vectorizer lands.",
                file=sys.stderr,
            )
            return 2
        embedding_provider = build_embedding_provider(provider_name)

    components = asyncio.run(build_components(graph_path=Path(args.graph)))
    retriever_choice = build_retriever(
        args.retriever,
        components=components,
        settings=settings,
        embedding_provider=embedding_provider,
    )
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
    metrics = compute_rag_metrics(rag_results, baseline_metrics=baseline, k=args.k)

    thresholds = _thresholds_from_args(args)
    threshold_failures = _check_thresholds(metrics, thresholds)

    report = RagEvaluationReport(
        rag_results=rag_results,
        metrics=metrics,
        baseline_metrics=baseline,
        runner_args={
            "planner": args.planner,
            "retriever": f"{args.retriever} ({retriever_choice.description})",
            "reranker": args.reranker,
            "embedding_provider": args.embedding_provider or "n/a",
            "cases": str(args.cases),
            "graph": str(args.graph),
            "executed": str(not args.no_execute),
            "baseline_report": str(args.baseline_report) if args.baseline_report else "",
            "k": str(args.k),
            "fail_below_threshold": str(args.fail_below_threshold),
        },
        threshold_failures=threshold_failures,
    )

    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metrics.json").write_text(metrics_to_json(metrics))
        (out_dir / "report.md").write_text(render_rag_report(report))
        (out_dir / "report.json").write_text(_serialize_report_json(report))

    print(metrics_to_json(metrics))

    if threshold_failures:
        print("\nFailed thresholds:", file=sys.stderr)
        for f in threshold_failures:
            print(f"- {f}", file=sys.stderr)

    if args.fail_below_threshold and threshold_failures:
        return 2
    # Without an explicit threshold gate the runner reports success even if
    # individual cases failed — exploration mode rather than CI gate.
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graph-mcp-rag-evals")
    parser.add_argument("--planner", default="rag", choices=("rag", "deterministic"))
    parser.add_argument("--retriever", default="mock", choices=("mock", "qdrant"))
    parser.add_argument(
        "--reranker",
        default="heuristic",
        choices=("noop", "heuristic", "model"),
        help=("'model' is reserved for a future implementation and is rejected at parse time."),
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        choices=("missing", "fake"),
        help=(
            "Required when --retriever qdrant. 'fake' is for smoke tests; the real "
            "vectorizer is not implemented yet."
        ),
    )
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
    parser.add_argument("--k", type=int, default=8, help="Top-k for retrieval recall metrics.")
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Load --baseline-report and emit *_delta_vs_baseline metrics.",
    )
    parser.add_argument("--baseline-report", default=None)
    # Quality gates.
    parser.add_argument("--min-case-pass-rate", type=float, default=None)
    parser.add_argument("--min-selected-case-recall", type=float, default=None)
    parser.add_argument("--min-retrieval-case-recall-at-k", type=float, default=None)
    parser.add_argument("--min-selected-precision", type=float, default=None)
    parser.add_argument("--max-unresolved-mention-rate", type=float, default=None)
    parser.add_argument("--max-safety-violations", type=float, default=None)
    parser.add_argument(
        "--fail-below-threshold",
        action="store_true",
        help="Exit nonzero if any --min/--max threshold is missed.",
    )
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
    """Build a generate callable backed by PydanticAI."""
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
    """Serialize the full report as JSON for downstream tools."""
    payload = {
        "metrics": report.metrics,
        "baseline_metrics": report.baseline_metrics or {},
        "runner_args": report.runner_args or {},
        "threshold_failures": list(report.threshold_failures or []),
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
