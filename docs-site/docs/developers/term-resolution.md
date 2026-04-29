---
id: term-resolution
title: Term resolution
sidebar_position: 8
description: How TermResolver maps mentions to ranked schema candidates.
---

# Term resolution

`TermResolver` (`src/graph_mcp/graph/term_resolver.py`) is a
deterministic lexical matcher. It does not call out to any model, does
not embed, and does not network — it reads only the cached
`SchemaSnapshot`.

The intent is to keep the LLM honest: every IRI it places in a plan
should come from a schema lookup, not from a hallucination.

## Inputs

```python
TermResolutionResult = TermResolver.resolve(
    mentions: list[str],
    expected_kinds: list[TermKind] | None = None,  # default: all four
    limit: int = 10,
)
```

`TermKind` is a literal type alias:
`"class" | "property" | "individual" | "graph" | "unknown"`. The
host's MCP tool input rejects `"unknown"` as a request kind.

## Scoring algorithm

For each `(mention, candidate)` pair:

1. Normalize both: lowercase, replace non-alphanumerics with spaces,
   strip.
2. Score:
   - exact match → `1.0`;
   - one is a substring of the other → `0.85`;
   - otherwise → `SequenceMatcher(None, a, b).ratio()`.

Candidate strings are derived from the schema term:

- the term's `label`;
- each `alias`;
- the local part of `prefixed_name` with camelCase split
  (`worksFor` → `works for`);
- the last path segment of the IRI with camelCase split.

The candidate's reported score is the **max** over all candidate
strings.

## Filtering

Per mention, candidates with `score < 0.4` are dropped. If nothing
remains, the result includes a single placeholder candidate with
`kind="unknown"`, `iri=""`, and `score=0.0`. This signals to the LLM
that it should ask a clarifying question rather than invent.

## Why not embeddings?

- offline, deterministic, and free;
- testable with a small fixture (no network);
- "good enough" for the tasks the LLM does in this server: turn a
  user mention into a small set of plausible IRIs the LLM can
  cross-check.

The score function is intentionally simple. If you have a richer
matcher, swap in a custom resolver:

```python
class MyResolver:
    def resolve(self, mentions, *, expected_kinds=None, limit=10):
        ...

server = build_server(...)  # then attach your resolver via dependency
```

(In practice, `TermResolver` is constructed inside `build_server`; for
a custom one you'd subclass / monkey-patch the construction. PRs
welcome.)

## Tests

- `tests/test_term_resolver.py` — exact match, alias match,
  substring match, threshold, empty-result placeholder.
- `tests/test_schema_discovery.py::test_term_resolver_uses_discovered_schema`
  — end-to-end: discover schema from sample graph and resolve
  mentions through the same path.
