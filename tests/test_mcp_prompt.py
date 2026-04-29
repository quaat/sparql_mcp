"""Test that the MCP build_query_plan prompt covers the workflow lessons (§11)."""

from __future__ import annotations

from graph_mcp.mcp_tools.prompts import BUILD_QUERY_PLAN_PROMPT, get_prompts


def test_mcp_prompt_mentions_full_workflow_tools() -> None:
    for token in (
        "resolve_terms",
        "validate_query_plan",
        "render_sparql",
        "query_graph",
    ):
        assert token in BUILD_QUERY_PLAN_PROMPT, f"prompt missing {token!r}"


def test_mcp_prompt_describes_clarify_and_refuse() -> None:
    assert "clarification" in BUILD_QUERY_PLAN_PROMPT.lower()
    assert "refuse" in BUILD_QUERY_PLAN_PROMPT.lower()


def test_mcp_prompt_forbids_raw_sparql_and_destructive_ops() -> None:
    assert "raw SPARQL" in BUILD_QUERY_PLAN_PROMPT
    for token in ("DROP", "DELETE", "destructive"):
        assert token in BUILD_QUERY_PLAN_PROMPT


def test_get_prompts_registers_build_query_plan() -> None:
    prompts = get_prompts()
    names = {p[0] for p in prompts}
    assert "build_query_plan" in names
