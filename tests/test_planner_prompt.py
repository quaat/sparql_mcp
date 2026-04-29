"""Tests for the planner prompt cookbook + few-shot examples (§4)."""

from __future__ import annotations

from pathlib import Path

import yaml

from evals.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    build_full_system_prompt,
    load_curated_examples,
)


def test_prompt_mentions_each_required_pattern() -> None:
    """The IR cookbook section must contain shape examples for each pattern."""
    required = [
        "OPTIONAL",
        "UNION",
        "FILTER NOT EXISTS",
        "VALUES",
        "BIND",
        "GROUP BY",
        "HAVING",
        "property path",
        "Refused",
    ]
    for token in required:
        assert token in PLANNER_SYSTEM_PROMPT, f"prompt missing token {token!r}"


def test_prompt_describes_three_status_variants() -> None:
    for status in ("planned", "needs_clarification", "refused"):
        assert status in PLANNER_SYSTEM_PROMPT


def test_prompt_forbids_raw_sparql_and_invented_iris() -> None:
    assert "NEVER write raw SPARQL" in PLANNER_SYSTEM_PROMPT
    assert "invent" in PLANNER_SYSTEM_PROMPT.lower()


def test_curated_examples_load_and_cover_main_patterns() -> None:
    examples = load_curated_examples()
    assert examples, "expected curated examples"
    names = {e.get("name") for e in examples}
    expected_names = {
        "english_label_filter",
        "optional_with_inner_filter",
        "union_two_relationships",
        "filter_not_exists",
        "values_list_then_relationship",
        "bind_doubled",
        "count_per_group",
        "having_count_threshold",
        "property_path_one_or_more",
        "ambiguous_clarification",
        "refused_destructive",
    }
    missing = expected_names - names
    assert not missing, f"curated examples missing patterns: {missing}"


def test_curated_examples_yaml_is_well_formed() -> None:
    path = Path(__file__).parent.parent / "evals" / "planner_examples.yaml"
    raw = yaml.safe_load(path.read_text())
    assert isinstance(raw, list)
    for ex in raw:
        assert "name" in ex
        assert "output" in ex
        assert "status" in ex["output"]


def test_full_system_prompt_includes_all_blocks() -> None:
    assembled = build_full_system_prompt(
        cookbook=PLANNER_SYSTEM_PROMPT,
        schema_block='{"prefixes": {"ex": "http://example.org/"}}',
        qp_schema='{"PlannedOutput": {}}',
        examples=[{"name": "x", "question": "?"}],
    )
    assert "Available schema" in assembled
    assert "Output schema" in assembled
    assert "Curated examples" in assembled
    assert "OPTIONAL" in assembled  # cookbook still present
