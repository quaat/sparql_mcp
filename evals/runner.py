"""Eval runner CLI: load cases, run planner, validate/render/execute, score."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from evals.agent import DeterministicPlanner, Planner, build_pydantic_ai_planner
from evals.metrics import compute_metrics
from evals.models import CaseResult, EvaluationReport, GoldenCase
from graph_mcp.compiler import QueryPlanValidator, SparqlRenderer
from graph_mcp.config import Settings
from graph_mcp.graph import LocalRdflibEndpoint
from graph_mcp.security import SecurityPolicy

_DEFAULT_GRAPH = Path(__file__).parent / "sample_graph.ttl"
_DEFAULT_CASES = Path(__file__).parent / "golden_cases.yaml"

_AZURE_DEFAULT_ENDPOINT = "https://oceanai-dev-swe-01-fo.services.ai.azure.com/openai/v1"
_AZURE_DEFAULT_MODEL = "gpt-5.5-1"


def _build_azure_openai_model(model_name: str | None = None) -> Any:
    """Build an Azure-backed pydantic-ai model.

    Reads the API key from ``AZURE_OPENAI_API_KEY``. Endpoint and model name
    fall back to the defaults baked into this module but can be overridden
    via ``AZURE_OPENAI_ENDPOINT`` and ``AZURE_OPENAI_MODEL``.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.azure import AzureProvider

    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY environment variable is not set; "
            "export it before running with --azure"
        )
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", _AZURE_DEFAULT_ENDPOINT)
    name = model_name or os.environ.get("AZURE_OPENAI_MODEL") or _AZURE_DEFAULT_MODEL
    provider = AzureProvider(azure_endpoint=endpoint, api_key=api_key)
    return OpenAIChatModel(name, provider=provider)


def load_cases(path: str | Path) -> list[GoldenCase]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError(f"expected list of cases, got {type(raw).__name__}")
    return [GoldenCase.model_validate(c) for c in raw]


async def run_one(
    case: GoldenCase,
    planner: Planner,
    *,
    validator: QueryPlanValidator,
    renderer: SparqlRenderer,
    endpoint: LocalRdflibEndpoint,
    policy: SecurityPolicy,
    execute: bool = True,
) -> CaseResult:
    failures: list[str] = []
    warnings: list[str] = []
    plan_generated = False
    plan_valid = False
    rendered_sparql: str | None = None
    executed = False
    row_count: int | None = None

    try:
        # Run in a thread so planners that use ``agent.run_sync`` (which
        # internally calls ``loop.run_until_complete``) don't collide with
        # the runner's own asyncio loop.
        out = await asyncio.to_thread(planner.plan, case.question)
        plan_generated = True
    except Exception as exc:
        failures.append(f"PLAN_ERROR: {exc}")
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=False,
            plan_valid=False,
            failures=failures,
        )

    if case.expected.expect_clarification:
        clarif_correct = bool(out.needs_clarification)
        if not clarif_correct:
            failures.append("EXPECTED_CLARIFICATION: planner did not request clarification")
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
            failures=failures,
            is_clarification_case=True,
            clarification_correct=clarif_correct,
        )

    res = validator.validate(out.plan)
    plan_valid = res.ok
    warnings.extend(f"{w.code}: {w.message}" for w in res.warnings)

    if case.expected.expect_invalid:
        rejected = not res.ok
        if not rejected:
            failures.append("EXPECTED_INVALID: validator accepted an unsafe plan")
        # forbidden features still apply
        if "raw_sparql" in case.expected.forbidden_features and "raw" in case.question.lower():
            # The planner cannot produce a raw SPARQL string; the IR makes
            # that impossible by construction, so this is a pass.
            pass
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=plan_valid,
            failures=failures,
            warnings=warnings,
            is_unsafe_request_case=True,
            unsafe_request_rejected=rejected,
        )

    if not res.ok:
        failures.append("INVALID_PLAN: " + "; ".join(f"{e.code}: {e.message}" for e in res.errors))
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
            failures=failures,
            warnings=warnings,
        )

    rendered = renderer.render(out.plan)
    rendered_sparql = rendered.sparql

    # --- legacy structural checks (rendered-string substring matches) ---
    expected = case.expected
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
            failures.append(f"MISSING_PATTERNS: {sorted(missing)}")
    for term in expected.required_terms:
        if term in rendered.sparql:
            rf_present += 1
            et_present += 1
        else:
            failures.append(f"MISSING_TERM: {term!r} not in rendered SPARQL")
    ff_total = 0
    ff_violated = 0
    for forbidden in expected.forbidden_features:
        if forbidden == "raw_sparql":
            ff_total += 1
            continue
        ff_total += 1
        if forbidden == "service" and "SERVICE" in rendered.sparql.upper():
            ff_violated += 1
            failures.append("SAFETY: SERVICE used")

    # --- IR-level structural checks (deep matching) ---------------------
    from evals.structural import (
        collect_pattern_kinds,
        count_matching_triples,
        find_matching_aggregate,
        has_filter,
        has_group_by_var,
        has_order_by,
    )

    plan_kinds = collect_pattern_kinds(out.plan)

    # required_pattern_kinds (deep-walk version of required_patterns).
    rpk_total = len(expected.required_pattern_kinds)
    rpk_present = sum(1 for k in expected.required_pattern_kinds if k.lower() in plan_kinds)
    if rpk_total and rpk_present < rpk_total:
        missing_kinds = [k for k in expected.required_pattern_kinds if k.lower() not in plan_kinds]
        failures.append(f"MISSING_KINDS: {sorted(missing_kinds)}")

    # forbidden_pattern_kinds.
    fpk_total = len(expected.forbidden_pattern_kinds)
    fpk_violated = sum(1 for k in expected.forbidden_pattern_kinds if k.lower() in plan_kinds)
    if fpk_violated:
        failures.append(
            "SAFETY: forbidden pattern kinds present: "
            + ", ".join(k for k in expected.forbidden_pattern_kinds if k.lower() in plan_kinds)
        )

    # required_triples
    triple_total = len(expected.required_triples)
    triple_present = 0
    for spec in expected.required_triples:
        if count_matching_triples(out.plan, spec) >= 1:
            triple_present += 1
        else:
            failures.append(f"MISSING_TRIPLE: <{spec.subject} {spec.predicate} {spec.object}>")

    # required_filters
    filter_total = len(expected.required_filters)
    filter_present = 0
    for fspec in expected.required_filters:
        if has_filter(out.plan, fspec):
            filter_present += 1
        else:
            failures.append(f"MISSING_FILTER: {fspec.kind}")

    # required_aggregates
    aggregate_total = len(expected.required_aggregates)
    aggregate_present = 0
    for aspec in expected.required_aggregates:
        if find_matching_aggregate(out.plan, aspec):
            aggregate_present += 1
        else:
            failures.append(f"MISSING_AGGREGATE: {aspec.function}")

    # required_group_by
    gb_total = len(expected.required_group_by)
    gb_present = sum(1 for v in expected.required_group_by if has_group_by_var(out.plan, v))
    if gb_total and gb_present < gb_total:
        missing_gb = [v for v in expected.required_group_by if not has_group_by_var(out.plan, v)]
        failures.append(f"MISSING_GROUP_BY: {missing_gb}")

    # required_order_by
    ob_total = len(expected.required_order_by)
    ob_present = sum(1 for o in expected.required_order_by if has_order_by(out.plan, o))
    if ob_total and ob_present < ob_total:
        failures.append("MISSING_ORDER_BY")

    # --- execution -------------------------------------------------------
    eb_total = len(expected.expected_bindings)
    eb_present = 0
    if execute:
        try:
            result = await endpoint.query(
                rendered.sparql,
                query_type=rendered.query_type,
                timeout_ms=policy.timeout_ms,
                max_rows=policy.default_limit,
            )
            executed = True
            actual_rows: list[dict[str, str]] = []
            if result.kind == "select":
                row_count = len(result.rows)
                actual_rows = [
                    {var: binding.value for var, binding in row.bindings.items()}
                    for row in result.rows
                ]
            exp = expected.result_expectation or {}
            if isinstance(exp, dict):
                if "min_rows" in exp and row_count is not None and row_count < int(exp["min_rows"]):
                    failures.append(
                        f"RESULT_MISMATCH: expected >= {exp['min_rows']} rows, got {row_count}"
                    )
                if "max_rows" in exp and row_count is not None and row_count > int(exp["max_rows"]):
                    failures.append(
                        f"RESULT_MISMATCH: expected <= {exp['max_rows']} rows, got {row_count}"
                    )
                if "ask" in exp and result.kind == "ask" and bool(exp["ask"]) != result.boolean:
                    failures.append(
                        f"RESULT_MISMATCH: expected ASK={exp['ask']}, got {result.boolean}"
                    )
            from evals.structural import matches_bindings

            for expected_row in expected.expected_bindings:
                if matches_bindings(actual_rows, expected_row):
                    eb_present += 1
                else:
                    failures.append(f"MISSING_BINDING: {expected_row}")
        except Exception as exc:
            failures.append(f"EXECUTION_ERROR: {exc}")

    return CaseResult(
        case_id=case.id,
        question=case.question,
        plan_generated=plan_generated,
        plan_valid=plan_valid,
        rendered_sparql=rendered_sparql,
        executed=executed,
        row_count=row_count,
        failures=failures,
        warnings=warnings,
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


def _pattern_kinds_in_plan(plan: Any) -> set[str]:
    """Walk a plan and collect pattern `kind` discriminators (lowercased)."""
    out: set[str] = set()

    def walk(p: Any) -> None:
        kind = getattr(p, "kind", None)
        if isinstance(kind, str):
            out.add(kind)
        # Walk children
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
        if c.failures:
            lines.append("Failures:")
            for f in c.failures:
                lines.append(f"  - {f}")
        if c.warnings:
            lines.append("Warnings:")
            for w in c.warnings:
                lines.append(f"  - {w}")
        if c.rendered_sparql:
            lines.append("```sparql")
            lines.append(c.rendered_sparql)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def make_planner(
    name: str,
    *,
    model: str | None = None,
    azure: bool = False,
    schema: Any = None,
) -> Planner:
    if name == "deterministic":
        return DeterministicPlanner()
    if name == "pydantic-ai":
        model_obj: Any
        if azure:
            model_obj = _build_azure_openai_model(model)
        else:
            if not model:
                raise ValueError("pydantic-ai planner requires --model (or --azure)")
            model_obj = model
        return build_pydantic_ai_planner(model_obj, schema=schema)
    raise ValueError(f"unknown planner: {name}")


async def _discover_schema(graph_path: Path) -> Any:
    """Build a static schema provider from the sample graph so the LLM
    planner has the ontology available in its system prompt."""
    from graph_mcp.graph.schema_discovery import StaticSchemaProvider

    endpoint = LocalRdflibEndpoint.from_turtle_file(graph_path)
    from graph_mcp.graph.schema_discovery import SparqlSchemaProvider

    provider = SparqlSchemaProvider(endpoint)
    snapshot = await provider.refresh()
    return StaticSchemaProvider(snapshot)


async def run(
    cases: Iterable[GoldenCase],
    planner: Planner,
    *,
    settings: Settings | None = None,
    execute: bool = True,
    graph_path: Path | None = None,
) -> EvaluationReport:
    settings = settings or Settings(allow_unbounded_paths=True)
    policy = SecurityPolicy.from_settings(settings)
    validator = QueryPlanValidator(policy)
    renderer = SparqlRenderer(policy)
    graph_path = graph_path or _DEFAULT_GRAPH
    endpoint = LocalRdflibEndpoint.from_turtle_file(graph_path)

    results: list[CaseResult] = []
    for case in cases:
        results.append(
            await run_one(
                case,
                planner,
                validator=validator,
                renderer=renderer,
                endpoint=endpoint,
                policy=policy,
                execute=execute,
            )
        )
    metrics = compute_metrics(results)
    return EvaluationReport(cases=results, metrics=metrics)


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
            "Use Azure OpenAI as the pydantic-ai backend. Reads "
            "AZURE_OPENAI_API_KEY from the environment; endpoint and model "
            "default to the values baked into the runner but can be "
            "overridden via AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_MODEL or "
            "--model."
        ),
    )
    parser.add_argument("--cases", default=str(_DEFAULT_CASES))
    parser.add_argument("--graph", default=str(_DEFAULT_GRAPH))
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--report-dir", default=None, help="Directory to write JSON+MD reports")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    schema = None
    if args.planner == "pydantic-ai":
        schema = asyncio.run(_discover_schema(Path(args.graph)))
    planner = make_planner(args.planner, model=args.model, azure=args.azure, schema=schema)

    report = asyncio.run(
        run(cases, planner, execute=not args.no_execute, graph_path=Path(args.graph))
    )

    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(json.dumps(report.model_dump(), indent=2))
        (out_dir / "report.md").write_text(render_markdown_report(report))

    print(json.dumps(report.metrics, indent=2))
    failures = sum(1 for c in report.cases if c.failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
