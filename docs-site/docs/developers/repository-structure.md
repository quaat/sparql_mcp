---
id: repository-structure
title: Repository structure
sidebar_position: 2
description: Where to find each layer of the codebase.
---

# Repository structure

```text
graph-mcp/
├── src/graph_mcp/
│   ├── __init__.py
│   ├── server.py                    # FastMCP entry point + CLI
│   ├── config.py                    # Pydantic Settings + ConfigurationError
│   ├── logging.py                   # structlog wiring
│   ├── compiler/
│   │   ├── validator.py             # QueryPlanValidator
│   │   ├── renderer.py              # SparqlRenderer (deterministic)
│   │   ├── escaping.py              # IRI / lang / string escaping
│   │   └── errors.py                # Compiler exception types
│   ├── graph/
│   │   ├── endpoint.py              # HttpSparqlEndpoint, LocalRdflibEndpoint
│   │   ├── schema_discovery.py      # SchemaProvider, SchemaSnapshot
│   │   ├── term_resolver.py         # TermResolver
│   │   └── result_normalizer.py     # SPARQL JSON → SelectResult
│   ├── mcp_tools/
│   │   ├── tools.py                 # MCP tool input/output models + tool fns
│   │   ├── resources.py             # graph://schema/* JSON renderers
│   │   ├── prompts.py               # build_query_plan template
│   │   └── sparql_scanner.py        # token-aware raw-SPARQL safety check
│   ├── models/
│   │   ├── _ir.py                   # Recursive IR (single source of truth)
│   │   ├── expressions.py           # Re-exports
│   │   ├── patterns.py              # Re-exports
│   │   ├── query_plan.py            # Re-exports
│   │   ├── iri.py                   # Var, Iri, PrefixedName, LiteralValue
│   │   ├── literals.py              # Regexes + DEFAULT_PREFIXES
│   │   ├── results.py               # SelectResult, AskResult, ...
│   │   └── validation.py            # ValidationIssue, ValidationResult
│   └── security/
│       └── policy.py                # SecurityPolicy (frozen dataclass)
├── evals/
│   ├── runner.py                    # graph-mcp-evals CLI
│   ├── agent.py                     # DeterministicPlanner + PydanticAI planner
│   ├── models.py                    # Eval IO + CaseResult
│   ├── metrics.py                   # Aggregate metrics
│   ├── structural.py                # IR-level recall computation
│   ├── golden_cases.yaml            # Baseline cases (keyword planner aligns)
│   ├── golden_cases_adversarial.yaml
│   └── sample_graph.ttl             # Bundled in-memory graph
├── tests/                            # pytest, asyncio mode auto
├── docs/
│   └── production_readiness.md      # Operator checklist (mirrored on the site)
├── docs-site/                        # This Docusaurus site
├── scripts/
│   ├── generate_docs_reference.py   # Refresh managed reference fragments
│   └── check_docs.py                # CI doc-coverage gate
├── .github/workflows/
│   ├── ci.yml                       # Python lint/tests/eval matrix
│   └── docs.yml                     # Docs build + GitHub Pages deploy
├── .env.example                     # Documented environment template
└── pyproject.toml                   # Build, deps, ruff/mypy/pytest config
```

## Why `models/_ir.py` is one file

The expression / pattern / plan models are mutually recursive
(`Expression` contains `NotExistsExpr` whose `patterns` are
`list[Pattern]`; `Pattern` contains `FilterPattern(Expression)` and
`SubqueryPattern(SelectPlan)`; `SelectPlan` contains `list[Pattern]`).

Putting them in one module means every forward reference is in the
same module's globals, so Pydantic v2's automatic forward-ref
resolution works without injecting custom type namespaces. The thin
`expressions.py` / `patterns.py` / `query_plan.py` shims exist purely
for naming readability.

This decision is guarded by `tests/test_import_robustness.py`, which
re-imports the package under several `PYTHONHASHSEED` values to catch
ordering-dependent bugs.

## Why `evals/` is a sibling of `src/`

The eval harness depends on the runtime, but the runtime has no
business depending on the harness. Keeping `evals/` outside
`src/graph_mcp/` makes the dependency direction obvious; `pyproject.toml`
ships both via the wheel.

## Where new code goes

- a new IR node → `src/graph_mcp/models/_ir.py` (and possibly `iri.py`);
- a new validation rule → `src/graph_mcp/compiler/validator.py`;
- a new renderer branch → `src/graph_mcp/compiler/renderer.py`;
- a new MCP tool or resource → `src/graph_mcp/mcp_tools/` and
  `src/graph_mcp/server.py`;
- a new schema discovery query → `src/graph_mcp/graph/schema_discovery.py`;
- a new eval golden case → `evals/golden_cases.yaml` (or the adversarial file).

See the [Extension guide](/developers/extension-guide/) for recipes.
