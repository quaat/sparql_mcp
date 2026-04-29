"""Prompt fragments specific to the RAG planner.

The retrieved-candidate-pack section is appended to the existing planner
system prompt. The base contract (no raw SPARQL, strict ``QueryPlan`` IR,
clarification / refusal variants) lives in :data:`evals.planner_prompt.PLANNER_SYSTEM_PROMPT`
and is reused unchanged — this module only adds RAG-specific guidance.
"""

from __future__ import annotations

from evals_rag.models import (
    ConceptCandidatePack,
    OntologyConcept,
    RerankedConcept,
)

RAG_GUIDANCE: str = """\
# RAG candidate-use rules

The user message ends with a "Retrieved ontology candidates" block. Those
entries come from a vector retrieval over the schema vocabulary, optionally
re-ranked. They are *candidates*, not facts:

- Use only concepts that appear in the **selected** list. Treat the broader
  retrieval list as background context, not as resolved terms.
- Prefer candidates with high ``final_score`` and a matching ``kind`` for
  the role you are filling (a "property" mention should pick a property,
  not an individual that happens to share a label).
- Use the ``domain`` and ``range`` hints when wiring relationships: the
  property ``ex:worksFor`` with domain ``ex:Person`` and range ``ex:Company``
  makes the subject and object types explicit.
- Never invent IRIs, prefixes, or local names that do not appear in the
  candidate pack or the schema block.
- If the selected candidates are insufficient to answer the question (no
  matching property, missing class, etc.), return ``ClarificationOutput``
  with a concrete clarification question. Do not guess.
- For unsafe / destructive requests, return ``RefusedOutput`` as before;
  retrieval results never override the policy.
"""


def render_candidate_pack(pack: ConceptCandidatePack) -> str:
    """Render the candidate pack as a compact prompt section.

    The output is grouped per mention when the planner ran retrievals at
    mention granularity (the common case); a single full-question retrieval
    is rendered under a sentinel ``"<question>"`` heading. The format is
    intentionally line-oriented so the LLM can scan it quickly without
    being overwhelmed by Qdrant payload metadata.
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
    """Bucket selected concepts by the mention they were retrieved for.

    The mapping uses the order of ``pack.mentions`` so the planner sees
    mentions in the same order they appear in the question. When the
    planner ran a single full-question retrieval, the bucket key is
    ``"<question>"``.
    """
    if not pack.mentions:
        return {"<question>": list(pack.selected)}
    grouped: dict[str, list[RerankedConcept]] = {m: [] for m in pack.mentions}
    leftover: list[RerankedConcept] = []
    # The planner stores retrieval-mention lineage in concept.metadata when
    # available. Fall back to the first mention bucket otherwise.
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
