"""Eval runner CLI: load cases, run planner, validate/render/execute, score.

The runner builds the validator, renderer, policy, schema provider, and
resolver once, then constructs the planner with all of those wired in. This
is what the v6 live eval was missing: ``make_planner`` used to call
``build_pydantic_ai_planner(model, schema=schema)`` without the dependency
context, so the workflow (validation, repair, term resolution) was bypassed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.agent import (
    DeterministicPlanner,
    Planner,
    PlannerOutput,
    build_pydantic_ai_planner,
)
from evals.metrics import compute_metrics
from evals.models import (
    CaseResult,
    ClarificationOutput,
    EvaluationReport,
    GoldenCase,
    PlannedOutput,
    RefusedOutput,
)
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.graph.endpoint import GraphEndpoint, HttpSparqlEndpoint
from graph_mcp.graph.schema_discovery import (
    SchemaProvider,
    SparqlDiscoveryConfig,
    SparqlSchemaProvider,
    StaticSchemaProvider,
)
from graph_mcp.graph.term_resolver import TermResolver
from graph_mcp.models.literals import DEFAULT_PREFIXES, OCEAN_KG_PREFIXES
from graph_mcp.security import SecurityPolicy

_DEFAULT_GRAPH = Path(__file__).parent / "sample_dataset.trig"
_DEFAULT_CASES = Path(__file__).parent / "golden_cases.yaml"


def _build_azure_openai_model(model_name: str | None = None, *, endpoint: str | None = None) -> Any:
    """Build an Azure-backed pydantic-ai model.

    Endpoint, model, and key are all required: the runner never bakes in a
    private organization endpoint. Pass values via environment variables
    (``AZURE_OPENAI_API_KEY``, ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_MODEL``)
    or via the ``--azure-endpoint`` / ``--model`` CLI flags.

    Environment-variable validation happens before the optional ``pydantic-ai``
    import so callers without that extra installed still get a clear error
    message about *what* is misconfigured rather than ``ImportError``.
    """
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY environment variable is not set; "
            "export it before running with --azure"
        )
    resolved_endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not resolved_endpoint:
        raise RuntimeError(
            "Azure endpoint is required: set AZURE_OPENAI_ENDPOINT or pass --azure-endpoint"
        )
    name = model_name or os.environ.get("AZURE_OPENAI_MODEL")
    if not name:
        raise RuntimeError("Azure model name is required: set AZURE_OPENAI_MODEL or pass --model")

    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.azure import AzureProvider
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "pydantic-ai is required for the Azure planner backend; "
            "install with `pip install graph-mcp[ai]`"
        ) from exc
    provider = AzureProvider(azure_endpoint=resolved_endpoint, api_key=api_key)
    return OpenAIChatModel(name, provider=provider)


def load_cases(path: str | Path) -> list[GoldenCase]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError(f"expected list of cases, got {type(raw).__name__}")
    return [GoldenCase.model_validate(c) for c in raw]


# --- Per-case execution ----------------------------------------------------


def _attach_diagnostics(result: CaseResult, planner: Planner, output: PlannerOutput) -> None:
    """Copy the workflow diagnostics + planner output onto the case result.

    The metric and report layers trust the *workflow*-selected terms (built
    deterministically by the resolver), not the LLM's self-reported
    ``resolved_terms`` field — those can be hallucinated.
    """
    diag = getattr(planner, "last_diagnostics", None)
    if diag is not None:
        result.extracted_mentions = list(diag.extracted_mentions)
        result.unresolved_mentions = list(diag.unresolved_mentions)
        result.ambiguous_mentions = list(getattr(diag, "ambiguous_mentions", []))
        result.workflow_selected_terms = list(getattr(diag, "selected_terms", []))
        result.validation_errors = list(diag.validation_errors_seen)
        result.repair_attempts = diag.repair_attempts
        result.rendered_sparql = result.rendered_sparql or diag.rendered_sparql
        result.relation_hints = [h.model_dump() for h in getattr(diag, "relation_hints", [])]
    result.planner_status = output.status
    result.planner_confidence = output.confidence
    result.planner_assumptions = list(output.assumptions)
    result.planner_reported_terms = list(output.resolved_terms)
    # Backwards-compat: ``resolved_terms`` shows the trusted workflow set.
    result.resolved_terms = list(result.workflow_selected_terms or output.resolved_terms)
    if isinstance(output, PlannedOutput):
        result.generated_plan_json = output.plan.model_dump()
    if isinstance(output, ClarificationOutput):
        result.clarification_question = output.clarification_question
    if isinstance(output, RefusedOutput):
        result.refusal_reason = output.refusal_reason
        result.policy_code = output.policy_code


def _classify_failure(case: GoldenCase, result: CaseResult) -> None:
    """Heuristically classify why a case failed. Sets `failure_classification`.

    Rules (most-specific first):

    - ``UNEXPECTED_REFUSAL`` / ``UNEXPECTED_CLARIFICATION`` on a case with
      unresolved or ambiguous mentions → ``PLANNER_SCHEMA_INFERENCE_GAP``.
    - Only ``MISSING_BINDING`` failures with an executed result whose row
      values look reasonable → ``EVAL_FALSE_NEGATIVE`` (variable name or
      prefix mismatch likely).
    - ``RESULT_MISMATCH`` only → ``REAL_PLANNER_OUTPUT_ERROR``.
    - ``EXECUTION_ERROR`` only → ``REAL_PLANNER_OUTPUT_ERROR``.
    - ``INVALID_PLAN`` → ``REAL_PLANNER_OUTPUT_ERROR``.
    - Otherwise → unset.
    """
    if not result.failures:
        return
    fails = result.failures
    only_bindings = all(f.startswith("MISSING_BINDING") for f in fails)
    if only_bindings and result.executed and result.execution_rows:
        # The query executed and returned rows; the only failure is binding
        # comparison. That is overwhelmingly an eval false negative.
        result.failure_classification = "EVAL_FALSE_NEGATIVE"
        result.failure_classification_reason = (
            "Only MISSING_BINDING failures and the query returned rows; "
            "likely variable-name or prefix mismatch in the matcher."
        )
        return
    if any("UNEXPECTED_REFUSAL" in f or "UNEXPECTED_CLARIFICATION" in f for f in fails):
        result.failure_classification = "PLANNER_SCHEMA_INFERENCE_GAP"
        result.failure_classification_reason = (
            "Planner refused or asked for clarification on a normal query. "
            "Often a schema-relationship inference gap."
        )
        return
    if any("INVALID_PLAN" in f for f in fails):
        result.failure_classification = "REAL_PLANNER_OUTPUT_ERROR"
        result.failure_classification_reason = "Validator rejected the planner's output."
        return
    if any("RESULT_MISMATCH" in f for f in fails):
        result.failure_classification = "REAL_PLANNER_OUTPUT_ERROR"
        result.failure_classification_reason = (
            "Query executed but row count outside the expected bound."
        )
        return
    if any("EXECUTION_ERROR" in f for f in fails):
        result.failure_classification = "REAL_PLANNER_OUTPUT_ERROR"
        result.failure_classification_reason = "Query failed at execution time."
        return
    if any("MISSING_KINDS" in f or "MISSING_TRIPLE" in f for f in fails):
        result.failure_classification = "REAL_PLANNER_OUTPUT_ERROR"
        result.failure_classification_reason = (
            "Plan structure does not match the expected pattern shape."
        )
        return


async def run_one(
    case: GoldenCase,
    planner: Planner,
    *,
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    endpoint: GraphEndpoint,
    policy: SecurityPolicy,
    execute: bool = True,
    semantic_repair_attempts: int = 0,
) -> CaseResult:
    semantic_failures: list[str] = []
    presentation_warnings: list[str] = []
    plan_generated = False
    plan_valid = False
    rendered_sparql: str | None = None
    executed = False
    row_count: int | None = None

    try:
        out = await asyncio.to_thread(planner.plan, case.question)
        plan_generated = True
    except Exception as exc:
        result = CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=False,
            plan_valid=False,
        )
        result.semantic_failures = [f"PLAN_ERROR: {exc}"]
        result.failures = list(result.semantic_failures)
        result.failure_classification = "REAL_PLANNER_OUTPUT_ERROR"
        result.failure_classification_reason = "Planner raised an exception."
        return result

    # --- Clarification expectation ---------------------------------------
    if case.expected.expect_clarification:
        clarif_correct = isinstance(out, ClarificationOutput)
        result = CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
            is_clarification_case=True,
            clarification_correct=clarif_correct,
        )
        if not clarif_correct:
            semantic_failures.append(
                f"EXPECTED_CLARIFICATION: planner returned status={out.status!r}"
            )
        result.semantic_failures = semantic_failures
        result.failures = list(semantic_failures)
        _attach_diagnostics(result, planner, out)
        _classify_failure(case, result)
        return result

    # --- Refusal / unsafe expectation ------------------------------------
    if case.expected.expect_invalid:
        rejected = False
        plan_valid_for_unsafe = False
        if isinstance(out, RefusedOutput):
            rejected = True  # planner refused — preferred path
        elif isinstance(out, PlannedOutput):
            res = validator.validate(out.plan)
            plan_valid_for_unsafe = res.ok
            rejected = not res.ok  # validator rejected
        result = CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=plan_valid_for_unsafe,
            is_unsafe_request_case=True,
            unsafe_request_rejected=rejected,
        )
        if not rejected:
            semantic_failures.append(
                "EXPECTED_REFUSAL_OR_INVALID: planner produced a valid plan for an unsafe request"
            )
        # Forbidden-feature accounting: the IR already prohibits raw_sparql by
        # construction, so we treat raw_sparql as a pass.
        result.semantic_failures = semantic_failures
        result.failures = list(semantic_failures)
        _attach_diagnostics(result, planner, out)
        _classify_failure(case, result)
        return result

    # --- Normal queries: planner must have produced a PlannedOutput ------
    if not isinstance(out, PlannedOutput):
        # The planner refused or asked for clarification on a normal case.
        if isinstance(out, RefusedOutput):
            semantic_failures.append(
                f"UNEXPECTED_REFUSAL: planner refused a normal case ({out.refusal_reason!r})"
            )
        else:
            semantic_failures.append(
                "UNEXPECTED_CLARIFICATION: planner asked for clarification on a normal case"
            )
        result = CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
        )
        result.semantic_failures = semantic_failures
        result.failures = list(semantic_failures)
        _attach_diagnostics(result, planner, out)
        _classify_failure(case, result)
        return result

    # --- Validate the plan -----------------------------------------------
    res = validator.validate(out.plan)
    plan_valid = res.ok
    presentation_warnings.extend(f"{w.code}: {w.message}" for w in res.warnings)

    if not res.ok:
        semantic_failures.append(
            "INVALID_PLAN: " + "; ".join(f"{e.code}: {e.message}" for e in res.errors)
        )
        result = CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
        )
        result.semantic_failures = semantic_failures
        result.failures = list(semantic_failures)
        result.warnings = list(presentation_warnings)
        result.validation_errors = list(res.errors)
        result.validation_warnings = list(res.warnings)
        _attach_diagnostics(result, planner, out)
        _classify_failure(case, result)
        return result

    rendered = renderer.render(out.plan)
    rendered_sparql = rendered.sparql

    expected = case.expected

    # --- Legacy / presentation checks (warnings only) --------------------
    pattern_names = {p.lower() for p in expected.required_patterns}
    rf_total = len(pattern_names) + len(expected.required_terms)
    rf_present = 0
    et_total = len(expected.required_terms)
    et_present = 0
    if pattern_names:
        present = _pattern_kinds_in_plan(out.plan)
        missing = pattern_names - present
        rf_present += len(pattern_names) - len(missing)
        if missing:
            presentation_warnings.append(f"MISSING_PATTERNS: {sorted(missing)}")
    for term in expected.required_terms:
        if term in rendered.sparql:
            rf_present += 1
            et_present += 1
        else:
            presentation_warnings.append(f"MISSING_TERM: {term!r} not in rendered SPARQL")
    ff_total = 0
    ff_violated = 0
    for forbidden in expected.forbidden_features:
        if forbidden == "raw_sparql":
            ff_total += 1
            continue
        ff_total += 1
        if forbidden == "service" and "SERVICE" in rendered.sparql.upper():
            ff_violated += 1
            semantic_failures.append("SAFETY: SERVICE used")

    # --- IR-level structural checks (semantic — fail the case) -----------
    from evals.structural import (
        collect_pattern_kinds,
        count_matching_triples,
        find_matching_aggregate,
        has_filter,
        has_group_by_var,
        has_order_by,
    )

    plan_kinds = collect_pattern_kinds(out.plan)

    rpk_total = len(expected.required_pattern_kinds)
    rpk_present = sum(1 for k in expected.required_pattern_kinds if k.lower() in plan_kinds)
    if rpk_total and rpk_present < rpk_total:
        missing_kinds = [k for k in expected.required_pattern_kinds if k.lower() not in plan_kinds]
        semantic_failures.append(f"MISSING_KINDS: {sorted(missing_kinds)}")

    fpk_total = len(expected.forbidden_pattern_kinds)
    fpk_violated = sum(1 for k in expected.forbidden_pattern_kinds if k.lower() in plan_kinds)
    if fpk_violated:
        semantic_failures.append(
            "SAFETY: forbidden pattern kinds present: "
            + ", ".join(k for k in expected.forbidden_pattern_kinds if k.lower() in plan_kinds)
        )

    triple_total = len(expected.required_triples)
    triple_present = 0
    for spec in expected.required_triples:
        if count_matching_triples(out.plan, spec) >= 1:
            triple_present += 1
        else:
            semantic_failures.append(
                f"MISSING_TRIPLE: <{spec.subject} {spec.predicate} {spec.object}>"
            )

    filter_total = len(expected.required_filters)
    filter_present = 0
    for fspec in expected.required_filters:
        if has_filter(out.plan, fspec):
            filter_present += 1
        else:
            semantic_failures.append(f"MISSING_FILTER: {fspec.kind}")

    aggregate_total = len(expected.required_aggregates)
    aggregate_present = 0
    for aspec in expected.required_aggregates:
        if find_matching_aggregate(out.plan, aspec):
            aggregate_present += 1
        else:
            semantic_failures.append(f"MISSING_AGGREGATE: {aspec.function}")

    gb_total = len(expected.required_group_by)
    gb_present = sum(1 for v in expected.required_group_by if has_group_by_var(out.plan, v))
    if gb_total and gb_present < gb_total:
        missing_gb = [v for v in expected.required_group_by if not has_group_by_var(out.plan, v)]
        semantic_failures.append(f"MISSING_GROUP_BY: {missing_gb}")

    ob_total = len(expected.required_order_by)
    ob_present = sum(1 for o in expected.required_order_by if has_order_by(out.plan, o))
    if ob_total and ob_present < ob_total:
        semantic_failures.append("MISSING_ORDER_BY")

    # required_property_paths
    from evals.structural import has_property_path

    for pp in expected.required_property_paths:
        if not has_property_path(out.plan, pp):
            semantic_failures.append(
                f"MISSING_PROPERTY_PATH: {pp.subject} {pp.operator}({pp.predicate}) {pp.object}"
            )

    # --- Execution -------------------------------------------------------
    eb_total = len(expected.expected_bindings)
    eb_present = 0
    actual_rows: list[dict[str, str]] = []
    if execute:
        try:
            result_q = await endpoint.query(
                rendered.sparql,
                query_type=rendered.query_type,
                timeout_ms=policy.timeout_ms,
                max_rows=policy.default_limit,
            )
            executed = True
            if result_q.kind == "select":
                row_count = len(result_q.rows)
                actual_rows = [
                    {var: binding.value for var, binding in row.bindings.items()}
                    for row in result_q.rows
                ]
            exp = expected.result_expectation or {}
            if isinstance(exp, dict):
                if "min_rows" in exp and row_count is not None and row_count < int(exp["min_rows"]):
                    semantic_failures.append(
                        f"RESULT_MISMATCH: expected >= {exp['min_rows']} rows, got {row_count}"
                    )
                if "max_rows" in exp and row_count is not None and row_count > int(exp["max_rows"]):
                    semantic_failures.append(
                        f"RESULT_MISMATCH: expected <= {exp['max_rows']} rows, got {row_count}"
                    )
                if "ask" in exp and result_q.kind == "ask" and bool(exp["ask"]) != result_q.boolean:
                    semantic_failures.append(
                        f"RESULT_MISMATCH: expected ASK={exp['ask']}, got {result_q.boolean}"
                    )
            from evals.structural import matches_bindings

            prefixes = {p.prefix: p.iri for p in out.plan.prefixes}
            for expected_row in expected.expected_bindings:
                if matches_bindings(
                    actual_rows,
                    expected_row,
                    prefixes=prefixes,
                    binding_aliases=expected.binding_aliases,
                ):
                    eb_present += 1
                else:
                    semantic_failures.append(f"MISSING_BINDING: {expected_row}")
        except Exception as exc:
            semantic_failures.append(f"EXECUTION_ERROR: {exc}")

    result = CaseResult(
        case_id=case.id,
        question=case.question,
        plan_generated=plan_generated,
        plan_valid=plan_valid,
        rendered_sparql=rendered_sparql,
        executed=executed,
        row_count=row_count,
        required_features_total=rf_total + rpk_total,
        required_features_present=rf_present + rpk_present,
        forbidden_features_total=ff_total + fpk_total,
        forbidden_features_violated=ff_violated + fpk_violated,
        expected_terms_total=et_total,
        expected_terms_present=et_present,
        triple_total=triple_total,
        triple_present=triple_present,
        filter_total=filter_total,
        filter_present=filter_present,
        aggregate_total=aggregate_total,
        aggregate_present=aggregate_present,
        group_by_total=gb_total,
        group_by_present=gb_present,
        order_by_total=ob_total,
        order_by_present=ob_present,
        expected_bindings_total=eb_total,
        expected_bindings_present=eb_present,
        forbidden_pattern_kinds_total=fpk_total,
        forbidden_pattern_kinds_violated=fpk_violated,
        repair_attempted=bool(getattr(planner, "last_repair_attempted", False)),
        repair_succeeded=bool(getattr(planner, "last_repair_succeeded", False)),
    )
    result.semantic_failures = semantic_failures
    result.failures = list(semantic_failures)
    result.presentation_warnings = list(presentation_warnings)
    result.warnings = list(presentation_warnings)
    result.validation_errors = list(res.errors)
    result.validation_warnings = list(res.warnings)
    result.execution_rows = actual_rows
    _attach_diagnostics(result, planner, out)

    # --- Optional semantic repair pass -----------------------------------
    if (
        semantic_repair_attempts > 0
        and result.failures
        and isinstance(out, PlannedOutput)
        and result.executed
    ):
        for _ in range(semantic_repair_attempts):
            feedback = _build_semantic_feedback(case, result)
            try:
                out2 = await asyncio.to_thread(planner.plan, feedback)
            except Exception:
                break
            if not isinstance(out2, PlannedOutput):
                break
            retry = await run_one(
                case,
                _SingleShotPlanner(out2, planner),
                validator=validator,
                renderer=renderer,
                endpoint=endpoint,
                policy=policy,
                execute=execute,
                semantic_repair_attempts=0,
            )
            if not retry.failures:
                # Repair worked: keep the better result but record that a
                # semantic repair was needed.
                retry.repair_attempts = max(retry.repair_attempts, 1)
                retry.repair_attempted = True
                retry.repair_succeeded = True
                _classify_failure(case, retry)
                return retry
            # Otherwise stick with the latest attempt's diagnostics.
            result = retry
            result.repair_attempts = max(result.repair_attempts, 1)
            result.repair_attempted = True
            result.repair_succeeded = False
            if not isinstance(out2, PlannedOutput):
                break
            out = out2

    _classify_failure(case, result)
    return result


def _build_semantic_feedback(case: GoldenCase, result: CaseResult) -> str:
    """Format a feedback prompt summarizing what went wrong semantically."""
    lines = [case.question, "", "## Semantic feedback from previous attempt"]
    if result.rendered_sparql:
        lines.append("Previous SPARQL:")
        lines.append("```sparql")
        lines.append(result.rendered_sparql)
        lines.append("```")
    if result.execution_rows:
        lines.append(f"Previous result rows ({result.row_count}):")
        for row in result.execution_rows[:5]:
            lines.append(f"  - {row}")
    lines.append("Failures:")
    for f in result.semantic_failures:
        lines.append(f"  - {f}")
    lines.append(
        "Produce a corrected PlannedOutput. Do not switch to raw SPARQL. "
        "Do not ask for clarification unless the failure is genuinely "
        "caused by ambiguous user input. Address the listed failures."
    )
    return "\n".join(lines)


class _SingleShotPlanner:
    """Wraps a pre-computed PlannedOutput as a Planner.

    Used by the semantic-repair loop: after we re-call the underlying
    planner with feedback we need to feed the new output through the same
    evaluation pipeline (validate → render → execute → score). Re-using
    ``run_one`` is simpler than duplicating that pipeline.
    """

    def __init__(self, output: PlannerOutput, real: Planner) -> None:
        self._output = output
        self.last_repair_attempted = bool(getattr(real, "last_repair_attempted", False))
        self.last_repair_succeeded = bool(getattr(real, "last_repair_succeeded", False))
        self.last_diagnostics = getattr(real, "last_diagnostics", None)

    def plan(self, question: str, *, resolver: Any = None) -> PlannerOutput:
        return self._output


def _pattern_kinds_in_plan(plan: Any) -> set[str]:
    """Walk a plan and collect pattern `kind` discriminators (lowercased)."""
    out: set[str] = set()

    def walk(p: Any) -> None:
        kind = getattr(p, "kind", None)
        if isinstance(kind, str):
            out.add(kind)
        for attr in ("where", "patterns", "branches", "template"):
            children = getattr(p, attr, None)
            if children is None:
                continue
            for c in children:
                if isinstance(c, list):
                    for cc in c:
                        walk(cc)
                else:
                    walk(c)
        if hasattr(p, "select"):
            walk(p.select)

    walk(plan)
    return out


# --- Markdown report --------------------------------------------------------


def render_markdown_report(report: EvaluationReport) -> str:
    lines = ["# Evaluation Report", "", "## Metrics", ""]
    for k, v in sorted(report.metrics.items()):
        lines.append(f"- **{k}**: {v:.3f}")
    lines.append("")
    lines.append("## Cases")
    for c in report.cases:
        ok = not c.failures
        marker = "PASS" if ok else "FAIL"
        lines.append(f"### [{marker}] {c.case_id} — {c.question}")
        if c.failure_classification:
            lines.append(
                f"- failure class: `{c.failure_classification}`"
                + (
                    f" — {c.failure_classification_reason}"
                    if c.failure_classification_reason
                    else ""
                )
            )
        if c.planner_status:
            lines.append(
                f"- planner status: `{c.planner_status}`"
                + (
                    f" (confidence={c.planner_confidence:.2f})"
                    if c.planner_confidence is not None
                    else ""
                )
            )
        if c.refusal_reason:
            lines.append(f"- refusal: {c.refusal_reason}")
        if c.clarification_question:
            lines.append(f"- clarification: {c.clarification_question}")
        if c.workflow_selected_terms:
            lines.append("- workflow-selected terms (deterministic, used for metrics):")
            for t in c.workflow_selected_terms:
                pn = t.prefixed_name or t.iri
                lines.append(f"  - {t.mention!r} → {pn} ({t.kind}, score={t.score:.2f})")
        if c.planner_reported_terms:
            # Only show this when it differs from the workflow set, to keep
            # the report compact.
            planner_iris = {t.iri for t in c.planner_reported_terms}
            workflow_iris = {t.iri for t in c.workflow_selected_terms}
            if planner_iris != workflow_iris:
                lines.append("- planner-reported terms (for cross-check):")
                for t in c.planner_reported_terms:
                    pn = t.prefixed_name or t.iri
                    lines.append(f"  - {t.mention!r} → {pn} ({t.kind})")
        if c.relation_hints:
            lines.append("- relation hints:")
            for h in c.relation_hints:
                hint_name = h.get("prefixed_name") or h.get("property_iri")
                lines.append(
                    f"  - {hint_name} "
                    f"({h.get('subject_type', '?')} → {h.get('object_type', '?')}) "
                    f"score={h.get('score', 0):.2f}"
                )
        if c.extracted_mentions:
            lines.append(f"- extracted mentions: {c.extracted_mentions}")
        if c.unresolved_mentions:
            lines.append(f"- unresolved mentions: {c.unresolved_mentions}")
        if c.ambiguous_mentions:
            lines.append(f"- ambiguous mentions: {c.ambiguous_mentions}")
        if c.semantic_failures:
            lines.append("- semantic failures:")
            for f in c.semantic_failures:
                lines.append(f"  - {f}")
        if c.validation_errors:
            lines.append("- validation errors:")
            for e in c.validation_errors:
                lines.append(f"  - `{e.code}`: {e.message}")
        if c.presentation_warnings:
            lines.append("- presentation warnings:")
            for w in c.presentation_warnings:
                lines.append(f"  - {w}")
        if c.repair_attempts:
            lines.append(
                f"- repair attempts: {c.repair_attempts} (succeeded: {c.repair_succeeded})"
            )
        if c.rendered_sparql:
            lines.append("```sparql")
            lines.append(c.rendered_sparql)
            lines.append("```")
        if c.execution_rows:
            lines.append(f"- result rows ({c.row_count}):")
            for row in c.execution_rows[:10]:
                lines.append(f"  - {row}")
        if c.generated_plan_json is not None and not c.failures:
            # Skip plan JSON in passing cases to keep the report compact.
            pass
        elif c.generated_plan_json is not None:
            lines.append("- plan JSON:")
            lines.append("```json")
            lines.append(json.dumps(c.generated_plan_json, indent=2, sort_keys=True))
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


# --- Planner factory --------------------------------------------------------


@dataclass
class PlannerComponents:
    """Bundle of dependencies the planner needs.

    ``endpoint`` is widened to :class:`GraphEndpoint` so the same component
    bundle can hold a local rdflib graph or a live HTTP SPARQL endpoint.
    """

    settings: Settings
    policy: SecurityPolicy
    validator: QueryPlanValidator
    renderer: SparqlRenderer
    schema_provider: SchemaProvider
    resolver: TermResolver
    endpoint: GraphEndpoint


async def build_components(
    *,
    graph_path: Path | None = None,
    endpoint_url: str | None = None,
    endpoint: GraphEndpoint | None = None,
    settings: Settings | None = None,
    extra_prefixes: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> PlannerComponents:
    """Construct every dependency the planner workflow needs.

    Exactly one of ``graph_path``, ``endpoint_url``, or ``endpoint`` must
    be supplied:

    - ``graph_path`` → local rdflib endpoint loaded from an RDF fixture.
    - ``endpoint_url`` → live :class:`HttpSparqlEndpoint` with optional
      Basic Auth.
    - ``endpoint`` → caller-built endpoint (used by tests to inject a
      fake / mocked endpoint).

    The schema is discovered from the chosen endpoint so the LLM planner
    has the ontology available in its prompt and the resolver has terms
    to map mentions onto. ``extra_prefixes`` are added to the discovery
    config's ``base_prefixes`` so domain-specific shortcuts (dcat, sosa,
    geo, …) are exposed without modifying the protected default-prefix
    set.
    """
    sources = sum(1 for src in (graph_path, endpoint_url, endpoint) if src is not None)
    if sources == 0:
        raise ValueError("build_components requires one of graph_path, endpoint_url, or endpoint")
    if sources > 1:
        raise ValueError(
            "build_components requires exactly one graph source; "
            "specify graph_path OR endpoint_url OR endpoint"
        )

    settings = settings or Settings(allow_unbounded_paths=True)
    policy = SecurityPolicy.from_settings(settings)
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)

    chosen_endpoint: GraphEndpoint
    if graph_path is not None:
        chosen_endpoint = LocalRdflibEndpoint.from_rdf_file(graph_path)
    elif endpoint_url is not None:
        chosen_endpoint = HttpSparqlEndpoint(endpoint_url, auth=auth)
    else:
        assert endpoint is not None  # narrowed by the source-count check
        chosen_endpoint = endpoint

    base_prefixes = dict(DEFAULT_PREFIXES)
    if extra_prefixes:
        base_prefixes.update(extra_prefixes)
    discovery_config = SparqlDiscoveryConfig(base_prefixes=base_prefixes)
    discovery = SparqlSchemaProvider(chosen_endpoint, discovery_config)
    snapshot = await discovery.refresh()
    schema_provider: SchemaProvider = StaticSchemaProvider(snapshot)
    resolver = TermResolver(schema_provider)
    return PlannerComponents(
        settings=settings,
        policy=policy,
        validator=validator,
        renderer=renderer,
        schema_provider=schema_provider,
        resolver=resolver,
        endpoint=chosen_endpoint,
    )


# Re-export so callers can splice ocean prefixes into build_components without
# importing from graph_mcp.models directly.
__all_ocean_prefixes__ = OCEAN_KG_PREFIXES


def make_planner(
    name: str,
    components: PlannerComponents,
    *,
    model: str | None = None,
    azure: bool = False,
    azure_endpoint: str | None = None,
    examples: list[dict[str, Any]] | None = None,
    max_repair_attempts: int = 2,
) -> Planner:
    if name == "deterministic":
        return DeterministicPlanner()
    if name == "pydantic-ai":
        model_obj: Any
        if azure:
            model_obj = _build_azure_openai_model(model, endpoint=azure_endpoint)
        else:
            if not model:
                raise ValueError("pydantic-ai planner requires --model (or --azure)")
            model_obj = model
        return build_pydantic_ai_planner(
            model_obj,
            schema=components.schema_provider,
            validator=components.validator,
            renderer=components.renderer,
            policy=components.policy,
            resolver=components.resolver,
            examples=examples,
            max_repair_attempts=max_repair_attempts,
        )
    raise ValueError(f"unknown planner: {name}")


# --- Main entry point ------------------------------------------------------


async def run(
    cases: Iterable[GoldenCase],
    planner: Planner,
    *,
    components: PlannerComponents,
    execute: bool = True,
    semantic_repair_attempts: int = 0,
) -> EvaluationReport:
    results: list[CaseResult] = []
    for case in cases:
        results.append(
            await run_one(
                case,
                planner,
                validator=components.validator,
                renderer=components.renderer,
                endpoint=components.endpoint,
                policy=components.policy,
                execute=execute,
                semantic_repair_attempts=semantic_repair_attempts,
            )
        )
    metrics = compute_metrics(results)
    return EvaluationReport(cases=results, metrics=metrics)


@dataclass
class ThresholdSpec:
    metric: str
    minimum: float | None = None
    maximum: float | None = None


def _check_thresholds(metrics: dict[str, float], thresholds: list[ThresholdSpec]) -> list[str]:
    """Return human-readable failure messages for metrics that miss thresholds."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-mcp-evals")
    parser.add_argument(
        "--planner",
        default="deterministic",
        choices=("deterministic", "pydantic-ai"),
    )
    parser.add_argument("--model", default=None, help="LLM model for pydantic-ai planner")
    parser.add_argument(
        "--azure",
        action="store_true",
        help=(
            "Use Azure OpenAI as the pydantic-ai backend. Requires "
            "AZURE_OPENAI_API_KEY in the environment, plus an endpoint and "
            "model (via --azure-endpoint/--model or AZURE_OPENAI_ENDPOINT/"
            "AZURE_OPENAI_MODEL). The runner does not bake in any default "
            "endpoint or model name."
        ),
    )
    parser.add_argument("--azure-endpoint", default=None, help="Azure OpenAI endpoint URL")
    parser.add_argument("--cases", default=str(_DEFAULT_CASES))
    parser.add_argument("--graph", default=str(_DEFAULT_GRAPH))
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--report-dir", default=None, help="Directory to write JSON+MD reports")
    parser.add_argument("--max-repair-attempts", type=int, default=2)
    parser.add_argument(
        "--semantic-repair-attempts",
        type=int,
        default=0,
        help=(
            "When >0, re-call the planner with structured semantic feedback "
            "(missing bindings, wrong row counts) up to this many times after "
            "an executed plan fails semantic checks. Eval-only signal; the "
            "production MCP server does not see this."
        ),
    )
    # Quality gates.
    parser.add_argument("--min-case-pass-rate", type=float, default=None)
    parser.add_argument("--min-valid-plan-rate", type=float, default=None)
    parser.add_argument("--min-render-success-rate", type=float, default=None)
    parser.add_argument("--min-execution-success-rate", type=float, default=None)
    parser.add_argument("--min-term-resolution-accuracy", type=float, default=None)
    parser.add_argument("--max-safety-violations", type=float, default=None)
    parser.add_argument(
        "--fail-below-threshold",
        action="store_true",
        help="Exit nonzero if any --min/--max threshold is missed.",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)

    components = asyncio.run(build_components(graph_path=Path(args.graph)))
    planner = make_planner(
        args.planner,
        components,
        model=args.model,
        azure=args.azure,
        azure_endpoint=args.azure_endpoint,
        max_repair_attempts=args.max_repair_attempts,
    )

    report = asyncio.run(
        run(
            cases,
            planner,
            components=components,
            execute=not args.no_execute,
            semantic_repair_attempts=args.semantic_repair_attempts,
        )
    )

    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(json.dumps(report.model_dump(), indent=2, default=str))
        (out_dir / "report.md").write_text(render_markdown_report(report))

    print(json.dumps(report.metrics, indent=2))

    # Threshold checks.
    thresholds: list[ThresholdSpec] = []
    if args.min_case_pass_rate is not None:
        thresholds.append(ThresholdSpec("case_pass_rate", minimum=args.min_case_pass_rate))
    if args.min_valid_plan_rate is not None:
        thresholds.append(ThresholdSpec("valid_plan_rate", minimum=args.min_valid_plan_rate))
    if args.min_render_success_rate is not None:
        thresholds.append(
            ThresholdSpec("render_success_rate", minimum=args.min_render_success_rate)
        )
    if args.min_execution_success_rate is not None:
        thresholds.append(
            ThresholdSpec("execution_success_rate", minimum=args.min_execution_success_rate)
        )
    if args.min_term_resolution_accuracy is not None:
        thresholds.append(
            ThresholdSpec("term_resolution_accuracy", minimum=args.min_term_resolution_accuracy)
        )
    if args.max_safety_violations is not None:
        thresholds.append(
            ThresholdSpec("safety_violation_count", maximum=args.max_safety_violations)
        )
    threshold_failures = _check_thresholds(report.metrics, thresholds)
    if threshold_failures:
        print("\nFailed thresholds:", file=sys.stderr)
        for f in threshold_failures:
            print(f"- {f}", file=sys.stderr)

    case_failures = sum(1 for c in report.cases if c.failures)
    if args.fail_below_threshold and threshold_failures:
        return 2
    return 0 if case_failures == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
