---
id: testing
title: Testing
sidebar_position: 11
description: How the test suite is organized and how to run each category locally.
---

# Testing

The test suite lives under `tests/` and is invoked through pytest. It
runs offline by default — no network, no API keys, no live LLM calls.

```bash
python -m pytest -q
```

`pytest.ini_options` in `pyproject.toml` sets `asyncio_mode = "auto"`,
so async tests do not need explicit `@pytest.mark.asyncio` markers.

## Categories

| Category | Files |
| --- | --- |
| Models / IR | `test_models.py`, `test_import_robustness.py` |
| Validator | `test_validator.py`, `test_aggregate_validation.py`, `test_exists_validation.py`, `test_property_path_validation.py`, `test_graph_allowlist.py`, `test_graph_allowlist_strict.py`, `test_default_prefix_protection.py`, `test_prefix_handling.py`, `test_security_policy.py` |
| Renderer | `test_renderer.py`, `test_select_star_projection.py`, `test_recursive_limits.py` |
| Endpoints | `test_local_endpoint.py`, `test_remote_construct.py` |
| Schema discovery | `test_schema_discovery.py`, `test_schema_provider_modes.py`, `test_term_resolver.py` |
| MCP tools | `test_mcp_tools.py`, `test_query_graph_limits.py`, `test_raw_sparql_hardening.py`, `test_sparql_scanner.py` |
| Evals | `test_evals.py`, `test_eval_metrics.py`, `test_eval_structural.py`, `test_planner_workflow.py` |

## Import-robustness tests

`tests/test_import_robustness.py` re-imports the package under several
hash seeds:

```python
@pytest.mark.parametrize("seed", ["0", "1", "2", ..., "random"])
def test_import_under_hash_seed(seed): ...
```

This exists because the recursive Pydantic IR has historically been
sensitive to dict-iteration ordering. The CI hash-seed-stress job in
`.github/workflows/ci.yml` extends the same idea by running 11 seeds
in a separate job.

## Raw-SPARQL scanner tests

`tests/test_sparql_scanner.py` covers the token-aware scanner directly
(tokenize, IRI fragments, string opacity, forbidden keywords, SERVICE
endpoint allowlist, top-level-LIMIT detection).

`tests/test_raw_sparql_hardening.py` exercises the same logic through
the `tool_execute_sparql_raw` boundary.

## Schema-provider tests

- `test_schema_discovery.py` — runs a `SparqlSchemaProvider` against
  the bundled sample graph; verifies discovered classes/properties
  and the term resolver's end-to-end behaviour.
- `test_schema_provider_modes.py` — `static`, `auto`, and `sparql`
  modes; the fail-fast `ConfigurationError`.

## MCP tool tests

`test_mcp_tools.py` exercises every tool function with a fake
endpoint. `test_query_graph_limits.py` is the regression suite for the
"cap before validation" behaviour.

## LLM tests (skipped by default)

The `llm` pytest marker is registered in `pyproject.toml` for tests
that require an API key. They are skipped unless a marker is selected
explicitly:

```bash
pytest -m llm
```

CI does not run them. There are currently no `llm`-marked tests
shipping in the suite — the harness lives in `evals/` and is not wired
into pytest collection.

## Running a subset

```bash
# Single file
pytest tests/test_validator.py -q

# Single function
pytest tests/test_validator.py::test_unbound_variable_is_rejected -q

# By directory
pytest tests/ -q

# With verbose output
pytest -vv
```

## What CI runs

`.github/workflows/ci.yml` runs, in a fresh virtualenv per Python
version (3.11, 3.12, 3.13):

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
python -c "import graph_mcp.models; print('ok')"
python -c "from pydantic import TypeAdapter; from graph_mcp.models import QueryPlan; TypeAdapter(QueryPlan).json_schema(); print('ok')"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src evals
python -m evals.runner --planner deterministic
python -m evals.runner --cases evals/golden_cases_adversarial.yaml --planner deterministic
```

The `hash-seed-stress` job re-runs the import smoke test under
`PYTHONHASHSEED ∈ {0..9, random}`.

The docs build is in a separate workflow
([CI](/developers/ci/)).
