"""Deterministic mention extraction for the planner workflow.

The extractor finds candidate term mentions in a natural-language question
using simple, schema-aware string matching:

- It scans the question for label / prefixed-name / local-name occurrences
  drawn from the :class:`SchemaSnapshot`.
- It also picks up capitalized tokens not yet in the schema, since they may
  point to individuals the resolver should clarify.

This is intentionally not an LLM call: the resolver runs against the output
to give the planner a stable, ranked candidate table before the LLM ever
sees the question. Trading breadth for determinism keeps the resolver +
prompt surface area predictable across model versions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from graph_mcp.graph.schema_discovery import SchemaSnapshot

TermKind = Literal["class", "property", "individual", "graph", "unknown"]


@dataclass
class TermMention:
    text: str
    """The substring as it appears in the question (lowercased for stable IO)."""

    expected_kinds: tuple[TermKind, ...] = ()
    """Kinds the extractor expects this mention to resolve to. Empty means
    "any kind"; the resolver uses it as a filter."""

    span: tuple[int, int] = (0, 0)
    """Character span (start, end) in the original question."""

    sources: list[str] = field(default_factory=list)
    """Why we picked this mention up: ``"label"``, ``"prefixed_name"``,
    ``"local_name"``, ``"capitalized"``, ``"verb_phrase"``."""


_CAPITAL_WORD = re.compile(r"\b([A-Z][a-zA-Z0-9_-]+)\b")
_LOWER_WORD = re.compile(r"\b([a-z][a-zA-Z0-9_-]+)\b")
_VERB_PHRASE = re.compile(
    r"\b(works\s+for|knows|founded(?:\s+by)?|joined|contributes\s+to|labels?|"
    r"age|knows[\^]\+?|transitively\s+knows)\b",
    re.IGNORECASE,
)
_CLASS_NOUNS = re.compile(
    r"\b(person|people|company|companies|project|projects|graph)\b",
    re.IGNORECASE,
)

# Tokens we skip even when capitalized — they're never schema terms.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "at",
        "for",
        "to",
        "with",
        "by",
        "from",
        "on",
        "via",
        "is",
        "are",
        "was",
        "were",
        "show",
        "find",
        "who",
        "which",
        "what",
        "list",
        "all",
        "every",
        "each",
        "use",
        "drop",
        "delete",
        "raw",
        "sparql",
        "graph",
        "query",
        "select",
        "ask",
        "construct",
        "filter",
        "optional",
        "union",
        "minus",
        "values",
        "bind",
        "having",
        "group",
        "order",
        "explicitly",
    }
)


def _add_unique(out: list[TermMention], mention: TermMention) -> None:
    for existing in out:
        if existing.text == mention.text and existing.expected_kinds == mention.expected_kinds:
            existing.sources.extend(s for s in mention.sources if s not in existing.sources)
            return
    out.append(mention)


def extract_mentions(question: str, schema: SchemaSnapshot) -> list[TermMention]:
    """Extract candidate mentions from ``question`` using ``schema`` as a hint set.

    The extractor returns *candidates*; the resolver decides which ones map
    to a real schema term. The order of the returned list is roughly:

    1. Schema-anchored mentions (a label / prefixed-name / local-name from
       the snapshot literally appears in the question).
    2. Verb-phrase / class-noun heuristics ("works for", "company", etc.).
    3. Standalone capitalized tokens (potentially individual mentions).
    """
    out: list[TermMention] = []
    q = question
    ql = question.lower()

    # 1) Schema-anchored hits ------------------------------------------------
    def _scan_schema(
        term_iri: str, label: str | None, prefixed: str | None, kind: TermKind
    ) -> None:
        # Try label first (case-insensitive substring), then local name.
        candidates: list[tuple[str, str]] = []
        if label:
            candidates.append((label.lower(), "label"))
        if prefixed:
            local = prefixed.split(":", 1)[-1]
            candidates.append((local.lower(), "local_name"))
            # camelCase split: "worksFor" -> "works for"
            split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local).lower()
            if split != local.lower():
                candidates.append((split, "local_name"))
        last = re.split(r"[#/]", term_iri.rstrip("#/"))[-1].lower()
        if last and (last, "local_name") not in candidates:
            candidates.append((last, "local_name"))
        for needle, source in candidates:
            if not needle or len(needle) < 2:
                continue
            idx = ql.find(needle)
            if idx == -1:
                continue
            text = q[idx : idx + len(needle)]
            _add_unique(
                out,
                TermMention(
                    text=text,
                    expected_kinds=(kind,),
                    span=(idx, idx + len(needle)),
                    sources=[source],
                ),
            )

    for c in schema.classes:
        _scan_schema(c.iri, c.label, c.prefixed_name, "class")
    for p in schema.properties:
        _scan_schema(p.iri, p.label, p.prefixed_name, "property")
    for i in schema.individuals:
        _scan_schema(i.iri, i.label, i.prefixed_name, "individual")
    for g in schema.named_graphs:
        _scan_schema(g.iri, g.label, None, "graph")

    # 2) Verb-phrase heuristics ---------------------------------------------
    for m in _VERB_PHRASE.finditer(question):
        text = m.group(1).lower().strip()
        _add_unique(
            out,
            TermMention(
                text=text,
                expected_kinds=("property",),
                span=m.span(),
                sources=["verb_phrase"],
            ),
        )
    for m in _CLASS_NOUNS.finditer(question):
        text = m.group(1).lower()
        _add_unique(
            out,
            TermMention(
                text=text,
                expected_kinds=("class",),
                span=m.span(),
                sources=["class_noun"],
            ),
        )

    # 3) Capitalized tokens — likely individuals / classes -------------------
    for m in _CAPITAL_WORD.finditer(question):
        token = m.group(1)
        if token.lower() in _STOPWORDS:
            continue
        # Skip tokens that appear as the first word of a sentence (e.g. "Show")
        # unless followed by a noun phrase. The cheap heuristic: skip if it's
        # a pure imperative verb.
        if token.lower() in {
            "show",
            "list",
            "find",
            "use",
            "restrict",
            "drop",
            "return",
            "count",
            "pick",
            "anyone",
        }:
            continue
        _add_unique(
            out,
            TermMention(
                text=token,
                expected_kinds=("individual", "class"),
                span=m.span(),
                sources=["capitalized"],
            ),
        )

    # 4) Lowercase common-name individuals (alice, bob, etc.) — only when the
    # schema actually has matching individuals; this keeps generic words out.
    individual_names = {ind.label.lower() for ind in schema.individuals if ind.label}
    individual_names.update(
        ind.prefixed_name.split(":", 1)[-1].lower()
        for ind in schema.individuals
        if ind.prefixed_name
    )
    for m in _LOWER_WORD.finditer(question):
        token = m.group(1)
        if token.lower() in individual_names:
            _add_unique(
                out,
                TermMention(
                    text=token,
                    expected_kinds=("individual",),
                    span=m.span(),
                    sources=["individual_name"],
                ),
            )

    return out
