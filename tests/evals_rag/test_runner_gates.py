"""Quality-gate / CLI tests for the RAG runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals_rag.runner import _build_parser, main

_REPO = Path(__file__).resolve().parent.parent.parent
_CASES = _REPO / "evals" / "golden_cases.yaml"
_GRAPH = _REPO / "evals" / "sample_dataset.trig"


def _baseline_argv(tmp_dir: Path) -> list[str]:
    return [
        "--planner",
        "rag",
        "--retriever",
        "mock",
        "--reranker",
        "heuristic",
        "--cases",
        str(_CASES),
        "--graph",
        str(_GRAPH),
        "--report-dir",
        str(tmp_dir),
    ]


def test_runner_without_thresholds_returns_zero_for_eval_failures(tmp_path):
    """No --fail-below-threshold: runner returns 0 even if cases fail."""
    rc = main(_baseline_argv(tmp_path / "soft"))
    assert rc == 0


def test_runner_threshold_failure_returns_nonzero(tmp_path):
    out_dir = tmp_path / "gated"
    rc = main(
        [
            *_baseline_argv(out_dir),
            "--min-case-pass-rate",
            "1.5",  # impossibly high → must fail the gate
            "--fail-below-threshold",
        ]
    )
    assert rc == 2


def test_threshold_failure_summary_lists_failed_metric(tmp_path):
    out_dir = tmp_path / "summary"
    main([*_baseline_argv(out_dir), "--min-case-pass-rate", "1.5", "--fail-below-threshold"])
    payload = json.loads((out_dir / "report.json").read_text())
    assert payload["threshold_failures"]
    assert any("case_pass_rate" in f for f in payload["threshold_failures"])
    md = (out_dir / "report.md").read_text()
    assert "Threshold failures" in md


def test_model_reranker_cli_rejected(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--reranker", "model"])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "reserved" in err.lower()


def test_qdrant_without_embedding_provider_fails_fast(tmp_path, capsys):
    rc = main(
        [
            "--planner",
            "rag",
            "--retriever",
            "qdrant",
            "--reranker",
            "heuristic",
            "--cases",
            str(_CASES),
            "--graph",
            str(_GRAPH),
            "--report-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "embedding provider" in err.lower()


def test_qdrant_with_fake_embedding_provider_does_not_fail_at_cli(tmp_path):
    """Fake provider passes the CLI gate; per-case retrieval may then error
    against the placeholder Qdrant client, but those errors are recorded as
    retrieval diagnostics rather than crashing the runner."""
    rc = main(
        [
            "--planner",
            "rag",
            "--retriever",
            "qdrant",
            "--embedding-provider",
            "fake",
            "--reranker",
            "heuristic",
            "--cases",
            str(_CASES),
            "--graph",
            str(_GRAPH),
            "--report-dir",
            str(tmp_path),
            "--no-execute",
        ]
    )
    # The runner must not crash; it should complete and exit 0 (no gates).
    assert rc == 0


def test_parser_includes_new_gate_flags():
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--min-case-pass-rate",
            "0.9",
            "--min-selected-case-recall",
            "0.9",
            "--min-retrieval-case-recall-at-k",
            "0.9",
            "--min-selected-precision",
            "0.5",
            "--max-unresolved-mention-rate",
            "0.1",
            "--max-safety-violations",
            "0",
            "--fail-below-threshold",
        ]
    )
    assert args.min_case_pass_rate == 0.9
    assert args.min_selected_case_recall == 0.9
    assert args.min_retrieval_case_recall_at_k == 0.9
    assert args.min_selected_precision == 0.5
    assert args.max_unresolved_mention_rate == 0.1
    assert args.max_safety_violations == 0
    assert args.fail_below_threshold is True
