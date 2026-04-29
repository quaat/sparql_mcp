"""Markdown report rendering for the RAG eval harness."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from evals.runner import render_markdown_report as render_base_report
from evals_rag.metrics import RagCaseResult


@dataclass
class RagEvaluationReport:
    """Bundle a list of :class:`RagCaseResult` with aggregate metrics."""

    rag_results: list[RagCaseResult]
    metrics: dict[str, float]
    baseline_metrics: dict[str, float] | None = None
    runner_args: dict[str, str] | None = None
    threshold_failures: list[str] = field(default_factory=list)


def render_rag_report(report: RagEvaluationReport) -> str:
    """Render a markdown summary including base + RAG-specific sections."""
    lines: list[str] = ["# RAG Evaluation Report", ""]
    if report.runner_args:
        lines.append("## Run configuration")
        lines.append("")
        for key, value in sorted(report.runner_args.items()):
            lines.append(f"- **{key}**: `{value}`")
        lines.append("")

    if report.threshold_failures:
        lines.append("## Threshold failures")
        lines.append("")
        for f in report.threshold_failures:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("## Aggregate metrics")
    lines.append("")
    grouped = _split_metrics(report.metrics)
    for label, group in (
        ("RAG-specific (case-level)", grouped["rag_case"]),
        ("RAG-specific (concept-level)", grouped["rag_concept"]),
        ("RAG-specific (other)", grouped["rag_other"]),
        ("Pipeline", grouped["pipeline"]),
        ("Quality", grouped["quality"]),
        ("Deltas vs baseline", grouped["delta"]),
        ("Deprecated aliases", grouped["deprecated"]),
    ):
        if not group:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for k, v in sorted(group.items()):
            lines.append(f"- **{k}**: {v:.3f}")
        lines.append("")

    lines.append("## Retrieval diagnostics summary")
    lines.append("")
    summary = _retrieval_summary(report)
    if not summary:
        lines.append("- (nothing notable)")
    for line in summary:
        lines.append(f"- {line}")
    lines.append("")

    lines.append("## Cases")
    lines.append("")
    for entry in report.rag_results:
        lines.append(_render_case(entry))
    return "\n".join(lines)


def _retrieval_summary(report: RagEvaluationReport) -> list[str]:
    """Top-level bullets summarizing notable retrieval state across cases."""
    out: list[str] = []
    cases_with_unresolved = [e for e in report.rag_results if e.rag_diagnostics.unresolved_mentions]
    cases_empty_selection = [
        e
        for e in report.rag_results
        if e.rag_diagnostics.retrieved_concepts and not e.rag_diagnostics.selected_concepts
    ]
    cases_with_errors = [e for e in report.rag_results if e.rag_diagnostics.retrieval_errors]
    cases_with_promotions = [e for e in report.rag_results if e.rag_diagnostics.promoted_term_iris]
    if cases_with_unresolved:
        ids = ", ".join(e.case.id for e in cases_with_unresolved)
        out.append(f"cases with unresolved mentions: {ids}")
    if cases_empty_selection:
        ids = ", ".join(e.case.id for e in cases_empty_selection)
        out.append(f"cases with empty selection (retrieved but nothing kept): {ids}")
    if cases_with_errors:
        ids = ", ".join(e.case.id for e in cases_with_errors)
        out.append(f"cases with retrieval errors: {ids}")
    if cases_with_promotions:
        out.append(
            f"cases with RAG-promoted terms: {len(cases_with_promotions)} of "
            f"{len(report.rag_results)}"
        )
    return out


def _render_case(entry: RagCaseResult) -> str:
    """Render one case + its retrieval / rerank tables."""
    case = entry.case
    result = entry.result
    rag = entry.rag_diagnostics
    marker = "PASS" if not result.failures else "FAIL"
    lines = [f"### [{marker}] {case.id} — {case.question}"]
    if result.planner_status:
        lines.append(f"- planner status: `{result.planner_status}`")

    if rag.mention_diagnostics:
        lines.append("- mentions:")
        for m in rag.mention_diagnostics:
            kinds = ",".join(m.expected_kinds) if m.expected_kinds else "any"
            sources = ",".join(m.sources) if m.sources else "?"
            lines.append(f"  - {m.text!r} (expected_kinds={kinds}, sources={sources})")

    pdiag = rag.planner_diagnostics or {}
    baseline_terms = pdiag.get("baseline_selected_terms") or []
    rag_terms = pdiag.get("rag_selected_terms") or []
    merged_terms = pdiag.get("selected_terms") or []
    if baseline_terms:
        lines.append("- baseline resolved terms:")
        for t in baseline_terms:
            lines.append(f"  - {_format_term_dict(t)}")
    if rag_terms:
        lines.append("- RAG-promoted terms:")
        for t in rag_terms:
            lines.append(f"  - {_format_term_dict(t)}")
    if merged_terms and not baseline_terms and not rag_terms:
        lines.append("- resolved terms (merged):")
        for t in merged_terms:
            lines.append(f"  - {_format_term_dict(t)}")

    if rag.unresolved_mentions:
        lines.append(f"- unresolved mentions (post-merge): {rag.unresolved_mentions}")
    if rag.retrieval_errors:
        lines.append("- retrieval errors:")
        for err in rag.retrieval_errors:
            lines.append(f"  - {err}")
    if rag.retrieval_queries:
        lines.append("- retrieval queries:")
        for rq in rag.retrieval_queries:
            scope = rq.mention or "<question>"
            kinds_label = ",".join(rq.expected_kinds) if rq.expected_kinds else "any"
            lines.append(f"  - {scope!r} kinds={kinds_label} limit={rq.limit}")
    if rag.retrieved_concepts:
        lines.append(f"- retrieved concepts ({len(rag.retrieved_concepts)}, deduped):")
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
    if rag.candidate_pack_text:
        lines.append("- candidate pack injected into prompt:")
        lines.append("```text")
        lines.append(rag.candidate_pack_text)
        lines.append("```")
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


def _format_term_dict(t: dict) -> str:  # type: ignore[no-untyped-def]
    """Format a serialized :class:`TermCandidate` for the report."""
    name = t.get("prefixed_name") or t.get("iri") or "?"
    label = t.get("label") or "?"
    return (
        f"mention={t.get('mention', '')!r} → {name} ({t.get('kind', '?')}, "
        f"score={float(t.get('score', 0.0)):.2f}, label={label!r})"
    )


def _split_metrics(metrics: dict[str, float]) -> dict[str, dict[str, float]]:
    """Group metric keys for prettier rendering."""
    rag_case_keys = {
        "selected_case_recall",
        "selected_precision",
    }
    rag_concept_keys = {
        "retrieval_concept_recall_at_8",
        "selected_concept_recall",
        "reranker_promotion_rate",
        "reranker_demotion_error_rate",
    }
    rag_other_keys = {
        "mean_selected_candidates",
        "mean_retrieved_candidates",
        "unresolved_mention_rate",
        "concept_ambiguity_rate",
        "empty_selection_rate",
        "retrieval_error_rate",
        "planner_case_pass_rate",
    }
    deprecated_keys = {
        "retrieval_recall_at_8",
        "selected_concept_accuracy",
        "reranker_improvement_rate",
    }
    pipeline_keys = {
        "valid_plan_rate",
        "render_success_rate",
        "execution_success_rate",
        "case_pass_rate",
        "planner_output_rate",
        "total_cases",
    }
    out: dict[str, dict[str, float]] = {
        "rag_case": {},
        "rag_concept": {},
        "rag_other": {},
        "pipeline": {},
        "quality": {},
        "delta": {},
        "deprecated": {},
    }
    for k, v in metrics.items():
        if k.endswith("_delta_vs_baseline"):
            out["delta"][k] = v
        elif k in deprecated_keys:
            out["deprecated"][k] = v
        elif k in rag_case_keys:
            out["rag_case"][k] = v
        elif k in rag_concept_keys or k.startswith("retrieval_concept_recall_at_"):
            out["rag_concept"][k] = v
        elif k.startswith("retrieval_case_recall_at_"):
            out["rag_case"][k] = v
        elif k in rag_other_keys:
            out["rag_other"][k] = v
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
