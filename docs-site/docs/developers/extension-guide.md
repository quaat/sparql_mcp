---
id: extension-guide
title: Extension guide
sidebar_position: 13
description: Step-by-step recipes for the common ways to extend graph-mcp.
---

# Extension guide

Each recipe lists the files you must touch and the tests you must add.
The CI doc-coverage check fails when a new tool or resource isn't
mentioned in the corresponding reference page, so plan to update docs
in the same PR.

## Add a new expression function

Goal: add support for a SPARQL function the IR currently rejects, e.g.
`encode_for_uri`.

1. Add it to `ALLOWED_FUNCTIONS` in
   `src/graph_mcp/models/_ir.py`.
2. The renderer's default `FunctionExpr` branch already
   uppercases the name and emits `NAME(args)`. If the function needs
   special rendering, add a branch in
   `src/graph_mcp/compiler/renderer.py:_render_expr`.
3. If validation needs special handling (e.g. argument arity), add a
   `model_post_init` check on `FunctionExpr` or extend
   `_check_expr_vars`.
4. Add tests:
   - `tests/test_renderer.py` — golden output;
   - `tests/test_validator.py` — accepted / rejected inputs.

## Add a new pattern type

Goal: introduce a new `kind` in the `Pattern` union, e.g. a
`MaterializedView` pattern.

1. Define the model in `src/graph_mcp/models/_ir.py` and append the
   class to the `Pattern` `Annotated[..., Field(discriminator="kind")]`
   union.
2. Add it to `_REBUILD_NAMESPACE` and the rebuild loop at the bottom
   of the file.
3. Add a branch in
   `src/graph_mcp/compiler/validator.py:_validate_pattern` to walk it
   and update scope.
4. Add a branch in
   `src/graph_mcp/compiler/renderer.py:_render_pattern` to emit the
   SPARQL form.
5. If needed, update the helper `_iter_visible_variables` (in the
   renderer) and `_vars_in_pattern` (in the validator).
6. Tests:
   - `tests/test_validator.py` — scope behaviour, error codes;
   - `tests/test_renderer.py` — golden SPARQL.
7. Document in [QueryPlan IR](/developers/query-plan-ir/) and
   [QueryPlan schema reference](/reference/query-plan-schema/).

## Add a new validation rule

1. Decide where the rule belongs:
   - structural (e.g. "no duplicate ORDER BY keys") → inside
     `_validate_select`;
   - per-pattern (e.g. "no nested SERVICE") → inside
     `_validate_pattern`;
   - expression-level (e.g. "regex flags must be a known set") → keep
     it as a Pydantic `field_validator` on the model so the IR rejects
     bad input at parse time.
2. Choose a stable error code and document it in
   [Validation errors reference](/reference/validation-errors/).
3. Add tests under `tests/test_validator.py`.

## Add a new renderer branch

When a new IR feature exists (see "Add a new pattern type"), update
the renderer:

1. Branch in `_render_pattern` / `_render_expr` / `_render_path`.
2. Use the existing escape helpers — never construct user-controlled
   text manually.
3. Update `_iter_visible_variables` if the new pattern binds variables
   visible from the outer scope.
4. Add a test in `tests/test_renderer.py` with a small plan and an
   exact expected SPARQL string.

## Add a new MCP tool

1. Add Pydantic IO models to `src/graph_mcp/mcp_tools/tools.py`.
2. Add the pure tool function (`tool_xxx(input, deps...) -> ...`).
3. Register in `src/graph_mcp/server.py`:
   ```python
   @mcp.tool()
   def xxx(input: XxxInput) -> XxxOutput:
       return tool_xxx(input, ...)
   ```
4. Document under
   [User guide → MCP tools](/users/mcp-tools/) and
   [Tools reference](/reference/tools-reference/).
5. Run `python scripts/generate_docs_reference.py` so the auto-table
   picks up the new tool.
6. Add tests in `tests/test_mcp_tools.py`.

## Add a new resource

1. Add a JSON-returning function in
   `src/graph_mcp/mcp_tools/resources.py`.
2. Register in `server.py`:
   ```python
   @mcp.resource("graph://your/uri")
   def res_yours() -> str:
       return your_renderer(...)
   ```
3. Document in [Resources reference](/reference/resources-reference/)
   with a section heading whose anchor matches the auto-generated
   table (`uri.replace("graph://", "").replace("/", "-")`).
4. Run `python scripts/generate_docs_reference.py`.
5. Add a test under `tests/`.

## Add a new schema discovery field

See [Schema provider → Adding a new discovery field](/developers/schema-provider/#adding-a-new-discovery-field).

## Add a new eval metric

See [Evaluation harness → Adding a new metric](/developers/evals/#adding-a-new-metric).

## Add a new ADR

1. Copy `docs-site/docs/adr/0004-docusaurus-documentation-site.md` as
   a template.
2. Pick the next sequential number (`0005-...`).
3. Add the file to the `adr` sidebar in
   `docs-site/sidebars.ts`.
4. Cross-link from the affected developer doc.

The ADR template is intentionally short: context, decision,
alternatives, consequences. No prose framing — these are decision
records, not blog posts.
