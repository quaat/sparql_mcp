#!/usr/bin/env python3
"""Run the RAG eval gate against the curated golden cases.

Thin wrapper around ``python -m evals_rag.runner`` that pins the
production thresholds. CI / release pipelines should call this script
rather than invoke the runner directly so that the gate definitions stay
in one place.

Without ``--azure`` the script uses the deterministic baseline planner so
CI can exercise the RAG plumbing without an LLM key. With ``--azure`` it
forwards the standard Azure OpenAI environment variables to the runner:

- ``AZURE_OPENAI_API_KEY``
- ``AZURE_OPENAI_ENDPOINT``
- ``AZURE_OPENAI_MODEL``
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Pin the gated thresholds in one place. Keep these conservative for the
# mock retriever so the gate is meaningful but not flaky on CI hardware.
_GATE_FLAGS = [
    "--min-case-pass-rate",
    "0.95",
    "--min-selected-case-recall",
    "0.95",
    "--min-retrieval-case-recall-at-k",
    "0.95",
    "--max-unresolved-mention-rate",
    "0.05",
    "--max-safety-violations",
    "0",
    "--fail-below-threshold",
]


def _runner_argv(
    *,
    planner: str,
    retriever: str,
    reranker: str,
    cases: Path,
    report_dir: Path,
    azure: bool,
    azure_endpoint: str | None,
    model: str | None,
    embedding_provider: str | None,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "evals_rag.runner",
        "--planner",
        planner,
        "--retriever",
        retriever,
        "--reranker",
        reranker,
        "--cases",
        str(cases),
        "--report-dir",
        str(report_dir),
    ]
    if embedding_provider:
        argv.extend(["--embedding-provider", embedding_provider])
    if azure:
        argv.append("--azure")
        if azure_endpoint:
            argv.extend(["--azure-endpoint", azure_endpoint])
    if model:
        argv.extend(["--model", model])
    argv.extend(_GATE_FLAGS)
    return argv


def _run(argv: list[str]) -> int:
    print("$ " + " ".join(argv), flush=True)
    return subprocess.call(argv)


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_rag_eval_gate")
    parser.add_argument(
        "--planner",
        default="rag",
        choices=("rag", "deterministic"),
        help="Planner under test. 'deterministic' is the no-LLM smoke run.",
    )
    parser.add_argument(
        "--retriever",
        default="mock",
        choices=("mock", "qdrant"),
        help="Retriever under test. 'qdrant' requires --embedding-provider.",
    )
    parser.add_argument(
        "--reranker",
        default="heuristic",
        choices=("noop", "heuristic"),
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        choices=("missing", "fake"),
        help="Required when --retriever qdrant; 'fake' is a smoke-test stub.",
    )
    parser.add_argument("--azure", action="store_true")
    parser.add_argument("--azure-endpoint", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--reports-root",
        default=str(REPO / "reports"),
        help="Directory under which 'rag-gate' is written.",
    )
    parsed = parser.parse_args(args)

    reports_root = Path(parsed.reports_root)
    reports_root.mkdir(parents=True, exist_ok=True)

    return _run(
        _runner_argv(
            planner=parsed.planner,
            retriever=parsed.retriever,
            reranker=parsed.reranker,
            cases=REPO / "evals" / "golden_cases.yaml",
            report_dir=reports_root / "rag-gate",
            azure=parsed.azure,
            azure_endpoint=parsed.azure_endpoint,
            model=parsed.model,
            embedding_provider=parsed.embedding_provider,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
