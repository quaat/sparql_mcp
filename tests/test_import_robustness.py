"""Regression tests for the model-import path.

These tests guard against regressions in which Pydantic's recursive
forward-ref resolution hangs, fails, or grows unstable across Python
hash seeds. The recursive IR lives in :mod:`graph_mcp.models._ir` and is
rebuilt with an explicit `_types_namespace`; if any future change
re-introduces a fragile rebuild loop, these tests will catch it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Per-subprocess timeout. Generous to absorb cold-start cost on slow CI.
_IMPORT_TIMEOUT_S = 15.0
_HASH_SEEDS = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "random")

_PROBE_IMPORT = "import graph_mcp.models; print('ok')"
_PROBE_TYPES = (
    "from graph_mcp.models import "
    "SelectPlan, QueryPlan, Pattern, Expression, "
    "ExistsExpr, NotExistsExpr, SubqueryPattern; "
    "print('ok')"
)
_PROBE_JSON_SCHEMA = (
    "from pydantic import TypeAdapter; "
    "from graph_mcp.models import QueryPlan; "
    "TypeAdapter(QueryPlan).json_schema(); "
    "print('ok')"
)


def _run_with_seed(probe: str, seed: str) -> tuple[float, str]:
    """Spawn a fresh interpreter, run ``probe`` with ``PYTHONHASHSEED=seed``."""
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = seed
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-c", probe],
        timeout=_IMPORT_TIMEOUT_S,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    duration = time.perf_counter() - started
    return duration, result.stdout.strip()


def test_import_graph_mcp_models_completes() -> None:
    """In-process: importing the package must not raise."""
    import graph_mcp.models  # noqa: F401


def test_recursive_types_are_directly_importable_in_process() -> None:
    """The discriminated unions must be importable in the same process."""
    from graph_mcp.models import (  # noqa: F401
        ExistsExpr,
        Expression,
        NotExistsExpr,
        Pattern,
        QueryPlan,
        SelectPlan,
        SubqueryPattern,
    )


def test_models_module_does_not_run_rebuild_loop_at_package_level() -> None:
    """Recursive rebuilds must live in ``_ir`` only — not at package level."""
    init_path = Path(__file__).parent.parent / "src" / "graph_mcp" / "models" / "__init__.py"
    src = init_path.read_text()
    assert ".model_rebuild(" not in src, (
        "graph_mcp.models.__init__ should not call model_rebuild; "
        "recursive types are rebuilt inside _ir.py"
    )


@pytest.mark.parametrize("seed", _HASH_SEEDS)
def test_basic_import_under_hash_seed(seed: str) -> None:
    """Fresh subprocess with ``PYTHONHASHSEED=<seed>`` must import in time."""
    _, out = _run_with_seed(_PROBE_IMPORT, seed)
    assert out == "ok"


@pytest.mark.parametrize("seed", _HASH_SEEDS)
def test_recursive_types_import_under_hash_seed(seed: str) -> None:
    """Importing the recursive types under each seed must not hang."""
    _, out = _run_with_seed(_PROBE_TYPES, seed)
    assert out == "ok"


@pytest.mark.parametrize("seed", _HASH_SEEDS)
def test_typeadapter_json_schema_under_hash_seed(seed: str) -> None:
    """Building the QueryPlan JSON schema under each seed must not hang."""
    _, out = _run_with_seed(_PROBE_JSON_SCHEMA, seed)
    assert out == "ok"


def test_import_repeats_are_stable() -> None:
    """Run the import probe repeatedly and assert no run takes excessive time.

    A regression that re-introduces an O(n^2) rebuild walk would show up
    as one or two outlier runs. We require every run within 5x the median.
    """
    durations: list[float] = []
    for _ in range(5):
        d, _ = _run_with_seed(_PROBE_IMPORT, "0")
        durations.append(d)
    durations.sort()
    median = durations[len(durations) // 2]
    worst = durations[-1]
    # Generous bound: worst case at most 5x the median.
    assert worst <= max(median * 5, 2.0), (
        f"import time is unstable: {durations}, median={median:.3f}, worst={worst:.3f}"
    )


def test_typeadapter_repeats_are_stable() -> None:
    """The JSON-schema probe must also stay stable across runs."""
    durations: list[float] = []
    for _ in range(3):
        d, _ = _run_with_seed(_PROBE_JSON_SCHEMA, "0")
        durations.append(d)
    durations.sort()
    median = durations[len(durations) // 2]
    worst = durations[-1]
    assert worst <= max(median * 5, 3.0), (
        f"json_schema time is unstable: {durations}, median={median:.3f}, worst={worst:.3f}"
    )
