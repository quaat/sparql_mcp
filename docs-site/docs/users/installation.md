---
id: installation
title: Installation
sidebar_position: 3
description: Supported platforms, Python versions, and install commands for graph-mcp.
---

# Installation

## Supported runtimes

| Python | Pydantic | Status |
| --- | --- | --- |
| 3.11 | 2.6 – 2.13 | CI green |
| 3.12 | 2.6 – 2.13 | CI green |
| 3.13 | 2.6 – 2.13 | CI green |

The recursive `QueryPlan` IR is pinned to Pydantic `>=2.6,<3`. The
free-threaded build (`python3.13t`) is **not** supported — one of the
transitive dependencies pulls in CFFI, which currently does not build on
the GIL-less interpreter.

## Install from a clone

Replace `YOUR_ORG_OR_USER` with the GitHub organization or username
that owns the fork you cloned from:

```bash
git clone https://github.com/YOUR_ORG_OR_USER/graph-mcp.git
cd graph-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Two extras are available:

| Extra | Adds |
| --- | --- |
| `[dev]` | `pytest`, `pytest-asyncio`, `ruff`, `mypy`, type stubs. Use this for any local development or running the eval suite. |
| `[ai]` | `pydantic-ai` for the optional LLM eval planner. Requires an API key for the chosen model. |

You can combine them: `pip install -e ".[dev,ai]"`.

## Verify the install

```bash
python -m pip check
python -c "import graph_mcp; print(graph_mcp.__version__)"
python -c "from pydantic import TypeAdapter; from graph_mcp.models import QueryPlan; TypeAdapter(QueryPlan).json_schema(); print('ok')"
python -m pytest -q
```

`pip check` should report `No broken requirements found.` and the test
suite should pass — see [Testing](/developers/testing/) for what runs.

## Where things land

```text
graph-mcp/
├── src/graph_mcp/        # the runtime package (importable as graph_mcp)
├── evals/                 # eval harness & sample graph
├── tests/                 # pytest suite
├── docs/                  # production-readiness checklist (Markdown)
├── docs-site/             # this documentation site (Docusaurus)
└── pyproject.toml
```

The `graph-mcp` console script is registered by `pyproject.toml`:

```bash
graph-mcp --help
```

You can also invoke the server module directly:

```bash
python -m graph_mcp.server --transport stdio
```
