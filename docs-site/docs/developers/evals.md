---
id: evals
title: Evaluation harness
sidebar_position: 10
description: How the evals/ pipeline scores planner output, and how to add new cases.
---

# Evaluation harness

`evals/` exists so we can compare planners — the deterministic
keyword-matcher baseline, plus any LLM-backed planner — against
golden cases without relying on string-equality checks.

The harness lives outside `src/graph_mcp/` so the runtime never depends
on it.

## Components

| File | Purpose |
| --- | --- |
| `evals/runner.py` | CLI entry point; loads cases, builds a planner, runs the validate→render→execute pipeline per case. |
| `evals/agent.py` | Planner implementations: `DeterministicPlanner` and the optional `build_pydantic_ai_planner`. |
| `evals/models.py` | Pydantic IO: `PlanGenerationOutput`, `CaseResult`, etc. |
| `evals/metrics.py` | Aggregate metrics over per-case results. |
| `evals/structural.py` | IR-level recall computation (triple/filter/aggregate/...). |
| `evals/golden_cases.yaml` | Baseline cases — aligned with the deterministic planner's keyword triggers. |
| `evals/golden_cases_adversarial.yaml` | Paraphrased / clarification-trap cases the keyword baseline cannot answer. |
| `evals/sample_graph.ttl` | Bundled in-memory graph for executing cases. |

## Planners

### `DeterministicPlanner`

Hand-coded `if "work" in q and "acme" in q: return ...`. **Not** an
LLM. It exercises validator/renderer/executor end-to-end without an
API key. Its 100% case-pass rate on `golden_cases.yaml` is by
construction; do not read it as evidence of LLM planning quality.

### PydanticAI planner

`build_pydantic_ai_planner(model, ...)` constructs an
`pydantic_ai.Agent` with the bundled system prompt, schema snapshot,
and JSON Schema of `PlanGenerationOutput`. The harness then runs
`generate → validate → repair (up to N attempts)`.

Tool-backed term resolution inside this agent is **out of scope** for
the MCP server package — the production path is for hosts to call the
server's `resolve_terms` tool directly. See
[ADR 0004](/adr/0004-docusaurus-documentation-site/) and the readme
note on this decision.

## Per-case lifecycle

1. Load `cases.yaml`.
2. For each case, ask the planner for a `PlanGenerationOutput`.
3. If the planner asked for clarification, score
   `clarification_correct` against the case expectation.
4. Else: validate. Capture errors. Render. Execute against the bundled
   graph (or `--no-execute` skip).
5. Compute structural metrics: triple-pattern recall, filter recall,
   aggregate recall, grouping recall, expected-binding accuracy,
   forbidden-pattern violations.
6. Append to `report.cases`.

## Metrics

Defined in `evals/metrics.py`. The full reference list is in
[Eval metrics reference](/reference/eval-metrics/). Categories:

| Category | Examples |
| --- | --- |
| Pipeline health | `valid_plan_rate`, `render_success_rate`, `execution_success_rate`, `case_pass_rate`, `planner_output_rate` |
| Quality | `required_feature_recall`, `forbidden_feature_violation_rate`, `term_resolution_accuracy`, `structural_plan_score`, `execution_result_accuracy` |
| Safety | `safety_violation_count`, `validation_error_rate` |
| Repair | `repair_attempted_rate`, `repair_success_rate` |
| IR-level recall | `triple_pattern_recall`, `filter_semantics_recall`, `aggregate_semantics_recall`, `grouping_semantics_recall` |
| Special cases | `clarification_accuracy`, `unsafe_request_rejection_accuracy` |

## Repair-loop metrics

The PydanticAI planner workflow re-prompts the LLM with the
validator's structured errors when validation fails:

- `repair_attempted_rate` = fraction of cases where ≥1 repair pass
  was needed;
- `repair_success_rate` = of those, fraction that became valid after
  repair.

Deterministic planner reports zero on both. These metrics are the
honest signal for an LLM planner.

## Limitations of "deterministic 100% pass rate"

The deterministic planner's 100% on `golden_cases.yaml` is
intentional — those cases are tuned to its keywords. This is **not**
a measure of LLM planning quality. Use:

- `golden_cases_adversarial.yaml` for paraphrased and clarification
  traps (the keyword baseline scores around 36%);
- structural metrics (`required_feature_recall`,
  `triple_pattern_recall`, ...) for finer-grained signal;
- `case_pass_rate` for an aggregate measure.

## Running

```bash
python -m evals.runner --planner deterministic
python -m evals.runner --planner deterministic --cases evals/golden_cases_adversarial.yaml
python -m evals.runner --planner pydantic-ai --model anthropic:claude-sonnet-4-6
python -m evals.runner --report-dir build/eval_report
```

Without an API key, the deterministic planner is the only option that
runs (and it is what CI exercises).

## Adding a new golden case

Append to `evals/golden_cases.yaml` (or the adversarial file). Each
case is YAML with at least:

```yaml
- question: "How many Persons are there?"
  expected:
    plan:
      kind: select
      patterns: ["TriplePattern", "GroupPattern"]
    rendered_must_contain: ["COUNT(", "GROUP BY"]
    expected_terms:
      - { kind: class, prefixed_name: "ex:Person" }
    rows_at_least: 0
    forbidden_features: ["DESCRIBE"]
```

If you add something the deterministic planner does not match, that
is fine — the case will fail for that planner. Add the corresponding
`if` branch in `evals/agent.py.DeterministicPlanner.plan` if you want
it to.

## Adding a new metric

1. Add the per-case input field on `CaseResult` in `evals/models.py`.
2. Compute it inside `evals/runner.py.run_case`.
3. Aggregate it in `evals/metrics.py.compute_metrics`.
4. Document it in
   [Eval metrics reference](/reference/eval-metrics/).
5. Update `tests/test_eval_metrics.py`.
