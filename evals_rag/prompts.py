"""Prompt fragments specific to the RAG planner.

The retrieved-candidate-pack section is appended to the existing planner
system prompt. The base contract (no raw SPARQL, strict ``QueryPlan`` IR,
clarification / refusal variants) lives in
:data:`evals.planner_prompt.PLANNER_SYSTEM_PROMPT` and is reused unchanged
— this module only adds RAG-specific guidance.
"""

from __future__ import annotations

from evals_rag.models import (
    ConceptCandidatePack,
    OntologyConcept,
    RerankedConcept,
)

RAG_GUIDANCE: str = """\
# RAG candidate-use rules

The user message contains two complementary blocks:

1. **Resolved terms** — produced by the deterministic resolver and (when
   applicable) supplemented by RAG-promoted candidates. This block is
   *authoritative*: the IRIs and prefixed names listed here are the ones
   you must use when planning.
2. **Retrieved ontology candidates** — the raw retrieve-then-rerank
   output. This block exists to explain *why* a concept was promoted into
   the resolved-terms block, and to surface high-scoring candidates that
   were considered but not promoted.

Rules:

- Treat the **Resolved terms** block as the source of truth. RAG-promoted
  entries in that block are exactly as trustworthy as deterministic ones.
- The **Retrieved ontology candidates** block is reference material only.
  Do not introduce IRIs from there unless the entry has been promoted to
  the resolved-terms block, or unless the prompt explicitly states the
  selected candidate is high-confidence.
- When a retrieved candidate conflicts with a resolved term (different
  IRI for the same mention), prefer the resolved term unless the
  conflict is marked as "RAG-promoted, high-confidence".
- Do not use low-scoring (or unselected) retrieved concepts to invent
  triples or property paths.
- Use ``domain`` and ``range`` hints when wiring relationships: a
  property with domain ``ex:Person`` and range ``ex:Company`` makes the
  subject and object types explicit.
- If even after RAG promotion a required mention remains unresolved or
  ambiguous, return :class:`ClarificationOutput`. Do not guess.
- Destructive / unsafe requests still go to :class:`RefusedOutput` —
  retrieval results never override policy.
"""


def render_candidate_pack(pack: ConceptCandidatePack) -> str:
    """Render the candidate pack as a compact prompt section.

    The output is grouped per mention when the planner ran retrievals at
    mention granularity (the common case); a single full-question retrieval
    is rendered under a sentinel ``"<question>"`` heading. Concepts that
    were retrieved against multiple mentions show all originating mentions
    in their lineage line.
    """
    if not pack.selected and not pack.retrieved:
        return "(no ontology candidates retrieved)"
    lines: list[str] = ["## Retrieved ontology candidates"]
    grouped = _group_by_mention(pack)
    for mention, items in grouped.items():
        header = f'Mention: "{mention}"' if mention != "<question>" else "Question-level retrieval"
        lines.append("")
        lines.append(header)
        if not items:
            lines.append("  (no matching concepts)")
            continue
        lines.append("Selected candidates:")
        for ordinal, item in enumerate(items, start=1):
            lines.append(f"  {ordinal}. {_format_candidate_line(item)}")
    if pack.unresolved_mentions:
        lines.append("")
        lines.append("Unresolved mentions (no concept retrieved):")
        for m in pack.unresolved_mentions:
            lines.append(f"  - {m!r}")
    if pack.diagnostics:
        lines.append("")
        lines.append("Retrieval diagnostics:")
        for d in pack.diagnostics:
            lines.append(f"  - {d}")
    return "\n".join(lines)


def _group_by_mention(pack: ConceptCandidatePack) -> dict[str, list[RerankedConcept]]:
    """Bucket selected concepts by the mention they were retrieved for."""
    if not pack.mentions:
        return {"<question>": list(pack.selected)}
    grouped: dict[str, list[RerankedConcept]] = {m: [] for m in pack.mentions}
    leftover: list[RerankedConcept] = []
    for item in pack.selected:
        bucket = item.concept.metadata.get("rag_mention") if item.concept.metadata else None
        if isinstance(bucket, str) and bucket in grouped:
            grouped[bucket].append(item)
        else:
            leftover.append(item)
    if leftover:
        grouped.setdefault("<question>", []).extend(leftover)
    return grouped


def _format_candidate_line(item: RerankedConcept) -> str:
    """Single-line summary used in the prompt's candidate list."""
    name = item.concept.prefixed_name or item.concept.iri
    label = item.concept.label or "?"
    parts = [
        name,
        f"kind={item.concept.kind}",
        f"score={item.final_score:.2f}",
        f"label={label!r}",
    ]
    mentions = item.concept.metadata.get("rag_mentions")
    if isinstance(mentions, list) and len(mentions) > 1:
        parts.append("mentions=" + ",".join(str(m) for m in mentions))
    if item.concept.domain:
        parts.append(f"domain={_compact_iris(item.concept.domain)}")
    if item.concept.range:
        parts.append(f"range={_compact_iris(item.concept.range)}")
    return " | ".join(parts)


def _compact_iris(iris: list[str]) -> str:
    return ",".join(_compact_iri(iri) for iri in iris[:3])


def _compact_iri(iri: str) -> str:
    """Shorten an absolute IRI to its last path segment for prompt density."""
    if ":" in iri and not iri.startswith(("http://", "https://")):
        return iri
    return iri.rstrip("#/").rsplit("/", 1)[-1].rsplit("#", 1)[-1] or iri


def concepts_for_kinds(selected: list[RerankedConcept], kinds: list[str]) -> list[OntologyConcept]:
    """Filter ``selected`` candidates to a kind subset (used by tests)."""
    kind_set = set(kinds)
    return [c.concept for c in selected if c.concept.kind in kind_set]
