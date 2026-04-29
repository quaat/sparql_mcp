---
id: eval-metrics
title: Eval metrics
sidebar_position: 7
description: Every metric emitted by evals/metrics.py, with definitions and interpretation.
---

# Eval metrics

`evals/metrics.py:compute_metrics` returns a flat `dict[str, float]`
of all the metrics below. The runner prints it as JSON at the end of
`python -m evals.runner`.

Higher is better unless noted. "Deterministic-only" markers mean the
metric reports zero (or a degenerate value) for the
`DeterministicPlanner`; they exist for LLM planners.

## Pipeline health

| Metric | Definition | Interpretation | Higher? |
| --- | --- | --- | --- |
| `valid_plan_rate` | `plan_valid / total_cases` | Share of cases whose generated plan passes the validator. | yes |
| `render_success_rate` | `rendered / total_cases` | Share that also rendered successfully. | yes |
| `execution_success_rate` | `executed / total_cases` | Share that also executed against the bundled graph. | yes |
| `case_pass_rate` | `(total - failures) / total` | Share with zero structural / safety / execution failures. | yes |
| `planner_output_rate` | `plan_generated / total_cases` | Share where the planner produced any plan at all. | yes |

Common failure causes:

- low `valid_plan_rate` → planner emits unbound variables, missing
  `GROUP BY`, etc. Check `validation_error_rate`.
- low `render_success_rate` (with high `valid_plan_rate`) → plan
  passed the validator but the renderer hit something unexpected.
  Should be rare; investigate.
- low `execution_success_rate` (with high `render_success_rate`) →
  the rendered SPARQL fails on the sample graph. Often a missing
  prefix or an invented IRI.

## Quality

| Metric | Definition | Interpretation | Higher? |
| --- | --- | --- | --- |
| `required_feature_recall` | hit rate over `expected.required_features` (pattern kinds + required tokens) | Did the planner produce the right shape? | yes |
| `forbidden_feature_violation_rate` | violation rate over `expected.forbidden_features` | Did the planner avoid forbidden constructs (`DESCRIBE`, etc.)? | no (lower is better) |
| `term_resolution_accuracy` | `et_present / et_total` over expected schema terms | Did the planner use the expected IRIs? | yes |
| `structural_plan_score` | `required_feature_recall × (1 − forbidden_feature_violation_rate)` | Single rolled-up structural score. | yes |
| `execution_result_accuracy` | fraction of executed cases whose row count matches the expectation | Did the query return the right number of rows? | yes |

## Safety

| Metric | Definition | Interpretation | Higher? |
| --- | --- | --- | --- |
| `safety_violation_count` | count of cases with any `SAFETY:` failure | SERVICE used, raw SPARQL leaked, ... | no |
| `validation_error_rate` | share of cases with an `INVALID_PLAN` failure | Validator rejected the plan. | no |

## Repair (LLM-only signal)

| Metric | Definition | Interpretation | Higher? |
| --- | --- | --- | --- |
| `repair_attempted_rate` | share of cases where the planner workflow attempted at least one repair pass | Frequency of "first try wasn't valid". | depends |
| `repair_success_rate` | of those, fraction that became valid after repair | Effectiveness of the repair loop. | yes |

For the deterministic planner both are zero — it does not run a
repair workflow.

## IR-level recall

These metrics look beyond `required_feature_recall` and check the IR
itself. They are useful when you want to know *which* aspect of a
plan was wrong.

| Metric | Definition | Higher? |
| --- | --- | --- |
| `triple_pattern_recall` | `triple_present / triple_total` | yes |
| `filter_semantics_recall` | `filter_present / filter_total` | yes |
| `aggregate_semantics_recall` | `agg_present / agg_total` | yes |
| `grouping_semantics_recall` | `(gb_present + ob_present) / (gb_total + ob_total)` | yes |
| `result_binding_accuracy` | `eb_present / eb_total` over expected bindings | yes |
| `forbidden_pattern_violation_rate` | `fpk_violated / fpk_total` over forbidden pattern kinds | no |

When a section's denominator is zero, the metric is `1.0` (recall) or
`0.0` (violation rate) so that "no expectation" doesn't penalize the
planner.

## Special cases

| Metric | Definition | Higher? |
| --- | --- | --- |
| `clarification_accuracy` | for `is_clarification_case` cases, share that correctly asked for clarification | yes |
| `unsafe_request_rejection_accuracy` | for `is_unsafe_request_case` cases, share that correctly refused or rerouted | yes |

## Totals

| Metric | Definition |
| --- | --- |
| `total_cases` | Number of cases in the run. |

## Reading the deterministic baseline

The `DeterministicPlanner` will report:

- `case_pass_rate ≈ 1.0` on `golden_cases.yaml` (by construction);
- `case_pass_rate ≈ 0.36` on `golden_cases_adversarial.yaml`
  (paraphrases that the keyword matcher misses);
- `repair_attempted_rate = 0`, `repair_success_rate = 0`;
- `term_resolution_accuracy = 1.0` because the deterministic planner
  always emits the expected terms.

These numbers are baselines for testing the runner pipeline, not
quality signals for the underlying server.
