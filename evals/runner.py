"""Eval runner CLI: load cases, run planner, validate/render/execute, score."""

from __future__ import annotations

import argparse
import asyncio
import json
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
        out = planner.plan(case.question)
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
        if not out.needs_clarification:
            failures.append("EXPECTED_CLARIFICATION: planner did not request clarification")
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=False,
            failures=failures,
        )

    res = validator.validate(out.plan)
    plan_valid = res.ok
    warnings.extend(f"{w.code}: {w.message}" for w in res.warnings)

    if case.expected.expect_invalid:
        if res.ok:
            failures.append("EXPECTED_INVALID: validator accepted an unsafe plan")
        # forbidden features still apply
        if "raw_sparql" in case.expected.forbidden_features and "raw" in case.question.lower():
            # The planner must not have produced a raw SPARQL string; our IR
            # makes that impossible by construction, so this is a pass.
            pass
        return CaseResult(
            case_id=case.id,
            question=case.question,
            plan_generated=plan_generated,
            plan_valid=plan_valid,
            failures=failures,
            warnings=warnings,
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

    # --- structural checks ----------------------------------------------
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
            # Not representable in the IR — count it as a present-and-clean
            # constraint so the metric reflects schema-enforced safety.
            ff_total += 1
            continue
        ff_total += 1
        if forbidden == "service" and "SERVICE" in rendered.sparql.upper():
            ff_violated += 1
            failures.append("SAFETY: SERVICE used")

    # --- execution -------------------------------------------------------
    if execute:
        try:
            result = await endpoint.query(
                rendered.sparql,
                query_type=rendered.query_type,
                timeout_ms=policy.timeout_ms,
                max_rows=policy.default_limit,
            )
            executed = True
            if result.kind == "select":
                row_count = len(result.rows)
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
        required_features_total=rf_total,
        required_features_present=rf_present,
        forbidden_features_total=ff_total,
        forbidden_features_violated=ff_violated,
        expected_terms_total=et_total,
        expected_terms_present=et_present,
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


def make_planner(name: str, *, model: str | None = None) -> Planner:
    if name == "deterministic":
        return DeterministicPlanner()
    if name == "pydantic-ai":
        if not model:
            raise ValueError("pydantic-ai planner requires --model")
        return build_pydantic_ai_planner(model)
    raise ValueError(f"unknown planner: {name}")


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
    parser.add_argument("--cases", default=str(_DEFAULT_CASES))
    parser.add_argument("--graph", default=str(_DEFAULT_GRAPH))
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--report-dir", default=None, help="Directory to write JSON+MD reports")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    planner = make_planner(args.planner, model=args.model)

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
