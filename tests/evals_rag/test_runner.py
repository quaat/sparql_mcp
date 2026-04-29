"""End-to-end tests for the RAG runner with the mock retriever.

These tests run against the local sample graph and the existing golden
case file via the deterministic baseline planner, so they require neither
an LLM nor a vector database.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals_rag.runner import _build_parser, main

_REPO = Path(__file__).resolve().parent.parent.parent


def test_runner_writes_metrics_and_report(tmp_path):
    out_dir = tmp_path / "rag-mock"
    rc = main(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--cases",
            str(_REPO / "evals" / "golden_cases.yaml"),
            "--graph",
            str(_REPO / "evals" / "sample_dataset.trig"),
            "--report-dir",
            str(out_dir),
        ]
    )
    # Some golden cases may fail under the deterministic planner; we only
    # care that the runner wrote artifacts and exited cleanly.
    assert rc in (0, 1)
    metrics_path = out_dir / "metrics.json"
    report_path = out_dir / "report.md"
    json_path = out_dir / "report.json"
    assert metrics_path.exists()
    assert report_path.exists()
    assert json_path.exists()
    metrics = json.loads(metrics_path.read_text())
    assert "case_pass_rate" in metrics
    assert "selected_concept_accuracy" in metrics
    assert "retrieval_recall_at_8" in metrics
    text = report_path.read_text()
    assert "RAG Evaluation Report" in text
    assert "Retrieved ontology candidates" not in text  # belongs in prompt, not report
    payload = json.loads(json_path.read_text())
    assert payload["metrics"] == metrics
    assert payload["cases"], "expected at least one case in report.json"


def test_compare_baseline_emits_deltas(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"metrics": {"case_pass_rate": 0.5}}))
    out_dir = tmp_path / "rag-with-baseline"
    rc = main(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--cases",
            str(_REPO / "evals" / "golden_cases.yaml"),
            "--graph",
            str(_REPO / "evals" / "sample_dataset.trig"),
            "--report-dir",
            str(out_dir),
            "--compare-baseline",
            "--baseline-report",
            str(baseline),
        ]
    )
    assert rc in (0, 1)
    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert "planner_case_pass_delta_vs_baseline" in metrics


def test_parser_defaults_are_stable():
    parser = _build_parser()
    args = parser.parse_args(["--report-dir", "x"])
    assert args.planner == "rag"
    assert args.retriever == "mock"
    assert args.reranker == "heuristic"
    assert args.report_dir == "x"
    assert args.no_execute is False


def test_runner_no_execute_skips_endpoint(tmp_path):
    out_dir = tmp_path / "rag-no-execute"
    rc = main(
        [
            "--planner",
            "rag",
            "--retriever",
            "mock",
            "--reranker",
            "heuristic",
            "--cases",
            str(_REPO / "evals" / "golden_cases.yaml"),
            "--graph",
            str(_REPO / "evals" / "sample_dataset.trig"),
            "--report-dir",
            str(out_dir),
            "--no-execute",
        ]
    )
    assert rc in (0, 1)
    payload = json.loads((out_dir / "report.json").read_text())
    assert payload["runner_args"]["executed"] == "False"
