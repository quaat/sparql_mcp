---
id: ci
title: CI / CD
sidebar_position: 12
description: GitHub Actions workflows for tests, linting, evals, docs build, and GitHub Pages deployment.
---

# CI / CD

Two workflows run in this repository.

## `.github/workflows/ci.yml` — Python pipeline

Triggers: push to `main`, pull request to `main`, manual dispatch.

| Job | What it does |
| --- | --- |
| `clean-install (3.11)` | Fresh venv → `pip install -e .[dev]` → `pip check` → import smoke → `pytest` → `ruff check` → `ruff format --check` → `mypy src evals` → both eval files. |
| `clean-install (3.12)` | Same on Python 3.12. |
| `clean-install (3.13)` | Same on Python 3.13. |
| `hash-seed-stress` | Runs after the matrix; re-imports `graph_mcp.models` under `PYTHONHASHSEED ∈ {0..9, random}`. |

Each matrix job creates an isolated venv (`python -m venv .venv-ci`).
This catches packaging mistakes that only surface from a clean
checkout.

## `.github/workflows/docs.yml` — documentation pipeline

Triggers:

- pushes to `main` that touch `docs-site/`, `src/graph_mcp/`,
  `evals/`, the doc scripts, or the workflow file;
- pull requests touching the same paths (build only, no deploy);
- manual dispatch.

Permissions are scoped to what GitHub Pages requires:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

`concurrency.group: pages` keeps a deploy from racing itself.

| Job | What it does |
| --- | --- |
| `docs-checks` | Set up Python; run `python scripts/generate_docs_reference.py --check` and `python scripts/check_docs.py`. Fails when generated artifacts are stale or the docs are missing required references. |
| `build` | `npm ci` → `npm run typecheck` → `npm run build` in `docs-site/`. Runs on every PR and push. Uploads the static site as a Pages artifact only on push to `main`. |
| `deploy` | Runs only on push to `main`. Uses `actions/deploy-pages@v4` to publish the artifact to the `github-pages` environment. |

The `deploy` job is the only place GitHub Pages credentials are used,
and it uses GitHub's OIDC token — there are no long-lived secrets.

### Required GitHub repository setting

In the repository settings, set:

```text
Settings → Pages → Build and deployment → Source → GitHub Actions
```

Without this, `actions/deploy-pages` cannot publish even if the
workflow runs successfully.

### Configurable URLs

`docs-site/docusaurus.config.ts` reads two optional environment
variables:

| Variable | Default |
| --- | --- |
| `DOCUSAURUS_URL` | `https://<owner>.github.io` (derived from `GITHUB_REPOSITORY`) |
| `DOCUSAURUS_BASE_URL` | `/<repo-name>/` for project sites, `/` for `*.github.io` org/user sites |

The workflow sets both based on the GitHub repo slug. Override them
in the workflow `env:` block (or as repo Variables) when serving from
a custom domain.

## Local CI parity

Run the same gates locally before pushing:

```bash
python -m pip check
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src evals
python -m evals.runner --planner deterministic
python scripts/generate_docs_reference.py --check
python scripts/check_docs.py
cd docs-site && npm ci && npm run typecheck && npm run build
```

The `Makefile` provides convenience targets (`make test`, `make lint`,
`make typecheck`, `make all`) for the Python gates.

## Docs deployment guarantees

We do not claim that GitHub Pages is currently serving anything — that
depends on the repository's owner enabling Pages and merging the
workflow. The workflow is structured so that:

- PRs build the site but do not deploy;
- merges to `main` build, then deploy;
- a deploy never overlaps with another deploy on the same group.

If you change the docs and want to verify the build before merging,
the PR's `build` job log includes the static-site output path.
