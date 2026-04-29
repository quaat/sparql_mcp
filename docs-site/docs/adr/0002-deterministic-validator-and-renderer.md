---
id: 0002-deterministic-validator-and-renderer
title: ADR 0002 — Deterministic validator and renderer
sidebar_position: 2
description: Why both validation and rendering are pure, deterministic functions.
---

# ADR 0002 — Deterministic validator and renderer

**Status:** accepted

## Context

The server's hot path is `plan → validator → renderer → endpoint`.
Each layer can in principle be implemented as anything — including a
nondeterministic process such as a model call ("ask an LLM whether
this plan is safe"). We choose between deterministic and
non-deterministic implementations.

## Decision

Both the validator (`QueryPlanValidator`) and the renderer
(`SparqlRenderer`) are **pure, deterministic, side-effect-free
functions** of the plan and the active `SecurityPolicy`.

## Why deterministic validation?

1. **Repair-able errors.** The validator emits a structured ordered
   list of `ValidationIssue` records. Each issue has a stable `code`,
   a `path` into the plan tree, and an optional `hint`. The LLM can
   programmatically map `unbound_projection_var` → "add the missing
   triple"; it cannot programmatically map a free-text "this plan
   looks weird" to anything actionable.
2. **Test-able errors.** The same plan must always produce the same
   set of issues. This makes the test suite small and the regression
   surface obvious.
3. **Auditable safety.** When a safety check rejects a plan, the
   reviewer can read the rule that fired. There is no "the model
   said no" black box.
4. **Deterministic CI.** The validator runs hundreds of times per CI
   build (every test case + every eval golden case). A
   nondeterministic validator would make CI flaky.

## Why deterministic rendering?

1. **Escape correctness.** The renderer is the only path from
   user-controlled strings to query text. Determinism makes "every
   user string goes through `escape_iri` / `escape_string_literal` /
   `escape_lang_tag`" a property the test suite can assert.
2. **Stable diffs.** Two semantically equivalent plans render to
   byte-identical SPARQL. Code review of plan changes can read the
   `--git diff` of rendered output instead of comparing structures.
3. **Reproducible bugs.** "Run this plan; render it; you'll get this
   string" is a reproducible report. Any non-determinism in rendering
   would balloon bug-triage cost.
4. **Eval comparability.** The eval harness scores planners by
   matching required tokens in the rendered SPARQL (along with
   structural metrics). A nondeterministic renderer would make those
   matches probabilistic.

## Implementation properties

- The validator builds a single `_Ctx` per `validate(plan)` call and
  walks the plan tree without external state.
- The renderer's only mutable state during a render is
  `_iri_to_prefix`, populated once from the plan's prefix block and
  used for IRI compaction.
- The prefix block is sorted alphabetically so prefix-set changes
  produce minimal diffs.
- Pattern lists are walked in their original order.
- All escape helpers are pure functions in
  `graph_mcp/compiler/escaping.py`.

## Consequences

- New IR features must come with both a validator branch and a
  renderer branch — there is no escape hatch where the renderer "asks
  the validator" or vice versa.
- New escape needs go through the helpers; tests guard them.
- The server can run offline (no model calls in the hot path) and
  this is what enables the `LocalRdflibEndpoint` mode used by tests
  and demos.

## References

- [Validator](/developers/validator/)
- [Renderer](/developers/renderer/)
- [Architecture overview](/developers/architecture/)
