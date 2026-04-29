---
id: release-process
title: Release process
sidebar_position: 15
description: How to cut a release of graph-mcp.
---

# Release process

`graph-mcp` follows simple semver. The current version is in
`pyproject.toml` (`[project] version`) and re-exported as
`graph_mcp.__version__`.

## Pre-release checklist

Run locally, in order:

```bash
python -m pip check
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src evals
python -m evals.runner --planner deterministic
python -m evals.runner --cases evals/golden_cases_adversarial.yaml --planner deterministic
python scripts/generate_docs_reference.py --check
python scripts/check_docs.py
cd docs-site && npm ci && npm run typecheck && npm run build
```

Everything must pass.

## Cutting a release

1. Bump `version` in `pyproject.toml` and `__version__` in
   `src/graph_mcp/__init__.py` to the new value.
2. Update the changelog (if any).
3. Commit with a message like `release: 0.2.0`.
4. Tag: `git tag -a v0.2.0 -m "v0.2.0"`.
5. Push the commit and the tag.
6. CI runs the full pipeline on the tag.

## Publishing

The repository does not currently auto-publish to PyPI. To publish
manually:

```bash
python -m pip install --upgrade build twine
python -m build
twine check dist/*
twine upload dist/*
```

If/when an automated publish step is added, it belongs in a separate
workflow with `id-token: write` permission and PyPI Trusted
Publishing.

## Documentation deploys

Documentation deploys automatically on push to `main` via
`.github/workflows/docs.yml`. There is no separate "doc release"
step — the live docs always reflect the latest `main`.

## Compatibility commitments

- Pydantic is pinned to `>=2.6,<3`. A bump past 3.0 is a release with a
  major version bump.
- The IR JSON Schema is exported as
  [`/schema/query-plan.schema.json`](/schema/query-plan.schema.json).
  Backwards-incompatible IR changes are major-version events.
- The MCP tool surface (`resolve_terms`, `validate_query_plan`,
  `render_sparql`, `query_graph`, `explain_query_plan`,
  `refresh_schema`, `execute_sparql_raw`) is part of the public API.
  Renaming or removing a tool is a major-version event.

## Hotfix process

For urgent fixes (security, critical data correctness):

1. Branch from the most recent tag.
2. Land the fix on `main` first (or in parallel) so trunk is healthy.
3. Cherry-pick to the hotfix branch.
4. Tag a patch release (`v0.1.1`).
5. Document the fix in the changelog with a short note on impact.
