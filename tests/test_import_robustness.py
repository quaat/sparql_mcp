"""Regression tests for the model-import path.

These tests guard against the failure mode in which Pydantic's recursive
forward-ref resolution hangs or fails when ``graph_mcp.models`` is imported.
"""

from __future__ import annotations

import subprocess
import sys


def test_import_graph_mcp_models_completes() -> None:
    """In-process: importing the package must not raise."""
    import graph_mcp.models  # noqa: F401


def test_model_import_does_not_hang() -> None:
    """Cross-process: a fresh interpreter must complete the import within 5s.

    A regression that re-introduces a rebuild loop or a circular forward
    reference would cause this test to time out.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import graph_mcp.models; print('ok')"],
        timeout=5,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ok"


def test_recursive_types_are_directly_importable() -> None:
    """The discriminated unions must be reachable from a fresh process too."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from graph_mcp.models import "
                "SelectPlan, NotExistsExpr, SubqueryPattern, ExistsExpr, "
                "FilterPattern, Pattern, Expression, QueryPlan; "
                "print('ok')"
            ),
        ],
        timeout=5,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ok"


def test_models_module_does_not_run_rebuild_loop_at_package_level() -> None:
    """The package ``__init__`` must not call ``model_rebuild`` itself.

    Rebuild loops outside the type-defining module are the failure mode that
    motivated the IR consolidation. Catching this with a source-level check
    keeps the architecture honest.
    """
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "src" / "graph_mcp" / "models" / "__init__.py"
    src = init_path.read_text()
    # Match the call form, not docstring mentions of the word.
    assert ".model_rebuild(" not in src, (
        "graph_mcp.models.__init__ should not call model_rebuild; "
        "recursive types now live in _ir.py"
    )
