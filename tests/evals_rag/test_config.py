"""Tests for RagSettings strict parsing."""

from __future__ import annotations

import pytest

from evals_rag.config import RagConfigError, RagSettings


def test_defaults_construct_cleanly():
    s = RagSettings()
    assert s.retrieval_limit == 20
    assert s.selected_limit == 8


def test_invalid_retrieval_limit_fails_fast():
    with pytest.raises(RagConfigError) as excinfo:
        RagSettings.from_env({"GRAPH_MCP_RAG_RETRIEVAL_LIMIT": "not_an_int"})
    assert "GRAPH_MCP_RAG_RETRIEVAL_LIMIT" in str(excinfo.value)


def test_invalid_score_threshold_fails_fast():
    with pytest.raises(RagConfigError):
        RagSettings.from_env({"GRAPH_MCP_RAG_SCORE_THRESHOLD": "abc"})


def test_negative_score_threshold_rejected():
    with pytest.raises(RagConfigError):
        RagSettings(score_threshold=-0.1)


def test_zero_retrieval_limit_rejected():
    with pytest.raises(RagConfigError):
        RagSettings(retrieval_limit=0)


def test_selected_limit_must_not_exceed_retrieval_limit():
    with pytest.raises(RagConfigError):
        RagSettings(retrieval_limit=4, selected_limit=8)


def test_empty_qdrant_url_rejected():
    with pytest.raises(RagConfigError):
        RagSettings(qdrant_url="")


def test_bool_parsing_rejects_unknown_values():
    with pytest.raises(RagConfigError):
        RagSettings.from_env({"GRAPH_MCP_RAG_USE_RERANKER": "maybe"})


@pytest.mark.parametrize("value,expected", [("true", True), ("0", False), ("YES", True)])
def test_bool_parsing_accepts_canonical_values(value, expected):
    s = RagSettings.from_env({"GRAPH_MCP_RAG_USE_RERANKER": value})
    assert s.use_reranker is expected


def test_empty_string_falls_back_to_default():
    s = RagSettings.from_env({"GRAPH_MCP_RAG_RETRIEVAL_LIMIT": ""})
    assert s.retrieval_limit == 20
