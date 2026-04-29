# graph-mcp documentation site

Static documentation for the `graph-mcp` server, built with
[Docusaurus 3](https://docusaurus.io). The published site lives at the URL
configured for GitHub Pages on this repository (see
[`.github/workflows/docs.yml`](../.github/workflows/docs.yml)).

## Local preview

```bash
cd docs-site
npm ci
npm run start    # http://localhost:3000
```

`npm run start` reloads on changes to Markdown, MDX, and the TS config.

## Build

```bash
cd docs-site
npm ci
npm run typecheck   # tsc --noEmit (Docusaurus tsconfig)
npm run build       # writes static site to docs-site/build
npm run serve       # serve the built site locally
```

## Configuring the URL / base URL

`docusaurus.config.ts` reads two environment variables, both optional:

| Variable | Purpose |
| --- | --- |
| `DOCUSAURUS_URL` | Public origin (e.g. `https://example.github.io`). |
| `DOCUSAURUS_BASE_URL` | Path prefix (e.g. `/graph-mcp/` for project sites, `/` for org/user sites). |

**Both are optional.** The config derives sensible defaults from the
GitHub repository slug (`GITHUB_REPOSITORY`) when these are unset or
empty:

- `url` falls back to `https://<owner>.github.io`;
- `baseUrl` falls back to `/<repo>/` for project sites and `/` for
  `*.github.io` user/org sites.

Empty strings (the typical effect of an undefined GitHub Actions
repository Variable) are treated the same as unset so the build never
ends up with `url=""`. Override these only when you serve from a
custom domain.

## Reference fragments are generated

`scripts/generate_docs_reference.py` regenerates a few reference
artifacts under `docs-site/static/schema/` (the QueryPlan JSON Schema)
and the configuration table in
`docs-site/docs/reference/configuration-reference.md`. Run it whenever
the IR or config changes:

```bash
python scripts/generate_docs_reference.py
```

The same script supports `--check` for CI. See
[`scripts/check_docs.py`](../scripts/check_docs.py) for the
documentation-coverage check.
