"""Markdown report rendering for the RAG eval harness.

The base eval's :func:`evals.runner.render_markdown_report` is reused for
the per-case section so cases look the same across runs. This module
prepends RAG-specific aggregate metrics + per-case retrieval/rerank
diagnostics so the operator can see what the planner saw.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from evals.runner import render_markdown_report as render_base_report
from evals_rag.metrics import RagCaseResult


@dataclass
class RagEvaluationReport:
    """Bundle a list of :class:`RagCaseResult` with aggregate metrics.

    Kept distinct from :class:`evals.models.EvaluationReport` so the runner
    can serialize RAG-specific diagnostics without forcing an extension on
    the base model.
    """

    rag_results: list[RagCaseResult]
    metrics: dict[str, float]
    baseline_metrics: dict[str, float] | None = None
    runner_args: dict[str, str] | None = None


def render_rag_report(report: RagEvaluationReport) -> str:
    """Render a markdown summary including base + RAG-specific sections."""
    base_lines = ["# RAG Evaluation Report", ""]
    if report.runner_args:
        base_lines.append("## Run configuration")
        base_lines.append("")
        for key, value in sorted(report.runner_args.items()):
            base_lines.append(f"- **{key}**: `{value}`")
        base_lines.append("")

    base_lines.append("## Aggregate metrics")
    base_lines.append("")
    rag_keys = _split_metrics(report.metrics)
    for label, group in (
        ("RAG-specific", rag_keys["rag"]),
        ("Pipeline", rag_keys["pipeline"]),
        ("Quality", rag_keys["quality"]),
        ("Deltas vs baseline", rag_keys["delta"]),
    ):
        if not group:
            continue
        base_lines.append(f"### {label}")
        base_lines.append("")
        for k, v in sorted(group.items()):
            base_lines.append(f"- **{k}**: {v:.3f}")
        base_lines.append("")

    base_lines.append("## Cases")
    base_lines.append("")
    for entry in report.rag_results:
        base_lines.append(_render_case(entry))
    return "\n".join(base_lines)


def _render_case(entry: RagCaseResult) -> str:
    """Render one case + its retrieval / rerank tables."""
    case = entry.case
    result = entry.result
    rag = entry.rag_diagnostics
    marker = "PASS" if not result.failures else "FAIL"
    lines = [f"### [{marker}] {case.id} — {case.question}"]
    if result.planner_status:
        lines.append(f"- planner status: `{result.planner_status}`")
    if rag.mentions:
        lines.append(f"- mentions: {rag.mentions}")
    if rag.retrieval_queries:
        lines.append("- retrieval queries:")
        for rq in rag.retrieval_queries:
            scope = rq.mention or "<question>"
            kinds = rq.expected_kinds or "any"
            lines.append(f"  - {scope!r} kinds={kinds} limit={rq.limit}")
    if rag.retrieved_concepts:
        lines.append(f"- retrieved concepts ({len(rag.retrieved_concepts)}):")
        for rc in rag.retrieved_concepts[:10]:
            name = rc.concept.prefixed_name or rc.concept.iri
            lines.append(
                f"  - {name} ({rc.concept.kind}) score={rc.score:.2f} via={rc.retrieval_source}"
            )
        if len(rag.retrieved_concepts) > 10:
            lines.append(f"  - ... ({len(rag.retrieved_concepts) - 10} more)")
    if rag.reranked_concepts:
        lines.append(f"- reranked top-{min(len(rag.reranked_concepts), 8)}:")
        for rk in rag.reranked_concepts[:8]:
            name = rk.concept.prefixed_name or rk.concept.iri
            lines.append(
                f"  - {name} ({rk.concept.kind}) "
                f"final={rk.final_score:.2f} (retrieval={rk.retrieval_score:.2f}, "
                f"rerank+={rk.rerank_score:.2f}) — {rk.explanation}"
            )
    if rag.selected_concepts:
        lines.append(f"- selected ({len(rag.selected_concepts)}):")
        for sc in rag.selected_concepts:
            name = sc.concept.prefixed_name or sc.concept.iri
            lines.append(f"  - {name} ({sc.concept.kind}) score={sc.final_score:.2f}")
    if rag.unresolved_mentions:
        lines.append(f"- unresolved mentions: {rag.unresolved_mentions}")
    if result.semantic_failures:
        lines.append("- semantic failures:")
        for f in result.semantic_failures:
            lines.append(f"  - {f}")
    if result.rendered_sparql:
        lines.append("```sparql")
        lines.append(result.rendered_sparql)
        lines.append("```")
    if result.execution_rows:
        lines.append(f"- result rows ({result.row_count}):")
        for row in result.execution_rows[:5]:
            lines.append(f"  - {row}")
    lines.append("")
    return "\n".join(lines)


def _split_metrics(metrics: dict[str, float]) -> dict[str, dict[str, float]]:
    """Group metric keys for prettier rendering."""
    rag_keys = {
        "retrieval_recall_at_8",
        "selected_concept_accuracy",
        "reranker_improvement_rate",
        "unresolved_mention_rate",
        "concept_ambiguity_rate",
    }
    pipeline_keys = {
        "valid_plan_rate",
        "render_success_rate",
        "execution_success_rate",
        "case_pass_rate",
        "planner_output_rate",
        "planner_case_pass_rate",
        "total_cases",
    }
    out: dict[str, dict[str, float]] = {
        "rag": {},
        "pipeline": {},
        "quality": {},
        "delta": {},
    }
    for k, v in metrics.items():
        if k.endswith("_delta_vs_baseline"):
            out["delta"][k] = v
        elif k in rag_keys or k.startswith("retrieval_recall_at_"):
            out["rag"][k] = v
        elif k in pipeline_keys:
            out["pipeline"][k] = v
        else:
            out["quality"][k] = v
    return out


def render_base_markdown(report) -> str:  # type: ignore[no-untyped-def]
    """Re-export the base markdown renderer for convenience."""
    return render_base_report(report)


def metrics_to_json(metrics: dict[str, float]) -> str:
    """Stable JSON dump for ``metrics.json`` artifacts."""
    return json.dumps(metrics, indent=2, sort_keys=True)
