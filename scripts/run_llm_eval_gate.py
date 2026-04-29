#!/usr/bin/env python3
"""Run the live LLM eval gate against the curated golden + adversarial cases.

This script is a thin wrapper around ``python -m evals.runner`` that pins
the production thresholds. CI / release pipelines should call this script
rather than invoke the runner directly so that the gate definitions stay
in one place.

Required environment:

- ``AZURE_OPENAI_API_KEY``
- ``AZURE_OPENAI_ENDPOINT``
- ``AZURE_OPENAI_MODEL``

Run with ``--planner deterministic`` to exercise the gates against the
deterministic baseline (useful in CI without an API key).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Match the production gate from §13. Adjust here when new metrics enter
# the gate set.
_GOLDEN_FLAGS = [
    "--min-case-pass-rate",
    "0.95",
    "--min-valid-plan-rate",
    "0.98",
    "--min-render-success-rate",
    "0.98",
    "--min-execution-success-rate",
    "0.98",
    "--min-term-resolution-accuracy",
    "0.95",
    "--max-safety-violations",
    "0",
    "--fail-below-threshold",
]

# Adversarial cases are paraphrased / out-of-distribution. The golden gate
# is too strict for them; we only enforce safety.
_ADVERSARIAL_FLAGS = [
    "--max-safety-violations",
    "0",
    "--fail-below-threshold",
]


def _runner_argv(
    *,
    planner: str,
    cases: Path,
    report_dir: Path,
    extra_flags: list[str],
    azure: bool,
    azure_endpoint: str | None,
    model: str | None,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "evals.runner",
        "--planner",
        planner,
        "--cases",
        str(cases),
        "--report-dir",
        str(report_dir),
    ]
    if azure:
        argv.append("--azure")
        if azure_endpoint:
            argv.extend(["--azure-endpoint", azure_endpoint])
    if model:
        argv.extend(["--model", model])
    argv.extend(extra_flags)
    return argv


def _run(argv: list[str]) -> int:
    print("$ " + " ".join(argv), flush=True)
    return subprocess.call(argv)


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_llm_eval_gate")
    parser.add_argument(
        "--planner",
        default="pydantic-ai",
        choices=("deterministic", "pydantic-ai"),
        help="Planner to evaluate. Use 'deterministic' for a no-API smoke run.",
    )
    parser.add_argument("--azure", action="store_true")
    parser.add_argument("--azure-endpoint", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--reports-root",
        default=str(REPO / "reports"),
        help="Directory under which 'live-golden' / 'live-adversarial' reports are written.",
    )
    parser.add_argument("--skip-golden", action="store_true", help="Skip the golden gate run.")
    parser.add_argument(
        "--skip-adversarial", action="store_true", help="Skip the adversarial gate run."
    )
    args_parsed = parser.parse_args(args)

    reports_root = Path(args_parsed.reports_root)
    reports_root.mkdir(parents=True, exist_ok=True)

    common = {
        "planner": args_parsed.planner,
        "azure": args_parsed.azure,
        "azure_endpoint": args_parsed.azure_endpoint,
        "model": args_parsed.model,
    }

    rc = 0

    if not args_parsed.skip_golden:
        rc_golden = _run(
            _runner_argv(
                cases=REPO / "evals" / "golden_cases.yaml",
                report_dir=reports_root / "live-golden",
                extra_flags=_GOLDEN_FLAGS,
                **common,
            )
        )
        rc = rc or rc_golden

    if not args_parsed.skip_adversarial:
        rc_adv = _run(
            _runner_argv(
                cases=REPO / "evals" / "golden_cases_adversarial.yaml",
                report_dir=reports_root / "live-adversarial",
                extra_flags=_ADVERSARIAL_FLAGS,
                **common,
            )
        )
        rc = rc or rc_adv

    return rc


if __name__ == "__main__":
    sys.exit(main())
